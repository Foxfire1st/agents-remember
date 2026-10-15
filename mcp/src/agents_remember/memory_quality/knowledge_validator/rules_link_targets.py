"""Record-link path existence at every validator route (MIK-R54 rule 11)."""

from __future__ import annotations

import json
from collections.abc import Iterator

from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.registry import (
    Finding,
    ValidationContext,
    ValidationRule,
    merge_report_note,
    merge_reports_at,
    register_rule,
)
from agents_remember.models.knowledge_files.shapes import ROUTE_TARGET_PREFIX, Anchor


def _key(target: Anchor | str) -> str:
    value = target.to_document() if isinstance(target, Anchor) else target
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def check_link_target_paths(context: ValidationContext) -> Iterator[Finding]:
    code = context.code
    if code is None:
        return
    carried = {
        (record.record.id, _key(link.target))
        for base in context.bases
        for record in parse_tree(base).records
        for link in getattr(record.record, "links", ())
        if isinstance(link.target, (Anchor, str))
    }
    for record in context.parsed.records:
        for index, link in enumerate(getattr(record.record, "links", ())):
            target = link.target
            if isinstance(target, Anchor):
                path = target.path
                if path is None or code.has_file(path):
                    continue
                kind = "file"
            elif isinstance(target, str) and target.startswith(ROUTE_TARGET_PREFIX):
                path = target.removeprefix(ROUTE_TARGET_PREFIX)
                if code.has_directory(path):
                    continue
                kind = "directory"
            else:
                continue
            equal_base = (record.record.id, _key(target)) in carried
            message = (
                f"record {record.record.id}: link {index} target {path!r} names no {kind} "
                f"in paired code tree {code.label}; restate or remove the link"
            )
            if equal_base and merge_reports_at(context):
                message += f"; {merge_report_note(context)}"
            yield Finding(record.path, f"links.{index}.target", message, carried=equal_base)


LINK_TARGET_RULE = register_rule(
    ValidationRule(
        "R22.6-link-target-path",
        "MIK-R54 rule 11 (MIK-R22 rule 6)",
        "record anchor and route link targets exist in the paired code tree",
        check_link_target_paths,
        merge_reports=True,
        merge_requires_carried=True,
    )
)
