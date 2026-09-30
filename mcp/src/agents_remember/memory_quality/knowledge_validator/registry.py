"""The validator's rule registry (MIK-R22 rule 9) and the context every rule reads.

A rule is a :class:`ValidationRule`: a stable ``id``, the packet rule that owns it, a one-line
summary, a ``check`` over the :class:`ValidationContext`, ``report_only`` and ``writer_reports``. A
report-only rule's findings are carried in the report and never refuse, whatever the check returns:
the flag lives here, in the registry, and nowhere else.

``writer_reports`` marks a rule that **refuses at every commit route but is only reported inside the
writer**: a leaf may break it mid-way and repair it before closeout. MIK-R04's family route rules
(Coverage, Non-empty, an added route's directory) carry it (MIK-R04 rule 6). The validator's own
report is unchanged -- :func:`validate_tree` and :func:`require_valid_commit` still refuse such a
finding -- so a writer (MIK-R12) reads :func:`writer_reported_rule_ids` and treats those violations
as reports; no commit route may.

MIK-R22 registers its own rules when :mod:`.rules` is imported. Later packets add theirs with
:func:`register_rule` -- MIK-R04 (family Coverage, Non-empty and ``route_unassigned``), MIK-R20 (the
census schemas and their append-only rules), MIK-R27 (admission) and MIK-R13 (decision content) --
and every registered rule then runs wherever the validator runs (rule 8). There is no way to run a
subset at a commit route.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cached_property

from agents_remember.memory_quality.knowledge_validator.parsed import (
    ParsedTree,
    SidecarFile,
    parse_sidecars_leniently,
)
from agents_remember.memory_quality.knowledge_validator.trees import CodeTree, KnowledgeTree
from agents_remember.models.knowledge_files.shapes import Anchor, AnchorTarget
from agents_remember.models.knowledge_files.sidecars import (
    FileSidecar,
    ProofEntry,
    RealizationEntry,
)


@dataclass(frozen=True)
class Finding:
    """What a check reports; the validator adds the rule's ID and its report-only flag."""

    path: str
    field: str
    message: str


RuleCheck = Callable[["ValidationContext"], Iterable[Finding]]


@dataclass(frozen=True)
class ValidationRule:
    id: str
    owner: str
    summary: str
    check: RuleCheck
    report_only: bool = False
    writer_reports: bool = False


_REGISTRY: dict[str, ValidationRule] = {}


def register_rule(rule: ValidationRule) -> ValidationRule:
    """Add ``rule`` to the registry; an ID is registered once."""

    if rule.id in _REGISTRY:
        raise ValueError(f"validator rule {rule.id!r} is already registered")
    _REGISTRY[rule.id] = rule
    return rule


def registered_rules() -> tuple[ValidationRule, ...]:
    """Every registered rule, in registration order."""

    return tuple(_REGISTRY.values())


def writer_reported_rule_ids() -> frozenset[str]:
    """The IDs of the refusing rules a writer reports instead of refusing (``writer_reports``)."""

    return frozenset(
        rule.id for rule in _REGISTRY.values() if rule.writer_reports and not rule.report_only
    )


def sidecar_entries(sidecar: SidecarFile) -> tuple[RealizationEntry | ProofEntry, ...]:
    """The realization and proof entries of a file sidecar (none for a route sidecar)."""

    if isinstance(sidecar.sidecar, FileSidecar):
        return (*sidecar.sidecar.realizes, *(sidecar.sidecar.proves or ()))
    return ()


def anchor_key(anchor: Anchor, sidecar: SidecarFile) -> str:
    """The comparison key of an anchor as it sits in ``sidecar``, its own path filled in."""

    document = anchor.to_document()
    document.setdefault("path", sidecar.sidecar.path)
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class BaseAnchors:
    """The anchors of the comparison bases: per entry ID, and per sidecar for reference targets."""

    entries: frozenset[tuple[str, str]]
    references: frozenset[tuple[str, str]]

    def carries_entry(self, entry_id: str, key: str) -> bool:
        return (entry_id, key) in self.entries

    def carries_reference(self, sidecar_path: str, key: str) -> bool:
        return (sidecar_path, key) in self.references


def _base_anchors(bases: Iterable[KnowledgeTree]) -> BaseAnchors:
    entries: set[tuple[str, str]] = set()
    references: set[tuple[str, str]] = set()
    for base in bases:
        for sidecar in parse_sidecars_leniently(base):
            for reference in sidecar.sidecar.references.values():
                for target in reference.targets:
                    if isinstance(target, AnchorTarget):
                        references.add((sidecar.path, anchor_key(target.anchor, sidecar)))
            for entry in sidecar_entries(sidecar):
                entries.add((entry.id, anchor_key(entry.anchor, sidecar)))
    return BaseAnchors(frozenset(entries), frozenset(references))


@dataclass(frozen=True)
class ValidationContext:
    """One validation: the candidate K_C, its comparison bases and its paired code tree.

    ``bases`` is K_B at a commit route and every parent at a merge. ``code`` is the paired code
    tree anchors are checked against; it is ``None`` only for a standalone conversion, which checks
    no anchor for path existence (``conversion``).
    """

    candidate: KnowledgeTree
    parsed: ParsedTree
    bases: tuple[KnowledgeTree, ...] = ()
    code: CodeTree | None = None
    conversion: bool = False
    leaf_publication: bool = False
    """The commit publishes a leaf (its closeout, direct landing or recorded landing): every history
    file not closed in a base is its own and is checked whatever its ``closed`` flag (MIK-R09)."""

    @cached_property
    def record_ids(self) -> frozenset[str]:
        parsed = frozenset(record.record.id for record in self.parsed.records)
        return parsed | self.parsed.unparsed_record_ids

    @cached_property
    def base_anchors(self) -> BaseAnchors:
        return _base_anchors(self.bases)
