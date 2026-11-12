"""MIK-R27's admission rules in the validator's registry (MIK-R22 rule 9).

Every invariant, family and decision record states which admission criterion it meets, with a
one-sentence justification (the shape is MIK-R21 rule 4). Admission governs **creation, not
maintenance**, so the same failure is refused on a new record and only reported on every other one:

* **New.** A record is *new* when its ID is absent from every comparison base (K_B at a commit
  route, each parent at a merge) and it is not an export: an exported record's ``origin.legacyId``
  derives its ID (:func:`derived_record_id`, the conversion's own derivation), so a forged legacy ID
  does not make a record exported, and a decision is never an export (the conversion exports
  none). Exported records are never new, and a record with ``status:
  retired`` is never refused. With no base (a curator's standalone run without ``--base``) every
  record that is not an export is new.
* **Refused on a new record** (``R27.2-new-record``): ``legacy-unassessed``, which only the export
  writes; a justification that consists only of task, leaf, requirement, step or section references,
  developer ruling IDs, commit hashes or dates ("Added in L43", "Per ruling D14"), found by a word
  list (below); a claimed ``spans_locations`` (realized in more than one file) or
  ``guarded_by_test`` (at least one proof entry, MIK-R28) that the tree's sidecar entries -- the
  facts the derived index (MIK-R23) projects -- do not support. A record with no criterion at all
  does not parse (MIK-R21 rule 4: ``criteria`` has at least one item) and is refused by the shape
  rule, naming ``admission.criteria``.
* **Reported on every other record** (``R27.2-existing-record``, ``merge_reports``): a claimed
  ``spans_locations`` or ``guarded_by_test`` that no longer holds -- a test was deleted, a
  realization moved into the same file. The finding refuses at every commit and landing route
  and reports only at a sync merge, which can join two valid sides; an unrelated commit, or a
  master's own landing, is never blocked by an old record's admission.
* **Counted** (``R27.4-legacy-unassessed``, ``migration_debt``): the live records still marked
  ``legacy-unassessed``, until the migration (MIK-R19) assesses or demotes each one. A finding a
  comparison base already holds is reported while the migration is not complete, and refused at
  every route once it is -- then one finding per record; a finding no base holds is new and
  refuses at once.
* **Refused at every route** (``R27.4-record-kept``, MIK-R52 rule 6): every record ID a
  comparison base holds still exists in the candidate. A record is never deleted: it leaves use
  by ``status: retired``, never by removing its file.

Only the two mechanically checkable criteria are checked, plus presence and shape (Exclusions).
Whether a justification is plausible, and whether ``family_guarantee``, ``prevents_costly_mistake``,
``joint_guarantee``, ``real_alternatives`` or ``constrains_future_work`` really hold, is the
reviewer's judgment (Doc13).

**Transition.** The validator runs only over converted memory (MIK-R22 rule 8): inside the writer,
which refuses unconverted trees, and at a commit route whose K_B or K_C holds the layout marker. The
conversion's own commit and a crossing sync carry only exported records or records already present
in a parent, so no rule here refuses anything until records are authored on a converted line.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Final

from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    debt_report_note,
    debt_reports_at,
    merge_report_note,
    merge_reports_at,
    register_rule,
    sidecar_entries,
)
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    RECORD_DIRECTORIES,
    split_record_filename,
)
from agents_remember.models.knowledge_files.history import RECORD_PREFIXES, RecordRow
from agents_remember.models.knowledge_files.ids import derived_record_id
from agents_remember.models.knowledge_files.records import (
    DecisionRecord,
    FamilyRecord,
    InvariantRecord,
)
from agents_remember.models.knowledge_files.shapes import LEGACY_UNASSESSED
from agents_remember.models.knowledge_files.sidecars import FileSidecar, ProofEntry

AdmittedRecord = InvariantRecord | FamilyRecord | DecisionRecord

SPANS_LOCATIONS: Final = "spans_locations"
GUARDED_BY_TEST: Final = "guarded_by_test"

_RECORD_DIRECTORY_PREFIXES: Final = tuple(
    f"{KNOWLEDGE_ROOT}/{directory}/" for directory in RECORD_DIRECTORIES.values()
)

# A justification "consists only of a task, leaf or requirement reference" when, after removing
# every such reference, only provenance filler words and numbers remain: provenance is never a
# reason, and the justification must state the reason in words. The detector is a word list, not a
# judgment (Exclusions); the reviewer judges plausibility (OM-4).
#
# * **References:** task and leaf IDs (``260928-MIK``, ``260928-MIK-L27``, ``ICR L45`` -- a master
#   code followed by a leaf or requirement is joined into one reference first), requirement IDs with
#   dotted sub-rules (``MIK-R27@v1``, ``R27.2``), bare leaf IDs (``L43``), step IDs (``S2``),
#   section signs (``§2``), task directories, developer rulings (``D14``) and commit hashes (7-40
#   hex digits, at least one a digit, so a hex-only word such as "defaced" stays a word), plus ISO
#   dates (L27 rulings round Q2 and review round F1).
# * **Filler:** connectives and the words a provenance note is made of ("added", "introduced",
#   "implements", "per", "ruling", "developer", "commit", "decision", "acceptance", "criteria",
#   "section" ...), and bare numbers.
# * **Tokens** split on whitespace, most punctuation, ``/`` and apostrophes ("L43's", "L43/L44");
#   surrounding dashes, dots and similar marks are stripped.
#
# A justification that says anything about the code keeps at least one other word and is admitted.
_REFERENCE: Final = re.compile(
    r"""^(?:
        \d{6}-[A-Z0-9]+(?:-L\d+[A-Z]?)?               # a task or leaf ID: 260928-MIK, 260928-MIK-L27
      | [A-Z][A-Z0-9]*-(?:R|L)\d+(?:\.\d+)*[A-Z]?(?:@v\d+)?  # MIK-R27@v1, ICR-R03, MIK-L27, ICR-L45
      | [RL]\d+(?:\.\d+)*[A-Z]?(?:@v\d+)?             # R27, R27.2, L43, R27@v1
      | S\d+                                          # a leaf step: S2
      | §[\d.]*                                       # a section: §2
      | \S*\d{6}_[\w-]+\S*                            # a task directory or a path inside one
      | D\d+                                          # a developer ruling: D14
      | (?=[0-9A-F]*\d)[0-9A-F]{7,40}                  # a commit hash, 7-40 hex digits, one a digit
      | \d{4}-\d{2}-\d{2}(?:T[\d:.+Z-]*)?              # an ISO date or instant
    )$""",
    re.IGNORECASE | re.VERBOSE,
)
# A master code directly before a leaf or requirement ID ("ICR L45") is one reference.
_MASTER_PREFIXED: Final = re.compile(r"\b([A-Z][A-Z0-9]{1,7})\s+([LR]\d+)\b")
# Curly quotes and dashes are written as escapes: \u2018 \u2019 \u201c \u201d, \u2013 \u2014.
_TOKEN: Final = re.compile("[^\\s,;:()\\[\\]{}`'\"/\u2018\u2019\u201c\u201d]+")
_STRIPPED: Final = ".!?-\u2013\u2014*_"
_FILLER_WORDS: Final = """
    a an and as at by during for from in into its of on or per see the this to under via with s
    add added adds author authored create created creates deliver delivered implement implemented
    implements implementing introduce introduced introduces land landed new part required requires
    leaf leaves task tasks subtask sub-task master wave step steps rule rules requirement requirements
    packet packets acceptance criteria criterion version section sections item items
    ruling rulings developer developers decision decisions commit commits hash sha
    closes close fixes fix follow-up followup
"""
_FILLER: Final = frozenset(_FILLER_WORDS.split())


def _only_references(justification: str) -> bool:
    references = 0
    for raw in _TOKEN.findall(_MASTER_PREFIXED.sub(r"\1-\2", justification)):
        token = raw.strip(_STRIPPED)
        if not token or token.isdigit() or token.lower() in _FILLER:
            continue
        if _REFERENCE.match(token):
            references += 1
            continue
        return False
    return references > 0


@dataclass(frozen=True)
class _Admitted:
    path: str
    record: AdmittedRecord
    new: bool

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def kind(self) -> str:
        return type(self.record).record_kind


def _base_record_ids(bases: Iterable[KnowledgeTree]) -> frozenset[str]:
    """Every record ID a comparison base holds, read from its record filenames (rule 2 binds them)."""

    ids: set[str] = set()
    for base in bases:
        for path in base.files:
            if path.startswith(_RECORD_DIRECTORY_PREFIXES) and path.endswith(".json"):
                try:
                    ids.add(split_record_filename(path.rsplit("/", 1)[-1])[0])
                except ValueError:
                    continue
    return frozenset(ids)


def _admitted_records(context: ValidationContext) -> Iterator[_Admitted]:
    base_ids = _base_record_ids(context.bases)
    for record_file in context.parsed.records:
        record = record_file.record
        if not isinstance(record, InvariantRecord | FamilyRecord | DecisionRecord):
            continue
        new = record.id not in base_ids and not _exported(record)
        yield _Admitted(record_file.path, record, new)


def _exported(record: AdmittedRecord) -> bool:
    """An exported record: its ``legacyId`` derives its ID, as the conversion does (Doc14 §6).

    A ``legacyId`` that does not derive the record's ID is not an export's, so the record is judged
    for admission like any other (review round F2). A decision is never an export: the conversion
    exports no decisions (MIK-R24), so a ``legacyId`` on a decision exempts it from nothing (L13
    review F6).
    """

    if isinstance(record, DecisionRecord):
        return False
    legacy_id = record.origin.legacy_id
    return bool(legacy_id) and derived_record_id(type(record).record_kind, legacy_id) == record.id


def _base_live_unassessed_ids(bases: Iterable[KnowledgeTree]) -> frozenset[str]:
    """The IDs of the records a comparison base holds as a live unassessed finding (rule 10).

    Carried debt is the actual base finding -- a record the base holds assessed or retired does
    not carry the candidate's unassessed state, so assessed-to-unassessed and newly live states
    refuse at once (review round F10).
    """

    found: set[str] = set()
    for base in bases:
        for record_file in parse_tree(base).records:
            record = record_file.record
            if (
                isinstance(record, InvariantRecord | FamilyRecord | DecisionRecord)
                and record.admission == LEGACY_UNASSESSED
                and not _retired(record)
            ):
                found.add(record.id)
    return frozenset(found)


def _retired(record: AdmittedRecord) -> bool:
    return not isinstance(record, DecisionRecord) and record.status == "retired"


@dataclass(frozen=True)
class _EntryFacts:
    """Per invariant: the files its realization entries sit in, and whether any proof names it."""

    realized_in: dict[str, frozenset[str]]
    proven: frozenset[str]


def _entry_facts(context: ValidationContext) -> _EntryFacts:
    realized: dict[str, set[str]] = {}
    proven: set[str] = set()
    for sidecar in context.parsed.sidecars:
        if not isinstance(sidecar.sidecar, FileSidecar):
            continue
        for entry in sidecar_entries(sidecar):
            if isinstance(entry, ProofEntry):
                proven.add(entry.invariant)
            else:
                realized.setdefault(entry.invariant, set()).add(sidecar.sidecar.path)
    return _EntryFacts(
        {invariant: frozenset(paths) for invariant, paths in realized.items()},
        frozenset(proven),
    )


def _unsupported(admitted: _Admitted, facts: _EntryFacts) -> Iterator[tuple[str, str]]:
    """``(criterion, why)`` for each claimed checkable criterion the tree does not support."""

    admission = admitted.record.admission
    if admission == LEGACY_UNASSESSED or not isinstance(admitted.record, InvariantRecord):
        return
    criteria = tuple(getattr(admission, "criteria", ()))
    if SPANS_LOCATIONS in criteria:
        files = sorted(facts.realized_in.get(admitted.id, ()))
        if len(files) < 2:
            where = ", ".join(files) or "no file"
            yield (
                SPANS_LOCATIONS,
                f"claims {SPANS_LOCATIONS} but its realization entries sit in {where}; the "
                "criterion needs realizations in more than one file",
            )
    if GUARDED_BY_TEST in criteria and admitted.id not in facts.proven:
        yield (
            GUARDED_BY_TEST,
            f"claims {GUARDED_BY_TEST} but no proof entry (MIK-R28) names it",
        )


def check_new_record_admission(context: ValidationContext) -> Iterator[Finding]:
    facts = _entry_facts(context)
    for admitted in _admitted_records(context):
        if not admitted.new or _retired(admitted.record):
            continue
        label = f"new {admitted.kind} {admitted.id}"
        admission = admitted.record.admission
        if admission == LEGACY_UNASSESSED:
            yield Finding(
                admitted.path,
                "admission",
                f"{label} is legacy-unassessed, which only exported records carry: state the "
                "criterion it meets with a one-sentence justification",
            )
            continue
        justification = getattr(admission, "justification", "")
        if _only_references(justification):
            criteria = ", ".join(getattr(admission, "criteria", ()))
            yield Finding(
                admitted.path,
                "admission.justification",
                f"{label}: the justification for {criteria} is only task, leaf, requirement or "
                f"ruling references or commit hashes ({justification!r}); state in words why "
                "the record meets the criterion",
            )
        for _criterion, why in _unsupported(admitted, facts):
            yield Finding(admitted.path, "admission.criteria", f"{label} {why}")


def check_existing_record_admission(context: ValidationContext) -> Iterator[Finding]:
    facts = _entry_facts(context)
    reporting = merge_reports_at(context)
    for admitted in _admitted_records(context):
        if admitted.new or _retired(admitted.record):
            continue
        for criterion, why in _unsupported(admitted, facts):
            message = (
                f"{admitted.kind} {admitted.id} {why}; admission governs "
                f"creation (reassess {criterion} or demote the record)"
            )
            if reporting:
                message += f"; {merge_report_note(context)}"
            yield Finding(
                admitted.path,
                "admission.criteria",
                message,
            )


def check_legacy_unassessed(context: ValidationContext) -> Iterator[Finding]:
    migration = context.migration
    base_ids = _base_live_unassessed_ids(context.bases)
    unassessed = [
        admitted
        for admitted in _admitted_records(context)
        if admitted.record.admission == LEGACY_UNASSESSED and not _retired(admitted.record)
    ]
    carried = [admitted for admitted in unassessed if admitted.id in base_ids]
    new = [admitted for admitted in unassessed if admitted.id not in base_ids]
    if carried and debt_reports_at(context, carried=True):
        counts: Counter[str] = Counter(admitted.kind for admitted in carried)
        parts = ", ".join(f"{counts[kind]} {kind}(s)" for kind in sorted(counts))
        yield Finding(
            KNOWLEDGE_ROOT,
            "admission",
            f"{len(carried)} live record(s) are still legacy-unassessed ({parts}); the "
            f"migration (MIK-R19) assesses or demotes each one; {debt_report_note(migration)}",
            carried=True,
        )
    refusing = [(admitted, True) for admitted in new]
    if migration.complete:
        refusing += [(admitted, False) for admitted in carried]
    for admitted, is_new in refusing:
        if is_new:
            message = (
                f"{admitted.kind} {admitted.id} is legacy-unassessed but no comparison base "
                "holds it: a new record states the criterion it meets with a one-sentence "
                "justification, which only exported records may skip by carrying "
                "legacy-unassessed (MIK-R53 rule 10)"
            )
        else:
            message = (
                f"{admitted.kind} {admitted.id} is still legacy-unassessed and the migration "
                "is complete (every onboarding route migrated or excluded): assess the record "
                "or demote it (MIK-R19, MIK-R53 rule 10)"
            )
        if debt_reports_at(context, carried=not is_new):
            message += f"; {debt_report_note(migration)}"
        yield Finding(admitted.path, "admission", message)


def _retired_row_answers(context: ValidationContext, record_id: str) -> bool:
    """Whether a leaf's history row retired this record (MIK-R124 rule 5).

    A dependent record is the one kind a tree may be rid of: its document was deleted, so the
    record has no subject left. The removal is not silent -- the leaf's ``record`` row about it
    says ``retired``, which is what this reads.
    """

    for _owner, history in context.parsed.histories:
        for row in history.rows:
            if (
                isinstance(row, RecordRow)
                and row.subject == record_id
                and row.disposition == "retired"
            ):
                return True
    return False


def check_record_kept(context: ValidationContext) -> Iterator[Finding]:
    """MIK-R52 rule 6: a record is never deleted -- every record ID a comparison base holds
    still exists in the candidate. Refuses at every route; a record leaves use by
    ``status: retired``, never by removing its file.

    One exception, because the packet names it (MIK-R124 rule 5): a *dependent* record leaves the
    tree when its document is deleted, and the leaf's ``retired`` row about it is the record of
    that removal. Every other kind keeps this rule whole.
    """

    base_ids = _base_record_ids(context.bases)
    if not base_ids:
        return
    candidate_ids = _base_record_ids((context.candidate,)) | {
        admitted.id for admitted in _admitted_records(context)
    }
    dependent_prefix = f"{RECORD_PREFIXES['dependent']}-"
    for missing in sorted(base_ids - candidate_ids):
        if missing.startswith(dependent_prefix) and _retired_row_answers(context, missing):
            continue
        yield Finding(
            KNOWLEDGE_ROOT,
            f"records.{missing}",
            f"record {missing} is held by a comparison base but absent from this tree; a "
            "record is never deleted: it leaves use by status: retired",
        )


ADMISSION_RULES = (
    ValidationRule(
        "R27.2-new-record",
        "MIK-R27 rule 2",
        "a new invariant, family or decision states a supported admission criterion",
        check_new_record_admission,
    ),
    ValidationRule(
        "R27.2-existing-record",
        "MIK-R27 rule 2",
        "an existing record whose checkable admission criterion no longer holds refuses, merge_reports",
        check_existing_record_admission,
        writer_reports=True,
        merge_reports=True,
    ),
    ValidationRule(
        "R27.4-legacy-unassessed",
        "MIK-R27 rule 4",
        "records still legacy-unassessed are migration debt, one finding per record once refused",
        check_legacy_unassessed,
        migration_debt=True,
    ),
    ValidationRule(
        "R27.4-record-kept",
        "MIK-R27 rule 4",
        "a record a comparison base holds still exists in the candidate",
        check_record_kept,
    ),
)

for _rule in ADMISSION_RULES:
    register_rule(_rule)
