"""The removed ``internal`` memory mode: refused by name, reported, never substituted.

``internal`` memory -- a repo-sidecar root at ``<code-repository-root>/ar-memory`` -- was removed
from the product. The supported set is ``external`` and ``disabled``. Three properties are pinned
here, and each one names the failure it prevents:

1. **Refused by name.** Every surface that used to accept the vocabulary answers with the typed
   ``memory-mode-unsupported`` status, carrying the removal, the supported set and the route out.
   The failure this prevents is the silent substitution: a caller asking for ``internal`` and
   receiving ``external`` (or a default) without being told the mode is gone.
2. **Reported, not migrated.** Existing state that records the mode -- a contract file, a settings
   file, a memory root -- is named by its exact artifact and left byte-identical on disk. The
   failure this prevents is a silent rewrite of a developer's contract or the deletion of a memory
   root.
3. **The supported set is untouched.** ``external`` and ``disabled`` still resolve, still write and
   still read, in the application layer and through the CLI the developer actually types. The
   failure this prevents is a removal that takes the working modes with it.

Both sides of every comparison below come from different sources: the expected supported set is
written out literally here, while the observed set is whatever the raising surface produced.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from agents_remember.application.coordination_tools import _topology
from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractRefused,
    admit_configured_contract,
    admit_configured_terminal_contract,
)
from agents_remember.application.lifecycle.direct_landing import (
    _read_direct_contract,
    direct_landing_tool,
)
from agents_remember.application.lifecycle.lifecycle_operation_location import (
    configured_lifecycle_operation_location,
)
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.task_docs.task_unstarted_evidence import _contract_fact
from agents_remember.application.worktree_status import worktree_status_packet
from agents_remember.errors import MemoryModeUnsupportedError
from agents_remember.kernel.coordination_context.models import MemoryMode, Topology
from agents_remember.kernel.coordination_context.paths import (
    infer_topology_from_onboarding_root,
    memory_roots_from_settings,
)
from agents_remember.kernel.coordination_context.resolver import detect_coordination_selection
from agents_remember.kernel.memory_mode import (
    SUPPORTED_MEMORY_MODES,
    SUPPORTED_TOPOLOGIES,
    require_supported_memory_mode,
    require_supported_topology,
)
from agents_remember.kernel.primitives.runtime_config import default_memory_root, load_config
from agents_remember.mcp.registration import worktrees as worktrees_registration
from agents_remember.mcp.tools.worktree import worktree_status_payload
from agents_remember.memory.baseline import _normalize_topology
from agents_remember.tasks import SubTaskRef
from agents_remember.worktrees.direct_landing import DirectLandingRequest
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cli import build_parser
from agents_remember.worktrees.modules.start import start_result
from agents_remember.worktrees.modules.startup.master_series_admission import (
    memory_mode_for_repository,
)
from agents_remember.worktrees.task_leaf_binding import LeafTaskBinding
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    load_contract,
    write_contract,
)
from lifecycle_enclosure_test_support import (
    publish_test_enclosure,
    terminalize_test_enclosure,
)
from test_worktree_support import git, init_repo

# The supported set, written here rather than imported, so a change to the vocabulary has to be
# made in two places and this file cannot silently agree with a mistake.
EXPECTED_MODES = ("external", "disabled")
EXPECTED_TOPOLOGIES = ("external",)
REMOVED = "internal"


def _refusal(call) -> MemoryModeUnsupportedError:
    with pytest.raises(MemoryModeUnsupportedError) as raised:
        call()
    return raised.value


def _assert_names_the_removal(error: MemoryModeUnsupportedError) -> None:
    """The refusal must be actionable, not just typed: removal, supported set, route out."""
    assert error.status == "memory-mode-unsupported"
    assert error.requested == REMOVED
    assert error.supported == EXPECTED_MODES
    assert REMOVED in error.detail
    assert "removed" in error.detail
    for mode in EXPECTED_MODES:
        assert f"`{mode}`" in error.detail
    assert "c-00-initialize-memory-repo" in error.detail
    assert error.remedies


# --------------------------------------------------------------------------------------
# The vocabulary itself
# --------------------------------------------------------------------------------------


def test_the_declared_vocabulary_has_no_removed_member() -> None:
    assert SUPPORTED_MEMORY_MODES == EXPECTED_MODES
    assert SUPPORTED_TOPOLOGIES == EXPECTED_TOPOLOGIES
    assert MemoryMode is not None and Topology is not None


def test_require_supported_memory_mode_refuses_the_removed_member() -> None:
    error = _refusal(lambda: require_supported_memory_mode(REMOVED))
    _assert_names_the_removal(error)
    assert error.artifact is None


def test_require_supported_memory_mode_accepts_the_supported_set() -> None:
    assert require_supported_memory_mode("external") == "external"
    assert require_supported_memory_mode("disabled") == "disabled"
    assert require_supported_topology("external") == "external"


def test_require_supported_topology_keeps_unknown_tokens_distinct_from_the_removal() -> None:
    """An unknown token is invalid input, not a removed mode -- the two must not be conflated."""
    with pytest.raises(ValueError) as raised:
        require_supported_topology("sidecar")
    assert not isinstance(raised.value, MemoryModeUnsupportedError)
    assert "external" in str(raised.value)


# --------------------------------------------------------------------------------------
# Code surfaces that used to accept it
# --------------------------------------------------------------------------------------


def test_worktree_start_refuses_an_explicit_removed_memory_mode() -> None:
    """The public start status, before any worktree or contract exists."""
    result = start_result(
        WorktreeArgs(
            code_repository_name="repo-a",
            code_repository_root=Path("/nonexistent/code"),
            task_name="task",
            worktree_name="leaf",
            memory_mode=REMOVED,
        )
    )
    assert result.returncode == 2
    assert result.payload["state"] == "memory-mode-unsupported"
    assert result.payload["status"] == "memory-mode-unsupported"
    assert result.payload["requested"] == REMOVED
    assert result.payload["supported"] == list(EXPECTED_MODES)
    assert result.payload["remedies"]


def test_worktree_start_refuses_a_removed_topology_argument() -> None:
    result = start_result(
        WorktreeArgs(
            code_repository_name="repo-a",
            code_repository_root=Path("/nonexistent/code"),
            task_name="task",
            worktree_name="leaf",
            topology=REMOVED,  # type: ignore[arg-type]
        )
    )
    assert result.returncode == 2
    assert result.payload["state"] == "memory-mode-unsupported"


def test_resolver_refuses_a_requested_removed_topology() -> None:
    error = _refusal(
        lambda: detect_coordination_selection(
            "repo-a",
            Path("/nonexistent/code"),
            requested_topology=REMOVED,  # type: ignore[arg-type]
        )
    )
    _assert_names_the_removal(error)


def test_resolver_reports_a_repository_that_still_carries_the_removed_root(tmp_path: Path) -> None:
    """Existing layout: named by exact path and left on disk."""
    code_repo = tmp_path / "code"
    code_repo.mkdir()
    legacy_root = code_repo / "ar-memory"
    legacy_root.mkdir()
    (legacy_root / "onboarding").mkdir()
    coordination_root = tmp_path / "coordination"
    (coordination_root / "memory-repos" / "ar-repo-a").mkdir(parents=True)

    error = _refusal(
        lambda: detect_coordination_selection(
            "repo-a", code_repo, coordination_root_hint=coordination_root
        )
    )
    _assert_names_the_removal(error)
    assert error.artifact == legacy_root.resolve().as_posix()
    assert legacy_root.is_dir(), "the removed memory root must be reported, never deleted"


def test_settings_inside_the_removed_root_are_reported_by_their_own_path(tmp_path: Path) -> None:
    settings_path = tmp_path / "code" / "ar-memory" / "system" / "settings.md"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("# Settings\n", encoding="utf-8")

    error = _refusal(lambda: memory_roots_from_settings(settings_path, "repo-a"))
    _assert_names_the_removal(error)
    assert error.artifact == settings_path.as_posix()
    assert settings_path.is_file()


def test_onboarding_root_inside_the_removed_root_is_reported(tmp_path: Path) -> None:
    onboarding_root = tmp_path / "code" / "ar-memory" / "onboarding"
    onboarding_root.mkdir(parents=True)

    error = _refusal(lambda: infer_topology_from_onboarding_root(onboarding_root))
    _assert_names_the_removal(error)
    assert error.artifact == onboarding_root.as_posix()


def test_configured_memory_root_refuses_the_removed_layout(tmp_path: Path) -> None:
    code_repo = tmp_path / "code"
    legacy_root = code_repo / "ar-memory"
    legacy_root.mkdir(parents=True)

    error = _refusal(lambda: default_memory_root(code_repo, tmp_path / "coordination", "repo-a"))
    _assert_names_the_removal(error)
    assert error.artifact == legacy_root.resolve().as_posix()
    assert legacy_root.is_dir()


def test_contract_vocabulary_derivation_refuses_the_removed_root(tmp_path: Path) -> None:
    code_repo = tmp_path / "code"
    legacy_root = code_repo / "ar-memory"
    legacy_root.mkdir(parents=True)

    error = _refusal(lambda: memory_mode_for_repository(code_repo, legacy_root))
    _assert_names_the_removal(error)
    assert error.artifact == legacy_root.resolve().as_posix()


def test_resolve_context_tool_refuses_the_removed_topology() -> None:
    error = _refusal(lambda: _topology(REMOVED))
    _assert_names_the_removal(error)
    assert _topology(None) is None
    assert _topology("external") == "external"


def test_baseline_topology_flag_refuses_the_removed_topology() -> None:
    error = _refusal(lambda: _normalize_topology(REMOVED))
    _assert_names_the_removal(error)
    assert _normalize_topology(None) is None
    assert _normalize_topology("external") == "external"


def test_contract_writer_refuses_a_removed_memory_mode_request(tmp_path: Path) -> None:
    """Nothing in this package may put the removed token on disk."""
    error = _refusal(
        lambda: default_contract(
            ContractTask(
                name="task",
                repo_name="repo-a",
                coordination_root=tmp_path / "coordination",
                workflow_kind="light-task",
                memory_mode=REMOVED,
            ),
            leaf=LeafIdentity(worktree_name="leaf", leaf_id="leaf-1"),
            code=RepoBranchPlan(
                repo_path=tmp_path / "code",
                source_branch="main",
                work_branch="ar/leaf",
                base_commit="a" * 40,
            ),
        )
    )
    _assert_names_the_removal(error)


# --------------------------------------------------------------------------------------
# Existing state is reported, never rewritten
# --------------------------------------------------------------------------------------


def _disabled_contract(root: Path):
    code_repo = root / "code"
    init_repo(code_repo, "main")
    return default_contract(
        ContractTask(
            name="task",
            repo_name="repo-a",
            coordination_root=root / "coordination",
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        leaf=LeafIdentity(worktree_name="leaf", leaf_id="leaf-1"),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="main",
            work_branch="ar/leaf",
            base_commit="a" * 40,
        ),
    )


def test_a_contract_recording_the_removed_mode_is_reported_and_left_byte_identical(
    tmp_path: Path,
) -> None:
    contract = _disabled_contract(tmp_path)
    write_contract(contract.contract_path, contract)
    text = contract.contract_path.read_text(encoding="utf-8")
    assert "memory_mode: disabled" in text
    contract.contract_path.write_text(
        text.replace("memory_mode: disabled", "memory_mode: internal", 1), encoding="utf-8"
    )
    before = contract.contract_path.read_bytes()

    error = _refusal(lambda: load_contract(contract.contract_path))
    _assert_names_the_removal(error)
    assert error.artifact == contract.contract_path.as_posix()
    assert contract.contract_path.read_bytes() == before, "existing state must not be rewritten"


def test_a_removed_token_is_refused_while_an_unknown_token_still_degrades(tmp_path: Path) -> None:
    """The reader's tolerance is unchanged for unknown tokens; only the removal is a refusal."""
    contract = _disabled_contract(tmp_path)
    write_contract(contract.contract_path, contract)
    text = contract.contract_path.read_text(encoding="utf-8")
    contract.contract_path.write_text(
        text.replace("memory_mode: disabled", "memory_mode: hybrid", 1), encoding="utf-8"
    )
    loaded = load_contract(contract.contract_path)
    assert loaded.memory_mode == "disabled"
    assert loaded.unknown_cells


# --------------------------------------------------------------------------------------
# external and disabled are unregressed
# --------------------------------------------------------------------------------------


def test_external_selection_still_resolves(tmp_path: Path) -> None:
    code_repo = tmp_path / "code"
    code_repo.mkdir()
    memory_root = tmp_path / "coordination" / "memory-repos" / "ar-repo-a"
    memory_root.mkdir(parents=True)

    selection = detect_coordination_selection(
        "repo-a", code_repo, coordination_root_hint=tmp_path / "coordination"
    )
    assert selection.topology == "external"
    assert selection.memory_root == memory_root.resolve()

    requested = detect_coordination_selection(
        "repo-a",
        code_repo,
        requested_topology="external",
        coordination_root_hint=tmp_path / "coordination",
    )
    assert requested.topology == "external"


def test_default_memory_root_is_the_external_root_when_no_removed_layout_exists(
    tmp_path: Path,
) -> None:
    code_repo = tmp_path / "code"
    code_repo.mkdir()
    resolved = default_memory_root(code_repo, tmp_path / "coordination", "repo-a")
    assert resolved == tmp_path / "coordination" / "memory-repos" / "ar-repo-a"


def test_a_disabled_contract_round_trips(tmp_path: Path) -> None:
    contract = _disabled_contract(tmp_path)
    write_contract(contract.contract_path, contract)
    loaded = load_contract(contract.contract_path)
    assert loaded.memory_mode == "disabled"
    assert loaded.unknown_cells == ()
    assert loaded.contract_path == contract.contract_path


def test_cli_never_advertises_the_removed_member(capsys: pytest.CaptureFixture[str]) -> None:
    """The documented call surface is the rendered help, so that is what is checked."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["start", "--help"])
    help_text = capsys.readouterr().out
    assert REMOVED not in help_text
    assert "external" in help_text and "disabled" in help_text


def test_cli_parses_the_supported_set_and_hands_a_removed_token_to_the_typed_refusal() -> None:
    supported = build_parser().parse_args(
        ["start", "--worktree-name", "leaf", "--memory-mode", "disabled"]
    )
    assert supported.memory_mode == "disabled"

    # The token still parses here on purpose: argparse `choices` would reject it before the
    # application could answer with its own typed status, and a bare "invalid choice" does not
    # name the removal. The refusal is asserted where a developer actually meets it.
    parsed = build_parser().parse_args(
        ["start", "--worktree-name", "leaf", "--memory-mode", REMOVED]
    )
    refused = start_result(
        WorktreeArgs(
            code_repository_name="repo-a",
            code_repository_root=Path("/nonexistent/code"),
            task_name="task",
            worktree_name=parsed.worktree_name,
            memory_mode=parsed.memory_mode,
        )
    )
    assert refused.returncode == 2
    assert refused.payload["state"] == "memory-mode-unsupported"


def test_the_start_tool_documentation_no_longer_offers_the_removed_mode() -> None:
    """The MCP docstring is a documented call surface, so it is checked as one."""
    import inspect  # noqa: PLC0415 - one local import keeps the collector light

    source = inspect.getsource(worktrees_registration)
    assert "memory_mode is 'external' or 'disabled'" in source
    assert "memory_mode is 'internal'" not in source


# --------------------------------------------------------------------------------------
# L12R-1: the typed refusal must reach the operator on every contract-report surface
# --------------------------------------------------------------------------------------
#
# Each surface below reads the contract inside `except (ContractError, OSError, UnicodeError,
# ValueError)`, and `MemoryModeUnsupportedError` subclasses `ValueError` — so without an explicit
# clause ahead of the generic one the operator is told only that the document is "unreadable or
# invalid". These cases assert the published payload, not the raised error: the value the contract
# records, the supported set and the migration route must all be visible.
#
# The fixture is the repository boundary these surfaces live on (a published enclosure over a real
# code and memory repository), so the cases carry the integration marker.

REPO_NAME = "repo-a"
_LEAF_ID = "260698-l1"


def _published_fixture(root: Path, *, rewrite_mode: bool = True) -> tuple[WorktreeContract, Path]:
    """Publish one real enclosure, then make its contract record the removed mode.

    ``rewrite_mode=False`` stops after publication, for a case that has to drive another
    production step over the readable contract before the removed mode appears on disk.
    """
    workspace = root / "workspace"
    code_repo = workspace / REPO_NAME
    coordination_root = workspace / "ar-coordination"
    memory_repo = coordination_root / "memory-repos" / f"ar-{REPO_NAME}"
    base = init_repo(code_repo, "main")
    memory_base = init_repo(memory_repo, "main")
    git(code_repo, "branch", "super", "main")
    git(code_repo, "branch", "ar/01-demo-leaf", "super")
    contract = default_contract(
        ContractTask(
            name="260698_demo-series",
            repo_name=REPO_NAME,
            coordination_root=coordination_root,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(worktree_name="01-demo-leaf", leaf_id=_LEAF_ID),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="super",
            work_branch="ar/01-demo-leaf",
            base_commit=base,
        ),
        memory=RepoBranchPlan(
            repo_path=memory_repo,
            source_branch="main",
            work_branch="ar/01-demo-leaf",
            base_commit=memory_base,
        ),
    )
    write_contract(contract.contract_path, contract)
    location = publish_test_enclosure(contract, contract.contract_path.read_text(encoding="utf-8"))
    if rewrite_mode:
        remove_contract_mode(contract.contract_path, "external")
    return contract, location.contract_path


def remove_contract_mode(contract_path: Path, written: str) -> None:
    """Rewrite one written cell to the removed mode, so the reader meets real existing state."""
    text = contract_path.read_text(encoding="utf-8")
    assert f"memory_mode: {written}" in text
    contract_path.write_text(
        text.replace(f"memory_mode: {written}", "memory_mode: internal", 1), encoding="utf-8"
    )


def _config(root: Path, contract: WorktreeContract, *, direct_execution: bool = False):
    path = root / "mcp-settings.json"
    document: dict[str, object] = {
        "version": 1,
        "coordinationRoot": contract.coordination_root.as_posix(),
        "workspaceRoot": (root / "workspace").as_posix(),
        "repositories": {REPO_NAME: {}},
        "providers": {},
        "timeoutCaps": {"toolSeconds": 30, "providerSetupSeconds": 1800},
        "benchmarksEnabled": False,
    }
    if direct_execution:
        document["directExecutionEnabled"] = True
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return load_config(path)


def _assert_operator_payload(payload: Mapping[str, Any], *, artifact: str) -> None:
    """The removal facts an operator needs, wherever the payload keeps them."""
    assert payload["status"] == "memory-mode-unsupported"
    assert payload["requested"] == REMOVED
    assert payload["supported"] == list(EXPECTED_MODES)
    assert payload["remedies"]
    assert artifact in str(payload["detail"])


@pytest.mark.integration
def test_worktree_status_packet_reports_the_removed_mode_to_the_operator(tmp_path: Path) -> None:
    contract, contract_path = _published_fixture(tmp_path)
    summary = worktree_status_packet(_config(tmp_path, contract), contract_path)

    assert summary.state == "invalidContract"
    assert summary.status == "memory-mode-unsupported"
    assert summary.error is not None and contract_path.as_posix() in summary.error
    evidence = summary.errorEvidence
    assert evidence is not None
    observed = cast("Mapping[str, Any]", evidence["observed"])
    assert observed["requested"] == REMOVED
    assert observed["supported"] == list(EXPECTED_MODES)
    assert observed["remedies"]


@pytest.mark.integration
def test_worktree_status_payload_reports_the_removed_mode_to_the_operator(tmp_path: Path) -> None:
    """The assembled tool payload, not just the context packet's summary."""
    contract, contract_path = _published_fixture(tmp_path)
    payload = worktree_status_payload(
        _config(tmp_path, contract),
        TaskRef(repo_id=REPO_NAME, contract_path=contract_path.as_posix()),
    )
    _assert_operator_payload(payload, artifact=contract_path.as_posix())


@pytest.mark.integration
def test_configured_admission_reports_the_removed_mode_with_its_own_reason(tmp_path: Path) -> None:
    contract, contract_path = _published_fixture(tmp_path)
    refused = admit_configured_contract(_config(tmp_path, contract), contract_path.as_posix())

    assert isinstance(refused, ConfiguredContractRefused)
    assert refused.reason == "memory-mode-unsupported"
    assert refused.status == "memory-mode-unsupported"
    assert contract_path.as_posix() in refused.detail
    assert refused.expected["supported"] == list(EXPECTED_MODES)
    observed = cast("Mapping[str, Any]", refused.observed["observed"])
    assert observed["requested"] == REMOVED


@pytest.mark.integration
def test_terminal_admission_reports_the_removed_mode_on_a_surviving_contract(
    tmp_path: Path,
) -> None:
    contract, contract_path = _published_fixture(tmp_path, rewrite_mode=False)
    _, location = configured_lifecycle_operation_location(
        _config(tmp_path, contract), contract_path
    )
    terminalize_test_enclosure(location)
    # The mode is recorded after the archive: the archive itself reads the contract, and the
    # state this case is about is the surviving contract of an already-terminal enclosure.
    remove_contract_mode(contract_path, "external")

    refused = admit_configured_terminal_contract(
        _config(tmp_path, contract), contract_path.as_posix()
    )
    assert isinstance(refused, ConfiguredContractRefused)
    assert refused.reason == "memory-mode-unsupported"
    assert refused.status == "memory-mode-unsupported"
    observed = cast("Mapping[str, Any]", refused.observed["observed"])
    assert observed["requested"] == REMOVED
    assert observed["remedies"]


@pytest.mark.integration
def test_direct_landing_reports_the_removed_mode_to_the_operator(tmp_path: Path) -> None:
    contract, contract_path = _published_fixture(tmp_path)
    payload = direct_landing_tool(
        _config(tmp_path, contract, direct_execution=True),
        DirectLandingRequest(contract_path=contract_path.as_posix(), code_commit="a" * 40),
    )
    _assert_operator_payload(payload, artifact=contract_path.as_posix())


@pytest.mark.integration
def test_the_direct_landing_reader_answers_with_the_refusal(tmp_path: Path) -> None:
    """The deeper net behind admission: the ``:152`` catch itself, reached directly."""
    contract, contract_path = _published_fixture(tmp_path)
    config = _config(tmp_path, contract, direct_execution=True)

    answer = _read_direct_contract(config, contract_path)
    assert isinstance(answer, dict)
    _assert_operator_payload(answer, artifact=contract_path.as_posix())


@pytest.mark.integration
def test_unstarted_evidence_reports_the_removed_mode_as_its_own_fact(tmp_path: Path) -> None:
    contract, _contract_path = _published_fixture(tmp_path)
    binding = LeafTaskBinding(
        coordination_root=contract.coordination_root,
        repo_id=REPO_NAME,
        task_name="260698_demo-series",
        task_root=contract.task_root,
        parent_path=contract.task_root,
        parent=cast("Any", None),
        row=SubTaskRef(number=_LEAF_ID, name="leaf"),
        leaf_json_path=contract.task_root / "leaf.json",
        leaf_markdown_path=contract.task_root / "leaf.md",
        leaf=None,
        task_ref=cast("Any", None),
    )
    facts: list[dict[str, Any]] = []
    severity: list[Literal["started", "ambiguous"]] = []

    got, failure = _contract_fact(binding, facts, severity)

    assert got is None
    assert failure is not None
    assert failure["state"] == "removed-mode"
    assert failure["requested"] == REMOVED
    assert failure["supported"] == list(EXPECTED_MODES)
    assert failure["remedies"]
    assert severity == ["ambiguous"]
    mode_facts = [fact for fact in facts if fact["kind"] == "memoryMode"]
    assert mode_facts and mode_facts[-1]["state"] == "removed"


@pytest.mark.integration
def test_a_removed_mode_answer_never_claims_publication_was_lost(tmp_path: Path) -> None:
    """The shared observation synthesises a publication-loss decision for any read failure.

    Nothing was lost here -- the contract is present and readable -- so that synthesis must not
    survive as the operator's reason on either worktree_status path.
    """
    contract, contract_path = _published_fixture(tmp_path)
    config = _config(tmp_path, contract)

    summary = worktree_status_packet(config, contract_path)
    payload = worktree_status_payload(
        config, TaskRef(repo_id=REPO_NAME, contract_path=contract_path.as_posix())
    )

    for answer in (summary.status, payload["status"], payload["state"]):
        assert answer == "memory-mode-unsupported"


# --------------------------------------------------------------------------------------
# L12R-2: an unknown token is invalid input, not a removal
# --------------------------------------------------------------------------------------


def test_the_flag_narrowers_do_not_report_a_typo_as_a_removal() -> None:
    """Contrast cases for the two CLI narrowers, against the shared helper's behaviour."""
    assert _topology("external") == "external"
    assert _normalize_topology("external") == "external"

    for narrower in (_topology, _normalize_topology):
        with pytest.raises(ValueError) as raised:
            narrower("bogus")
        assert not isinstance(raised.value, MemoryModeUnsupportedError), narrower.__name__
        assert "external" in str(raised.value)

        with pytest.raises(MemoryModeUnsupportedError):
            narrower(REMOVED)


# --------------------------------------------------------------------------------------
# L12R-3: the instruction and documentation plane is guarded by a failing case
# --------------------------------------------------------------------------------------
#
# The removal's strongest claim -- that nothing still *teaches* the mode -- had no executable
# guard: a reverted canonical skill propagated to all nine generated copies left every generator
# check green, and a wholesale revert of `docs/architecture.md` left the whole unit population
# green. Each row below pairs a phrase the correction introduced (which must be present) with
# phrases only the old doctrine used (which must be absent). A revert therefore fails here, not
# only in review.

_REPO_ROOT = Path(__file__).resolve().parents[2]

CORRECTED_SURFACES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "skills/c-00-initialize-memory-repo/SKILL.md",
        (
            "Defaults to repo-local internal memory",
            "`topology`: `internal` by default",
            "Default internal memory:",
            "Fresh Internal Memory",
            "Set `memory_root` to `<code_repository_root>/ar-memory`",
        ),
        "It is the only supported topology",
    ),
    (
        "mcp/src/agents_remember/package_data/runtime/skills/c-00-initialize-memory-repo/SKILL.md",
        (
            "Defaults to repo-local internal memory",
            "`topology`: `internal` by default",
            "Default internal memory:",
            "Fresh Internal Memory",
        ),
        "It is the only supported topology",
    ),
    (
        "skills/c-13-install-and-onboard/SKILL.md",
        ("(internal by default", "silently choose internal memory"),
        "Removed-layout note",
    ),
    (
        "skills/c-08-ar-coordination-context-resolver/SKILL.md",
        ("using repo-local internal memory or selected external memory",),
        "only supported topology",
    ),
    (
        "skills/c-03-repo-bootstrap/templates/bootstrap-state-template.md",
        ("internal / external / mixed",),
        "external / mixed",
    ),
    (
        "skills/c-03-repo-bootstrap/templates/cross-repo-boundary-pack-template.md",
        ("internal / external / mixed",),
        "external / mixed",
    ),
    (
        "skills/c-03-repo-bootstrap/templates/bootstrap-input-ledger-template.md",
        ("internal / external / mixed",),
        "external / mixed",
    ),
    (
        "docs/architecture.md",
        (
            "Internal memory is the default",
            "my-app/ar-memory/",
            "repo-local internal memory at `<repo>/ar-memory/`",
        ),
        "External memory is the only supported topology",
    ),
    (
        "docs/concepts.md",
        ("Internal memory is the default",),
        "External memory is the only supported topology",
    ),
    (
        "docs/getting-started.md",
        (
            "creates repo-local internal memory",
            "the recommended default is repo-local internal memory",
        ),
        "External memory is the only supported topology",
    ),
    (
        "docs/FAQ.md",
        ("Most users should start with internal memory",),
        "was removed from the product",
    ),
    (
        "docs/reference/settings-json.md",
        ("## Internal Memory Example",),
        "## Memory Repo Example",
    ),
    (
        "system/defaults/examples/memory-repo/settings.md",
        ("- repo-local internal memory:",),
        "was removed from the product",
    ),
)


@pytest.mark.parametrize(
    ("relative_path", "forbidden", "required"),
    CORRECTED_SURFACES,
    ids=[row[0].rsplit("/", 2)[-1] + "@" + row[0].split("/")[0] for row in CORRECTED_SURFACES],
)
def test_the_instruction_and_documentation_plane_never_teaches_the_removed_default(
    relative_path: str, forbidden: tuple[str, ...], required: str
) -> None:
    text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
    for phrase in forbidden:
        assert phrase not in text, f"{relative_path} re-teaches the removed default: {phrase!r}"
    assert required in text, f"{relative_path} lost the corrected statement: {required!r}"


def test_the_generated_skill_copy_carries_the_canonical_correction() -> None:
    """The copy `runtime_install` serves is checked against the tree it is generated from."""
    canonical = (_REPO_ROOT / "skills/c-00-initialize-memory-repo/SKILL.md").read_bytes()
    generated = (
        _REPO_ROOT
        / "mcp/src/agents_remember/package_data/runtime/skills/c-00-initialize-memory-repo/SKILL.md"
    ).read_bytes()
    assert generated == canonical
