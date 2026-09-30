"""MIK-R25: the reviewer on Git trees -- four trees, pins, the tree view, reopen, legacy, archive.

The fixture is a coordination root holding one task and one leaf enclosure, a code repository and a
converted memory repository (``main`` is the official line of both), and the leaf's two linked
worktrees on their work branches. The leaf edits code and knowledge without committing, which is
the live review's ordinary state.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import apsw
import pytest
from agents_remember.application import review_tree_comparison
from agents_remember.application.knowledge_review import (
    compose_review,
    list_knowledge_review_entries,
)
from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.application.review_artifact_cleanup import (
    cleanup_review_artifacts,
)
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_comparison_freeze import (
    ComparisonGenerationRequest,
    freeze_comparison_generation,
)
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review import ReviewRefusal, ReviewSurfaceRequest
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.serving.review_trees import ReviewTreesQuery, register_review_trees_route
from agents_remember.worktrees.services import (
    ReviewArtifactCleanupRequest,
    reset_worktree_services,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO = "agents-remember"
MASTER = "260101_tree_review"
TASK = "260101-TRV"
LEAF = "260101-TRV-L1"
INVARIANT = "INV-AAAAAA"
FAMILY = "FAM-F00001"
CODE_FILE = "pkg/a.py"
CODE_V1 = "def land(value):\n    return value\n\n\ndef keep():\n    return 1\n"
ORIGIN = {"task": "260101-OLD", "leaf": "260101-OLD-L1"}
INVARIANT_PATH = f"knowledge/invariants/{INVARIANT}-land.json"


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


def invariant(revision: int = 1, statement: str = "Values land unchanged.") -> str:
    return canonical_text(
        {
            "schema": "ar-invariant/v1",
            "id": INVARIANT,
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
    tree = git(code, "rev-parse", f"{code_commit}^{{tree}}")
    trees = CodeTrees.open(code, tree, tree)
    blob = trees.base()[CODE_FILE]

    def realization(entry_id: str, name: str) -> dict[str, Any]:
        locator = {"kind": "symbol", "name": name}
        resolved = trees.resolve(CODE_FILE, locator, blob, blob)
        assert resolved is not None
        return {
            "id": entry_id,
            "invariant": INVARIANT,
            "anchor": {"locator": locator, "blob": blob, "content": resolved.content},
            "role": "primary-authority",
            "rationale": "It is the rule.",
        }

    return {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
        INVARIANT_PATH: invariant(),
        f"knowledge/families/{FAMILY}-landing.json": canonical_text(
            {
                "schema": "ar-family/v1",
                "id": FAMILY,
                "revision": 1,
                "status": "accepted",
                "title": "Landing",
                "guarantee": "Landing holds.",
                "members": [INVARIANT],
                "routes": ["pkg"],
                "admission": "legacy-unassessed",
                "origin": ORIGIN,
            }
        ),
        f"onboarding/{CODE_FILE}.md": "# a\n",
        f"onboarding/{CODE_FILE}.json": canonical_text(
            {
                "schema": "ar-onboarding-file/v1",
                "path": CODE_FILE,
                "references": {},
                "realizes": [realization("RLZ-A00001", "land"), realization("RLZ-A00002", "keep")],
            }
        ),
        "onboarding/overview.md": "# root\n",
    }


def history_file() -> str:
    return canonical_text(
        {
            "schema": "ar-history/v1",
            "leaf": LEAF,
            "closed": False,
            "rows": [
                {
                    "id": "ROW-9Q7K3F",
                    "subject": FAMILY,
                    "disposition": "no_impact",
                    "reason": "every member still holds together",
                    "items": [],
                    "examined": [{"id": INVARIANT, "revision": 2}],
                }
            ],
        }
    )


@dataclass
class World:
    root: Path
    config: McpRuntimeConfig
    code: Path
    memory: Path
    code_worktree: Path
    memory_worktree: Path
    task_root: Path
    code_base: str
    memory_base: str

    def contract(self, *, live: bool = True, **recorded: str) -> Path:
        enclosure = self.task_root / "enclosures" / LEAF.lower()
        enclosure.mkdir(parents=True, exist_ok=True)
        path = enclosure / "series-contract.md"
        code_worktree = self.code_worktree if live else self.root / "removed-code"
        memory_worktree = self.memory_worktree if live else self.root / "removed-memory"
        lines = [
            "---",
            "schema: ar-series-contract/v1",
            "schemaVersion: 1.0",
            "kind: leaf",
            "task_id: 260101_TREE-REVIEW",
            f"task_name: {MASTER}",
            f"repo_name: {REPO}",
            "workflow_kind: light-task",
            "memory_mode: external",
            "",
            "coordination:",
            f"  root: {self.config.coordination_root}",
            f"  task_root: {self.task_root}",
            f"  task_artifact: {self.task_root / 'task.md'}",
            f"  worktree_group: {self.root / 'group'}",
            f"  leaf_id: {LEAF}",
            f"  parent_task_name: {MASTER}",
            "",
            "code:",
            f"  repo_path: {self.code}",
            "  source_branch: main",
            "  work_branch: leaf",
            f"  base_commit: {self.code_base}",
            f"  worktree: {code_worktree}",
            "",
            "memory:",
            "  mode: external",
            f"  repo_path: {self.memory}",
            "  source_branch: main",
            "  work_branch: leaf",
            f"  base_commit: {self.memory_base}",
            f"  worktree: {memory_worktree}",
            f"  ledger: {memory_worktree / 'memory.md'}",
            "",
            "closeout:",
            "  status: not-started",
        ]
        if recorded:
            lines += [f"  {key}: {value}" for key, value in recorded.items()]
        path.write_text("\n".join([*lines, "---", ""]), encoding="utf-8")
        return path

    def edit(self) -> None:
        """The leaf's uncommitted change: a body edit of ``land`` and a revised statement."""

        (self.code_worktree / CODE_FILE).write_text(
            CODE_V1.replace("return value", "return value + 0"), encoding="utf-8"
        )
        _write(
            self.memory_worktree,
            {
                INVARIANT_PATH: invariant(2, "Values land exactly as given."),
                f"knowledge/history/{LEAF}.json": history_file(),
            },
        )

    def review(self, **fields: Any) -> ReviewSurfaceRequest:
        return ReviewSurfaceRequest(repository_id=REPO, master=MASTER, leaf_id=LEAF, **fields)


def _repository(root: Path) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "tree review fixture")
    return root


@pytest.fixture
def world(tmp_path: Path) -> World:
    code = _repository(tmp_path / "repos" / "code")
    code_base = commit(code, {CODE_FILE: CODE_V1})
    memory = _repository(tmp_path / "repos" / "memory")
    memory_base = commit(memory, converted_memory(code, code_base), trailer=code_base)
    code_worktree, memory_worktree = tmp_path / "group" / "code", tmp_path / "group" / "memory"
    git(code, "worktree", "add", "-q", "-b", "leaf", str(code_worktree))
    git(memory, "worktree", "add", "-q", "-b", "leaf", str(memory_worktree))
    coordination = tmp_path / "coordination"
    task_root = coordination / "tasks" / REPO / MASTER
    task_root.mkdir(parents=True)
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    (task_root / "task.json").write_text(json.dumps({"id": TASK}), encoding="utf-8")
    config = McpRuntimeConfig(
        workspace_root=tmp_path,
        coordination_root=coordination,
        config_path=tmp_path / "config.json",
        transcript_root=tmp_path / "transcripts",
    )
    made = World(
        tmp_path,
        config,
        code,
        memory,
        code_worktree,
        memory_worktree,
        task_root,
        code_base,
        memory_base,
    )
    made.contract()
    return made


def _resolve(world: World, *, recorded: bool = False) -> ReviewCandidateResolution:
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF, recorded=recorded)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    return resolved


def _refs(repository: Path) -> dict[str, str]:
    listed = git(repository, "for-each-ref", "--format=%(refname) %(objectname)", "refs/ar/")
    return dict(line.split(" ", 1) for line in listed.splitlines() if line)


def _has_object(repository: Path, object_id: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", object_id], cwd=repository, capture_output=True, check=False
        ).returncode
        == 0
    )


def _query(**fields: Any) -> ReviewTreesQuery:
    return ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF, **fields)


def _seed() -> InvariantIdentitySeed:
    return InvariantIdentitySeed(invariant_id=text_uuid("identity", INVARIANT))


# -- rule 1: the comparison ------------------------------------------------------------------------


def test_a_live_review_is_four_trees_with_its_uncommitted_candidates_pinned_and_reused(
    world: World,
) -> None:
    world.edit()
    resolved = _resolve(world)
    trees = resolved.trees
    assert trees is not None and trees.record_path is not None
    record = trees.record
    ref = f"refs/ar/review/{MASTER}/{LEAF}/1"
    assert record.number == 1 and record.task_id == MASTER
    # Committed sides name their commits and need no ref; the two uncommitted candidates are pinned.
    assert record.code_base.commit == world.code_base and record.code_base.ref is None
    assert record.memory_base.commit == world.memory_base and record.memory_base.ref is None
    assert record.code_candidate.ref == ref and record.memory_candidate.ref == ref
    assert _refs(world.code) == {ref: record.code_candidate.tree}
    assert _refs(world.memory) == {ref: record.memory_candidate.tree}
    assert git(world.memory, "cat-file", "-t", ref) == "tree"
    assert json.loads(trees.record_path.read_text())["schema"] == "ar-review-tree-comparison/v1"
    # Each memory side is read through the index of its own tree -- never a dataset copy.
    cache = world.config.coordination_root / "runtime" / "knowledge-index"
    assert resolved.baseline_database.parent == cache
    assert resolved.candidate_database == cache / f"{record.memory_candidate.tree}.sqlite"
    # The landed composition reads the index unchanged (rule 6), and declares the tree comparison.
    review = compose_review(resolved, world.review(selector=_seed()))
    assert review.state == "review" and review.payload is not None, review.refusal
    knowledge = review.payload.knowledge
    assert knowledge.before_statement.text == "Values land unchanged."
    assert knowledge.after_statement.text == "Values land exactly as given."
    assert "review:trees:1" in review.payload.limitations
    assert "knowledge-index:after:complete" in review.payload.limitations
    entries = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert entries.state == "entries" and {e.label for e in entries.entries} == {
        INVARIANT,
        "Landing",
    }
    # Reading again reuses the record, re-creating a pin that has gone; a new edit is a new
    # comparison with its own pins.
    git(world.memory, "update-ref", "-d", ref)
    assert _resolve(world).trees.record.number == 1  # type: ignore[union-attr]
    assert _refs(world.memory) == {ref: record.memory_candidate.tree}
    (world.code_worktree / "pkg" / "b.py").write_text("B = 1\n", encoding="utf-8")
    second = _resolve(world).trees
    assert second is not None and second.record.number == 2
    assert set(_refs(world.code)) == {ref, f"refs/ar/review/{MASTER}/{LEAF}/2"}
    # The memory candidate did not change, so its pin at 2 names the same tree as at 1.
    assert _refs(world.memory)[f"refs/ar/review/{MASTER}/{LEAF}/2"] == record.memory_candidate.tree


def _state(world: World) -> dict[str, object]:
    """Every ref, every object and every recorded comparison file, byte for byte."""

    def objects(repository: Path) -> list[str]:
        listed = git(repository, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
        return sorted(listed.split())

    records = world.task_root / "notes" / "reports" / "review-comparisons"
    return {
        "refs": {repo.name: git(repo, "for-each-ref") for repo in (world.code, world.memory)},
        "objects": {repo.name: objects(repo) for repo in (world.code, world.memory)},
        "records": {
            str(path): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in sorted(records.rglob("*.json"))
        },
    }


def test_a_read_writes_only_review_refs_and_comparison_objects_and_a_repeat_writes_nothing(
    world: World,
) -> None:
    world.edit()
    before = _state(world)
    view = read_review_trees(world.config, _query())
    assert view.state == "trees" and view.comparison is not None
    first = _state(world)
    # The only refs the read wrote are the review pins; no branch, HEAD or other ref moved.
    for repo in ("code", "memory"):
        old = set(before["refs"][repo].splitlines())  # type: ignore[index]
        new = set(first["refs"][repo].splitlines())  # type: ignore[index]
        assert old <= new and all("refs/ar/review/" in line for line in new - old)
    # The objects it added are the comparison's own: the two captured candidate trees and theirs.
    added = set(first["objects"]["memory"]) - set(before["objects"]["memory"])  # type: ignore[index]
    assert view.comparison.memory_candidate.tree in added
    # Reading again -- the tree view, the review, the entry list -- writes nothing new at all.
    read_review_trees(world.config, _query())
    compose_review(_resolve(world), world.review(selector=_seed()))
    list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert _state(world) == first


def test_committed_candidates_need_no_ref_and_a_failed_pin_refuses_naming_repository_and_ref(
    world: World,
) -> None:
    # Nothing edited: both candidates are the durable line's own trees, so nothing is pinned.
    committed = _resolve(world).trees
    assert committed is not None
    assert committed.record.code_candidate.commit == world.code_base
    assert committed.record.memory_candidate.commit == world.memory_base
    assert _refs(world.code) == {} and _refs(world.memory) == {}
    world.edit()
    real = review_tree_comparison.run_git

    def refusing(repository: Path, args: list[str], *rest: Any) -> Any:
        if args[0] == "update-ref" and repository == world.memory:
            return subprocess.CompletedProcess(args, 1, "", "cannot lock ref")
        return real(repository, args, *rest)

    with mock.patch.object(review_tree_comparison, "run_git", refusing):
        refused = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(refused, ReviewRefusal)
    assert refused.offending_input == f"refs/ar/review/{MASTER}/{LEAF}/2"
    assert str(world.memory) in refused.detail and "not published" in refused.detail
    # The code pin this attempt made was removed again, and nothing was recorded.
    assert _refs(world.code) == {} and _refs(world.memory) == {}
    assert len(list(committed.record_path.parent.glob("*.json"))) == 1  # type: ignore[union-attr]


# -- rules 2 and 3: the tree view ----------------------------------------------------------------------


def test_the_tree_view_shows_the_knowledge_diff_currentness_per_side_and_the_worklist(
    world: World,
) -> None:
    world.edit()
    view = read_review_trees(world.config, _query())
    assert view.state == "trees" and view.comparison is not None and view.knowledge_diff is not None
    diff = view.knowledge_diff
    record = next(group for group in diff.records if group.record_id == INVARIANT)
    assert record.kind == "invariant" and record.files[0].path == INVARIANT_PATH
    assert '+  "statement": "Values land exactly as given."' in record.files[0].patch
    assert [change.path for change in diff.history] == [f"knowledge/history/{LEAF}.json"]
    assert [side.index_state for side in view.knowledge_sides] == ["complete", "complete"]
    # Currentness per side, each against its own code tree: the body edit makes the after side stale.
    assert view.currentness is not None
    before, after = view.currentness["before"], view.currentness["after"]
    assert before["codeTree"]["treeId"] == view.comparison.code_base.tree
    assert {one["id"]: one["state"] for one in before["invariants"]} == {INVARIANT: "current"}
    assert {one["id"]: one["state"] for one in after["invariants"]} == {INVARIANT: "stale"}
    assert after["families"] == [
        {"id": FAMILY, "members": 1, "staleMembers": 1, "stale": [INVARIANT]}
    ]
    # The worklist view: the items, the history rows about their subjects, the gate linkage.
    worklist = view.worklist
    assert worklist is not None and worklist.source == "computed" and worklist.bound
    kinds = {(item["kind"], item["subject"]) for item in worklist.items}
    assert ("touched_invariant", INVARIANT) in kinds and ("reached_family", FAMILY) in kinds
    assert [(row["subject"], row["disposition"]) for row in worklist.history_rows] == [
        (FAMILY, "no_impact")
    ]
    assert "current" not in worklist.history_rows[0] and "stale" not in worklist.history_rows[0]
    change = next(one for one in worklist.changes if one["path"] == CODE_FILE)
    assert [hunk["linked"] for hunk in change["hunks"]] == [True]
    # A sidecar change is grouped by its source path and under the record its entry realizes.
    _write(world.memory_worktree, {f"onboarding/{CODE_FILE}.json": _without_keep(world)})
    regrouped = read_review_trees(world.config, _query()).knowledge_diff
    assert regrouped is not None
    source = next(group for group in regrouped.sources if group.source_path == CODE_FILE)
    assert source.records == (INVARIANT,)
    assert ("RLZ-A00002", CODE_FILE, "removed") in next(
        group for group in regrouped.records if group.record_id == INVARIANT
    ).entries


def _without_keep(world: World) -> str:
    sidecar = json.loads((world.memory_worktree / f"onboarding/{CODE_FILE}.json").read_text())
    sidecar["realizes"] = [one for one in sidecar["realizes"] if one["id"] != "RLZ-A00002"]
    return canonical_text(sidecar)


def test_a_partial_index_shows_its_state_on_the_affected_side(world: World) -> None:
    world.edit()
    _write(world.memory_worktree, {"knowledge/invariants/INV-BBBBBB-broken.json": "{}\n"})
    resolved = _resolve(world)
    assert resolved.trees is not None
    after = resolved.trees.after.wire
    assert after.index_state == "partial"
    assert [path for path, _ in after.problems] == ["knowledge/invariants/INV-BBBBBB-broken.json"]
    assert resolved.trees.before.wire.index_state == "complete"
    review = compose_review(resolved, world.review(selector=_seed()))
    assert review.payload is not None
    assert "knowledge-index:after:partial" in review.payload.limitations


# -- rule 4: reopen, the converted base and legacy comparisons ---------------------------------------


def test_a_comparison_reopens_from_its_tree_ids_and_names_a_tree_git_can_no_longer_produce(
    world: World,
) -> None:
    world.edit()
    live = _resolve(world).trees
    assert live is not None
    world.contract(live=False)  # the enclosure closes; the record is what remains
    reopened = _resolve(world, recorded=True)
    assert reopened.trees is not None and reopened.trees.record == live.record
    review = compose_review(reopened, world.review(selector=_seed()))
    assert review.payload is not None
    assert review.payload.knowledge.after_statement.text == "Values land exactly as given."
    # The pins go and Git collects the candidate tree: the side is unavailable-history, named.
    for repository in (world.code, world.memory):
        for ref in _refs(repository):
            git(repository, "update-ref", "-d", ref)
    git(world.memory, "worktree", "remove", "--force", str(world.memory_worktree))
    git(world.memory, "reflog", "expire", "--expire=now", "--all")
    git(world.memory, "gc", "-q", "--prune=now")
    lost = _resolve(world, recorded=True)
    assert lost.trees is not None
    after = lost.trees.after.wire
    tree = live.record.memory_candidate.tree
    assert after.state == "unavailable-history" and tree in (after.detail or "")
    assert lost.trees.before.wire.state == "available"
    refused = compose_review(lost, world.review(selector=_seed()))
    assert refused.state == "refused" and refused.refusal is not None
    assert f"after:unavailable-history:{tree}" in (refused.refusal.offending_input or "")
    # The code sides stay: the task-context review still carries the source inventory.
    context = compose_review(lost, world.review())
    assert context.payload is not None and context.payload.source.inventory.state != "unavailable"
    assert "history:intent:after:unavailable-history" in context.payload.limitations
    assert [side.state for side in lost.trees.code_sides] == ["available", "available"]
    # Git collects the code candidate too: that code side is unavailable-history, named as well.
    git(world.code, "worktree", "remove", "--force", str(world.code_worktree))
    git(world.code, "reflog", "expire", "--expire=now", "--all")
    git(world.code, "gc", "-q", "--prune=now")
    code_lost = _resolve(world, recorded=True).trees
    assert code_lost is not None
    candidate = live.record.code_candidate.tree
    assert [(side.side, side.state) for side in code_lost.code_sides] == [
        ("base", "available"),
        ("candidate", "unavailable-history"),
    ]
    assert candidate in (code_lost.code_sides[1].detail or "")
    view = read_review_trees(world.config, _query(recorded=True))
    assert [side.state for side in view.code_sides] == ["available", "unavailable-history"]
    assert f"history:code:candidate:unavailable-history:{candidate}" in (
        compose_review(_resolve(world, recorded=True), world.review()).payload.limitations  # type: ignore[union-attr]
    )


def test_an_unconverted_before_side_is_compared_as_its_conversion(world: World) -> None:
    # The official line's base is unconverted (legacy onboarding only); the leaf is converted.
    legacy_code = commit(world.code, {"pkg/c.py": "C = 1\n"})
    git(world.memory, "rm", "-q", "-r", "knowledge")
    legacy_memory = commit(world.memory, {"onboarding/legacy.md": "# legacy\n"}, legacy_code)
    world.code_base = legacy_code
    world.contract()
    world.edit()
    converted = {
        **converted_memory(world.code, world.code_base),
        "onboarding/legacy.md": "# legacy\n",
    }
    files = {path: text.encode() for path, text in converted.items()}
    with mock.patch.object(
        review_tree_comparison, "converted_base_files", return_value=files
    ) as convert:
        trees = _resolve(world).trees
        assert trees is not None and convert.call_count == 1
        base = trees.record.converted_base
        assert base is not None and base.commit == legacy_memory and base.version == "1"
        assert trees.before.wire.tree == base.tree != trees.record.memory_base.tree
        # The conversion itself is no change: only the leaf's own edits are in the knowledge diff.
        view = read_review_trees(world.config, _query())
        review = compose_review(_resolve(world), world.review(selector=_seed()))
    assert view.knowledge_diff is not None
    changed = {change.path for group in view.knowledge_diff.records for change in group.files}
    assert changed == {INVARIANT_PATH}
    assert review.payload is not None
    assert "review:converted-base:1" in review.payload.limitations
    assert review.payload.knowledge.before_statement.text == "Values land unchanged."
    # A reopen re-derives a converted base Git no longer holds, and checks its tree id.
    world.contract(live=False)
    git(world.memory, "worktree", "remove", "--force", str(world.memory_worktree))
    git(world.memory, "reflog", "expire", "--expire=now", "--all")
    git(world.memory, "gc", "-q", "--prune=now")
    assert not _has_object(world.memory, base.tree)
    with mock.patch.object(review_tree_comparison, "converted_base_files", return_value=files):
        reopened = _resolve(world, recorded=True).trees
    assert reopened is not None and reopened.before.wire.state == "available"


def test_a_comparison_recorded_before_the_conversion_keeps_its_code_sides_only(
    world: World,
) -> None:
    # The leaf closed on an unconverted memory line; the official line has converted since.
    git(world.memory, "rm", "-q", "-r", "knowledge")
    legacy_head = commit(world.memory, {"onboarding/old.md": "# old\n"})
    landed = commit(world.code, {"pkg/d.py": "D = 1\n"})
    commit(world.memory, converted_memory(world.code, world.code_base), trailer=landed)
    world.contract(live=False, code_commit=landed, memory_content_commit=legacy_head)
    opened: list[str] = []
    real = apsw.Connection

    def recording(path: str, *args: Any, **kwargs: Any) -> apsw.Connection:
        opened.append(path)
        return real(path, *args, **kwargs)

    with mock.patch.object(apsw, "Connection", recording):
        resolved = _resolve(world, recorded=True)
        assert resolved.trees is None
        assert {state for _, state, _ in resolved.knowledge_unavailable} == {"legacy-unavailable"}
        subject = compose_review(resolved, world.review(selector=_seed()))
        context = compose_review(resolved, world.review())
    assert subject.state == "refused" and subject.refusal is not None
    assert "legacy-unavailable" in subject.refusal.detail
    assert context.payload is not None
    assert context.payload.source.inventory.state != "unavailable"
    assert {"history:legacy-comparison", "history:intent:before:legacy-unavailable"} <= set(
        context.payload.limitations
    )
    assert opened == [], "a legacy comparison's knowledge must not be read from any database"


def test_no_review_path_opens_a_database_other_than_the_derived_index(world: World) -> None:
    world.edit()
    cache = world.config.coordination_root / "runtime" / "knowledge-index"
    opened: list[str] = []
    real_apsw, real_sqlite = apsw.Connection, sqlite3.connect

    def recording(path: str, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(path))
        return real_apsw(path, *args, **kwargs)

    def recording_sqlite(path: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(path))
        return real_sqlite(path, *args, **kwargs)

    with (
        mock.patch.object(apsw, "Connection", recording),
        mock.patch.object(sqlite3, "connect", recording_sqlite),
    ):
        resolved = _resolve(world)
        compose_review(resolved, world.review(selector=_seed()))
        compose_review(resolved, world.review())
        list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
        read_review_trees(world.config, _query())
    assert opened, "the review read no knowledge at all"
    assert all(Path(path).parent == cache for path in opened), sorted(set(opened))
    assert not list(world.root.rglob("knowledge.sqlite"))


def test_an_unconverted_leaf_keeps_the_dataset_review(world: World) -> None:
    # Unconvert both lines: the dataset review applies, exactly as before, and nothing is pinned.
    for repository in (world.memory, world.memory_worktree):
        git(repository, "rm", "-q", "-r", "knowledge")
        git(repository, "commit", "-q", "-m", "unconverted")
    world.edit()
    resolved = _resolve(world)
    assert resolved.trees is None
    assert resolved.candidate_database == (
        world.root
        / "group/provider-runtime/dev-ar-coordination/knowledge/candidate"
        / "knowledge-candidate.sqlite"
    )
    assert _refs(world.code) == {} and _refs(world.memory) == {}
    assert read_review_trees(world.config, _query()).state == "not-converted"


def test_a_tree_comparison_is_never_frozen_into_a_dataset_generation(world: World) -> None:
    world.edit()
    resolved = _resolve(world)
    review = compose_review(resolved, world.review())
    assert review.payload is not None
    frozen = freeze_comparison_generation(
        ComparisonGenerationRequest(resolution=resolved, inventory=review.payload.source.inventory)
    )
    assert frozen.state == "refused" and frozen.refusal is not None
    assert "no knowledge dataset is copied" in frozen.refusal.detail
    assert not (world.task_root / "notes" / "reports" / "comparison-generations").exists()


# -- rule 5: pins are named by the task directory (the archive hook: test_review_artifact_cleanup)


def test_pins_are_named_by_the_task_directory_and_archival_removes_them(world: World) -> None:
    # task.json's id (260101-TRV) names no review ref: pins live under the directory name (R5).
    world.edit()
    ref = f"refs/ar/review/{MASTER}/{LEAF}/1"
    assert _resolve(world).trees.record.task_id == MASTER  # type: ignore[union-attr]
    assert set(_refs(world.code)) == {ref} and set(_refs(world.memory)) == {ref}
    report = cleanup_review_artifacts(
        ReviewArtifactCleanupRequest(world.task_root, MASTER, world.code, world.memory, False)
    )
    assert {entry["ref"] for entry in report["reviewRefs"]} == {ref}
    assert _refs(world.code) == {} and _refs(world.memory) == {}


def test_the_tree_view_route_serves_the_port_and_refuses_when_unwired(world: World) -> None:
    world.edit()
    app = FastAPI()
    seen: list[ReviewTreesQuery] = []

    def port(query: ReviewTreesQuery) -> Any:
        seen.append(query)
        return read_review_trees(world.config, query)

    register_review_trees_route(app, port)
    client = TestClient(app)
    body = client.get("/api/review/trees", params={"repo": REPO, "master": MASTER, "leaf": LEAF})
    assert body.status_code == 200 and body.json()["state"] == "trees"
    assert body.json()["comparison"]["schema"] == "ar-review-tree-comparison/v1"
    numbered = client.get(
        "/api/review/trees",
        params={"repo": REPO, "master": MASTER, "leaf": LEAF, "comparison": 1},
    )
    assert numbered.json()["comparison"]["number"] == 1 and seen[-1].number == 1
    unwired = FastAPI()
    register_review_trees_route(unwired, None)
    assert (
        TestClient(unwired)
        .get("/api/review/trees", params={"repo": REPO, "master": MASTER, "leaf": LEAF})
        .status_code
        == 503
    )


@pytest.fixture(autouse=True)
def _no_bound_services() -> Iterator[None]:
    yield
    reset_worktree_services()
