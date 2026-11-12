"""Record update fields whose identity belongs to stored authorial content (MIK-R54)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .memory_state import deep_copy

if TYPE_CHECKING:
    from .authoring import Authoring


def record_origin(
    operation: Authoring,
    stored: Mapping[str, Any] | None,
    handoff_entry: str,
    evidence: Sequence[str],
    replace_evidence: bool,
) -> tuple[dict[str, Any], bool]:
    if stored is not None and isinstance(stored.get("origin"), Mapping):
        origin = deep_copy(stored["origin"])
        if not evidence and not replace_evidence:
            return origin, False
        if (
            origin.get(operation.owner.kind) != operation.owner.id
            or origin.get("task") != operation.owner.task
        ):
            if evidence:
                operation._foreign_evidence.setdefault(str(stored.get("id")), []).append(
                    (handoff_entry, tuple(evidence))
                )
            return origin, False
        handoff = dict(origin.get("handoff") or {})
        handoff.setdefault("path", operation.handoff_path)
        prior = [] if replace_evidence else handoff.get("evidence", [])
        handoff["evidence"] = list(dict.fromkeys([*prior, *evidence]))
        if not handoff["evidence"]:
            handoff.pop("evidence")
        origin["handoff"] = handoff
        return origin, True
    handoff: dict[str, Any] = {"path": operation.handoff_path}
    if evidence:
        handoff["evidence"] = list(dict.fromkeys(evidence))
    return {**operation.owner.origin(), "handoff": handoff, "handoffEntry": handoff_entry}, bool(
        evidence
    )


def remap_links(
    operation: Authoring, before: Mapping[str, Any] | None, fields: dict[str, Any], where: str
) -> None:
    if before is None or "alternatives" not in fields or "links" in fields:
        return
    alternatives = fields["alternatives"]
    if not isinstance(alternatives, list):
        return  # the record model names the malformed alternative list
    old = before.get("alternatives") or []
    links = deep_copy(before).get("links") or []
    for position, link in enumerate(links):
        if not isinstance(link, dict) or link.get("relation") != "reconsider_on":
            continue
        index = link.get("alternative")
        option = (
            old[index].get("option") if isinstance(index, int) and 0 <= index < len(old) else None
        )
        matches = [
            index
            for index, alternative in enumerate(alternatives)
            if isinstance(alternative, Mapping) and alternative.get("option") == option
        ]
        if option is None or len(matches) != 1:
            operation.problem(
                where,
                f"link {position} addressed alternative '{option}', which this update no longer holds; restate `links`",
            )
        else:
            link["alternative"] = matches[0]
    fields["links"] = links
