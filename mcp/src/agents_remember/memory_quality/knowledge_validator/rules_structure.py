"""MIK-R22's per-file and cross-tree structure rules: shape, identity, single owner, freezing.

* **Rule 1, shape.** Every file parses under its location's model; a converted tree keeps its layout
  marker; every JSON file is canonically formatted, and the refusal names the formatter command.
* **Rule 2, identity.** A record's filename begins with its ID. IDs -- record IDs and realization
  and proof entry IDs -- are unique across the tree; a duplicate names every file that holds it, and
  after a merge it is a conflict between those files. History row IDs are not part of this: a
  history file is checked for shape only (rule 7), and two frozen files could never be repaired.
* **Rule 4, single owner.** A record field its schema does not declare is a relationship recorded
  on the wrong side (an invariant that lists its realizations, tests or families).
* **Rule 6, locators and content.** Every locator kind is one of the three, and every ``content``
  is ``sha256:`` and 64 hex digits.
* **Rule 6, bases.** A comparison base with no layout marker must be replaced by its conversion
  (MIK-R24 rules 7 and 8) before anchors can be compared; a route that has none is refused.
* **Rule 7, freezing.** A history file ``closed`` in any base is byte-identical in the candidate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from agents_remember.memory_quality.knowledge_validator.parsed import (
    HISTORY_DIRECTORY,
    ProblemCategory,
)
from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    register_rule,
    sidecar_entries,
)
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.models.knowledge_files.history import frozen_history_violation


def _problems(context: ValidationContext, category: ProblemCategory) -> Iterator[Finding]:
    for problem in context.parsed.problems:
        if problem.category == category:
            yield Finding(problem.path, problem.field, problem.message)


def check_shape(context: ValidationContext) -> Iterator[Finding]:
    if not context.candidate.converted:
        yield Finding(
            LAYOUT_MARKER_PATH,
            "",
            "the layout marker is missing; a converted memory tree keeps it (MIK-R21 rule 1)",
        )
    yield from _problems(context, "shape")


def check_canonical(context: ValidationContext) -> Iterator[Finding]:
    yield from _problems(context, "canonical")


def check_identity(context: ValidationContext) -> Iterator[Finding]:
    yield from _problems(context, "identity")
    holders: dict[str, list[str]] = defaultdict(list)
    for record in context.parsed.records:
        holders[record.record.id].append(record.path)
    for sidecar in context.parsed.sidecars:
        for entry in sidecar_entries(sidecar):
            holders[entry.id].append(sidecar.path)
    merge = len(context.bases) > 1
    for identifier, paths in sorted(holders.items()):
        if len(paths) < 2:
            continue
        what = "merge conflict: " if merge else ""
        yield Finding(
            paths[0],
            "id",
            f"{what}ID {identifier} is defined by more than one file: {', '.join(paths)}",
        )
    for path in sorted(context.parsed.markdown):
        if path.startswith("knowledge/") and path.removesuffix(".md") + ".json" not in (
            context.candidate.files
        ):
            yield Finding(path, "", "a record's Markdown sits beside its JSON record")


def check_single_owner(context: ValidationContext) -> Iterator[Finding]:
    for finding in _problems(context, "single_owner"):
        yield Finding(
            finding.path,
            finding.field,
            f"'{finding.field}' is not a field of this record: a relationship is recorded only "
            "on its owner's side (Doc14 §2)",
        )


def check_locators(context: ValidationContext) -> Iterator[Finding]:
    yield from _problems(context, "locator")


def check_content(context: ValidationContext) -> Iterator[Finding]:
    yield from _problems(context, "content")


def check_bases_converted(context: ValidationContext) -> Iterator[Finding]:
    if context.conversion:
        return
    for base in context.bases:
        if not base.converted:
            yield Finding(
                LAYOUT_MARKER_PATH,
                "",
                f"comparison base {base.label} is unconverted; it is replaced by its conversion "
                "(MIK-R24 rules 7 and 8), which this route does not have: a crossing sync converts it",
            )


def check_history_frozen(context: ValidationContext) -> Iterator[Finding]:
    paths = {
        path
        for tree in (*context.closed_before, context.candidate)
        for path in tree.files
        if path.startswith(HISTORY_DIRECTORY)
    }
    for path in sorted(paths):
        bases = [base.get(path) for base in context.closed_before]
        candidate = context.candidate.get(path)
        if frozen_history_violation(bases, candidate):
            change = "deleted" if candidate is None else "changed"
            yield Finding(
                path,
                "",
                f"this history file is closed in a base and was {change}: a closed history file "
                "is frozen (MIK-R07 rule 7); a correction belongs to a new leaf's rows, or to the "
                "same leaf's next attempt file",
            )


STRUCTURE_RULES = (
    ValidationRule("R22.1-shape", "MIK-R22 rule 1", "files match their schema", check_shape),
    ValidationRule(
        "R22.1-canonical", "MIK-R22 rule 1", "JSON is canonically formatted", check_canonical
    ),
    ValidationRule(
        "R22.2-identity", "MIK-R22 rule 2", "IDs are unique and name files", check_identity
    ),
    ValidationRule(
        "R22.4-single-owner", "MIK-R22 rule 4", "no relationship on both sides", check_single_owner
    ),
    ValidationRule(
        "R22.6-locator", "MIK-R22 rule 6", "locator kinds are supported", check_locators
    ),
    ValidationRule(
        "R22.6-content", "MIK-R22 rule 6", "content fields are well formed", check_content
    ),
    ValidationRule(
        "R22.6-base-converted",
        "MIK-R22 rule 6",
        "comparison bases are converted",
        check_bases_converted,
    ),
    ValidationRule(
        "R22.7-history-frozen",
        "MIK-R22 rule 7",
        "closed history files are frozen",
        check_history_frozen,
    ),
)

for _rule in STRUCTURE_RULES:
    register_rule(_rule)
