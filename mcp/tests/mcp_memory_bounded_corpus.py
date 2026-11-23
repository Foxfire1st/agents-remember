"""Deterministic converted-memory inputs for the repeated stdio workload."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.anchor_content import content_identity, range_bytes
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    dependent_link_index,
    file_sidecar_path,
    history_path,
    record_path,
    route_sidecar_path,
)
from agents_remember.models.knowledge_files.history import (
    CoveredEntry,
    HistoryFile,
    InvariantRow,
    OnboardingTraceRow,
    SourceBlob,
)
from agents_remember.models.knowledge_files.records import (
    Confirmation,
    DependentFileLink,
    DependentRecord,
    DependentRecordLink,
    FamilyRecord,
    InvariantRecord,
)
from agents_remember.models.knowledge_files.shapes import (
    Anchor,
    AnchorTarget,
    FileModel,
    LineRangeLocator,
    Origin,
    Reference,
    SymbolLocator,
    WholeFileLocator,
)
from agents_remember.models.knowledge_files.sidecars import (
    FileSidecar,
    LayoutMarker,
    ProofEntry,
    RealizationEntry,
    RouteSidecar,
)
from agents_remember_test_support.testing.review_git_trees import git

GROUPS = 16
MODULES_PER_GROUP = 116
FAMILY_COUNT = 106
INVARIANT_COUNT = 1280
DEPENDENT_COUNT = 55
HISTORY_COUNT = 165
TRACE_ROWS = 80
PAIRED_TRACE_ROWS = 30
INVARIANT_ROWS = 8
COVERS_PER_ROW = 10
REFERENCES_PER_CARD = 6
ARCHIVE_PARAGRAPH_STEPS = 13
CARD_PARAGRAPH_STEPS = 5
ENTRIES_PER_MODULE = 4
SEED_FAMILY_SIZE = 96

OBSERVE_PATH = "pkg/observe.py"
OBSERVE_SYMBOL = "observe"
OBSERVE_TEST_PATH = "tests/test_observe.py"
OBSERVE_TEST_SYMBOL = "test_observe"

SOURCE_CONTEXT_SEEDS = tuple(f"pkg/group_{group:02d}/module_000.py" for group in range(6))
INVARIANT_IDS = ("INV-000001", "INV-000002", "INV-000003", "INV-000004")

_ARCHIVE_TASK = "260101-MEMORY-SAMPLE"
_ORIGIN = Origin(task=_ARCHIVE_TASK, leaf=f"{_ARCHIVE_TASK}-L001")
_CALIBRATION_NOTE = (
    "Follow the paired implementation and example in the same numbered group. "
    "The observation covers the complete tracked body at the captured revision."
)


@dataclass(frozen=True)
class _SourceFile:
    path: str
    kind: Literal["code", "test"]
    whole: Anchor
    entries: tuple[Anchor, ...]
    slots: tuple[int, ...]

    def target(self) -> AnchorTarget:
        return AnchorTarget(
            kind=self.kind, anchor=self.whole.model_copy(update={"path": self.path})
        )


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _document(root: Path, relative: str, model: FileModel) -> None:
    _write(root, relative, canonical_text(model.to_document()))


def _module_paths() -> Iterable[tuple[str, str]]:
    for group in range(GROUPS):
        for number in range(MODULES_PER_GROUP):
            yield (
                f"pkg/group_{group:02d}/module_{number:03d}.py",
                f"tests/group_{group:02d}/test_module_{number:03d}.py",
            )


def _guide_path(number: int) -> str:
    return f"notes/guide_{number:03d}.md"


def _source_text(number: int) -> str:
    functions = [
        f"def transform_{offset}(value: int) -> int:\n    return value + {number + offset}\n"
        for offset in range(ENTRIES_PER_MODULE)
    ]
    return f'"""Sample operation {number}."""\n\n' + "\n\n".join(functions)


def _test_text(number: int, source_path: str) -> str:
    module = source_path.removesuffix(".py").replace("/", ".")
    symbols = ", ".join(f"transform_{offset}" for offset in range(ENTRIES_PER_MODULE))
    functions = [
        f"def test_transform_{offset}() -> None:\n"
        f"    assert transform_{offset}(0) == {number + offset}\n"
        for offset in range(ENTRIES_PER_MODULE)
    ]
    return f"from {module} import {symbols}\n\n\n" + "\n\n".join(functions)


def write_code(root: Path) -> None:
    """Write the generated observation and test files before the fixture commits them."""
    _write(root, OBSERVE_PATH, "def observe(value: int) -> int:\n    return value\n")
    _write(
        root,
        OBSERVE_TEST_PATH,
        "from pkg.observe import observe\n\n\ndef test_observe() -> None:\n    assert observe(1) == 1\n",
    )
    for number, (source, test) in enumerate(_module_paths()):
        _write(root, source, _source_text(number))
        _write(root, test, _test_text(number, source))
    for number in range(DEPENDENT_COUNT):
        _write(root, _guide_path(number), f"# Sample guide {number}\n\nExplains one operation.\n")


def _invariant_id(slot: int) -> str:
    return f"INV-{slot % INVARIANT_COUNT + 1:06d}"


def _assignment(number: int) -> int:
    # Put each seed on its own broad family and keep the remaining assignment a permutation.
    for group in range(1, len(SOURCE_CONTEXT_SEEDS)):
        seed = group * MODULES_PER_GROUP
        local = group * SEED_FAMILY_SIZE // ENTRIES_PER_MODULE
        if number == seed:
            return local
        if number == local:
            return seed
    return number


def _span_anchors(data: bytes, blob: str) -> tuple[Anchor, ...]:
    anchors: list[Anchor] = []
    for node in ast.parse(data).body:
        if isinstance(node, ast.FunctionDef):
            end = node.end_lineno
            assert end is not None
            anchors.append(
                Anchor(
                    locator=LineRangeLocator(kind="line_range", start=node.lineno, end=end),
                    blob=blob,
                    content=content_identity(range_bytes(data, node.lineno, end)),
                )
            )
    return tuple(anchors)


def _sample_file(
    code: Path,
    path: str,
    blob: str,
    kind: Literal["code", "test"],
    slots: tuple[int, ...],
) -> _SourceFile:
    data = (code / path).read_bytes()
    whole = Anchor(locator=WholeFileLocator(), blob=blob, content=content_identity(data))
    return _SourceFile(path, kind, whole, _span_anchors(data, blob), slots)


def _observe_file(file: _SourceFile, trees: CodeTrees, symbol: str) -> _SourceFile:
    locator = SymbolLocator(kind="symbol", name=symbol)
    blob = file.whole.blob
    resolved = trees.resolve(file.path, locator.to_document(), blob, blob)
    assert resolved is not None
    entry = Anchor(locator=locator, blob=blob, content=resolved.content)
    return _SourceFile(file.path, file.kind, file.whole, (entry,), file.slots)


def _files(code: Path, trees: CodeTrees) -> tuple[_SourceFile, ...]:
    blobs = trees.base()
    population: list[_SourceFile] = []
    for number, (source, test) in enumerate(_module_paths()):
        start = _assignment(number) * ENTRIES_PER_MODULE
        slots = tuple(range(start, start + ENTRIES_PER_MODULE))
        population.append(_sample_file(code, source, blobs[source], "code", slots))
        population.append(_sample_file(code, test, blobs[test], "test", slots))
    support_slot = (INVARIANT_COUNT,)
    source = _sample_file(code, OBSERVE_PATH, blobs[OBSERVE_PATH], "code", support_slot)
    test = _sample_file(code, OBSERVE_TEST_PATH, blobs[OBSERVE_TEST_PATH], "test", support_slot)
    population.extend(
        (
            _observe_file(source, trees, OBSERVE_SYMBOL),
            _observe_file(test, trees, OBSERVE_TEST_SYMBOL),
        )
    )
    return tuple(population)


def _observations(
    file: _SourceFile, number: int
) -> tuple[tuple[RealizationEntry, ...], tuple[ProofEntry, ...]]:
    entries = zip(file.entries, file.slots, strict=True)
    start = number * ENTRIES_PER_MODULE
    if file.kind == "code":
        return (
            tuple(
                RealizationEntry(
                    id=f"RLZ-{start + offset + 1:06d}",
                    invariant=_invariant_id(slot),
                    anchor=anchor,
                    role="primary-authority" if slot < INVARIANT_COUNT else "support",
                    rationale="This implementation performs the operation sampled by the invariant.",
                )
                for offset, (anchor, slot) in enumerate(entries)
            ),
            (),
        )
    return (
        (),
        tuple(
            ProofEntry(
                id=f"PRF-{start + offset + 1:06d}",
                invariant=_invariant_id(slot),
                anchor=anchor,
                facet="This test proves the numbered example returns its expected value.",
            )
            for offset, (anchor, slot) in enumerate(entries)
        ),
    )


def _paragraph(subject: str, steps: int) -> str:
    return " ".join(
        f"Observation {step + 1} examines {subject} in its source group and compares the "
        "numbered operation with its recorded example."
        for step in range(steps)
    )


def _write_cards(root: Path, files: Sequence[_SourceFile]) -> dict[str, list[CoveredEntry]]:
    covered: defaultdict[str, list[CoveredEntry]] = defaultdict(list)
    for number, file in enumerate(files):
        realizations, proofs = _observations(file, number)
        sidecar = file_sidecar_path(file.path)
        _document(
            root,
            sidecar,
            FileSidecar(
                path=file.path,
                references={
                    str(offset): Reference(
                        targets=(files[(number + offset) % len(files)].target(),),
                        note=_CALIBRATION_NOTE,
                    )
                    for offset in range(1, REFERENCES_PER_CARD + 1)
                },
                realizes=realizations,
                proves=proofs,
            ),
        )
        paragraphs = [
            f"{_paragraph(file.path, CARD_PARAGRAPH_STEPS)} [{offset}]"
            for offset in range(1, REFERENCES_PER_CARD + 1)
        ]
        _write(
            root,
            sidecar.removesuffix(".json") + ".md",
            f"# {file.path}\n\n" + "\n\n".join(paragraphs) + "\n",
        )
        for entry in (*realizations, *proofs):
            anchor = entry.anchor.model_copy(update={"path": file.path})
            covered[entry.invariant].append(CoveredEntry(id=entry.id, before=anchor, after=anchor))
    return dict(covered)


def _write_routes(root: Path, files: Sequence[_SourceFile]) -> None:
    first_files = {str(Path(file.path).parent): file for file in reversed(files)}
    first_files.update({".": files[0], "pkg": files[0], "tests": files[1]})
    for route, file in sorted(first_files.items()):
        sidecar = route_sidecar_path(route)
        _document(
            root,
            sidecar,
            RouteSidecar(path=route, references={"1": Reference(targets=(file.target(),))}),
        )
        _write(
            root,
            sidecar.removesuffix(".json") + ".md",
            f"# {route}\n\n## What This Repo Is\n\nThis route holds paired operations and examples [1].\n",
        )


def _family_members(number: int) -> tuple[str, ...]:
    seed_count = len(SOURCE_CONTEXT_SEEDS)
    if number < seed_count:
        start = number * SEED_FAMILY_SIZE
        slots = range(start, start + SEED_FAMILY_SIZE)
    else:
        start = seed_count * SEED_FAMILY_SIZE + number - seed_count
        slots = range(start, INVARIANT_COUNT, FAMILY_COUNT - seed_count)
    return tuple(_invariant_id(slot) for slot in slots)


def _write_invariants(root: Path) -> None:
    for slot in range(INVARIANT_COUNT):
        identity = _invariant_id(slot)
        _document(
            root,
            record_path("invariant", identity, "sample-operation"),
            InvariantRecord(
                id=identity,
                revision=1,
                status="accepted",
                statement=f"The numbered operation {slot + 1} returns its recorded example value.",
                applicability=_paragraph(f"operation {slot + 1}", 10),
                conditions=("The function receives the recorded integer input.",),
                exclusions=("Calls outside the numbered examples.",),
                supersedes=(),
                admission="legacy-unassessed",
                origin=_ORIGIN,
            ),
        )


def _write_families(root: Path) -> None:
    for number in range(FAMILY_COUNT):
        identity = f"FAM-{number + 1:06d}"
        _document(
            root,
            record_path("family", identity, "sample-route"),
            FamilyRecord(
                id=identity,
                revision=1,
                status="accepted",
                title=f"Sample group {number + 1}",
                guarantee="The grouped operations keep their recorded examples intact.",
                members=_family_members(number),
                routes=("pkg",),
                admission="legacy-unassessed",
                origin=_ORIGIN,
            ),
        )


def _write_dependents(
    root: Path, files: Sequence[_SourceFile], commit: str, tree: str
) -> list[tuple[str, DependentRecord]]:
    confirmed = Confirmation(task=_ARCHIVE_TASK, role="curator", commit=commit, codeTree=tree)
    records: list[tuple[str, DependentRecord]] = []
    for number in range(DEPENDENT_COUNT):
        source = files[number * 2]
        identity = f"DPD-{number + 1:06d}"
        record = DependentRecord(
            id=identity,
            origin=_ORIGIN,
            revision=1,
            document=_guide_path(number),
            stands_on=(
                DependentFileLink(path=source.path, blob=source.whole.blob),
                DependentRecordLink(id=_invariant_id(source.slots[0]), revision=1),
            ),
            confirmed=confirmed,
        )
        path = record_path("dependent", identity, "sample-guide")
        _document(root, path, record)
        records.append((path, record))
    return records


def _trace_rows(
    files: Sequence[_SourceFile], number: int, first_id: int
) -> list[OnboardingTraceRow]:
    rows: list[OnboardingTraceRow] = []
    for offset in range(TRACE_ROWS):
        index = (number * TRACE_ROWS + offset) % len(files)
        file = files[index]
        recorded = (file,)
        if offset < PAIRED_TRACE_ROWS:
            recorded += (files[index ^ 1],)
        rows.append(
            OnboardingTraceRow(
                id=f"ROW-{first_id + offset:06d}",
                subject=f"onboarding:{file.path}",
                disposition="no_impact",
                reason=_paragraph(f"{file.path} archive {number + 1}", ARCHIVE_PARAGRAPH_STEPS),
                items=(),
                sources=tuple(SourceBlob(path=one.path, blob=one.whole.blob) for one in recorded),
            )
        )
    return rows


def _invariant_rows(
    covered: Mapping[str, list[CoveredEntry]], number: int, first_id: int
) -> list[InvariantRow]:
    rows: list[InvariantRow] = []
    for offset in range(INVARIANT_ROWS):
        identity = _invariant_id(number * INVARIANT_ROWS + offset)
        rows.append(
            InvariantRow(
                id=f"ROW-{first_id + offset:06d}",
                subject=identity,
                disposition="no_impact",
                reason="The tracked implementation and proof still match their recorded anchors.",
                items=(),
                revision=1,
                covers=tuple(covered[identity][:COVERS_PER_ROW]),
            )
        )
    return rows


def _write_history(
    root: Path, files: Sequence[_SourceFile], covered: Mapping[str, list[CoveredEntry]]
) -> None:
    for number in range(HISTORY_COUNT):
        owner = f"{_ARCHIVE_TASK}-L{number + 1:03d}"
        first_id = number * (TRACE_ROWS + INVARIANT_ROWS) + 1
        traces = _trace_rows(files, number, first_id)
        judgments = _invariant_rows(covered, number, first_id + TRACE_ROWS)
        _document(
            root,
            history_path(owner),
            HistoryFile(leaf=owner, closed=True, rows=(*traces, *judgments)),
        )


def write_memory(root: Path, code: Path) -> None:
    """Build converted records over the actual committed fixture code."""
    commit = git(code, "rev-parse", "HEAD")
    tree = git(code, "rev-parse", "HEAD^{tree}")
    files = _files(code, CodeTrees.open(code, tree, tree))
    covered = _write_cards(root, files)
    _write_routes(root, files)
    _write_invariants(root)
    _write_families(root)
    dependents = _write_dependents(root, files, commit, tree)
    _document(
        root,
        LAYOUT_MARKER_PATH,
        LayoutMarker(conversion="1", dependent_links=dependent_link_index(dependents)),
    )
    _write_history(root, files, covered)
