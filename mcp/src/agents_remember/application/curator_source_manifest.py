"""The curator's external-source plane: a bounded manifest, and the origin refs that bind to it.

ICR-R28@v2 requires that every authored item carry inspectable origin references, and
``BOOTSTRAP-HANDOVER.md`` fixes how external sources are retained: an external source is *not* a
:class:`~agents_remember.models.knowledge.source.SourceAnchor`, because an anchor names a
repository-relative path and an exact Git blob, so recording a URL there would fabricate a Git
identity the repository does not have. Instead the run retains a **bounded, attributable source
manifest** -- source URL/document identity, version or retrieval time, the inspected-content digest
when one is available, and the relevant passage/location -- and binds the authored record's existing
``Authorship.origin_refs`` to that manifest's own digest.

Three properties are the whole of it:

* **Nothing is invented.** A source exists here exactly when the curator declared one. No URL is
  resolved, no document is fetched, and no digest is computed over bytes this run never saw: the
  manifest records what the curator inspected and says which of those fields were recorded and which
  were not.
* **A declaration is attributable or it is refused.** ``document``, ``location`` and one of
  ``version``/``retrieved_at`` are required, because a source with none of them cannot be found again;
  a ``content_digest`` that is present must be a sha256, and its absence is recorded as ``null``
  rather than filled with a favourable default.
* **The manifest is written through the artifact owner the candidate already has.** It is one
  canonical-JSON file written atomically into the candidate directory beside the allocation journal,
  read back and verified, and named by its own sha256 -- so a reader resolves a record's origin
  reference to these exact bytes or to nothing.

The manifest is a record of what the hand-off list **declared**, and it says so in its own header: the
run's report is what says which entries committed. That split is deliberate, because the manifest is
written before the batch that could still refuse, and a record that claimed authors it did not reach
would be exactly the kind of false completeness this requirement refuses.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.models.knowledge.base import REFERENCE_MAX_LENGTH, SHA256_PATTERN

__all__ = [
    "ENTRY_SOURCES_KEY",
    "MAX_SOURCES_PER_ENTRY",
    "SOURCE_MANIFEST_NAME",
    "EntrySourceOutcome",
    "EntrySourceRefusal",
    "ExternalSource",
    "SourceCoverage",
    "SourceCoverageScope",
    "SourceManifest",
    "SourcePlaneRead",
    "origin_refs",
    "read_source_plane",
    "source_coverage",
    "source_manifest",
    "write_source_manifest",
]

# The hand-off key the curator's external sources travel under, and the bound one entry may carry.
# The bound is declared rather than implied: a manifest is a bounded attributable record, and an
# unbounded one is a second store wearing a file's name.
ENTRY_SOURCES_KEY = "external_sources"
MAX_SOURCES_PER_ENTRY = 32

# The one artifact name this plane owns in the candidate directory, and the schema line its own
# header carries so a reader of the file needs no other document to read it.
SOURCE_MANIFEST_NAME = "curator-source-manifest.json"
SOURCE_MANIFEST_SCHEMA = "curator-source-manifest/v1"

# The two origin-reference prefixes one run records. They are spellings of *references* and not paths:
# the hand-off reference names the list by its own digest, and the manifest reference names the
# manifest file by its own, so a record's origin is resolvable without inventing a Git object.
_HANDOFF_REF_PREFIX = "curator-handoff:v1:"
_MANIFEST_REF_PREFIX = "curator-source-manifest:v1:"


@dataclass(frozen=True)
class EntrySourceRefusal:
    """One entry's external-source plane refused: the code that names why, and the sentence."""

    entry_id: str
    code: str
    reason: str


@dataclass(frozen=True)
class ExternalSource:
    """One external source the curator inspected, with the fields that make it findable again.

    ``version`` and ``retrieved_at`` are the two ways an external document identifies the revision that
    was read, and at least one of them is required. ``content_digest`` is the digest of what the
    curator inspected, and it is ``None`` when no digest was taken -- which the manifest records as
    ``null`` rather than as a verified content identity.
    """

    source_id: str
    document: str
    location: str
    version: str | None = None
    retrieved_at: str | None = None
    content_digest: str | None = None

    def as_record(self) -> dict[str, str | None]:
        """The exact JSON object the manifest stores for this source."""

        return {
            "id": self.source_id,
            "document": self.document,
            "version": self.version,
            "retrievedAt": self.retrieved_at,
            "contentDigest": self.content_digest,
            "location": self.location,
        }


@dataclass(frozen=True)
class EntrySources:
    """One entry's whole external-source declaration.

    ``examined`` is present exactly when the entry carried the key at all, so an entry that declared
    no source and an entry the curator never examined are two different facts all the way to the
    report.
    """

    entry_id: str
    examined: bool
    sources: tuple[ExternalSource, ...] = ()


@dataclass(frozen=True)
class SourcePlaneRead:
    """Every entry's declaration in one list, with the entries whose declaration could not be read."""

    entries: Mapping[str, EntrySources]
    refusals: Mapping[str, EntrySourceRefusal]

    def sources_of(self, entry_id: str) -> tuple[ExternalSource, ...]:
        """The sources one entry declared, empty when it declared none or was not examined."""

        found = self.entries.get(entry_id)
        return () if found is None else found.sources

    def examined(self, entry_id: str) -> bool:
        """Whether the curator examined this entry's external sources at all."""

        found = self.entries.get(entry_id)
        return found is not None and found.examined

    def refusal_of(self, entry_id: str) -> EntrySourceRefusal | None:
        """Why this entry's declaration was refused, or ``None`` when it was readable."""

        return self.refusals.get(entry_id)


def read_source_plane(entries: Sequence[Mapping[str, Any]]) -> SourcePlaneRead:
    """Read every entry's declared external sources, refusing what is not attributable."""

    read: dict[str, EntrySources] = {}
    refusals: dict[str, EntrySourceRefusal] = {}
    for raw in entries:
        entry_id = str(raw.get("id", ""))
        declared = raw.get(ENTRY_SOURCES_KEY)
        if declared is None:
            continue
        sources, refusal = _read_entry_sources(entry_id, declared)
        if refusal is not None:
            refusals[entry_id] = refusal
            continue
        read[entry_id] = EntrySources(entry_id=entry_id, examined=True, sources=sources)
    return SourcePlaneRead(entries=read, refusals=refusals)


def _read_entry_sources(
    entry_id: str, raw: object
) -> tuple[tuple[ExternalSource, ...], EntrySourceRefusal | None]:
    """One entry's declared list: bounded, and every member attributable on its own."""

    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return (), _refusal(
            entry_id, "external_sources_malformed", f"{ENTRY_SOURCES_KEY} must be a JSON list"
        )
    if len(raw) > MAX_SOURCES_PER_ENTRY:
        return (), _refusal(
            entry_id,
            "external_sources_over_bound",
            f"the entry declares {len(raw)} external sources and the bounded manifest holds at most "
            f"{MAX_SOURCES_PER_ENTRY}: an unbounded list is a second store rather than a manifest",
        )
    sources: list[ExternalSource] = []
    seen: set[str] = set()
    for one in raw:
        source, refusal = _read_source(entry_id, one)
        if refusal is not None:
            return (), refusal
        assert source is not None
        if source.source_id in seen:
            return (), _refusal(
                entry_id,
                "external_sources_duplicate_id",
                f"two declared sources carry the id {source.source_id!r}, so a record's origin "
                "reference would name two documents",
            )
        seen.add(source.source_id)
        sources.append(source)
    return tuple(sources), None


def _read_source(
    entry_id: str, raw: object
) -> tuple[ExternalSource | None, EntrySourceRefusal | None]:
    """One declared source: its identity, where it was read, and what was read there."""

    if not isinstance(raw, Mapping):
        return None, _refusal(
            entry_id, "external_source_malformed", "every declared source is a JSON object"
        )
    source_id = _text(raw.get("id"))
    document = _text(raw.get("document"))
    location = _text(raw.get("location"))
    if source_id is None or document is None or location is None:
        return None, _refusal(
            entry_id,
            "external_source_malformed",
            "every declared source names an id, the document it is (a URL or a document identity) and "
            "the relevant location inside it",
        )
    version = _text(raw.get("version"))
    retrieved_at = _text(raw.get("retrieved_at"))
    if version is None and retrieved_at is None:
        return None, _refusal(
            entry_id,
            "external_source_unversioned",
            f"the declared source {source_id!r} names neither a version nor a retrieval time, so the "
            "document revision it was authored from cannot be found again",
        )
    digest = raw.get("content_digest")
    if digest is not None and (
        not isinstance(digest, str) or not re.fullmatch(SHA256_PATTERN, digest.strip())
    ):
        return None, _refusal(
            entry_id,
            "external_source_digest_malformed",
            f"the declared source {source_id!r} carries a content_digest that is not a sha256; an "
            "unreadable digest is refused rather than recorded as if it identified the content",
        )
    return (
        ExternalSource(
            source_id=source_id,
            document=document,
            location=location,
            version=version,
            retrieved_at=retrieved_at,
            content_digest=None if digest is None else digest.strip(),
        ),
        None,
    )


@dataclass(frozen=True)
class SourceManifest:
    """The exact bytes one run records, the digest that names them, and what they declare."""

    payload: bytes
    digest: str
    entries: tuple[EntrySources, ...]

    @property
    def declared(self) -> int:
        """How many sources the manifest records."""

        return sum(len(one.sources) for one in self.entries)

    @property
    def with_content_digest(self) -> int:
        """How many of them carry an inspected-content digest, as a measured count."""

        return sum(
            1 for one in self.entries for source in one.sources if source.content_digest is not None
        )

    @property
    def without_content_digest(self) -> int:
        """How many carry none, so their content identity was recorded as absent."""

        return self.declared - self.with_content_digest


def source_manifest(read: SourcePlaneRead, list_digest: str) -> SourceManifest | None:
    """The manifest this list declares, or ``None`` when it declares no external source at all.

    ``None`` is not an empty manifest: nothing declared means no artifact is written and the run's
    origin references name the hand-off list alone, while a written manifest is a real file with a real
    digest.
    """

    entries = tuple(
        one for one in (read.entries[key] for key in sorted(read.entries)) if one.sources
    )
    if not entries:
        return None
    payload = canonical_json_bytes(
        {
            "schema": SOURCE_MANIFEST_SCHEMA,
            "declaredBy": "curator-hand-off-list",
            "handOffListDigest": list_digest,
            "entries": [
                {
                    "entryId": one.entry_id,
                    "sources": [source.as_record() for source in one.sources],
                }
                for one in entries
            ],
        }
    )
    return SourceManifest(
        payload=payload, digest=hashlib.sha256(payload).hexdigest(), entries=entries
    )


def write_source_manifest(directory: Path, manifest: SourceManifest) -> Path:
    """Write the manifest into the candidate directory and prove it read back as it was written."""

    path = Path(directory) / SOURCE_MANIFEST_NAME
    atomic_write_bytes(path, manifest.payload)
    if path.read_bytes() != manifest.payload:
        raise ValueError(
            f"the source manifest at {path} did not read back as it was written, so the origin "
            "references this run records would name bytes no reader can find"
        )
    return path


def origin_refs(list_digest: str, manifest: SourceManifest | None) -> tuple[str, ...]:
    """The origin references one run records on every row its batch writes.

    There are two at most, and each is a *reference* rather than a path: the hand-off list's own
    digest, and -- when the list declared external sources -- the manifest's digest. A reader resolves
    the second to the manifest file, whose per-entry records are where each declaration's document
    identity, version or retrieval time, content digest and location live. No external source is ever
    turned into a source anchor here, so nothing claims a Git identity for a document the repository
    does not hold.
    """

    refs = [f"{_HANDOFF_REF_PREFIX}{list_digest}"]
    if manifest is not None:
        refs.append(f"{_MANIFEST_REF_PREFIX}{manifest.digest}")
    return tuple(refs)


@dataclass(frozen=True)
class EntrySourceOutcome:
    """One committed entry's external-source coverage, as the run established it."""

    entry_id: str
    examined: bool
    declared: int
    content_digests_recorded: int
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceCoverage:
    """The whole external-source result: the manifest this run recorded, and per-entry coverage.

    ``state`` is one of four and they never merge: ``recorded`` means the manifest was written and the
    digest below names its exact bytes; ``projected`` means a planning run would write it and wrote
    nothing, so the digest is the one it would name; ``not-recorded`` means no manifest exists, with
    ``detail`` naming why and no path and no digest claimed for it; and every committed entry's own
    ``examined``/``declared`` pair keeps "declared none" and "not examined" apart. ``declared`` and
    ``content_digests_recorded`` count what the LIST declared, which is a fact about the list in every
    state; ``state`` is what says whether those declarations were retained. ``unexamined`` names the
    committed entries whose external sources this run did not examine.
    """

    state: str
    detail: str
    path: str | None = None
    digest: str | None = None
    refs: tuple[str, ...] = ()
    declared: int = 0
    content_digests_recorded: int = 0
    entries: tuple[EntrySourceOutcome, ...] = ()
    unexamined: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceCoverageScope:
    """One run's own outcome, as the source coverage needs it: what it recorded, and over what.

    Grouped rather than passed as six trailing arguments because they are one fact about one run:
    ``state``/``detail`` say what this run established about the manifest, ``refs`` are the origin
    references it stamped, and ``placed``/``path``/``unresolved`` are the entries and the file the
    outcome is about.
    """

    state: str
    detail: str
    refs: tuple[str, ...]
    placed: tuple[str, ...]
    path: Path | None = None
    unresolved: tuple[str, ...] = ()


def source_coverage(
    read: SourcePlaneRead, manifest: SourceManifest | None, scope: SourceCoverageScope
) -> SourceCoverage:
    """Assemble the source coverage one report carries, from what the run read and recorded."""

    outcomes = tuple(
        EntrySourceOutcome(
            entry_id=entry,
            examined=read.examined(entry),
            declared=len(read.sources_of(entry)),
            content_digests_recorded=sum(
                1 for one in read.sources_of(entry) if one.content_digest is not None
            ),
            source_ids=tuple(one.source_id for one in read.sources_of(entry)),
        )
        for entry in scope.placed
    )
    retained = scope.state == "recorded"
    return SourceCoverage(
        state=scope.state,
        detail=scope.detail,
        path=None if scope.path is None else str(scope.path),
        digest=(
            None
            if manifest is None or not (retained or scope.state == "projected")
            else manifest.digest
        ),
        refs=tuple(scope.refs),
        declared=0 if manifest is None else manifest.declared,
        content_digests_recorded=0 if manifest is None else manifest.with_content_digest,
        entries=outcomes,
        unexamined=tuple(entry for entry in scope.placed if not read.examined(entry)),
        unresolved=tuple(scope.unresolved),
    )


def _text(raw: object) -> str | None:
    """One declared field as non-blank stripped text, or ``None`` when it is absent or blank."""

    if raw is None or not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    return cleaned or None


def _refusal(entry_id: str, code: str, reason: str) -> EntrySourceRefusal:
    return EntrySourceRefusal(entry_id=entry_id, code=code, reason=reason[:REFERENCE_MAX_LENGTH])
