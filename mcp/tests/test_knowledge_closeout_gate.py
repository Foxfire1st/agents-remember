"""MIK-R09@v2: the mandatory invariant closeout gate, its currentness rules and every route.

The fixture below is a converted leaf on real repositories: two invariants of one family, and the
leaf's own ``leaf`` branches beside the official ``main`` line. ``main`` is the official line of
both repositories; the leaf works on ``leaf`` in both, so the parent line's memory tip and the
leaf's own memory commits are different commits.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application import prepared_certification
from agents_remember.application.knowledge_gate import (
    GATE_PREDICATES,
    GateContext,
    GateResult,
    KnowledgeGate,
    evaluate_leaf_gate,
    item_open_reason,
    memo,
)
from agents_remember.application.knowledge_gate import gate as gate_module
from agents_remember.application.knowledge_worklist import ITEM_KINDS, compute, satisfying_row
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees, leaf_worklist
from agents_remember.errors import CertificationContractError, CuratorCoherenceError
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import GitKnowledgeValidation
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import FamilyRow
from agents_remember.worktrees import direct_landing as route
from agents_remember.worktrees.integration.closeout import curator_coherence
from agents_remember.worktrees.integration.closeout.certification import execution
from agents_remember.worktrees.integration.integration_ref_transaction import (
    IntegratedCommits,
    IntegrationSources,
)
from agents_remember.worktrees.knowledge_gate import (
    GATE_UNBOUND,
    checkout_memory_converted,
    close_owner_history,
    landing_gate_refusal,
    leaf_gate_refusal,
    leaf_memory_converted,
    prepared_closeout_refusal,
)
from agents_remember.worktrees.modules import closeout_external, integrate, record_landing
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.services import (
    LandingGateRequest,
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    default_series_contract,
    load_contract,
)

LEAF = "260928-MIK-L97"
A, B = "pkg/a.py", "pkg/b.py"
CODE_A = "def land(value):\n    return value\n\n\ndef keep():\n    return 1\n"
CODE_B = "def sibling():\n    return 2\n"
ORIGIN = {"task": "260101-OLD", "leaf": "260101-OLD-L1"}
LOCATORS = {
    "RLZ-A00001": (A, "INV-AAAAAA", {"kind": "symbol", "name": "land"}),
    "RLZ-A00002": (A, "INV-AAAAAA", {"kind": "symbol", "name": "keep"}),
    "RLZ-B00001": (B, "INV-BBBBBB", {"kind": "symbol", "name": "sibling"}),
}
ADMITTED = {
    "criteria": ["prevents_costly_mistake"],
    "justification": "A caller that trusts the value would otherwise receive a silently altered one.",
}


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def init(root: Path) -> None:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "gate fixture")


def write(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def commit(root: Path, files: dict[str, str], trailer: str | None = None) -> str:
    write(root, files)
    git(root, "add", "-A")
    message = "change" if trailer is None else f"memory\n\nCode-Commit: {trailer}"
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def anchor_at(code: Path, path: str, locator: dict[str, Any], data: bytes) -> dict[str, Any]:
    """The anchor of ``locator`` in ``data`` (the bytes of ``path``), its blob written to the store."""

    blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=code,
            input=data,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    tree = git(code, "rev-parse", "HEAD^{tree}")
    resolved = CodeTrees.open(code, tree, tree).resolve(path, locator, blob, blob)
    assert resolved is not None, f"{locator} does not resolve in {path}"
    return {"locator": locator, "blob": blob, "content": resolved.content}


def record(kind: str, record_id: str, **fields: Any) -> str:
    return canonical_text(
        {
            "schema": f"ar-{kind}/v1",
            "id": record_id,
            "revision": 1,
            "status": "accepted",
            "admission": "legacy-unassessed",
            "origin": ORIGIN,
            **fields,
        }
    )


def invariant(record_id: str, statement: str, revision: int = 1, **fields: Any) -> str:
    base = {"applicability": "Always.", "conditions": [], "exclusions": [], "supersedes": []}
    return record(
        "invariant", record_id, statement=statement, **{**base, **fields, "revision": revision}
    )


def family(members: list[str], revision: int = 1) -> str:
    return record(
        "family",
        "FAM-F00001",
        title="Landing",
        guarantee="Landing holds.",
        members=members,
        routes=["pkg"],
        revision=revision,
    )


def sidecar(path: str, entries: list[dict[str, Any]]) -> str:
    return canonical_text(
        {"schema": "ar-onboarding-file/v1", "path": path, "references": {}, "realizes": entries}
    )


def entry(entry_id: str, anchor: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry_id,
        "invariant": LOCATORS[entry_id][1],
        "anchor": anchor,
        "role": "primary-authority",
        "rationale": "It is the rule.",
    }


@dataclass
class Gated:
    """The fixture world: both repositories, the task root and the leaf's contract."""

    root: Path
    code: Path
    memory: Path
    task_root: Path
    code_base: str
    memory_base: str

    @property
    def contract(self) -> WorktreeContract:
        return load_contract(self.contract_path())

    def contract_path(self) -> Path:
        enclosure = self.task_root / "enclosures" / LEAF.lower()
        enclosure.mkdir(parents=True, exist_ok=True)
        path = enclosure / "series-contract.md"
        path.write_text(
            "---\nschema: ar-series-contract/v1\nschemaVersion: 1.0\nkind: leaf\n"
            "task_id: 260928_GATE-CASE\ntask_name: gate_case\nrepo_name: agents-remember\n"
            "workflow_kind: light-task\nmemory_mode: external\n\ncoordination:\n"
            f"  root: {self.root}\n  task_root: {self.task_root}\n"
            f"  task_artifact: {self.task_root / 'task.md'}\n  worktree_group: {self.root}\n"
            f"  leaf_id: {LEAF}\n  parent_task_name: gate_case\n\ncode:\n"
            f"  repo_path: {self.code}\n  source_branch: main\n  work_branch: leaf\n"
            f"  base_commit: {self.code_base}\n  worktree: {self.code}\n\nmemory:\n"
            f"  mode: external\n  repo_path: {self.memory}\n  source_branch: main\n"
            f"  work_branch: leaf\n  base_commit: {self.memory_base}\n"
            f"  worktree: {self.memory}\n  ledger: {self.memory / 'memory.md'}\n---\n",
            encoding="utf-8",
        )
        return path

    def task_document(self, **fields: Any) -> None:
        document = {
            "schema": "ar-task-document/v1",
            "id": LEAF,
            "slug": "leaf",
            "title": "Leaf",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-09-30T00:00+02:00",
            **fields,
        }
        (self.task_root / "leaf.json").write_text(json.dumps(document), encoding="utf-8")

    # -- the curator's edits ------------------------------------------------------------------

    def sidecar_entries(self, path: str) -> list[dict[str, Any]]:
        return json.loads((self.memory / f"onboarding/{path}.json").read_text())["realizes"]

    def anchor(self, entry_id: str) -> dict[str, Any]:
        """The entry's anchor in K_C, its path filled in (a row's ``after``)."""

        path = LOCATORS[entry_id][0]
        found = next(one for one in self.sidecar_entries(path) if one["id"] == entry_id)
        return {**found["anchor"], "path": path}

    def reanchor(self, *entry_ids: str) -> dict[str, dict[str, Any]]:
        """Re-anchor entries at the code worktree's current bytes; return each ``before``."""

        before: dict[str, dict[str, Any]] = {}
        for entry_id in entry_ids:
            path, _invariant, locator = LOCATORS[entry_id]
            before[entry_id] = self.anchor(entry_id)
            data = (self.code / path).read_bytes()
            entries = [
                {**one, "anchor": anchor_at(self.code, path, locator, data)}
                if one["id"] == entry_id
                else one
                for one in self.sidecar_entries(path)
            ]
            write(self.memory, {f"onboarding/{path}.json": sidecar(path, entries)})
        return before

    def rows(self, *rows: dict[str, Any], closed: bool = False) -> None:
        numbered = [
            {"id": f"ROW-{index:06d}", "items": [], **row} for index, row in enumerate(rows)
        ]
        document = {"schema": "ar-history/v1", "leaf": LEAF, "closed": closed, "rows": numbered}
        write(self.memory, {f"knowledge/history/{LEAF}.json": canonical_text(document)})

    def invariant_row(
        self,
        subject: str,
        before: dict[str, dict[str, Any]],
        *,
        revision: int = 1,
        **fields: Any,
    ) -> dict[str, Any]:
        covers = [
            {"id": one, "before": anchor, "after": self.anchor(one)}
            for one, anchor in before.items()
        ]
        return {
            "subject": subject,
            "disposition": "no_impact",
            "reason": "The body changed; the rule holds.",
            "covers": covers,
            "revision": revision,
            **fields,
        }

    # -- the gate -------------------------------------------------------------------------------

    def candidate(self) -> CandidateTrees:
        with TemporaryDirectory() as scratch:
            code = worktree_candidate_tree(self.code, Path(scratch) / "code" / "index")
            memory = worktree_candidate_tree(self.memory, Path(scratch) / "memory" / "index")
        return CandidateTrees(code=code, memory=memory)

    def gate(self) -> GateResult:
        result = evaluate_leaf_gate(
            self.contract, self.candidate(), parent_memory_tip=git(self.memory, "rev-parse", "main")
        )
        assert result is not None
        return result


def trace_rows(*subjects: str) -> list[dict[str, Any]]:
    return [
        {"subject": subject, "disposition": "no_impact", "reason": "Only the body changed."}
        for subject in subjects
    ]


def family_row(examined: dict[str, int]) -> dict[str, Any]:
    return {
        "subject": "FAM-F00001",
        "disposition": "no_impact",
        "reason": "Every member still holds.",
        "examined": [{"id": one, "revision": revision} for one, revision in examined.items()],
    }


def build_gated(tmp_path: Path) -> Gated:
    code, memory, task_root = tmp_path / "code", tmp_path / "memory", tmp_path / "task"
    init(code)
    code_base = commit(code, {A: CODE_A, B: CODE_B})
    init(memory)
    anchors = {
        entry_id: anchor_at(code, path, locator, (code / path).read_bytes())
        for entry_id, (path, _invariant, locator) in LOCATORS.items()
    }
    memory_base = commit(
        memory,
        {
            "knowledge/layout.json": canonical_text(
                {"schema": "ar-memory-layout/v2", "conversion": "1"}
            ),
            "knowledge/invariants/INV-AAAAAA-land.json": invariant(
                "INV-AAAAAA", "Values land unchanged."
            ),
            "knowledge/invariants/INV-BBBBBB-sibling.json": invariant(
                "INV-BBBBBB", "The sibling answers two."
            ),
            "knowledge/families/FAM-F00001-landing.json": family(["INV-AAAAAA", "INV-BBBBBB"]),
            f"onboarding/{A}.md": "# a\n",
            f"onboarding/{A}.json": sidecar(
                A,
                [
                    entry("RLZ-A00001", anchors["RLZ-A00001"]),
                    entry("RLZ-A00002", anchors["RLZ-A00002"]),
                ],
            ),
            f"onboarding/{B}.md": "# b\n",
            f"onboarding/{B}.json": sidecar(B, [entry("RLZ-B00001", anchors["RLZ-B00001"])]),
            "onboarding/pkg/overview.md": "# pkg\n",
            "onboarding/overview.md": "# root\n",
        },
        trailer=code_base,
    )
    for repository in (code, memory):
        git(repository, "checkout", "-q", "-b", "leaf")
    task_root.mkdir()
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    world = Gated(tmp_path, code, memory, task_root, code_base, memory_base)
    world.task_document()
    return world


TRACES = ("onboarding:pkg/a.py", "onboarding:pkg/overview")


@pytest.fixture
def world(tmp_path: Path) -> Gated:
    return build_gated(tmp_path)


def _open(result: Any) -> dict[str, str]:
    """``{subject-ish head of the message: message}`` of every open-item finding."""

    return {
        finding.message.split(" (item ")[0]: finding.message
        for finding in result.findings
        if finding.code == "knowledge-item-open"
    }


def _edit(world: Gated, text: str) -> None:
    (world.code / A).write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------------------------------
# Rule 2: currentness of invariant and family rows; the packet's examples
# --------------------------------------------------------------------------------------------------


def test_a_leaf_is_ready_only_once_current_rows_answer_every_item_and_a_new_edit_reopens_two(
    world: Gated,
) -> None:
    """Conforming, non-conforming and the first boundary example of the packet."""

    _edit(world, CODE_A.replace("return value", "return value + 0").replace("return 1", "return 3"))
    first = world.gate()
    assert not first.ok
    assert set(_open(first)) == {
        "touched_invariant INV-AAAAAA",
        "reached_family FAM-F00001",
        "onboarding_trace onboarding:pkg/a.py",
        "onboarding_trace onboarding:pkg/overview",
    }
    assert "Required:" in _open(first)["touched_invariant INV-AAAAAA"]
    assert "RLZ-A00001 touched" in _open(first)["touched_invariant INV-AAAAAA"]

    # Non-conforming: a row that covers only one of the two touched entries is not current.
    before = world.reanchor("RLZ-A00001", "RLZ-A00002")
    one = {"RLZ-A00001": before["RLZ-A00001"]}
    examined = family_row({"INV-AAAAAA": 1, "INV-BBBBBB": 1})
    world.rows(world.invariant_row("INV-AAAAAA", one), examined, *trace_rows(*TRACES))
    partial = world.gate()
    assert set(_open(partial)) == {"touched_invariant INV-AAAAAA", "reached_family FAM-F00001"}
    assert "does not cover RLZ-A00002" in _open(partial)["touched_invariant INV-AAAAAA"]
    assert "INV-AAAAAA" in _open(partial)["reached_family FAM-F00001"]

    # Conforming: a current invariant row and a family row naming every member.
    world.rows(world.invariant_row("INV-AAAAAA", before), examined, *trace_rows(*TRACES))
    ready = world.gate()
    assert ready.ok and ready.refusal() is None, ready.refusal()
    assert ready.brief()["state"] == "pass"

    # Boundary: the worker edits `land` again. The entry is stale at C, the invariant row stops
    # being current, and so does the family row: the finding count returns to 2.
    _edit(world, CODE_A.replace("return value", "return value - 0").replace("return 1", "return 3"))
    again = world.gate()
    assert set(_open(again)) == {"touched_invariant INV-AAAAAA", "reached_family FAM-F00001"}
    assert len(again.findings) == 2
    assert "RLZ-A00001 is stale" in _open(again)["touched_invariant INV-AAAAAA"]


def _answered(world: Gated, before: dict[str, dict[str, Any]], *extra: dict[str, Any]) -> None:
    examined = {"INV-AAAAAA": 1, "INV-BBBBBB": 1}
    world.rows(world.invariant_row("INV-AAAAAA", before), family_row(examined), *extra)


def test_a_sibling_whose_meaning_changed_after_the_family_row_reopens_the_family(
    world: Gated,
) -> None:
    """Second boundary example: a sibling's revision rises from 1 to 2 after the family row."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    before = world.reanchor("RLZ-A00001")
    _answered(world, before, *trace_rows(*TRACES))
    assert world.gate().ok

    write(
        world.memory,
        {
            "knowledge/invariants/INV-BBBBBB-sibling.json": invariant(
                "INV-BBBBBB", "The sibling answers two, always.", revision=2
            )
        },
    )
    reopened = _open(world.gate())
    assert "INV-BBBBBB (examined at 1, now 2)" in reopened["reached_family FAM-F00001"]
    assert "touched_invariant INV-BBBBBB" in reopened  # its own record changed: its own row

    sibling = {
        "subject": "INV-BBBBBB",
        "disposition": "changed",
        "effect": "clarify",
        "reason": "The statement now says always.",
        "covers": [],
        "revision": 2,
    }
    world.rows(
        world.invariant_row("INV-AAAAAA", before),
        family_row({"INV-AAAAAA": 1, "INV-BBBBBB": 2}),
        sibling,
        *trace_rows(*TRACES),
    )
    assert world.gate().ok


def test_a_new_invariant_needs_no_row_and_admission_judges_it_new_against_the_parent_line(
    world: Gated,
) -> None:
    """Rule 2: new records raise no item; L27 carried: the base is the parent line's memory tip."""

    bare = {"criteria": ["prevents_costly_mistake"], "justification": f"Added in {LEAF}."}
    origin = {"task": "260928-MIK", "leaf": LEAF}
    members = ["INV-AAAAAA", "INV-BBBBBB", "INV-CCCCCC"]
    # Committed on the leaf's own memory branch: inside the writer it would already "exist".
    commit(
        world.memory,
        {
            "knowledge/invariants/INV-CCCCCC-new.json": invariant(
                "INV-CCCCCC", "Keep answers one.", admission=bare, origin=origin
            ),
            "knowledge/families/FAM-F00001-landing.json": family(members, revision=2),
        },
    )
    refused = world.gate()
    violations = [one.message for one in refused.findings if one.code == "knowledge-validator"]
    assert any("R27.2-new-record" in one and "INV-CCCCCC" in one for one in violations)
    assert set(_open(refused)) == {"reached_family FAM-F00001"}  # no item for the new invariant
    assert "3 member(s) to examine" in _open(refused)["reached_family FAM-F00001"]

    write(
        world.memory,
        {
            "knowledge/invariants/INV-CCCCCC-new.json": invariant(
                "INV-CCCCCC", "Keep answers one.", admission=ADMITTED, origin=origin
            )
        },
    )
    world.rows(family_row({"INV-AAAAAA": 1, "INV-BBBBBB": 1, "INV-CCCCCC": 1}))
    assert world.gate().ok


def test_items_are_recomputed_from_the_new_base_after_a_sync(world: Gated) -> None:
    """Failure and recovery: rows that stay current stay satisfied; stale ones reopen."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    before = world.reanchor("RLZ-A00001")
    _answered(world, before, *trace_rows(*TRACES))
    assert world.gate().ok
    for repository in (world.code, world.memory):
        commit(repository, {})  # the leaf's work so far, committed on its branch
    # The official line moves on: another leaf changed INV-AAAAAA's meaning (revision 2).
    git(world.code, "checkout", "-q", "main")
    later = commit(world.code, {"pkg/c.py": "C = 3\n"})
    git(world.memory, "checkout", "-q", "main")
    memory_later = commit(
        world.memory,
        {
            "knowledge/invariants/INV-AAAAAA-land.json": invariant(
                "INV-AAAAAA", "Values land unchanged and whole.", revision=2
            )
        },
        trailer=later,
    )
    for repository in (world.code, world.memory):
        git(repository, "checkout", "-q", "leaf")
        git(repository, "merge", "-q", "--no-edit", "main")
    world.code_base, world.memory_base = later, memory_later

    synced = world.gate()
    assert synced.worklist["pairing"]["memoryBase"]["commit"] == memory_later
    reopened = _open(synced)
    assert "revision 1, but INV-AAAAAA is at revision 2" in reopened["touched_invariant INV-AAAAAA"]
    assert "INV-AAAAAA (examined at 1, now 2)" in reopened["reached_family FAM-F00001"]
    assert not any(key.startswith("onboarding_trace") for key in reopened)  # still current
    world.rows(
        world.invariant_row("INV-AAAAAA", before, revision=2),
        family_row({"INV-AAAAAA": 2, "INV-BBBBBB": 1}),
        *trace_rows(*TRACES),
    )
    assert world.gate().ok


def test_an_incomplete_run_is_one_finding_naming_its_input_never_an_unhandled_error(
    world: Gated,
) -> None:
    """Carried from L11 (fail-closed task document) and L03 (Git failures)."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    (world.task_root / "leaf.json").write_text(
        json.dumps({"schema": "ar-task-document/v1", "id": LEAF, "kind": "subTask", "title": 5}),
        encoding="utf-8",
    )
    unreadable = world.gate()
    assert [(one.code, one.path) for one in unreadable.findings] == [
        ("knowledge-worklist-incomplete", "leaf task document")
    ]
    world.task_document()

    timeout = subprocess.TimeoutExpired(["git", "diff"], 30)
    with mock.patch.object(gate_module, "leaf_worklist", side_effect=timeout):
        outer = world.gate()

    with mock.patch.object(compute, "tree_difference_observation", side_effect=timeout):
        inner = world.gate()
    for result in (outer, inner):
        incomplete = [one for one in result.findings if one.code == "knowledge-worklist-incomplete"]
        assert [one.path for one in incomplete] == ["git"]
        assert "TimeoutExpired" in incomplete[0].message
        assert not result.ok


def test_every_registered_kind_is_decided_by_its_own_predicate_never_a_generic_lookup() -> None:
    """Carried from L30, L06, L10 and L14; D29: an unanswered reconsideration blocks."""

    assert set(GATE_PREDICATES) == set(ITEM_KINDS)

    def context(*rows: Any) -> GateContext:
        history = cast(Any, SimpleNamespace(rows=list(rows)))
        return GateContext(LEAF, history, candidate=cast(Any, None), code=cast(Any, None))

    def row(subject: str) -> Any:
        return SimpleNamespace(id="ROW-000009", subject=subject)

    # onboarding_trace: a counted change satisfies it with no row; the generic lookup finds none.
    counted = {"id": "sha256:1", "kind": "onboarding_trace", "subject": TRACES[0]}
    assert item_open_reason({**counted, "facts": {"countedChange": True}}, context()) is None
    assert satisfying_row("onboarding_trace", TRACES[0], None) is None
    uncounted = {**counted, "facts": {"countedChange": False}}
    assert item_open_reason(uncounted, context()) is not None
    assert item_open_reason(uncounted, context(row(TRACES[0]))) is None
    unreadable = {**counted, "facts": {"countedChange": False, "sidecarUnreadable": True}}
    assert item_open_reason(unreadable, context(row(TRACES[0]))) is not None

    # unexplained: a covered change needs its no_invariant row; an uncovered one its trace.
    hunk = {"id": "sha256:2", "kind": "unexplained_hunk", "subject": "hunk:pkg/a.py@absent..absent"}
    covered = {**hunk, "facts": {"coverage": {"state": "covered"}}}
    assert item_open_reason(covered, context()) is not None
    assert item_open_reason(covered, context(row("hunk:sha256:2"))) is None
    trace = {"countedChange": False, "subject": TRACES[0]}
    uncovered = {**hunk, "facts": {"coverage": {"state": "uncovered"}, "onboardingTrace": trace}}
    assert item_open_reason(uncovered, context(row("hunk:sha256:2"))) is not None
    assert item_open_reason(uncovered, context(row(TRACES[0]))) is None

    # planned_untouched and reconsideration_candidate: their own row by subject (D29).
    for kind, subject in (
        ("planned_untouched", "planned:invariant:INV-AAAAAA#clarify"),
        ("reconsideration_candidate", "reconsider:DEC-AAAAAA#1"),
    ):
        item = {"id": "sha256:3", "kind": kind, "subject": subject, "facts": {}}
        assert item_open_reason(item, context()) is not None
        assert item_open_reason(item, context(row(subject))) is None

    # family_route_condition: decided by subject, so an item whose ID changed between recomputes
    # is answered by the same family row (L06 N2); never by no_impact.
    def family(disposition: str) -> FamilyRow:
        return FamilyRow.model_validate(
            {
                "id": "ROW-000001",
                "subject": "FAM-F00001",
                "disposition": disposition,
                "reason": "Routes follow the move.",
                "items": [],
                "examined": [{"id": "INV-AAAAAA", "revision": 1}],
            }
        )

    for item_id in ("sha256:4", "sha256:5"):
        condition = {
            "id": item_id,
            "kind": "family_route_condition",
            "subject": "FAM-F00001#route_emptied",
            "facts": {"recordSatisfiesRoutes": True},
        }
        assert item_open_reason(condition, context(family("rerouted"))) is None
        assert item_open_reason(condition, context(family("no_impact"))) is not None
        unsatisfied = {**condition, "facts": {"recordSatisfiesRoutes": False}}
        assert item_open_reason(unsatisfied, context(family("rerouted"))) is not None

    made_up = {"id": "sha256:6", "kind": "made_up", "subject": "x", "facts": {}}
    assert "no gate predicate" in str(item_open_reason(made_up, context()))
    invariant_item = {"id": "sha256:7", "kind": "touched_invariant", "subject": "INV-AAAAAA"}
    no_history = GateContext(LEAF, None, candidate=cast(Any, None), code=cast(Any, None))
    assert "no invariant row" in str(item_open_reason({**invariant_item, "facts": {}}, no_history))


# --------------------------------------------------------------------------------------------------
# The validator at the gate, with the open-history-row rule (carried from L12)
# --------------------------------------------------------------------------------------------------


def test_the_gate_runs_the_validator_and_its_history_row_rule(world: Gated) -> None:
    _edit(world, CODE_A.replace("return value", "return -value"))
    before = world.reanchor("RLZ-A00001")
    # A hand-edited row: its `after` is the old anchor, so it contradicts K_C. Rule 2 alone would
    # accept it (the entry is current at C); only the validator rule sees the row.
    stale_after = world.invariant_row("INV-AAAAAA", before)
    stale_after["covers"][0]["after"] = before["RLZ-A00001"]
    ghost = {"subject": "INV-ZZZZZZ", "disposition": "no_impact", "reason": "x", "covers": []}
    world.rows(
        stale_after,
        {**ghost, "revision": 1},
        family_row({"INV-AAAAAA": 1, "INV-BBBBBB": 1}),
        *trace_rows(*TRACES),
    )
    write(world.memory, {"onboarding/pkg/a.py.md": "# a [4]\n"})  # a marker with no reference
    result = world.gate()
    violations = [one.message for one in result.findings if one.code == "knowledge-validator"]
    assert any("R09-history-rows" in one and "RLZ-A00001" in one for one in violations)
    assert any("R09-history-rows" in one and "INV-ZZZZZZ" in one for one in violations)
    assert any("R22.3-markers" in one for one in violations)
    assert not _open(result)  # every item has a current row: only the validator refuses

    # The leaf's own file closed by hand is no waiver: it is not closed in the base (review R1, F1).
    world.rows({**ghost, "revision": 1}, closed=True)
    write(world.memory, {"onboarding/pkg/a.py.md": "# a\n"})
    closed = [one.message for one in world.gate().findings if one.code == "knowledge-validator"]
    assert any("R09-history-rows" in one and "INV-ZZZZZZ" in one for one in closed)


# --------------------------------------------------------------------------------------------------
# Every route: a refusal test at each (R22 rule 8, no bypass)
# --------------------------------------------------------------------------------------------------


@pytest.fixture
def ports() -> Any:

    providers = mock.Mock()
    providers.setup_status.return_value = {}

    def bind(*, gate: bool = True) -> None:
        bind_worktree_services(
            WorktreeServices(
                provider_lifecycle=providers,
                memory_quality=cast(Any, None),
                citation_guard=cast(Any, None),
                knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
                knowledge_gate=KnowledgeGate() if gate else None,
            )
        )

    bind()
    yield bind
    reset_worktree_services()


def test_the_closeout_validator_refuses_until_the_gate_passes_and_never_runs_ungated(
    world: Gated, ports: Any
) -> None:

    _edit(world, CODE_A.replace("return value", "return -value"))
    candidate = world.candidate()
    dumped = mock.Mock()
    dumped.model_dump.return_value = {"same": True}
    observation = SimpleNamespace(
        pair_identity=dumped,
        code_candidate_tree=candidate.code,
        memory_candidate_tree=candidate.memory,
        task_topology_fingerprint="t",
        task_intent=dumped,
        attestation_sha256="s",
        source_candidates=[],
        candidate=SimpleNamespace(ref="leaf.json"),
    )
    record = SimpleNamespace(
        taskIntent=dumped,
        pairIdentity=dumped,
        codeCandidateTree=candidate.code,
        memoryCandidateTree=candidate.memory,
        taskTopologyFingerprint="t",
        attestationSha256="s",
        sourceCandidates=[],
        taskDocumentRef="leaf.json",
        judgments=[],
    )
    with (
        mock.patch.object(
            curator_coherence, "observe_curator_coherence_source", return_value=observation
        ),
        mock.patch.object(
            curator_coherence,
            "load_curator_coherence_authority",
            return_value=SimpleNamespace(record=record),
        ),
        mock.patch.object(curator_coherence, "require_current_task_intent"),
        mock.patch.object(curator_coherence, "_require_current_dependencies"),
        mock.patch.object(curator_coherence, "require_recorded_judgments_current"),
        pytest.raises(CuratorCoherenceError) as refused,
    ):
        curator_coherence.require_current_curator_coherence(world.contract)
    assert refused.value.status == "curator-coherence-knowledge-gate-refused"
    assert refused.value.next_action == "memory_quality_check"
    assert "touched_invariant INV-AAAAAA" in refused.value.detail

    ports(gate=False)  # a converted leaf with no bound gate is refused, never passed
    assert leaf_gate_refusal(
        world.contract, code_tree=candidate.code, memory_tree=candidate.memory
    ) == (GATE_UNBOUND)
    ports()
    before = world.reanchor("RLZ-A00001")
    _answered(world, before, *trace_rows(*TRACES))
    answered = world.candidate()
    assert (
        leaf_gate_refusal(world.contract, code_tree=answered.code, memory_tree=answered.memory)
        is None
    )


def test_the_closeout_memory_commit_closes_the_history_file_and_validates_its_exact_tree(
    world: Gated, ports: Any
) -> None:

    history = world.memory / f"knowledge/history/{LEAF}.json"
    closing = close_owner_history(world.memory, LEAF)  # a leaf with no rows gets an empty file
    assert json.loads(history.read_text()) == {
        "closed": True,
        "leaf": LEAF,
        "rows": [],
        "schema": "ar-history/v1",
    }
    closing.restore()
    assert not history.exists()
    world.rows(*trace_rows(*TRACES))
    open_bytes = history.read_bytes()
    closing = close_owner_history(world.memory, LEAF)
    closed = json.loads(history.read_text())
    assert closed["closed"] is True and closed["rows"] == json.loads(open_bytes)["rows"]
    assert history.read_text() == canonical_text(closed)
    assert leaf_memory_converted(world.contract)

    code_commit = commit(world.code, {"pkg/a.py": CODE_A.replace("return 1", "return 3")})
    write(world.memory, {"onboarding/pkg/a.py.md": "# a [4]\n"})
    never = mock.Mock(side_effect=AssertionError("committed an invalid tree"))
    with (
        mock.patch.object(closeout_external, "begin_git_mutation", never),
        pytest.raises(RuntimeError, match=r"R22\.3-markers"),
    ):
        closeout_external._commit_memory_content(
            world.contract,
            cast(Any, None),
            cast(Any, None),
            code_commit=code_commit,
            closing=closing,
        )
    never.assert_not_called()
    assert history.read_bytes() == open_bytes  # the refusal restored the file (review R1, F2)
    write(world.memory, {"onboarding/pkg/a.py.md": "# a\n"})
    closing = close_owner_history(world.memory, LEAF)
    reached = mock.Mock(side_effect=RuntimeError("reached the commit"))
    with (
        mock.patch.object(closeout_external, "begin_git_mutation", reached),
        mock.patch.object(closeout_external, "report_operation_progress"),
        pytest.raises(RuntimeError, match="reached the commit"),
    ):
        closeout_external._commit_memory_content(
            world.contract,
            cast(Any, None),
            cast(Any, None),
            code_commit=code_commit,
            closing=closing,
        )


def _series(world: Gated) -> Any:

    task = ContractTask(
        name="gate_case",
        repo_name="agents-remember",
        coordination_root=world.root,
        workflow_kind="light-task",
        memory_mode="external",
    )
    return default_series_contract(
        task,
        code=RepoBranchPlan(world.code, "main", "leaf", world.code_base),
        memory=RepoBranchPlan(world.memory, "main", "leaf", world.memory_base),
        task_root=world.task_root,
    )


def test_direct_landing_gates_names_its_leaf_closes_its_history_and_restores_on_refusal(
    world: Gated, ports: Any
) -> None:

    series = _series(world)
    code_commit = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    with pytest.raises(route.DirectLandingError) as unnamed:
        route._close_gated_leaf(series, code_commit)
    assert unnamed.value.status == "direct-landing-knowledge-gate-refused"
    assert "0 open leaf history file(s)" in unnamed.value.detail

    world.rows()  # the direct-mode leaf's own open history file names it
    with pytest.raises(route.DirectLandingError) as unanswered:
        route._close_gated_leaf(series, code_commit)
    assert "touched_invariant INV-AAAAAA" in unanswered.value.detail

    before = world.reanchor("RLZ-A00001")
    _answered(world, before, *trace_rows(*TRACES))
    history = world.memory / f"knowledge/history/{LEAF}.json"
    open_bytes = history.read_bytes()
    with (
        mock.patch.object(route, "memory_commit_refusal", return_value="refused: exact tree"),
        pytest.raises(route.DirectLandingError) as exact,
    ):
        route._close_gated_leaf(series, code_commit)
    assert exact.value.status == "direct-landing-knowledge-validation-refused"
    assert history.read_bytes() == open_bytes  # a refused landing leaves the checkout as it was

    closing = route._close_gated_leaf(series, code_commit)
    assert closing is not None and json.loads(history.read_text())["closed"] is True
    commit(world.memory, {}, trailer=code_commit)
    assert route._close_gated_leaf(series, code_commit) is None  # a replay commits nothing


def test_record_landing_checks_the_landed_memory_commit_is_closed_and_valid(
    world: Gated, ports: Any
) -> None:

    contract = world.contract
    code_commit = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    before = world.reanchor("RLZ-A00001")
    _answered(world, before, *trace_rows(*TRACES))
    landed_open = commit(world.memory, {}, trailer=code_commit)
    refusal = record_landing._knowledge_gate_refusal(contract, code_commit, landed_open)
    assert refusal is not None and "is not closed" in refusal

    history = world.memory / f"knowledge/history/{LEAF}.json"
    closed = {**json.loads(history.read_text()), "closed": True}
    write(world.memory, {f"knowledge/history/{LEAF}.json": canonical_text(closed)})
    landed = commit(world.memory, {}, trailer=code_commit)
    assert record_landing._knowledge_gate_refusal(contract, code_commit, landed) is None

    unnamed = record_landing._knowledge_gate_refusal(contract, code_commit, "")
    assert unnamed is not None and "landed_memory_content_commit" in unnamed
    # A record the leaf made in an earlier commit of its own is still new against the parent line
    # the leaf synced from (carried from L27): its admission is judged, not merely reported.
    bare = {"criteria": ["prevents_costly_mistake"], "justification": f"Added in {LEAF}."}
    origin = {"task": "260928-MIK", "leaf": LEAF}
    path = "knowledge/invariants/INV-CCCCCC-new.json"
    commit(world.memory, {path: invariant("INV-CCCCCC", "New.", admission=bare, origin=origin)})
    later = commit(world.memory, {}, trailer=code_commit)
    admitted = record_landing._knowledge_gate_refusal(contract, code_commit, later)
    assert admitted is not None and "R27.2-new-record" in admitted
    broken = commit(world.memory, {"onboarding/pkg/a.py.md": "# a [4]\n"}, trailer=code_commit)
    invalid = record_landing._knowledge_gate_refusal(contract, code_commit, broken)
    assert invalid is not None and "R22.3-markers" in invalid


def test_a_master_or_checkpoint_landing_waits_until_no_entry_at_a_changed_path_is_stale(
    world: Gated, ports: Any
) -> None:
    """Rule 4: the validator on the master's memory, and its net staleness at its code commit."""

    series = _series(world)
    code_commit = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    memory_commit = commit(world.memory, {}, trailer=code_commit)  # the master re-anchored nothing
    sources = IntegrationSources(world.code_base, world.memory_base, False, False)
    blocked = integrate._knowledge_gate_block(
        series,
        cast(Any, SimpleNamespace(dry_run=True)),
        IntegratedCommits(code=code_commit, memory_content=memory_commit),
        sources,
    )
    assert blocked is not None and blocked.returncode == 2
    assert blocked.payload["state"] == "knowledge-gate-refused"
    reason = str(blocked.payload["reason"])
    assert "knowledge-stale-at-landing" in reason and "RLZ-A00001" in reason
    assert "knowledge-maintenance leaf" in reason
    assert "RLZ-A00002" not in reason  # `keep`'s bytes are unchanged: current, not stale

    world.reanchor("RLZ-A00001")  # the maintenance leaf's re-anchor makes the entry current
    maintained = commit(world.memory, {}, trailer=code_commit)
    request = LandingGateRequest(
        memory_repository=world.memory,
        memory_commit=maintained,
        memory_bases=(world.memory_base,),
        code_repository=world.code,
        code_commit=code_commit,
        code_base=world.code_base,
    )
    assert landing_gate_refusal(request) is None
    broken = commit(world.memory, {"onboarding/pkg/a.py.md": "# a [4]\n"}, trailer=code_commit)
    invalid = landing_gate_refusal(
        LandingGateRequest(**{**request.__dict__, "memory_commit": broken})
    )
    assert invalid is not None and "R22.3-markers" in invalid


# --------------------------------------------------------------------------------------------------
# Definition 8 at the gate (carried from L10 N3), and the transition
# --------------------------------------------------------------------------------------------------


def test_an_insertion_only_hunk_is_linked_only_by_a_candidate_range(world: Gated) -> None:
    """The symmetric correction: a line inserted inside `land`, whose entry K_C no longer holds."""

    entries = [one for one in world.sidecar_entries(A) if one["id"] != "RLZ-A00001"]
    write(world.memory, {f"onboarding/{A}.json": sidecar(A, entries)})
    _edit(world, CODE_A.replace("    return value\n", "    value = value\n    return value\n"))
    document = leaf_worklist(world.contract, persist=False)
    assert document is not None and document["state"] == "complete"
    change = next(one for one in document["changes"] if one["path"] == A)
    assert [(hunk["base"][1], hunk["linked"]) for hunk in change["hunks"]] == [(0, False)]
    subjects = {(item["kind"], item["subject"]) for item in document["items"]}
    assert ("touched_invariant", "INV-AAAAAA") in subjects  # the retired entry still raises
    assert any(kind == "unexplained_hunk" for kind, _subject in subjects)


def test_an_unconverted_leaf_is_not_gated_at_any_route(world: Gated) -> None:
    """Rule 6 / transition: no layout marker on either side, so every route behaves as before."""

    for branch in ("main", "leaf"):
        git(world.memory, "checkout", "-q", branch)
        git(world.memory, "rm", "-q", "knowledge/layout.json")
        commit(world.memory, {})
    world.memory_base = git(world.memory, "rev-parse", "main")  # the leaf's base: unconverted
    reset_worktree_services()  # nothing is bound: an unconverted route never asks the gate
    _edit(world, CODE_A.replace("return value", "return -value"))
    candidate = world.candidate()
    head = commit(world.memory, {})  # its parent holds no marker either (a deletion is gated)
    assert (
        leaf_gate_refusal(world.contract, code_tree=candidate.code, memory_tree=candidate.memory)
        is None
    )
    assert not leaf_memory_converted(world.contract)
    assert not checkout_memory_converted(world.memory)
    main = git(world.memory, "rev-parse", "main")
    request = LandingGateRequest(world.memory, head, (main,), world.code, world.code_base)
    assert prepared_closeout_refusal(world.contract) is None  # gap 3 touches converted memory only
    # Gap 2: an unconverted direct landing is unchanged: no gate, no capture, nothing written.

    series = _series(world)
    code_commit = commit(world.code, {A: CODE_A.replace("return value", "return +value")})
    objects = git(world.memory, "count-objects", "-v")
    status = git(world.memory, "status", "--porcelain", "--untracked-files=all")
    assert route._close_gated_leaf(series, code_commit) is None
    assert git(world.memory, "count-objects", "-v") == objects
    assert git(world.memory, "status", "--porcelain", "--untracked-files=all") == status
    assert landing_gate_refusal(request) is None
    assert record_landing._knowledge_gate_refusal(world.contract, world.code_base, head) is None
    assert record_landing._knowledge_gate_refusal(world.contract, world.code_base, "") is None
    assert leaf_worklist(world.contract, persist=False) is None


# --------------------------------------------------------------------------------------------------
# Rulings round (2026-09-30T14:38:47+02:00): gaps 2, 3 and 4
# --------------------------------------------------------------------------------------------------


def test_the_prepared_closeout_path_fails_closed_on_converted_memory(world: Gated) -> None:
    """Gap 3: it cannot close the history file, so it refuses by name until it can."""

    codes = []
    with pytest.raises(CertificationContractError) as entry:
        execution.execute_selected_closeout(world.contract, cast(Any, None), cast(Any, None))
    codes.append(entry.value.findings[0]["code"])
    request = cast(Any, SimpleNamespace(handoff=SimpleNamespace(contract=world.contract)))
    with pytest.raises(CertificationContractError) as realize:
        prepared_certification._realize_prepared_memory(request)
    codes.append(realize.value.findings[0]["code"])
    assert codes == ["prepared-closeout-knowledge-history-unclosable"] * 2
    assert "cannot set closed: true" in str(realize.value.findings[0]["observed"])


def test_the_gate_memo_reuses_a_verdict_only_for_the_identical_inputs(
    world: Gated, ports: Any
) -> None:
    """Gap 4: keyed by the exact trees, the contract, the parent tip, the task document, the build."""

    memo.GATE_MEMO.clear()
    _edit(world, CODE_A.replace("return value", "return -value"))
    candidate = world.candidate()
    evaluated = mock.Mock(wraps=gate_module._evaluate)
    with mock.patch.object(gate_module, "_evaluate", evaluated):
        # One memory-quality run: the count (no tip given), then the closeout validator's two reads.
        first = evaluate_leaf_gate(world.contract, candidate)
        for _ in range(2):
            refusal = leaf_gate_refusal(
                world.contract, code_tree=candidate.code, memory_tree=candidate.memory
            )
            assert refusal == (None if first is None else first.refusal())
        assert evaluated.call_count == 1

        before = world.reanchor("RLZ-A00001")  # a changed memory tree misses
        _answered(world, before, *trace_rows(*TRACES))
        answered = world.candidate()
        assert evaluate_leaf_gate(world.contract, answered) is not None
        assert evaluated.call_count == 2
        path = world.contract_path()  # a changed contract (here its ledger cell) misses
        path.write_text(path.read_text().replace("/memory.md\n", "/ledger.md\n"))
        assert evaluate_leaf_gate(load_contract(path), answered) is not None
        assert evaluated.call_count == 3

        # Incomplete and failed runs are never kept: each evaluation reads again.
        world.task_document(title="Leaf, renamed")  # a new task document: a fresh key
        incomplete = gate_module.incomplete_worklist(
            gate_module.Incomplete("git", "timed out"), owner=LEAF, pairing=None
        )
        with mock.patch.object(gate_module, "recompute_for_gate", return_value=incomplete):
            for _ in range(2):
                assert evaluate_leaf_gate(world.contract, answered) is not None
        assert evaluated.call_count == 5
        with mock.patch.object(gate_module, "leaf_worklist", side_effect=RuntimeError("boom")):
            for _ in range(2):
                failed = evaluate_leaf_gate(world.contract, answered)
                assert failed is not None and not failed.memoisable
        assert evaluated.call_count == 7

    world.task_document()  # back to the inputs of the second evaluation, whose verdict passed
    key = memo.memo_key(world.contract, answered, git(world.memory, "rev-parse", "main"))
    assert key is not None and memo.remembered(key) is not None  # the passing verdict is kept
    late = time.monotonic() + memo.MAX_AGE_SECONDS + 1
    assert memo.remembered(key, now=late) is None  # and never older than one run's span


# --------------------------------------------------------------------------------------------------
# Rulings round 2 (2026-09-30): a kept verdict is never reused past a change of approval state
# --------------------------------------------------------------------------------------------------

DECISION = "DEC-AAAAAA"
REQUIREMENT_TASK = {"repository": "agents-remember", "path": "260928_gate"}
REQUIREMENT_PACKET = "requirements/MIK-R21-v1-formats.md"


def _decided(world: Gated) -> Path:
    """K_B gains a decision whose rejected alternative is linked to a requirement endpoint.

    Returns the owning task's root, whose v1 packet the requirement owner resolves.
    """

    endpoint = {
        "task": REQUIREMENT_TASK,
        "packet": REQUIREMENT_PACKET,
        "id": "MIK-R21",
        "version": "v1",
    }
    record = canonical_text(
        {
            "schema": "ar-decision/v1",
            "id": DECISION,
            "revision": 1,
            "status": "active",
            "context": "A choice that binds later work.",
            "alternatives": [
                {"option": "Text files", "status": "chosen", "reason": "Weighed."},
                {
                    "option": "A database",
                    "status": "rejected",
                    "reason": "Weighed.",
                    "reconsider_when": "The format requirement changes.",
                },
            ],
            "consequences": ["Later work follows it."],
            "decider": "developer",
            "supersedes": [],
            "links": [
                {"relation": "constrains", "target": "route:pkg"},
                {"relation": "reconsider_on", "target": endpoint, "alternative": 1},
            ],
            "admission": {"criteria": ["real_alternatives"], "justification": "Weighed, chosen."},
            "origin": ORIGIN,
        }
    )
    git(world.memory, "checkout", "-q", "main")
    world.memory_base = commit(
        world.memory, {f"knowledge/decisions/{DECISION}-text.json": record}, trailer=world.code_base
    )
    git(world.memory, "checkout", "-q", "leaf")
    git(world.memory, "merge", "-q", "--no-edit", "main")
    task_root = world.root / "tasks" / REQUIREMENT_TASK["repository"] / REQUIREMENT_TASK["path"]
    (task_root / "requirements").mkdir(parents=True)
    (task_root / REQUIREMENT_PACKET).write_text(
        "# MIK-R21 @ v1\n\n| Field | Value |\n| --- | --- |\n| Stable ID | MIK-R21 |\n"
        "| Version | v1 |\n",
        encoding="utf-8",
    )
    return task_root


def _approve(task_root: Path, *versions: str) -> None:
    packets = [{"id": "MIK-R21", "version": one, "state": "approved"} for one in versions]
    (task_root / "requirements" / "manifest.json").write_text(
        json.dumps({"format": "approved-requirement-corpus", "packets": packets}), encoding="utf-8"
    )


def _reconsidered(result: Any) -> bool:
    return f"reconsideration_candidate reconsider:{DECISION}#1" in _open(result)


@pytest.mark.parametrize("before", ["v1 approved", "manifest missing"])
def test_a_kept_pass_is_recomputed_once_an_endpoint_s_approval_state_changes(
    world: Gated, before: str
) -> None:
    """A newly approved version -- or a manifest that appears -- is a miss, never a reused pass."""

    memo.GATE_MEMO.clear()
    task_root = _decided(world)
    if before == "v1 approved":
        _approve(task_root, "v1")
    candidate = world.candidate()
    evaluated = mock.Mock(wraps=gate_module._evaluate)
    with mock.patch.object(gate_module, "_evaluate", evaluated):
        kept = evaluate_leaf_gate(world.contract, candidate)
        assert kept is not None
        assert kept.ok, kept.refusal()
        assert evaluate_leaf_gate(world.contract, candidate) is kept  # unchanged: reused
        assert evaluated.call_count == 1

        _approve(task_root, "v1", "v2")  # the endpoint's task approves a newer version
        recomputed = evaluate_leaf_gate(world.contract, candidate)
        assert evaluated.call_count == 2  # the same trees and contract, yet recomputed
        assert recomputed is not None and not recomputed.ok
        assert _reconsidered(recomputed)
