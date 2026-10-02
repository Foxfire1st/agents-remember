"""The cutover's carried completions (MIK-R37; L37 P1).

* the cutover lock (MIK-R09 rule 6, second bullet; MIK-R24 rule 9; MIK-R37 rule 6) at every write,
  memory-quality, sync, closeout and landing route, inert in a repository that holds no converted
  memory and never in the way of the converting candidate;
* the frozen database (MIK-R37 rule 3);
* no production read selects the database on a converted tree (L23 F8);
* ``origin.handoff.evidence`` is a list of strings (L24 carry).

The routes that need the CLI or the MCP tool surface are tested beside their own suites, which
already consume those surfaces: ``knowledge-bootstrap`` and the crossing owner's rows in
``test_knowledge_writer.py``, and the unheld read seeds in ``test_knowledge_index_surfaces.py``.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application import prepared_certification
from agents_remember.application import published_intent as published_intent_module
from agents_remember.application import review_final_output_receipt as receipt_module
from agents_remember.application import review_sync_rebinding as rebinding_module
from agents_remember.application import review_unchanged_knowledge as unchanged_module
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.application.knowledge_write_admission import (
    KnowledgeDatabaseFrozen,
    as_write_admission,
)
from agents_remember.application.memory_quality import controller
from agents_remember.application.published_intent import resolve_published_intent
from agents_remember.application.review_comparison_freeze import (
    EMPTY_FREEZE_OPTIONS,
    tree_comparison_refusal,
)
from agents_remember.errors import CertificationContractError
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory.knowledge.publication import publish_prepared_snapshot
from agents_remember.memory_quality.knowledge_validator.commit_route import GitKnowledgeValidation
from agents_remember.models.knowledge.snapshot import SnapshotDestinationRequest
from agents_remember.models.knowledge_files.shapes import HandoffOrigin
from agents_remember.worktrees import cutover_lock, sync_transaction
from agents_remember.worktrees import direct_landing as route
from agents_remember.worktrees.cutover_lock import CUTOVER_LOCK_CODE, converted_memory_location
from agents_remember.worktrees.integration.closeout.certification import execution
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationSources,
)
from agents_remember.worktrees.knowledge_crossing import unconverted_line_refusal
from agents_remember.worktrees.knowledge_gate import (
    landing_gate_refusal,
    leaf_gate_refusal,
    prepared_closeout_lock,
)
from agents_remember.worktrees.modules import closeout_external, integrate, record_landing
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.services import (
    LandingGateRequest,
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from test_knowledge_closeout_gate import (
    CODE_A,
    LEAF,
    A,
    Gated,
    _edit,
    _series,
    build_gated,
    commit,
    git,
)

LINE = "converted-line"


class _Reached(Exception):
    """The route passed the lock and reached its next step."""


@pytest.fixture
def ports() -> Iterator[None]:
    providers = mock.Mock()
    providers.setup_status.return_value = {}
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=providers,
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


def _unconverted(tmp_path: Path) -> Gated:
    """The gate's world with both memory branches unconverted, and a converted sibling line."""

    world = build_gated(tmp_path)
    git(world.memory, "branch", LINE, world.memory_base)  # another master's converted line
    git(world.memory, "checkout", "-q", "main")
    git(world.memory, "rm", "-q", "knowledge/layout.json")
    world.memory_base = commit(world.memory, {})
    # One unconverted commit under both branches. Two separate commits are the same commit only
    # when they fall in the same second; otherwise their merge base is the converted commit, and
    # the sync is a crossing sync, which is rightly not refused (L37 P1c: this test was flaky).
    git(world.memory, "checkout", "-q", "-B", "leaf", "main")
    return world


@dataclass(frozen=True)
class _Probe:
    """The sides every route is asked about: a leaf edit, its candidate and the commits it made."""

    world: Gated
    candidate: Any
    head: str
    code: str
    main: str

    @property
    def commits(self) -> IntegratedCommits:
        return IntegratedCommits(code=self.code, memory_content=self.head)

    @property
    def sources(self) -> IntegrationSources:
        return IntegrationSources(self.world.code_base, self.main, False, False)


def _closeout(probe: _Probe) -> str | None:
    with mock.patch.object(closeout_external, "_refresh_external_memory", side_effect=_Reached):
        try:
            closeout_external.external_closeout_commits(
                probe.world.contract,
                cast(Any, SimpleNamespace(recovery_commits=None)),
                cast(Any, None),
                cast(Any, SimpleNamespace(commit=probe.code)),
            )
        except RuntimeError as error:
            return str(error)
        except _Reached:
            return None
    raise AssertionError("the closeout neither refused nor reached its refresh")


def _direct(probe: _Probe) -> str | None:
    try:
        route._close_gated_leaf(_series(probe.world), probe.code)
    except route.DirectLandingError as error:
        assert error.status == f"direct-landing-{CUTOVER_LOCK_CODE}"
        return error.detail
    return None


def _blocked(probe: _Probe, target: Any) -> str | None:
    dry = cast(Any, SimpleNamespace(dry_run=True))
    result = integrate._knowledge_gate_block(target, dry, probe.commits, probe.sources)
    return None if result is None else str(result.payload["reason"])


def _sync(probe: _Probe) -> str | None:
    """The ``worktree_sync`` preview of a memory merge (the leaf and its line diverged), from the
    sync transaction's own entry (the fixture has no ``origin`` for the tool's fetch step)."""

    args = WorktreeArgs(
        contract_path=probe.world.contract_path(), dry_run=True, memory_sync_choice="merge-memory"
    )
    result = sync_transaction.sync_contract_under_authority(probe.world.contract, args, fetch={})
    if result.payload.get("state") != f"sync-{CUTOVER_LOCK_CODE}":
        assert result.returncode == 0, result.payload  # the preview, as before this master
        return None
    return str(result.payload["detail"])


def _ingest(world: Gated) -> str | None:
    """What ``knowledge-ingest`` asks on an unconverted leaf (``unconverted_write_refusal``)."""

    contract = world.contract
    assert contract.memory_worktree is not None
    return unconverted_line_refusal(
        memory_worktree=contract.memory_worktree,
        memory_repository=contract.memory_repo_path,
        official_branch=contract.memory_source_branch,
        operation="knowledge-ingest",
    )


def _database_writer(probe: _Probe) -> str | None:
    try:
        as_write_admission(probe.world.contract)
    except KnowledgeDatabaseFrozen as error:
        return str(error)
    return None


def _probe(world: Gated) -> _Probe:
    _edit(world, CODE_A.replace("return value", "return -value"))
    candidate = world.candidate()
    head = commit(world.memory, {})
    code = commit(world.code, {A: CODE_A.replace("return value", "return +value")})
    # The leaf's line moves on past the leaf's recorded base, so a memory sync has work to do.
    moved = git(world.memory, "commit-tree", "main^{tree}", "-p", "main", "-m", "the line moves")
    git(world.memory, "branch", "-f", "main", moved)
    return _Probe(world, candidate, head, code, moved)


_ROUTES: dict[str, Callable[[_Probe], str | None]] = {
    "knowledge-ingest": lambda p: _ingest(p.world),
    "knowledge database writer": _database_writer,
    "memory_quality_check (leaf)": lambda p: controller._unconverted_refusal(
        SimpleNamespace(contract=p.world.contract)
    ),
    "memory_quality_check (repository)": lambda p: controller._unconverted_refusal(
        SimpleNamespace(contract=None, onboarding_root=p.world.memory / "onboarding")
    ),
    "worktree_sync": _sync,
    "closeout validator": lambda p: leaf_gate_refusal(
        p.world.contract, code_tree=p.candidate.code, memory_tree=p.candidate.memory
    ),
    "closeout commit": _closeout,
    "prepared closeout": lambda p: prepared_closeout_lock(p.world.contract),
    "direct landing": _direct,
    "record landing": lambda p: record_landing._knowledge_gate_refusal(
        p.world.contract, p.world.code_base, p.head
    ),
    "record landing (no memory commit named)": lambda p: record_landing._knowledge_gate_refusal(
        p.world.contract, p.world.code_base, ""
    ),
    "leaf integration": lambda p: _blocked(p, p.world.contract),
    "master or checkpoint landing": lambda p: _blocked(p, _series(p.world)),
    "landing gate": lambda p: landing_gate_refusal(
        LandingGateRequest(p.world.memory, p.head, (p.main,), p.world.code, p.world.code_base)
    ),
}


def test_every_route_refuses_unconverted_memory_once_the_repository_holds_converted_memory(
    tmp_path: Path, ports: None
) -> None:
    world = _unconverted(tmp_path)
    probe = _probe(world)
    for name, run in _ROUTES.items():
        refusal = run(probe)
        assert refusal is not None, f"{name} did not refuse"
        assert f"branch {LINE}" in refusal and "crossing sync" in refusal, (name, refusal)

    git(world.memory, "branch", "-D", LINE)  # no converted memory: the lock is inert
    assert converted_memory_location(world.memory) is None
    assert {name: run(probe) for name, run in _ROUTES.items()} == dict.fromkeys(_ROUTES)


def test_the_prepared_closeout_refuses_by_the_lock_at_both_entry_points(tmp_path: Path) -> None:
    world = _unconverted(tmp_path)
    request = cast(Any, SimpleNamespace(handoff=SimpleNamespace(contract=world.contract)))
    for enter in (
        lambda: execution.execute_selected_closeout(world.contract, cast(Any, None), None),  # type: ignore[arg-type]
        lambda: prepared_certification._realize_prepared_memory(request),
    ):
        with pytest.raises(CertificationContractError) as refused:
            enter()
        assert refused.value.findings[0]["code"] == CUTOVER_LOCK_CODE


def test_a_converted_worktree_locks_too_and_an_unanswered_probe_refuses(tmp_path: Path) -> None:
    world = _unconverted(tmp_path)
    git(world.memory, "branch", "-D", LINE)
    elsewhere = tmp_path / "elsewhere"
    git(world.memory, "worktree", "add", "-q", "-b", "elsewhere", str(elsewhere), "main")
    (elsewhere / "knowledge").mkdir(exist_ok=True)
    (elsewhere / "knowledge/layout.json").write_text("{}\n", encoding="utf-8")  # uncommitted
    assert converted_memory_location(world.memory) == f"worktree {elsewhere.as_posix()}"
    assert "worktree" in str(_ingest(world))

    timed_out = subprocess.TimeoutExpired(["git"], 30)
    with mock.patch.object(cutover_lock, "run_git", side_effect=timed_out):
        refusal = _ingest(world)
    assert refusal is not None and "never read as unlocked" in refusal

    real = cutover_lock.run_git

    def failing(repository: Path, args: list[str], options: Any = None) -> Any:
        """The repository answers; the probe's own query exits non-zero (review R1, X01)."""

        if args[0] == "rev-parse":
            return real(repository, args, options)
        return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: bad object HEAD")

    with mock.patch.object(cutover_lock, "run_git", side_effect=failing):
        refusal = _ingest(world)
    assert refusal is not None and "never read as unlocked" in refusal
    assert "fatal: bad object HEAD" in refusal


def test_only_a_definite_non_repository_reads_as_holding_no_converted_memory(
    tmp_path: Path,
) -> None:
    """Review R1, F4: a ``rev-parse`` failure is "no lines" only when Git finds no repository and
    nothing on disk says there is one; any other failure refuses by name (MIK-R09 rule 6)."""

    world = _unconverted(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert converted_memory_location(plain) is None
    assert cutover_lock.cutover_lock_refusal(plain, operation="op", line="l") is None

    pruned = tmp_path / "pruned"
    git(world.memory, "worktree", "add", "-q", "--detach", str(pruned), "main")
    shutil.rmtree(world.memory / ".git" / "worktrees" / "pruned")  # its gitdir is gone
    refusal = cutover_lock.cutover_lock_refusal(pruned, operation="op", line="l")
    assert refusal is not None and "never read as unlocked" in refusal
    assert "git rev-parse failed" in refusal

    denied = subprocess.CompletedProcess(["git"], 128, "", "fatal: detected dubious ownership")
    with mock.patch.object(cutover_lock, "run_git", return_value=denied):
        refusal = cutover_lock.cutover_lock_refusal(world.memory, operation="op", line="l")
    assert refusal is not None and "dubious ownership" in refusal
    with mock.patch.object(cutover_lock, "run_git", side_effect=PermissionError("denied")):
        refusal = cutover_lock.cutover_lock_refusal(world.memory, operation="op", line="l")
    assert refusal is not None and "PermissionError" in refusal


def test_a_bare_repository_git_cannot_open_refuses_by_name(tmp_path: Path) -> None:
    """Review R2, R2-3: a bare repository has no ``.git`` entry, so only its own ``HEAD`` and
    ``objects`` say it is one. When Git cannot open it, the probe refuses by name; it is never
    read as a plain directory with no lines."""

    bare = tmp_path / "memory.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    assert converted_memory_location(bare) is None  # a healthy bare repository with no lines
    git(bare, "config", "core.repositoryformatversion", "99")  # Git now refuses to open it
    refusal = cutover_lock.cutover_lock_refusal(bare, operation="op", line="l")
    assert refusal is not None and "never read as unlocked" in refusal
    assert "git rev-parse failed" in refusal


def test_the_converting_candidate_is_gated_never_locked(tmp_path: Path, ports: None) -> None:
    """MIK-R24 rule 7: the leaf whose candidate holds the marker is judged by the gate."""

    world = _unconverted(tmp_path)
    marker = git(world.memory, "show", f"{LINE}:knowledge/layout.json")
    (world.memory / "knowledge/layout.json").write_text(marker + "\n", encoding="utf-8")
    candidate = world.candidate()
    assert (
        unconverted_line_refusal(
            memory_worktree=world.memory,
            memory_repository=world.memory,
            official_branch="main",
            operation="memory_quality_check",
        )
        is None
    )
    judged = mock.Mock(return_value=None)
    with mock.patch.object(KnowledgeGate, "leaf_refusal", judged):
        assert (
            leaf_gate_refusal(
                world.contract, code_tree=candidate.code, memory_tree=candidate.memory
            )
            is None
        )
    judged.assert_called_once()
    converted = commit(world.memory, {})
    crossing = SimpleNamespace(
        plan="merge",
        repository=str(world.memory),
        worktree=str(tmp_path / "not-yet-created"),  # a series side's sync worktree
        preSyncHead=converted,
        sourceCommit=git(world.memory, "rev-parse", "main"),
        workBranch="leaf",
    )
    assert sync_transaction._cutover_locked(cast(Any, crossing), {}) is None  # a crossing sync
    skipped = SimpleNamespace(plan="skip")
    assert sync_transaction._cutover_locked(cast(Any, skipped), {}) is None  # code only

    # Review R1, F3: the leaf's own converted commit lands on its unconverted line (X04, X05), and a
    # repository-level run on a converted checkout is never locked (X07).
    main = git(world.memory, "rev-parse", "main")
    landed = IntegratedCommits(code=git(world.code, "rev-parse", "HEAD"), memory_content=converted)
    sources = IntegrationSources(world.code_base, main, False, False)
    assert integrate._leaf_landing_lock(world.contract, landed, sources) is None
    recorded = record_landing._knowledge_gate_refusal(world.contract, world.code_base, converted)
    assert "refuses the unconverted memory" not in str(recorded)  # the gate judges it instead
    repository_level = SimpleNamespace(contract=None, onboarding_root=world.memory / "onboarding")
    assert controller._unconverted_refusal(repository_level) is None
    unconverted_landing = IntegratedCommits(code=landed.code, memory_content=main)
    assert integrate._leaf_landing_lock(world.contract, unconverted_landing, sources) is not None


def test_the_database_writer_is_frozen_on_a_converted_tree_and_names_the_file_writer(
    tmp_path: Path,
) -> None:
    world = build_gated(tmp_path / "converted")
    with pytest.raises(KnowledgeDatabaseFrozen, match="knowledge-ingest"):
        as_write_admission(world.contract)
    published = publish_prepared_snapshot(
        cast(Any, None),
        SnapshotDestinationRequest(destination_path=world.memory / "knowledge.sqlite"),
    )
    assert published.state == "refused" and published.refusal is not None
    assert published.refusal.code == "database_frozen"
    assert "knowledge-ingest" in published.refusal.next_action
    assert not (world.memory / "knowledge.sqlite").exists()  # stays where it is; nothing written

    plain = _unconverted(tmp_path / "plain")  # unconverted, in a repository with no converted
    git(plain.memory, "branch", "-D", LINE)  # memory at all: the database writer admits it
    assert as_write_admission(plain.contract).memory_worktree == plain.memory


def test_no_read_selects_the_database_of_a_converted_tree(tmp_path: Path) -> None:
    """L23 F8: the receipt and the rebinding read the tree's state, never its frozen database."""

    world = build_gated(tmp_path)
    (world.memory / "knowledge.sqlite").write_bytes(b"frozen at the cutover")
    location = SimpleNamespace(
        context=SimpleNamespace(memory_root=world.memory, code_repository_name="agents-remember"),
        path=world.memory / "knowledge.sqlite",
    )
    never = mock.Mock(side_effect=AssertionError("the frozen database was read"))
    with (
        mock.patch.object(published_intent_module, "read_dataset_identity", never),
        mock.patch.object(receipt_module, "declared_publication_location", return_value=location),
        mock.patch.object(rebinding_module, "declared_publication_location", return_value=location),
    ):
        resolved = resolve_published_intent(cast(Any, location.context))
        receipt = receipt_module._published_knowledge(world.contract)
        rebinding = rebinding_module._resolved_knowledge(world.contract)
    assert isinstance(resolved, published_intent_module.PublishedIntentUnavailable)
    assert (receipt.state, receipt.identity) == ("not-recorded", None)
    assert (rebinding.state, rebinding.dataset) == ("not-recorded", None)
    assert "converted" in receipt.detail and "converted" in rebinding.detail

    resolution = SimpleNamespace(trees=object(), knowledge_unavailable=(), contract=world.contract)
    with (
        mock.patch.object(unchanged_module, "resolve_review_candidate", return_value=resolution),
        mock.patch.object(unchanged_module, "read_memory_knowledge", never),
    ):
        frozen = unchanged_module.freeze_unchanged_knowledge_review(
            cast(Any, None),
            cast(Any, SimpleNamespace(repository_id="r", master="m", leaf_id=LEAF)),
            EMPTY_FREEZE_OPTIONS,
        )
    assert (frozen.state, frozen.refusal) == ("refused", tree_comparison_refusal())


def test_hand_off_evidence_is_a_list_of_strings() -> None:
    """L24 carry: ``origin.handoff.evidence`` is a list of strings, as L28's list assumes."""

    origin = HandoffOrigin.model_validate({"evidence": ["Evidence: measured."]})
    assert origin.to_document() == {"evidence": ["Evidence: measured."]}
    for wrong in ("Evidence: measured.", [1], []):
        with pytest.raises(ValueError):
            HandoffOrigin.model_validate({"evidence": wrong})
