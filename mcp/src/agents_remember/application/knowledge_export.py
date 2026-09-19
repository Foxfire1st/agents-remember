"""The composition seam between admitted authority and the portable knowledge artifact.

This is the fourth application module the storage design anticipated, and it exists for the same
reason as the three before it: exporting and importing a whole dataset is a different composed
operation from a candidate write, a snapshot publication or a merge, and keeping them apart leaves
each entry point readable as one intent.

Nothing here decides authority, and nothing here holds durable state. It hands the typed request to
:mod:`agents_remember.memory.knowledge` and returns the typed result unchanged, so a lower owner --
the worktree or lifecycle package that wants to carry a dataset between candidates -- receives
:mod:`agents_remember.models.knowledge` values and never an import of the store.

Two boundaries this seam does not cross, stated here because a reader of the artifact will look for
them: an export is **not** a filtered read response and **not** a Markdown projection, and an import
creates **no Git commit** and restores **no Git ancestry**. Capturing an exported artifact into a
memory tree, or committing a restored database, is the existing candidate-tree and closeout owner's
operation.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.memory.knowledge.export_import import (
    artifact_digest,
    export_knowledge_dataset,
    import_knowledge_dataset,
    read_artifact,
)
from agents_remember.memory.knowledge.export_portable import (
    EXPORT_FORMAT,
    PORTABLE_NOTES,
    logical_body_of_artifact,
    parse_export,
    validate_export,
)
from agents_remember.models.knowledge.portable import (
    ExportRequest,
    ExportResult,
    ImportRequest,
    ImportResult,
    PortableValidation,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = [
    "EXPORT_FORMAT",
    "PORTABLE_NOTES",
    "PortableValidation",
    "artifact_digest",
    "canonical_body_of_artifact",
    "export_knowledge_dataset",
    "import_knowledge_dataset",
    "logical_body_of_artifact",
    "read_knowledge_artifact",
    "validate_knowledge_artifact",
]


def export_knowledge_artifact(request: ExportRequest) -> ExportResult:
    """Encode one admitted dataset as its complete portable artifact, or return the refusal."""

    return export_knowledge_dataset(request)


def import_knowledge_artifact(request: ImportRequest) -> ImportResult:
    """Validate one artifact and install its dataset at an admitted destination, or refuse.

    The result reports what was validated and what now exists. Importing a row whose stored
    ``state_at_origin`` says ``accepted`` imports that value; it grants the receiving context no
    authority, and nothing on this path promotes a record.
    """

    return import_knowledge_dataset(request)


def validate_knowledge_artifact(
    text: str, *, expected_repository_id: str | None = None
) -> PortableValidation:
    """Validate one artifact's text without importing it, for a caller that only wants the verdict.

    This is the read-only half of the import: it answers whether the artifact is a complete export
    of a supported generation and what logical dataset it holds, and it produces no database at all.
    """

    parsed = parse_export(text)
    if isinstance(parsed, KnowledgeRefusal):
        return PortableValidation(state="refused", refusal=parsed)
    checked = validate_export(parsed, expected_repository_id=expected_repository_id)
    if checked.refusal is not None:
        return PortableValidation(state="refused", refusal=checked.refusal)
    if checked.validation is None:  # pragma: no cover - ValidatedExport forbids this pairing
        raise KnowledgeArtifactSeamDefect(
            "artifact validation returned neither a report nor a refusal"
        )
    return checked.validation


def read_knowledge_artifact(path: Path) -> str | KnowledgeRefusal:
    """Read one artifact file as UTF-8 text, or return the typed refusal that says why it is not one.

    The helper's contract is a value or a refusal, like every other reader on this boundary: a file
    that cannot be read at all is ``selected_input_unavailable``, and a byte sequence that is not
    UTF-8 is ``invalid_export``. Nothing on this path is persisted, and nothing raises -- an
    ``OSError`` or a ``UnicodeDecodeError`` handed to a caller to catch would be a failure with no
    code to branch on.
    """

    return read_artifact(Path(path))


def canonical_body_of_artifact(text: str) -> dict[str, object] | KnowledgeRefusal:
    """Return the canonical logical body one *validated* artifact carries, or the refusal.

    Validation comes first on purpose. A body is what a comparison is made of, and an artifact the
    validator refuses -- a filtered projection, a document that dropped a collection, one whose
    declared seal does not cover its records -- has no dataset identity to compare against: returning
    a body for it would hand a caller the shape of a comparison it must not make. This is the
    read-only half of the recovery recipe, so it produces no database and no artifact, and the
    refusal it returns is the validator's own.
    """

    verdict = validate_knowledge_artifact(text)
    if verdict.refusal is not None:
        return verdict.refusal
    return logical_body_of_artifact(text)


class KnowledgeArtifactSeamDefect(RuntimeError):
    """A seam state the layer below makes unreachable: a report and a refusal both absent."""
