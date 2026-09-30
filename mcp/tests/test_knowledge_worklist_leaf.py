"""MIK-R08@v2 on a leaf: pairing by trailer, sync, persistence, surfaces and the writer's carry.

The fixture is a real code repository and a real converted memory repository on ``main`` (the
official line), plus a task root holding the leaf's task document and its series contract in the
leaf's enclosure -- exactly the inputs the curator's memory-quality run hands the worklist.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_worklist import (
    WORKLIST_FILE_NAME,
    ExplicitSides,
    leaf_worklist,
    worklist_for_sides,
)
from agents_remember.application.knowledge_worklist import base_cache as worklist_base_cache
from agents_remember.application.knowledge_worklist import leaf as worklist_leaf
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.knowledge_writer.memory_state import Owner
from agents_remember.application.knowledge_writer.writer import WriteRequest, write_knowledge
from agents_remember.application.memory_quality import controller
from agents_remember.application.memory_scope import MemoryScope, MemoryScopeIdentity
from agents_remember.errors import CuratorCoherenceError
from agents_remember.mcp.tools.knowledge import (
    IntegrityCheckRequest,
    knowledge_integrity_check_payload,
)
from agents_remember.memory_quality.knowledge_worklist_section import (
    WORKLIST_SECTION_HEADING,
    knowledge_worklist_lines,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.worktrees import sync_transaction_recovery as sync_recovery
from agents_remember.worktrees import sync_transaction_results as sync_results
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.sync_transaction_recovery import recompute_knowledge_worklist
from agents_remember.worktrees.worktree_contract import load_contract

LEAF = "260928-MIK-L99"
CODE_FILE = "pkg/a.py"
LINES_FILE = "pkg/notes.txt"
ORIGIN = {"task": "260101-OLD", "leaf": "260101-OLD-L1"}
CODE_V1 = "def land(value):\n    return value\n\n\ndef keep():\n    return 1\n"
NOTES_V1 = "".join(f"note {number}\n" for number in range(1, 9))


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


def _init(root: Path) -> None:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "worklist fixture")


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def commit(root: Path, files: dict[str, str], trailer: str | None = None) -> str:
    _write(root, files)
    git(root, "add", "-A")
    message = "change" if trailer is None else f"memory\n\nCode-Commit: {trailer}"
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def anchor(code: Path, commit_id: str, path: str, locator: dict[str, Any]) -> dict[str, Any]:
    tree = git(code, "rev-parse", f"{commit_id}^{{tree}}")
    trees = CodeTrees.open(code, tree, tree)
    blob = trees.base()[path]
    resolved = trees.resolve(path, locator, blob, blob)
    assert resolved is not None
    return {"locator": locator, "blob": blob, "content": resolved.content}


def invariant(revision: int = 1, statement: str = "Values land unchanged.") -> str:
    return canonical_text(
        {
            "schema": "ar-invariant/v1",
            "id": "INV-AAAAAA",
            "revision": revision,
            "status": "accepted",
            "statement": statement,
            "applicability": "Always.",
            "conditions": [],
            "exclusions": [],
            "supersedes": [],
            "admission": "legacy-unassessed",
            "origin": ORIGIN,
        }
    )


def converted_memory(code: Path, code_commit: str) -> dict[str, str]:
    def realization(entry_id: str, path: str, locator: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": entry_id,
            "invariant": "INV-AAAAAA",
            "anchor": anchor(code, code_commit, path, locator),
            "role": "primary-authority",
            "rationale": "It is the rule.",
        }

    def sidecar(path: str, *entries: dict[str, Any]) -> str:
        return canonical_text(
            {
                "schema": "ar-onboarding-file/v1",
                "path": path,
                "references": {},
                "realizes": list(entries),
            }
        )

    return {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
        "knowledge/invariants/INV-AAAAAA-land.json": invariant(),
        "knowledge/families/FAM-F00001-landing.json": canonical_text(
            {
                "schema": "ar-family/v1",
                "id": "FAM-F00001",
                "revision": 1,
                "status": "accepted",
                "title": "Landing",
                "guarantee": "Landing holds.",
                "members": ["INV-AAAAAA"],
                "routes": ["pkg"],
                "admission": "legacy-unassessed",
                "origin": ORIGIN,
            }
        ),
        f"onboarding/{CODE_FILE}.md": "# a\n",
        f"onboarding/{CODE_FILE}.json": sidecar(
            CODE_FILE,
            realization("RLZ-A00001", CODE_FILE, {"kind": "symbol", "name": "land"}),
            realization("RLZ-A00002", CODE_FILE, {"kind": "symbol", "name": "keep"}),
        ),
        f"onboarding/{LINES_FILE}.md": "# notes\n",
        f"onboarding/{LINES_FILE}.json": sidecar(
            LINES_FILE,
            realization("RLZ-A00003", LINES_FILE, {"kind": "line_range", "start": 4, "end": 5}),
        ),
        "onboarding/overview.md": "# root\n",
    }


@dataclass
class Leaf:
    root: Path
    code: Path
    memory: Path
    task_root: Path
    code_base: str
    memory_base: str

    def contract(self, base: str | None = None) -> Path:
        enclosure = self.task_root / "enclosures" / LEAF.lower()
        enclosure.mkdir(parents=True, exist_ok=True)
        path = enclosure / "series-contract.md"
        path.write_text(
            "---\nschema: ar-series-contract/v1\nschemaVersion: 1.0\nkind: leaf\n"
            "task_id: 260928_WORKLIST-CASE\ntask_name: worklist_case\nrepo_name: agents-remember\n"
            "workflow_kind: light-task\nmemory_mode: external\n\ncoordination:\n"
            f"  root: {self.root}\n  task_root: {self.task_root}\n"
            f"  task_artifact: {self.task_root / 'task.md'}\n  worktree_group: {self.root}\n"
            f"  leaf_id: {LEAF}\n  parent_task_name: worklist_case\n\ncode:\n"
            f"  repo_path: {self.code}\n  source_branch: main\n  work_branch: main\n"
            f"  base_commit: {base or self.code_base}\n  worktree: {self.code}\n\nmemory:\n"
            f"  mode: external\n  repo_path: {self.memory}\n  source_branch: main\n"
            f"  work_branch: main\n  base_commit: {self.memory_base}\n"
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
            "createdAt": "2026-09-29T00:00+02:00",
            **fields,
        }
        (self.task_root / "leaf.json").write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def leaf(tmp_path: Path) -> Leaf:
    code, memory, task_root = tmp_path / "code", tmp_path / "memory", tmp_path / "task"
    _init(code)
    code_base = commit(code, {CODE_FILE: CODE_V1, LINES_FILE: NOTES_V1})
    _init(memory)
    memory_base = commit(memory, converted_memory(code, code_base), trailer=code_base)
    task_root.mkdir()
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    world = Leaf(tmp_path, code, memory, task_root, code_base, memory_base)
    world.task_document()
    return world


def _kinds(document: dict[str, Any]) -> set[tuple[str, str]]:
    return {(item["kind"], item["subject"]) for item in document["items"]}


def test_a_leaf_pairs_k_b_by_trailer_follows_its_sync_and_persists_beside_its_contract(
    leaf: Leaf,
) -> None:
    # The official memory line moves on: a later code commit and a memory commit naming it.
    later_code = commit(leaf.code, {"pkg/b.py": "X = 1\n"})
    later_memory = commit(
        leaf.memory,
        {"knowledge/invariants/INV-AAAAAA-land.json": invariant(2, "Values land as given.")},
        trailer=later_code,
    )
    # An uncommitted edit of `land` is part of C.
    (leaf.code / CODE_FILE).write_text(CODE_V1.replace("return value", "return value + 0"))
    contract = leaf.contract()
    document = leaf_worklist(load_contract(contract))
    assert document is not None and document["state"] == "complete"
    # B is the fork point: K_B is the memory commit naming it, not the later one.
    assert document["pairing"]["memoryBase"]["commit"] == leaf.memory_base
    assert document["pairing"]["base"]["commit"] == leaf.code_base
    assert ("touched_invariant", "INV-AAAAAA") in _kinds(document)
    touched = next(item for item in document["items"] if item["kind"] == "touched_invariant")
    assert touched["facts"]["record"] == {
        "changed": True,
        "baseRevision": 1,
        "candidateRevision": 2,
    }
    assert {entry["id"]: entry["class"] for entry in touched["facts"]["entries"]}[
        "RLZ-A00001"
    ] == "touched"
    persisted = json.loads((contract.parent / WORKLIST_FILE_NAME).read_text(encoding="utf-8"))
    assert persisted == document
    assert not (leaf.memory / WORKLIST_FILE_NAME).exists()

    # After a sync the base is the later code commit, so K_B is the later memory commit.
    synced = leaf_worklist(load_contract(leaf.contract(base=later_code)))
    assert synced is not None
    assert synced["pairing"]["memoryBase"]["commit"] == later_memory
    touched = next(item for item in synced["items"] if item["kind"] == "touched_invariant")
    assert touched["facts"]["record"]["changed"] is False
    # A base no memory commit names directly pairs with the newest one naming an ancestor.
    newest = commit(leaf.code, {"pkg/c.py": "Y = 2\n"})
    ancestor = leaf_worklist(load_contract(leaf.contract(base=newest)))
    assert ancestor is not None
    assert ancestor["pairing"]["memoryBase"]["commit"] == later_memory


def test_a_base_no_memory_commit_pairs_with_is_incomplete_naming_the_pairing(leaf: Leaf) -> None:
    git(leaf.code, "checkout", "-q", "--orphan", "elsewhere")
    stranger = commit(leaf.code, {"z.py": "Z = 0\n"})
    git(leaf.code, "checkout", "-q", "main")
    document = leaf_worklist(load_contract(leaf.contract(base=stranger)))
    assert document is not None and document["state"] == "incomplete"
    assert document["incomplete"][0]["input"] == "pairing"


def test_the_task_documents_maintenance_scope_classifies_every_entry(leaf: Leaf) -> None:
    leaf.task_document(knowledgeMaintenanceScope=True)
    document = leaf_worklist(load_contract(leaf.contract()))
    assert document is not None
    assert document["scope"]["knowledgeMaintenanceScope"] is True
    assert {entry["id"] for entry in document["entries"]} == {
        "RLZ-A00001",
        "RLZ-A00002",
        "RLZ-A00003",
    }


def test_an_unconverted_base_is_compared_as_its_conversion(tmp_path: Path) -> None:
    code, memory = tmp_path / "code", tmp_path / "memory"
    _init(code)
    code_base = commit(code, {CODE_FILE: CODE_V1, LINES_FILE: NOTES_V1})
    _init(memory)
    legacy = commit(memory, {"onboarding/overview.md": "# root\n"}, trailer=code_base)
    commit(memory, converted_memory(code, code_base), trailer=code_base)
    document = worklist_for_sides(
        ExplicitSides(
            code_repository=code,
            base=code_base,
            memory_repository=memory,
            memory_base=legacy,
            memory_candidate="HEAD",
            code_candidate=code_base,
        )
    )
    assert document is not None and document["state"] == "complete"
    assert document["pairing"]["memoryBase"]["convertedBase"] is True
    assert document["pairing"]["memoryBase"]["conversion"] == "1"
    # Everything in K_C is new against the converted base: new records raise nothing.
    assert document["items"] == []


def test_the_writer_carries_moved_blobs_and_the_rows_that_cover_them(leaf: Leaf) -> None:
    covered = anchor(
        leaf.code, leaf.code_base, LINES_FILE, {"kind": "line_range", "start": 4, "end": 5}
    )
    history = {
        "schema": "ar-history/v1",
        "leaf": LEAF,
        "closed": False,
        "rows": [
            {
                "id": "ROW-AAAAAA",
                "subject": "INV-AAAAAA",
                "disposition": "no_impact",
                "reason": "Checked.",
                "items": [],
                "covers": [
                    {
                        "id": "RLZ-A00003",
                        "before": {**covered, "path": LINES_FILE},
                        "after": {**covered, "path": LINES_FILE},
                    }
                ],
                "revision": 1,
            }
        ],
    }
    _write(leaf.memory, {f"knowledge/history/{LEAF}.json": canonical_text(history)})
    # Code changes elsewhere in both files: `keep` and lines 4-5 keep their bytes.
    _write(
        leaf.code,
        {
            CODE_FILE: CODE_V1.replace("return value", "return value * 1"),
            LINES_FILE: "note 0\n" + NOTES_V1,
        },
    )
    report = write_knowledge(
        WriteRequest(
            memory_root=leaf.memory,
            code_root=leaf.code,
            owner=Owner(task="260928-MIK", kind="leaf", id=LEAF),
            handoff_path="handoff.json",
            document=[],
            commit=True,
        )
    )
    assert report.state == "written", report.render()
    assert report.carried == ("RLZ-A00002", "RLZ-A00003")
    notes = json.loads((leaf.memory / f"onboarding/{LINES_FILE}.json").read_text())
    moved = notes["realizes"][0]["anchor"]
    assert moved["locator"] == {"kind": "line_range", "start": 5, "end": 6}
    assert moved["content"] == covered["content"] and moved["blob"] != covered["blob"]
    code_sidecar = json.loads((leaf.memory / f"onboarding/{CODE_FILE}.json").read_text())
    anchors = {entry["id"]: entry["anchor"] for entry in code_sidecar["realizes"]}
    assert anchors["RLZ-A00001"]["blob"] != anchors["RLZ-A00002"]["blob"]  # touched: not carried
    written = json.loads((leaf.memory / f"knowledge/history/{LEAF}.json").read_text())
    assert written["rows"][0]["covers"][0]["after"] == {**moved, "path": LINES_FILE}
    assert written["rows"][0]["covers"][0]["before"] == {**covered, "path": LINES_FILE}


def test_the_tool_returns_the_latest_worklist_and_the_checklist_shows_it(leaf: Leaf) -> None:
    contract = leaf.contract()
    absent = knowledge_integrity_check_payload(IntegrityCheckRequest(contractPath=str(contract)))
    assert absent["state"] == "reported" and absent["worklistState"] == "absent"
    (leaf.code / CODE_FILE).write_text(CODE_V1.replace("return value", "return -value"))
    document = leaf_worklist(load_contract(contract))
    assert document is not None
    present = knowledge_integrity_check_payload(IntegrityCheckRequest(contractPath=str(contract)))
    assert present["worklistState"] == "present"
    worklist = present["worklist"]
    assert worklist["digest"] == document["digest"] and worklist["owner"] == LEAF
    assert worklist["itemsByKind"] == {
        "reached_family": 1,
        "touched_invariant": 1,
        "onboarding_trace": 2,  # MIK-R30: the edited file's card and its root route (ruling Q2)
    }
    assert [item["id"] for item in worklist["items"]] == [item["id"] for item in document["items"]]
    neither = knowledge_integrity_check_payload(IntegrityCheckRequest())
    assert neither["state"] == "refused"
    lines = knowledge_worklist_lines(document, "w.json")
    assert lines[0] == WORKLIST_SECTION_HEADING
    assert any(
        "| touched_invariant | INV-AAAAAA |" in line and "RLZ-A00001 touched" in line
        for line in lines
    )
    incomplete = {
        **document,
        "state": "incomplete",
        "items": [],
        "incomplete": [{"input": "K_B", "detail": "x"}],
    }
    assert any("`K_B`: x" in line for line in knowledge_worklist_lines(incomplete, None))


def test_the_memory_quality_run_recomputes_persists_and_names_its_own_failure(leaf: Leaf) -> None:
    contract = load_contract(leaf.contract())
    scope = cast(Any, SimpleNamespace(contract=contract))
    found = controller._knowledge_worklist(scope)
    assert found is not None
    document, path = found
    assert path is not None
    assert path == (leaf.task_root / "enclosures" / LEAF.lower() / WORKLIST_FILE_NAME).as_posix()
    assert json.loads(Path(path).read_text(encoding="utf-8")) == document
    with mock.patch.object(worklist_leaf, "leaf_worklist", side_effect=RuntimeError("boom")):
        failed = controller._knowledge_worklist(scope)
    assert failed is not None and failed[0]["state"] == "incomplete"
    assert failed[0]["incomplete"][0]["input"] == "worklist run"
    assert controller._knowledge_worklist(cast(Any, SimpleNamespace(contract=None))) is None


def _history(owner: str, closed: bool, covered: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "ar-history/v1",
        "leaf": owner,
        "closed": closed,
        "rows": [
            {
                "id": "ROW-BBBBBB",
                "subject": "INV-AAAAAA",
                "disposition": "no_impact",
                "reason": "Checked.",
                "items": [],
                "covers": [
                    {
                        "id": "RLZ-A00003",
                        "before": {**covered, "path": LINES_FILE},
                        "after": {**covered, "path": LINES_FILE},
                    }
                ],
                "revision": 1,
            }
        ],
    }


def _carry(leaf: Leaf) -> Any:
    _write(leaf.code, {LINES_FILE: "note 0\n" + NOTES_V1})
    return write_knowledge(
        WriteRequest(
            memory_root=leaf.memory,
            code_root=leaf.code,
            owner=Owner(task="260928-MIK", kind="leaf", id=LEAF),
            handoff_path="handoff.json",
            document=[],
            commit=True,
        )
    )


def test_carrying_never_edits_another_owners_row_or_a_closed_history_file(leaf: Leaf) -> None:
    """Ruling Q5: only this leaf's own OPEN row that still holds the old anchor is updated."""

    covered = anchor(
        leaf.code, leaf.code_base, LINES_FILE, {"kind": "line_range", "start": 4, "end": 5}
    )
    other = "knowledge/history/260928-MIK-L98.json"
    _write(leaf.memory, {other: canonical_text(_history("260928-MIK-L98", False, covered))})
    other_bytes = (leaf.memory / other).read_bytes()
    report = _carry(leaf)
    assert report.state == "written" and report.carried == ("RLZ-A00003",), report.render()
    assert (leaf.memory / other).read_bytes() == other_bytes  # another owner's row: untouched

    own = f"knowledge/history/{LEAF}.json"
    _write(leaf.memory, {own: canonical_text(_history(LEAF, True, covered))})
    git(leaf.memory, "checkout", "-q", "--", f"onboarding/{LINES_FILE}.json")
    closed_bytes = (leaf.memory / own).read_bytes()
    refused = _carry(leaf)
    # The closed file is frozen: the carry does not rewrite its row, so the row now contradicts
    # the candidate and the writer refuses, naming the freeze, and writes nothing.
    assert (leaf.memory / own).read_bytes() == closed_bytes
    assert refused.state == "refused"
    assert any("closed and frozen" in problem.message for problem in refused.problems)


def test_a_proof_authored_through_the_writer_raises_its_invariant_when_its_test_changes(
    leaf: Leaf,
) -> None:
    """Carried from L28: a proof written through the writer's ``proofs`` key joins the worklist."""

    test_file = "tests/test_a.py"
    test_source = "from pkg.a import land\n\n\ndef test_land():\n    assert land(1) == 1\n"
    code_commit = commit(leaf.code, {test_file: test_source})
    _write(leaf.memory, {f"onboarding/{test_file}.md": "# tests\n"})
    entry = {
        "id": "M-1",
        "statement": "Values land unchanged.",
        "kind": "clause",
        "target": [],
        "found_at": [],
        "disposition": "satisfied",
        "disposition_source": None,
        "evidence": None,
        "authority": {"task_document": "worklist_case"},
        "resolution": None,
        "validated_at": None,
        "record_action": None,
        "supersedes": None,
        "invariant_id": "INV-AAAAAA",
        "proofs": [{"test": f"{test_file}::test_land", "facet": "a value lands as given"}],
    }
    report = write_knowledge(
        WriteRequest(
            memory_root=leaf.memory,
            code_root=leaf.code,
            owner=Owner(task="260928-MIK", kind="leaf", id="260928-MIK-L97"),
            handoff_path="handoff.json",
            document=[entry],
            commit=True,
        )
    )
    assert report.state == "written", report.render()
    (proof,) = json.loads((leaf.memory / f"onboarding/{test_file}.json").read_text())["proves"]
    commit(leaf.memory, {}, trailer=code_commit)
    (leaf.code / test_file).write_text(test_source.replace("land(1) == 1", "land(2) == 2"))
    document = leaf_worklist(load_contract(leaf.contract(base=code_commit)))
    assert document is not None and document["state"] == "complete"
    touched = next(item for item in document["items"] if item["kind"] == "touched_invariant")
    classes = {entry["id"]: entry["class"] for entry in touched["facts"]["entries"]}
    assert touched["subject"] == "INV-AAAAAA" and classes[proof["id"]] == "touched"


def test_a_completed_sync_recomputes_through_the_bound_port(leaf: Leaf) -> None:
    """Ruling Q1: managed-sync completion recomputes the worklist through the composition port."""

    contract = load_contract(leaf.contract())
    reset_worktree_services()
    assert recompute_knowledge_worklist(contract) is None  # nothing bound: the sync is unchanged
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=cast(Any, None),
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_worklist=worklist_leaf.LeafWorklistRecompute(),
        )
    )
    try:
        summary = recompute_knowledge_worklist(contract)
    finally:
        reset_worktree_services()
    assert summary is not None and summary["state"] == "complete"
    assert Path(str(summary["path"])).is_file()


# --------------------------------------------------------------------------------------------------
# Fix round 1 (review R1)
# --------------------------------------------------------------------------------------------------


def test_the_memory_quality_controller_persists_the_worklist_and_renders_it_in_the_checklist(
    leaf: Leaf,
) -> None:
    """F1: the controller path itself (``_execute_memory_quality``), not the helper."""

    (leaf.code / CODE_FILE).write_text(CODE_V1.replace("return value", "return -value"))
    contract = load_contract(leaf.contract())
    pair = MemoryCandidatePairIdentity(
        repoId="agents-remember",
        contractPath=str(contract.contract_path),
        contractDigest="9" * 64,
        codeRoot=str(leaf.code),
        memoryRoot=str(leaf.memory),
        codeSourceBranch="main",
        codeWorkBranch="main",
        codeBaseCommit=leaf.code_base,
        memorySourceBranch="main",
        memoryWorkBranch="main",
        memoryBaseCommit=leaf.memory_base,
        onboardingRoot=str(leaf.memory / "onboarding"),
        ledgerPath=str(leaf.memory / "memory.md"),
    )
    report = leaf.root / "group" / "reports" / "curator-memory-quality.md"
    scope = MemoryScope(
        repo_id="agents-remember",
        identity=MemoryScopeIdentity(
            authority="leaf",
            authority_path=pair.contractPath,
            code_root=pair.codeRoot,
            onboarding_root=pair.onboardingRoot,
            pair_identity=pair,
        ),
        code_root=leaf.code,
        onboarding_root=leaf.memory / "onboarding",
        context=mock.Mock(),
        curator_report_path=report,
        contract=contract,
        pair_identity=pair,
    )
    execution = controller.MemoryQualityExecution(
        config=mock.Mock(),
        scope=scope,
        checks=(),
        detail_limit=50,
        publish_curator_report=True,
    )
    census = SimpleNamespace(
        scope=SimpleNamespace(pair_identity=pair, working_paths=(), committed_paths=()),
        result=SimpleNamespace(rows=[], blockers=[]),
    )
    quality = {"ok": True, "checks": {}, "findings": [], "findingCount": 0}
    with (
        mock.patch.object(controller, "revalidate_memory_candidate_scope", return_value=scope),
        # The exact candidate trees are captured for real: MIK-R09's gate judges them.
        mock.patch.object(controller, "prepare_memory_census", return_value=census),
        mock.patch.object(controller, "publish_memory_census", return_value={}),
        mock.patch.object(controller, "census_curator_candidates", return_value=()),
        mock.patch.object(controller, "run_memory_quality_check", return_value=quality),
        mock.patch.object(
            controller, "check_missing_onboarding", return_value={"missingCount": 0, "missing": []}
        ),
        mock.patch.object(
            controller, "build_route_indexes", return_value=mock.Mock(stale_indexes=[])
        ),
        mock.patch.object(controller, "split_commit_owned_findings", return_value=([], [])),
        mock.patch.object(
            controller,
            "require_current_curator_coherence",
            side_effect=CuratorCoherenceError("absent", "none recorded"),
        ),
        mock.patch.object(controller, "validate_memory_refresh_attestations"),
        mock.patch.object(controller, "contract_memory_verified_commit", return_value=""),
        mock.patch.object(controller, "_without_proof", return_value=None),
        mock.patch.object(controller, "_attach_coherence_readiness"),
        mock.patch.object(controller, "_attach_final_full_catalog"),
    ):
        response = controller._execute_memory_quality(execution)

    persisted = contract.contract_path.parent / WORKLIST_FILE_NAME
    document = json.loads(persisted.read_text(encoding="utf-8"))
    assert document["state"] == "complete"
    summary = cast(dict[str, Any], response["knowledgeWorklist"])
    assert summary["digest"] == document["digest"] and summary["path"] == persisted.as_posix()
    assert summary["itemsByKind"] == {
        "reached_family": 1,
        "touched_invariant": 1,
        "onboarding_trace": 2,  # MIK-R30: the edited file's card and its root route (ruling Q2)
    }
    rendered = report.read_text(encoding="utf-8")
    assert WORKLIST_SECTION_HEADING in rendered
    assert "| touched_invariant | INV-AAAAAA |" in rendered
    # MIK-R09: each open item is one repair finding, so the four open items now count.
    assert response["curatorActionableCount"] == 4
    assert cast(dict[str, Any], response["knowledgeGate"])["openItemCount"] == 4


def test_the_recompute_never_raises_and_a_failure_never_fails_a_completed_sync(
    leaf: Leaf,
) -> None:
    """F2: an unwritable enclosure, a failing port and a failing reload are all absorbed."""

    contract = load_contract(leaf.contract())
    with mock.patch.object(worklist_leaf, "persist_worklist", side_effect=OSError("read-only")):
        found = worklist_leaf.recompute_leaf_worklist(contract)
    assert found is not None and found[0]["state"] == "complete" and found[1] is None
    with (
        mock.patch.object(worklist_leaf, "leaf_worklist", side_effect=RuntimeError("boom")),
        mock.patch.object(worklist_leaf, "persist_worklist", side_effect=OSError("read-only")),
    ):
        failed = worklist_leaf.recompute_leaf_worklist(contract)
    assert failed is not None and failed[0]["incomplete"][0]["input"] == "worklist run"

    failing_port = mock.Mock()
    failing_port.recompute.side_effect = RuntimeError("port failed")
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=cast(Any, None),
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_worklist=failing_port,
        )
    )
    try:
        assert recompute_knowledge_worklist(contract) is None
        with mock.patch.object(
            sync_recovery, "reload_contract", side_effect=RuntimeError("contract gone")
        ):
            assert recompute_knowledge_worklist(contract) is None
        completed = WorktreeCommandResult(0, {"state": "synced"})
        assert sync_recovery.with_recomputed_worklist(completed, contract).payload == {
            "state": "synced"
        }
    finally:
        reset_worktree_services()


def test_the_continue_replay_of_a_completed_sync_recomputes_too(leaf: Leaf) -> None:
    """F2: ``resolution_action='continue'`` on a completed generation reports the worklist."""

    contract = load_contract(leaf.contract())
    record = cast(Any, SimpleNamespace(phase="completed", memorySyncChoice=None))
    args = cast(Any, SimpleNamespace(memory_sync_choice=None, resolution_action="continue"))
    replayed = WorktreeCommandResult(0, {"state": "synced"})
    with (
        mock.patch.object(sync_results, "completed_sync_result", return_value=replayed),
        mock.patch.object(
            sync_recovery, "recompute_knowledge_worklist", return_value={"state": "complete"}
        ),
    ):
        result = sync_results.terminal_resolution_replay(contract, args, record, {})
    assert result.payload["knowledgeWorklist"] == {"state": "complete"}


def test_converted_bases_are_cached_by_commit_version_and_code_commit(tmp_path: Path) -> None:
    """F6: the second run over the same unconverted K_B reads the cache instead of converting."""

    code, memory = tmp_path / "code", tmp_path / "memory"
    _init(code)
    code_base = commit(code, {CODE_FILE: CODE_V1, LINES_FILE: NOTES_V1})
    _init(memory)
    legacy = commit(memory, {"onboarding/overview.md": "# root\n"}, trailer=code_base)
    commit(memory, converted_memory(code, code_base), trailer=code_base)
    cache = tmp_path / "coordination" / "runtime" / "knowledge-worklist-bases"
    sides = ExplicitSides(
        code_repository=code,
        base=code_base,
        memory_repository=memory,
        memory_base=legacy,
        memory_candidate="HEAD",
        code_candidate=code_base,
        cache_directory=cache,
    )
    first = worklist_for_sides(sides)
    assert first is not None and first["state"] == "complete"
    assert len(list(cache.glob("*.json.gz"))) == 1
    with mock.patch.object(
        worklist_base_cache, "converted_base", side_effect=AssertionError("reconvert")
    ):
        second = worklist_for_sides(sides)
    assert second == first
    # A cache location inside a Git working tree is refused: the run converts and writes nothing.
    inside = replace(sides, cache_directory=memory / ".cache" / "bases")
    with mock.patch.object(
        worklist_base_cache, "converted_base", wraps=worklist_base_cache.converted_base
    ) as converting:
        third = worklist_for_sides(inside)
    assert third == first and converting.called
    assert not (memory / ".cache").exists()
