"""MIK-R22 rule 8: where the validator runs -- the Git inputs, the commit-route port, the managed
sync after a merge, and the curator's standalone command -- with a refusal test at each point.

Every route validates only when a side of the commit holds the layout marker, so the unconverted
memory of today's production commits exactly as before; the existing sync tests prove that path.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.cli.__main__ import main as cli_main
from agents_remember.kernel.git_command import read_git_blobs_bytes
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.memory_quality.knowledge_validator import (
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
    validate_tree,
)
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.worktrees.knowledge_validation import PairedCode, memory_commit_refusal
from agents_remember.worktrees.services import LeafPublication, bind_worktree_services
from agents_remember.worktrees.services import worktree_services as bound_worktree_services
from agents_remember.worktrees.sync_transaction_authority import parse_refusal_violations
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
)
from knowledge_validator_test_support import (
    CODE_PATHS,
    INTEGRATE,
    INTEGRATE_CARD,
    INTEGRATE_SIDECAR,
    INVARIANT,
    LEGACY_COUNT,
    code,
    edit_json,
    encode,
    fixture_tree_files,
    invariant_document,
    invariant_path,
    tree,
    write_tree,
)
from test_knowledge_requirement_reference_contract import _write_packet
from test_worktree_sync import SyncFixture

LEFT_DUPLICATE = invariant_path("INV-D0P111", "left")
RIGHT_DUPLICATE = invariant_path("INV-D0P111", "right")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def _repository(path: Path, files: dict[str, bytes]) -> str:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "agents-remember@example.invalid")
    git(path, "config", "user.name", "Agents Remember")
    return _commit(path, files, "seed")


def _commit(path: Path, files: dict[str, bytes], message: str) -> str:
    write_tree(path, files)
    git(path, "add", "-A")
    git(path, "commit", "-q", "--allow-empty", "-m", message)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def code_repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "code"
    return root, _repository(root, {path: b"# code\n" for path in CODE_PATHS})


def test_marker_refusal_offers_fenced_code_block_and_code_span() -> None:
    files = {**fixture_tree_files(), INTEGRATE_CARD: b"# integrate\n\nSlots[0]\n"}
    report = validate_tree(tree(files), bases=[tree(files)], code=code())
    message = next(one.message for one in report.refusals if one.rule == "R22.3-markers")
    assert "fenced code block" in message
    assert "code span" in message


def test_validator_working_tree_ignores_ignored_paths_but_sees_untracked_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code_repository: tuple[Path, str]
) -> None:
    code_root, _ = code_repository
    (code_root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    write_tree(code_root, {"ignored/file.py": b"# ignored\n", "untracked/file.py": b"# visible\n"})
    record = "knowledge/decisions/DEC-D12RTE-local-family-routes.json"
    files = edit_json(
        fixture_tree_files(),
        record,
        lambda doc: doc["links"].extend(
            [
                {"relation": "constrains", "target": "route:ignored"},
                {"relation": "constrains", "target": "route:untracked"},
            ]
        ),
    )
    memory = tmp_path / "memory"
    base = _repository(memory, files)
    status, out = _cli(capsys, str(memory), "--code", str(code_root), "--base", base, "--json")
    assert status == 1, out
    links = [
        one for one in json.loads(out)["violations"] if one["rule"] == "R22.6-link-target-path"
    ]
    assert len(links) == 1 and "ignored" in links[0]["message"], out


def test_validator_without_base_explains_every_authored_record_is_new(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code_repository: tuple[Path, str]
) -> None:
    code_root, _ = code_repository
    memory = tmp_path / "memory"
    _repository(memory, fixture_tree_files())
    _, out = _cli(capsys, str(memory), "--code", str(code_root))
    assert "every authored record is judged as new" in out
    _, out = _cli(capsys, str(memory), "--code", str(code_root), "--json")
    assert any(
        "every authored record is judged as new" in note for note in json.loads(out)["notes"]
    )


def test_validator_lists_record_and_sidecar_requirement_endpoints_without_refusing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code_repository: tuple[Path, str]
) -> None:
    record = "knowledge/decisions/DEC-D12RTE-local-family-routes.json"
    files = fixture_tree_files()
    reference = json.loads(files[record])["links"][3]["target"]
    unresolved = {**reference, "version": "v99"}
    files = edit_json(
        files,
        INTEGRATE_SIDECAR,
        lambda doc: doc["references"].update(
            {
                "7": {"targets": [{"kind": "requirement", "requirement": unresolved}]},
            }
        ),
    )
    files[INTEGRATE_CARD] += b"\nPacket [7].\n"
    memory = tmp_path / "memory"
    base = _repository(memory, files)
    coordination = tmp_path / "coordination"
    archived = (
        coordination
        / "tasks"
        / reference["task"]["repository"]
        / "0_archive"
        / reference["task"]["path"]
    )
    _write_packet(
        archived, reference["packet"], stable_id=reference["id"], version=reference["version"]
    )
    args = (
        str(memory),
        "--code",
        str(code_repository[0]),
        "--base",
        base,
        "--coordination-root",
        str(coordination),
    )
    status, out = _cli(capsys, *args, "--json")
    assert status == 0, out
    document = json.loads(out)
    endpoints = document["requirementEndpoints"]
    assert len(endpoints) == 2
    assert {(one["path"], one["state"], one["archived"]) for one in endpoints} == {
        (record, "resolved", True),
        (INTEGRATE_SIDECAR, "unresolved", True),
    }
    assert document["refusalCount"] == 0
    status, out = _cli(capsys, *args)
    assert status == 0 and "requirement endpoints" in out
    assert "resolved" in out and "unresolved" in out and "archived" in out


def test_git_trees_are_read_exactly_and_the_route_index_cache_is_ignored(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    files = {
        **fixture_tree_files(),
        INVARIANT: fixture_tree_files()[INVARIANT].replace(b"\n", b"\r\n"),
        "onboarding/mcp/overview.index.json": b"{}",
        "README.md": b"# not knowledge\n",
        "onboarding/.git-blame-ignore-revs.md": b"# dot-named card\n",
        "onboarding/.ar-index/cache.json": b"{}",
    }
    commit = _repository(memory, files)

    snapshot = knowledge_tree_from_git(memory, commit)

    assert snapshot.files[INVARIANT] == files[INVARIANT]
    assert "onboarding/mcp/overview.index.json" not in snapshot.files
    assert "README.md" not in snapshot.files
    assert "onboarding/.git-blame-ignore-revs.md" in snapshot.files
    assert "onboarding/.ar-index/cache.json" not in snapshot.files
    assert knowledge_tree_from_directory(memory).files == snapshot.files
    assert snapshot.converted
    blob = git(memory, "rev-parse", f"{commit}:{INVARIANT}")
    assert read_git_blobs_bytes(memory, [blob, blob]) == {blob: files[INVARIANT]}


def test_the_commit_route_adapter_refuses_naming_every_violation(
    tmp_path: Path, code_repository: tuple[Path, str]
) -> None:
    code_root, code_commit = code_repository
    memory = tmp_path / "memory"
    unconverted = _repository(memory, {"onboarding/x.md": b"# x [1]\n"})
    git(memory, "rm", "-q", "onboarding/x.md")
    base = _commit(memory, fixture_tree_files(), "convert")
    broken = {**fixture_tree_files(), INTEGRATE_CARD: b"# integrate.py\n\nSee [4].\n"}
    _commit(memory, broken, "hand edit")
    broken_tree = git(memory, "rev-parse", "HEAD^{tree}")
    validation = GitKnowledgeValidation()

    def refusal(tree: str, bases: list[str]) -> str | None:
        return validation.refusal(
            memory_repository=memory,
            candidate_tree=tree,
            bases=bases,
            code_repository=code_root,
            code_commit=code_commit,
        )

    assert refusal(unconverted, [unconverted]) is None
    assert refusal(base, [unconverted]) is not None  # the conversion commit needs its conversion
    assert refusal(base, [base]) is None
    assert "cannot read" in (refusal(base, ["no-such-ref"]) or "")
    refused = refusal(broken_tree, [base])
    assert refused is not None
    assert f"{INTEGRATE_CARD}: line 3: [R22.3-markers] marker [4] has no reference" in refused


def test_the_worktree_route_never_commits_converted_memory_unvalidated(
    tmp_path: Path, code_repository: tuple[Path, str], worktree_services: None
) -> None:
    code_root, code_commit = code_repository
    memory = tmp_path / "memory"
    unconverted = _repository(memory, {"onboarding/x.md": b"# x [1]\n"})
    git(memory, "rm", "-q", "onboarding/x.md")
    converted = _commit(memory, fixture_tree_files(), "convert")
    paired = PairedCode(code_root, code_commit)
    bound = bound_worktree_services()

    def refusal(tree: str, code: PairedCode | None) -> str | None:
        return memory_commit_refusal(
            memory_repository=memory, candidate_tree=tree, bases=[tree], paired_code=code
        )

    assert refusal(converted, paired) is None
    assert "cannot read" in (refusal("0" * 40, paired) or "")  # a Git failure fails closed
    assert "paired code commit is unknown" in (refusal(converted, None) or "")
    try:
        bind_worktree_services(replace(bound, knowledge_validation=None))
        assert refusal(unconverted, None) is None
        assert "not bound" in (refusal(converted, paired) or "")
    finally:
        bind_worktree_services(bound)


def test_the_managed_sync_refuses_a_merge_with_duplicate_ids_until_it_is_repaired(
    tmp_path: Path, worktree_services: None
) -> None:
    """Rule 2 and rule 8: after the merge, a duplicate ID is a conflict naming both files."""

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    work = {**fixture_tree_files(), LEFT_DUPLICATE: encode(invariant_document("INV-D0P111"))}
    before = _commit(worktree, work, "Work: mint an invariant")
    code_tip = fixture.move_official_code()
    source = {**fixture_tree_files(), RIGHT_DUPLICATE: encode(invariant_document("INV-D0P111"))}
    _commit(fixture.memory_repo, source, render_memory_content_message("Source mint", code_tip))
    source_tip = git(fixture.memory_repo, "rev-parse", "HEAD")

    refused = fixture.sync(memory_sync_choice="merge-memory")

    assert refused.payload["state"] == "sync-knowledge-validation-refused", refused.payload
    summary = str(refused.payload["summary"])
    assert "merge conflict: ID INV-D0P111" in summary
    assert LEFT_DUPLICATE in summary and RIGHT_DUPLICATE in summary
    assert git(worktree, "rev-parse", "HEAD") == before
    assert git(worktree, "rev-parse", "MERGE_HEAD") == source_tip
    assert "then rerun worktree_sync; or cancel the sync with resolution_action='cancel'" in summary

    git(worktree, "rm", "-q", "-f", RIGHT_DUPLICATE)
    repaired = fixture.sync()

    assert repaired.payload["state"] == "synced", repaired.payload
    assert git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:] == [
        before,
        source_tip,
    ]


def test_a_fast_forward_onto_an_invalid_tree_refuses_without_moving_any_ref(
    tmp_path: Path, worktree_services: None
) -> None:
    """MIK-R61 rule 4 and rule 5: a fast-forward judges what it adopts before any ref moves.

    The incoming converted tree carries a duplicate invariant ID, so the validator
    refuses the fast-forward: the memory ref has not moved, the refusal is journaled,
    and after the line is repaired a new sync passes and clears it.
    """

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    memory_before = git(worktree, "rev-parse", "HEAD")
    code_tip = fixture.move_official_code()
    source = {
        **fixture_tree_files(),
        LEFT_DUPLICATE: encode(invariant_document("INV-D0P111")),
        RIGHT_DUPLICATE: encode(invariant_document("INV-D0P111")),
    }
    _commit(fixture.memory_repo, source, render_memory_content_message("Source mint", code_tip))

    refused = fixture.sync()

    assert refused.payload["state"] == "sync-knowledge-validation-refused", refused.payload
    assert git(worktree, "rev-parse", "HEAD") == memory_before
    # MIK-R61 rule 5: the status carries the parsed fields, not just the journal.
    carried = refused.payload.get("validationRefusal")
    assert isinstance(carried, dict), refused.payload
    assert carried["kind"] == "fast-forward"
    assert carried["total"] >= 1
    assert 1 <= len(carried["violations"]) <= 50
    assert carried["total"] >= len(carried["violations"])
    assert all(entry["path"] and entry["rule"] for entry in carried["violations"])
    store = SyncOperationStore(fixture.contract.worktree_group)
    journaled = store.read()
    assert isinstance(journaled, SyncOperationRecord)
    assert journaled.validationRefusal is not None
    assert journaled.validationRefusal.kind == "fast-forward"
    # MIK-R61 rule 5: the journaled entries are the validator's own per-path,
    # per-rule violations with its true total, not one generic message.
    assert journaled.validationRefusal.total >= 1
    assert len(journaled.validationRefusal.violations) >= 1
    assert len(journaled.validationRefusal.violations) <= 50
    assert journaled.validationRefusal.total >= len(journaled.validationRefusal.violations)
    assert all(entry.path and entry.rule for entry in journaled.validationRefusal.violations)

    git(fixture.memory_repo, "rm", "-q", RIGHT_DUPLICATE)
    git(
        fixture.memory_repo,
        "commit",
        "-q",
        "-m",
        render_memory_content_message("Repair the duplicate", code_tip),
    )
    summary = str(refused.payload.get("summary"))
    assert "resolution_action='cancel'" in summary
    cancelled = fixture.sync(resolution_action="cancel")
    assert cancelled.payload["state"] == "sync-cancelled", cancelled.payload
    repaired = fixture.sync()

    assert repaired.payload["state"] == "synced", repaired.payload
    assert git(worktree, "rev-parse", "HEAD") != memory_before
    cleared = store.read()
    assert isinstance(cleared, SyncOperationRecord)
    assert cleared.validationRefusal is None


def test_a_repaired_staged_merge_clears_the_journaled_refusal(
    tmp_path: Path, worktree_services: None
) -> None:
    """MIK-R61 rule 5: the refusal is journaled with its violations; a continued sync
    validates again and clears it when the tree passes."""

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    work = {**fixture_tree_files(), LEFT_DUPLICATE: encode(invariant_document("INV-D0P111"))}
    _commit(worktree, work, "Work: mint an invariant")
    code_tip = fixture.move_official_code()
    source = {**fixture_tree_files(), RIGHT_DUPLICATE: encode(invariant_document("INV-D0P111"))}
    _commit(fixture.memory_repo, source, render_memory_content_message("Source mint", code_tip))

    refused = fixture.sync(memory_sync_choice="merge-memory")

    assert refused.payload["state"] == "sync-knowledge-validation-refused", refused.payload
    store = SyncOperationStore(fixture.contract.worktree_group)
    journaled = store.read()
    assert isinstance(journaled, SyncOperationRecord)
    assert journaled.validationRefusal is not None
    assert journaled.validationRefusal.kind == "staged-merge"
    assert journaled.validationRefusal.total >= 1

    git(worktree, "rm", "-q", "-f", RIGHT_DUPLICATE)
    repaired = fixture.sync()

    assert repaired.payload["state"] == "synced", repaired.payload
    cleared = store.read()
    assert isinstance(cleared, SyncOperationRecord)
    assert cleared.validationRefusal is None


def test_parallel_minted_ids_sync_cleanly(tmp_path: Path, worktree_services: None) -> None:
    """Packet conforming example: two parallel leaves each mint an invariant."""

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    left = invariant_path("INV-AAAAAA")
    right = invariant_path("INV-BBBBBB")
    _commit(worktree, {**fixture_tree_files(), left: encode(invariant_document("INV-AAAAAA"))}, "w")
    code_tip = fixture.move_official_code()
    source = {**fixture_tree_files(), right: encode(invariant_document("INV-BBBBBB"))}
    _commit(fixture.memory_repo, source, render_memory_content_message("Source mint", code_tip))

    result = fixture.sync(memory_sync_choice="merge-memory")

    assert result.payload["state"] == "synced", result.payload
    assert (worktree / left).is_file() and (worktree / right).is_file()


LEAF_HISTORY = "knowledge/history/260928-MIK-L97.json"


def _moved(document: dict[str, Any]) -> None:
    """The official line re-anchors the integrate route's entry (another leaf edited the code)."""

    moved = {"blob": "1" * 40, "content": "sha256:" + "2" * 64}
    document["realizes"][0]["anchor"] = {**document["realizes"][0]["anchor"], **moved}


@pytest.mark.parametrize("own_row", ["consistent", "already wrong"])
def test_a_sync_merge_refuses_a_history_row_only_when_the_leaf_s_own_side_had_it_wrong(
    tmp_path: Path, worktree_services: None, own_row: str
) -> None:
    """MIK-R09 at a sync: a mismatch the merge caused surfaces at the gate, not at the sync.

    The leaf's open row covers the integrate route's entry; the official line then re-anchors that
    entry. A row that agreed with the leaf's own side syncs (``R09-history-rows-merged`` reports it);
    a row that already disagreed there is refused by ``R09-history-rows``.
    """

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    files = fixture_tree_files()
    code_tip = fixture.move_official_code()
    _commit(fixture.memory_repo, files, render_memory_content_message("Convert", code_tip))
    assert fixture.sync(memory_sync_choice="merge-memory").payload["state"] == "synced"

    entry = json.loads(files[INTEGRATE_SIDECAR])["realizes"][0]
    anchor = {**entry["anchor"], "path": INTEGRATE}
    after = anchor if own_row == "consistent" else {**anchor, "blob": "3" * 40}
    row = {
        "id": "ROW-000000",
        "items": [],
        "subject": entry["invariant"],
        "disposition": "no_impact",
        "reason": "The route still pairs its commits.",
        "covers": [{"id": entry["id"], "before": anchor, "after": after}],
        "revision": json.loads(files[INVARIANT])["revision"],
    }
    history = {"schema": "ar-history/v1", "leaf": "260928-MIK-L97", "closed": False, "rows": [row]}
    _commit(worktree, {LEAF_HISTORY: encode(history)}, "Work: a row about the route")

    later = _commit(fixture.code_repo, {"src/later.py": b"LATER = 1\n"}, "Later code")
    moved = edit_json(files, INTEGRATE_SIDECAR, _moved)[INTEGRATE_SIDECAR]
    message = render_memory_content_message("Re-anchor", later)
    _commit(fixture.memory_repo, {INTEGRATE_SIDECAR: moved}, message)

    result = fixture.sync(memory_sync_choice="merge-memory")

    if own_row == "consistent":
        assert result.payload["state"] == "synced", result.payload
        assert (worktree / INTEGRATE_SIDECAR).read_bytes() == moved  # the merge moved the entry
    else:
        assert result.payload["state"] == "sync-knowledge-validation-refused", result.payload
        summary = str(result.payload["summary"])
        assert "R09-history-rows" in summary and "R09-history-rows-merged" not in summary


def _cli(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    status = cli_main(["knowledge-validate", *args])
    return status, capsys.readouterr().out


def test_the_standalone_command_validates_a_converted_fixture_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code_repository: tuple[Path, str]
) -> None:
    code_root, code_commit = code_repository
    memory = tmp_path / "memory"
    base = _repository(memory, fixture_tree_files())

    status, out = _cli(capsys, str(memory), "--code", str(code_root), "--base", base)
    assert status == 0, out
    # The one report-only finding is MIK-R27's count of the fixture's exported family.
    assert "passes: 0 violation(s), 1 report-only finding(s)" in out

    (memory / INTEGRATE_CARD).write_bytes(b"# integrate.py\n\nSee [4].\n")
    status, out = _cli(
        capsys, str(memory), "--code", str(code_root), "--code-commit", code_commit, "--json"
    )
    assert status == 1
    document = json.loads(out)
    assert document["ok"] is False
    rules = sorted((one["rule"], one["reportOnly"]) for one in document["violations"])
    assert rules == [("R22.3-markers", False), (LEGACY_COUNT, False)]

    unconverted = tmp_path / "unconverted"
    write_tree(unconverted, {"onboarding/x.md": b"# x [1]\n"})
    status, out = _cli(capsys, str(unconverted), "--code", str(code_root))
    assert status == 0 and "unconverted" in out

    status, out = _cli(capsys, str(memory), "--code", str(code_root), "--base", "no-such-ref")
    assert status == 2 and "cannot read" in out


def test_a_resolved_memory_conflict_that_breaks_validation_is_refused_on_continue(
    tmp_path: Path, worktree_services: None
) -> None:
    """The retained-conflict path: the agent resolves a real conflict into a duplicate ID."""

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    work = {
        **fixture_tree_files(),
        LEFT_DUPLICATE: encode(invariant_document("INV-D0P111")),
        "README.md": b"work version\n",
    }
    _commit(worktree, work, "Work: mint an invariant")
    code_tip = fixture.move_official_code()
    source = {
        **fixture_tree_files(),
        RIGHT_DUPLICATE: encode(invariant_document("INV-D0P111")),
        "README.md": b"source version\n",
    }
    _commit(fixture.memory_repo, source, render_memory_content_message("Source mint", code_tip))
    conflicted = fixture.sync(memory_sync_choice="merge-memory")
    assert conflicted.payload["state"] == "sync-resolution-required", conflicted.payload
    (worktree / "README.md").write_text("resolved\n", encoding="utf-8")
    git(worktree, "add", "README.md")

    refused = fixture.sync(resolution_action="continue")

    assert refused.payload["state"] == "sync-knowledge-validation-refused", refused.payload
    summary = str(refused.payload["summary"])
    assert "merge conflict: ID INV-D0P111" in summary
    assert "rerun worktree_sync with resolution_action='continue'" in summary
    git(worktree, "rm", "-q", "-f", RIGHT_DUPLICATE)
    assert fixture.sync(resolution_action="continue").payload["state"] == "synced"


def test_a_merge_moved_row_refuses_at_leaf_publication(
    tmp_path: Path, worktree_services: None
) -> None:
    """MIK-R52 rule 7: the report-only merge treatment ends at a leaf publication.

    The same merge the sync test above syncs (the official line moved the covered entry
    while the leaf's own row agreed with its side) is judged here as the leaf's publication:
    every disagreeing row refuses, whether the merge moved it or not.
    """

    fixture = SyncFixture(tmp_path)
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    files = fixture_tree_files()
    code_tip = fixture.move_official_code()
    convert = _commit(
        fixture.memory_repo, files, render_memory_content_message("Convert", code_tip)
    )
    assert fixture.sync(memory_sync_choice="merge-memory").payload["state"] == "synced"

    entry = json.loads(files[INTEGRATE_SIDECAR])["realizes"][0]
    anchor = {**entry["anchor"], "path": INTEGRATE}
    row = {
        "id": "ROW-000000",
        "items": [],
        "subject": entry["invariant"],
        "disposition": "no_impact",
        "reason": "The route still pairs its commits.",
        "covers": [{"id": entry["id"], "before": anchor, "after": anchor}],
        "revision": json.loads(files[INVARIANT])["revision"],
    }
    history = {"schema": "ar-history/v1", "leaf": "260928-MIK-L97", "closed": False, "rows": [row]}
    _commit(worktree, {LEAF_HISTORY: encode(history)}, "Work: a row about the route")

    later = _commit(fixture.code_repo, {"src/later.py": b"LATER = 1\n"}, "Later code")
    moved = edit_json(files, INTEGRATE_SIDECAR, _moved)[INTEGRATE_SIDECAR]
    message = render_memory_content_message("Re-anchor", later)
    _commit(fixture.memory_repo, {INTEGRATE_SIDECAR: moved}, message)
    assert fixture.sync(memory_sync_choice="merge-memory").payload["state"] == "synced"

    merge = git(fixture.memory_repo, "rev-parse", fixture.contract.memory_work_branch)
    refusal = memory_commit_refusal(
        memory_repository=fixture.memory_repo,
        candidate_tree=merge,
        bases=(convert,),
        paired_code=PairedCode(repository=fixture.code_repo, commit=later),
        leaf_publication=LeafPublication(merge, (convert,)),
    )
    assert refusal is not None
    assert "R09-history-rows" in refusal
    assert "R09-history-rows-merged" not in refusal


def test_refusal_text_journals_structured_violations_with_true_total() -> None:
    """MIK-R61 rule 5: the journal keeps per-path, per-rule entries, not one generic message."""

    message = (
        "the knowledge validator (MIK-R22) refuses this memory commit: 3 violation(s) in abc123\n"
        "knowledge/invariants/a.json: rows: [duplicate-invariant] duplicate id INV-1\n"
        "knowledge/cards/b.md: -: [card-body] body is empty\n"
        "knowledge/sidecar/c.json: quorum: [sidecar-quorum] only 1 of 2 witnesses\n"
    )
    violations, total = parse_refusal_violations(message, context_path="staged memory merge tree")
    assert total == 3
    assert [(entry.path, entry.rule) for entry in violations] == [
        ("knowledge/invariants/a.json", "duplicate-invariant"),
        ("knowledge/cards/b.md", "card-body"),
        ("knowledge/sidecar/c.json", "sidecar-quorum"),
    ]
    assert violations[0].message == "rows: duplicate id INV-1"
    assert violations[1].message == "body is empty"


def test_refusal_text_beyond_fifty_keeps_first_fifty_with_true_total() -> None:
    """MIK-R61 rule 5: exactly the first inline-limit entries are kept; the total stays true."""

    lines = [
        "the knowledge validator (MIK-R22) refuses this memory commit: 60 violation(s) in abc123"
    ]
    lines += [
        f"knowledge/invariants/f{i:02}.json: rows: [duplicate-invariant] duplicate id INV-{i}"
        for i in range(60)
    ]
    violations, total = parse_refusal_violations(
        "\n".join(lines), context_path="incoming memory tree abc123"
    )
    assert total == 60
    assert len(violations) == 50
    assert violations[0].path == "knowledge/invariants/f00.json"
    assert violations[49].path == "knowledge/invariants/f49.json"


def test_unparseable_refusal_text_is_kept_not_lost() -> None:
    """MIK-R61 rule 5: a refusal the parser does not recognize still journals its text."""

    violations, total = parse_refusal_violations(
        "the validator is not bound in this process", context_path="staged memory merge tree"
    )
    assert total == 1
    assert len(violations) == 1
    assert violations[0].rule == "knowledge-validator"
    assert "not bound" in violations[0].message
