"""External projection: Markdown and JSON as sibling views from the same resolved records.

``Doc13:265`` states the whole shape and ``KS-R20@v1`` §4 turns it into behaviour: Markdown and JSON
"are sibling views from the same resolved records", they are rendered by the same renderer/profile
version, and neither is the portable export -- that is ``KS-R06@v1``'s different contract.

**The renderer places authored text; it does not produce it.** There is no model call here, no
summary, no score and no reassessment. A displayed disposition is the recorded disposition; a
displayed status is the stored status; an unassessed claim renders as unassessed rather than as
favourably assessed. Requirement 4.4's "renderers do not call an LLM to invent fresh explanation"
is therefore true of this module by construction -- the only functions it contains build strings out
of the payload it was handed.

**Every artifact carries the three recorded values.** Each
:class:`~agents_remember.models.knowledge.projection_manifest.RenderedOutput` is built here with its
stable identity, its source snapshot and the renderer/profile version. A view payload whose snapshot
cannot be read is not projected at all: it is reported as an unresolved projection input, because
requirement 4.3 forbids emitting an artifact without all three.

**Conditions accompany the invariant even in a compact view.** Requirement 4.8 is why the Markdown
renderer writes the essential conditions *before* the statement's prose and writes an explicit
omission line when the view could not carry them: a compact projection may shorten prose it is
licensed to shorten and may not drop the conditions under which the invariant applies.

**A detection signal and an authored description stay separately attributed.** Requirement 4.9: the
two are written as two blocks with their own provenance lines, and the payload carries no merged
field combining them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_views import (
    VIEW_RENDERER_VERSION,
    read_knowledge_view,
)
from agents_remember.memory.knowledge.managed_projection import (
    ManagedProjectionWriter,
    ProjectionHooks,
    refusal_report,
)
from agents_remember.models.knowledge.projection_manifest import (
    DestinationProfile,
    ProjectionPlan,
    ProjectionRefusal,
    ProjectionReport,
    RenderedOutput,
)
from agents_remember.models.knowledge.view import ViewPayloadUnion, ViewRequest

__all__ = [
    "PROJECTION_PROFILE_VERSION",
    "ProjectionOptions",
    "project_knowledge",
    "render_payload_as_json",
    "render_payload_as_markdown",
]

# The JSON sibling view is *not* an export and is never described, named or documented as one. The
# marker is written into the artifact itself so a reader who finds the file cannot mistake it for
# ``KS-R06@v1``'s portable round trip.
PROJECTION_NOT_AN_EXPORT = (
    "This file is a projection of resolved knowledge records, not a portable database export. "
    "The complete portable round trip is a different contract."
)

PROJECTION_PROFILE_VERSION = VIEW_RENDERER_VERSION


@dataclass(frozen=True)
class ProjectionOptions:
    """Everything one projection is configured with, as one value rather than seven arguments.

    ``authorized_overwrites`` is the explicit per-path caller authorization requirement 5.8 makes the
    only route to replacing an externally edited file, and ``hooks`` is the deterministic
    interruption seam the vault-safety case uses.
    """

    repository_root: Path | None = None
    code_tree_id: str | None = None
    authorized_overwrites: tuple[str, ...] = ()
    hooks: ProjectionHooks | None = None


def _document_path(payload: ViewPayloadUnion, subject: str) -> str:
    """Where one payload's artifact lands, from the view name and the projected identity.

    The destination is derived from the view and the subject's recorded identity, never from a
    symbol name, a path prefix, a directory depth or a file extension. Two records with equal
    identities would collide here, and the writer reports that as a collision rather than
    disambiguating it.
    """

    safe = "".join(
        character if character.isalnum() or character in "-_." else "-" for character in subject
    )
    return f"{payload.view}/{safe or 'unidentified'}"


def render_payload_as_markdown(payload: ViewPayloadUnion, subject: str) -> str:
    """One payload as Markdown: authored text placed with its provenance, and nothing composed."""

    lines: list[str] = [
        f"# {payload.view} — {subject}",
        "",
        f"- renderer/profile version: `{payload.renderer_version}`",
        f"- source snapshot: `{payload.snapshot.logical_digest}`",
        f"- schema generation: `{payload.snapshot.schema_version}`",
        f"- complete within declared scope: `{str(payload.completeness.complete_within_declared_scope).lower()}`",
        f"- declared scope: graph `{payload.completeness.scope.recorded_graph}`, policy "
        f"`{payload.completeness.scope.traversal_policy}`",
        "",
    ]
    for row in payload.rows:
        lines.extend(_row_markdown(row))
    if payload.limitations:
        lines.extend(["## Unresolved limitations", ""])
        for limitation in payload.limitations:
            lines.append(f"- `{limitation.code}` {limitation.detail}")
        lines.append("")
    lines.append(f"> {PROJECTION_NOT_AN_EXPORT}")
    lines.append("")
    return "\n".join(lines)


def _row_markdown(row: Any) -> list[str]:
    """One row's Markdown, with its ordered position and its provenance class beside the value."""

    provenance = row.provenance
    if provenance.authored is not None:
        class_line = (
            f"provenance: **authored** by `{provenance.authored.author_ref}` — "
            f"{provenance.authored.rationale}"
        )
    else:
        class_line = (
            f"provenance: **mechanical** rule `{provenance.rule_id}` v{provenance.rule_version}"
        )
    lines = [
        f"## position {row.order.position}",
        "",
        f"- ordering input: `{row.order.ordering_input}` (`{provenance.provenance_class}`)",
        f"- {class_line}",
    ]
    lines.append(f"- subject: `{row.subject.record_kind}:{row.subject.record_id}`")
    conditions = getattr(row, "essential_conditions", ())
    if conditions:
        # Requirement 4.8: the conditions come before the prose they qualify, never after it.
        lines.append("- essential conditions:")
        lines.extend(f"    - {condition}" for condition in conditions)
    elif getattr(row, "conditions_omitted", False):
        lines.append("- essential conditions: **omitted by this view's scope**")
    statement = getattr(row, "statement", None)
    if statement:
        lines.append("")
        lines.append(statement)
    consequence = getattr(row, "consequence", None)
    if consequence is not None:
        lines.append("")
        lines.append(_consequence_markdown(consequence))
    if getattr(row, "assessment_status", None) == "missing":
        lines.append("- assessment: **none recorded**")
    lines.append("")
    return lines


def _consequence_markdown(consequence: Any) -> str:
    """One no-consequence statement, with the class that produced it written beside it."""

    provenance = consequence.provenance
    if provenance.authored is not None:
        return (
            f"no consequence — **authored** by `{provenance.authored.author_ref}` "
            f"(claim `{consequence.claim_ref}`): {consequence.detail}"
        )
    return (
        f"no consequence — **mechanical** under rule `{provenance.rule_id}` "
        f"v{provenance.rule_version}: {consequence.detail}"
    )


def render_payload_as_json(payload: ViewPayloadUnion, subject: str) -> str:
    """One payload as the JSON sibling view, from the same resolved rows the Markdown view used."""

    body = {
        "projectionNote": PROJECTION_NOT_AN_EXPORT,
        "view": payload.view,
        "subject": subject,
        "rendererVersion": payload.renderer_version,
        "sourceSnapshot": payload.snapshot.logical_digest,
        "schemaVersion": payload.snapshot.schema_version,
        "completeWithinDeclaredScope": payload.completeness.complete_within_declared_scope,
        "counts": payload.counts.model_dump(mode="json"),
        "limitations": [item.model_dump(mode="json") for item in payload.limitations],
        "rows": [row.model_dump(mode="json") for row in payload.rows],
    }
    return json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def project_knowledge(
    database_path: Path,
    profile: DestinationProfile,
    requests: tuple[tuple[ViewRequest, str], ...],
    options: ProjectionOptions | None = None,
) -> ProjectionReport:
    """Render each requested view and hand the whole plan to the projection writer.

    ``requests`` pairs one view request with the stable identity the artifact is about, because the
    identity is what the artifact records and the manifest tracks -- not the destination path, which
    is configuration.
    """

    resolved = options or ProjectionOptions()
    if not requests:
        raise ValueError("a projection names at least one view request to resolve its namespace")
    context = open_read_context(
        database_path,
        requests[0][0].repository_id,
        repository_root=resolved.repository_root,
        code_tree_id=resolved.code_tree_id,
    )
    outputs: list[RenderedOutput] = []
    for request, subject in requests:
        result = read_knowledge_view(database_path, context, request)
        if result.state == "refused" or result.payload is None:
            return refusal_report(
                profile,
                ProjectionRefusal(
                    code="unresolved_projection_input",
                    detail=(
                        "a requested view could not be read, so no artifact is emitted without the "
                        f"identity, snapshot and renderer version requirement 4.3 requires: "
                        f"{None if result.refusal is None else result.refusal.detail}"
                    ),
                    offending_path=subject,
                    resolved_root=profile.destination_root,
                    next_action="resolve the refused input and re-run the projection",
                ),
            )
        outputs.extend(_outputs_for(result.payload, subject, profile))
    plan = ProjectionPlan(
        destination=profile,
        outputs=tuple(outputs),
        authorized_overwrites=resolved.authorized_overwrites,
    )
    writer = ManagedProjectionWriter(resolved.hooks)
    return writer.write(plan)


def _outputs_for(
    payload: ViewPayloadUnion, subject: str, profile: DestinationProfile
) -> list[RenderedOutput]:
    """The sibling artifacts for one payload, one per format the profile declares."""

    base = _document_path(payload, subject)
    rendered: list[RenderedOutput] = []
    for declared in profile.formats:
        if declared == "markdown":
            rendered.append(
                _output(
                    payload,
                    subject,
                    f"{base}.md",
                    "markdown",
                    render_payload_as_markdown(payload, subject),
                )
            )
        else:
            rendered.append(
                _output(
                    payload,
                    subject,
                    f"{base}.json",
                    "json",
                    render_payload_as_json(payload, subject),
                )
            )
    return rendered


def _output(
    payload: ViewPayloadUnion,
    subject: str,
    relative_path: str,
    declared_format: str,
    text: str,
) -> RenderedOutput:
    """One rendered artifact, with the three recorded values the manifest and the reader need."""

    return RenderedOutput(
        destination_relative_path=relative_path,
        stable_identity=subject,
        record_kind=payload.view,
        format=declared_format,  # type: ignore[arg-type]
        source_snapshot=payload.snapshot.logical_digest,
        renderer_version=payload.renderer_version,
        text=text,
    )
