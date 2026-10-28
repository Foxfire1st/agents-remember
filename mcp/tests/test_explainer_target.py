"""The real target reads and the one staleness answer composed from them (MIK-R187 rules 4 and 8).

Two modules are exercised here against real Git repositories, made with ``init``, ``add`` and
``write-tree`` so every id is a real object and ``cat-file``/``ls-tree``/``diff`` run as the product
runs them.

* the **target reads** (:mod:`agents_remember.canvas.explainer_target`) answer rule 8's questions of
  one comparison: a range's bytes and its mapped image, a record's revision at a named side, a proof
  entry's fingerprint and execution, the judgment rows and history rows, the packet's receipts and
  the requirements manifest's registered version -- and ``None`` for every one of them when the
  trees cannot be read;
* the **staleness read** (:mod:`agents_remember.canvas.explainer_staleness_read`) composes them into
  one record: the stale parts, the trusted list, the per-grade states, the stale grade references
  and the regeneration text, against the target rule 4 names -- an environment's binding, the
  comparison on screen after Retire, or a planning leaf's planned comparison at the current tips.

Each case names the row of rule 8's table it proves: tree-blob, knowledge-record, proof-entry,
judgment-row, history-event, supplemental-receipt, comparison-field, and a claim with no basis.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest
from agents_remember.application.review_evidence_packet import (
    COMPARISON_OWNER,
    SideState,
    build_evidence_packet,
    planned_reference,
)
from agents_remember.canvas.explainer_grades import (
    Grade,
    Grades,
    GradeStore,
    ScalePoint,
    grades_path,
)
from agents_remember.canvas.explainer_staleness import (
    BlockFingerprint,
    BlockIdentity,
    CardSide,
    card_fingerprint,
    parse_block_identity,
)
from agents_remember.canvas.explainer_staleness_read import (
    GradeInputs,
    gone_grade_references,
    reconcile_grades,
    screen_target,
    staleness_read,
)
from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.canvas.explainer_target import (
    ExplainerTargetReads,
    proof_entry_fingerprint,
    source_version,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    EvidenceMap,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_frame import (
    FrameAsset,
    FrameAuthoring,
    FrameBinding,
    FrameEntrypoint,
    FrameManifest,
    FramePart,
    artifacts_digest,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.models.knowledge_files.anchor_content import content_identity, range_bytes

TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L183"
AT = "2026-10-22T09:00:00Z"
OLD_CODE = {
    "src/app.py": "alpha\nbeta\ngamma\ndelta\nepsilon\nzeta\neta\ntheta\n",
    "docs/notes.md": "the note that does not move\n",
}
NEW_CODE = {
    "src/app.py": "# new header\n# another header\nalpha\nbeta\ngamma\ndelta\nepsilon\nzeta\n",
    "docs/notes.md": "the note that does not move\n",
}
RECORD_FILE = "knowledge/invariants/INV-AAA-one.json"
OTHER_RECORD_FILE = "knowledge/invariants/INV-BBB-one.json"
FIRST_HISTORY = "knowledge/history/260928-MIK-L07.json"
SECOND_HISTORY = "knowledge/history/260928-MIK-L07-attempt-2.json"
SIDECAR = "onboarding/src/app.py.json"
PROOF = {"id": "PRF-ONE", "invariant": "INV-AAA", "facet": "it holds"}
PROOF_AT_TARGET = {"id": "PRF-ONE", "invariant": "INV-AAA", "facet": "it holds after the change"}
DIGEST = "a" * 64
MOVED_SOURCE = "requirements/MIK-R998-v1-moved.md"
STEADY_SOURCE = "requirements/MIK-R999-v1-note.md"
REGISTERED_SOURCE = "requirements/MIK-R187-v1-per-part.md"


def _git(repository: Path, *args: str) -> str:
    completed = run_git(repository, list(args))
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _repository(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "--quiet")
    return root


def _tree(repository: Path, files: Mapping[str, object]) -> str:
    """Make this the repository's whole content and answer the tree id Git writes."""

    for path in repository.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            path.unlink()
    for name, content in files.items():
        target = repository / name
        target.parent.mkdir(parents=True, exist_ok=True)
        text = content if isinstance(content, str) else json.dumps(content, indent=2) + "\n"
        target.write_text(text, encoding="utf-8")
    _git(repository, "add", "-A")
    return _git(repository, "write-tree").strip()


def _memory_files(*, revision: int, proof: dict[str, str], execution: str | None) -> dict:
    """One memory tree: two records, two history files and one sidecar with one proof entry."""

    entry = dict(proof)
    if execution is not None:
        entry["execution"] = execution
    return {
        RECORD_FILE: {"id": "INV-AAA", "kind": "invariant", "revision": revision},
        OTHER_RECORD_FILE: {"id": "INV-BBB", "kind": "invariant", "revision": 2},
        FIRST_HISTORY: {
            "owner": "260928-MIK-L07",
            "attempt": 1,
            "rows": [
                {"id": "ROW-1", "subject": "INV-AAA", "disposition": "changed"},
                {"id": "ROW-OTHER", "subject": "INV-BBB", "disposition": "changed"},
            ],
        },
        SECOND_HISTORY: {
            "owner": "260928-MIK-L07",
            "attempt": 2,
            "rows": [{"id": "ROW-1", "subject": "INV-AAA", "disposition": "extended"}],
        },
        SIDECAR: {"schema": "ar-onboarding-file/v1", "path": "src/app.py", "proves": [entry]},
    }


def _receipt(identifier: str, source: str, digest: str) -> SupplementalReceipt:
    """One supplemental receipt of a task document, as a target packet carries it."""

    return SupplementalReceipt(
        receipt_id=identifier,
        canonical_source=source,
        digest=digest,
        byte_size=12,
        read_state="present",
        capture_identity=f"capture-{identifier}",
        source_version="v1",
        establishes="the packet's own words",
    )


def _single_binding(repository: Path, tree: str, *, number: int = 8) -> FrameBinding:
    """One comparison whose four sides are this one tree, for a reader that uses one of them."""

    side = ReviewTreeSide(repository=repository.as_posix(), tree=tree)
    return FrameBinding.from_record(
        ReviewTreeComparisonRecord(
            task_id=TASK,
            leaf_id=LEAF,
            number=number,
            code_base=side,
            code_candidate=side,
            memory_base=side,
            memory_candidate=side,
            recorded_at=AT,
        ),
        comparison_owner="recorded",
        comparison_reference=f"review-comparison:{TASK}/{LEAF}/{number}",
    )


class Trees:
    """The four trees one comparison names, over two real repositories."""

    def __init__(self, tmp_path: Path) -> None:
        self.code = _repository(tmp_path, "code")
        self.memory = _repository(tmp_path, "memory")
        self.code_old = _tree(self.code, OLD_CODE)
        self.code_new = _tree(self.code, NEW_CODE)
        self.memory_old = _tree(
            self.memory, _memory_files(revision=3, proof=PROOF, execution="established")
        )
        self.memory_new = _tree(
            self.memory,
            _memory_files(revision=4, proof=PROOF_AT_TARGET, execution="claimed"),
        )

    def binding(self, *, number: int = 8, owner: str = "recorded") -> FrameBinding:
        comparison = ReviewTreeComparisonRecord(
            task_id=TASK,
            leaf_id=LEAF,
            number=number,
            code_base=ReviewTreeSide(repository=self.code.as_posix(), tree=self.code_old),
            code_candidate=ReviewTreeSide(repository=self.code.as_posix(), tree=self.code_old),
            memory_base=ReviewTreeSide(repository=self.memory.as_posix(), tree=self.memory_old),
            memory_candidate=ReviewTreeSide(
                repository=self.memory.as_posix(), tree=self.memory_old
            ),
            recorded_at=AT,
        )
        return FrameBinding.from_record(
            comparison,
            comparison_owner=owner,
            comparison_reference=f"review-comparison:{TASK}/{LEAF}/{number}",
        )

    def target(self, *, number: int = 8) -> FrameBinding:
        """The later comparison: both trees moved on, both memory sides later."""

        comparison = ReviewTreeComparisonRecord(
            task_id=TASK,
            leaf_id=LEAF,
            number=number,
            code_base=ReviewTreeSide(repository=self.code.as_posix(), tree=self.code_new),
            code_candidate=ReviewTreeSide(repository=self.code.as_posix(), tree=self.code_new),
            memory_base=ReviewTreeSide(repository=self.memory.as_posix(), tree=self.memory_new),
            memory_candidate=ReviewTreeSide(
                repository=self.memory.as_posix(), tree=self.memory_new
            ),
            recorded_at=AT,
        )
        return FrameBinding.from_record(
            comparison,
            comparison_owner="recorded",
            comparison_reference=f"review-comparison:{TASK}/{LEAF}/{number}",
        )


# --- rule 8: the tree-blob row ----------------------------------------------------------------


def test_a_whole_blob_is_current_when_its_bytes_are_and_stale_when_they_move(
    tmp_path: Path,
) -> None:
    trees = Trees(tmp_path)
    reads = ExplainerTargetReads(binding=trees.binding())
    same = reads.content_identity(
        side="code-candidate", tree=trees.code_old, path="docs/notes.md", lines=None
    )
    assert same is not None and same.startswith("sha256:")
    assert (
        reads.content_identity(
            side="code-candidate", tree=trees.code_new, path="docs/notes.md", lines=None
        )
        == same
    )
    changed = reads.content_identity(
        side="code-candidate", tree=trees.code_new, path="src/app.py", lines=None
    )
    assert changed != reads.content_identity(
        side="code-candidate", tree=trees.code_old, path="src/app.py", lines=None
    )


def test_a_line_range_maps_through_the_diff_and_hashes_the_same_bytes(tmp_path: Path) -> None:
    trees = Trees(tmp_path)
    reads = ExplainerTargetReads(binding=trees.binding())
    recorded = (4, 6)
    mapped = reads.mapped_lines(
        side="code-candidate",
        path="src/app.py",
        from_tree=trees.code_old,
        to_tree=trees.code_new,
        lines=recorded,
    )
    assert mapped == (6, 8), "two lines were inserted above the range"
    assert reads.content_identity(
        side="code-candidate", tree=trees.code_old, path="src/app.py", lines=recorded
    ) == reads.content_identity(
        side="code-candidate", tree=trees.code_new, path="src/app.py", lines=mapped
    )
    shifted_start = (1, 3)
    assert reads.content_identity(
        side="code-candidate", tree=trees.code_old, path="src/app.py", lines=shifted_start
    ) == reads.content_identity(
        side="code-candidate", tree=trees.code_new, path="src/app.py", lines=(3, 5)
    ), "the mapped range is where those same bytes now are"
    assert reads.content_identity(
        side="code-candidate", tree=trees.code_old, path="src/app.py", lines=shifted_start
    ) != reads.content_identity(
        side="code-candidate", tree=trees.code_new, path="src/app.py", lines=shifted_start
    ), "reading the recorded numbers at the target would name other bytes"


def test_an_unmappable_range_and_a_blob_the_tree_lacks_answer_none(tmp_path: Path) -> None:
    trees = Trees(tmp_path)
    reads = ExplainerTargetReads(binding=trees.binding())
    deleted = reads.mapped_lines(
        side="code-candidate",
        path="src/app.py",
        from_tree=trees.code_old,
        to_tree=trees.code_new,
        lines=(7, 8),
    )
    assert deleted is None, "every line of the range was deleted, so it has no image"
    assert (
        reads.content_identity(
            side="code-candidate", tree=trees.code_new, path="src/absent.py", lines=None
        )
        is None
    )
    assert (
        reads.content_identity(
            side="code-candidate", tree=trees.code_new, path="src/app.py", lines=(90, 92)
        )
        is None
    )


# --- rule 8: the knowledge-record row ---------------------------------------------------------


def test_a_record_answers_its_revision_at_the_side_the_basis_names(tmp_path: Path) -> None:
    trees = Trees(tmp_path)
    reads = ExplainerTargetReads(binding=trees.binding())
    assert reads.memory_revision(side="memory-candidate", record="INV-AAA", path=RECORD_FILE) == 3
    later = ExplainerTargetReads(binding=trees.target())
    assert later.memory_revision(side="memory-candidate", record="INV-AAA", path=RECORD_FILE) == 4
    assert later.memory_revision(side="memory-base", record="INV-AAA", path=RECORD_FILE) == 4
    assert (
        later.memory_revision(side="memory-candidate", record="INV-GONE", path=RECORD_FILE) is None
    ), "the file holds another record"
    assert (
        later.memory_revision(
            side="memory-candidate", record="INV-AAA", path="knowledge/invariants/gone.json"
        )
        is None
    )
    assert later.memory_revision(side="code-candidate", record="INV-AAA", path=RECORD_FILE) is None


# --- rule 8: the proof-entry row --------------------------------------------------------------


def test_a_proof_entry_answers_its_fingerprint_and_recorded_execution(tmp_path: Path) -> None:
    trees = Trees(tmp_path)
    held = ExplainerTargetReads(binding=trees.binding()).proof_entry(
        proof="PRF-ONE", record="INV-AAA"
    )
    assert held is not None
    assert held.execution == "established"
    assert held.fingerprint == proof_entry_fingerprint({**PROOF, "execution": "established"})
    later = ExplainerTargetReads(binding=trees.target()).proof_entry(
        proof="PRF-ONE", record="INV-AAA"
    )
    assert later is not None
    assert later.execution == "claimed"
    assert later.fingerprint != held.fingerprint, "the entry's own content moved"
    assert (
        ExplainerTargetReads(binding=trees.target()).proof_entry(proof="PRF-ONE", record="INV-BBB")
        is None
    )


def test_a_proof_entry_is_also_named_by_its_sidecar_when_it_records_no_id(tmp_path: Path) -> None:
    memory = _repository(tmp_path, "memory")
    files = _memory_files(revision=3, proof=PROOF, execution="established")
    files[SIDECAR] = {
        "schema": "ar-onboarding-file/v1",
        "path": "src/app.py",
        "proves": [{"invariant": "INV-AAA", "facet": "it holds"}],
    }
    tree = _tree(memory, files)
    entry = ExplainerTargetReads(binding=_single_binding(memory, tree)).proof_entry(
        proof=f"{SIDECAR}#INV-AAA", record="INV-AAA"
    )
    assert entry is not None and entry.execution == "none"


# --- rule 8: the judgment-row and history-event rows ------------------------------------------


def test_judgment_rows_answer_every_attempt_and_only_that_records_rows(tmp_path: Path) -> None:
    trees = Trees(tmp_path)
    held = ExplainerTargetReads(binding=trees.binding()).judgment_rows(record="INV-AAA")
    assert held is not None
    assert set(held) == {("ROW-1", 1), ("ROW-1", 2)}, (
        "both attempts of the row, each with its file's"
    )
    assert ExplainerTargetReads(binding=trees.binding()).judgment_rows(record="INV-BBB") == (
        ("ROW-OTHER", 1),
    )
    assert ExplainerTargetReads(binding=trees.binding()).judgment_rows(record="INV-NONE") == ()


def test_a_history_file_answers_its_row_ids_and_an_absent_file_holds_no_rows(
    tmp_path: Path,
) -> None:
    trees = Trees(tmp_path)
    reads = ExplainerTargetReads(binding=trees.binding())
    assert reads.history_rows(path=FIRST_HISTORY) == ("ROW-1", "ROW-OTHER")
    assert reads.history_rows(path=SECOND_HISTORY) == ("ROW-1",)
    assert reads.history_rows(path="knowledge/history/260928-MIK-L07-attempt-9.json") == (), (
        "a file the target's history does not hold holds no row, which is a stale row"
    )


# --- rule 8: the supplemental-receipt row and the manifest's version ---------------------------


def test_the_targets_receipts_are_the_packets_and_an_unread_packet_answers_none(
    tmp_path: Path,
) -> None:
    trees = Trees(tmp_path)
    receipt = _receipt("RCP-ONE", "requirements/MIK-R187-v1-per-part.md", "a" * 64)
    assert ExplainerTargetReads(binding=trees.binding(), supplemental=(receipt,)).receipts() == (
        receipt,
    )
    assert ExplainerTargetReads(binding=trees.binding()).receipts() is None


def test_a_later_requirements_version_is_registered_and_the_recorded_one_is_not(
    tmp_path: Path,
) -> None:
    task_root = tmp_path / "task"
    (task_root / "requirements").mkdir(parents=True)
    (task_root / "requirements" / "manifest.json").write_text(
        json.dumps({"packets": [{"id": "MIK-R187", "version": "2"}]}), encoding="utf-8"
    )
    trees = Trees(tmp_path)
    reads = ExplainerTargetReads(binding=trees.binding(), task_root=task_root)
    assert reads.registered_version(source="requirements/MIK-R187-v1-per-part.md") == 2
    assert reads.registered_version(source="requirements/MIK-R999-v1-other.md") is None
    assert source_version("requirements/MIK-R187-v1-per-part.md") == ("MIK-R187", 1)

    (task_root / "requirements" / "manifest.json").unlink()
    assert (
        ExplainerTargetReads(binding=trees.binding(), task_root=task_root).registered_version(
            source="requirements/MIK-R187-v1-per-part.md"
        )
        is None
    )


# --- the failure clause -----------------------------------------------------------------------


def test_every_question_answers_none_when_the_trees_cannot_be_read(tmp_path: Path) -> None:
    trees = Trees(tmp_path)
    gone = FrameBinding.from_record(
        ReviewTreeComparisonRecord(
            task_id=TASK,
            leaf_id=LEAF,
            number=8,
            code_base=ReviewTreeSide(repository=str(tmp_path / "gone"), tree="a" * 40),
            code_candidate=ReviewTreeSide(repository=str(tmp_path / "gone"), tree="b" * 40),
            memory_base=ReviewTreeSide(repository=str(tmp_path / "gone"), tree="c" * 40),
            memory_candidate=ReviewTreeSide(repository=str(tmp_path / "gone"), tree="d" * 40),
            recorded_at=AT,
        ),
        comparison_owner="recorded",
        comparison_reference=f"review-comparison:{TASK}/{LEAF}/8",
    )
    reads = ExplainerTargetReads(binding=gone)
    answers: Iterable[object] = (
        reads.content_identity(
            side="code-candidate", tree=trees.code_new, path="src/app.py", lines=None
        ),
        reads.mapped_lines(
            side="code-candidate",
            path="src/app.py",
            from_tree=trees.code_old,
            to_tree=trees.code_new,
            lines=(1, 2),
        ),
        reads.memory_revision(side="memory-candidate", record="INV-AAA", path=RECORD_FILE),
        reads.proof_entry(proof="PRF-ONE", record="INV-AAA"),
        reads.judgment_rows(record="INV-AAA"),
        reads.history_rows(path=FIRST_HISTORY),
        reads.receipts(),
    )
    assert all(answer is None for answer in answers)


# --- the fixture: two repositories, four trees ------------------------------------------------


def _comparison(
    code: Path,
    memory: Path,
    trees: tuple[str, str],
    *,
    number: int,
    identity: tuple[str, str | None] = (COMPARISON_OWNER, None),
) -> FrameBinding:
    """One comparison of these two repositories at these two trees, with its own identity."""

    owner, reference = identity
    code_tree, memory_tree = trees
    record = ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=number,
        code_base=ReviewTreeSide(repository=code.as_posix(), tree=code_tree),
        code_candidate=ReviewTreeSide(repository=code.as_posix(), tree=code_tree),
        memory_base=ReviewTreeSide(repository=memory.as_posix(), tree=memory_tree),
        memory_candidate=ReviewTreeSide(repository=memory.as_posix(), tree=memory_tree),
        recorded_at=AT,
    )
    return FrameBinding.from_record(
        record,
        comparison_owner=owner,
        comparison_reference=reference or f"review-comparison:{TASK}/{LEAF}/{number}",
    )


class World:
    """Two repositories and the two comparisons a moved leaf has: #7 (old) and #8 (new)."""

    def __init__(self, tmp_path: Path) -> None:
        self.task_root = tmp_path / "task"
        self.task_root.mkdir()
        code = tmp_path / "code"
        code.mkdir()
        _git(code, "init", "--quiet")
        memory = tmp_path / "memory"
        memory.mkdir()
        _git(memory, "init", "--quiet")
        self.code_old = _tree(code, OLD_CODE)
        self.code_new = _tree(code, NEW_CODE)
        self.memory_old = _tree(memory, _memory(revision=3))
        self.memory_new = _tree(memory, _memory(revision=4))
        self.retained = _comparison(code, memory, (self.code_old, self.memory_old), number=7)
        self.target = _comparison(code, memory, (self.code_new, self.memory_new), number=8)
        (self.task_root / "requirements").mkdir()
        (self.task_root / "requirements" / "manifest.json").write_text(
            json.dumps({"packets": [{"id": "MIK-R187", "version": "2"}]}), encoding="utf-8"
        )

    def reads(self, *, supplemental: tuple[SupplementalReceipt, ...] | None = None):
        return ExplainerTargetReads(
            binding=self.target, task_root=self.task_root, supplemental=supplemental
        )

    def target_comparison(self, *, supplemental: tuple[SupplementalReceipt, ...] | None = None):
        return screen_target(
            task_root=self.task_root, on_screen=self.target, supplemental=supplemental
        )


def _memory(*, revision: int) -> dict[str, object]:
    """One memory tree.

    The later revision is the target: it holds revision 4 of INV-AAA, a claimed proof entry, and a
    history file whose second row was removed -- the three moves the claims resting on them see.
    """

    rows = [{"id": "ROW-1", "subject": "INV-AAA", "disposition": "changed"}]
    if revision == 3:
        rows.append({"id": "ROW-GONE", "subject": "INV-CCC", "disposition": "changed"})
    return {
        RECORD_FILE: {"id": "INV-AAA", "kind": "invariant", "revision": revision},
        OTHER_RECORD_FILE: {"id": "INV-BBB", "kind": "invariant", "revision": 2},
        FIRST_HISTORY: {"owner": "260928-MIK-L07", "attempt": 1, "rows": rows},
        SECOND_HISTORY: {
            "owner": "260928-MIK-L07",
            "attempt": 2,
            "rows": [{"id": "ROW-1", "subject": "INV-AAA", "disposition": "extended"}],
        },
        SIDECAR: {
            "schema": "ar-onboarding-file/v1",
            "path": "src/app.py",
            "proves": [
                {
                    "id": PROOF["id"],
                    "invariant": PROOF["invariant"],
                    "facet": "it holds" if revision == 3 else "it holds after the change",
                    "execution": "established" if revision == 3 else "claimed",
                },
                {
                    "id": "PRF-TWO",
                    "invariant": PROOF["invariant"],
                    "facet": "it also holds",
                    "execution": "established",
                },
            ],
        },
        "onboarding/src/app.py.md": "# src/app.py onboarding\n\nPaired Markdown file.\n",
    }


# --- the claims and their eleven parts -----------------------------------------------------------


def _blob(path: str, tree: str, *, lines: tuple[int, int] | None = None) -> ClaimBasis:
    """One ``tree-blob`` basis of the recorded candidate tree, over ``lines`` or the whole blob."""

    return ClaimBasis(
        kind="tree-blob",
        reference=f"code-candidate:{path}",
        state="present",
        side="code-candidate",
        tree=tree,
        path=path,
        start_line=None if lines is None else lines[0],
        end_line=None if lines is None else lines[1],
        comparison_number=7,
    )


def _claim(identifier: str, *bases: ClaimBasis, classification: str = "presenter-inference"):
    return EvidenceClaim(
        claim_id=identifier,
        authoring_revision="rev-1",
        classification=classification,  # type: ignore[arg-type]
        subject="the change to src/app.py",
        applicability="this comparison",
        statement="a claim about the change",
        bases=bases,
        limit="the question stays open" if classification == "unresolved-question" else None,
    )


def _claims(world: World) -> tuple[EvidenceClaim, ...]:
    """Every row of rule 8's table, each claim in the part that carries it."""

    def blob(path: str, identifier: str, *, lines: tuple[int, int] | None = None):
        return _claim(identifier, _blob(path, world.code_old, lines=lines))

    return (
        blob("src/app.py", "c-blob-changed"),
        blob("docs/notes.md", "c-blob-current"),
        blob("src/app.py", "c-range", lines=(4, 6)),
        blob("src/app.py", "c-unmappable", lines=(7, 8)),
        _claim(
            "c-record",
            ClaimBasis(
                kind="knowledge-record",
                reference="memory-candidate:INV-AAA",
                state="present",
                side="memory-candidate",
                record_id="INV-AAA",
                record_revision=3,
                path=RECORD_FILE,
            ),
        ),
        _claim(
            "c-record-current",
            ClaimBasis(
                kind="knowledge-record",
                reference="memory-candidate:INV-BBB",
                state="present",
                side="memory-candidate",
                record_id="INV-BBB",
                record_revision=2,
                path=OTHER_RECORD_FILE,
            ),
        ),
        _claim(
            "c-record-gone",
            ClaimBasis(
                kind="knowledge-record",
                reference="memory-candidate:INV-GONE",
                state="present",
                side="memory-candidate",
                record_id="INV-GONE",
                record_revision=1,
                path="knowledge/invariants/INV-GONE-one.json",
            ),
        ),
        _claim(
            "c-proof",
            ClaimBasis(
                kind="proof-entry",
                reference="memory-candidate:onboarding/src/app.py.json#PRF-ONE",
                state="present",
                proof_id=PROOF["id"],
                record_id=PROOF["invariant"],
                execution="established",
            ),
        ),
        _claim(
            "c-proof-current",
            ClaimBasis(
                kind="proof-entry",
                reference="memory-candidate:onboarding/src/app.py.json#PRF-TWO",
                state="present",
                proof_id="PRF-TWO",
                record_id=PROOF["invariant"],
                execution="established",
            ),
        ),
        _claim(
            "c-judgment",
            ClaimBasis(
                kind="judgment-row",
                reference=f"memory-candidate:{FIRST_HISTORY}#ROW-1@1",
                state="present",
                side="memory-candidate",
                path=FIRST_HISTORY,
                record_id="INV-AAA",
                row_id="ROW-1",
                attempt=1,
            ),
        ),
        _claim(
            "c-judgment-current",
            ClaimBasis(
                kind="judgment-row",
                reference=f"memory-candidate:{FIRST_HISTORY}#ROW-1@2",
                state="present",
                side="memory-candidate",
                path=FIRST_HISTORY,
                record_id="INV-AAA",
                row_id="ROW-1",
                attempt=2,
            ),
        ),
        _claim(
            "c-history",
            ClaimBasis(
                kind="history-event",
                reference=f"memory-candidate:{FIRST_HISTORY}#ROW-1",
                state="present",
                side="memory-candidate",
                path=FIRST_HISTORY,
                row_id="ROW-1",
            ),
        ),
        _claim(
            "c-history-gone",
            ClaimBasis(
                kind="history-event",
                reference=f"memory-candidate:{FIRST_HISTORY}#ROW-GONE",
                state="present",
                side="memory-candidate",
                path=FIRST_HISTORY,
                row_id="ROW-GONE",
            ),
        ),
        _claim(
            "c-field",
            ClaimBasis(
                kind="comparison-field",
                reference="comparison:7",
                state="present",
                comparison_number=7,
            ),
        ),
        _claim("c-nobasis", classification="unresolved-question"),
        *(
            _claim(
                identifier,
                ClaimBasis(
                    kind="supplemental-receipt",
                    reference=receipt,
                    state="present",
                    receipt_id=receipt,
                ),
            )
            for identifier, receipt in (
                ("c-receipt-changed", "RCP-CHANGED"),
                ("c-receipt-current", "RCP-CURRENT"),
                ("c-receipt-later", "RCP-LATER"),
            )
        ),
    )


PART_MAP = (
    FramePart(part="changed-blob", title="The file that moved", claims=("c-blob-changed",)),
    FramePart(part="same-blob", title="The note that stayed", claims=("c-blob-current",)),
    FramePart(part="shifted-range", title="The shifted range", claims=("c-range",)),
    FramePart(part="deleted-range", title="The deleted range", claims=("c-unmappable",)),
    FramePart(
        part="records",
        title="The records",
        claims=("c-record", "c-record-gone", "c-record-current"),
    ),
    FramePart(part="proofs", title="The proofs", claims=("c-proof", "c-proof-current")),
    FramePart(
        part="judgments",
        title="The judgments",
        claims=("c-judgment", "c-judgment-current"),
    ),
    FramePart(part="history", title="The history", claims=("c-history", "c-history-gone")),
    FramePart(part="fields", title="The comparison fields", claims=("c-field",)),
    FramePart(part="open", title="The open question", claims=("c-nobasis",)),
    FramePart(
        part="receipts",
        title="The packets",
        claims=("c-receipt-changed", "c-receipt-current", "c-receipt-later"),
    ),
)
STALE_PARTS = (
    "changed-blob",
    "deleted-range",
    "records",
    "proofs",
    "judgments",
    "history",
    "fields",
    "open",
    "receipts",
)


def _retained_receipts() -> tuple[SupplementalReceipt, ...]:
    return (
        _receipt("RCP-CHANGED", MOVED_SOURCE, "c" * 64),
        _receipt("RCP-CURRENT", STEADY_SOURCE, DIGEST),
        _receipt("RCP-LATER", REGISTERED_SOURCE, DIGEST),
    )


def _target_receipts() -> tuple[SupplementalReceipt, ...]:
    """The target's packet: the moved source has new bytes, the others are the same bytes."""

    return (
        _receipt("RCP-CHANGED", MOVED_SOURCE, "d" * 64),
        _receipt("RCP-CURRENT", STEADY_SOURCE, DIGEST),
        _receipt("RCP-LATER", REGISTERED_SOURCE, DIGEST),
    )


def _manifest(world: World, claims, part_map=PART_MAP) -> FrameManifest:
    receipts = _retained_receipts()
    comparison = ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=7,
        code_base=ReviewTreeSide(
            repository=world.retained.code_base_repository, tree=world.code_old
        ),
        code_candidate=ReviewTreeSide(
            repository=world.retained.code_candidate_repository, tree=world.code_old
        ),
        memory_base=ReviewTreeSide(
            repository=world.retained.memory_base_repository, tree=world.memory_old
        ),
        memory_candidate=ReviewTreeSide(
            repository=world.retained.memory_candidate_repository, tree=world.memory_old
        ),
        recorded_at=AT,
    )
    evidence = build_evidence_packet(
        comparison,
        repository_id="agents-remember",
        scope="the change to src/app.py",
        question="why did it move?",
        code_base=SideState("available"),
        code_candidate=SideState("available"),
        memory_before=SideState("available", index_state="complete"),
        memory_after=SideState("available", index_state="complete"),
        evidence_map=EvidenceMap(coverage="the selected change"),
        supplemental=receipts,
        claims=tuple(claims),
        recorded_at=AT,
    )
    assets = (FrameAsset(path="index.html", media_type="text/html", byte_size=24, digest="e" * 64),)
    return FrameManifest(
        frame_id="the-page",
        revision=3,
        binding=FrameBinding.from_packet(evidence.comparison),
        focus="the change to src/app.py",
        authoring=FrameAuthoring(request_id="req-1", started_at=AT),
        created_at=AT,
        entrypoint=FrameEntrypoint(path="index.html"),
        assets=assets,
        artifact_digest=artifacts_digest(assets),
        evidence=evidence,
        supplemental=receipts,
        part_map=part_map,
    )


def _steady_manifest(world: World) -> FrameManifest:
    """A page whose two bases already resolve at the retained comparison itself."""

    claims = (
        _claim(
            "c-field-current",
            ClaimBasis(
                kind="comparison-field",
                reference="comparison:7",
                state="present",
                comparison_number=7,
            ),
        ),
        _claim("c-blob-current", _blob("docs/notes.md", world.code_old)),
        _claim(
            "c-record-current",
            ClaimBasis(
                kind="knowledge-record",
                reference="memory-candidate:INV-BBB",
                state="present",
                side="memory-candidate",
                record_id="INV-BBB",
                record_revision=2,
                path=OTHER_RECORD_FILE,
            ),
        ),
    )
    return _manifest(
        world,
        claims,
        part_map=(
            FramePart(part="fields", title="The comparison field", claims=("c-field-current",)),
            FramePart(part="same-blob", title="The note that stayed", claims=("c-blob-current",)),
            FramePart(part="records", title="The records", claims=("c-record-current",)),
        ),
    )


def _read(world: World, *, grades: GradeInputs | None = None):
    manifest = _manifest(world, _claims(world))
    return staleness_read(
        task_root=world.task_root,
        leaf_id=LEAF,
        manifest=manifest,
        target=world.target_comparison(supplemental=_target_receipts()),
        grades=grades,
    )


# --- rule 8's table, one page -----------------------------------------------------------------


def test_each_stale_basis_names_its_own_part_and_the_rest_still_resolve(tmp_path: Path) -> None:
    read = _read(World(tmp_path))
    assert read.parts.part_ids == STALE_PARTS
    assert read.parts.whole_page is False
    assert read.parts.checked == len(PART_MAP)
    reasons = {part.part: " ".join(part.reasons) for part in read.parts.parts}
    assert "the code-candidate bytes of src/app.py changed" in reasons["changed-blob"]
    assert "maps to no range" in reasons["deleted-range"]
    assert "is at revision 4" in reasons["records"]
    assert "holds no record 'INV-GONE'" in reasons["records"]
    assert "is claimed at the target" in reasons["proofs"]
    assert "later than the recorded attempt 1" in reasons["judgments"]
    assert "holds no row 'ROW-GONE'" in reasons["history"]
    assert "any side that moves makes it stale" in reasons["fields"]
    assert "names no basis" in reasons["open"]
    assert "holds no receipt of" in reasons["receipts"]
    assert "registers version 2 of" in reasons["receipts"]
    current = {
        "c-blob-current",
        "c-range",
        "c-record-current",
        "c-proof-current",
        "c-judgment-current",
        "c-history",
        "c-receipt-current",
    }
    stale_claims = {claim for part in read.parts.parts for claim in part.claims}
    assert not current & stale_claims, "every current basis of every row stays out of the list"


def test_the_trusted_list_states_every_stale_part_and_the_current_parts_stay_out(
    tmp_path: Path,
) -> None:
    read = _read(World(tmp_path))
    assert read.trusted.stale is True
    assert read.trusted.lines[0].startswith("revision 3 is stale in 9 part(s) against")
    assert read.trusted.target == f"review-comparison:{TASK}/{LEAF}/8"
    listed = [line.split(" (")[0] for line in read.trusted.lines[1:]]
    assert listed == [
        "The file that moved",
        "The deleted range",
        "The records",
        "The proofs",
        "The judgments",
        "The history",
        "The comparison fields",
        "The open question",
        "The packets",
    ]
    assert "The note that stayed" not in listed
    assert "The shifted range" not in listed


def test_the_regeneration_text_names_the_parts_their_bases_and_the_stale_grades(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    read = _read(world)
    assert read.regeneration.label == "Regenerate stale (9 parts · 0 grades)"
    assert read.regeneration.part_count == 9
    assert "- The file that moved (changed-blob): tree-blob" in read.regeneration.message
    assert "Nothing regenerates by itself" in read.regeneration.message
    assert read.regeneration.grade_count == 0


def test_the_moved_sides_are_the_comparison_identity_and_the_whole_payload_reads(
    tmp_path: Path,
) -> None:
    read = _read(World(tmp_path))
    assert read.moved == (
        "code-base",
        "code-candidate",
        "memory-base",
        "memory-candidate",
        "comparison",
    )
    record = read.as_record()
    assert record["revision"] == 3 and record["stale"] is True
    assert record["staleParts"] == list(STALE_PARTS)
    assert record["regeneration"]["label"] == "Regenerate stale (9 parts · 0 grades)"
    assert record["binding"]["comparison_number"] == 7


# --- rule 3's grades through the same target --------------------------------------------------


def _card(world: World, path: str) -> BlockFingerprint:
    """One whole-file card's fingerprint at the target, read from the real trees."""

    base = world.reads().content_identity(
        side="code-base", tree=world.code_new, path=path, lines=None
    )
    candidate = world.reads().content_identity(
        side="code-candidate", tree=world.code_new, path=path, lines=None
    )
    block = BlockIdentity.card(
        entry="INV-AAA",
        side="code-candidate",
        path=path,
        lines=None,
        trees=(world.code_old, world.code_old),
    )
    return card_fingerprint(
        block=block,
        base=CardSide(content=base),
        candidate=CardSide(content=candidate),
        locator="file",
    )


def _grade(reference: str, fingerprint: str, *, grade: str = "thin") -> Grade:
    return Grade(
        reference=reference,
        category="risk",
        grade=grade,
        reason="one line",
        fingerprint=fingerprint,
        binding=FrameBinding.from_record(
            ReviewTreeComparisonRecord(
                task_id=TASK,
                leaf_id=LEAF,
                number=7,
                code_base=ReviewTreeSide(repository="/repo", tree="a" * 40),
                code_candidate=ReviewTreeSide(repository="/repo", tree="a" * 40),
                memory_base=ReviewTreeSide(repository="/repo", tree="a" * 40),
                memory_candidate=ReviewTreeSide(repository="/repo", tree="a" * 40),
                recorded_at=AT,
            ),
            comparison_owner=COMPARISON_OWNER,
            comparison_reference=f"review-comparison:{TASK}/{LEAF}/7",
        ),
        made_at=AT,
    )


def test_a_grade_is_current_when_its_blocks_fingerprint_still_matches_at_the_target(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    steady = _card(world, "docs/notes.md")
    moved = _card(world, "src/app.py")
    assert steady.value is not None and moved.value is not None
    grades = GradeInputs(
        grades=Grades(
            leaf_id=LEAF,
            grades=(
                _grade(steady.reference, str(steady.value), grade="sound"),
                _grade(moved.reference, "sha256:as-recorded", grade="wrong"),
            ),
        ),
        fingerprints={steady.reference: steady, moved.reference: moved},
    )
    read = _read(world, grades=grades)
    states = {state.reference: state for state in read.grades}
    assert states[steady.reference].stale is False
    assert states[moved.reference].stale is True
    assert "the block changed" in (states[moved.reference].reason or "")
    assert read.stale_grades == (moved.reference,)
    assert read.regeneration.label == "Regenerate stale (9 parts · 1 grade)"
    assert f"- {moved.reference} (wrong)" in read.regeneration.message
    assert "the block changed" in read.regeneration.message


def test_a_grade_whose_block_was_not_fingerprinted_is_stale_never_current(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    stray = _grade("card-reference-nobody-fingerprinted", "sha256:whatever")
    read = _read(world, grades=GradeInputs(grades=Grades(leaf_id=LEAF, grades=(stray,))))
    assert read.grades[0].stale is True
    assert "cannot be computed" in (read.grades[0].reason or "")
    assert read.regeneration.grade_count == 1


def test_gone_grades_are_the_references_the_target_no_longer_holds(tmp_path: Path) -> None:
    world = World(tmp_path)
    steady = _card(world, "docs/notes.md")
    moved = _card(world, "src/app.py")
    grades = GradeInputs(
        grades=Grades(
            leaf_id=LEAF,
            grades=(_grade(steady.reference, str(steady.value)), _grade(moved.reference, "stale")),
        ),
        fingerprints={steady.reference: steady, moved.reference: moved},
    )
    read = _read(world, grades=grades)
    assert gone_grade_references(read.grades, (steady.reference,)) == (moved.reference,)
    assert gone_grade_references(read.grades, (steady.reference, moved.reference)) == ()


def test_a_proof_entries_changed_fingerprint_makes_its_claim_stale(tmp_path: Path) -> None:
    world = World(tmp_path)
    code_repo = Path(world.retained.code_base_repository)
    mem_repo = Path(world.retained.memory_base_repository)
    test_v1 = "def test_guard():\n    assert 1 == 1\n"
    test_v1_shifted = "# header line 1\n# header line 2\ndef test_guard():\n    assert 1 == 1\n"
    test_v2_changed = "def test_guard():\n    assert 1 == 2\n"
    code_tree_1 = _tree(code_repo, {**OLD_CODE, "tests/test_app.py": test_v1})
    code_tree_shifted = _tree(code_repo, {**OLD_CODE, "tests/test_app.py": test_v1_shifted})
    code_tree_changed = _tree(code_repo, {**OLD_CODE, "tests/test_app.py": test_v2_changed})
    blob_1 = _git(code_repo, "rev-parse", f"{code_tree_1}:tests/test_app.py").strip()
    blob_shifted = _git(code_repo, "rev-parse", f"{code_tree_shifted}:tests/test_app.py").strip()
    test_hash = content_identity(test_v1.encode("utf-8"))
    mem_v1 = {
        **_memory(revision=3),
        "onboarding/tests/test_app.py.md": "# test_app.py\n",
        "onboarding/tests/test_app.py.json": {
            "schema": "ar-onboarding-file/v1",
            "path": "tests/test_app.py",
            "references": {},
            "realizes": [],
            "proves": [
                {
                    "id": "PRF-ANCHORED",
                    "invariant": "INV-AAA",
                    "facet": "it guards the invariant",
                    "anchor": {
                        "locator": {"kind": "line_range", "start": 1, "end": 2},
                        "blob": blob_1,
                        "content": test_hash,
                    },
                }
            ],
        },
    }
    mem_reanchored = {
        **mem_v1,
        "onboarding/tests/test_app.py.json": {
            "schema": "ar-onboarding-file/v1",
            "path": "tests/test_app.py",
            "references": {},
            "realizes": [],
            "proves": [
                {
                    "id": "PRF-ANCHORED",
                    "invariant": "INV-AAA",
                    "facet": "it guards the invariant",
                    "anchor": {
                        "locator": {"kind": "line_range", "start": 3, "end": 4},
                        "blob": blob_shifted,
                        "content": test_hash,
                    },
                }
            ],
        },
    }
    mem_tree_1 = _tree(mem_repo, mem_v1)
    mem_tree_reanchored = _tree(mem_repo, mem_reanchored)
    retained_binding = _comparison(code_repo, mem_repo, (code_tree_1, mem_tree_1), number=7)
    shifted_binding = _comparison(
        code_repo, mem_repo, (code_tree_shifted, mem_tree_reanchored), number=8
    )
    drifted_binding = _comparison(code_repo, mem_repo, (code_tree_changed, mem_tree_1), number=9)

    proof_claim = _claim(
        "c-proof-two",
        ClaimBasis(
            kind="proof-entry",
            reference="memory-candidate:onboarding/tests/test_app.py.json#PRF-ANCHORED",
            state="present",
            proof_id="PRF-ANCHORED",
            record_id="INV-AAA",
            execution="established",
        ),
    )
    manifest = _manifest(
        world,
        (proof_claim,),
        part_map=(FramePart(part="proofs", title="The proofs", claims=("c-proof-two",)),),
    ).model_copy(update={"binding": retained_binding})

    # 1. Identical test content shifted + sidecar re-anchored => current without injected fingerprints
    shifted_read = staleness_read(
        task_root=world.task_root,
        leaf_id=LEAF,
        manifest=manifest,
        target=screen_target(task_root=world.task_root, on_screen=shifted_binding),
    )
    assert shifted_read.parts.parts == ()

    # 2. Unchanged sidecar JSON + modified anchored test code => stale without injected fingerprints
    drifted_read = staleness_read(
        task_root=world.task_root,
        leaf_id=LEAF,
        manifest=manifest,
        target=screen_target(task_root=world.task_root, on_screen=drifted_binding),
    )
    assert drifted_read.parts.part_ids == ("proofs",)
    assert "PRF-ANCHORED" in " ".join(drifted_read.parts.parts[0].reasons)

    # 3. Genuinely malformed selected JSON sidecar => stale with reason
    mem_malformed = {**mem_v1, "onboarding/tests/test_app.py.json": "{not valid json"}
    mem_tree_malformed = _tree(mem_repo, mem_malformed)
    malformed_binding = _comparison(
        code_repo, mem_repo, (code_tree_1, mem_tree_malformed), number=10
    )
    malformed_read = staleness_read(
        task_root=world.task_root,
        leaf_id=LEAF,
        manifest=manifest,
        target=screen_target(task_root=world.task_root, on_screen=malformed_binding),
    )
    assert malformed_read.parts.part_ids == ("proofs",)


# --- rule 4: which comparison the read compares against ---------------------------------------


def test_an_environment_binds_the_target_and_nothing_moved_is_current(tmp_path: Path) -> None:
    world = World(tmp_path)
    manifest = _steady_manifest(world)
    current = staleness_read(
        task_root=world.task_root,
        leaf_id=LEAF,
        manifest=manifest,
        target=screen_target(
            task_root=world.task_root,
            environment=manifest.binding,
            supplemental=_retained_receipts(),
        ),
    )
    assert current.target == manifest.binding.comparison_reference
    assert current.moved == ()
    assert current.parts.parts == ()
    assert current.trusted.stale is False
    assert "every claim basis still resolves" in current.trusted.lines[0]


def test_without_an_environment_the_comparison_on_screen_is_computed_when_read(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    manifest = _manifest(world, _claims(world))
    assert world.reads().content_identity(
        side="code-candidate", tree=world.code_new, path="src/app.py", lines=None
    ) != world.reads().content_identity(
        side="code-candidate", tree=world.code_old, path="src/app.py", lines=None
    )
    read = staleness_read(
        task_root=world.task_root,
        leaf_id=LEAF,
        manifest=manifest,
        target=screen_target(
            task_root=world.task_root,
            on_screen=world.target,
            supplemental=_target_receipts(),
        ),
    )
    assert read.target == f"review-comparison:{TASK}/{LEAF}/8"
    assert read.parts.part_ids == STALE_PARTS


def test_a_read_without_a_comparison_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(ExplainerRefused) as refused:
        screen_target(task_root=tmp_path)
    assert refused.value.reason == "comparison-unavailable"


def test_a_planning_leaf_reads_the_planned_comparison_at_the_current_tips(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    code = Path(world.retained.code_base_repository)
    memory = Path(world.retained.memory_base_repository)
    reference = planned_reference(TASK, LEAF)
    at_old_tips = _comparison(
        code,
        memory,
        (world.code_old, world.memory_old),
        number=0,
        identity=("planned", reference),
    )
    at_new_tips = _comparison(
        code,
        memory,
        (world.code_new, world.memory_new),
        number=0,
        identity=("planned", reference),
    )
    manifest = _planned_manifest(world, at_old_tips)
    assert (
        staleness_read(
            task_root=world.task_root,
            leaf_id=LEAF,
            manifest=manifest,
            target=screen_target(task_root=world.task_root, on_screen=at_old_tips),
        ).parts.parts
        == ()
    )
    moved = staleness_read(
        task_root=world.task_root,
        leaf_id=LEAF,
        manifest=manifest,
        target=screen_target(task_root=world.task_root, on_screen=at_new_tips),
    )
    assert moved.binding.comparison_owner == "planned"
    assert moved.binding.comparison_number == 0
    assert moved.target.endswith("/planned")
    assert moved.moved == ("code-base", "code-candidate", "memory-base", "memory-candidate")
    assert moved.parts.part_ids == ("intent",)
    assert "the code-candidate bytes of src/app.py changed" in moved.parts.parts[0].reasons[0]


def test_a_revision_or_a_comparison_of_another_leaf_is_refused(tmp_path: Path) -> None:
    world = World(tmp_path)
    manifest = _manifest(world, _claims(world))
    with pytest.raises(ExplainerRefused) as wrong_leaf:
        staleness_read(
            task_root=world.task_root,
            leaf_id="260928-MIK-L999",
            manifest=manifest,
            target=world.target_comparison(supplemental=_target_receipts()),
        )
    assert wrong_leaf.value.reason == "revision-foreign"
    foreign = _comparison(
        Path(world.retained.code_base_repository),
        Path(world.retained.memory_base_repository),
        (world.code_new, world.memory_new),
        number=8,
    ).model_copy(update={"leaf_id": "260928-MIK-L999"})
    with pytest.raises(ExplainerRefused) as other_leaf:
        staleness_read(
            task_root=world.task_root,
            leaf_id=LEAF,
            manifest=manifest,
            target=screen_target(task_root=world.task_root, on_screen=foreign),
        )
    assert other_leaf.value.reason == "comparison-foreign"


# --- an older revision: no part map, and a map that admits no claim ---------------------------


def _planned_manifest(world: World, binding: FrameBinding) -> FrameManifest:
    """One planned page: a single claim resting on the code tip's own bytes."""

    comparison = ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=0,
        code_base=ReviewTreeSide(
            repository=binding.code_base_repository, tree=binding.code_base_tree
        ),
        code_candidate=ReviewTreeSide(
            repository=binding.code_candidate_repository, tree=binding.code_candidate_tree
        ),
        memory_base=ReviewTreeSide(
            repository=binding.memory_base_repository, tree=binding.memory_base_tree
        ),
        memory_candidate=ReviewTreeSide(
            repository=binding.memory_candidate_repository, tree=binding.memory_candidate_tree
        ),
        recorded_at="planned-master-tip",
    )
    claim = _claim(
        "c-intent",
        ClaimBasis(
            kind="tree-blob",
            reference="code-candidate:src/app.py",
            state="present",
            side="code-candidate",
            tree=world.code_old,
            path="src/app.py",
            comparison_number=0,
        ),
    )
    claims = (claim,)
    evidence = build_evidence_packet(
        comparison,
        repository_id="agents-remember",
        scope="the intended change",
        question="what is meant to change?",
        code_base=SideState("available"),
        code_candidate=SideState("available"),
        memory_before=SideState("available", index_state="complete"),
        memory_after=SideState("available", index_state="complete"),
        evidence_map=EvidenceMap(coverage="the intended change"),
        claims=claims,
        recorded_at=AT,
    )
    assets = (FrameAsset(path="index.html", media_type="text/html", byte_size=24, digest="e" * 64),)
    return FrameManifest(
        frame_id="the-planned-page",
        revision=1,
        binding=binding,
        focus="the intended change",
        authoring=FrameAuthoring(request_id="req-1", started_at=AT),
        created_at=AT,
        entrypoint=FrameEntrypoint(path="index.html"),
        assets=assets,
        artifact_digest=artifacts_digest(assets),
        evidence=evidence,
        part_map=(FramePart(part="intent", title="The intent", claims=("c-intent",)),),
    )


def test_a_revision_without_a_part_map_is_stale_as_a_whole(tmp_path: Path) -> None:
    world = World(tmp_path)
    manifest = _manifest(world, _claims(world), part_map=())
    read = staleness_read(
        task_root=world.task_root,
        leaf_id=LEAF,
        manifest=manifest,
        target=world.target_comparison(supplemental=_target_receipts()),
    )
    assert read.parts.whole_page is True
    assert read.parts.part_ids == ("page",)
    assert read.trusted.lines[0].startswith("revision 3 is stale as a whole")
    assert read.regeneration.part_count == 1


def _file_state(*roots: Path) -> dict[str, int]:
    """Every file under these roots with its size: a read that writes changes one of them."""

    return {
        f"{root.as_posix()}/{path.relative_to(root).as_posix()}": path.stat().st_size
        for root in roots
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_a_staleness_read_writes_nothing_in_the_repositories_or_the_task(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    roots = (
        Path(world.retained.code_base_repository),
        Path(world.retained.memory_base_repository),
        world.task_root,
    )
    before = _file_state(*roots)
    read = _read(world)
    assert read.parts.parts, "the read did answer from those repositories"
    assert _file_state(*roots) == before, "no file of a repository or of the task was written"


def test_the_leafs_store_is_read_when_the_caller_passes_no_grades(tmp_path: Path) -> None:
    world = World(tmp_path)
    stray = _grade("card-reference-nobody-fingerprinted", "sha256:whatever")
    store = grades_path(world.task_root, LEAF)
    store.parent.mkdir(parents=True)
    store.write_text(
        Grades(leaf_id=LEAF, grades=(stray,)).model_dump_json(by_alias=True), encoding="utf-8"
    )
    read = staleness_read(
        task_root=world.task_root,
        leaf_id=LEAF,
        manifest=_manifest(world, _claims(world)),
        target=world.target_comparison(supplemental=_target_receipts()),
    )
    assert [state.reference for state in read.grades] == [stray.reference]
    assert read.grades[0].stale is True, "no fingerprint was passed, so no grade reads current"
    assert "cannot be computed" in (read.grades[0].reason or "")


def test_a_part_map_that_admits_no_claim_leaves_the_page_stale_as_a_whole(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    manifest = _manifest(
        world,
        _claims(world),
        part_map=(FramePart(part="change", title="The change"),),
    )
    read = staleness_read(
        task_root=world.task_root,
        leaf_id=LEAF,
        manifest=manifest,
        target=world.target_comparison(supplemental=_target_receipts()),
    )
    assert read.parts.whole_page is True
    assert read.parts.part_ids == ("page",)
    assert len(read.parts.unmapped_claims) == len(_claims(world))


def test_native_grade_reconciliation_handles_shifts_merges_and_gone_pruning_on_grade_only_leaf(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    code_repo = Path(world.retained.code_base_repository)
    mem_repo = Path(world.retained.memory_base_repository)
    base_files = {
        "src/mod.py": (
            "def keep_sym():\n"
            "    return 42\n"
            "\n"
            "def range_fn():\n"
            "    return 7\n"
            "\n"
            "def file_fn():\n"
            "    return 1\n"
        ),
        "src/hunks.py": "a1\na2\na3\na4\na5\na6\n",
    }
    cand_v1_files = {
        "src/mod.py": base_files["src/mod.py"],
        "src/hunks.py": "b1\na2\na3\na4\nb5\na6\n",
        "src/gone.py": "only_in_v1 = True\n",
    }
    cand_v2_files = {
        "src/mod.py": (
            "# added comment line 1\n"
            "# added comment line 2\n"
            "def keep_sym():\n"
            "    return 42\n"
            "\n"
            "def range_fn():\n"
            "    return 7\n"
            "\n"
            "def file_fn():\n"
            "    return 99\n"
        ),
        "src/hunks.py": "c1\nc2\nc3\nc4\nc5\na6\n",
    }
    base_tree = _tree(code_repo, base_files)
    cand_v1_tree = _tree(code_repo, cand_v1_files)
    cand_v2_tree = _tree(code_repo, cand_v2_files)
    mod_blob_v1 = _git(code_repo, "rev-parse", f"{cand_v1_tree}:src/mod.py").strip()
    mod_v1_bytes = cand_v1_files["src/mod.py"].encode("utf-8")
    mem_files = {
        **_memory(revision=3),
        "knowledge/invariants/INV-BBB-one.json": {
            "id": "INV-BBB",
            "kind": "invariant",
            "revision": 2,
        },
        "onboarding/src/mod.py.json": {
            "schema": "ar-onboarding-file/v1",
            "path": "src/mod.py",
            "references": {},
            "realizes": [
                {
                    "id": "RLZ-SYM",
                    "invariant": "INV-BBB",
                    "role": "primary-authority",
                    "rationale": "symbol",
                    "anchor": {
                        "locator": {"kind": "symbol", "name": "keep_sym"},
                        "blob": mod_blob_v1,
                        "content": content_identity(range_bytes(mod_v1_bytes, 1, 2)),
                    },
                },
                {
                    "id": "RLZ-RNG",
                    "invariant": "INV-BBB",
                    "role": "support",
                    "rationale": "range",
                    "anchor": {
                        "locator": {"kind": "line_range", "start": 4, "end": 5},
                        "blob": mod_blob_v1,
                        "content": content_identity(range_bytes(mod_v1_bytes, 4, 5)),
                    },
                },
                {
                    "id": "RLZ-FIL",
                    "invariant": "INV-BBB",
                    "role": "support",
                    "rationale": "file",
                    "anchor": {
                        "locator": {"kind": "file"},
                        "blob": mod_blob_v1,
                        "content": content_identity(mod_v1_bytes),
                    },
                },
            ],
        },
    }
    mem_tree = _tree(mem_repo, mem_files)
    binding_v1 = FrameBinding.from_record(
        ReviewTreeComparisonRecord(
            task_id=TASK,
            leaf_id=LEAF,
            number=1,
            code_base=ReviewTreeSide(repository=code_repo.as_posix(), tree=base_tree),
            code_candidate=ReviewTreeSide(repository=code_repo.as_posix(), tree=cand_v1_tree),
            memory_base=ReviewTreeSide(repository=mem_repo.as_posix(), tree=mem_tree),
            memory_candidate=ReviewTreeSide(repository=mem_repo.as_posix(), tree=mem_tree),
            recorded_at=AT,
        ),
        comparison_owner=COMPARISON_OWNER,
        comparison_reference=f"review-comparison:{TASK}/{LEAF}/1",
    )
    binding_v2 = FrameBinding.from_record(
        ReviewTreeComparisonRecord(
            task_id=TASK,
            leaf_id=LEAF,
            number=2,
            code_base=ReviewTreeSide(repository=code_repo.as_posix(), tree=base_tree),
            code_candidate=ReviewTreeSide(repository=code_repo.as_posix(), tree=cand_v2_tree),
            memory_base=ReviewTreeSide(repository=mem_repo.as_posix(), tree=mem_tree),
            memory_candidate=ReviewTreeSide(repository=mem_repo.as_posix(), tree=mem_tree),
            recorded_at=AT,
        ),
        comparison_owner=COMPARISON_OWNER,
        comparison_reference=f"review-comparison:{TASK}/{LEAF}/2",
    )
    reads_v1 = ExplainerTargetReads(binding=binding_v1, task_root=world.task_root)
    hunks_v1 = reads_v1.file_hunks("src/hunks.py")
    assert hunks_v1 is not None and len(hunks_v1) == 2
    gone_hunks = reads_v1.file_hunks("src/gone.py")
    assert gone_hunks is not None and len(gone_hunks) == 1

    sym_ref = BlockIdentity.card(
        entry="RLZ-SYM",
        side="after",
        path="src/mod.py",
        lines=(1, 2),
        trees=(base_tree, cand_v1_tree, mem_tree, mem_tree),
    ).block_key()
    rng_ref = BlockIdentity.card(
        entry="RLZ-RNG",
        side="after",
        path="src/mod.py",
        lines=(4, 5),
        trees=(base_tree, cand_v1_tree, mem_tree, mem_tree),
    ).block_key()
    fil_ref = BlockIdentity.card(
        entry="RLZ-FIL",
        side="after",
        path="src/mod.py",
        lines=(1, 8),
        trees=(base_tree, cand_v1_tree, mem_tree, mem_tree),
    ).block_key()
    inv_ref = BlockIdentity.record_at(kind="invariant", record="INV-BBB", revision=2).reference()
    hunk_1_ref = hunks_v1[0].identity().block_key()
    hunk_2_ref = hunks_v1[1].identity().block_key()
    gone_ref = gone_hunks[0].identity().block_key()

    all_refs = (sym_ref, rng_ref, fil_ref, inv_ref, hunk_1_ref, hunk_2_ref, gone_ref)
    fps_v1 = reads_v1.resolve_fingerprints(all_refs)
    assert set(fps_v1) == set(all_refs)

    store = GradeStore(path=grades_path(world.task_root, LEAF), leaf_id=LEAF)
    store.define(
        "risk",
        (ScalePoint(grade="sound", meaning="good"), ScalePoint(grade="thin", meaning="weak")),
        meaning="change risk",
    )
    store.grade(
        references=(sym_ref, rng_ref, fil_ref, inv_ref, hunk_1_ref, gone_ref),
        category="risk",
        grade="thin",
        reason="initial grade",
        fingerprints=fps_v1,
        binding=binding_v1,
        at=AT,
    )
    store.grade(
        references=(hunk_2_ref,),
        category="risk",
        grade="sound",
        reason="second hunk is sound",
        fingerprints=fps_v1,
        binding=binding_v1,
        at=AT,
    )

    reconciled = reconcile_grades(
        world.task_root,
        LEAF,
        screen_target(task_root=world.task_root, on_screen=binding_v2),
        prune=True,
        active_category="risk",
    )
    assert reconciled.removed_count == 1
    assert reconciled.gone_grades == (gone_ref,)
    assert len(reconciled.carried_hunks) == 1
    carry = reconciled.carried_hunks[0]
    assert carry.chip == "sound"
    assert [item["grade"] for item in carry.hover] == ["sound", "thin"]

    by_entry = {
        parsed.entry: s
        for s in reconciled.states
        if (parsed := parse_block_identity(s.reference)) is not None and parsed.entry is not None
    }
    assert by_entry["RLZ-SYM"].stale is False
    assert by_entry["RLZ-RNG"].stale is False
    assert by_entry["RLZ-FIL"].stale is True
    inv_state = next(s for s in reconciled.states if s.reference == inv_ref)
    assert inv_state.stale is False


def test_an_edited_moved_or_purely_deleted_hunk_keeps_its_grade_on_the_target_block(
    tmp_path: Path,
) -> None:
    """Rule 3 and its evidence: an edited hunk keeps its identity and turns stale; a hunk whose
    changed lines moved keeps its grade on the moved target span, stale; a pure deletion is
    identified by its base span -- stale when the deletion grows, current when it is unchanged.
    """

    world = World(tmp_path)
    code_repo = Path(world.retained.code_base_repository)
    mem_repo = Path(world.retained.memory_base_repository)
    base_files = {
        "src/h.py": "a1\na2\na3\na4\na5\na6\na7\na8\n",
        "src/d.py": "a1\na2\na3\na4\na5\na6\n",
    }
    v1_files = {
        "src/h.py": "b1\na2\na3\na4\nb5\na6\na7\na8\n",  # two hunks: lines 1 and 5 changed
        "src/d.py": "a1\na2\na3\na4\n",  # lines 5-6 deleted: a pure deletion
    }
    v2_files = {
        # hunk one edited again (b1 -> c1); hunk two's changed line b5 moved from 5 to 7.
        "src/h.py": "c1\na2\na3\na4\na5\na6\nb5\na8\n",
        "src/d.py": "a1\na2\n",  # the deletion grew: lines 3-4 are gone too
    }
    base_tree = _tree(code_repo, base_files)
    v1_tree = _tree(code_repo, v1_files)
    v2_tree = _tree(code_repo, v2_files)
    mem_tree = _tree(mem_repo, _memory(revision=3))

    def binding(code_tree: str, number: int) -> FrameBinding:
        return FrameBinding.from_record(
            ReviewTreeComparisonRecord(
                task_id=TASK,
                leaf_id=LEAF,
                number=number,
                code_base=ReviewTreeSide(repository=code_repo.as_posix(), tree=base_tree),
                code_candidate=ReviewTreeSide(repository=code_repo.as_posix(), tree=code_tree),
                memory_base=ReviewTreeSide(repository=mem_repo.as_posix(), tree=mem_tree),
                memory_candidate=ReviewTreeSide(repository=mem_repo.as_posix(), tree=mem_tree),
                recorded_at=AT,
            ),
            comparison_owner=COMPARISON_OWNER,
            comparison_reference=f"review-comparison:{TASK}/{LEAF}/{number}",
        )

    bound_v1 = binding(v1_tree, 1)
    bound_v2 = binding(v2_tree, 2)
    reads_v1 = ExplainerTargetReads(binding=bound_v1)
    hunks_v1 = reads_v1.file_hunks("src/h.py")
    deletions_v1 = reads_v1.file_hunks("src/d.py")
    assert hunks_v1 is not None and len(hunks_v1) == 2
    assert deletions_v1 is not None and len(deletions_v1) == 1
    assert deletions_v1[0].pure_deletion is True

    edited_ref = hunks_v1[0].identity().block_key()
    moved_ref = hunks_v1[1].identity().block_key()
    deletion_ref = deletions_v1[0].identity().block_key()
    edited_fp = hunks_v1[0].fingerprint().value
    moved_fp = hunks_v1[1].fingerprint().value
    deletion_fp = deletions_v1[0].fingerprint().value
    assert edited_fp is not None and moved_fp is not None and deletion_fp is not None
    fingerprints = {
        edited_ref: edited_fp,
        moved_ref: moved_fp,
        deletion_ref: deletion_fp,
    }
    store = GradeStore(path=grades_path(world.task_root, LEAF), leaf_id=LEAF)
    store.define(
        "risk",
        (ScalePoint(grade="sound", meaning="good"), ScalePoint(grade="thin", meaning="weak")),
        meaning="change risk",
    )
    store.grade(
        references=(edited_ref, moved_ref, deletion_ref),
        category="risk",
        grade="thin",
        reason="initial grades",
        fingerprints=fingerprints,
        binding=bound_v1,
        at=AT,
    )

    reconciled = reconcile_grades(
        world.task_root,
        LEAF,
        screen_target(task_root=world.task_root, on_screen=bound_v2),
        prune=True,
        active_category="risk",
    )
    assert reconciled.gone_grades == ()  # none of the three is pruned: they moved, not vanished
    by_span = {
        (block.start_line, block.end_line): state
        for state in reconciled.states
        if (block := parse_block_identity(state.reference)) is not None
    }
    # The edited hunk kept its span and its grade, stale because its fingerprint changed.
    assert (1, 1) in by_span and by_span[(1, 1)].stale is True
    assert "the block changed" in (by_span[(1, 1)].reason or "")
    # The moved hunk's grade rides the moved span, stale: the base lines it replaced changed.
    assert (7, 7) in by_span and by_span[(7, 7)].stale is True
    assert "the block changed" in (by_span[(7, 7)].reason or "")
    # The pure deletion is identified by its base span; the grown deletion's span is the target's
    # base span (lines 3-6 now), and its grade is stale, kept.
    assert (3, 6) in by_span and by_span[(3, 6)].stale is True

    # An unchanged pure deletion stays current: the same subject at the same base span.
    steady = reconcile_grades(
        world.task_root,
        LEAF,
        screen_target(task_root=world.task_root, on_screen=bound_v1),
        prune=True,
        active_category="risk",
    )
    spans = {
        (block.start_line, block.end_line): state
        for state in steady.states
        if (block := parse_block_identity(state.reference)) is not None
    }
    assert spans[(5, 6)].stale is False
    assert all(state.stale is False for state in steady.states)
