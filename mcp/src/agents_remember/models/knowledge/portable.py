"""The portable export/import vocabulary, and the boundary it draws around external input.

Three splits carry this module's contract, and each one exists because collapsing it would make a
statement the code cannot support:

* **A request versus an admitted identity.** :class:`ExportRequest` carries the exact logical
  identity the caller admitted for the database it names. Storage re-reads it before encoding, so
  an export cannot silently describe a dataset that moved between the caller's check and the read
  -- the same rule every other read in this package follows.
* **A validated artifact versus a published dataset.** :class:`PortableValidation` reports what was
  checked; :class:`ImportResult` reports what now exists. Neither can carry a semantic judgement
  about whether the knowledge is correct, and neither can grant acceptance: a row whose
  ``state_at_origin`` says ``accepted`` is imported as that stored value and nothing more.
* **A staging fact versus a destination fact.** An import can validate perfectly and still not
  publish, so the result reports a verified logical identity separately from the state of the
  destination it did or did not reach.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import PATH_MAX_LENGTH, SHA256_PATTERN, KnowledgeModel
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.result import KnowledgeRefusal

# The one portable format this package writes and reads. It is declared here rather than in the
# encoder so a caller can name the format without importing the storage module that implements it.
ExportFormat = Literal["ar-knowledge-export/v1"]
EXPORT_FORMAT: ExportFormat = "ar-knowledge-export/v1"


class ExportRequest(KnowledgeModel):
    """One request to encode an admitted dataset as its complete portable artifact.

    ``expected_identity`` is required: an export is addressed at *a dataset*, not at whatever a
    path currently holds, and the identity is what makes the two the same object.
    """

    database_path: Path
    expected_identity: SnapshotIdentity


class ImportRequest(KnowledgeModel):
    """One request to validate and install a portable artifact into an admitted destination.

    ``expected_destination`` is either the exact logical identity the caller observed at the
    destination or ``None`` for "the destination is expected to be absent". There is no third mode:
    an occupied destination the caller did not admit is refused rather than replaced, and this
    operation never patches a live database in place.

    ``expected_repository_id`` is optional and narrows the import to one namespace. It rests on the
    same fact the repository table already declares -- a dataset is bound to exactly one namespace
    -- so it admits nothing; it refuses an artifact that is internally valid but belongs elsewhere.
    """

    artifact: str = Field(min_length=1)
    destination_path: Path
    expected_destination: SnapshotIdentity | None = None
    expected_repository_id: str | None = None


class PortableValidation(KnowledgeModel):
    """What one artifact's validation established, or the refusal that ended it.

    ``row_counts`` covers every canonical table including the empty ones, because "present and
    empty" is the fact that separates a complete export from one that dropped a collection.
    """

    state: Literal["validated", "refused"]
    repository_id: str | None = None
    schema_fingerprint: str | None = None
    logical_digest: str | None = None
    declared_digest: str | None = None
    row_counts: dict[str, int] = Field(default_factory=dict)
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_validation(self) -> PortableValidation:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused validation must carry its refusal")
            if self.logical_digest is not None:
                raise ValueError(
                    "a refused artifact established no logical identity, so it reports none"
                )
            return self
        if self.refusal is not None:
            raise ValueError("a validated artifact cannot also carry a refusal")
        if self.repository_id is None or self.logical_digest is None:
            raise ValueError(
                "a validated artifact names the namespace it is bound to and the logical digest "
                "its records produce"
            )
        return self


class ExportResult(KnowledgeModel):
    """One complete portable artifact, or the refusal that prevented producing it.

    ``artifact`` is the exact envelope text. It is carried rather than written so the encoder has
    no filesystem side effect at all: a caller that wants the artifact on disk writes the bytes
    through the repository's own atomic write, and a caller that wants to hash them hashes the
    string it was handed.
    """

    state: Literal["exported", "refused"]
    artifact: str | None = None
    format: ExportFormat | None = None
    identity: SnapshotIdentity | None = None
    artifact_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    row_counts: dict[str, int] = Field(default_factory=dict)
    notes: tuple[str, ...] = ()
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_export(self) -> ExportResult:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused export must carry its refusal")
            if self.artifact is not None or self.identity is not None:
                raise ValueError("a refused export produced no artifact and reports no identity")
            return self
        if self.refusal is not None:
            raise ValueError("an export that was produced cannot also carry a refusal")
        if self.artifact is None or self.identity is None or self.format is None:
            raise ValueError(
                "an export reports the artifact it produced, its format and the identity it seals"
            )
        return self


class ImportResult(KnowledgeModel):
    """What one import validated and what it did at the destination.

    ``state`` is ``installed`` or ``no_change`` when the destination now holds the artifact's exact
    logical dataset (``no_change`` meaning it already did, so no bytes were rewritten), and
    ``refused`` when it does not. ``verified_identity`` is carried independently of the state
    because a validated artifact that failed to publish has an identity worth naming -- and
    ``publication`` is ``None`` in that case rather than a state, because nothing was published.
    """

    state: Literal["installed", "no_change", "refused"]
    verified_identity: SnapshotIdentity | None = None
    destination_identity: SnapshotIdentity | None = None
    destination_ref: str | None = Field(default=None, max_length=PATH_MAX_LENGTH)
    publication: Literal["published", "no_change"] | None = None
    row_counts: dict[str, int] = Field(default_factory=dict)
    validation: PortableValidation | None = None
    refusal: KnowledgeRefusal | None = None

    @model_validator(mode="after")
    def _require_consistent_import(self) -> ImportResult:
        if self.state == "refused":
            if self.refusal is None:
                raise ValueError("a refused import must carry its refusal")
            if self.destination_identity is not None or self.publication is not None:
                raise ValueError(
                    "a refused import reports no destination identity and no publication state"
                )
            return self
        if self.refusal is not None:
            raise ValueError("an import that reached a destination cannot also carry a refusal")
        if self.verified_identity is None or self.destination_identity is None:
            raise ValueError(
                "an import that was not refused names the identity it verified and the identity "
                "the destination now holds"
            )
        if self.verified_identity.logical_digest != self.destination_identity.logical_digest:
            raise ValueError(
                "an imported destination must hold exactly the logical dataset the import verified"
            )
        return self
