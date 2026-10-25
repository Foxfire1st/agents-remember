"""MIK-R198 rule 5: no memory publication while a reference anchor misses its code.

The guard lives in the shared knowledge validator, so these tests drive it the way the routes do:
one memory Git tree, the exact paired code commit, and the validator's own refusal. The binary under
test is the anchor rule, not a second scanner: every case changes one thing in the pair and reads the
sharp edge -- a stale blob whose bytes still match, a same-blob anchor whose bytes do not, a cited
source changed outside the leaf's authored closure, and the two reporting dispositions.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.memory_quality.converted_check import converted_knowledge_check
from agents_remember.memory_quality.final_certification.catalog import (
    ReadinessProjectionInput,
    final_catalog_readiness,
)
from agents_remember.memory_quality.knowledge_validator import (
    ValidationReport,
    validate_tree,
    writer_reported_rule_ids,
)
from agents_remember.memory_quality.knowledge_validator.commit_route import GitKnowledgeValidation
from agents_remember.memory_quality.reference_state import git_blob_id, located
from agents_remember.models.knowledge_files.anchor_content import content_identity
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.worktrees.knowledge_validation import PairedCode, memory_commit_refusal
from agents_remember.worktrees.modules.record_landing import _landing_request
from agents_remember.worktrees.services import LeafPublication
from agents_remember.worktrees.worktree_contract import WorktreeContract
from knowledge_validator_test_support import (
    CODE_PATHS,
    DIRECT_LANDING,
    DIRECT_LANDING_SIDECAR,
    ROUTE_SIDECAR,
    code,
    encode,
    fixture_code_contents,
    fixture_tree_files,
    tree,
)

RULE = "R198.1-reference-anchor"
DRIFT_ITEM = "integrity.onboarding_drift_check.summary"


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def _repository(path: Path, files: dict[str, bytes]) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "agents-remember@example.invalid")
    _git(path, "config", "user.name", "Agents Remember")
    return _commit(path, files, "seed")


def _commit(path: Path, files: dict[str, bytes], message: str) -> str:
    for relative, data in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "--allow-empty", "-m", message)
    return _git(path, "rev-parse", "HEAD")


def _rules(report: ValidationReport) -> set[str]:
    return {violation.rule for violation in report.refusals}


def _anchor_of(files: dict[str, bytes], sidecar: str, field: str) -> dict[str, Any]:
    document = json.loads(files[sidecar])
    number, _, index = field.removeprefix("references.").partition(".targets.")
    return document["references"][number]["targets"][int(index)]["anchor"]


def _edit_anchor(
    files: dict[str, bytes], sidecar: str, field: str, change: dict[str, Any]
) -> dict[str, bytes]:
    edited = dict(files)
    document = json.loads(edited[sidecar])
    number, _, index = field.removeprefix("references.").partition(".targets.")
    document["references"][number]["targets"][int(index)]["anchor"].update(change)
    edited[sidecar] = encode(document)
    return edited


def _published(memory: Path, candidate: str, base: str, code_root: Path, code_commit: str) -> Any:
    """The leaf-publication verdict of the shared validator for one exact pair."""

    publication = LeafPublication(candidate_tree=candidate, bases=(base,), frozen=())
    return GitKnowledgeValidation().leaf_refusal(
        memory_repository=memory,
        publication=publication,
        code_repository=code_root,
        code_commit=code_commit,
    )


def test_a_stale_blob_with_identical_content_refuses_a_leaf_publication() -> None:
    """Class (a): the bytes still match, the recorded blob does not -- the anchor still refuses."""

    files = fixture_tree_files()
    anchor = _anchor_of(files, DIRECT_LANDING_SIDECAR, "references.1.targets.0")
    stale = _edit_anchor(
        files, DIRECT_LANDING_SIDECAR, "references.1.targets.0", {"blob": "0" * 40}
    )

    report = validate_tree(tree(stale), bases=[tree(files)], code=code(), leaf_publication=True)

    assert _rules(report) == {RULE}, report.render()
    (violation,) = [one for one in report.refusals if one.rule == RULE]
    assert violation.path == DIRECT_LANDING_SIDECAR
    assert violation.field == "references.1.targets.0"
    assert "stale:blob" in violation.message
    # The bytes are untouched: re-recording the blob alone closes the finding.
    repaired = _edit_anchor(
        files, DIRECT_LANDING_SIDECAR, "references.1.targets.0", {"blob": anchor["blob"]}
    )
    assert validate_tree(tree(repaired), bases=[tree(files)], code=code()).ok


def test_a_same_blob_anchor_with_the_wrong_recorded_content_refuses() -> None:
    """A blob match is not enough: the recorded content identity must hold at that blob too."""

    files = fixture_tree_files()
    stale = _edit_anchor(
        files, DIRECT_LANDING_SIDECAR, "references.2.targets.0", {"content": "sha256:" + "0" * 64}
    )

    report = validate_tree(tree(stale), bases=[tree(files)], code=code(), leaf_publication=True)

    assert _rules(report) == {RULE}, report.render()
    assert "stale:content" in report.refusals[0].message


def test_the_anchor_debt_reports_at_a_sync_merge_and_refuses_every_publication() -> None:
    """MIK-R198 rule 7: repair inputs are allowed; publication is not."""

    files = fixture_tree_files()
    stale = _edit_anchor(files, ROUTE_SIDECAR, "references.1.targets.0", {"blob": "0" * 40})

    merge = validate_tree(tree(stale), bases=[tree(files), tree(files)], code=code())
    leaf = validate_tree(tree(stale), bases=[tree(files)], code=code(), leaf_publication=True)

    assert merge.ok
    assert RULE in {one.rule for one in merge.reports}
    (reported,) = [one for one in merge.reports if one.rule == RULE]
    assert "the next leaf publication or landing refuses it" in reported.message
    assert _rules(leaf) == {RULE}
    assert RULE in writer_reported_rule_ids()


def test_changing_the_cited_source_refuses_publication_until_every_reference_is_repaired(
    tmp_path: Path,
) -> None:
    """Expected evidence 3: the source moves outside the leaf's closure, the pair is judged."""

    code_root = tmp_path / "code"
    code_files = {path: b"# code\n" for path in CODE_PATHS} | dict(fixture_code_contents())
    code_commit = _repository(code_root, code_files)
    memory = tmp_path / "memory"
    files = fixture_tree_files()
    base = _repository(memory, files)

    assert _published(memory, base, base, code_root, code_commit) is None

    # The cited file changes -- not in the memory tree, which this leaf never authored.
    changed = code_root / DIRECT_LANDING
    changed.write_bytes(changed.read_bytes() + b"\n# an unrelated landing appends a line\n")
    _git(code_root, "commit", "-q", "-am", "unrelated code landing")
    moved = _git(code_root, "rev-parse", "HEAD")

    refusal = _published(memory, base, base, code_root, moved)
    assert refusal is not None and RULE in refusal
    cited = [DIRECT_LANDING_SIDECAR, ROUTE_SIDECAR]

    # Repairing one sidecar's anchors leaves the other sidecar's reference refusing.
    snapshot = CodeSnapshot.at_commit(code_root, moved)
    fields = tuple(f"references.{number}.targets.0" for number in (1, 2, 3, 4))
    first = _repair(files, snapshot, DIRECT_LANDING_SIDECAR, fields)
    partially = _commit(memory, first, "repair one sidecar")
    refusal = _published(memory, partially, base, code_root, moved)
    assert refusal is not None and RULE in refusal
    assert ROUTE_SIDECAR in refusal and DIRECT_LANDING_SIDECAR not in refusal

    second = _repair(
        first, snapshot, ROUTE_SIDECAR, tuple(f"references.1.targets.{index}" for index in (0, 1))
    )
    repaired = _commit(memory, second, "repair the rest")
    assert _published(memory, repaired, partially, code_root, moved) is None
    assert cited == [DIRECT_LANDING_SIDECAR, ROUTE_SIDECAR]


def _repair(
    files: dict[str, bytes], snapshot: CodeSnapshot, sidecar: str, fields: tuple[str, ...]
) -> dict[str, bytes]:
    """Re-record exactly ``fields`` of ``sidecar`` at the code snapshot, as the fixer does."""

    repaired = dict(files)
    document = json.loads(repaired[sidecar])
    own = document.get("path")
    for field in fields:
        number, _, index = field.removeprefix("references.").partition(".targets.")
        target = document["references"][number]["targets"][int(index)]
        path = target["anchor"].get("path") or own
        anchor = snapshot.resolve(path, target["anchor"]["locator"])
        recorded = {
            "locator": anchor.locator.model_dump(),
            "blob": anchor.blob,
            "content": anchor.content,
        }
        target["anchor"] = recorded if path == own else {"path": path, **recorded}
    repaired[sidecar] = canonical_text(document).encode("utf-8")
    return repaired


def test_a_stale_reference_fails_the_exact_pair_quality_and_its_readiness_item(
    tmp_path: Path,
) -> None:
    """Expected evidence 6: full quality refuses, and the drift item is no longer report-only."""

    memory = tmp_path / "memory"
    code_root = tmp_path / "code"
    source = b"def alpha():\n    return 1\n"
    locator = {"kind": "symbol", "name": "alpha"}
    found = located("pkg/module.py", source, locator)
    assert found is not None
    anchor = {
        "locator": locator,
        "blob": git_blob_id(source),
        "content": content_identity(found),
    }
    sidecar = {
        "schema": "ar-onboarding-file/v1",
        "path": "pkg/module.py",
        "references": {"1": {"targets": [{"kind": "code", "anchor": anchor}]}},
        "realizes": [],
    }
    write(
        memory / "knowledge/layout.json",
        encode({"conversion": "1", "schema": "ar-memory-layout/v2"}),
    )
    write(
        memory / "onboarding/pkg/module.py.md",
        b"# module.py\n\n## Purpose\n\nIt holds alpha [1].\n",
    )
    write(memory / "onboarding/pkg/module.py.json", encode(sidecar))
    write(code_root / "pkg/module.py", source)

    passing = converted_knowledge_check(memory, code_root, include_landing=False)
    assert passing["ok"], passing["findings"]
    assert passing["referenceStates"] == {"current": 1, "stale": 0}

    stale = json.loads(encode(sidecar))
    stale["references"]["1"]["targets"][0]["anchor"]["blob"] = "0" * 40
    write(memory / "onboarding/pkg/module.py.json", encode(stale))

    failing = converted_knowledge_check(memory, code_root, include_landing=False)
    assert not failing["ok"]
    assert [one["rule"] for one in failing["findings"]] == [RULE]
    assert failing["findings"][0]["path"] == "onboarding/pkg/module.py.json"
    assert failing["findings"][0]["field"] == "references.1.targets.0"
    assert failing["referenceStates"] == {"current": 0, "stale": 1}
    assert failing["referenceStateDetail"] == {"stale:blob": 1}

    drift = _drift_item(failing)
    assert (drift["status"], drift["findingCount"]) == ("fail", 1)
    projection = final_catalog_readiness(
        ReadinessProjectionInput(executed_checks={"knowledge.converted": failing})
    )
    assert projection["finalizationEligible"] is False
    assert projection["fullFinalRequired"] is True

    write(memory / "onboarding/pkg/module.py.json", encode(sidecar))
    repaired = converted_knowledge_check(memory, code_root, include_landing=False)
    assert repaired["ok"], repaired["findings"]
    assert _drift_item(repaired)["status"] == "pass"


def test_a_recorded_landing_of_a_merge_commit_is_judged_with_one_base(tmp_path: Path) -> None:
    """A publication is not a sync merge: the landed line's first parent is its comparison base.

    Handing in both parents made a two-parent recorded landing look like a sync merge, where
    MIK-R198 rule 7 reports a stale anchor instead of refusing it -- so an unchanged memory commit
    could be recorded with anchors that no longer hold.
    """

    code_root = tmp_path / "code"
    code_commit = _repository(code_root, {"pkg/module.py": b"def alpha():\n    return 1\n"})
    memory = tmp_path / "memory"
    _repository(memory, {"onboarding/pkg/module.py.md": b"# module.py\n"})
    _git(memory, "checkout", "-q", "-b", "side")
    _commit(memory, {"onboarding/pkg/side.md": b"# side\n"}, "side")
    _git(memory, "checkout", "-q", "main")
    line_tip = _commit(memory, {"onboarding/pkg/line.md": b"# line\n"}, "line")
    _git(memory, "merge", "-q", "--no-ff", "-m", "merge the side", "side")
    landed = _git(memory, "rev-parse", "HEAD")

    request = _landing_request(_contract(tmp_path, code_root, memory), memory, code_commit, landed)

    assert not isinstance(request, str), request
    assert request.memory_bases == (line_tip,)


def _contract(root: Path, code_root: Path, memory: Path) -> WorktreeContract:
    return WorktreeContract(
        task_id="260928_TEST",
        task_name="anchor-hold",
        repo_name="agents-remember",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=root,
        task_root=root / "tasks",
        contract_path=root / "tasks" / "leaf-contract.md",
        task_artifact=root / "tasks" / "task.md",
        worktree_group=root / "worktrees",
        code_repo_path=code_root,
        code_source_branch="main",
        code_work_branch="ar/leaf",
        code_base_commit="",
        code_worktree=root / "worktrees" / "leaf",
        memory_repo_path=memory,
        memory_source_branch="main",
        memory_work_branch="ar/leaf",
        memory_base_commit="",
        memory_worktree=root / "worktrees" / "leaf-memory",
        integrated_code_commit="",
        integrated_memory_content_commit="",
        leaf_id="leaf",
    )


def test_the_worktree_route_reports_at_a_sync_merge_and_refuses_a_publication(
    tmp_path: Path, worktree_services: None
) -> None:
    """The route every publication uses: two bases report rule 5, a leaf publication refuses it."""

    code_root = tmp_path / "code"
    code_commit = _repository(
        code_root,
        {path: b"# code\n" for path in CODE_PATHS} | dict(fixture_code_contents()),
    )
    files = fixture_tree_files()
    memory = tmp_path / "memory"
    base = _repository(memory, files)
    stale = _edit_anchor(files, ROUTE_SIDECAR, "references.1.targets.0", {"blob": "0" * 40})
    candidate = _commit(memory, stale, "stale anchor")
    paired = PairedCode(repository=code_root, commit=code_commit)

    merge = memory_commit_refusal(
        memory_repository=memory,
        candidate_tree=candidate,
        bases=(base, base),
        paired_code=paired,
    )
    assert merge is None  # a sync merge reports the debt; it does not refuse the merge

    (refusal,) = [
        memory_commit_refusal(
            memory_repository=memory,
            candidate_tree=candidate,
            bases=(base,),
            paired_code=paired,
            leaf_publication=LeafPublication(candidate_tree=candidate, bases=(base,), frozen=()),
        )
    ]
    assert refusal is not None and RULE in refusal


def _drift_item(result: dict[str, Any]) -> dict[str, Any]:
    projected = final_catalog_readiness(
        ReadinessProjectionInput(executed_checks={"knowledge.converted": result})
    )
    (item,) = (one for one in projected["items"] if one["item"]["itemId"] == DRIFT_ITEM)  # type: ignore[union-attr]
    return item
