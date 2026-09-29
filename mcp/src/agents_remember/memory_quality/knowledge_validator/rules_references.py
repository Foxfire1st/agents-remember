"""MIK-R22's cross-file rules: markers and references, record links, families and anchor paths.

None of these rules reads a history file: its anchors and subjects are historical (rule 7).

* **Rule 3, markers.** Every unescaped ``[n]`` marker of an onboarding Markdown file has a reference
  in its sidecar, and every reference is used. A Markdown file without a sidecar -- a record's
  Markdown included -- has no markers. A file sidecar without Markdown holds no references; the
  sidecar itself is reported, not refused (MIK-R21 rule 5), by a report-only rule. A route sidecar
  sits beside its ``overview.md``.
* **Rule 3, record links.** Every record ID a sidecar reference, a sidecar entry or a record's
  ``links`` and ``supersedes`` names exists in the tree. Records are never deleted, so a retired
  record still resolves.
* **Rule 3, relations.** Every relation is admitted for its record kind.
* **Rule 3, unresolved targets** are reported, never refused.
* **Rule 5, families.** Every member ID exists.
* **Rule 6, anchor paths.** An anchor is checked for path existence in the paired code tree only
  when no comparison base holds an equal anchor for the same entry (by entry ID) or the same
  reference target (in the same sidecar). A carried anchor whose path is absent is reported as
  stale, never refused, so the stalest knowledge can still be represented and repaired.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from agents_remember.memory_quality.knowledge_validator.markers import Marker, find_markers
from agents_remember.memory_quality.knowledge_validator.parsed import RecordFile, SidecarFile
from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    anchor_key,
    register_rule,
    sidecar_entries,
)
from agents_remember.models.knowledge_files.shapes import (
    ROUTE_TARGET_PREFIX,
    AnchorTarget,
    IdTarget,
    UnresolvedTarget,
)
from agents_remember.models.knowledge_files.sidecars import FileSidecar, RealizationEntry

_ESCAPE_HINT = "write it as \\[n] or put it in a code span if it is not a marker"


def _unpaired_marker_message(marker: Marker, sidecar: SidecarFile | None) -> str | None:
    if not marker.valid:
        return f"[{marker.number}] is not a reference number; {_ESCAPE_HINT}"
    if sidecar is None:
        return f"marker [{marker.number}] in a Markdown file without a sidecar; {_ESCAPE_HINT}"
    if marker.number not in sidecar.sidecar.references:
        return f"marker [{marker.number}] has no reference in {sidecar.path}"
    return None


def _markdown_findings(path: str, text: str, sidecar: SidecarFile | None) -> Iterator[Finding]:
    markers = find_markers(text)
    for marker in markers:
        message = _unpaired_marker_message(marker, sidecar)
        if message is not None:
            yield Finding(path, f"line {marker.line}", message)
    if sidecar is None:
        return
    used = {marker.number for marker in markers if marker.valid}
    for number in sorted(set(sidecar.sidecar.references) - used, key=int):
        yield Finding(
            sidecar.path, f"references.{number}", f"reference {number} is never used in {path}"
        )


def check_markers(context: ValidationContext) -> Iterator[Finding]:
    by_markdown = {sidecar.markdown_path: sidecar for sidecar in context.parsed.sidecars}
    for sidecar in context.parsed.sidecars:
        if sidecar.markdown_path in context.parsed.markdown:
            continue
        if isinstance(sidecar.sidecar, FileSidecar):
            for number in sorted(sidecar.sidecar.references, key=int):
                yield Finding(
                    sidecar.path,
                    f"references.{number}",
                    "a file sidecar without Markdown holds no references (MIK-R21 rule 5)",
                )
        else:
            yield Finding(sidecar.path, "", f"a route sidecar sits beside {sidecar.markdown_path}")
    for path, text in sorted(context.parsed.markdown.items()):
        sidecar = by_markdown.get(path)
        paired_json = f"{path.removesuffix('.md')}.json"
        if (
            sidecar is None
            and path.startswith("onboarding/")
            and paired_json in context.candidate.files
        ):
            continue  # the sidecar does not parse; its shape violation is reported instead
        yield from _markdown_findings(path, text, sidecar)


def check_sidecar_without_markdown(context: ValidationContext) -> Iterator[Finding]:
    for sidecar in context.parsed.sidecars:
        if isinstance(sidecar.sidecar, FileSidecar) and (
            sidecar.markdown_path not in context.parsed.markdown
        ):
            yield Finding(
                sidecar.path,
                "",
                "file sidecar without Markdown: its entries wait for a curator to re-home or retire "
                "them (MIK-R21 rule 5)",
            )


def _missing(context: ValidationContext, path: str, field: str, target: str) -> Iterator[Finding]:
    if target not in context.record_ids:
        yield Finding(path, field, f"{target} names no record in this tree")


def _sidecar_record_links(context: ValidationContext, sidecar: SidecarFile) -> Iterator[Finding]:
    for number, reference in sidecar.sidecar.references.items():
        for index, target in enumerate(reference.targets):
            if isinstance(target, IdTarget):
                field = f"references.{number}.targets.{index}.id"
                yield from _missing(context, sidecar.path, field, target.id)
    for entry in sidecar_entries(sidecar):
        kind = "realizes" if isinstance(entry, RealizationEntry) else "proves"
        yield from _missing(context, sidecar.path, f"{kind}.{entry.id}.invariant", entry.invariant)


def _record_links(context: ValidationContext, record: RecordFile) -> Iterator[Finding]:
    for index, superseded in enumerate(getattr(record.record, "supersedes", ())):
        yield from _missing(context, record.path, f"supersedes.{index}", superseded)
    for index, link in enumerate(getattr(record.record, "links", ())):
        if isinstance(link.target, str) and not link.target.startswith(ROUTE_TARGET_PREFIX):
            yield from _missing(context, record.path, f"links.{index}.target", link.target)


def check_record_links(context: ValidationContext) -> Iterator[Finding]:
    for sidecar in context.parsed.sidecars:
        yield from _sidecar_record_links(context, sidecar)
    for record in context.parsed.records:
        yield from _record_links(context, record)


def check_relations(context: ValidationContext) -> Iterator[Finding]:
    """Relations are admitted per record kind; a disallowed one is found while parsing."""

    for problem in context.parsed.problems:
        if problem.category == "relation":
            yield Finding(problem.path, problem.field, problem.message)


def check_unresolved_targets(context: ValidationContext) -> Iterator[Finding]:
    for sidecar in context.parsed.sidecars:
        for number, reference in sidecar.sidecar.references.items():
            for index, target in enumerate(reference.targets):
                if isinstance(target, UnresolvedTarget):
                    yield Finding(
                        sidecar.path,
                        f"references.{number}.targets.{index}",
                        f"unresolved reference target: {target.text}",
                    )


def check_family_members(context: ValidationContext) -> Iterator[Finding]:
    for record in context.parsed.records:
        for index, member in enumerate(getattr(record.record, "members", ())):
            yield from _missing(context, record.path, f"members.{index}", member)


@dataclass(frozen=True)
class _SidecarAnchor:
    sidecar: str
    field: str
    path: str
    carried: bool


def _sidecar_anchors(context: ValidationContext) -> Iterator[_SidecarAnchor]:
    carried = context.base_anchors
    for sidecar in context.parsed.sidecars:
        for number, reference in sidecar.sidecar.references.items():
            for index, target in enumerate(reference.targets):
                if not isinstance(target, AnchorTarget):
                    continue
                key = anchor_key(target.anchor, sidecar)
                yield _SidecarAnchor(
                    sidecar.path,
                    f"references.{number}.targets.{index}.anchor.path",
                    target.anchor.path or sidecar.sidecar.path,
                    carried.carries_reference(sidecar.path, key),
                )
        for entry in sidecar_entries(sidecar):
            key = anchor_key(entry.anchor, sidecar)
            kind = "realizes" if isinstance(entry, RealizationEntry) else "proves"
            yield _SidecarAnchor(
                sidecar.path,
                f"{kind}.{entry.id}.anchor",
                sidecar.sidecar.path,
                carried.carries_entry(entry.id, key),
            )


def check_anchor_paths(context: ValidationContext) -> Iterator[Finding]:
    code = context.code
    if context.conversion or code is None:
        return
    for anchor in _sidecar_anchors(context):
        if not anchor.carried and not code.has_file(anchor.path):
            yield Finding(
                anchor.sidecar,
                anchor.field,
                f"{anchor.path} does not exist in the paired code tree {code.label}",
            )


def check_carried_stale(context: ValidationContext) -> Iterator[Finding]:
    code = context.code
    if code is None:
        return
    for anchor in _sidecar_anchors(context):
        if anchor.carried and not code.has_file(anchor.path):
            yield Finding(
                anchor.sidecar,
                anchor.field,
                f"carried anchor names {anchor.path}, absent from {code.label}: a stale entry "
                "(MIK-R03) or stale reference (MIK-R24 rule 5), for the maintenance pass",
            )


REFERENCE_RULES = (
    ValidationRule(
        "R22.3-markers", "MIK-R22 rule 3", "markers and references match", check_markers
    ),
    ValidationRule(
        "R22.3-sidecar-without-markdown",
        "MIK-R22 rule 3 (MIK-R21 rule 5)",
        "a file sidecar without Markdown is reported",
        check_sidecar_without_markdown,
        report_only=True,
    ),
    ValidationRule(
        "R22.3-record-links", "MIK-R22 rule 3", "referenced record IDs exist", check_record_links
    ),
    ValidationRule(
        "R22.3-relations", "MIK-R22 rule 3", "relations are admitted per kind", check_relations
    ),
    ValidationRule(
        "R22.3-unresolved-target",
        "MIK-R22 rule 3",
        "unresolved reference targets are reported",
        check_unresolved_targets,
        report_only=True,
    ),
    ValidationRule(
        "R22.5-family-members", "MIK-R22 rule 5", "family members exist", check_family_members
    ),
    ValidationRule(
        "R22.6-anchor-path",
        "MIK-R22 rule 6",
        "added anchors name existing paths",
        check_anchor_paths,
    ),
    ValidationRule(
        "R22.6-carried-stale",
        "MIK-R22 rule 6",
        "carried anchors at absent paths are reported stale",
        check_carried_stale,
        report_only=True,
    ),
)

for _rule in REFERENCE_RULES:
    register_rule(_rule)
