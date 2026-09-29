"""MIK-R30@v1: the onboarding refresh gate on history files, on converted-format fixtures.

A real code repository and a real converted memory repository (``main`` is the official line, its
commit carries the ``Code-Commit`` trailer naming the leaf's base), plus a leaf series contract --
the inputs the curator's memory-quality run and the closeout validator hand the gate.
"""

from __future__ import annotations

import gzip
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from agents_remember.application.knowledge_worklist import (
    ITEM_KINDS,
    base_cache,
    leaf_onboarding_trace_sides,
    recompute_leaf_worklist,
    satisfying_row,
)
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.knowledge_worklist.onboarding_trace import (
    TraceSideRequest,
    onboarding_trace_sides,
)
from agents_remember.application.knowledge_writer.memory_state import Owner
from agents_remember.application.knowledge_writer.writer import WriteRequest, write_knowledge
from agents_remember.application.memory_quality import controller
from agents_remember.application.memory_scope import MemoryScope, MemoryScopeIdentity
from agents_remember.errors import CuratorCoherenceError
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import convert_memory
from agents_remember.memory.conversion.inputs import memory_from_directory, write_changed
from agents_remember.memory_quality.reference_state import fix_references
from agents_remember.memory_quality.style.update_history.history_order_fix import (
    fix_onboarding_root,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.history import HistoryFile
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.worktrees.modules.models import VerifiedChange
from agents_remember.worktrees.modules.onboarding import (
    onboarding_trace_gate_for_context,
    refresh_onboarding_metadata_for_context,
    refresh_route_overview_metadata_for_context,
    validate_onboarding_traces_for_context,
)
from agents_remember.worktrees.modules.onboarding_trace import (
    ITEM_KIND,
    MISSING_CODE,
    OnboardingTraceSides,
    counted_markdown_change,
    counted_sidecar_change,
    onboarding_item_open,
    onboarding_trace_result,
)
from agents_remember.worktrees.worktree_contract import load_contract

LEAF = "260928-MIK-L98"
A, B, C = "pkg/a.py", "pkg/b.py", "pkg/c.py"
SOURCE = "def land(value):\n    return value\n\n\ndef keep():\n    return 1\n"
LAYOUT = canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"})


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
    git(root, "config", "user.name", "onboarding trace fixture")


def commit(root: Path, files: dict[str, str], trailer: str | None = None) -> str:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git(root, "add", "-A")
    message = "change" if trailer is None else f"memory\n\nCode-Commit: {trailer}"
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def _sidecar(
    code: Path, code_commit: str, path: str, locator: dict[str, Any], *, realizes: bool = False
) -> str:
    tree = git(code, "rev-parse", f"{code_commit}^{{tree}}")
    trees = CodeTrees.open(code, tree, tree)
    blob = trees.base()[path]
    resolved = trees.resolve(path, locator, blob, blob)
    assert resolved is not None
    target = {"locator": locator, "blob": blob, "content": resolved.content}
    entries = [
        {
            "id": "RLZ-A00003",
            "invariant": "INV-AAAAAA",
            "anchor": target,
            "role": "support",
            "rationale": "Y is the rule.",
        }
    ]
    return canonical_text(
        {
            "schema": "ar-onboarding-file/v1",
            "path": path,
            "references": {"1": {"targets": [{"kind": "code", "anchor": target}], "note": "Why."}},
            "realizes": entries if realizes else [],
        }
    )


INVARIANT = canonical_text(
    {
        "schema": "ar-invariant/v1",
        "id": "INV-AAAAAA",
        "revision": 1,
        "status": "accepted",
        "statement": "Y is two.",
        "applicability": "Always.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
        "origin": {"task": "260101-OLD", "leaf": "260101-OLD-L1"},
    }
)


@dataclass
class Leaf:
    root: Path
    code: Path
    memory: Path
    task_root: Path
    code_base: str
    memory_base: str

    def contract(self) -> Path:
        enclosure = self.task_root / "enclosures" / LEAF.lower()
        enclosure.mkdir(parents=True, exist_ok=True)
        path = enclosure / "series-contract.md"
        path.write_text(
            "---\nschema: ar-series-contract/v1\nschemaVersion: 1.0\nkind: leaf\n"
            "task_id: 260928_TRACE-CASE\ntask_name: trace_case\nrepo_name: agents-remember\n"
            "workflow_kind: light-task\nmemory_mode: external\n\ncoordination:\n"
            f"  root: {self.root}\n  task_root: {self.task_root}\n"
            f"  task_artifact: {self.task_root / 'task.md'}\n  worktree_group: {self.root}\n"
            f"  leaf_id: {LEAF}\n  parent_task_name: trace_case\n\ncode:\n"
            f"  repo_path: {self.code}\n  source_branch: main\n  work_branch: main\n"
            f"  base_commit: {self.code_base}\n  worktree: {self.code}\n\nmemory:\n"
            f"  mode: external\n  repo_path: {self.memory}\n  source_branch: main\n"
            f"  work_branch: main\n  base_commit: {self.memory_base}\n"
            f"  worktree: {self.memory}\n  ledger: {self.memory / 'memory.md'}\n---\n",
            encoding="utf-8",
        )
        return path

    def context(self) -> SimpleNamespace:
        return SimpleNamespace(
            storage=StorageSettings(),
            code_repository_name="agents-remember",
            onboarding_root=self.memory / "onboarding",
        )

    def write(self, owner_rows: list[dict[str, Any]]) -> Any:
        return write_knowledge(
            WriteRequest(
                memory_root=self.memory,
                code_root=self.code,
                owner=Owner(task="260928-MIK", kind="leaf", id=LEAF),
                handoff_path="handoff.json",
                document={"history": owner_rows},
                commit=True,
            )
        )


@pytest.fixture
def leaf(tmp_path: Path) -> Leaf:
    code, memory, task_root = tmp_path / "code", tmp_path / "memory", tmp_path / "task"
    _init(code)
    base = commit(code, {A: SOURCE, B: "X = 1\n", C: "\n\nY = 2\n", "top.py": "Z = 3\n"})
    _init(memory)
    files = {
        "knowledge/layout.json": LAYOUT,
        "knowledge/invariants/INV-AAAAAA-y.json": INVARIANT,
        "onboarding/overview.md": "# root\n",
        "onboarding/pkg/overview.md": "# pkg\n\nThe package route.\n",
        "onboarding/pkg/overview.json": canonical_text(
            {"schema": "ar-onboarding-route/v1", "path": "pkg", "references": {}}
        ),
        f"onboarding/{A}.md": "# a\n\nLands values [1].\n",
        f"onboarding/{A}.json": _sidecar(code, base, A, {"kind": "symbol", "name": "land"}),
        f"onboarding/{B}.md": "# b\n",
        f"onboarding/{C}.md": "# c\n\nY is two [1].\n",
        f"onboarding/{C}.json": _sidecar(
            code, base, C, {"kind": "line_range", "start": 3, "end": 3}, realizes=True
        ),
        "onboarding/top.py.md": "# top\n",
    }
    memory_base = commit(memory, files, trailer=base)
    task_root.mkdir()
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    return Leaf(tmp_path, code, memory, task_root, base, memory_base)


def _gate(leaf: Leaf, changed: list[str]) -> Any:
    sides = leaf_onboarding_trace_sides(load_contract(leaf.contract()))
    assert sides is not None and sides.incomplete is None
    return onboarding_trace_result(leaf.context(), changed, sides)


def _by_subject(result: Any) -> dict[str, Any]:
    return {item.subject: item for item in result.items}


# --------------------------------------------------------------------------------------------------
# Rule 3: what counts
# --------------------------------------------------------------------------------------------------


def test_only_an_anchors_blob_line_numbers_and_content_do_not_count() -> None:
    anchor = {
        "locator": {"kind": "line_range", "start": 3, "end": 4},
        "blob": "a" * 40,
        "content": "sha256:" + "1" * 64,
    }
    sidecar = {
        "schema": "ar-onboarding-file/v1",
        "path": A,
        "references": {"1": {"targets": [{"kind": "code", "anchor": anchor}], "note": "N."}},
        "realizes": [
            {
                "id": "RLZ-A00001",
                "invariant": "INV-AAAAAA",
                "anchor": dict(anchor),
                "role": "support",
                "rationale": "R.",
            }
        ],
    }

    def variant(**changes: Any) -> bytes:
        document = json.loads(json.dumps(sidecar))
        for where, value in changes.items():
            node: Any = document
            *path, last = where.split("__")
            for key in path:
                node = node[int(key)] if isinstance(node, list) else node[key]
            node[last] = value
        return canonical_text(document).encode()

    before = canonical_text(sidecar).encode()
    mechanical = variant(
        references__1__targets__0__anchor__blob="b" * 40,
        references__1__targets__0__anchor__content="sha256:" + "2" * 64,
        realizes__0__anchor__locator={"kind": "line_range", "start": 9, "end": 10},
    )
    assert not counted_sidecar_change(before, mechanical)  # writer's or curator's, never counts
    assert not counted_sidecar_change(before, before)
    for authored in (
        variant(references__1__note="Other."),
        variant(realizes__0__rationale="Changed."),
        variant(realizes__0__anchor__locator={"kind": "symbol", "name": "land"}),
        variant(references__1__targets__0__anchor__path="pkg/other.py"),
    ):
        assert counted_sidecar_change(before, authored)
    assert counted_sidecar_change(None, before) and counted_sidecar_change(before, None)
    assert counted_markdown_change(b"# a\n", b"# a\n\nMore.\n")
    assert counted_markdown_change(None, b"# new\n") and not counted_markdown_change(b"x", b"x")


# --------------------------------------------------------------------------------------------------
# Rules 2, 6 and 7 on a converted leaf
# --------------------------------------------------------------------------------------------------


def test_the_conforming_example_and_the_route_case(leaf: Leaf) -> None:
    for path, text in ((A, SOURCE.replace("return value", "return -value")), (B, "X = 10\n")):
        (leaf.code / path).write_text(text, encoding="utf-8")
    changed = [A, B]
    # Nothing traced yet: both cards and their nearest route (pkg, not the root) are open.
    open_now = {item.subject for item in _gate(leaf, changed).open_items}
    assert open_now == {"onboarding:pkg/a.py", "onboarding:pkg/b.py", "onboarding:pkg/overview"}
    # The curator updates A's journal paragraph; B and the route get no_impact rows by the writer.
    card = leaf.memory / f"onboarding/{A}.md"
    card.write_text(card.read_text() + "\nNegates values now [1].\n", encoding="utf-8")
    report = leaf.write(
        [
            {
                "subject": "onboarding:pkg/b.py",
                "disposition": "no_impact",
                "reason": "test-only rename",
            },
            {
                "subject": "onboarding:pkg/overview",
                "disposition": "no_impact",
                "reason": "The route model is unchanged.",
            },
        ]
    )
    assert report.state == "written", report.render()
    result = _gate(leaf, changed)
    items = _by_subject(result)
    assert result.ok and not result.repair_findings()
    assert items["onboarding:pkg/a.py"].counted and items["onboarding:pkg/a.py"].row is None
    assert items["onboarding:pkg/b.py"].row is not None and not items["onboarding:pkg/b.py"].counted
    assert items["onboarding:pkg/overview"].sources == (A, B)
    assert "onboarding:overview" not in items  # only the nearest governing route is gated
    # A root-level change is governed by the root route.
    root = _gate(leaf, ["top.py"])
    assert {item.subject for item in root.items} == {"onboarding:top.py", "onboarding:overview"}


def test_a_missing_trace_is_one_named_repair_finding_and_the_closeout_refuses(leaf: Leaf) -> None:
    (leaf.code / A).write_text(SOURCE + "\n# tail\n", encoding="utf-8")
    contract = load_contract(leaf.contract())
    sides = leaf_onboarding_trace_sides(contract)
    assert sides is not None
    result, findings = onboarding_trace_gate_for_context(leaf.context(), [A], sides)
    assert [finding["code"] for finding in findings] == [MISSING_CODE, MISSING_CODE]
    card = next(finding for finding in findings if finding["path"] == f"onboarding/{A}.md")
    assert "pkg/a.py" in card["message"] and '"onboarding:pkg/a.py"' in card["message"]
    assert "knowledge/history/260928-MIK-L98.json" in card["message"]
    assert card["itemId"] == result.open_items[0].id
    with pytest.raises(RuntimeError, match="requires an onboarding trace") as refused:
        validate_onboarding_traces_for_context(leaf.context(), [A], sides)
    assert f"onboarding/{A}.md" in str(refused.value)
    assert "onboarding/pkg/overview.md" in str(refused.value)


def test_the_writers_mechanical_anchor_update_is_not_a_trace(leaf: Leaf) -> None:
    # Lines are inserted above Y: C's line-range anchor moves; its content does not.
    (leaf.code / C).write_text("# lead\n\n\nY = 2\n", encoding="utf-8")
    before = (leaf.memory / f"onboarding/{C}.json").read_bytes()
    report = leaf.write([])
    assert report.state == "written", report.render()
    assert report.carried == ("RLZ-A00003",)  # the writer's carry: the realization's blob, lines
    fixed = fix_references(leaf.memory, leaf.code)  # the fixer's: the reference's blob, lines
    assert fixed["refreshedAnchors"] == 1, fixed
    after = json.loads((leaf.memory / f"onboarding/{C}.json").read_text())
    assert after != json.loads(before)
    assert after["realizes"][0]["anchor"]["locator"] == {"kind": "line_range", "start": 4, "end": 4}
    item = _by_subject(_gate(leaf, [C]))["onboarding:pkg/c.py"]
    assert item.open and not item.counted  # mechanical updates never count (R30-F1)


def test_rows_are_only_about_changed_files_and_the_registry_rule_is_both_halves(
    leaf: Leaf,
) -> None:
    (leaf.code / A).write_text(SOURCE + "\n# tail\n", encoding="utf-8")
    report = leaf.write(
        [
            {"subject": "onboarding:pkg/a.py", "disposition": "no_impact", "reason": "Comment."},
            {"subject": "onboarding:pkg/overview", "disposition": "no_impact", "reason": "Same."},
            {"subject": "onboarding:pkg/b.py", "disposition": "no_impact", "reason": "Unneeded."},
        ]
    )
    assert report.state == "written", report.render()
    result = _gate(leaf, [A])
    assert result.ok
    (unneeded,) = result.report_only_findings()
    assert "onboarding:pkg/b.py" in unneeded["message"] and "not an error" in unneeded["message"]
    # The registered kind (MIK-R08 rule 1): four declarations, and its two satisfying halves.
    kind = ITEM_KINDS["onboarding_trace"]
    assert kind.owner == "MIK-R30" and kind.accepts_subject("onboarding:pkg/overview")
    history_text = (leaf.memory / f"knowledge/history/{LEAF}.json").read_text()
    history = HistoryFile.model_validate(json.loads(history_text))
    assert satisfying_row("onboarding_trace", "onboarding:pkg/a.py", history) is not None
    assert satisfying_row("onboarding_trace", "onboarding:pkg/c.py", history) is None
    counted = {"subject": "onboarding:pkg/c.py", "facts": {"countedChange": True}}
    assert not onboarding_item_open(counted, {})
    assert onboarding_item_open({**counted, "facts": {"countedChange": False}}, {})
    # The writer refuses fields an onboarding row does not have, and dispositions it does not.
    refused = leaf.write(
        [{"subject": "onboarding:pkg/c.py", "disposition": "changed", "reason": "No."}]
    )
    assert refused.state == "refused"


def test_a_moved_marker_row_satisfies_its_item_and_survives_a_rewrite(leaf: Leaf) -> None:
    """Rule 4: an open leaf's Update History markers, moved by MIK-R24 rule 8 step 1, count."""

    (leaf.code / A).write_text(SOURCE + "\n# tail\n", encoding="utf-8")
    marker = "- 2026-09-20T10:00+02:00 — reviewed. No content impact: a comment only."
    history = leaf.memory / f"knowledge/history/{LEAF}.json"
    history.parent.mkdir(parents=True)
    history.write_text(
        canonical_text(
            {
                "schema": "ar-history/v1",
                "leaf": LEAF,
                "closed": False,
                "rows": [
                    {
                        "id": "ROW-AAAAAA",
                        "subject": "onboarding:pkg/a.py",
                        "disposition": "no_impact",
                        "reason": "Moved by the crossing sync.",
                        "items": [],
                        "markers": [marker],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert _by_subject(_gate(leaf, [A]))["onboarding:pkg/a.py"].row == "ROW-AAAAAA"
    report = leaf.write(
        [{"subject": "onboarding:pkg/a.py", "disposition": "no_impact", "reason": "Re-reviewed."}]
    )
    assert report.state == "written", report.render()
    (row,) = json.loads(history.read_text())["rows"]
    assert row["id"] == "ROW-AAAAAA" and row["markers"] == [marker]
    assert row["reason"] == "Re-reviewed."


def test_mixed_formats_are_an_incomplete_side_never_a_vacuous_pass(leaf: Leaf) -> None:
    """N1: K_B converted with K_C unconverted, or a marker without a version, refuses."""

    (leaf.code / A).write_text(SOURCE + "\n# tail\n", encoding="utf-8")
    marker = leaf.memory / "knowledge/layout.json"

    def refused_and_incomplete(reason: str) -> None:
        contract = load_contract(leaf.contract())
        sides = leaf_onboarding_trace_sides(contract)
        assert sides is not None and reason in str(sides.incomplete)
        with pytest.raises(RuntimeError, match=reason) as refusal:
            validate_onboarding_traces_for_context(leaf.context(), [A], sides)
        assert ": :" not in str(refusal.value)
        found = recompute_leaf_worklist(contract)
        assert found is not None and found[1] is not None
        persisted = json.loads(Path(found[1]).read_text(encoding="utf-8"))
        assert persisted["state"] == "incomplete" and persisted["items"] == []

    marker.unlink()  # K_C unconverted; K_B (the official line) is converted
    refused_and_incomplete("K_C is unconverted")
    git(leaf.memory, "rm", "-q", "--cached", "knowledge/layout.json")
    leaf.memory_base = commit(leaf.memory, {}, trailer=leaf.code_base)  # K_B unconverted now
    marker.write_text(canonical_text({"schema": "ar-memory-layout/v2"}), encoding="utf-8")
    refused_and_incomplete("without a pinned conversion-format version")


def test_an_unreadable_sidecar_never_satisfies_a_trace(leaf: Leaf) -> None:
    """N5, R2-1: the item stays open, named, whatever else changed; a row does not satisfy it
    either, and the stored item's predicate agrees with the live gate."""

    (leaf.code / C).write_text("\n\nY = 3\n", encoding="utf-8")
    report = leaf.write(
        [{"subject": "onboarding:pkg/c.py", "disposition": "no_impact", "reason": "Unchanged."}]
    )
    assert report.state == "written", report.render()
    sidecar = leaf.memory / f"onboarding/{C}.json"
    before = sidecar.read_bytes()
    assert not counted_sidecar_change(before, b"{not json")
    sidecar.write_bytes(b"{not json")
    card = leaf.memory / f"onboarding/{C}.md"
    card.write_text(card.read_text() + "\nY is three now [1].\n", encoding="utf-8")
    result = _gate(leaf, [C])
    item = _by_subject(result)["onboarding:pkg/c.py"]
    assert item.open and item.unreadable and item.row is not None  # Markdown edit and row: open
    document = item.to_document()
    assert document["facts"]["sidecarUnreadable"] == f"onboarding/{C}.json in K_C"
    assert document["facts"]["countedChange"] is False and document["satisfiedBy"] is None
    assert onboarding_item_open(document, {item.subject: item.row})  # the row case
    assert onboarding_item_open(document, {})  # the Markdown-edit case
    (finding,) = [one for one in result.repair_findings() if one["path"] == f"onboarding/{C}.md"]
    assert "sidecar unreadable" in finding["message"]


def test_an_unreadable_base_sidecar_is_an_incomplete_input_and_a_repair_counts(
    leaf: Leaf,
) -> None:
    """R2-2: a defect in the fixed base commit is a named input problem, not an open item."""

    sidecar = leaf.memory / f"onboarding/{C}.json"
    good = sidecar.read_bytes()
    assert counted_sidecar_change(b"{not json", good)  # a readable repair counts as a change
    leaf.memory_base = commit(
        leaf.memory, {f"onboarding/{C}.json": "{not json"}, trailer=leaf.code_base
    )
    sidecar.write_bytes(good)  # the leaf repairs it in K_C
    (leaf.code / C).write_text("\n\nY = 3\n", encoding="utf-8")
    result = _gate(leaf, [C])
    item = _by_subject(result)["onboarding:pkg/c.py"]
    assert not item.unreadable and item.counted
    assert not result.ok
    (problem,) = [one for one in result.repair_findings() if one["path"] == f"onboarding/{C}.json"]
    assert problem["code"] == "onboarding-trace-base-sidecar-unreadable"
    assert "K_B's sidecar cannot be parsed" in problem["message"]


def test_unreadable_history_and_unestablished_sides_are_findings_never_a_pass(
    leaf: Leaf,
) -> None:
    (leaf.code / A).write_text(SOURCE + "\n# tail\n", encoding="utf-8")
    history = leaf.memory / f"knowledge/history/{LEAF}.json"
    history.parent.mkdir(parents=True)
    history.write_text("{not json", encoding="utf-8")
    result = _gate(leaf, [A])
    assert not result.ok and result.problems[0][1] == f"knowledge/history/{LEAF}.json"
    incomplete = onboarding_trace_result(
        leaf.context(), [A], OnboardingTraceSides(owner=LEAF, incomplete="pairing: none")
    )
    assert not incomplete.ok and "pairing: none" in incomplete.repair_findings()[0]["message"]
    # A base no official memory commit pairs with (MIK-R07 rule 0) is an incomplete side, named.
    git(leaf.code, "checkout", "-q", "--orphan", "elsewhere")
    stranger = commit(leaf.code, {"new.py": "N = 1\n"})
    leaf.code_base = stranger
    sides = leaf_onboarding_trace_sides(load_contract(leaf.contract()))
    assert sides is not None and sides.incomplete is not None and stranger in sides.incomplete
    (finding,) = onboarding_trace_result(leaf.context(), [A], sides).repair_findings()
    assert finding["code"] == "onboarding-trace-incomplete"


# --------------------------------------------------------------------------------------------------
# The converted base (MIK-R24 rule 7) and the unconverted boundary
# --------------------------------------------------------------------------------------------------


def _legacy_card(path: str, verified: str, prose: str) -> str:
    return (
        f"# {path}\n\n| Field | Value |\n| --- | --- |\n| path | `{path}` |\n"
        "| doc_type | `file-level-onboarding` |\n| lastUpdated | 2026-09-01T00:00 |\n"
        f"| lastVerifiedCommitHash | `{verified}` |\n"
        "| lastVerifiedCommitDate | 2026-09-01T00:00:00+00:00 |\n"
        "| governingOverview | `../overview.md` |\n\n"
        f"## Purpose\n\n{prose}\n\n## Update History\n\n- 2026-09-01T00:00+00:00 — created.\n"
    )


def test_the_conversion_itself_counts_for_nothing_at_the_converting_leaf(tmp_path: Path) -> None:
    code, memory = tmp_path / "code", tmp_path / "memory"
    _init(code)
    head = commit(code, {A: SOURCE})
    _init(memory)
    legacy = commit(
        memory,
        {
            "onboarding/overview.md": "# root\n\nThe repository root route.\n",
            "onboarding/pkg/overview.md": "# pkg\n\n## Route Model\n\nThe package route.\n",
            f"onboarding/{A}.md": _legacy_card(A, head, "Lands values."),
        },
    )
    outcome = convert_memory(memory_from_directory(memory), CodeObjects(code), paired_commit=head)
    write_changed(memory, outcome.changed)  # the leaf that converts (MIK-R37's standalone run)
    context = SimpleNamespace(
        storage=StorageSettings(),
        code_repository_name="demo",
        onboarding_root=memory / "onboarding",
    )
    cache = tmp_path / "coordination" / "runtime" / "knowledge-worklist-bases"
    request = TraceSideRequest("260101-FIX-L3", memory, legacy, memory, code, head, cache)

    def items() -> dict[str, Any]:
        sides = onboarding_trace_sides(request)
        assert sides is not None and sides.pairing["convertedBase"] is True
        return _by_subject(onboarding_trace_result(context, [A], sides))

    before = items()
    (cached,) = cache.glob("*.json.gz")  # the worklist's cache now holds the gate's Markdown too
    held = json.loads(gzip.decompress(cached.read_bytes()))
    assert held["format"] == "knowledge-worklist-base/v2" and f"onboarding/{A}.md" in held["files"]
    with mock.patch.object(base_cache, "converted_base", side_effect=AssertionError("reconvert")):
        assert items() == before  # the second run reads the cache
    # A v1 file at the same key (JSON only, no Markdown) is ignored and rewritten as v2 (N3): read
    # as a base, it would make every card look new and the gate pass vacuously.
    key = held["key"]
    v1 = {"format": "knowledge-worklist-base/v1", "key": key, "files": {}}
    cached.write_bytes(gzip.compress(json.dumps(v1).encode()))
    assert items() == before and before[f"onboarding:{A}"].open
    assert (
        json.loads(gzip.decompress(cached.read_bytes()))["format"] == "knowledge-worklist-base/v2"
    )
    assert (memory / f"onboarding/{A}.md").read_text() != _legacy_card(A, head, "Lands values.")
    assert before[f"onboarding:{A}"].open  # the mechanical conversion is not a trace
    assert before["onboarding:pkg/overview"].open
    card = memory / f"onboarding/{A}.md"
    card.write_text(card.read_text() + "\nA reviewed sentence.\n", encoding="utf-8")
    assert items()[f"onboarding:{A}"].counted
    # An unconverted K_B and K_C: no sides, today's gate applies unchanged.
    git(memory, "checkout", "-q", "--", ".")
    git(memory, "clean", "-fdq")  # back to the committed, unconverted tree
    assert onboarding_trace_sides(request) is None


def test_converted_trees_get_no_verification_stamps_and_no_history_sort(leaf: Leaf) -> None:
    change = VerifiedChange(
        commit="c" * 40,
        commit_date="2026-09-29T00:00:00+00:00",
        changed_paths=[A],
        working_paths=[A],
    )
    card = leaf.memory / f"onboarding/{A}.md"
    before = card.read_bytes()
    assert refresh_onboarding_metadata_for_context(leaf.context(), change) == []
    assert refresh_route_overview_metadata_for_context(leaf.context(), change) == []
    assert card.read_bytes() == before
    fixed = fix_onboarding_root(leaf.memory / "onboarding")
    assert fixed["status"] == "not-applicable-converted" and fixed["filesChecked"] == 0


# --------------------------------------------------------------------------------------------------
# Enforcement where the gate runs today: the curator's memory-quality run
# --------------------------------------------------------------------------------------------------


def _run_controller(leaf: Leaf, changed: tuple[str, ...]) -> tuple[dict[str, Any], mock.Mock]:
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
        context=leaf.context(),  # type: ignore[arg-type]
        curator_report_path=leaf.root / "group" / "reports" / "curator-memory-quality.md",
        contract=contract,
        pair_identity=pair,
    )
    execution = controller.MemoryQualityExecution(
        config=mock.Mock(), scope=scope, checks=(), detail_limit=50, publish_curator_report=True
    )
    census = SimpleNamespace(
        scope=SimpleNamespace(pair_identity=pair, working_paths=changed, committed_paths=()),
        result=SimpleNamespace(rows=[], blockers=[]),
    )
    legacy = mock.Mock()
    with (
        mock.patch.object(controller, "revalidate_memory_candidate_scope", return_value=scope),
        mock.patch.object(
            controller,
            "_curator_candidate_inputs",
            return_value=controller._CuratorCandidateInputs("a" * 40, "b" * 40),
        ),
        mock.patch.object(controller, "prepare_memory_census", return_value=census),
        mock.patch.object(controller, "publish_memory_census", return_value={}),
        mock.patch.object(controller, "census_curator_candidates", return_value=()),
        mock.patch.object(
            controller,
            "run_memory_quality_check",
            return_value={"ok": True, "checks": {}, "findings": [], "findingCount": 0},
        ),
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
        mock.patch.object(controller, "validate_memory_refresh_attestations", legacy),
        mock.patch.object(controller, "_without_proof", return_value=None),
        mock.patch.object(controller, "recompute_leaf_worklist", return_value=None),
        mock.patch.object(controller, "_attach_coherence_readiness"),
        mock.patch.object(controller, "_attach_final_full_catalog"),
    ):
        response = controller._execute_memory_quality(execution)
    return response, legacy


def test_the_memory_quality_run_counts_each_missing_trace_toward_the_actionable_count(
    leaf: Leaf,
) -> None:
    (leaf.code / A).write_text(SOURCE + "\n# tail\n", encoding="utf-8")
    response, legacy = _run_controller(leaf, (A,))
    legacy.assert_not_called()  # a converted tree never runs the Update History gate
    assert response["curatorActionableCount"] == 2  # the card and its governing route
    assert response["onboardingTrace"]["open"] == ["onboarding:pkg/a.py", "onboarding:pkg/overview"]
    rendered = (leaf.root / "group" / "reports" / "curator-memory-quality.md").read_text()
    assert MISSING_CODE in rendered
    leaf.write(
        [
            {"subject": "onboarding:pkg/a.py", "disposition": "no_impact", "reason": "Comment."},
            {"subject": "onboarding:pkg/overview", "disposition": "no_impact", "reason": "Same."},
        ]
    )
    response, _ = _run_controller(leaf, (A,))
    assert response["curatorActionableCount"] == 0


def test_the_persisted_worklist_carries_the_onboarding_items_in_its_one_list(leaf: Leaf) -> None:
    """Ruling Q2: the gate's items, facts and satisfaction are in ``knowledge-worklist.json``."""

    (leaf.code / B).write_text("X = 10\n", encoding="utf-8")
    (leaf.code / C).write_text("\n\nY = 3\n", encoding="utf-8")  # also touches INV-AAAAAA
    contract = load_contract(leaf.contract())
    found = recompute_leaf_worklist(contract)
    assert found is not None and found[1] is not None
    persisted = json.loads(Path(found[1]).read_text(encoding="utf-8"))
    order = [(item["kind"], item["subject"]) for item in persisted["items"]]
    assert order == sorted(order) and ("touched_invariant", "INV-AAAAAA") in order  # N2
    traces = {item["subject"]: item for item in persisted["items"] if item["kind"] == ITEM_KIND}
    assert set(traces) == {"onboarding:pkg/b.py", "onboarding:pkg/c.py", "onboarding:pkg/overview"}
    card = traces["onboarding:pkg/b.py"]
    assert card["facts"]["sources"] == [B] and card["satisfiedBy"] is None
    assert card["id"] == _by_subject(_gate(leaf, [B]))["onboarding:pkg/b.py"].id
    assert "onboarding_trace" in {kind["name"] for kind in persisted["kinds"]}
    leaf.write([{"subject": "onboarding:pkg/b.py", "disposition": "no_impact", "reason": "Same."}])
    again = recompute_leaf_worklist(contract)
    assert again is not None
    rewritten = {item["subject"]: item for item in again[0]["items"]}["onboarding:pkg/b.py"]
    assert rewritten["satisfiedBy"].startswith("ROW-")
    assert again[0]["digest"] != persisted["digest"]  # the digest covers the gate's items


def test_an_unconverted_leaf_keeps_todays_gate_unchanged(leaf: Leaf) -> None:
    git(leaf.memory, "rm", "-q", "knowledge/layout.json")
    leaf.memory_base = commit(leaf.memory, {}, trailer=leaf.code_base)
    assert leaf_onboarding_trace_sides(load_contract(leaf.contract())) is None
    assert recompute_leaf_worklist(load_contract(leaf.contract())) is None  # still no worklist
    response, legacy = _run_controller(leaf, (A,))
    legacy.assert_called_once()  # today's Update History gate, with today's arguments
    assert "onboardingTrace" not in response
