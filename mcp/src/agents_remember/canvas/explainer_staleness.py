"""Which blocks and which parts of an explanation page went stale, and in what order they read.

MIK-R187 makes two things go stale against one target comparison: a **grade**, when the fingerprint
of the block it judges changed (rule 3), and a **part** of a page, when a basis of one of its claims
changed (rule 8). Neither ever reads as current when the comparison cannot be made -- an unmappable
range, a blob the target's tree does not hold, an unreadable record -- so a block or a part is stale
*with its reason* rather than silently current.

This module is pure: it takes the revision (its manifest), the revision's claim map, the target
comparison and the leaf's grades, and answers data -- no file written, no Git command run. The
target's bytes come through :class:`TargetReads`, a small protocol the caller implements with the
reads it already holds open, so every rule below is testable against a fake.

**Fingerprints (rule 3).** The identity's fields are MIK-R89 v2's: an entry's id, side, path, mapped
range and the comparison's trees; a hunk's path, spans and code trees; a record and its revision.
:meth:`BlockIdentity.reference` writes the one text a grade is keyed by. Each kind has its own
fingerprint: a card's ``content`` on each side, a hunk's subject ``hunk:<path>@<base>..<candidate>``
(a pure deletion's span being the base span), a non-text change's ``file:<path>@<blob>``, a record's
revision. A line shift keeps a card's fingerprint for a ``symbol`` locator, and for a ``line_range``
while the recorded blob is held; a ``file`` locator moves with any change to the file.

**Per-part staleness (rule 8).** One predicate per basis kind, each answering the sentence a reader
is owed or ``None`` when the basis still resolves. A part is stale when any claim basis in it is
stale. A revision with no part map, one whose marks are not claim ids of its own map, or one holding
a stale claim no part admits, is stale as a whole: the alternative would name a part that does not
hold the claim. :func:`trusted_list` is the trusted view's record; :func:`regeneration_request` is
the one message behind "Regenerate stale (n parts · m grades)".

**The judgment sort (rule 6).** Subjects keep Priority; inside a subject blocks reorder by grade,
highest first, ties in the order given, ungraded last under ``ungraded (n)``. A stale grade sorts by
its value and keeps its mark: staleness never hides or demotes a judgment.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast
from urllib.parse import unquote

from agents_remember.canvas.explainer_grades import Grade, Grades
from agents_remember.canvas.parts import WHOLE_PAGE_PART, PartEntry
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    ClaimMap,
    EvidenceClaim,
    EvidenceSide,
    ExecutionBasis,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_frame import (
    FrameBinding,
    FrameManifest,
    FrameStalePart,
)
from agents_remember.models.knowledge_files.unexplained import (
    ABSENT,
    file_subject,
    hunk_subject,
    parse_hunk_subject,
)

__all__ = [
    "BlockFingerprint",
    "BlockIdentity",
    "BlockKind",
    "CardSide",
    "GradeState",
    "GradedBlock",
    "HunkCarry",
    "HunkSides",
    "LocatorKind",
    "PartStaleness",
    "ProofEntryRead",
    "RecordKind",
    "RegenerationRequest",
    "StalePart",
    "StalenessContext",
    "SubjectOrder",
    "TargetComparison",
    "TargetReads",
    "TrustedExplanation",
    "basis_stale",
    "card_fingerprint",
    "carry_merged_hunk_grades",
    "file_fingerprint",
    "frame_stale_parts",
    "grade_staleness",
    "hunk_fingerprint",
    "judgment_order",
    "judgment_sort",
    "keeps_fingerprint_across_a_line_shift",
    "moved_hunk_match",
    "parse_block_identity",
    "part_staleness",
    "record_fingerprint",
    "regeneration_request",
    "stale_grade_references",
    "trusted_list",
]

LocatorKind = Literal["symbol", "line_range", "file"]
#: The two record kinds a grade can name (rule 3's last two rows).
RecordKind = Literal["invariant", "family"]
#: The six graded blocks of rule 3's table.
BlockKind = Literal[
    "invariant-card",
    "unexplained-hunk",
    "pure-deletion-hunk",
    "non-text-file",
    "invariant",
    "family",
]
_MEMORY_SIDES = ("memory-base", "memory-candidate")
_TARGET = "the target comparison"


# --- rule 3: identity, fingerprint and the locator kinds ----------------------------------------


def keeps_fingerprint_across_a_line_shift(
    locator: LocatorKind, *, recorded_blob_held: bool
) -> bool:
    """Whether a location's fingerprint survives a line shift (rule 3).

    A ``symbol`` is the same text after a shift; a ``line_range`` maps through the diff while its
    blob is held; a ``file`` hashes the whole file, so any change in it moves the fingerprint.
    """

    if locator == "symbol":
        return True
    if locator == "line_range":
        return recorded_blob_held
    return False


@dataclass(frozen=True)
class BlockIdentity:
    """One block as MIK-R89 v2 identifies it: the key a grade is stored under (rule 3)."""

    kind: BlockKind
    entry: str | None = None
    side: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    base_tree: str | None = None
    candidate_tree: str | None = None
    memory_base_tree: str | None = None
    memory_candidate_tree: str | None = None
    before_span: tuple[int, int] | None = None
    after_span: tuple[int, int] | None = None
    record: str | None = None
    revision: int | None = None

    @classmethod
    def card(
        cls,
        *,
        entry: str,
        side: str,
        path: str,
        lines: tuple[int, int] | None,
        trees: tuple[str, str] | tuple[str, str, str, str],
    ) -> BlockIdentity:
        """One invariant location (a card), with the mapped range and comparison trees."""

        start, end = (None, None) if lines is None else lines
        mem_base, mem_cand = (trees[2], trees[3]) if len(trees) == 4 else (None, None)
        return cls(
            kind="invariant-card",
            entry=entry,
            side=side,
            path=path,
            start_line=start,
            end_line=end,
            base_tree=trees[0],
            candidate_tree=trees[1],
            memory_base_tree=mem_base,
            memory_candidate_tree=mem_cand,
        )

    @classmethod
    def hunk(
        cls,
        *,
        path: str,
        trees: tuple[str, str],
        span: tuple[int, int] | None = None,
        pure_deletion: bool = False,
        spans: tuple[tuple[int, int], tuple[int, int]] | None = None,
    ) -> BlockIdentity:
        """One hunk; a pure deletion is identified by its base span (rule 3)."""

        before, after = (None, None) if spans is None else spans
        if after is not None and after[1] == 0 and (before is None or before[1] > 0):
            pure_deletion = True
        if span is None:
            chosen = before if pure_deletion and before is not None else (after or before or (0, 0))
            span = (chosen[0], chosen[0] + max(chosen[1], 1) - 1)
        return cls(
            kind="pure-deletion-hunk" if pure_deletion else "unexplained-hunk",
            path=path,
            start_line=span[0],
            end_line=span[1],
            base_tree=trees[0],
            candidate_tree=trees[1],
            before_span=before,
            after_span=after,
        )

    @classmethod
    def non_text(cls, *, path: str, trees: tuple[str, str]) -> BlockIdentity:
        """One non-text change: binary, symlink, submodule, type or mode change."""

        return cls(kind="non-text-file", path=path, base_tree=trees[0], candidate_tree=trees[1])

    @classmethod
    def record_at(cls, *, kind: RecordKind, record: str, revision: int) -> BlockIdentity:
        """An invariant or a family at one of its revisions."""

        return cls(kind=kind, record=record, revision=revision)

    @classmethod
    def parse(cls, reference: str, binding: FrameBinding | None = None) -> BlockIdentity | None:
        return parse_block_identity(reference, binding=binding)

    def as_record(self) -> dict[str, Any]:
        """The identity as plain data, without the fields this kind does not carry."""

        fields: dict[str, Any] = {
            "kind": self.kind,
            "entry": self.entry,
            "side": self.side,
            "path": self.path,
            "start": self.start_line,
            "end": self.end_line,
            "before": (
                None
                if self.before_span is None
                else {"start": self.before_span[0], "count": self.before_span[1]}
            ),
            "after": (
                None
                if self.after_span is None
                else {"start": self.after_span[0], "count": self.after_span[1]}
            ),
            "codeBase": self.base_tree,
            "codeCandidate": self.candidate_tree,
            "memoryBase": self.memory_base_tree,
            "memoryCandidate": self.memory_candidate_tree,
            "record": self.record,
            "revision": self.revision,
        }
        return {name: value for name, value in fields.items() if value is not None}

    def block_key(self) -> str:
        """The native L89 ``blockKey`` spelling when all native discriminators are present."""

        if (
            self.kind == "invariant-card"
            and self.memory_base_tree is not None
            and self.memory_candidate_tree is not None
        ):
            side = (
                "before"
                if self.side in {"before", "code-base"}
                else ("after" if self.side == "code-candidate" else (self.side or "after"))
            )
            span = (
                f"{self.start_line}-{self.end_line}"
                if self.start_line is not None and self.end_line is not None
                else "-"
            )
            return "\x00".join(
                [
                    "b",
                    self.entry or "",
                    side,
                    self.path or "",
                    span,
                    self.base_tree or "",
                    self.candidate_tree or "",
                    self.memory_base_tree,
                    self.memory_candidate_tree,
                ]
            )
        if (
            self.kind in {"unexplained-hunk", "pure-deletion-hunk"}
            and self.before_span is not None
            and self.after_span is not None
        ):
            return "\x00".join(
                [
                    "h",
                    self.path or "",
                    f"{self.before_span[0]},{self.before_span[1]}",
                    f"{self.after_span[0]},{self.after_span[1]}",
                    self.base_tree or "",
                    self.candidate_tree or "",
                ]
            )
        return self.reference()

    def reference(self) -> str:
        """The one text a grade is keyed by: this identity's canonical form.

        Sorted keys and no padding, so a re-read gives the same string; a block that gains a tree or
        a line is another reference, so a grade never attaches itself to another block.
        """

        return json.dumps(self.as_record(), sort_keys=True, separators=(",", ":"))


def _span_pair(raw: str) -> tuple[int, int] | None:
    start_s, sep, count_s = raw.partition(",")
    if not sep or not start_s.isdigit() or not count_s.isdigit():
        return None
    return (int(start_s), int(count_s))


def _range_pair(raw: str) -> tuple[int, int] | None:
    if raw == "-":
        return None
    start_s, sep, end_s = raw.partition("-")
    if not sep or not start_s.isdigit() or not end_s.isdigit():
        return None
    return (int(start_s), int(end_s))


def _parse_nul_key(reference: str) -> BlockIdentity | None:
    parts = reference.split("\x00")
    if parts[0] == "b" and len(parts) == 9:
        lines = None if parts[4] == "-" else _range_pair(parts[4])
        if parts[4] != "-" and lines is None:
            return None
        return BlockIdentity.card(
            entry=parts[1],
            side=parts[2],
            path=parts[3],
            lines=lines,
            trees=(parts[5], parts[6], parts[7], parts[8]),
        )
    if parts[0] == "h" and len(parts) == 6:
        before, after = _span_pair(parts[2]), _span_pair(parts[3])
        if before is None or after is None:
            return None
        return BlockIdentity.hunk(
            path=parts[1],
            trees=(parts[4], parts[5]),
            pure_deletion=(after[1] == 0 and before[1] > 0),
            spans=(before, after),
        )
    return None


def _decode_b64_json(encoded: str) -> dict[str, object] | list[object] | None:
    try:
        padded = encoded + "=" * ((4 - len(encoded) % 4) % 4)
        payload: object = json.loads(
            base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return None


def _record_kind(name: str) -> RecordKind | None:
    """The record kind ``name`` spells, or ``None`` when it is another kind of block."""

    if name == "invariant":
        return "invariant"
    if name == "family":
        return "family"
    return None


def _parse_selection_key(reference: str, binding: FrameBinding | None) -> BlockIdentity | None:
    if reference.startswith("b:"):
        payload = _decode_b64_json(reference[2:])
        if not isinstance(payload, list) or len(payload) != 9:
            return None
        lines = (
            (int(payload[3]), int(payload[4]))
            if isinstance(payload[3], int) and isinstance(payload[4], int)
            else None
        )
        return BlockIdentity.card(
            entry=str(payload[0]),
            side=str(payload[1]),
            path=str(payload[2]),
            lines=lines,
            trees=(str(payload[5]), str(payload[6]), str(payload[7]), str(payload[8])),
        )
    if reference.startswith("h:"):
        payload = _decode_b64_json(reference[2:])
        if not isinstance(payload, list) or len(payload) != 7:
            return None
        before = (int(cast(int, payload[1])), int(cast(int, payload[2])))
        after = (int(cast(int, payload[3])), int(cast(int, payload[4])))
        return BlockIdentity.hunk(
            path=str(payload[0]),
            trees=(str(payload[5]), str(payload[6])),
            pure_deletion=(after[1] == 0 and before[1] > 0),
            spans=(before, after),
        )
    if reference.startswith("s:"):
        rest = reference[2:]
        kind, sep, raw_id = rest.partition(":")
        record_kind = _record_kind(kind)
        if sep and record_kind is not None and raw_id:
            rec_id, _, rev_s = unquote(raw_id).partition("@")
            rev = int(rev_s) if rev_s.isdigit() else 1
            return BlockIdentity.record_at(kind=record_kind, record=rec_id, revision=rev)
    del binding
    return None


def _parse_json_card(data: dict[str, Any], trees_obj: dict[str, Any]) -> BlockIdentity:
    rng = data.get("range")
    start = rng.get("start") if isinstance(rng, dict) else data.get("start")
    end = rng.get("end") if isinstance(rng, dict) else data.get("end")
    lines = (int(start), int(end)) if isinstance(start, int) and isinstance(end, int) else None
    code_base = trees_obj.get("codeBase")
    code_cand = trees_obj.get("codeCandidate")
    mem_base = trees_obj.get("memoryBase")
    mem_cand = trees_obj.get("memoryCandidate")
    return BlockIdentity(
        kind="invariant-card",
        entry=str(data["entry"]) if data.get("entry") else None,
        side=str(data["side"]) if data.get("side") else None,
        path=str(data["path"]) if data.get("path") else None,
        start_line=None if lines is None else lines[0],
        end_line=None if lines is None else lines[1],
        base_tree=str(code_base) if code_base else None,
        candidate_tree=str(code_cand) if code_cand else None,
        memory_base_tree=str(mem_base) if mem_base else None,
        memory_candidate_tree=str(mem_cand) if mem_cand else None,
    )


def _parse_json_hunk(data: dict[str, Any], trees_obj: dict[str, Any], kind: str) -> BlockIdentity:
    before_d = data.get("before")
    after_d = data.get("after")
    before = (
        (int(before_d["start"]), int(before_d["count"]))
        if isinstance(before_d, dict) and "start" in before_d and "count" in before_d
        else None
    )
    after = (
        (int(after_d["start"]), int(after_d["count"]))
        if isinstance(after_d, dict) and "start" in after_d and "count" in after_d
        else None
    )
    span = (
        (int(data["start"]), int(data["end"]))
        if isinstance(data.get("start"), int) and isinstance(data.get("end"), int)
        else None
    )
    return BlockIdentity.hunk(
        path=str(data.get("path") or ""),
        span=span,
        trees=(str(trees_obj.get("codeBase") or ""), str(trees_obj.get("codeCandidate") or "")),
        pure_deletion=(kind == "pure-deletion-hunk"),
        spans=(before, after) if before is not None and after is not None else None,
    )


def _parse_json_other(
    data: dict[str, Any], trees_obj: dict[str, Any], kind: str
) -> BlockIdentity | None:
    if kind == "non-text-file":
        return BlockIdentity.non_text(
            path=str(data.get("path") or ""),
            trees=(str(trees_obj.get("codeBase") or ""), str(trees_obj.get("codeCandidate") or "")),
        )
    record_kind = _record_kind(kind)
    if record_kind is not None:
        rev = data.get("revision")
        return BlockIdentity(
            kind=record_kind,
            record=str(data.get("record") or data.get("id") or ""),
            revision=int(rev) if isinstance(rev, int) else None,
        )
    return None


def _parse_json_identity(reference: str) -> BlockIdentity | None:
    try:
        data = json.loads(reference)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("kind") or "")
    raw_trees = data.get("trees")
    trees_obj: dict[str, Any] = raw_trees if isinstance(raw_trees, dict) else data
    if kind in {"invariant-card", "entry"}:
        return _parse_json_card(data, trees_obj)
    if kind in {"unexplained-hunk", "pure-deletion-hunk", "hunk"}:
        return _parse_json_hunk(data, trees_obj, kind)
    return _parse_json_other(data, trees_obj, kind)


def _parse_shorthand_identity(
    reference: str, binding: FrameBinding | None = None
) -> BlockIdentity | None:
    trees: tuple[str, str] | tuple[str, str, str, str] = (
        (
            binding.code_base_tree,
            binding.code_candidate_tree,
            binding.compared_memory_base_tree,
            binding.memory_candidate_tree,
        )
        if binding is not None
        else ("", "")
    )
    code_trees = (trees[0], trees[1])
    if reference.startswith(("inv:", "entry:")):
        body = reference.split(":", 1)[1]
        parts = body.split("/", 2)
        if len(parts) == 3:
            path_part, _, range_s = parts[2].rpartition(":")
            lines = _range_pair(range_s) if path_part else None
            path = path_part if lines is not None else parts[2]
            return BlockIdentity.card(
                entry=parts[0],
                side=parts[1],
                path=path,
                lines=lines,
                trees=trees,
            )
    if reference.startswith("hunk:"):
        parsed = parse_hunk_subject(reference)
        if parsed is not None:
            return BlockIdentity.hunk(
                path=parsed[0],
                span=(1, 1),
                trees=code_trees,
                pure_deletion=(parsed[2] == ABSENT),
            )
    if reference.startswith("file:"):
        path, _, _blob = reference[5:].partition("@")
        if path:
            return BlockIdentity.non_text(path=path, trees=code_trees)
    if reference.startswith(("invariant:", "family:")):
        kind, _, rest = reference.partition(":")
        record_kind = _record_kind(kind)
        rec, _, rev_s = rest.partition("@")
        if rec and record_kind is not None:
            return BlockIdentity(
                kind=record_kind,
                record=rec,
                revision=int(rev_s) if rev_s.isdigit() else None,
            )
    return None


def parse_block_identity(
    reference: str, *, binding: FrameBinding | None = None
) -> BlockIdentity | None:
    """Parse a block reference in native L89 ``blockKey``, URL selection, JSON, or shorthand form."""

    if "\x00" in reference:
        return _parse_nul_key(reference)
    if reference.startswith("{"):
        return _parse_json_identity(reference)
    if reference.startswith(("b:", "h:", "s:")):
        return _parse_selection_key(reference, binding)
    return _parse_shorthand_identity(reference, binding)


@dataclass(frozen=True)
class BlockFingerprint:
    """One block's fingerprint at a comparison, or why it cannot be computed (rule 3, Failure)."""

    block: BlockIdentity
    value: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.reason is None):
            raise ValueError("a fingerprint is either computed or names why it is not")

    @classmethod
    def computed(cls, block: BlockIdentity, value: str) -> BlockFingerprint:
        return cls(block=block, value=value)

    @classmethod
    def uncomputable(cls, block: BlockIdentity, reason: str) -> BlockFingerprint:
        return cls(block=block, reason=reason)

    @property
    def reference(self) -> str:
        return self.block.reference()

    @property
    def current(self) -> bool:
        """Whether a fingerprint exists at all; a grade is current only when it also matches."""

        return self.value is not None


@dataclass(frozen=True)
class CardSide:
    """One side of an invariant location: its ``content``, or that nothing is there.

    ``absent`` is a real fingerprint (a deletion), not an unread side: only an unread side makes the
    fingerprint uncomputable.
    """

    content: str | None = None
    absent: bool = False

    def __post_init__(self) -> None:
        if self.absent and self.content is not None:
            raise ValueError("a side is absent or it has content, never both")

    def identity(self, side: str) -> str:
        """This side's contribution to the card's fingerprint."""

        if self.absent:
            return f"{side}:{ABSENT}"
        assert self.content is not None  # only an absent side carries no content
        return f"{side}:{self.content}"


def _fingerprint(*parts: str) -> str:
    """One fingerprint from its parts: ``sha256:`` over their canonical JSON."""

    payload = json.dumps(list(parts), separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def card_fingerprint(
    *, block: BlockIdentity, base: CardSide, candidate: CardSide, locator: LocatorKind
) -> BlockFingerprint:
    """An invariant location's fingerprint: each side's ``content`` (rule 3's first row).

    The locator kind is part of it because it decides what a line shift does: a ``symbol`` keeps its
    content across a shift, a ``line_range`` while its blob is held, a ``file`` not at all.
    """

    if locator not in ("symbol", "line_range", "file"):
        return BlockFingerprint.uncomputable(block, f"{locator!r} is not a locator kind")
    for name, side in (("base", base), ("candidate", candidate)):
        if not side.absent and side.content is None:
            return BlockFingerprint.uncomputable(
                block,
                f"the {name} side's content of {block.path!r} was not read, so the location's "
                "fingerprint cannot be computed",
            )
    value = _fingerprint(f"card:{locator}", base.identity("base"), candidate.identity("candidate"))
    return BlockFingerprint.computed(block, value)


@dataclass(frozen=True)
class HunkSides:
    """One hunk's changed lines on each side, and the span that identifies it."""

    path: str
    base: str
    candidate: str
    base_span: tuple[int, int]
    candidate_span: tuple[int, int]
    trees: tuple[str, str]

    @classmethod
    def from_lane_hunk(
        cls,
        *,
        path: str,
        subject: str | None,
        before: tuple[int, int],
        after: tuple[int, int],
        trees: tuple[str, str],
    ) -> HunkSides | None:
        """The sides of one review lane hunk, or ``None`` when the lane computed no subject."""

        if subject is None:
            return None
        parsed = parse_hunk_subject(subject)
        if parsed is None or parsed[0] != path:
            return None
        return cls(
            path=path,
            base=parsed[1],
            candidate=parsed[2],
            base_span=before,
            candidate_span=after,
            trees=trees,
        )

    @property
    def pure_deletion(self) -> bool:
        """Whether every changed line is on the base side (rule 3's third row)."""

        return self.candidate == ABSENT

    @property
    def identity_span(self) -> tuple[int, int]:
        """The hunk's inclusive line range: its base span for a pure deletion, else its after span.

        A lane span is Git's ``(start, count)``; an identity states the same lines as an inclusive
        range, so a hunk of four lines from 40 ends at 43.
        """

        start, count = self.base_span if self.pure_deletion else self.candidate_span
        return (start, start + max(count, 1) - 1)

    def identity(self) -> BlockIdentity:
        return BlockIdentity.hunk(
            path=self.path,
            span=self.identity_span,
            trees=self.trees,
            pure_deletion=self.pure_deletion,
            spans=(self.base_span, self.candidate_span),
        )

    def fingerprint(self) -> BlockFingerprint:
        """Its hunk subject: the changed lines' identities on both sides."""

        if self.base == ABSENT and self.candidate == ABSENT:
            return BlockFingerprint.uncomputable(
                self.identity(), f"the hunk {self.path} changes no line on either side"
            )
        return BlockFingerprint.computed(
            self.identity(), hunk_subject(self.path, self.base, self.candidate)
        )


def hunk_fingerprint(
    sides: HunkSides | None, *, path: str, trees: tuple[str, str]
) -> BlockFingerprint:
    """A hunk's fingerprint, or the reason a hunk without a subject cannot have one."""

    if sides is not None:
        return sides.fingerprint()
    block = BlockIdentity.hunk(path=path, span=(0, 0), trees=trees, pure_deletion=False)
    return BlockFingerprint.uncomputable(
        block,
        f"the changed lines of {path!r} were not hashed, so the hunk's fingerprint cannot be "
        "computed",
    )


def moved_hunk_match(*, stored_subject: str, hunks: Sequence[HunkSides]) -> HunkSides | None:
    """The target hunk a stored subject names after its changed lines moved within the file.

    A move keeps the changed lines themselves -- the candidate side -- while the base lines they
    replace change, so the subject cannot be equal and the span does not overlap. The candidate
    content still names the same judgment, carried to the moved span and marked stale. A pure
    deletion names no candidate lines; its base span cannot move, so it is matched by span, never
    here.
    """

    parsed = parse_hunk_subject(stored_subject)
    if parsed is None or parsed[2] == ABSENT:
        return None
    return next((item for item in hunks if item.candidate == parsed[2]), None)


def file_fingerprint(
    *,
    path: str,
    blob: str | None = None,
    trees: tuple[str, str],
    absent: bool = False,
    unread: bool = False,
) -> BlockFingerprint:
    """A non-text change's fingerprint: ``file:<path>@<blob>`` (``@absent`` when deleted)."""

    block = BlockIdentity.non_text(path=path, trees=trees)
    if unread or blob == "":
        return BlockFingerprint.uncomputable(
            block,
            f"the candidate side's blob of {path!r} was not read, so its change cannot be fingerprinted",
        )
    chosen = None if (absent or blob is None or blob == ABSENT) else blob
    return BlockFingerprint.computed(block, file_subject(path, chosen))


def record_fingerprint(*, kind: RecordKind, record: str, revision: int | None) -> BlockFingerprint:
    """An invariant's or a family's fingerprint: its revision (rule 3's last two rows).

    A code change under an invariant does not touch it: only a new revision of the record moves it.
    """

    if revision is None:
        placeholder = BlockIdentity(kind=kind, record=record)
        return BlockFingerprint.uncomputable(
            placeholder, f"the {kind} {record!r} names no revision, so its grade cannot be compared"
        )
    block = BlockIdentity.record_at(kind=kind, record=record, revision=revision)
    return BlockFingerprint.computed(block, f"{kind}:{record}@{revision}")


# --- rule 4's target, asked through one small protocol --------------------------------------------


@dataclass(frozen=True)
class ProofEntryRead:
    """What the target says about one proof entry: its fingerprint and its execution state."""

    fingerprint: str | None = None
    execution: ExecutionBasis | None = None


class TargetReads(Protocol):
    """The target comparison's own answers, as the caller can actually read them.

    Every method answers ``None`` for "this could not be read", never an empty value that would read
    as current; :meth:`registered_version` answers the later version the manifest registers, or None.
    """

    def content_identity(
        self, *, side: EvidenceSide, tree: str, path: str, lines: tuple[int, int] | None
    ) -> str | None:
        """The hash of ``path``'s bytes at ``tree``, over ``lines`` or the whole blob."""

    def mapped_lines(
        self,
        *,
        side: EvidenceSide,
        path: str,
        from_tree: str,
        to_tree: str,
        lines: tuple[int, int],
    ) -> tuple[int, int] | None:
        """``lines`` read at ``from_tree``, as the same lines in ``to_tree``; ``None`` unmappable."""

    def memory_revision(self, *, side: str, record: str, path: str) -> int | None:
        """The revision one memory side holds for ``record`` at ``path``, or ``None`` for none."""

    def proof_entry(self, *, proof: str, record: str) -> ProofEntryRead | None:
        """One proof entry as the target reads it, or ``None`` when it no longer resolves."""

    def judgment_rows(self, *, record: str) -> tuple[tuple[str, int], ...] | None:
        """Every ``(row id, attempt)`` one judgment record holds at the target."""

    def history_rows(self, *, path: str) -> tuple[str, ...] | None:
        """Every row id one history file holds at the target."""

    def receipts(self) -> tuple[SupplementalReceipt, ...] | None:
        """The target packet's supplemental receipts."""

    def registered_version(self, *, source: str) -> int | None:
        """The later version the manifest registers for ``source``, or ``None`` for none."""


@dataclass(frozen=True)
class TargetComparison:
    """The one comparison grades and parts are compared against (rule 4)."""

    binding: FrameBinding
    reads: TargetReads

    def tree_for(self, side: EvidenceSide) -> str:
        """The target's tree for one side, read from the binding and never guessed."""

        trees = {
            "code-base": self.binding.code_base_tree,
            "code-candidate": self.binding.code_candidate_tree,
            "memory-base": self.binding.compared_memory_base_tree,
            "memory-candidate": self.binding.memory_candidate_tree,
        }
        return trees[side]


def moved_sides(retained: FrameBinding, selected: FrameBinding) -> tuple[str, ...]:
    """The comparison's own identity test: the owner, the number and every side (rule 4).

    The owner is part of the identity, so a planned revision never reads as current against the
    landed leaf's number-0 comparison.
    """

    names = ("code-base", "code-candidate", "memory-base", "memory-candidate")
    changed = [
        name
        for name, held, now in zip(
            names, retained.side_identity(), selected.side_identity(), strict=True
        )
        if held != now
    ]
    if retained.conversion_identity() != selected.conversion_identity():
        changed.append("converted-base")
    if (retained.comparison_owner, retained.task_id, retained.leaf_id) != (
        selected.comparison_owner,
        selected.task_id,
        selected.leaf_id,
    ) or retained.comparison_number != selected.comparison_number:
        changed.append("comparison")
    return tuple(changed)


@dataclass(frozen=True)
class StalenessContext:
    """Everything one staleness comparison needs besides the basis itself."""

    retained: FrameBinding
    target: TargetComparison
    receipts: Mapping[str, SupplementalReceipt] = field(default_factory=dict)
    recorded_fingerprints: Mapping[str, str] = field(default_factory=dict)

    @property
    def moved(self) -> tuple[str, ...]:
        """Which sides of the comparison moved between the revision's binding and the target."""

        return moved_sides(self.retained, self.target.binding)


# --- rule 8: one predicate per basis kind ---------------------------------------------------------


def tree_blob_stale(basis: ClaimBasis, context: StalenessContext) -> str | None:
    """Stale when the range's hash at its own tree differs from the same range at the target."""

    if basis.side is None or basis.tree is None or basis.path is None:
        return "the basis names no side, tree and path, so no range resolves at the target"
    side, path = basis.side, basis.path
    recorded = (
        None
        if basis.start_line is None or basis.end_line is None
        else (basis.start_line, basis.end_line)
    )
    target_tree = context.target.tree_for(side)
    where = path if recorded is None else f"{path}:{recorded[0]}-{recorded[1]}"
    mapped = recorded
    if recorded is not None:
        mapped = context.target.reads.mapped_lines(
            side=side, path=path, from_tree=basis.tree, to_tree=target_tree, lines=recorded
        )
        if mapped is None:
            return (
                f"{where} read at tree {basis.tree[:12]} maps to no range in the target's "
                f"{side} tree {target_tree[:12]}"
            )
    at_basis = context.target.reads.content_identity(
        side=side, tree=basis.tree, path=path, lines=recorded
    )
    at_target = context.target.reads.content_identity(
        side=side, tree=target_tree, path=path, lines=mapped
    )
    if at_basis is None:
        return f"the {side} tree {basis.tree[:12]} holds no readable {where}"
    if at_target is None:
        return f"the target's {side} tree {target_tree[:12]} holds no readable {where}"
    if at_basis != at_target:
        return f"the {side} bytes of {where} changed: {at_basis} is not {at_target}"
    return None


def knowledge_record_stale(basis: ClaimBasis, context: StalenessContext) -> str | None:
    """Stale when the named memory side holds another revision of the record, or none."""

    if basis.record_id is None or basis.record_revision is None or basis.path is None:
        return "the basis names no record, revision and path, so no record resolves"
    side = basis.side if basis.side in _MEMORY_SIDES else "memory-candidate"
    held = context.target.reads.memory_revision(side=side, record=basis.record_id, path=basis.path)
    if held is None:
        return (
            f"the target's {side} memory holds no record {basis.record_id!r} at {basis.path}, "
            f"where the claim read revision {basis.record_revision}"
        )
    if held != basis.record_revision:
        return (
            f"the record {basis.record_id!r} is at revision {held} on the target's {side} memory, "
            f"not the recorded {basis.record_revision}"
        )
    return None


def _proof_fingerprint_stale(
    proof_id: str, entry: ProofEntryRead, recorded: str | None
) -> str | None:
    if entry.fingerprint is None:
        return (
            f"the fingerprint of proof entry {proof_id!r} cannot be computed at the target, "
            "so the claim cannot be called current"
        )
    if recorded is None:
        return (
            f"the revision recorded no baseline fingerprint for proof entry {proof_id!r}, "
            "so the claim cannot be called current"
        )
    if entry.fingerprint != recorded:
        return f"the proof entry {proof_id!r} changed: {recorded} is not {entry.fingerprint}"
    return None


def proof_entry_stale(basis: ClaimBasis, context: StalenessContext) -> str | None:
    """Stale when the entry's fingerprint or its execution state differs at the target."""

    if basis.proof_id is None or basis.record_id is None:
        return "the basis names no proof entry, so no execution resolves"
    entry = context.target.reads.proof_entry(proof=basis.proof_id, record=basis.record_id)
    if entry is None:
        return (
            f"the target holds no proof entry {basis.proof_id!r} of {basis.record_id!r}: the test "
            "the claim rested on is gone"
        )
    if basis.execution is not None and entry.execution != basis.execution:
        return (
            f"the proof entry {basis.proof_id!r} is {entry.execution or 'unexecuted'} at the "
            f"target, where the claim read it {basis.execution}"
        )
    return _proof_fingerprint_stale(
        basis.proof_id, entry, context.recorded_fingerprints.get(basis.proof_id)
    )


def judgment_row_stale(basis: ClaimBasis, context: StalenessContext) -> str | None:
    """Stale when the target holds no such row and attempt, or holds a later attempt of it."""

    if basis.record_id is None or basis.row_id is None or basis.attempt is None:
        return "the basis names no judgment row and attempt, so no row resolves"
    rows = context.target.reads.judgment_rows(record=basis.record_id)
    if rows is None:
        return (
            f"the target's judgment record {basis.record_id!r} cannot be read, so row "
            f"{basis.row_id!r} cannot be called current"
        )
    attempts = [attempt for row, attempt in rows if row == basis.row_id]
    if not attempts:
        return (
            f"the target's judgment record {basis.record_id!r} holds no row {basis.row_id!r}, "
            f"where the claim read attempt {basis.attempt}"
        )
    later = [attempt for attempt in attempts if attempt > basis.attempt]
    if later:
        return (
            f"row {basis.row_id!r} is at attempt {max(later)} at the target, later than the "
            f"recorded attempt {basis.attempt}"
        )
    if basis.attempt not in attempts:
        return (
            f"the target's judgment record {basis.record_id!r} holds no attempt {basis.attempt} "
            f"of row {basis.row_id!r}"
        )
    return None


def history_event_stale(basis: ClaimBasis, context: StalenessContext) -> str | None:
    """Stale when the target's history file holds no such row."""

    if basis.path is None or basis.row_id is None:
        return "the basis names no history file and row, so no event resolves"
    rows = context.target.reads.history_rows(path=basis.path)
    if rows is None:
        return (
            f"the target's history file {basis.path} cannot be read, so row {basis.row_id!r} "
            "cannot be called current"
        )
    if basis.row_id not in rows:
        return f"the target's history file {basis.path} holds no row {basis.row_id!r}"
    return None


def supplemental_receipt_stale(basis: ClaimBasis, context: StalenessContext) -> str | None:
    """Stale when the target holds no receipt of those bytes, or a later version is registered."""

    receipt = context.receipts.get(basis.receipt_id or "")
    if receipt is None:
        return (
            f"the revision's packet holds no receipt {basis.receipt_id!r}, so the bytes the claim "
            "examined cannot be compared"
        )
    if receipt.digest is None:
        return (
            f"the receipt {receipt.receipt_id!r} names no examined bytes, so nothing about it can "
            "be current"
        )
    held = context.target.reads.receipts()
    if held is None:
        return (
            f"the target's packet cannot be read, so no receipt of {receipt.canonical_source} "
            "resolves"
        )
    if not any(
        one.canonical_source == receipt.canonical_source and one.digest == receipt.digest
        for one in held
    ):
        return (
            f"the target's packet holds no receipt of {receipt.canonical_source} with digest "
            f"{receipt.digest}"
        )
    later = context.target.reads.registered_version(source=receipt.canonical_source)
    if later is not None:
        return (
            f"the requirements manifest registers version {later} of "
            f"{receipt.canonical_source}, later than the receipt's own version"
        )
    return None


def _comparison_moved(context: StalenessContext) -> str | None:
    """Which sides moved, as one sentence fragment, or ``None`` when the comparison stands still."""

    moved = context.moved
    return None if not moved else ", ".join(moved)


def comparison_field_stale(_basis: ClaimBasis, context: StalenessContext) -> str | None:
    """Stale when any side of the comparison moved: the basis names nothing narrower."""

    moved = _comparison_moved(context)
    if moved is None:
        return None
    return (
        "the basis names a comparison field rather than a block, so any side that moves makes it "
        f"stale: {moved} moved"
    )


_BASIS_PREDICATES: dict[str, Callable[[ClaimBasis, StalenessContext], str | None]] = {
    "tree-blob": tree_blob_stale,
    "knowledge-record": knowledge_record_stale,
    "proof-entry": proof_entry_stale,
    "judgment-row": judgment_row_stale,
    "history-event": history_event_stale,
    "supplemental-receipt": supplemental_receipt_stale,
    "comparison-field": comparison_field_stale,
    "worklist-row": comparison_field_stale,
}


def basis_stale(basis: ClaimBasis | None, context: StalenessContext) -> str | None:
    """Why one claim basis is stale at the target, or ``None`` when it still resolves.

    A claim with no basis rests on the comparison itself: any side that moved makes it stale. An
    unknown kind is stale too -- a basis this module cannot read is not a basis it may call current.
    """

    if basis is None:
        moved = _comparison_moved(context)
        if moved is None:
            return None
        return (
            f"the claim names no basis, so nothing below the comparison supports it: {moved} moved"
        )
    predicate = _BASIS_PREDICATES.get(basis.kind)
    if predicate is None:
        return f"{basis.kind!r} is not a basis kind this reader resolves, so the claim is stale"
    return predicate(basis, context)


# --- claims, parts and the trusted list -----------------------------------------------------------


@dataclass(frozen=True)
class StalePart:
    """One stale part of a page: its id, the title a reader sees, and why it is stale."""

    part: str
    title: str
    reasons: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "part": self.part,
            "title": self.title,
            "claims": list(self.claims),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class PartStaleness:
    """One revision's per-part staleness against one target comparison (rule 8)."""

    revision: int
    target: str
    parts: tuple[StalePart, ...] = ()
    whole_page: bool = False
    checked: int = 0
    unmapped_claims: tuple[str, ...] = ()

    @property
    def part_ids(self) -> tuple[str, ...]:
        return tuple(part.part for part in self.parts)

    def as_record(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "target": self.target,
            "wholePage": self.whole_page,
            "checked": self.checked,
            "staleParts": [part.as_record() for part in self.parts],
            "unmappedClaims": list(self.unmapped_claims),
        }


def _claim_reasons(claim: EvidenceClaim, context: StalenessContext) -> tuple[str, ...]:
    """One sentence per basis of the claim that no longer resolves at the target."""

    if not claim.bases:
        why = basis_stale(None, context)
        return () if why is None else (f"no basis: {why}",)
    return tuple(
        f"{basis.kind} {basis.reference}: {why}"
        for basis in claim.bases
        if (why := basis_stale(basis, context)) is not None
    )


def _context(
    manifest: FrameManifest,
    target: TargetComparison,
    receipts: Mapping[str, SupplementalReceipt] | None,
    recorded_fingerprints: Mapping[str, str] | None,
) -> StalenessContext:
    """The revision's own receipts and the caller's recorded fingerprints, in one context."""

    held = (
        {receipt.receipt_id: receipt for receipt in manifest.supplemental}
        if receipts is None
        else dict(receipts)
    )
    return StalenessContext(
        retained=manifest.binding,
        target=target,
        receipts=held,
        recorded_fingerprints=dict(recorded_fingerprints or {}),
    )


def _declared_parts(
    manifest: FrameManifest, claims: ClaimMap
) -> tuple[tuple[PartEntry, ...], bool, tuple[str, ...]]:
    """The revision's parts, whether its staleness is the page's, and the claims no part admits.

    A page without parts is one part holding every claim. A part map listing something that is not a
    claim id of its own map is an older revision: it has no per-part answer, and rule 8 lets it go
    stale as a whole.
    """

    known = tuple(claim.claim_id for claim in claims.claims)
    map_holds_ids = all(claim in set(known) for part in manifest.part_map for claim in part.claims)
    if not manifest.part_map or not map_holds_ids:
        return ((PartEntry(WHOLE_PAGE_PART, "the page", known),), True, ())
    parts = tuple(
        PartEntry(part.part, part.title, tuple(part.claims)) for part in manifest.part_map
    )
    mapped = {claim for part in parts for claim in part.claims}
    return (parts, False, tuple(claim for claim in known if claim not in mapped))


def part_staleness(
    manifest: FrameManifest,
    claims: ClaimMap,
    target: TargetComparison,
    *,
    receipts: Mapping[str, SupplementalReceipt] | None = None,
    recorded_fingerprints: Mapping[str, str] | None = None,
) -> PartStaleness:
    """Which parts of one revision are stale against one target comparison (rule 8).

    A part is stale when a basis of one of its claims is stale, and the reason names the basis and
    what changed. A page with no per-part answer -- no map, a map of basis references, or a stale
    claim no part admits -- is stale as a whole.
    """

    context = _context(manifest, target, receipts, recorded_fingerprints)
    stale = {
        claim.claim_id: reasons
        for claim in claims.claims
        if (reasons := _claim_reasons(claim, context))
    }
    parts, whole_page, unmapped = _declared_parts(manifest, claims)
    named = _stale_parts(parts, stale)
    stranded = tuple(claim for claim in unmapped if claim in stale)
    if stranded:
        whole_page = True
        named = (
            *named,
            StalePart(
                part=WHOLE_PAGE_PART,
                title="the page",
                reasons=tuple(reason for claim in stranded for reason in stale[claim]),
                claims=stranded,
            ),
        )
    return PartStaleness(
        revision=manifest.revision,
        target=target.binding.comparison_reference,
        parts=named,
        whole_page=whole_page,
        checked=len(parts),
        unmapped_claims=unmapped,
    )


def _stale_parts(
    parts: Iterable[PartEntry], stale: Mapping[str, tuple[str, ...]]
) -> tuple[StalePart, ...]:
    """Every part holding at least one stale claim, in the page's own order."""

    named: list[StalePart] = []
    for part in parts:
        claims = tuple(claim for claim in part.claims if claim in stale)
        if not claims:
            continue
        named.append(
            StalePart(
                part=part.part_id,
                title=part.title,
                reasons=tuple(dict.fromkeys(reason for claim in claims for reason in stale[claim])),
                claims=claims,
            )
        )
    return tuple(named)


def frame_stale_parts(report: PartStaleness) -> tuple[FrameStalePart, ...]:
    """The report as the manifest's own staleness record, for a ``FrameStaleness`` answer."""

    return tuple(
        FrameStalePart(part=part.part, title=part.title, reasons=part.reasons)
        for part in report.parts
    )


@dataclass(frozen=True)
class TrustedExplanation:
    """The Explanation view's own record of staleness: every stale part, its title and why.

    Nothing drawn inside the frame changes these lines (MIK-R104 rule 3), and a current revision
    still gets the line that says so.
    """

    revision: int
    target: str
    lines: tuple[str, ...] = ()
    parts: tuple[StalePart, ...] = ()

    @property
    def stale(self) -> bool:
        return bool(self.parts)


def trusted_list(report: PartStaleness) -> TrustedExplanation:
    """The stale parts as the trusted view states them: one line for the window, one per part."""

    if not report.parts:
        lines = (
            f"revision {report.revision} describes {report.target} in "
            f"{report.checked} part(s), and every claim basis still resolves",
        )
        return TrustedExplanation(revision=report.revision, target=report.target, lines=lines)
    where = "as a whole" if report.whole_page else f"in {len(report.parts)} part(s)"
    lines = (
        f"revision {report.revision} is stale {where} against {report.target}",
        *(f"{part.title} ({part.part}): " + "; ".join(part.reasons) for part in report.parts),
    )
    return TrustedExplanation(
        revision=report.revision, target=report.target, lines=lines, parts=report.parts
    )


# --- rule 3's grades and rule 8's regeneration request --------------------------------------------


@dataclass(frozen=True)
class GradeState:
    """One grade and whether its block still carries the fingerprint the grade was made at."""

    grade: Grade
    stale: bool
    reason: str | None = None

    @property
    def reference(self) -> str:
        return self.grade.reference


def grade_staleness(
    grades: Grades, fingerprints: Mapping[str, BlockFingerprint]
) -> tuple[GradeState, ...]:
    """Every stored grade, current or stale, against the blocks' fingerprints at the target.

    A block whose fingerprint cannot be computed makes its grade stale with that reason.
    """

    return tuple(_grade_state(grade, fingerprints.get(grade.reference)) for grade in grades.grades)


def _grade_state(grade: Grade, now: BlockFingerprint | None) -> GradeState:
    """One grade against its block's fingerprint at the target."""

    if now is None:
        return GradeState(
            grade=grade,
            stale=True,
            reason=(
                f"the block's fingerprint at {_TARGET} cannot be computed, so the grade cannot be "
                "called current"
            ),
        )
    if not now.current:
        return GradeState(grade=grade, stale=True, reason=now.reason)
    if now.value != grade.fingerprint:
        return GradeState(
            grade=grade,
            stale=True,
            reason=f"the block changed: {grade.fingerprint} is not {now.value}",
        )
    return GradeState(grade=grade, stale=False)


def stale_grade_references(states: Sequence[GradeState]) -> tuple[str, ...]:
    """The blocks whose grades are stale, each once, in the order the store holds them."""

    return tuple(dict.fromkeys(state.reference for state in states if state.stale))


@dataclass(frozen=True)
class HunkCarry:
    """Hunks that merged into one target hunk: their grades carried onto it, all stale.

    Rule 3 keeps the judgments: the merged hunk's chip shows the highest grade it carries.
    """

    target: BlockFingerprint
    carried: tuple[GradeState, ...] = ()
    chip: str | None = None
    hover: tuple[dict[str, Any], ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "reference": self.target.reference,
            "fingerprint": self.target.value,
            "chip": self.chip,
            "hover": list(self.hover),
            "carried": [
                {
                    "reference": state.reference,
                    "category": state.grade.category,
                    "grade": state.grade.grade,
                    "reason": state.grade.reason,
                    "stale": state.stale,
                    "staleReason": state.reason,
                }
                for state in self.carried
            ],
        }


def carry_merged_hunk_grades(
    *,
    target: BlockFingerprint,
    sources: Sequence[Grade],
    scale: Sequence[str],
    active_category: str | None = None,
) -> HunkCarry:
    """Carry the grades of merged hunks onto the one hunk that now holds their lines."""

    reason = f"the hunk merged into {target.reference}, so its grade is stale until it is redone"
    carried = tuple(
        GradeState(
            grade=grade.model_copy(
                update={
                    "reference": target.reference,
                    "stale": True,
                    "stale_reason": reason,
                }
            ),
            stale=True,
            reason=reason,
        )
        for grade in sources
    )
    ordered = sorted(
        enumerate(carried),
        key=lambda item: (
            0 if active_category and item[1].grade.category == active_category else 1,
            scale.index(item[1].grade.grade) if item[1].grade.grade in scale else len(scale),
            item[0],
        ),
    )
    hover = tuple(
        {
            "category": state.grade.category,
            "grade": state.grade.grade,
            "reason": state.grade.reason,
            "stale": True,
            "staleReason": state.reason,
        }
        for _index, state in ordered
    )
    ranked = [
        grade.grade
        for grade in sources
        if grade.grade in scale and (active_category is None or grade.category == active_category)
    ] or [grade.grade for grade in sources if grade.grade in scale]
    chip = min(ranked, key=scale.index) if ranked else None
    return HunkCarry(target=target, carried=carried, chip=chip, hover=hover)


@dataclass(frozen=True)
class RegenerationRequest:
    """The one message behind "Regenerate stale (n parts · m grades)" (rule 8).

    It names both counts, because the mockup's single number hid the two the Update step names; its
    parts and grade references also fill the first-request form when no seat is open.
    """

    label: str
    message: str
    parts: tuple[StalePart, ...] = ()
    grade_references: tuple[str, ...] = ()
    part_count: int = 0
    grade_count: int = 0


def regeneration_request(
    report: PartStaleness, states: Sequence[GradeState]
) -> RegenerationRequest:
    """The regeneration message: the stale parts with their changed bases, and the stale grades."""

    references = stale_grade_references(states)
    label = (
        f"Regenerate stale ({_count(len(report.parts), 'part')} · "
        f"{_count(len(references), 'grade')})"
    )
    if report.parts:
        parts_lines = [
            f"The {_count(len(report.parts), 'stale part')}:",
            *(f"- {part.title} ({part.part}): " + "; ".join(part.reasons) for part in report.parts),
        ]
    else:
        parts_lines = [
            "No part is stale: every claim basis still resolves against this comparison."
        ]
    lines = [
        f"Regenerate revision {report.revision} against {report.target}.",
        *parts_lines,
        *_grade_lines(states, references),
        (
            "Nothing regenerates by itself: this is the message your press sends, and only the "
            "stale parts and grades are redone."
        ),
    ]
    return RegenerationRequest(
        label=label,
        message="\n".join(lines),
        parts=report.parts,
        grade_references=references,
        part_count=len(report.parts),
        grade_count=len(references),
    )


def _grade_lines(states: Sequence[GradeState], references: Sequence[str]) -> list[str]:
    """The message's grade paragraph: which blocks' grades are stale, and with what grade."""

    if not references:
        return ["No grade is stale: every graded block still carries its fingerprint."]
    by_reference = {state.reference: state for state in states if state.stale}
    return [
        f"The {_count(len(references), 'stale grade')}:",
        *(
            f"- {reference} ({by_reference[reference].grade.grade}): "
            f"{by_reference[reference].reason or 'the block changed'}"
            for reference in references
        ),
    ]


def _count(number: int, noun: str) -> str:
    """``2 parts`` / ``1 part``: a count always reads with its noun, never as a bare number."""

    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


# --- rule 6: the judgment sort --------------------------------------------------------------------


@dataclass(frozen=True)
class GradedBlock:
    """One block as a judgment sort sees it: its reference, its grade and its stale mark."""

    reference: str
    grade: str | None = None
    stale: bool = False


@dataclass(frozen=True)
class SubjectOrder:
    """One subject's blocks in judgment order, with the ungraded group kept apart."""

    subject: str
    blocks: tuple[GradedBlock, ...] = ()
    ungraded: tuple[GradedBlock, ...] = ()

    @property
    def ungraded_label(self) -> str:
        """The heading the ungraded blocks read under, with its count (rule 6)."""

        return f"ungraded ({len(self.ungraded)})"


def judgment_order(
    subject: str, blocks: Sequence[GradedBlock], *, scale: Sequence[str]
) -> SubjectOrder:
    """One subject's blocks by grade, highest first, ties in the order given (rule 6).

    The order given is Priority's block order, so a tie falls back to it exactly; ungraded blocks
    come last under ``ungraded (n)``. A stale grade sorts by its value and keeps its mark.
    """

    graded = [(index, block) for index, block in enumerate(blocks) if block.grade in scale]
    ungraded = [(index, block) for index, block in enumerate(blocks) if block.grade not in scale]
    ranked = sorted(graded, key=lambda item: (scale.index(str(item[1].grade)), item[0]))
    return SubjectOrder(
        subject=subject,
        blocks=tuple(block for _index, block in (*ranked, *ungraded)),
        ungraded=tuple(block for _index, block in ungraded),
    )


def judgment_sort(
    subjects: Sequence[tuple[str, Sequence[GradedBlock]]], *, scale: Sequence[str]
) -> tuple[SubjectOrder, ...]:
    """Every subject in Priority's own order, its blocks reordered by the judgment sort (rule 6).

    Only the blocks inside one subject move, so a reader who learned where a subject sits still
    finds it there under the judgment sort.
    """

    return tuple(judgment_order(subject, blocks, scale=scale) for subject, blocks in subjects)
