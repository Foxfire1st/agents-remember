from __future__ import annotations

import re
from pathlib import Path

from agents_remember.kernel.memory_mode import (
    LEGACY_INTERNAL_MEMORY_DIRNAME,
    Topology,
    refuse_removed_memory_mode,
)

DEFAULT_AR_COORDINATION_ROOT = "../ar-coordination"


def looks_like_installed_coordination_root(path: Path) -> bool:
    return (
        (path / "skills").is_dir()
        and (path / "system").is_dir()
        and (path / "memory-repos").is_dir()
        and (path / "tasks").is_dir()
    )


def agents_repo_from_script() -> Path:
    script_path = Path(__file__).resolve()
    for parent in script_path.parents:
        if (parent / "runtime" / "skills").exists() and (parent / "AGENTS.md").exists():
            return parent
        if looks_like_installed_coordination_root(parent):
            return parent
    return script_path.parents[6]


def clean_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`"):
        value = value[1:-1]
    if value.startswith(('"', "'")) and value.endswith(('"', "'")) and len(value) >= 2:
        value = value[1:-1]
    return value.strip()


def normalize_rel_path(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def mirror_onboarding_path(onboarding_root: Path, source_file: str) -> Path:
    """Where ``source_file``'s sidecar onboarding lives: the same relative path, plus ``.md``.

    The 1:1 mirror rule, expressed once. It is a pure derivation over
    :func:`normalize_rel_path` -- no filesystem access, no memory-quality vocabulary -- and it
    is asked by three different substrates: the drift checker, the missing-onboarding check,
    and the sidecar pairing the read tools and the dashboard file API share. It used to be
    declared by the drift checker, which made ``kernel`` import ``memory_quality``
    (``layers.toml``) for a path join.
    """
    return onboarding_root / f"{normalize_rel_path(source_file)}.md"


def extract_yaml_blocks(markdown_text: str) -> list[str]:
    return [
        match.group(1)
        for match in re.finditer(r"```(?:yaml|yml)?\n(.*?)```", markdown_text, re.DOTALL)
    ]


def external_memory_root(coordination_root: Path, code_repository_name: str) -> Path:
    return (coordination_root / "memory-repos" / f"ar-{code_repository_name}").resolve()


def settings_path_for_roots(memory_root: Path, coordination_root: Path) -> Path:
    memory_settings = memory_root / "system" / "settings.md"
    coordination_settings = coordination_root / "system" / "settings.md"
    if memory_settings.exists():
        return memory_settings
    if coordination_settings.exists():
        return coordination_settings
    return memory_settings


def memory_roots_from_settings(
    settings_path: Path,
    code_repository_name: str,
) -> tuple[Path, Path]:
    """The coordination and memory roots a settings file implies.

    Two shapes are admitted: a settings file inside a per-repo memory repo, and one at a
    coordination root. A settings file anywhere else is not a supported memory location, so
    it is refused by its own path rather than silently resolving to some other root.
    """
    settings_root = settings_path.resolve().parent.parent
    if (
        settings_root.name == f"ar-{code_repository_name}"
        and settings_root.parent.name == "memory-repos"
    ):
        return settings_root.parent.parent, settings_root
    if settings_root.name == LEGACY_INTERNAL_MEMORY_DIRNAME:
        refuse_removed_memory_mode("internal", artifact=settings_path.as_posix())
    return settings_root, external_memory_root(settings_root, code_repository_name)


def resolve_coordination_root_hint(coordination_root: Path | None) -> Path:
    if coordination_root is not None:
        return coordination_root.resolve()

    runtime_root = agents_repo_from_script().resolve()
    if looks_like_installed_coordination_root(runtime_root):
        return runtime_root
    return (runtime_root / DEFAULT_AR_COORDINATION_ROOT).resolve()


def find_code_repository_root(workspace_root: Path, code_repository_name: str) -> Path:
    repo_path = Path(code_repository_name).expanduser()
    if repo_path.is_absolute() and repo_path.exists():
        return repo_path.resolve()

    direct = (workspace_root / code_repository_name).resolve()
    if direct.exists():
        return direct

    raise ValueError(
        f"code repository {code_repository_name!r} was not found under {workspace_root}"
    )


# A leaf enclosure's memory worktree is the second shape a memory root legitimately takes:
# ``worktree_group_for`` places it at
# ``<coordination>/worktrees/<code-repository-name>/<group>/memory-<worktree-name>/onboarding``.
# It is the shape the contract-scoped memory-quality route already measures, because that route
# takes its onboarding root from the contract rather than from this resolver -- so refusing it here
# refused a location the product itself uses (D-34).
WORKTREES_DIRNAME = "worktrees"
MEMORY_REPOS_DIRNAME = "memory-repos"
MEMORY_WORKTREE_PREFIX = "memory-"


def memory_worktree_enclosure(onboarding_root: Path) -> tuple[Path, str] | None:
    """``(coordination_root, code_repository_name)`` for a memory worktree, else ``None``.

    The shape is decoded structurally rather than guessed: ``.../worktrees/<repo>/<group>/
    memory-<name>/onboarding``. Anything that does not match every segment is not this shape, so a
    directory that merely happens to be named ``memory-something`` earns no acceptance.
    """
    segments = onboarding_root.resolve().parts
    if len(segments) < 5 or segments[-1] != "onboarding":
        return None
    name, group, repo_name, worktrees = segments[-2], segments[-3], segments[-4], segments[-5]
    if worktrees != WORKTREES_DIRNAME or not name.startswith(MEMORY_WORKTREE_PREFIX):
        return None
    if name == MEMORY_WORKTREE_PREFIX or not group or not repo_name:
        return None
    return Path(*segments[:-5]), repo_name


def infer_settings_path(onboarding_root: Path) -> Path:
    enclosure = memory_worktree_enclosure(onboarding_root)
    if enclosure is not None:
        coordination_root, code_repository_name = enclosure
        # A memory worktree carries no `system/` of its own: the settings that govern it are the
        # official memory repo's, which is the pair's own source. Falling back to the context-root
        # inference below keeps an unknown worktree reporting the missing file rather than
        # resolving to some other repository's settings.
        official = external_memory_root(coordination_root, code_repository_name) / "system"
        if (official / "settings.md").exists():
            return official / "settings.md"
    if onboarding_root.name == "onboarding":
        context_root = onboarding_root.parent
    elif onboarding_root.parent.name == "onboarding":
        context_root = onboarding_root.parent.parent
    else:
        context_root = onboarding_root.parent
    return context_root / "system" / "settings.md"


def path_settings_path_for(settings_path: Path) -> Path:
    return settings_path.with_suffix(".json")


def infer_topology_from_onboarding_root(onboarding_root: Path) -> Topology:
    """The topology an onboarding root belongs to, or a refusal naming that root.

    An onboarding root under the removed repo-sidecar layout is refused by name and by exact
    path: it is real existing state, and reporting it is the whole point -- resolving it to
    ``external``, or to any other root, would silently move a repository's memory.
    """
    if onboarding_root.parent.name == LEGACY_INTERNAL_MEMORY_DIRNAME:
        refuse_removed_memory_mode("internal", artifact=onboarding_root.as_posix())
    if (
        onboarding_root.parent.parent.name == MEMORY_REPOS_DIRNAME
        and onboarding_root.parent.name.startswith("ar-")
    ):
        return "external"
    if memory_worktree_enclosure(onboarding_root) is not None:
        # A leaf enclosure's memory worktree: the memory is external to the code repository all the
        # same, and this is the exact root the contract-scoped memory-quality route measures. It
        # was refused here only because the resolver inferred topology from the path while the tool
        # took its root from the contract.
        return "external"
    raise ValueError(
        "onboarding_root must point to a supported memory location, of which there are two: "
        f"<ar-coordination>/{MEMORY_REPOS_DIRNAME}/ar-<code-repository-name>/onboarding for an "
        f"official memory repo, or "
        f"<ar-coordination>/{WORKTREES_DIRNAME}/<code-repository-name>/<group>/"
        f"{MEMORY_WORKTREE_PREFIX}<worktree-name>/onboarding for a leaf enclosure's memory "
        f"worktree (the root the contract-scoped memory-quality route already measures). "
        f"Received: {onboarding_root.as_posix()}"
    )
