"""Record integrity: the comparisons that catch a record that stopped being true.

`260918-TSIP-L2`'s subject is the drift shape every finding of its campaign shared: a document
that was **true when written and silently stopped being true**, with nothing able to notice. A
master report described a superseded tip in one section while another section was right; a master
row read `Completed` before any closeout had run; a register cell named a leaf its own master does
not have; a route overview stated a source's pre-repair line count while every check stayed green.

Each of those is a disagreement between **two sides of the same fact**, and each side is
independently readable. That is the whole design here: nothing below interprets prose. Every
function reads a declared value from one artifact and the authoritative value from another, and
reports the rows where they differ, naming both sides and the number of rows compared.

The four comparisons, and the drift each was written for:

:func:`check_leaf_document_against_contract`
    A leaf document's ``status`` against its own enclosure contract's ``closeout``/``integration``
    cells. The historical case is 11 direct-child leaves reading ``planning``/``inProgress`` while
    their contract said the work had landed, and 24 reading ``Completed`` while closeout had never
    started (22 of them in one master).

:func:`check_master_rows_against_leaf_documents`
    A master document's ``subTasks[].status`` against the status its leaf document actually
    carries. The historical case is defect `D42`: a row flipped to ``Completed`` when the last
    step was marked, before any closeout, integration or finalize had run.

:func:`check_register_row_ownership`
    A register row's ``Owner`` cell and its State-cell ``→ L<n>`` arrows against the leaf ids the
    owning master actually declares. The historical case is five cells still pointing at an `L20`
    from the *previous* master's leaf set.

:func:`check_declared_figure_currency`
    A figure **written in prose** against the source it describes. This is the comparison the
    memory layer's own checks cannot make: ``range_resolution`` looks only inside cited ranges and
    ``claim_reopen`` only at cited claims, so a line count or case count stated as prose is
    invisible to both — the finding recorded as `T45`.

Every function returns the comparison it performed alongside its findings, because "0 disagreements"
without the two populations stated is a zero nobody can license. The populations are the licence.

These are helpers, not a gate: run them from the task root with
``python -m agents_remember_test_support.code_quality.record_integrity``, or call them from a test.
Nothing here writes to disk.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------------

REPORT_PREFIX = "record integrity:"

CONTRACT_GLOB = "**/enclosures/*/series-contract.md"

# A test function as pytest collects one. Named once so a figure check and its own cases cannot
# disagree about what a "case" is.
TEST_DEFINITION = re.compile(r"^\s*def test_", re.M)
FRONTMATTER = re.compile(r"^---\n(.*?)\n---", re.S)
SCALAR = re.compile(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(\S+)\s*$", re.M)
BLOCK_HEAD = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*$(.*?)(?=^\S|\Z)", re.M | re.S)

# A leaf document's statuses as the schema declares them; the vocabulary is closed on purpose so
# an unrecognized status is reported rather than silently compared.
ACTIVE_STATUSES = frozenset({"planning", "inProgress"})
LANDED_CONTRACT_STATUSES = frozenset({"completed", "complete", "done", "landed"})
STARTED_CONTRACT_STATUSES = frozenset(
    {"pending", "in-progress", "inProgress", "started", "blocked"}
)

# The comparison every finding names, so a reader can tell what produced it.
RULE_LEAF_CONTRACT = "leaf-document-status-vs-contract-cells"
# The same disagreement, found only once the contract's `leaf_id` and the document's `id` are
# compared case-insensitively. Reported separately because the stricter spelling finds fewer, and a
# reader comparing this run against an earlier one must be able to see which calibration produced
# the difference rather than reading it as drift.
RULE_LEAF_CONTRACT_KEYED = "leaf-document-status-vs-contract-cells-keyed"
RULE_MASTER_ROW = "master-row-status-vs-leaf-document"
RULE_MASTER_EARLY = "row-completed-before-landing"
# The mirror case: the row still reads untouched while its document has moved. Measured on four
# masters that predate `master_sync`, and shipped because it is the same two-sided comparison.
RULE_MASTER_LATE = "row-unmarked-after-work-began"
RULE_OWNER = "register-owner-arrow-vs-master-leaf-set"
RULE_FIGURE = "declared-prose-figure-vs-source-value"


class RecordIntegrityError(ValueError):
    """A comparison that could not be made, named rather than returned as a silent zero."""


@dataclass(frozen=True)
class Finding:
    """One disagreement, with both sides named and the line a reader can open."""

    rule: str
    subject: str
    authority: str
    declared: str
    measured: str
    message: str
    path: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            "subject": self.subject,
            "authority": self.authority,
            "declared": self.declared,
            "measured": self.measured,
            "message": self.message,
            "path": self.path,
            "line": self.line,
        }


@dataclass(frozen=True)
class Comparison:
    """What one check compared, so its finding count is readable rather than merely small."""

    check: str
    subjects: int
    authority: int
    rule: str
    detail: str
    findings: tuple[Finding, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def disagreements(self) -> int:
        return len(self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "check": self.check,
            "rule": self.rule,
            "subjects": self.subjects,
            "authority": self.authority,
            "detail": self.detail,
            "disagreements": len(self.findings),
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def counts_by_rule(self) -> dict[str, int]:
        """Findings per rule, in first-seen order, so one check may report several classes."""
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.rule] = counts.get(finding.rule, 0) + 1
        return counts

    def render(self) -> str:
        lines = [
            f"{REPORT_PREFIX} {self.check}",
            f"  compared: {self.detail}",
            f"  populations: subjects={self.subjects} authority={self.authority}",
            f"  disagreements: {len(self.findings)}",
        ]
        for rule, count in self.counts_by_rule().items():
            lines.append(f"    {count} x {rule}")
        for finding in self.findings:
            where = f"{finding.path}:{finding.line}" if finding.line else (finding.path or "-")
            lines.append(f"    [{finding.rule}] {where}")
            lines.append(
                f"      declared={finding.declared!r} authority={finding.measured!r} "
                f"({finding.authority})"
            )
            lines.append(f"      {finding.message}")
        return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# The two sides, parsed independently
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractCells:
    """One enclosure contract, reduced to the cells and the identity a comparison needs."""

    path: Path
    task_root: Path
    leaf_id: str | None
    closeout: str | None
    closeout_cleanup: str | None
    integration: str | None
    integration_cleanup: str | None
    kind: str | None

    @property
    def landed(self) -> bool:
        """Whether this contract says the leaf's work reached a landing route."""
        return (
            self.closeout in LANDED_CONTRACT_STATUSES
            or self.integration in LANDED_CONTRACT_STATUSES
        )

    @property
    def started(self) -> bool:
        return (
            self.closeout in STARTED_CONTRACT_STATUSES
            or self.integration in STARTED_CONTRACT_STATUSES
        )


@dataclass(frozen=True)
class LeafDocument:
    """One task document, reduced to the fields the comparisons read."""

    path: Path
    task_root: Path
    doc_id: str
    slug: str | None
    kind: str | None
    status: str | None
    steps: tuple[tuple[str, str, str | None], ...] = field(default=())

    def step_statuses(self) -> list[str]:
        """Every declared step and substep status, in document order."""
        return [status for _id, status, _parent in self.steps]


@dataclass(frozen=True)
class MasterRow:
    """One ``subTasks[]`` row of a master document."""

    master_path: Path
    number: str
    name: str | None
    file_cell: str | None
    status: str | None
    line: int | None


def _scalar_fields(frontmatter: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in SCALAR.finditer(frontmatter)}


def _block_fields(frontmatter: str) -> dict[str, dict[str, str]]:
    """Read the nested mapping blocks (``closeout:`` / ``integration:``) as cell -> value."""
    blocks: dict[str, dict[str, str]] = {}
    for match in BLOCK_HEAD.finditer(frontmatter):
        name, body = match.group(1), match.group(2)
        cells = {
            cell.group(1): cell.group(2)
            for cell in re.finditer(r"^\s+([A-Za-z_][\w-]*)\s*:\s*(\S+)\s*$", body, re.M)
        }
        if cells:
            blocks[name] = cells
    return blocks


def read_contract(path: Path, *, task_root: Path | None = None) -> ContractCells:
    """Read one enclosure contract, or refuse loudly if it carries no frontmatter."""
    text = path.read_text(encoding="utf-8")
    front = FRONTMATTER.match(text)
    if front is None:
        raise RecordIntegrityError(
            f"{path} carries no frontmatter block, so its cells are unreadable"
        )
    scalars = _scalar_fields(front.group(1))
    blocks = _block_fields(front.group(1))
    closeout = blocks.get("closeout", {})
    integration = blocks.get("integration", {})
    root = task_root if task_root is not None else path.parent.parent.parent
    return ContractCells(
        path=path,
        task_root=root,
        leaf_id=scalars.get("leaf_id"),
        closeout=closeout.get("status"),
        closeout_cleanup=closeout.get("cleanup"),
        integration=integration.get("status"),
        integration_cleanup=integration.get("cleanup"),
        kind=scalars.get("kind"),
    )


def read_leaf_document(path: Path, *, task_root: Path | None = None) -> LeafDocument:
    """Read one task document's identity and status, or refuse if it is not an AR document."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecordIntegrityError(f"{path} is not a readable task document: {error}") from error
    if not isinstance(payload, dict) or "schema" not in payload:
        raise RecordIntegrityError(f"{path} is not an ar-task-document (no schema key)")
    steps: list[tuple[str, str, str | None]] = []
    for step in payload.get("steps") or ():
        if not isinstance(step, dict):
            continue
        steps.append((str(step.get("id", "")), str(step.get("status", "")), None))
        for substep in step.get("substeps") or ():
            if isinstance(substep, dict):
                steps.append(
                    (
                        str(substep.get("id", "")),
                        str(substep.get("status", "")),
                        str(step.get("id", "")),
                    )
                )
    root = task_root if task_root is not None else path.parent
    return LeafDocument(
        path=path,
        task_root=root,
        doc_id=str(payload.get("id", "")),
        slug=payload.get("slug"),
        kind=payload.get("kind"),
        status=payload.get("status"),
        steps=tuple(steps),
    )


def master_rows(master_path: Path) -> tuple[MasterRow, ...]:
    """Read every ``subTasks[]`` row of a master document, with its line in the rendered file."""
    payload = json.loads(master_path.read_text(encoding="utf-8"))
    rendered = master_path.with_suffix(".md")
    lines = rendered.read_text(encoding="utf-8").splitlines() if rendered.exists() else []
    rows: list[MasterRow] = []
    for entry in payload.get("subTasks") or ():
        if not isinstance(entry, dict):
            continue
        number = str(entry.get("number", ""))
        name = entry.get("name")
        line = None
        for position, text in enumerate(lines, 1):
            if (
                number
                and re.search(rf"\b{re.escape(number)}\b", text)
                and text.lstrip().startswith("|")
            ):
                line = position
                break
        rows.append(
            MasterRow(
                master_path=master_path,
                number=number,
                name=name if isinstance(name, str) else None,
                file_cell=entry.get("file") if isinstance(entry.get("file"), str) else None,
                status=entry.get("status") if isinstance(entry.get("status"), str) else None,
                line=line,
            )
        )
    return tuple(rows)


# --------------------------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------------------------


def task_roots(coordination_root: Path) -> tuple[Path, ...]:
    """Every direct child directory of ``tasks/<repo>/`` that holds a task document or contract."""
    tasks = coordination_root / "tasks"
    if not tasks.is_dir():
        raise RecordIntegrityError(f"{tasks} is not a directory, so no task root can be enumerated")
    roots: list[Path] = []
    for repo_dir in sorted(child for child in tasks.iterdir() if child.is_dir()):
        for candidate in sorted(child for child in repo_dir.iterdir() if child.is_dir()):
            if (candidate / "task.json").exists() or (candidate / "enclosures").is_dir():
                roots.append(candidate)
    return tuple(roots)


def contracts_under(task_root: Path) -> tuple[Path, ...]:
    return tuple(sorted(task_root.glob(CONTRACT_GLOB)))


def leaf_documents(task_root: Path) -> tuple[Path, ...]:
    """The task documents a task root can carry, at any depth.

    Older masters store a leaf document beside the master's; this campaign stores one file per
    leaf at the root. Both are read, and the id — not the filename — is the matching key.
    """
    documents: list[Path] = []
    for path in sorted(task_root.rglob("*.json")):
        if "enclosures" in path.parts or "worktrees" in path.parts:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("schema") == "ar-task-document/v1":
            documents.append(path)
    return tuple(documents)


def _index_by_id(documents: Iterable[Path]) -> dict[str, LeafDocument]:
    index: dict[str, LeafDocument] = {}
    for path in documents:
        document = read_leaf_document(path, task_root=path.parent)
        index.setdefault(document.doc_id, document)
    return index


LEAF_SUFFIX = re.compile(r"(?:^|[-_])(L\d+)$", re.I)
BARE_LEAF = re.compile(r"^L\d+$", re.I)


def leaf_key(identifier: str | None) -> str | None:
    """Reduce a leaf identifier to the ``L<n>`` a register, a master row and a contract share.

    A master declares ``260918-TSIP-L2`` where a register's State cell writes ``→ L2``. They name
    the same leaf, so the comparison is made on the suffix; comparing the raw strings would report
    every arrow as a disagreement, which is spelling rather than drift.
    """
    if not identifier:
        return None
    text = identifier.strip()
    if BARE_LEAF.fullmatch(text):
        return text.upper()
    match = LEAF_SUFFIX.search(text)
    return match.group(1).upper() if match is not None else None


def _index_by_leaf_key(documents: Iterable[Path]) -> dict[str, LeafDocument]:
    index: dict[str, LeafDocument] = {}
    for path in documents:
        document = read_leaf_document(path, task_root=path.parent)
        key = leaf_key(document.doc_id)
        if key is not None:
            index.setdefault(key, document)
    return index


# --------------------------------------------------------------------------------------------
# Check 1 — leaf document status against its own contract's cells (requirement 4)
# --------------------------------------------------------------------------------------------


def check_leaf_document_against_contract(
    coordination_root: Path, *, task_roots_scope: Sequence[Path] | None = None
) -> Comparison:
    """Compare every enclosure contract's landing cells against the leaf document it belongs to.

    The contract is the **authority side**: its cells are written by the closeout and integration
    routes and are what a reader consults to learn whether work landed. The leaf document's
    ``status`` is the subject. Two disagreements are reported, and they are not symmetric:

    ``unmarked-plan``
        the document says the work is untouched or in progress while the contract says it landed.
        This is requirement 4's case: the work finished and the record did not follow.

    ``unlanded-completion``
        the document says ``Completed`` while the contract's closeout has not started. The work may
        well be done; what the record cannot say is that it landed. 22 of the 24 historical
        instances sat in one master.

    A contract whose ``leaf_id`` matches no document is **counted and reported separately** rather
    than failed: on older masters the leaf document is gone by design, and judging that needs a
    naming convention rather than a check. The count is returned in ``detail`` so the zero is
    licensed by the population it was drawn from.
    """
    roots = (
        tuple(task_roots_scope) if task_roots_scope is not None else task_roots(coordination_root)
    )
    findings: list[Finding] = []
    contracts = 0
    matched = 0
    matched_by_key = 0
    unmatched: list[str] = []
    for root in roots:
        documents = _index_by_id(leaf_documents(root))
        by_key = _index_by_leaf_key(leaf_documents(root))
        for contract_path in contracts_under(root):
            contract = read_contract(contract_path, task_root=root)
            contracts += 1
            if contract.leaf_id is None:
                unmatched.append(f"{contract_path} (no leaf_id cell)")
                continue
            discovered_by = RULE_LEAF_CONTRACT
            document = documents.get(contract.leaf_id)
            if document is None:
                key = leaf_key(contract.leaf_id)
                document = by_key.get(key) if key is not None else None
                if document is not None:
                    matched_by_key += 1
                    discovered_by = RULE_LEAF_CONTRACT_KEYED
            if document is None:
                unmatched.append(f"{contract.leaf_id} ({root.name})")
                continue
            matched += 1
            status = document.status or "(absent)"
            if status in ACTIVE_STATUSES and contract.landed:
                findings.append(
                    Finding(
                        rule=discovered_by,
                        subject=str(document.path),
                        authority=str(contract.path),
                        declared=f"document status={status}",
                        measured=(
                            f"closeout={contract.closeout} integration={contract.integration}"
                        ),
                        message=(
                            f"leaf {contract.leaf_id} reads {status} while its contract records a "
                            f"landing; the work is done and the document did not follow"
                        ),
                        path=str(document.path),
                    )
                )
            elif status == "Completed" and contract.closeout == "not-started":
                findings.append(
                    Finding(
                        rule=discovered_by,
                        subject=str(document.path),
                        authority=str(contract.path),
                        declared="document status=Completed",
                        measured=f"closeout={contract.closeout} integration={contract.integration}",
                        message=(
                            f"leaf {contract.leaf_id} reads Completed while its contract's closeout "
                            f"has not started, so the record cannot say the work landed"
                        ),
                        path=str(document.path),
                    )
                )
    exact = sum(1 for finding in findings if finding.rule == RULE_LEAF_CONTRACT)
    keyed = sum(1 for finding in findings if finding.rule == RULE_LEAF_CONTRACT_KEYED)
    detail = (
        f"{matched} enclosure contracts matched a leaf document "
        f"({matched - matched_by_key} by exact id, {matched_by_key} by case-insensitive L<n> key); "
        f"{len(unmatched)} contracts carry no matching document and are counted, not failed. "
        f"{exact} disagreements need no interpretation to find; {keyed} more are visible only "
        f"under the case-insensitive key, so a strict spelling match under-reports by {keyed}"
    )
    return Comparison(
        check="leaf-document-vs-contract",
        subjects=matched,
        authority=contracts,
        rule=RULE_LEAF_CONTRACT,
        detail=detail,
        findings=tuple(findings),
    )


# --------------------------------------------------------------------------------------------
# Check 2 — master row status against the leaf document it names (D42)
# --------------------------------------------------------------------------------------------


def derived_master_status(document: LeafDocument) -> str:
    """The row status a leaf document justifies, read from the leaf document alone.

    This is the shipped product's rule (``tasks/master_sync.py:derived_master_status``) restated
    for a reader: ``Completed`` requires **the document** to be ``Completed``, never merely every
    step marked. The historical defect `D42` is exactly that substitution.
    """
    if document.status == "abandoned":
        return "abandoned"
    statuses = document.step_statuses()
    if document.status == "Completed" and statuses and all(s == "done" for s in statuses):
        return "Completed"
    if any(status in {"done", "inProgress", "blocked"} for status in statuses):
        return "inProgress"
    if statuses and document.status == "Completed":
        return "inProgress"
    return document.status or "planning"


def check_master_rows_against_leaf_documents(
    coordination_root: Path, *, task_roots_scope: Sequence[Path] | None = None
) -> Comparison:
    """Compare every master ``subTasks[]`` row against the status its leaf document carries.

    The leaf document is the authority side, and the comparison is exact: the row must carry the
    status the shipped rule derives from the document beside it. Rows whose leaf document no longer
    exists are counted rather than failed — a finalized master's leaves are cleaned up by design.

    Two rules are reported, and only the first is the live defect class:

    ``master-row-vs-leaf-document``
        the row and the derived status differ at all. On old masters this is often a step-state
        vocabulary that predates the rule, so the rule is named on every finding.

    ``row-completed-before-landing``
        the row says ``Completed`` while its document is still ``planning`` or ``inProgress``. This
        is `D42` itself, and the historical case is a master row that read ``Completed`` while the
        leaf document read ``planning``, no closeout had run and the candidate was uncommitted.
    """
    roots = (
        tuple(task_roots_scope) if task_roots_scope is not None else task_roots(coordination_root)
    )
    findings: list[Finding] = []
    rows_compared = 0
    unmatched = 0
    for root in roots:
        master_json = root / "task.json"
        if not master_json.exists():
            continue
        documents = _index_by_leaf_key(leaf_documents(root))
        for row in master_rows(master_json):
            key = leaf_key(row.number)
            document = documents.get(key) if key is not None else None
            if document is None:
                unmatched += 1
                continue
            rows_compared += 1
            expected = derived_master_status(document)
            if row.status == expected:
                continue
            early = row.status == "Completed" and document.status in ACTIVE_STATUSES
            late = row.status in ACTIVE_STATUSES and expected == "inProgress"
            if early:
                rule = RULE_MASTER_EARLY
                message = (
                    f"the master's row for {row.number} says Completed while its leaf document "
                    f"still reads {document.status}: the row claims a landing the document does "
                    f"not support"
                )
            elif late:
                rule = RULE_MASTER_LATE
                message = (
                    f"the master's row for {row.number} still reads {row.status} while its leaf "
                    f"document has work marked; the row was not advanced with its document"
                )
            else:
                rule = RULE_MASTER_ROW
                message = (
                    f"the master's row for {row.number} says {row.status} while its leaf document "
                    f"justifies {expected}; a row is Completed when the work landed, not when its "
                    f"steps were marked"
                )
            findings.append(
                Finding(
                    rule=rule,
                    subject=f"{row.master_path}#subTasks[{row.number}]",
                    authority=str(document.path),
                    declared=f"row status={row.status}",
                    measured=f"derived={expected} (document status={document.status})",
                    message=message,
                    path=str(row.master_path),
                    line=row.line,
                )
            )

    def count(rule: str) -> int:
        return sum(1 for finding in findings if finding.rule == rule)

    detail = (
        f"{rows_compared} master rows matched a leaf document by L<n> before the status comparison; "
        f"{unmatched} rows name no document in their own task root. "
        f"Of {len(findings)} disagreements, {count(RULE_MASTER_EARLY)} are a row reading Completed "
        f"over an unlanded document and {count(RULE_MASTER_LATE)} are a row left behind by a "
        f"document that moved"
    )
    return Comparison(
        check="master-row-vs-leaf-document",
        subjects=rows_compared,
        authority=rows_compared,
        rule=RULE_MASTER_ROW,
        detail=detail,
        findings=tuple(findings),
    )


# --------------------------------------------------------------------------------------------
# Check 3 — register owner arrows against the owning master's leaf set
# --------------------------------------------------------------------------------------------

REGISTER_ROW = re.compile(r"^\|\s*([DT]\d+)\s*\|(.*)\|\s*$")
# Column positions inside a register row's cell list, where cell 0 is the row id. The Owner cell
# carries leaf ids only (`L6 / L7`, `every leaf`), never an arrow; the State cell carries the
# `→ L<n>` transitions. Checking arrows in the Owner cell as well would flag the register's own
# `… → L7` prose, which is the row's *owner name*, not a leaf-set membership claim.
STATE_COLUMN = 4
ARROW = re.compile(r"→\s*\**\s*(L\d+)")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def declared_leaf_ids(master_json: Path) -> set[str] | None:
    """The master's declared leaf set, reduced to the ``L<n>`` keys a register cell writes."""
    if not master_json.exists():
        return None
    payload = json.loads(master_json.read_text(encoding="utf-8"))
    ids = {
        key
        for entry in payload.get("subTasks") or ()
        if isinstance(entry, dict) and entry.get("number")
        for key in (leaf_key(str(entry.get("number"))),)
        if key is not None
    }
    return ids or None


def check_register_row_ownership(register_path: Path) -> Comparison:
    """Compare every ``→ L<n>`` arrow in a register's State cells against its master's leaf set.

    The master document's ``subTasks[].number`` set is the authority side. A cell that points at an
    owner the master does not have sends a reader to a leaf that cannot hold the row — the
    historical case being five cells left pointing at the previous master's `L20`.
    """
    master_json = register_path.parent.parent / "task.json"
    ownership = declared_leaf_ids(master_json)
    if ownership is None:
        raise RecordIntegrityError(
            f"{master_json} declares no subTasks, so there is no leaf set to compare arrows against"
        )
    arrows = ARROW
    findings: list[Finding] = []
    rows = 0
    arrows_seen = 0
    for line_number, text in enumerate(register_path.read_text(encoding="utf-8").splitlines(), 1):
        row = REGISTER_ROW.match(text)
        if row is None:
            continue
        rows += 1
        cells = _cells(text)
        state_cell = cells[STATE_COLUMN] if len(cells) > STATE_COLUMN else ""
        for match in arrows.finditer(state_cell):
            arrows_seen += 1
            target = match.group(1)
            if target not in ownership:
                findings.append(
                    Finding(
                        rule=RULE_OWNER,
                        subject=f"{register_path}:{line_number} ({row.group(1)} State)",
                        authority=str(master_json),
                        declared=target,
                        measured=",".join(sorted(ownership)),
                        message=(
                            f"the State cell points at {target}, which this master does not "
                            f"declare as a leaf; a reader is sent to a leaf that cannot hold it"
                        ),
                        path=str(register_path),
                        line=line_number,
                    )
                )
    detail = (
        f"{arrows_seen} owner arrows in {rows} register rows' Owner/State cells compared "
        f"against {len(ownership)} declared leaf ids"
    )
    return Comparison(
        check="register-owner-arrow-vs-leaf-set",
        subjects=arrows_seen,
        authority=len(ownership),
        rule=RULE_OWNER,
        detail=detail,
        findings=tuple(findings),
    )


# --------------------------------------------------------------------------------------------
# Check 4 — a figure written in prose against the source it describes (T45)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FigureClaim:
    """One figure a document may state in prose, and the authority that recomputes it.

    ``line_count`` / ``case_count`` are the two shapes shipped here, because they are the two the
    historical case measured (`instrument_discipline.py` 361 -> 432 lines and
    `test_instrument_discipline.py` 350 -> 537 lines / 16 -> 27 cases). A third shape is a
    deliberate omission: grading it would mean interpreting prose rather than comparing two sides.

    ``base_commit`` is the revision the prose describes, and it is what makes the comparison
    answerable. A card's body describes the source *at its own declared candidate*; a history entry
    describes the source as it stood when the entry was written. Reading both against the working
    tree would flag every history entry that correctly records a past count, so the claim carries
    the revision and the authority side is measured **there**. `None` means the working tree — the
    correct authority for a claim about the current candidate.
    """

    source: Path
    label_pattern: str
    shape: str
    stated_by: str
    documents: tuple[Path, ...] | None = None
    base_commit: str | None = None
    repo_root: Path | None = None

    def measured(self) -> int:
        """The source's value at the claim's own base, or in the working tree when it names none."""
        if self.base_commit is None:
            text = self.source.read_text(encoding="utf-8")
        else:
            repo = self.repo_root if self.repo_root is not None else _git_root(self.source)
            relative = self.source.resolve().relative_to(repo.resolve()).as_posix()
            probe = subprocess.run(
                ["git", "-C", str(repo), "show", f"{self.base_commit}:{relative}"],
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode != 0:
                raise RecordIntegrityError(
                    f"{self.base_commit}:{relative} does not resolve in {repo}, so the figure "
                    f"cannot be compared against the revision the prose describes"
                )
            text = probe.stdout
        if self.shape == "line_count":
            return len(text.splitlines())
        if self.shape == "case_count":
            return len(TEST_DEFINITION.findall(text))
        raise RecordIntegrityError(f"unknown figure shape {self.shape!r} for {self.source}")

    def unit(self) -> str:
        """The unit word this shape's figure is written in, as the pattern's trailing alternation."""
        return "lines?" if self.shape == "line_count" else "cases?"

    def pattern(self) -> re.Pattern[str]:
        r"""Find the number this source is credited with, in the clause that names it.

        The label is part of the pattern and the window is bounded on purpose. A bare
        ``\d+ lines`` pattern matches every line count on the page — the first run of this check
        reported twelve "disagreements" for one source, eleven of them other files' sizes — which is
        the fault this whole leaf is about. The unit is required too: a bare number beside a
        filename is as likely to be a citation range as a size.

        The unit is bound to its number rather than merely required to follow the window.
        ``350 lines / 16 cases`` states two figures, and a check that searched the window's numbers
        on its own would credit a case-count claim with the 350, reporting a disagreement the prose
        never made. The figure is captured by name, so extracting it does not depend on how many
        groups a caller's own label adds.

        The window ends at the last number its own unit follows, so a repair sentence reads
        ``instrument_discipline.py`` **361 → 432 lines** and the graded figure is the 432: the 361
        belongs to the state before the arrow, and a number bound to no unit of this shape is not a
        claim about this shape at all. The figure may not begin inside a longer number, so ``1,153``
        is one figure rather than a 153 the prose never stated.
        """
        unit = self.unit()
        return re.compile(
            rf"(?:{self.label_pattern})`?[^.;]{{0,200}}?(?<![\d.,])(?P<figure>\d[\d,]*)\s*"
            rf"(?:{unit})\b",
            re.I,
        )

    def stated_value(self, match: re.Match[str]) -> int:
        """The figure this match credits this source with, read from the unit it is bound to.

        The unit is part of the pattern precisely so this method cannot grade a number the prose
        stated in a different unit: ``350 lines / 16 cases`` is two claims, and reading the 350 as a
        case count reports a disagreement the prose never made.

        A match that carries no captured figure is a check fault rather than a clean zero, so it
        refuses by name instead of returning the window's first number.
        """
        digits = match.groupdict().get("figure")
        if digits is None:
            raise RecordIntegrityError(
                f"{match.group(0)!r} matched without a number bound to {self.unit()}, "
                f"so no figure can be graded"
            )
        return int(digits.replace(",", ""))


def _git_root(path: Path) -> Path:
    probe = subprocess.run(
        ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise RecordIntegrityError(f"{path} is not inside a Git work tree, so no base can be read")
    return Path(probe.stdout.strip())


def check_declared_figure_currency(
    documents: Sequence[Path], claims: Sequence[FigureClaim]
) -> Comparison:
    """Search ``documents`` for a figure stated in prose and compare it with its source's value.

    This is the comparison `T45` records as absent from the product: ``range_resolution`` looks only
    at anchors *inside cited ranges* and ``claim_reopen`` only at *cited claims*, so a number
    written as prose is invisible to both. The source at the claim's declared base is the authority
    side; the document is the subject.

    Frontmatter is skipped: a metadata row states the candidate the card was verified against, which
    is a revision, not a measurement of the source's size.
    """
    findings: list[Finding] = []
    scanned = 0
    occurrences = 0
    measured_cache: dict[int, int] = {}
    for document in documents:
        raw = document.read_text(encoding="utf-8")
        front = FRONTMATTER.match(raw)
        offset = front.end() if front is not None else 0
        text = raw[offset:]
        scanned += 1
        for claim in claims:
            if claim.documents is not None and document not in claim.documents:
                continue
            pattern = claim.pattern()
            # Scanned over the whole document, not line by line, because the sentence that carries
            # the stale figure and the sentence that names its source are often adjacent rather than
            # the same: "…this route told its readers the suite was 350 lines / 16 cases." names no
            # file at all, and the file is named in the paragraph above it. A per-line scan reports
            # that sentence as nothing, which is how a check comes to return a confident zero.
            for match in pattern.finditer(text):
                line_number = text.count("\n", 0, match.start()) + 1
                actual = measured_cache.setdefault(id(claim), claim.measured())
                base = claim.base_commit or "the working tree"
                stated = claim.stated_value(match)
                occurrences += 1
                if stated != actual:
                    findings.append(
                        Finding(
                            rule=RULE_FIGURE,
                            subject=f"{document}:{line_number}",
                            authority=f"{claim.source} @ {base}",
                            declared=f"{stated} of {match.group(0).strip()!r}",
                            measured=f"{actual} ({claim.shape})",
                            message=(
                                f"the document states {stated} for {claim.source.name} while the "
                                f"source {('at ' + base) if claim.base_commit else 'now'} carries "
                                f"{actual}; this figure is prose and no citation check reads it "
                                f"(T45, recorded by {claim.stated_by})"
                            ),
                            path=str(document),
                            line=line_number,
                        )
                    )
    detail = (
        f"{occurrences} prose figures across {scanned} documents compared against "
        f"{len(claims)} declared source values"
    )
    return Comparison(
        check="declared-figure-vs-source",
        subjects=occurrences,
        authority=len(claims),
        rule=RULE_FIGURE,
        detail=detail,
        findings=tuple(findings),
    )


# --------------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------------

CHECKS = (
    "leaf-document-vs-contract",
    "master-row-vs-leaf-document",
    "register-owner-arrow-vs-leaf-set",
    "declared-figure-vs-source",
)

COORDINATION_ROOT_ENV = "AR_COORDINATION_ROOT"

FIGURE_SHAPES = ("line_count", "case_count")


def parse_figure_claim(value: str) -> FigureClaim:
    """Read one ``--figure-claim`` argument, or refuse by naming the form it expected.

    The form is ``PATTERN:SHAPE:SOURCE:DOCUMENT[:DOCUMENT...]``: the regex naming the source in
    prose, the shape of the figure, the source whose value is the authority, and the documents the
    claim is scoped to. A claim names at least one document, because a restatement that names no
    file is a guess rather than a comparison.
    """
    parts = value.split(":")
    if len(parts) < 4:
        raise RecordIntegrityError(
            f"--figure-claim {value!r} is not PATTERN:SHAPE:SOURCE:DOCUMENT[:DOCUMENT...]"
        )
    label_pattern, shape, source_text, *document_texts = parts
    if shape not in FIGURE_SHAPES:
        raise RecordIntegrityError(
            f"--figure-claim {value!r} names shape {shape!r}; the shipped shapes are "
            f"{', '.join(FIGURE_SHAPES)}"
        )
    return FigureClaim(
        source=Path(source_text).expanduser(),
        label_pattern=label_pattern,
        shape=shape,
        stated_by="command line",
        documents=tuple(Path(text).expanduser() for text in document_texts),
    )


def coordination_root_from_environment() -> Path:
    """The coordination root the running session was told about, or a refusal naming the input.

    Requirement 3 asks for a check runnable *at any moment from the task root*. The root is an
    input, not a constant: this module is shipped code and may not hard-code one machine's layout,
    so the caller supplies it with ``--coordination-root`` or ``AR_COORDINATION_ROOT``.
    """
    value = os.environ.get(COORDINATION_ROOT_ENV)
    if not value:
        raise RecordIntegrityError(
            f"no coordination root was supplied; pass --coordination-root or set "
            f"{COORDINATION_ROOT_ENV}"
        )
    root = Path(value).expanduser()
    if not (root / "tasks").is_dir():
        raise RecordIntegrityError(f"{root} carries no tasks/ directory, so it is not a root")
    return root


def run_all(
    coordination_root: Path,
    *,
    register_path: Path | None = None,
    task_roots_scope: Sequence[Path] | None = None,
    figure_claims: Sequence[FigureClaim] = (),
) -> tuple[Comparison, ...]:
    """Run every check that needs no per-project figure declarations.

    The figure comparison joins the run only when claims are supplied: its authority side is a
    per-project declaration, so there is nothing for it to compare on its own. Every other check
    derives both of its sides from the coordination tree.
    """
    comparisons = [
        check_leaf_document_against_contract(coordination_root, task_roots_scope=task_roots_scope),
        check_master_rows_against_leaf_documents(
            coordination_root, task_roots_scope=task_roots_scope
        ),
    ]
    if register_path is not None:
        comparisons.append(check_register_row_ownership(register_path))
    if figure_claims:
        documents: list[Path] = []
        for claim in figure_claims:
            for document in claim.documents or ():
                if document not in documents:
                    documents.append(document)
        comparisons.append(check_declared_figure_currency(documents, figure_claims))
    return tuple(comparisons)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the two sides of a record fact and report the rows where they disagree. "
            "Every check states the populations it compared, so a zero is licensed by what it "
            "was drawn from. Nothing is written."
        )
    )
    parser.add_argument(
        "--coordination-root",
        type=Path,
        default=None,
        help=(
            f"The coordination root whose tasks/ tree is walked (read-only). Defaults to "
            f"${COORDINATION_ROOT_ENV}."
        ),
    )
    parser.add_argument(
        "--task-root",
        type=Path,
        action="append",
        default=None,
        help="Restrict the leaf/contract comparison to this task root; repeatable.",
    )
    parser.add_argument(
        "--register",
        type=Path,
        default=None,
        help="A defect register whose Owner/State arrows are compared against its master's leaf set.",
    )
    parser.add_argument(
        "--figure-claim",
        action="append",
        default=None,
        metavar="PATTERN:SHAPE:SOURCE:DOCUMENT[:DOCUMENT...]",
        help=(
            "A prose figure to compare with the source it describes: the regex naming the source, "
            "one of line_count/case_count, the authority source, and the document(s) the claim is "
            "scoped to. Repeatable; the figure check runs only when at least one is given."
        ),
    )
    parser.add_argument(
        "--check",
        action="append",
        choices=CHECKS,
        default=None,
        help="Run only this check; repeatable. Default: every check that needs no extra input.",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    coordination_root = args.coordination_root or coordination_root_from_environment()
    figure_claims = tuple(parse_figure_claim(value) for value in args.figure_claim or ())
    selected = set(args.check) if args.check else None
    comparisons = run_all(
        coordination_root,
        register_path=args.register,
        task_roots_scope=args.task_root,
        figure_claims=figure_claims,
    )
    if selected is not None:
        comparisons = tuple(row for row in comparisons if row.check in selected)
    if args.format == "json":
        print(json.dumps([row.to_dict() for row in comparisons], indent=2))
    else:
        for row in comparisons:
            print(row.render())
    return 0 if all(row.ok for row in comparisons) else 1


if __name__ == "__main__":
    sys.exit(main())
