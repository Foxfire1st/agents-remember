"""The curator writer's one operation: a hand-off document in, validated knowledge files out.

:func:`write_knowledge` reads the document (:mod:`.handoff`), applies it to the memory tree with
every mechanical field filled (:mod:`.authoring`), renders every touched file in the canonical
formatting, and runs the knowledge validator (MIK-R22) over the whole resulting tree against the
paired code candidate C. Then, and only in a committing run, it writes.

**All or nothing (MIK-R12 rule 4).** A problem in the document, an anchor that does not resolve at
C, a file its model refuses, or any refusing violation of the resulting tree refuses the whole
operation: nothing is written, and the report names every problem and every violation. Report-only
findings are carried in the report and never refuse, and so are the rules the registry marks
``writer_reports`` -- the MIK-R04 family route rules -- which only commit routes refuse.

**Carried entries (MIK-R08).** Every operation also re-records, at C, each entry whose anchored
content is identical at C while its file's blob moved (:mod:`.carry`): the mechanical ``blob`` and
line-number update the worklist's ``carried`` class promises, so such an entry needs no disposition.

**Converted trees only.** The writer writes a memory tree that holds the layout marker. An
unconverted tree is refused by name: until the cutover (MIK-R37) the installed runtime's database
ingest is the production path for unconverted memory, and nothing here changes it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.application.knowledge_worklist.base_cache import default_base_cache_directory
from agents_remember.application.knowledge_writer.authoring import Authoring, DecisionResolver
from agents_remember.application.knowledge_writer.base_side import BaseCode
from agents_remember.application.knowledge_writer.carry import carry_entries
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.knowledge_writer.handoff import Problem, read_handoff
from agents_remember.application.knowledge_writer.history_check import owner_history_problems
from agents_remember.application.knowledge_writer.memory_state import MemoryState, Owner
from agents_remember.application.knowledge_writer.reconsideration import (
    OpenQuestions,
    append_questions,
)
from agents_remember.application.knowledge_writer.report import WriteReport
from agents_remember.application.knowledge_writer.requirement_links import requirement_endpoints
from agents_remember.memory_quality.knowledge_validator import (
    CodePathSet,
    KnowledgeTree,
    ValidationReport,
    Violation,
    validate_tree,
    writer_reported_rule_ids,
)
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    parse_document,
    parse_history_document,
)
from agents_remember.models.knowledge_files.history import HISTORY_SCHEMA
from agents_remember.models.knowledge_files.unexplained import FILE_ITEM_KIND, HUNK_ITEM_KIND

UNCONVERTED = (
    "this memory tree has no layout marker, so it is unconverted: the file writer writes converted "
    "trees only. An unconverted line is curated through the database ingest only in a repository "
    "that holds no converted memory; otherwise it crosses the boundary first (MIK-R24 rules 8 and 9)"
)


@dataclass(frozen=True)
class WriteRequest:
    """One operation: the hand-off document, the two roots, and who authors it.

    ``handoff_path`` is the hand-off list's name as ``origin.handoff.path`` records it. ``commit``
    is the commit word: without it the operation plans, validates and reports, and writes nothing.
    ``authorization`` is the reference the run is admitted under; the file format has no field for
    it, so the report records it.
    """

    memory_root: Path
    code_root: Path
    owner: Owner
    handoff_path: str
    document: Any
    commit: bool = False
    authorization: str = ""
    decisions: DecisionResolver | None = None
    """The task owner's resolution of a planned ``dropped`` row's decision (MIK-R11); a write
    without a task owner (a wave) refuses such a row."""
    coordination_root: Path | None = None
    """Where requirement endpoints' owning tasks live (``tasks/<repository>/<path>``); without it
    every endpoint is reported unresolved (MIK-R13 rule 4). Never a reason to refuse."""
    worklist: Mapping[str, Any] | None = None
    """The leaf's persisted worklist (MIK-R08): a ``still_rejected`` row refreshes the links whose
    trigger fired on its item (MIK-R14), and a row whose hand-off names no item lists the items it
    answers (MIK-R07 rule 1); without it no link is refreshed and no item is filled in."""
    questions: OpenQuestions | None = None
    """The leaf task document's ``openQuestions``, where a ``raise`` row's question goes
    (MIK-R14); without it a ``raise`` is refused."""
    code_base: str | None = None
    """The code commit an unconverted ``HEAD`` without a ``Code-Commit`` trailer is converted at
    (MIK-R24 rule 7): a leaf's code base B, the gate's and the worklist's fallback, so all three
    share one cached base. Without it, the code tree's ``HEAD``."""


def write_knowledge(request: WriteRequest, *, code: CodeSnapshot | None = None) -> WriteReport:
    """Apply one hand-off document to the memory tree, or refuse it naming every problem."""

    document, problems = read_handoff(request.document)
    if request.owner.kind == "crossing":
        problems = [*problems, *_crossing_problems(document)]
    state, unwritable = _load(request)
    report = WriteReport(
        state="refused",
        owner=request.owner.id,
        memory_root=str(request.memory_root),
        code_tree="",
        authorization=request.authorization,
    )
    if unwritable is not None:
        return replace(report, problems=(*problems, unwritable))
    snapshot = code if code is not None else CodeSnapshot.capture(request.code_root)
    authoring = Authoring(
        state,
        snapshot,
        request.owner,
        request.handoff_path,
        decisions=request.decisions,
        questions=request.questions,
        reconsiderations=_reconsideration_items(request.worklist),
        worklist_items=_worklist_items(request.worklist),
        trace_items=_trace_items(request.worklist),
    )
    authoring.run(document)
    carried = carry_entries(state, snapshot, request.owner)
    history_problems = owner_history_problems(state, request.owner)
    rendered, render_problems = _render(state.changed_documents())
    problems = [*problems, *authoring.problems, *history_problems, *render_problems]
    report = replace(
        report,
        code_tree=snapshot.tree,
        records=tuple(authoring.records),
        entries=tuple(authoring.entries),
        rows=tuple(authoring.rows),
        evidence=tuple(authoring.evidence),
        rulings=tuple(entry.entry_id for entry in document.rulings),
        notes=tuple(authoring.notes),
        carried=carried,
        requirements=requirement_endpoints(state, authoring.records, request.coordination_root),
    )
    if problems:
        return replace(report, problems=tuple(problems))
    files = state.candidate_files(rendered)
    bases = (state.base,) if state.base is not None and state.base.converted else ()
    validation = validate_tree(
        KnowledgeTree(label=f"{request.owner.id} candidate", files=files),
        bases=bases,
        code=CodePathSet(label=f"code candidate {snapshot.tree}", paths=frozenset(snapshot.blobs)),
    )
    refusals, reports = _writer_split(validation)
    if refusals:
        return replace(report, violations=(*refusals, *reports))
    report = replace(report, violations=reports)
    refused = _append_raised(request, authoring.raised)
    if refused:
        return replace(report, problems=refused)
    return _finish(report, state, files, request.commit)


CROSSING_ROWS_ONLY = (
    "a master line's crossing sync resolves what conflicted and records judgment rows (MIK-R24 "
    "rule 8 step 4): its owner <task-id>-crossing-<n> may update an existing record by its 'id' -- "
    "the record both sides changed, which the writer resolves at one more than the higher side's "
    "revision -- and write 'history' rows; it authors no entry, ruling or new record"
)


def _crossing_problems(document: Any) -> list[Problem]:
    """A crossing owner resolves existing records and writes rows into the file its sync opened."""

    problems = [
        Problem(name, CROSSING_ROWS_ONLY)
        for name, items in (("entries", document.entries), ("rulings", document.rulings))
        if items
    ]
    problems.extend(
        Problem(f"records[{record.key}]", CROSSING_ROWS_ONLY)
        for record in document.records
        if record.record_id is None
    )
    return problems


def _load(request: WriteRequest) -> tuple[MemoryState, Problem | None]:
    """The memory tree and its base, or why the writer cannot write it: an unconverted tree, or
    a converted base (MIK-R24 rule 7) that cannot be built. The base is read through the
    coordination root's converted-base cache, the one the worklist and the gate use."""

    cache = (
        None
        if request.coordination_root is None
        else default_base_cache_directory(request.coordination_root)
    )
    state = MemoryState.load(
        request.memory_root,
        code=BaseCode(request.code_root, request.code_base, cache),
    )
    if not state.converted:
        return state, Problem(LAYOUT_MARKER_PATH, UNCONVERTED)
    if state.base_problem is not None:
        return state, Problem("base", state.base_problem)
    return state, None


def _reconsideration_items(worklist: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    items = (worklist or {}).get("items") or ()
    return {
        str(item.get("subject")): item
        for item in items
        if isinstance(item, Mapping) and item.get("kind") == "reconsideration_candidate"
    }


_ITEM_ID = re.compile(r"sha256:[0-9a-f]{64}")
_UNEXPLAINED_KINDS = frozenset({HUNK_ITEM_KIND, FILE_ITEM_KIND})


def _worklist_items(worklist: Mapping[str, Any] | None) -> tuple[tuple[str, str, str], ...]:
    """``(kind, subject, id)`` of every well-formed item of the leaf's persisted worklist."""

    items = (worklist or {}).get("items") or ()
    return tuple(
        (kind, subject, item_id)
        for item in items
        if isinstance(item, Mapping)
        and isinstance(kind := item.get("kind"), str)
        and isinstance(subject := item.get("subject"), str)
        and isinstance(item_id := item.get("id"), str)
        and _ITEM_ID.fullmatch(item_id)
    )


def _trace_items(worklist: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    """``(onboarding subject, id)`` of every unexplained item the file's onboarding row answers.

    An ``unexplained_hunk`` or ``unexplained_file`` item in an uncovered file is answered by the
    file's onboarding trace (MIK-R10 rule 5); its facts name the trace's subject.
    """

    found: list[tuple[str, str]] = []
    for item in (worklist or {}).get("items") or ():
        facts = item.get("facts") if isinstance(item, Mapping) else None
        trace = facts.get("onboardingTrace") if isinstance(facts, Mapping) else None
        subject = trace.get("subject") if isinstance(trace, Mapping) else None
        item_id = item.get("id") if isinstance(item, Mapping) else None
        if (
            isinstance(subject, str)
            and isinstance(item_id, str)
            and _ITEM_ID.fullmatch(item_id)
            and item.get("kind") in _UNEXPLAINED_KINDS
        ):
            found.append((subject, item_id))
    return tuple(found)


def _append_raised(request: WriteRequest, raised: list[tuple[str, str]]) -> tuple[Problem, ...]:
    """MIK-R14: a committing run's raise questions reach the task document before any file."""

    if not (request.commit and raised):
        return ()
    _appended, refused = append_questions(request.questions, raised)
    return tuple(Problem("openQuestions", f"the raise is refused: {one}") for one in refused)


def _writer_split(
    validation: ValidationReport,
) -> tuple[tuple[Violation, ...], tuple[Violation, ...]]:
    """The violations that refuse the writer, and the ones it reports.

    Rules the registry marks ``writer_reports`` (the MIK-R04 family route rules) are reports inside
    the writer, so a leaf may place routes across several runs (MIK-R04 rule 6); every commit route
    (``require_valid_commit``) still refuses them.
    """

    deferred = writer_reported_rule_ids()
    refusals = tuple(one for one in validation.refusals if one.rule not in deferred)
    reported = tuple(
        replace(one, report_only=True) for one in validation.refusals if one.rule in deferred
    )
    return refusals, tuple(sorted((*validation.reports, *reported)))


def _finish(
    report: WriteReport, state: MemoryState, files: Mapping[str, bytes], commit: bool
) -> WriteReport:
    """A validated operation: written in a committing run, planned (nothing written) otherwise."""

    if not commit:
        return replace(
            report,
            state="planned",
            written=tuple(path for path, data in files.items() if state.files.get(path) != data),
            removed=tuple(path for path in state.files if path not in files),
        )
    written, removed = state.write(files)
    return replace(report, state="written", written=tuple(written), removed=tuple(removed))


def _render(documents: Mapping[str, dict[str, Any]]) -> tuple[dict[str, bytes], list[Problem]]:
    """Check each touched document against its model, then render it canonically."""

    rendered: dict[str, bytes] = {}
    problems: list[Problem] = []
    for path, document in documents.items():
        text = canonical_text(document)
        try:
            if document.get("schema") == HISTORY_SCHEMA:
                parse_history_document(path, text)
            else:
                parse_document(document)
        except ValidationError as error:
            problems.extend(
                Problem(path, f"{'.'.join(str(part) for part in one['loc']) or '-'}: {one['msg']}")
                for one in error.errors()
            )
            continue
        except ValueError as error:
            problems.append(Problem(path, str(error)))
            continue
        rendered[path] = text.encode("utf-8")
    return rendered, problems
