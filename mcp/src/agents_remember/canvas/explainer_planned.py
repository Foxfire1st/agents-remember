"""The planned-mode review page: a planning leaf's intent review, with no changes yet (MIK-R189 r5).

Before a leaf starts there is no comparison to explain and no page to write: the leaf is ``planning``
and its binding is the typed planned comparison (owner ``planned``, number ``0``, base equal to
candidate at the master tips). Its review page therefore shows *what the task is meant to produce*,
read from the task's own documents, and nothing that only a build could know:

* **planned effects**: every ``expectedKnowledgeEffects`` declaration is one tree row reading
  ``planned · <effect>``; an existing ``invariant:``/``family:`` subject shows its current statement
  from the memory tip, and a ``new:`` subject shows the hand-off label it was declared with;
* **scope**: each packet's ``## Scope`` backticked repository paths, resolved against the code tip
  or listed as text when nothing answers to them;
* **the packets** the document's ``requirements`` names, rendered by their title and scope;
* **Prepared context**: the task document's section of that heading, or ``absent``;
* **reference files**: the names in ``notes/reference/<leaf>/``;
* **the top line** ``planned · no changes``, and only deterministic sorts.

This module is pure: it reads no file, resolves no path, starts no role and no model turn. Its
caller supplies the task document, the planned comparison, the statement each existing subject holds
at the memory tip, each packet's text, the reference folder's names and the code tip's path set, and
:func:`planned_page` answers the page's payload. That is what keeps MIK-R189 rule 10's counts true --
one task document, one read per effect, one read per packet, one resolution per scope path -- and
keeps zero model turns a property of the module rather than a promise about it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from agents_remember.application.review_evidence_packet import (
    INTENDED_AUTHORITY,
    planned_reference,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord

__all__ = [
    "INTENDED_NOTICE",
    "PARTS",
    "PLANNED_SORTS",
    "PREPARED_CONTEXT_HEADING",
    "TOP_LINE",
    "planned_page",
    "scope_paths",
]

#: The one top line a planned page carries: there is no change set, so nothing may suggest one.
TOP_LINE = "planned · no changes"
#: The task document's section heading the intent review shows verbatim (MIK-R189 r5).
PREPARED_CONTEXT_HEADING = "Prepared context"
#: Every diagram or claim of planned behaviour carries this notice (MIK-R189 rule 6).
INTENDED_NOTICE = "Intended, not a fact about the code"

#: The deterministic sorts the page offers, and only those. Each is a function of the declarations
#: themselves -- declaration order, the declared subject, the effect label -- so two readers of one
#: task document never see a different order. A judgment or grade sort is deliberately absent: a
#: planning leaf has no change set to grade (MIK-R187; MIK-R189 rule 5).
PLANNED_SORTS: tuple[dict[str, str], ...] = (
    {
        "id": "declared",
        "label": "as declared",
        "order": "the task document's own declaration order",
    },
    {"id": "subject", "label": "by subject", "order": "the declared subject, alphabetically"},
    {"id": "effect", "label": "by effect", "order": "the effect label, alphabetically"},
)

#: The page's parts, in reading order. Ids satisfy the part bound the product's map checks
#: (``canvas.parts.part_id_problem``): lowercase, digits and hyphens, starting with a letter.
PARTS: tuple[dict[str, str], ...] = (
    {"id": "effects", "title": "Planned effects"},
    {"id": "packets", "title": "Packets"},
    {"id": "prepared-context", "title": PREPARED_CONTEXT_HEADING},
    {"id": "reference-files", "title": "Reference files"},
)

_SCOPE_HEADING = "## Scope"
_BACKTICK = re.compile(r"`(?P<path>[^`\s]+)`")
_LINK = re.compile(r"\[\s*(?P<text>[^\]]*?)\s*\]\((?P<path>requirements/[^)\s]+)\)")
_PACKET_REF = re.compile(r"^(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)@v(?P<version>[1-9][0-9]*)$")
_PACKET_FILE = re.compile(
    r"^(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)-v(?P<version>[1-9][0-9]*)(?:-|\.md$)"
)


def _field(entry: object, name: str) -> Any:
    """One field of a task-document model or of the same document read as plain JSON."""

    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _declared_subject(subject: str) -> tuple[str, str]:
    """One declared subject as ``(kind, name)``: ``invariant:ID``, ``family:ID`` or ``new:label``."""

    kind, separator, name = subject.partition(":")
    if not separator:
        return "", subject
    return kind.strip(), name.strip()


def _statement_for(declared: str, name: str, statements: Mapping[str, str]) -> str | None:
    """The current statement of an existing subject, or ``None`` when the read did not reach one."""

    for key in (declared, name):
        statement = statements.get(key)
        if isinstance(statement, str) and statement.strip():
            return statement.strip()
    return None


def _effect_rows(
    document: Mapping[str, Any], statements: Mapping[str, str]
) -> list[dict[str, Any]]:
    """Every declared effect as one ``planned · <effect>`` row, in declaration order (rule 5)."""

    declarations = document.get("expectedKnowledgeEffects")
    rows: list[dict[str, Any]] = []
    for entry in declarations if isinstance(declarations, (list, tuple)) else ():
        subject = _text(_field(entry, "subject"))
        effect = _text(_field(entry, "effect"))
        if not subject or not effect:
            continue
        kind, name = _declared_subject(subject)
        statement = None if kind == "new" else _statement_for(subject, name, statements)
        rows.append(
            {
                "subject": subject,
                "kind": "new" if kind == "new" else "existing",
                "effect": effect,
                "title": f"planned · {effect}",
                "statement": statement,
                "label": name if kind == "new" else None,
                "statementState": (
                    "new" if kind == "new" else ("statement" if statement else "unread")
                ),
                "requirement": _text(_field(entry, "requirementRef")),
                "part": "effects",
            }
        )
    return rows


def _packet_ref(text: str, path: str) -> str:
    """A packet's ``<stable ID>@v<n>`` identity, from its link text or from its file name."""

    match = _PACKET_REF.match(text.strip())
    if match:
        return f"{match.group('id')}@v{match.group('version')}"
    name = path.rsplit("/", 1)[-1]
    file_match = _PACKET_FILE.match(name)
    if file_match:
        return f"{file_match.group('id')}@v{file_match.group('version')}"
    return name


def scope_paths(text: str) -> tuple[str, ...]:
    """The backticked repository paths of one packet's ``## Scope`` section, in order.

    Scope is prose (MIK-R189 rule 5 [I]), so this reads it as prose: the section's lines, each
    backticked span that looks like a repository path. A path repeated in the section is named once.
    """

    found: list[str] = []
    in_scope = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_scope = stripped == _SCOPE_HEADING
            continue
        if not in_scope:
            continue
        for match in _BACKTICK.finditer(stripped):
            path = match.group("path").strip().strip(".,;:")
            if not path:
                continue
            looks_like_a_path = "/" in path or path.endswith(
                (".py", ".ts", ".tsx", ".md", ".json", ".jsonl", ".toml")
            )
            if looks_like_a_path and path not in found:
                found.append(path)
    return tuple(found)


def _resolve(path: str, code_tip_paths: frozenset[str]) -> str | None:
    """The code tip's own path for one Scope path, or ``None`` when nothing answers uniquely.

    A packet writes its Scope paths as prose, and many are relative to the package a reader already
    knows (``worktrees/modules/start.py`` for ``mcp/src/agents_remember/worktrees/modules/start.py``).
    Three readings are tried, in this order, and each answers only when it is unambiguous:

    1. the path as written -- a file or a directory at the tip;
    2. the unique file whose repository path ends with ``/<path>``;
    3. the unique directory owning a path that contains ``/<path>/``.

    A path that two tip files or two directories answer is listed as text: choosing one of them
    would be the page's guess, not the packet's reference.
    """

    cleaned = path.rstrip("/")
    if cleaned in code_tip_paths:
        return cleaned
    if any(candidate.startswith(f"{cleaned}/") for candidate in code_tip_paths):
        return cleaned
    files = sorted(candidate for candidate in code_tip_paths if candidate.endswith(f"/{cleaned}"))
    if len(files) == 1:
        return files[0]
    owners = sorted(
        {
            candidate.split(f"/{cleaned}/", 1)[0] + f"/{cleaned}"
            for candidate in code_tip_paths
            if f"/{cleaned}/" in candidate
        }
    )
    return owners[0] if len(owners) == 1 else None


def _parse_packet_entry(entry: object) -> tuple[str, str]:
    if isinstance(entry, str):
        link = _LINK.search(entry)
        if link is None:
            return "", ""
        path = link.group("path")
        return path, _packet_ref(link.group("text"), path)
    if isinstance(entry, Mapping):
        path = _text(entry.get("path"))
        stable = _text(entry.get("stableId"))
        version = _text(entry.get("version"))
        if not path or not stable or not version:
            return "", ""
        return path, f"{stable}@{version if version.startswith('v') else f'v{version}'}"
    return "", ""


def _scope_rows(text: str, code_tip_paths: frozenset[str], code_tree: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in scope_paths(text):
        resolved = _resolve(candidate, code_tip_paths)
        rows.append(
            {
                "path": candidate,
                "state": "resolved" if resolved else "text",
                "exists": resolved is not None,
                "resolvedPath": resolved,
                "tipAddress": (
                    (f"{code_tree}:{resolved}" if code_tree else resolved) if resolved else None
                ),
            }
        )
    return rows


def _packets(
    document: Mapping[str, Any],
    packet_texts: Mapping[str, str],
    code_tip_paths: frozenset[str],
    *,
    code_tree: str = "",
) -> list[dict[str, Any]]:
    """The packets the document's ``requirements`` name, rendered with their scope (rule 5)."""

    entries = document.get("requirements")
    packets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries if isinstance(entries, (list, tuple)) else ():
        path, ref = _parse_packet_entry(entry)
        if not path or path in seen:
            continue
        seen.add(path)
        text = packet_texts.get(path)
        title = (
            ""
            if text is None
            else next(
                (line.strip()[2:].strip() for line in text.splitlines() if line.startswith("# ")),
                path.rsplit("/", 1)[-1],
            )
        )
        packets.append(
            {
                "ref": ref,
                "path": path,
                "title": title,
                "body": "" if text is None else text.strip(),
                "state": "unread" if text is None else "rendered",
                "part": "packets",
                "scope": [] if text is None else _scope_rows(text, code_tip_paths, code_tree),
            }
        )
    return packets


def _prepared_context(document: Mapping[str, Any]) -> dict[str, Any]:
    """The task document's Prepared-context section, or ``absent`` (rule 5)."""

    sections = document.get("sections")
    for section in sections if isinstance(sections, (list, tuple)) else ():
        heading = _text(_field(section, "heading"))
        if heading.casefold() != PREPARED_CONTEXT_HEADING.casefold():
            continue
        return {
            "heading": heading or PREPARED_CONTEXT_HEADING,
            "state": "present",
            "body": _text(_field(section, "body")),
            "part": "prepared-context",
        }
    return {
        "heading": PREPARED_CONTEXT_HEADING,
        "state": "absent",
        "body": "",
        "part": "prepared-context",
    }


def _reference_files(leaf_id: str, names: Iterable[str]) -> list[dict[str, str]]:
    """The names in ``notes/reference/<leaf>/``, deduplicated and sorted (rule 5)."""

    folder = f"notes/reference/{leaf_id}"
    files: list[dict[str, str]] = []
    for name in sorted({_text(entry).strip("/") for entry in names if _text(entry)}):
        path = name if name.startswith("notes/") else f"{folder}/{name}"
        if not path.startswith(f"{folder}/"):
            continue
        if any(segment in {"", ".", ".."} for segment in path.split("/")):
            continue
        files.append(
            {
                "path": path,
                "name": path.rsplit("/", 1)[-1],
                "part": "reference-files",
            }
        )
    return files


def planned_page(  # noqa: PLR0913 - one page states every source the planned read has
    *,
    document: Mapping[str, Any],
    comparison: ReviewTreeComparisonRecord,
    statements: Mapping[str, str] | None = None,
    packet_texts: Mapping[str, str] | None = None,
    reference_files: Iterable[str] = (),
    code_tip_paths: Iterable[str] = (),
) -> dict[str, Any]:
    """Answer the planned-mode page's payload from the sources the caller read (MIK-R189 rule 5).

    ``document`` is the leaf's task document (its own JSON, or the model), ``comparison`` the
    planned comparison the page is bound to, ``statements`` the memory tip's statement per existing
    subject key, ``packet_texts`` each requirement file's text by its task-relative path,
    ``reference_files`` the names in ``notes/reference/<leaf>/`` and ``code_tip_paths`` the paths at
    the code tip. Nothing here opens a file, launches anything or judges anything: two calls with
    equal inputs answer equal payloads.
    """

    leaf_id = _text(document.get("id")) or comparison.leaf_id
    rows = _effect_rows(document, statements or {})
    packets = _packets(
        document,
        packet_texts or {},
        frozenset(code_tip_paths),
        code_tree=comparison.code_candidate.tree,
    )
    context = _prepared_context(document)
    references = _reference_files(leaf_id, reference_files)
    scope_total = sum(len(packet["scope"]) for packet in packets)
    return {
        "mode": "planned",
        "topLine": TOP_LINE,
        "intendedNotice": INTENDED_NOTICE,
        "authority": INTENDED_AUTHORITY,
        "binding": {
            "owner": "planned",
            "number": comparison.number,
            "reference": planned_reference(comparison.task_id, comparison.leaf_id),
            "label": "planned",
        },
        "tips": {
            "code": comparison.code_candidate.commit or comparison.code_candidate.tree,
            "codeTree": comparison.code_candidate.tree,
            "memory": comparison.memory_candidate.commit or comparison.memory_candidate.tree,
            "memoryTree": comparison.memory_candidate.tree,
            "recordedAt": comparison.recorded_at,
        },
        "rows": rows,
        "packets": packets,
        "preparedContext": context,
        "referenceFiles": references,
        "sorts": [dict(entry) for entry in PLANNED_SORTS],
        "parts": [dict(entry) for entry in PARTS],
        "actions": [],
        "modelTurns": 0,
        "agentStarts": 0,
        "reads": {
            "taskDocuments": 1,
            "knowledgeStatements": sum(1 for row in rows if row["kind"] == "existing"),
            "packets": len(packets),
            "scopePaths": scope_total,
        },
    }
