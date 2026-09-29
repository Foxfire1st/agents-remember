"""MIK-R20's census rules, registered with the validator (MIK-R22 rule 9).

The checks themselves are :mod:`agents_remember.memory_quality.knowledge_census.checks`; this module
runs them once per validation and hands each registered rule its own findings. Record existence
(``R20.2-claim-records``) uses the validator's record IDs, so an unparseable record still resolves.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable, Iterator

from agents_remember.memory_quality.knowledge_census.checks import (
    CENSUS_RULES,
    CensusFinding,
    check_censuses,
)
from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    register_rule,
)

# One validation runs every rule over the same context; the census is read once for all of them.
# The cache holds only findings, keyed by the context's identity, and each entry is dropped when its
# context is collected, so no tree outlives its validation. A lookup is a single ``dict.get``.
_FINDINGS: dict[int, tuple[weakref.ref[ValidationContext], list[CensusFinding]]] = {}


def _findings(context: ValidationContext) -> list[CensusFinding]:
    cached = _FINDINGS.get(id(context))
    if cached is not None and cached[0]() is context:
        return cached[1]
    findings = check_censuses(
        context.candidate.files,
        bases=[base.files for base in context.bases],
        record_ids=context.record_ids,
    )
    _FINDINGS[id(context)] = (weakref.ref(context), findings)
    weakref.finalize(context, _FINDINGS.pop, id(context), None)
    return findings


def _rule_check(rule_id: str) -> Callable[[ValidationContext], Iterator[Finding]]:
    def check(context: ValidationContext) -> Iterator[Finding]:
        for finding in _findings(context):
            if finding.rule == rule_id:
                yield Finding(finding.path, finding.field, finding.message)

    return check


CENSUS_VALIDATION_RULES = tuple(
    register_rule(ValidationRule(rule_id, owner, summary, _rule_check(rule_id)))
    for rule_id, (owner, summary) in CENSUS_RULES.items()
)
