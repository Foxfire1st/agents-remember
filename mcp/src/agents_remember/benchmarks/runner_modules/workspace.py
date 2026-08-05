from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agents_remember.benchmarks.runner_modules.commands import (
    repo_has_commit,
    run_git_command,
)
from agents_remember.benchmarks.runner_modules.constants import (
    SOURCE_ONLY_AGENTS_TEMPLATE,
    WORKSPACE_AGENTS_TEMPLATE,
)
from agents_remember.benchmarks.runner_modules.filesystem import (
    remove_path,
    render_template,
    sync_runtime_assets,
    sync_workspace_skill_exposure,
    write_benchmark_root_marker,
)
from agents_remember.benchmarks.runner_modules.manifest import (
    manifest_path_component,
    manifest_relative_path,
)
from agents_remember.benchmarks.runner_modules.mcp_registration import (
    prepare_configured_providers,
    write_benchmark_mcp_registration,
)
from agents_remember.benchmarks.runner_modules.models import (
    BenchmarkCase,
    BenchmarkPreparation,
    BenchmarkWorkspace,
)
from agents_remember.kernel.git_command import GIT_BULK_REMOTE_TIMEOUT_SECONDS


def prepare_repo(
    repository: dict[str, Any], repo_root: Path, dry_run: bool, force_clone: bool = False
) -> None:
    url = str(repository["url"])
    commit = str(repository["commit"])
    if dry_run:
        print(f"Would ensure directory {repo_root.parent}")
    else:
        repo_root.parent.mkdir(parents=True, exist_ok=True)
    if force_clone and (repo_root.exists() or repo_root.is_symlink()):
        if dry_run:
            print(f"Would remove existing repository {repo_root}")
        else:
            remove_path(repo_root)
    existing_repo = not force_clone and (repo_root / ".git").exists()
    if not existing_repo:
        # The clone runs from the parent created above, because its own destination is what
        # it is about to make; ``repo_root`` stays the repository the command is about.
        run_git_command(
            repo_root,
            ["clone", url, str(repo_root)],
            dry_run,
            work_dir=repo_root.parent,
            timeout=GIT_BULK_REMOTE_TIMEOUT_SECONDS,
        )
    elif repo_has_commit(repo_root, commit):
        if dry_run:
            print(f"Would reuse cached repository {repo_root} at {commit}")
    else:
        run_git_command(
            repo_root,
            ["fetch", "--all", "--tags"],
            dry_run,
            timeout=GIT_BULK_REMOTE_TIMEOUT_SECONDS,
        )
    run_git_command(repo_root, ["checkout", "--detach", commit], dry_run)
    run_git_command(repo_root, ["reset", "--hard", commit], dry_run)
    run_git_command(repo_root, ["clean", "-fdx"], dry_run)


def workspace_root(benchmarks_root: Path, case: BenchmarkCase) -> Path:
    return benchmarks_root / manifest_relative_path(
        case.workspace["fixturePath"], f"{case.case_id}.workspace.fixturePath"
    )


def source_only_workspace_path(case: BenchmarkCase) -> Path:
    configured = case.workspace.get("sourceOnlyRoot")
    if configured:
        return manifest_relative_path(configured, f"{case.case_id}.workspace.sourceOnlyRoot")
    return Path("source-only")


def with_memory_workspace_path(case: BenchmarkCase) -> Path:
    configured = case.workspace.get("withMemoryRoot") or case.workspace.get("withOnboardingRoot")
    if configured:
        return manifest_relative_path(configured, f"{case.case_id}.workspace.withMemoryRoot")
    return Path("with-memory")


def source_only_workspace_root(benchmarks_root: Path, case: BenchmarkCase) -> Path:
    return workspace_root(benchmarks_root, case) / source_only_workspace_path(case)


def with_memory_workspace_root(benchmarks_root: Path, case: BenchmarkCase) -> Path:
    return workspace_root(benchmarks_root, case) / with_memory_workspace_path(case)


def repository_path(case: BenchmarkCase) -> Path:
    configured = case.workspace.get("repoRelativePath")
    if configured:
        return manifest_relative_path(configured, f"{case.case_id}.workspace.repoRelativePath")
    repo_name = manifest_path_component(case.repository["name"], f"{case.case_id}.repository.name")
    return Path("repos") / repo_name


def coordination_path(case: BenchmarkCase) -> Path:
    configured = case.workspace.get("coordinationRoot") or case.workspace.get(
        "withOnboardingCoordinationRoot"
    )
    if configured:
        return manifest_relative_path(configured, f"{case.case_id}.workspace.coordinationRoot")
    return Path("ar-coordination")


def memory_repo_name(case: BenchmarkCase) -> str:
    configured = case.memory_repository.get("name") or case.workspace.get("externalMemoryRepo")
    if not configured:
        raise RuntimeError(f"memory repository name is missing for case {case.case_id}")
    return manifest_path_component(configured, f"{case.case_id}.memoryRepository.name")


def render_workspace_agents(benchmarks_root: Path, case: BenchmarkCase, dry_run: bool) -> Path:
    template_path = benchmarks_root / WORKSPACE_AGENTS_TEMPLATE
    if not template_path.is_file():
        raise RuntimeError(f"benchmark workspace template not found: {template_path}")

    repo_relative_path = repository_path(case).as_posix()
    coordination_root = coordination_path(case).as_posix()
    destination = with_memory_workspace_root(benchmarks_root, case) / "AGENTS.md"
    rendered = render_template(
        template_path.read_text(encoding="utf-8"),
        {
            "case_id": case.case_id,
            "repository_name": str(case.repository["name"]),
            "repo_relative_path": repo_relative_path,
            "coordination_root": coordination_root,
            "memory_repository_name": memory_repo_name(case),
        },
    )
    if dry_run:
        print(f"Would render {template_path} -> {destination}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    return destination


def render_source_only_agents(benchmarks_root: Path, case: BenchmarkCase, dry_run: bool) -> Path:
    template_path = benchmarks_root / SOURCE_ONLY_AGENTS_TEMPLATE
    if not template_path.is_file():
        raise RuntimeError(f"benchmark source-only template not found: {template_path}")

    repo_relative_path = repository_path(case).as_posix()
    destination = source_only_workspace_root(benchmarks_root, case) / "AGENTS.md"
    rendered = render_template(
        template_path.read_text(encoding="utf-8"),
        {
            "case_id": case.case_id,
            "repository_name": str(case.repository["name"]),
            "repo_relative_path": repo_relative_path,
        },
    )
    if dry_run:
        print(f"Would render {template_path} -> {destination}")
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    return destination


def prune_legacy_workspace_paths(root: Path, dry_run: bool) -> None:
    for relative in (Path("AGENTS.md"), Path("repos"), Path("ar-coordination")):
        path = root / relative
        if not path.exists() and not path.is_symlink():
            continue
        if dry_run:
            print(f"Would remove legacy workspace path {path}")
            continue
        remove_path(path)


def prepare_memory_repo(
    case: BenchmarkCase, coordination_root: Path, dry_run: bool, force_clone: bool = False
) -> Path:
    memory_repo = coordination_root / "memory-repos" / memory_repo_name(case)
    memory_repository = case.memory_repository
    if memory_repository:
        prepare_repo(memory_repository, memory_repo, dry_run, force_clone=force_clone)
    elif not memory_repo.exists() and not dry_run:
        raise RuntimeError(f"workspace memory repo missing after preparation: {memory_repo}")
    return memory_repo


UNFILTERED_PROVIDERS_ENV = "AR_BENCHMARK_ALLOW_UNFILTERED_PROVIDERS"


def filter_benchmark_provider_ids(
    case_id: str,
    provider_ids: tuple[str, ...],
    allowed_provider_ids: tuple[str, ...] | None,
) -> tuple[str, ...]:
    """Containment R1 (260707-HFX-L1): the case manifest is not launch authority.

    Providers outside the live MCP authority set are neither persisted into the
    workspace registration (which arms every later session booted there) nor
    launched by prepare_configured_providers. ``None`` (no authority context,
    i.e. direct script use below the MCP layer) is FAIL-CLOSED too — review
    finding B4: an implicit default must not be the bypass. The explicit
    developer act is the ``AR_BENCHMARK_ALLOW_UNFILTERED_PROVIDERS=1`` env var.
    """
    if allowed_provider_ids is None:
        if os.environ.get(UNFILTERED_PROVIDERS_ENV) == "1":
            return provider_ids
        if provider_ids:
            print(
                f"Skipping benchmark providers {list(provider_ids)} for {case_id}: no MCP "
                "authority context (containment R1); direct script runs must set "
                f"{UNFILTERED_PROVIDERS_ENV}=1 to arm providers without an authority filter"
            )
        return ()
    allowed = set(allowed_provider_ids)
    skipped = tuple(p for p in provider_ids if p not in allowed)
    kept = tuple(p for p in provider_ids if p in allowed)
    if skipped:
        print(
            f"Skipping benchmark providers {list(skipped)} for {case_id}: not enabled "
            "in the live MCP authority settings (containment R1); the workspace registration "
            "carries only authority-enabled providers"
        )
    return kept


def prepare_case(
    preparation: BenchmarkPreparation,
    case: BenchmarkCase,
    *,
    provider_ids: tuple[str, ...] = (),
) -> None:
    benchmarks_root = preparation.benchmarks_root
    dry_run = preparation.dry_run
    force_clone = preparation.force_clone
    provider_ids = filter_benchmark_provider_ids(
        case.case_id, provider_ids, preparation.allowed_provider_ids
    )
    repository = case.repository
    root = workspace_root(benchmarks_root, case)
    source_only_root = source_only_workspace_root(benchmarks_root, case)
    with_memory_root = with_memory_workspace_root(benchmarks_root, case)
    source_only_repo_root = source_only_root / repository_path(case)
    with_memory_repo_root = with_memory_root / repository_path(case)
    print(f"Preparing {case.case_id}")

    prune_legacy_workspace_paths(root, dry_run)
    render_source_only_agents(benchmarks_root, case, dry_run)
    render_workspace_agents(benchmarks_root, case, dry_run)
    write_benchmark_root_marker(source_only_root, dry_run)
    write_benchmark_root_marker(with_memory_root, dry_run)
    prepare_repo(repository, source_only_repo_root, dry_run, force_clone=force_clone)
    prepare_repo(repository, with_memory_repo_root, dry_run, force_clone=force_clone)

    coordination_root = with_memory_root / coordination_path(case)
    sync_runtime_assets(coordination_root, dry_run)
    sync_workspace_skill_exposure(
        with_memory_root, coordination_root, dry_run, preparation.skill_exposure_mode
    )
    memory_repo = prepare_memory_repo(case, coordination_root, dry_run, force_clone=force_clone)
    workspace = BenchmarkWorkspace(
        case=case,
        workspace_root=with_memory_root,
        coordination_root=coordination_root,
        source_repo_root=with_memory_repo_root,
        memory_repo=memory_repo,
        provider_ids=provider_ids,
    )
    write_benchmark_mcp_registration(
        workspace,
        provider_timeout=preparation.provider_timeout,
        dry_run=dry_run,
    )
    prepare_configured_providers(
        workspace,
        dry_run=dry_run,
        provider_timeout=preparation.provider_timeout,
    )

    if dry_run:
        print(f"Would verify benchmark memory repo exists: {memory_repo}")
