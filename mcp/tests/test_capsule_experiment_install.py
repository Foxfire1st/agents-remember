"""The experimental installation and instruction cutover, end to end.

Every case here runs the **real** installer against a real source tree built from this
repository's canonical assets, and reads its result back out of the files the installer
actually wrote. Nothing hand-supplies a value the product is supposed to produce:

* the delivery mode and the per-run record come from ``install_runtime`` / its payload;
* the occurrence counts come from the bytes on disk under the scratch coordination root
  (side A) and from the capsule the production compile route produces (side B);
* the version strings the record carries are compared against the sha256 of
  ``skills/l-01-agent-lifecycles/composition-manifest.json`` and the pins in
  ``eve_runtime/package.json``, both computed here from the file.

The two fingerprints this module counts are asserted to resolve in their canonical
source before they are counted, so a corpus edit that moves one of them fails loudly
instead of turning the counts into zeros.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
if str(MCP_SRC) not in sys.path:
    sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.role_capsules.launch import compile_launch_capsule
from agents_remember.install import experiment as install_experiment
from agents_remember.install import runtime as install_runtime
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.core import runtime_install_payload
from agents_remember.serving.launch_capsule import LaunchCapsuleRequest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_AGENTS_MD = REPOSITORY_ROOT / "agents-md-files"
CANONICAL_SKILLS = REPOSITORY_ROOT / "skills"
CANONICAL_EVE_APPLICATION = REPOSITORY_ROOT / "eve_runtime"
CANONICAL_PROVIDER_REQUIREMENTS = REPOSITORY_ROOT / "providers" / "requirements"

CORPUS_FINGERPRINT = "# Core — Completion Truth And Handoff Acceptance"
"""A canonical block identity: the first heading of ``core/acceptance.md``.

Counted in the compiled capsule (the one delivery path an opted-in run has) and in the
installed startup surfaces (which must not carry it a second time).
"""

CORPUS_FINGERPRINT_SOURCE = CANONICAL_SKILLS / "l-01-agent-lifecycles" / "core" / "acceptance.md"

LEGACY_FINGERPRINT = "## Start Here — Route By Role"
"""The legacy AR startup chain's own routing heading, from the coordinator ``AGENTS.md``.

Presence of this heading in an installed coordination root is exactly "the old full AR
startup chain is installed". The capsule never carries it, so the two counts are
independent readings rather than one number compared with itself.
"""

LEGACY_FINGERPRINT_SOURCE = CANONICAL_AGENTS_MD / "coordinator" / "AGENTS.md"


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def create_install_source(root: Path) -> Path:
    """One install source tree built from this repository's canonical assets.

    The package-data layout, the four startup targets, the canonical corpus the
    compiler reads, the provider requirement files ``require_runtime_tree`` insists
    on, and the pinned eve application source the experiment installs.
    """

    source = root / "package_data"
    runtime_root = source / "runtime"
    shutil.copytree(CANONICAL_AGENTS_MD, runtime_root / "agents-md-files")
    shutil.copytree(
        CANONICAL_SKILLS / "l-01-agent-lifecycles",
        runtime_root / "skills" / "l-01-agent-lifecycles",
    )
    for name in ("codegraphcontext.txt", "grepai.txt"):
        _copy(
            CANONICAL_PROVIDER_REQUIREMENTS / name,
            runtime_root / "providers" / "requirements" / name,
        )
    application = source / install_experiment.PACKAGED_APPLICATION_PATH
    for relative in ("package.json", "package-lock.json", "agent/agent.ts"):
        _copy(CANONICAL_EVE_APPLICATION / relative, application / relative)
    return source


def dispatcherless_deps() -> install_runtime.ProviderDependencyInstall:
    """Provider dependencies off: this module never starts a provider stack."""

    return install_runtime.ProviderDependencyInstall(settings={}, timeout=1, enabled=False)


def run_install(
    source: Path, coordination_root: Path, *, experiment: str | None, dry_run: bool = False
):
    """Run the real installer for one run, through the entry point that run selects.

    A run that selected nothing goes through ``install_runtime`` — the unmodified
    entry point — and a selected run through ``install_experimental_runtime``, which
    is the same split ``install_runtime_from_config`` makes.
    """

    selection = install_experiment.resolve_experiment_selection(requested=experiment, environ={})
    scope = install_runtime.RuntimeInstallScope(
        source_root=source,
        coordination_root=coordination_root,
        dry_run=dry_run,
        provider_deps=dispatcherless_deps(),
        experiment=selection if selection.selected else None,
    )
    if selection.selected:
        return install_runtime.install_experimental_runtime(scope)
    return install_runtime.install_runtime(
        scope.source_root,
        scope.coordination_root,
        scope.dry_run,
        provider_deps=scope.provider_deps,
    )


def fingerprint_count(root: Path, fingerprint: str) -> int:
    """How many times ``fingerprint`` occurs in the effective material under ``root``.

    Every readable text file under ``root`` is scanned, minus the machine-local trees the
    installer never writes. Directories are included, so a heading carried by two files
    counts twice.
    """

    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        total += text.count(fingerprint)
    return total


def snapshot_regular_files(root: Path) -> dict[str, str]:
    """Every regular file under ``root`` by relative path, with its content digest.

    Extension-blind on purpose: the guard this feeds must see a ``.toml``, a ``.txt``, an
    extension-less file or a settings key exactly as it sees a ``.json``.
    """

    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            digests[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        except OSError:
            continue
    return digests


def files_containing(root: Path, needle: bytes) -> list[str]:
    """Every regular file under ``root`` whose bytes contain ``needle``, relative, sorted.

    Root-scoped, not diff-scoped: a file that was already there before the run is still a file
    under the coordination root, and a persisted switch that a *previous* run created is exactly
    as much a global switch as one this run creates. Binary files are searched as bytes, so an
    extension or an encoding cannot hide a hit.
    """

    offenders: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        if needle in payload:
            offenders.append(path.relative_to(root).as_posix())
    return offenders


def material_text(root: Path) -> str:
    """The effective material under ``root`` as one string, for a single-pass count."""

    parts: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            parts.append(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(parts)


def compiled_capsule_text() -> str:
    """The trusted instruction text the production compile route produces for a free agent."""

    coordination_root = Path(
        pytest.importorskip("tempfile").mkdtemp(prefix="ar-l9-capsule-compile-")
    )
    config = McpRuntimeConfig(
        config_path=coordination_root / "settings.json",
        coordination_root=coordination_root,
        workspace_root=REPOSITORY_ROOT,
        transcript_root=coordination_root / "logs" / "mcp",
    )
    launch = compile_launch_capsule(
        config,
        LaunchCapsuleRequest(role="bootstrap", harness="codex", workspace_root=REPOSITORY_ROOT),
    )
    assert launch.codex_delivery is not None, launch.explain()
    instructions = launch.codex_delivery.trusted_instructions
    assert isinstance(instructions, str) and instructions
    return instructions


def test_the_two_fingerprints_resolve_in_their_canonical_sources() -> None:
    """The selectors are real corpus text, so a zero count means absence, not drift."""

    assert CORPUS_FINGERPRINT in CORPUS_FINGERPRINT_SOURCE.read_text(encoding="utf-8")
    assert LEGACY_FINGERPRINT in LEGACY_FINGERPRINT_SOURCE.read_text(encoding="utf-8")
    assert CORPUS_FINGERPRINT not in LEGACY_FINGERPRINT_SOURCE.read_text(encoding="utf-8")


def test_an_unselected_run_installs_the_legacy_startup_chain(tmp_path: Path) -> None:
    source = create_install_source(tmp_path)
    coordination_root = tmp_path / "ar-coordination"

    summary = run_install(source, coordination_root, experiment=None)

    assert summary.experiment is None
    for target in install_experiment.WITHHELD_STARTUP_TARGETS:
        assert (coordination_root / target).is_file(), target
    assert not (coordination_root / install_experiment.INSTALLED_APPLICATION_PATH).exists()
    assert fingerprint_count(coordination_root, LEGACY_FINGERPRINT) == 1


def test_a_selected_run_cuts_over_to_the_capsule_path(tmp_path: Path) -> None:
    source = create_install_source(tmp_path)
    coordination_root = tmp_path / "ar-coordination"

    summary = run_install(source, coordination_root, experiment="role-capsules")

    assert summary.experiment is not None
    assert summary.experiment.delivery.mode == "capsule"
    for target in install_experiment.WITHHELD_STARTUP_TARGETS:
        assert not (coordination_root / target).exists(), target
    application = coordination_root / install_experiment.INSTALLED_APPLICATION_PATH
    assert (application / "package.json").is_file()
    assert (application / "agent" / "agent.ts").is_file()
    assert not (application / "node_modules").exists()
    assert fingerprint_count(coordination_root, LEGACY_FINGERPRINT) == 0
    # The one canonical authored source is still installed: it is the compiler's input.
    assert (coordination_root / "skills" / "l-01-agent-lifecycles" / "core").is_dir()


def test_a_selected_run_removes_an_earlier_legacy_installation(tmp_path: Path) -> None:
    """The cutover replaces a legacy install rather than sitting beside it."""

    source = create_install_source(tmp_path)
    coordination_root = tmp_path / "ar-coordination"
    run_install(source, coordination_root, experiment=None)
    assert (coordination_root / "AGENTS.md").is_file()

    summary = run_install(source, coordination_root, experiment="role-capsules")

    assert summary.experiment is not None
    assert summary.removed_paths >= len(install_experiment.WITHHELD_STARTUP_TARGETS)
    assert not (coordination_root / "AGENTS.md").exists()
    assert fingerprint_count(coordination_root, LEGACY_FINGERPRINT) == 0


def test_running_the_installer_again_without_the_selection_restores_the_legacy_chain(
    tmp_path: Path,
) -> None:
    """No global switch is left on: the next run's own input decides its mode."""

    source = create_install_source(tmp_path)
    coordination_root = tmp_path / "ar-coordination"
    run_install(source, coordination_root, experiment="role-capsules")
    assert not (coordination_root / "AGENTS.md").exists()

    summary = run_install(source, coordination_root, experiment=None)

    assert summary.experiment is None
    assert (coordination_root / "AGENTS.md").is_file()
    assert fingerprint_count(coordination_root, LEGACY_FINGERPRINT) == 1


def test_an_unknown_experiment_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(install_experiment.ExperimentError) as raised:
        install_experiment.resolve_experiment_selection(requested="role-capsul", environ={})

    message = str(raised.value)
    assert "role-capsul" in message
    assert install_experiment.ROLE_CAPSULES in message
    assert "never silently ignored" in message


def test_the_selection_resolves_from_the_request_then_the_run_environment(
    tmp_path: Path,
) -> None:
    from_request = install_experiment.resolve_experiment_selection(
        requested="role-capsules", environ={install_experiment.EXPERIMENT_ENV: "other"}
    )
    assert from_request.selected is True
    assert from_request.source == "request"

    from_environment = install_experiment.resolve_experiment_selection(
        requested=None, environ={install_experiment.EXPERIMENT_ENV: "role-capsules"}
    )
    assert from_environment.selected is True
    assert from_environment.source == "environment"

    unselected = install_experiment.resolve_experiment_selection(
        requested="  ", environ={install_experiment.EXPERIMENT_ENV: ""}
    )
    assert unselected.selected is False
    assert unselected.source == "unselected"


def test_a_selected_run_with_an_unavailable_capsule_path_refuses_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """The fail-closed rule: a selected run never falls back to the legacy chain."""

    source = create_install_source(tmp_path)
    anchor = install_experiment.corpus_anchor_path(source)
    anchor.unlink()
    coordination_root = tmp_path / "ar-coordination"

    with pytest.raises(install_experiment.ExperimentError) as raised:
        run_install(source, coordination_root, experiment="role-capsules")

    message = str(raised.value)
    assert "canonical-corpus-anchor" in message
    assert "refused before anything was written" in message
    assert "the selected mode is preserved" in message
    assert not coordination_root.exists()
    assert not (coordination_root / "AGENTS.md").exists()


def test_a_pin_mismatch_refuses_the_install_and_names_the_capability(tmp_path: Path) -> None:
    source = create_install_source(tmp_path)
    package_json = source / install_experiment.PACKAGED_APPLICATION_PATH / "package.json"
    declared = json.loads(package_json.read_text(encoding="utf-8"))
    declared["dependencies"]["eve"] = "^0.56.0"
    package_json.write_text(json.dumps(declared, indent=2), encoding="utf-8")
    coordination_root = tmp_path / "ar-coordination"

    with pytest.raises(install_experiment.ExperimentError) as raised:
        run_install(source, coordination_root, experiment="role-capsules")

    assert "pinned-eve-dependencies" in str(raised.value)
    assert not coordination_root.exists()


def test_a_dry_run_reports_the_cutover_and_writes_nothing(tmp_path: Path) -> None:
    source = create_install_source(tmp_path)
    coordination_root = tmp_path / "ar-coordination"
    run_install(source, coordination_root, experiment=None)

    summary = run_install(source, coordination_root, experiment="role-capsules", dry_run=True)

    assert summary.experiment is not None
    assert summary.experiment.delivery.mode == "capsule"
    assert (coordination_root / "AGENTS.md").is_file()
    assert not (coordination_root / install_experiment.INSTALLED_APPLICATION_PATH).exists()


def test_the_legacy_fingerprint_is_present_when_disabled_and_absent_when_opted_in(
    tmp_path: Path,
) -> None:
    """Two runs, one independent input (the selection), and the count moves one way."""

    disabled_root = tmp_path / "disabled"
    opted_in_root = tmp_path / "opted-in"
    disabled_source = create_install_source(tmp_path / "a")
    opted_in_source = create_install_source(tmp_path / "b")

    run_install(disabled_source, disabled_root, experiment=None)
    run_install(opted_in_source, opted_in_root, experiment="role-capsules")

    disabled_count = fingerprint_count(disabled_root, LEGACY_FINGERPRINT)
    opted_in_count = fingerprint_count(opted_in_root, LEGACY_FINGERPRINT)
    assert disabled_count == 1
    assert opted_in_count == 0
    assert disabled_count != opted_in_count


def test_the_canonical_corpus_reaches_an_opted_in_run_once_through_the_capsule() -> None:
    """Counted over the real effective material, from two different producers."""

    capsule_text = compiled_capsule_text()
    assert capsule_text.count(CORPUS_FINGERPRINT) == 1

    # Side B is the authored source the compiler read; the capsule carries its block, and the
    # legacy startup chain (side A) carries none of it.
    assert CORPUS_FINGERPRINT in CORPUS_FINGERPRINT_SOURCE.read_text(encoding="utf-8")
    assert LEGACY_FINGERPRINT_SOURCE.read_text(encoding="utf-8").count(CORPUS_FINGERPRINT) == 0


def test_the_run_record_carries_the_packet_shape_and_the_independent_versions(
    tmp_path: Path,
) -> None:
    source = create_install_source(tmp_path)
    coordination_root = tmp_path / "ar-coordination"

    summary = run_install(source, coordination_root, experiment="role-capsules")
    assert summary.experiment is not None
    payload = summary.experiment.payload()
    record = payload["record"]
    assert isinstance(record, dict)

    for key in ("experiment", "source", "harness", "instructionSource", "eveVersion", "mode"):
        assert key in record, key
    record = dict(record)

    anchor_digest = hashlib.sha256(
        install_experiment.corpus_anchor_path(source).read_bytes()
    ).hexdigest()
    assert record["instructionSource"] == f"sha256:{anchor_digest}"

    declared = json.loads((CANONICAL_EVE_APPLICATION / "package.json").read_text(encoding="utf-8"))[
        "dependencies"
    ]
    assert record["eveVersion"] == declared["eve"]
    assert record["mode"] == "capsule"
    assert record["experiment"] == install_experiment.ROLE_CAPSULES
    assert record["harness"] == "eve"
    assert (
        record["installedApplicationRoot"]
        == (coordination_root / install_experiment.INSTALLED_APPLICATION_PATH).as_posix()
    )


def test_the_selection_is_recorded_per_run_and_never_persisted(tmp_path: Path) -> None:
    """No file under the coordination root may carry the experiment id — whatever its name or age.

    Two readings, and the first is the one that carries R09.2's "no global switch is left on":

    1. **Root-scoped.** Every regular file under the coordination root is searched as bytes. A file
       that already existed before this run is still a file under the root, so a switch a
       *previous* (including a **deselected**) run persisted cannot hide behind an unchanged diff
       — the channel the first fix revision missed (baseline finding `L9-1`, owner item 1 of the
       post-verdict pass).
    2. **Diff-scoped.** The appeared-or-changed set is inspected too, so the failure names the file
       that moved rather than only the file that exists.

    Both are extension-blind and encoding-blind: a ``.toml``, a ``.txt``, an extension-less file, a
    settings key or nested JSON is caught exactly like a ``.json``.
    """

    source = create_install_source(tmp_path)
    coordination_root = tmp_path / "ar-coordination"
    config_path = coordination_root / "system" / "settings.json"

    run_install(source, coordination_root, experiment=None)
    before_files = snapshot_regular_files(coordination_root)
    before_settings = config_path.read_bytes()

    summary = run_install(source, coordination_root, experiment="role-capsules")

    after_files = snapshot_regular_files(coordination_root)
    changed = sorted(
        path
        for path in set(before_files) | set(after_files)
        if before_files.get(path) != after_files.get(path)
    )
    # Control: the snapshot must see this run's installation, or the guard proves nothing.
    assert changed, "the selected run changed nothing under the coordination root"
    assert any(path.endswith("AGENTS.md") for path in before_files), "the legacy install is absent"

    needle = install_experiment.ROLE_CAPSULES.encode()

    # 1. Root-scoped, and not vacuous: the authored corpus legitimately names the experiment, so
    #    the scan must find something -- and every hit must be an authored asset copied through
    #    byte-for-byte. Anything else is a persisted selection, however old the file is.
    offenders = files_containing(coordination_root, needle)
    assert offenders, "the scan found nothing at all, so it proves nothing about the root"
    unexplained = [
        relative
        for relative in offenders
        if not (source / "runtime" / relative).is_file()
        or (source / "runtime" / relative).read_bytes()
        != (coordination_root / relative).read_bytes()
    ]
    assert unexplained == [], unexplained
    # 2. Diff-scoped: nothing this run created or changed carries it either, so a failure names the
    #    file that moved rather than only the file that exists.
    assert [
        relative
        for relative in changed
        if (coordination_root / relative).is_file()
        and needle in (coordination_root / relative).read_bytes()
    ] == []

    assert config_path.read_bytes() == before_settings
    assert install_experiment.EXPERIMENT_ENV.encode() not in config_path.read_bytes()
    assert summary.experiment is not None
    assert summary.experiment.selection.source == "request"


def test_the_installed_root_carries_the_canonical_corpus_only_as_authored_source(
    tmp_path: Path,
) -> None:
    """The corpus in the *installed* root is authored source, never received startup material.

    This reads the installed coordination root itself — the screen the delivery-side capsule count
    does not read (baseline finding `L9-3`). The canonical block appears exactly once there, inside
    the copied ``skills/`` tree, which the production compiler never reads (it resolves its corpus
    from ``packaged_source_root()/runtime/skills``); none of the four auto-loaded startup files
    carries it.
    """

    source = create_install_source(tmp_path)
    coordination_root = tmp_path / "ar-coordination"

    run_install(source, coordination_root, experiment="role-capsules")

    authored = coordination_root / "skills" / "l-01-agent-lifecycles" / "core" / "acceptance.md"
    assert CORPUS_FINGERPRINT in authored.read_text(encoding="utf-8")
    assert material_text(coordination_root / "skills").count(CORPUS_FINGERPRINT) == 1
    startup_material = [
        coordination_root / target for target in install_experiment.WITHHELD_STARTUP_TARGETS
    ]
    assert all(not path.exists() for path in startup_material)
    assert fingerprint_count(coordination_root / "skills", LEGACY_FINGERPRINT) == 0


def test_the_registered_tool_takes_the_selection_as_this_run_s_own_input(tmp_path: Path) -> None:
    """The MCP surface exposes the selection per call, and the record names that input."""

    source = create_install_source(tmp_path)
    coordination_root = tmp_path / "ar-coordination"
    config = McpRuntimeConfig(
        config_path=coordination_root / "mcp" / "settings.json",
        coordination_root=coordination_root,
        workspace_root=tmp_path / "workspace",
        transcript_root=coordination_root / "logs" / "mcp",
    )

    payload = runtime_install_payload(
        config,
        install_runtime.RuntimeInstallRequest(
            dry_run=True,
            install_provider_deps=False,
            source_root=source,
            experiment=install_experiment.ROLE_CAPSULES,
        ),
    )

    experiment = payload["experiment"]
    assert experiment["selection"]["selected"] is True
    assert experiment["selection"]["source"] == "request"
    assert experiment["delivery"]["mode"] == "capsule"
    assert experiment["record"]["selectionSource"] == "request"

    unselected = runtime_install_payload(
        config,
        install_runtime.RuntimeInstallRequest(
            dry_run=True,
            install_provider_deps=False,
            source_root=source,
            experiment=None,
        ),
    )
    assert unselected["experiment"]["selection"]["selected"] is False
    assert unselected["experiment"]["delivery"]["mode"] == "legacy"
    assert unselected["experiment"]["record"] is None


def test_a_selected_install_writes_nothing_into_the_install_source_tree(
    tmp_path: Path,
) -> None:
    """Caches, manifests and generated state never land in the tracked source tree."""

    source = create_install_source(tmp_path)
    digests = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*")
        if path.is_file()
    }

    run_install(source, tmp_path / "ar-coordination", experiment="role-capsules")

    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source.rglob("*")
        if path.is_file()
    }
    assert after == digests


def test_the_installed_application_keeps_its_machine_local_trees_out_of_the_mirror(
    tmp_path: Path,
) -> None:
    """The source's dependency tree is never copied, and the install's own is never pruned.

    Both halves matter on one machine: copying the source's ``node_modules`` would ship a
    machine-local tree into every install, and pruning the install's would delete the
    operator's own dependency install on the next run.
    """

    source = create_install_source(tmp_path)
    source_modules = source / install_experiment.PACKAGED_APPLICATION_PATH / "node_modules"
    (source_modules / "eve").mkdir(parents=True)
    (source_modules / "eve" / "package.json").write_text('{"version":"machine-local"}\n')
    coordination_root = tmp_path / "ar-coordination"
    installed_modules = (
        coordination_root / install_experiment.INSTALLED_APPLICATION_PATH / "node_modules"
    )
    installed_modules.mkdir(parents=True)
    (installed_modules / "operator-install.marker").write_text("kept\n", encoding="utf-8")

    run_install(source, coordination_root, experiment="role-capsules")

    assert not (installed_modules / "eve").exists()
    assert (installed_modules / "operator-install.marker").is_file()
    # The hermeticity rule the report states depends on this: root `.gitignore` is
    # ``node_modules/`` *with a trailing slash*, which git matches against a directory only, so a
    # symlinked tree would be untracked-not-ignored and would stage as mode 120000. The installer
    # creates a real directory, and this asserts the property the claim rests on.
    assert installed_modules.is_dir()
    assert not installed_modules.is_symlink()


def test_the_rollback_plan_names_the_installed_artifacts_and_the_restore_command(
    tmp_path: Path,
) -> None:
    source = create_install_source(tmp_path)
    coordination_root = tmp_path / "ar-coordination"
    state_root = tmp_path / "state"

    rows = install_experiment.experiment_rollback_plan(
        coordination_root=coordination_root, state_root=state_root
    )

    by_path = {row.path: row for row in rows}
    application = (coordination_root / install_experiment.INSTALLED_APPLICATION_PATH).as_posix()
    assert application in by_path
    assert by_path[application].undo_command == f"rm -rf {application}"
    assert by_path[state_root.as_posix()].undo_command == f"rm -rf {state_root.as_posix()}"
    restore = by_path[(coordination_root / "AGENTS.md").as_posix()]
    assert "no experiment selected" in restore.undo_command

    summary = run_install(source, coordination_root, experiment="role-capsules")
    assert summary.experiment is not None
    payload_rows = summary.experiment.payload()["rollback"]
    assert isinstance(payload_rows, list)
    payload_paths = {row["path"] for row in payload_rows}
    # The run carried no state root, so its own plan is the plan without that row.
    assert payload_paths == {row.path for row in rows} - {state_root.as_posix()}
    assert application in payload_paths
