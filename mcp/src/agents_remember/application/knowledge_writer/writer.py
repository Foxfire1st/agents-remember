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

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.application.knowledge_writer.authoring import Authoring, DecisionResolver
from agents_remember.application.knowledge_writer.carry import carry_entries
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.knowledge_writer.handoff import Problem, read_handoff
from agents_remember.application.knowledge_writer.history_check import owner_history_problems
from agents_remember.application.knowledge_writer.memory_state import MemoryState, Owner
from agents_remember.application.knowledge_writer.report import WriteReport
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

UNCONVERTED = (
    "this memory tree has no layout marker, so it is unconverted: the file writer writes converted "
    "trees only. Until the cutover (MIK-R37) unconverted memory is curated through the installed "
    "database ingest"
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


def write_knowledge(request: WriteRequest, *, code: CodeSnapshot | None = None) -> WriteReport:
    """Apply one hand-off document to the memory tree, or refuse it naming every problem."""

    document, problems = read_handoff(request.document)
    state = MemoryState.load(request.memory_root)
    report = WriteReport(
        state="refused",
        owner=request.owner.id,
        memory_root=str(request.memory_root),
        code_tree="",
        authorization=request.authorization,
    )
    if not state.converted:
        return replace(report, problems=(*problems, Problem(LAYOUT_MARKER_PATH, UNCONVERTED)))
    snapshot = code if code is not None else CodeSnapshot.capture(request.code_root)
    authoring = Authoring(
        state, snapshot, request.owner, request.handoff_path, decisions=request.decisions
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
    return _finish(replace(report, violations=reports), state, files, request.commit)


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
