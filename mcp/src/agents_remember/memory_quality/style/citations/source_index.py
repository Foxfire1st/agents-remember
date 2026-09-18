"""Persistent source acquisition for repository-wide citation anchor lookup.

One immutable SQLite index represents one exact code-tree content snapshot. The snapshot
identity hashes every indexed relative path and file body, including dirty, added, deleted,
and renamed files. Default acquisition enumerates the code root's **Git-reported population**
(tracked, plus untracked-but-not-ignored; a gitignored scratch tree is not the candidate) and
falls back to a plain walk when the root is not a work tree; explicit candidate acquisition selects one exact Git tree and
verifies its working bytes. Independent CLI processes share the published index through a
deterministic bounded cache outside both worktrees.

Default warm acquisition enumerates and stats the indexed files and directories. A changed
file stat re-hashes only that file: unchanged content refreshes the metadata manifest
without re-tokenizing, while changed content rebuilds once under the publisher lock. An
explicit expected snapshot instead opens exactly the prevalidated frozen generation from
fixed-size database metadata, without inspecting the source tree or parsing its file manifest.
That fast path is an operator assertion that the source wave stayed frozen; it deliberately
does not pretend Git HEAD detects dirty or untracked edits. Readers hold a shared ``flock``;
one publisher holds it exclusively and atomically replaces the database and manifest.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import BinaryIO
from uuid import uuid4

from agents_remember.kernel.atomic_write import atomic_replace, atomic_write_text
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory_quality.style.citations import (
    extents,
    model,
    source_index_cache,
    source_index_database,
    source_index_state,
)
from agents_remember.memory_quality.style.citations.exclusion_register import (
    FallbackIgnoreMatcher,
    excluding_rule,
    fallback_matcher,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.memory_quality.style.citations.source_index_state import (
    EXCLUDED_SAMPLE_LIMIT,
    GITIGNORE_AUTHORITY_ABSENT,
    GITIGNORE_AUTHORITY_GIT,
    GITIGNORE_AUTHORITY_REGISTER,
    STATUS_NOT_EVALUATED,
    ExclusionRegister,
    SourceBoundsReport,
    SourceSkip,
    apply_source_bounds,
)

SCHEMA_VERSION = source_index_state.SCHEMA_VERSION
CACHE_SLOT_COUNT = 4
MAX_SOURCE_BYTES = source_index_state.MAX_SOURCE_BYTES
MAX_SOURCE_FILES = source_index_state.MAX_SOURCE_FILES
MAX_SOURCE_FILE_BYTES = source_index_state.MAX_SOURCE_FILE_BYTES
MAX_INDEX_BYTES = source_index_state.MAX_DATABASE_BYTES
BUILD_ATTEMPTS = 2
RECLAMATION_LOCK_TIMEOUT_SECONDS = 30.0
LEGACY_CACHE_NAME = re.compile(r"citation-source-index-v[0-9]+")

SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
        "venv",
    }
)
SKIPPED_SUFFIXES = frozenset(
    {
        ".avif",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".mp4",
        ".pdf",
        ".png",
        ".pyc",
        ".so",
        ".ttf",
        ".webm",
        ".webp",
        ".whl",
        ".woff",
        ".woff2",
        ".zip",
    }
)

_UNREADABLE = "unreadable"


SourceIndexError = source_index_state.SourceIndexError


class SourceTreeChangedError(SourceIndexError):
    """The code tree changed while an index generation was being built."""


Identity = source_index_state.Identity
SourceFile = source_index_state.SourceFile
TreeState = source_index_state.TreeState
Manifest = source_index_state.Manifest
ReadyGeneration = source_index_state.ReadyGeneration


@dataclass
class IndexMetrics:
    """Observable acquisition work for cold, warm, and metadata-refresh paths."""

    state: str = "warm"
    source_files_read: int = 0
    source_bytes_read: int = 0
    source_files_tokenized: int = 0
    source_files_parsed: int = 0
    metadata_files_stat: int = 0
    metadata_directories_stat: int = 0
    metadata_entries_enumerated: int = 0
    metadata_tree_enumerations: int = 0
    metadata_seconds: float = 0.0
    build_seconds: float = 0.0
    index_queries: int = 0


IndexedFile = source_index_database.IndexedFile


@dataclass(frozen=True)
class CachePaths:
    root: Path
    slot: Path
    database: Path
    manifest: Path
    readiness: Path
    lock: Path
    managed: bool = False
    namespace_id: str | None = None


@dataclass(frozen=True)
class PublicationBoundary:
    trees: Trees
    state: TreeState
    metrics: IndexMetrics


Validation = source_index_state.Validation


@dataclass
class RepositoryIndex:
    """A shared-lock lease on one complete immutable source snapshot."""

    database: source_index_database.Database
    lock_handle: BinaryIO
    paths: CachePaths
    snapshot_id: str
    files_indexed: int
    source_bytes: int
    metrics: IndexMetrics
    exclusions: ExclusionRegister = field(default_factory=ExclusionRegister)
    bounds: SourceBoundsReport = field(default_factory=SourceBoundsReport)
    excluded_files: int = 0
    excluded_sample: tuple[str, ...] = ()
    _closed: bool = False
    _candidate_tree: str | None = None

    @property
    def candidate_tree(self) -> str | None:
        """The exact acquisition selection proven by this leased generation's metadata."""
        return self._candidate_tree

    def __enter__(self) -> RepositoryIndex:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self.database.close()
        fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_UN)
        self.lock_handle.close()
        self._closed = True

    def locations(
        self, anchors: tuple[model.Anchor, ...]
    ) -> dict[model.Anchor, tuple[IndexedFile, ...]]:
        """All indexed locations for ``anchors`` with deterministic file/range order."""
        self.metrics.index_queries += 1
        return self.database.locations(anchors)

    def telemetry(self, *, post_fix_recheck: bool = False) -> dict[str, object]:
        return {
            "path": self.paths.database.as_posix(),
            "cacheManaged": self.paths.managed,
            "cacheNamespace": self.paths.namespace_id,
            "snapshotId": self.snapshot_id,
            "state": self.metrics.state,
            "filesIndexed": self.files_indexed,
            "sourceBytesIndexed": self.source_bytes,
            "indexBytes": _cache_bytes(self.paths),
            # The register and the caps report travel with every citation result: a reader can see
            # which rules produced the population and which files the caps skipped without
            # re-deriving either from the checkout.
            "exclusions": self.exclusions.to_dict(),
            "bounds": self.bounds.to_dict(),
            # The register's own effect: the exact count and a bounded offender sample, so a run
            # can show WHICH files the register removed instead of only that it removed some.
            "excludedFiles": self.excluded_files,
            "excludedSample": list(self.excluded_sample),
            "sourceFilesRead": self.metrics.source_files_read,
            "sourceBytesRead": self.metrics.source_bytes_read,
            "sourceFilesTokenized": self.metrics.source_files_tokenized,
            "sourceFilesParsed": self.metrics.source_files_parsed,
            "metadataFilesStat": self.metrics.metadata_files_stat,
            "metadataDirectoriesStat": self.metrics.metadata_directories_stat,
            "metadataEntriesEnumerated": self.metrics.metadata_entries_enumerated,
            "metadataTreeEnumerations": self.metrics.metadata_tree_enumerations,
            "metadataValidationSeconds": round(self.metrics.metadata_seconds, 6),
            "buildSeconds": round(self.metrics.build_seconds, 6),
            "indexQueries": self.metrics.index_queries,
            "directAnchorQueries": self.database.direct_anchor_queries,
            "quoteAnchorQueries": self.database.quote_anchor_queries,
            "quoteIndexLookups": self.database.quote_index_lookups,
            "quoteShortGramLookups": self.database.quote_short_gram_lookups,
            "quoteCandidateStreamsRead": self.database.quote_candidate_streams_read,
            "quoteCandidateTextBytesRead": self.database.quote_candidate_text_bytes_read,
            "quoteCorpusStreams": self.database.quote_stream_count,
            "quoteCorpusTextBytes": self.database.quote_text_bytes,
            "quoteFullCorpusScans": 0,
            "postFixRecheck": {
                "reusedLease": post_fix_recheck,
                "sourceFilesRead": 0,
                "sourceFilesTokenized": 0,
                "sourceFilesParsed": 0,
            },
        }


def cache_root() -> Path:
    """One fixed-slot cache root shared by every application schema generation."""
    configured = os.environ.get("XDG_CACHE_HOME")
    base = (
        Path(configured) if configured else Path(tempfile.gettempdir()) / f"ar-cache-{os.getuid()}"
    )
    return base / "agents-remember" / "citation-source-index"


def cache_paths(trees: Trees) -> CachePaths:
    code = trees.code_root.resolve()
    memory = trees.memory_root.resolve()
    authority = trees.cache_authority
    if authority is not None:
        authority.validate_roots(code, memory)
        root = authority.managed_root.resolve()
        slot = authority.namespace.resolve()
        return CachePaths(
            root=root,
            slot=slot,
            database=slot / "index.sqlite3",
            manifest=slot / "manifest.json",
            readiness=slot / "ready.json",
            lock=slot / "index.lock",
            managed=True,
            namespace_id=authority.namespace_id,
        )
    root = cache_root().resolve()
    if _under(root, code) or _under(root, memory):
        raise SourceIndexError(
            f"citation source-index cache {root} must stay outside code root {code} and "
            f"memory root {memory}"
        )
    identity = hashlib.sha256(f"{code}\0{memory}".encode()).hexdigest()
    slot_number = int(identity[:8], 16) % CACHE_SLOT_COUNT
    slot = root / f"slot-{slot_number}"
    return CachePaths(
        root=root,
        slot=slot,
        database=slot / "index.sqlite3",
        manifest=slot / "manifest.json",
        readiness=slot / "ready.json",
        lock=slot / "index.lock",
    )


def validate_expected_snapshot(expected_snapshot: str) -> None:
    """Reject every spelling except the canonical lowercase SHA-256 generation id."""
    if not source_index_state.canonical_hash(expected_snapshot):
        raise SourceIndexError(
            "expected citation source-index snapshot must be exactly 64 lowercase hex digits"
        )


def validate_operation_scope(
    document: str | None,
    expected_snapshot: str | None,
    *,
    leased_index: bool,
) -> None:
    """Validate document/generation authority before an operation performs any work.

    An acquisition-capable operation accepts either the dirty-safe tree-wide default or one
    exact document paired with one canonical frozen snapshot. A caller that already holds a
    repository-index lease may select one document for a postcheck, but cannot also provide an
    acquisition identity: the live lease is the sole source-generation authority in that arm.
    """
    if leased_index:
        if expected_snapshot is not None:
            raise SourceIndexError(
                "an already-open citation source-index lease cannot be combined with an "
                "expected snapshot"
            )
        return
    if (document is None) != (expected_snapshot is None):
        raise SourceIndexError(
            "citation operation scope requires --document and --expected-snapshot together, "
            "or neither"
        )
    if expected_snapshot is not None:
        validate_expected_snapshot(expected_snapshot)


def open_repository_index(
    trees: Trees,
    *,
    verify_integrity: bool = False,
    expected_snapshot: str | None = None,
) -> RepositoryIndex:
    """Open a source index through the default safe path or an explicit frozen lease.

    ``expected_snapshot`` is the build-once/query-many contract. It opens only that exact
    generation and never scans, rebuilds, or falls back. The caller is asserting that source
    has remained frozen since the explicit integrity-checked build; if source changes, the
    operator must refreeze and build once to obtain a new snapshot id.
    """
    if expected_snapshot is not None:
        if verify_integrity:
            raise SourceIndexError(
                "an expected frozen snapshot cannot request per-command integrity traversal; "
                "run the explicit source-index build once before the frozen wave"
            )
        return _open_expected_generation(trees, expected_snapshot)
    paths = cache_paths(trees)
    if not paths.managed:
        _reclaim_legacy_cache_roots(paths.root)
    handle = _open_shared_lock(paths, trees, create=True)
    metrics = IndexMetrics()
    try:
        current = _current_generation(
            paths,
            trees,
            metrics,
            check_content=False,
            verify_integrity=verify_integrity,
        )
        if current is not None and not current[1].metadata_changed:
            connection, validation, manifest = current
            return _repository_index(
                LeasedGeneration(connection, handle, paths, manifest, metrics), validation.state
            )
        if current is not None:
            current[0].close()
        source_index_cache.lock_exclusive(trees.cache_authority, handle)
        _reclaim_temps(paths)
        current = _current_generation(
            paths,
            trees,
            metrics,
            check_content=True,
            verify_integrity=verify_integrity,
        )
        if current is None:
            manifest = _build_and_publish(paths, trees, metrics)
            connection = _open_database(
                paths.database,
                _ready_generation(paths, trees),
                # The temporary database was fully checked before the readiness marker
                # made this exact immutable generation visible.
                verify_integrity=False,
            )
        else:
            connection, validation, manifest = current
            if validation.metadata_changed:
                manifest = _refreshed_manifest(manifest, validation.state)
                atomic_write_text(paths.manifest, manifest.to_json())
                metrics.state = "metadata-refreshed"
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        return _repository_index(
            LeasedGeneration(connection, handle, paths, manifest, metrics), None
        )
    except BaseException:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        raise


def build_repository_index(trees: Trees) -> dict[str, object]:
    """Explicit frozen-wave prebuild; a current generation is reused rather than rebuilt."""
    with open_repository_index(trees, verify_integrity=True) as index:
        return {
            "ok": True,
            "operation": "citation_source_index_build",
            "sourceIndex": index.telemetry(),
        }


def _open_expected_generation(trees: Trees, expected_snapshot: str) -> RepositoryIndex:
    """Lease one prevalidated generation without source-tree or full-manifest work."""
    validate_expected_snapshot(expected_snapshot)
    paths = cache_paths(trees)
    try:
        handle = _open_shared_lock(paths, trees, create=False)
    except (OSError, source_index_cache.CitationCacheError) as error:
        raise SourceIndexError(
            f"expected citation source-index snapshot {expected_snapshot} is not published"
        ) from error
    metrics = IndexMetrics(state="frozen")
    try:
        readiness = _ready_generation(paths, trees)
        if readiness.snapshot_id != expected_snapshot:
            raise SourceIndexError(
                f"expected citation source-index snapshot {expected_snapshot} is unavailable"
            )
        connection = _open_database(
            paths.database,
            readiness,
            verify_integrity=False,
        )
        return RepositoryIndex(
            database=connection,
            lock_handle=handle,
            paths=paths,
            snapshot_id=expected_snapshot,
            files_indexed=connection.files_indexed,
            source_bytes=connection.source_bytes,
            metrics=metrics,
            # A frozen lease reads a published generation without inspecting the source tree, so
            # it reports the caps in force and says plainly that it did not evaluate skips. It
            # never reports a default that a reader would take for "nothing was skipped".
            exclusions=trees.exclusions,
            bounds=SourceBoundsReport(
                caps=trees.caps,
                candidate_files=connection.files_indexed,
                indexed_files=connection.files_indexed,
                indexed_bytes=connection.source_bytes,
                status=STATUS_NOT_EVALUATED,
            ),
            _candidate_tree=readiness.candidate_tree,
        )
    except BaseException as error:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        if isinstance(error, SourceIndexError):
            raise
        raise SourceIndexError(
            f"expected citation source-index snapshot {expected_snapshot} is unavailable: {error}"
        ) from error


@dataclass(frozen=True)
class LeasedGeneration:
    """The four values one lease is opened from, kept together so a lease is one argument."""

    connection: source_index_database.Database
    handle: BinaryIO
    paths: CachePaths
    manifest: Manifest
    metrics: IndexMetrics


def _repository_index(lease: LeasedGeneration, state: TreeState | None) -> RepositoryIndex:
    """Lease one generation, reporting the register it was built under and its skip report.

    ``state`` is the just-measured tree record when the acquisition walked the tree; it carries
    the current skip list. The built and metadata-refreshed arms take theirs from the lease's
    manifest, which is the record the generation was published with.
    """
    return RepositoryIndex(
        database=lease.connection,
        lock_handle=lease.handle,
        paths=lease.paths,
        snapshot_id=lease.manifest.snapshot_id,
        files_indexed=len(lease.manifest.files),
        source_bytes=lease.manifest.source_bytes,
        metrics=lease.metrics,
        exclusions=lease.manifest.exclusions,
        bounds=lease.manifest.bounds if state is None else state.bounds,
        excluded_files=lease.manifest.excluded_files if state is None else state.excluded_files,
        excluded_sample=lease.manifest.excluded_sample if state is None else state.excluded_sample,
        _candidate_tree=lease.manifest.candidate_tree,
    )


def _open_shared_lock(paths: CachePaths, trees: Trees, *, create: bool) -> BinaryIO:
    try:
        return source_index_cache.open_index_lock(
            trees.cache_authority, paths.slot, paths.lock, create=create
        )
    except source_index_cache.CitationCacheError as error:
        raise SourceIndexError(str(error)) from error


def code_files(trees: Trees) -> list[tuple[Path, str]]:
    """The source files the citation resolver considers, in current candidate order."""
    return [(one.absolute, one.identity.path) for one in _tree_state(trees).files]


def _current_generation(
    paths: CachePaths,
    trees: Trees,
    metrics: IndexMetrics,
    *,
    check_content: bool,
    verify_integrity: bool,
) -> tuple[source_index_database.Database, Validation, Manifest] | None:
    try:
        manifest = Manifest.from_json(paths.manifest)
        readiness = _ready_generation(paths, trees)
        if (
            readiness.snapshot_id != manifest.snapshot_id
            or readiness.files_indexed != len(manifest.files)
            or readiness.source_bytes != manifest.source_bytes
            or readiness.candidate_tree != manifest.candidate_tree
        ):
            return None
        validation = _validate(manifest, trees, metrics, check_content=check_content)
        if validation.stale:
            return None
        connection = _open_database(
            paths.database,
            readiness,
            verify_integrity=verify_integrity,
        )
        return connection, validation, manifest
    except (OSError, KeyError, TypeError, ValueError, SourceIndexError):
        return None


def _open_database(
    path: Path,
    readiness: ReadyGeneration,
    *,
    verify_integrity: bool,
) -> source_index_database.Database:
    return source_index_database.Database.open(
        path,
        readiness=readiness,
        verify_integrity=verify_integrity,
    )


def _ready_generation(paths: CachePaths, trees: Trees) -> ReadyGeneration:
    readiness = ReadyGeneration.from_json(paths.readiness)
    if (
        readiness.code_root != trees.code_root.resolve().as_posix()
        or readiness.memory_root != trees.memory_root.resolve().as_posix()
        or readiness.candidate_tree != trees.candidate_tree
    ):
        raise SourceIndexError(
            "citation source-index readiness belongs to different roots or candidate"
        )
    return readiness


def _validate(
    manifest: Manifest,
    trees: Trees,
    metrics: IndexMetrics,
    *,
    check_content: bool,
) -> Validation:
    started = time.perf_counter()
    current = _tree_state(trees, metrics)
    metrics.metadata_seconds += time.perf_counter() - started
    if (
        manifest.code_root != trees.code_root.resolve().as_posix()
        or manifest.memory_root != trees.memory_root.resolve().as_posix()
        or manifest.candidate_tree != trees.candidate_tree
    ):
        return Validation(current, stale=True, metadata_changed=False)
    previous_files = {one.identity.path: one for one in manifest.files}
    current_files = {one.identity.path: one for one in current.files}
    if previous_files.keys() != current_files.keys():
        return Validation(current, stale=True, metadata_changed=False)
    changed = [
        current_files[path]
        for path, old in previous_files.items()
        if old.identity != current_files[path].identity
    ]
    if changed and not check_content:
        return Validation(current, stale=False, metadata_changed=True)
    for one in changed:
        raw = _stable_read(one.absolute, one.identity)
        metrics.source_files_read += raw is not None
        metrics.source_bytes_read += 0 if raw is None else len(raw)
        digest = _UNREADABLE if raw is None else hashlib.sha256(raw).hexdigest()
        if digest != previous_files[one.identity.path].content_sha256:
            return Validation(current, stale=True, metadata_changed=False)
    previous_dirs = {one.path: one for one in manifest.directories}
    current_dirs = {one.path: one for one in current.directories}
    metadata_changed = bool(changed) or previous_dirs != current_dirs
    return Validation(current, stale=False, metadata_changed=metadata_changed)


def _build_and_publish(paths: CachePaths, trees: Trees, metrics: IndexMetrics) -> Manifest:
    last_error: SourceTreeChangedError | None = None
    for _attempt in range(BUILD_ATTEMPTS):
        try:
            return _build_once(paths, trees, metrics)
        except SourceTreeChangedError as error:
            last_error = error
    assert last_error is not None
    raise last_error


def _build_once(paths: CachePaths, trees: Trees, metrics: IndexMetrics) -> Manifest:
    started = time.perf_counter()
    initial = _tree_state(trees, metrics)
    temp = paths.slot / f".index.sqlite3.{os.getpid()}.{uuid4().hex}.tmp"
    hashes: dict[str, str] = {}
    unreadable: list[SourceSkip] = []
    generation_id = hashlib.sha256(uuid4().bytes).hexdigest()
    try:
        database = source_index_database.Database.create(temp)
        try:
            for one in initial.files:
                raw = _stable_read(one.absolute, one.identity)
                if raw is None:
                    # An unreadable source is REPORTED with its path, never silently absent: a
                    # citation that resolves to nothing because the file could not be opened is
                    # exactly the defect the report exists to make visible.
                    unreadable.append(
                        SourceSkip(
                            path=one.identity.path,
                            size=one.identity.size,
                            reason=source_index_state.SKIP_UNREADABLE,
                        )
                    )
                    continue
                hashes[one.identity.path] = hashlib.sha256(raw).hexdigest()
                metrics.source_files_read += 1
                metrics.source_bytes_read += len(raw)
                lines = raw.decode("utf-8", errors="replace").splitlines()
                database.insert_file(one.identity.path, lines)
                metrics.source_files_tokenized += 1
                metrics.source_files_parsed += extents.parsed(one.identity.path)
                if database.bytes_used() > MAX_INDEX_BYTES:
                    raise SourceIndexError(
                        f"citation source index exceeded {MAX_INDEX_BYTES} bytes while building"
                    )
            bounds = initial.bounds.with_skips(unreadable)
            indexed = [one for one in initial.files if one.identity.path in hashes]
            snapshot_id = _snapshot_id(hashes)
            readiness = ReadyGeneration(
                generation_id=generation_id,
                snapshot_id=snapshot_id,
                code_root=trees.code_root.resolve().as_posix(),
                memory_root=trees.memory_root.resolve().as_posix(),
                files_indexed=len(indexed),
                source_bytes=sum(one.identity.size for one in indexed),
                # Replaced with the closed file's authoritative size before validation.
                database_bytes=1,
                candidate_tree=trees.candidate_tree,
            )
            database.write_snapshot(readiness)
        finally:
            database.close()
        readiness = replace(readiness, database_bytes=temp.stat().st_size)
        _validate_temporary_database(temp, readiness)
        final = _tree_state(trees, metrics)
        if _identities(initial) != _identities(final):
            raise SourceTreeChangedError(
                "code source changed while the citation index was being built; freeze the "
                "source snapshot and build again"
            )
        files = tuple(
            SourceFile(
                absolute=one.absolute,
                identity=one.identity,
                content_sha256=hashes[one.identity.path],
            )
            for one in final.files
            if one.identity.path in hashes
        )
        manifest = Manifest(
            code_root=trees.code_root.resolve().as_posix(),
            memory_root=trees.memory_root.resolve().as_posix(),
            snapshot_id=snapshot_id,
            source_bytes=sum(one.identity.size for one in files),
            directories=final.directories,
            files=files,
            candidate_tree=trees.candidate_tree,
            exclusions=initial.exclusions,
            bounds=bounds,
            excluded_files=initial.excluded_files,
            excluded_sample=initial.excluded_sample,
        )
        _publish_generation(
            paths,
            temp,
            manifest,
            readiness,
            PublicationBoundary(trees=trees, state=final, metrics=metrics),
        )
        metrics.state = "built"
        metrics.build_seconds += time.perf_counter() - started
        return manifest
    finally:
        temp.unlink(missing_ok=True)


def _validate_temporary_database(path: Path, readiness: ReadyGeneration) -> None:
    """Run the expensive SQLite and application checks before publication is possible."""
    try:
        checked = source_index_database.Database.open(
            path,
            readiness=readiness,
            verify_integrity=True,
        )
        checked.close()
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise SourceIndexError(
            f"citation source-index temporary generation failed validation: {error}"
        ) from error


def _publish_generation(
    paths: CachePaths,
    database: Path,
    manifest: Manifest,
    readiness: ReadyGeneration,
    boundary: PublicationBoundary,
) -> None:
    """Publish SQLite and manifest while the absent/last readiness marker is authority."""
    readiness_json = readiness.to_json()
    if (
        database.stat().st_size + len(manifest.to_json().encode()) + len(readiness_json.encode())
        > MAX_INDEX_BYTES
    ):
        raise SourceIndexError(f"citation source index exceeds its {MAX_INDEX_BYTES}-byte cap")
    # Removing readiness before SQLite replacement makes every crash boundary either the
    # old complete generation or an explicit refusal; no mixed pair can be leased as ready.
    paths.readiness.unlink(missing_ok=True)
    atomic_replace(database, paths.database)
    atomic_write_text(paths.manifest, manifest.to_json())
    published = _tree_state(boundary.trees, boundary.metrics)
    if _identities(boundary.state) != _identities(published):
        raise SourceTreeChangedError(
            "code source changed at the citation index publication boundary; freeze the "
            "source snapshot and build again"
        )
    atomic_write_text(paths.readiness, readiness_json)


def _git_candidate_paths(root: Path) -> frozenset[str] | None:
    """The paths Git reports as ``root``'s own, or ``None`` when ``root`` is not a work tree.

    D18: default acquisition used to index every file its skip lists did not name, so a
    machine-local, gitignored scratch tree (this experiment's ``eve_runtime/.eve`` dev hosts) counted
    against the index's per-file and aggregate caps and made the mandatory memory-quality checks
    *error* on a checkout whose tracked content is inside both. The population is therefore asked of
    Git -- ``ls-files --cached --others --exclude-standard``: everything Git reports as this work
    tree's own, which is the tracked population plus untracked-but-not-ignored paths -- instead of
    being every file the skip lists happen not to name. It is deliberately **not** "tracked only":
    the walk exists to hash dirty, added, deleted, and renamed working-tree bytes (see the module
    docstring), and a curator cites code a leaf has written but not yet committed, which is the state
    every citation-repair fixture in this suite builds. ``--exclude-standard`` is what removes the
    ignored scratch tree, and it is the only thing removed here that the old walk kept.

    Git-*assisted*, not Git-required: a root outside a work tree, a machine without ``git``, or a
    refused or timed-out command returns ``None`` and the caller keeps the documented walk, because
    the index must still work on a plain directory. ``git ls-files`` is run from ``root`` so the
    paths it prints are the same root-relative spellings the walk produces.
    """

    try:
        completed = run_git(
            root,
            ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return frozenset(entry for entry in completed.stdout.split("\0") if entry)


def _candidate_directories(candidates: frozenset[str] | None) -> frozenset[str]:
    """Every directory on a candidate's path, plus the root, spelled as the walk spells them.

    A directory with no candidate beneath it belongs to no candidate, and pruning it keeps an ignored
    build tree's churning directory identities out of the snapshot as well as out of the caps.
    """

    if candidates is None:
        return frozenset()
    directories = {"."}
    for candidate in candidates:
        parent = PurePosixPath(candidate).parent
        while parent.as_posix() not in {"", "."}:
            directories.add(parent.as_posix())
            parent = parent.parent
    return frozenset(directories)


def _indexed_file(
    path: Path,
    root: Path,
    candidates: frozenset[str] | None,
) -> SourceFile | None:
    """One walked file as an indexed source, or ``None`` when it is not part of the candidate."""

    if (
        path.name in {".git", ".hg", ".svn"}
        or path.suffix.lower() in SKIPPED_SUFFIXES
        or not path.is_file()
    ):
        return None
    relative = path.relative_to(root).as_posix()
    if candidates is not None and relative not in candidates:
        return None
    return SourceFile(path, Identity.read(path, relative))


@dataclass(frozen=True)
class WalkScope:
    """Everything one walk decides a directory or file against, resolved once per acquisition."""

    root: Path
    memory: Path
    candidates: frozenset[str] | None
    candidate_directories: frozenset[str]
    register: ExclusionRegister
    fallback: FallbackIgnoreMatcher | None

    def excluded_directory(self, relative: str) -> bool:
        """Whether the register (or, outside Git, the ignore file) removes a whole directory."""
        if relative == ".":
            return False
        if excluding_rule(self.register, relative, is_directory=True) is not None:
            return True
        if self.fallback is None or self.fallback.has_negation:
            return False
        return self.fallback.excludes(relative, is_directory=True)

    def excluded_file(self, relative: str) -> bool:
        if excluding_rule(self.register, relative) is not None:
            return True
        return self.fallback is not None and self.fallback.excludes(relative, is_directory=False)


def _walkable_directory(directory: Path, scope: WalkScope) -> bool:
    """Whether the walk descends into ``directory``: not the memory root, and part of the candidate.

    D18: a directory with no candidate beneath it is not part of the candidate, and pruning it here
    also keeps an ignored build tree's churning directory identities out of the snapshot, so a
    machine-local scratch tree can neither refuse the caps nor invalidate a published generation.

    B1: the shared exclusion register prunes here too, by the same rule that decides a file --
    a directory named by ``pathRules.exclude`` (or by the caller) is outside the candidate
    population entirely. A ``.gitignore`` rule prunes only when this register is the authority
    for it (a root that is not a work tree, where Git did not already apply it) and only when
    the ignore file carries no negation rule; with a negation present the walk descends and
    decides per file, because pruning a directory a later ``!`` rule re-includes is exactly the
    silent omission this register must not make.
    """
    if _under(directory, scope.memory):
        return False
    relative = directory.relative_to(scope.root).as_posix()
    if scope.excluded_directory(relative):
        return False
    if scope.candidates is None:
        return True
    return relative in scope.candidate_directories


def _walked_files(
    directory: Path,
    names: list[str],
    scope: WalkScope,
) -> tuple[list[SourceFile], list[str]]:
    """The candidate files under one directory, and the paths the register removed from it."""
    files: list[SourceFile] = []
    excluded: list[str] = []
    for name in sorted(names):
        one = _indexed_file(directory / name, scope.root, scope.candidates)
        if one is None:
            continue
        if scope.excluded_file(one.identity.path):
            excluded.append(one.identity.path)
            continue
        files.append(one)
    return files, excluded


def _gitignore_authority(register: ExclusionRegister, *, git_aware: bool) -> str:
    """How this acquisition's ``.gitignore`` was honoured, stated rather than assumed.

    ``git_aware`` is the property that decides it, and **both acquisition routes ask the same
    question**: the default walk is Git-aware when it has a candidate population, and the
    explicit candidate route is Git-aware by construction, because the tree it selected came
    from Git membership in the first place. Deriving the authority from the route rather than
    from a second ``ls-files`` call is what makes one root give one answer.

    In a Git-aware acquisition Git is the authority: ``ls-files --exclude-standard`` produced the
    population and already removed every ignored path, so the register records the patterns
    without re-applying them. Otherwise the register's own bounded matcher applies them. With no
    ``.gitignore`` at all there is nothing to honour either way.
    """
    if not register.gitignore_patterns:
        return GITIGNORE_AUTHORITY_ABSENT
    return GITIGNORE_AUTHORITY_GIT if git_aware else GITIGNORE_AUTHORITY_REGISTER


def _tree_state(trees: Trees, metrics: IndexMetrics | None = None) -> TreeState:
    root = trees.code_root.resolve()
    memory = trees.memory_root.resolve()
    if trees.source_candidate is not None:
        state = trees.source_candidate.state(
            memory,
            SKIPPED_SUFFIXES,
            trees.caps,
            replace(
                trees.exclusions,
                gitignore_authority=_gitignore_authority(trees.exclusions, git_aware=True),
            ),
        )
        if metrics is not None:
            metrics.metadata_tree_enumerations += 1
            metrics.metadata_directories_stat += len(state.directories)
            metrics.metadata_files_stat += len(state.files)
            metrics.metadata_entries_enumerated += len(state.directories) + len(state.files)
        return state
    directories: list[Identity] = []
    kept: list[SourceFile] = []
    if metrics is not None:
        metrics.metadata_tree_enumerations += 1
    candidates = _git_candidate_paths(root)
    register = replace(
        trees.exclusions,
        gitignore_authority=_gitignore_authority(
            trees.exclusions, git_aware=candidates is not None
        ),
    )
    scope = WalkScope(
        root=root,
        memory=memory,
        candidates=candidates,
        candidate_directories=_candidate_directories(candidates),
        register=register,
        fallback=(
            fallback_matcher(register)
            if register.gitignore_authority == GITIGNORE_AUTHORITY_REGISTER
            else None
        ),
    )
    excluded: list[str] = []
    for current, raw_dirs, raw_files in os.walk(root, followlinks=False):
        directory = Path(current)
        if not _walkable_directory(directory, scope):
            # D18: a gitignored scratch tree is not part of the candidate, and walking into it makes
            # the mandatory memory-quality check refuse a tree whose own content is inside every cap.
            # Pruning here also keeps its directory identities out of the snapshot, so a build tree
            # that churns cannot invalidate a published generation.
            raw_dirs[:] = []
            continue
        relative_dir = directory.relative_to(root).as_posix()
        directories.append(Identity.read(directory, relative_dir or "."))
        if metrics is not None:
            metrics.metadata_directories_stat += 1
            metrics.metadata_entries_enumerated += len(raw_dirs) + len(raw_files)
        raw_dirs[:] = [
            name
            for name in sorted(raw_dirs)
            if name not in SKIPPED_DIRECTORIES and not _under(directory / name, memory)
        ]
        admitted, removed = _walked_files(directory, raw_files, scope)
        kept.extend(admitted)
        excluded.extend(removed)
        if metrics is not None:
            metrics.metadata_files_stat += len(admitted)
    bounded, bounds = apply_source_bounds([one.identity for one in kept], register.caps)
    allowed = {one.path for one in bounded}
    files = [one for one in kept if one.identity.path in allowed]
    return TreeState(
        directories=tuple(sorted(directories, key=lambda one: one.path)),
        files=tuple(sorted(files, key=lambda one: one.identity.path)),
        exclusions=register,
        bounds=bounds,
        excluded_files=len(excluded),
        excluded_sample=tuple(sorted(excluded)[:EXCLUDED_SAMPLE_LIMIT]),
    )


def _stable_read(path: Path, expected: Identity) -> bytes | None:
    before = Identity.read(path, expected.path)
    if before != expected:
        raise SourceTreeChangedError(f"source changed before it could be read: {expected.path}")
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    after = Identity.read(path, expected.path)
    if after != before:
        raise SourceTreeChangedError(f"source changed while it was read: {expected.path}")
    return raw


def _snapshot_id(hashes: dict[str, str]) -> str:
    digest = hashlib.sha256(f"citation-source-index-v{SCHEMA_VERSION}\0".encode())
    for path, content_hash in sorted(hashes.items()):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(content_hash.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _identities(state: TreeState) -> tuple[tuple[Identity, ...], tuple[Identity, ...]]:
    return state.directories, tuple(one.identity for one in state.files)


def _refreshed_manifest(manifest: Manifest, state: TreeState) -> Manifest:
    previous = {one.identity.path: one.content_sha256 for one in manifest.files}
    files = tuple(
        SourceFile(one.absolute, one.identity, previous[one.identity.path]) for one in state.files
    )
    return Manifest(
        code_root=manifest.code_root,
        memory_root=manifest.memory_root,
        snapshot_id=manifest.snapshot_id,
        source_bytes=sum(one.identity.size for one in files),
        directories=state.directories,
        files=files,
        candidate_tree=manifest.candidate_tree,
        exclusions=state.exclusions,
        bounds=state.bounds,
        excluded_files=state.excluded_files,
        excluded_sample=state.excluded_sample,
    )


def _cache_bytes(paths: CachePaths) -> int:
    return sum(
        path.stat().st_size
        for path in (paths.database, paths.manifest, paths.readiness)
        if path.exists()
    )


def _reclaim_temps(paths: CachePaths) -> None:
    """A dead publisher leaves at most one temp; the next exclusive publisher removes it."""
    for pattern in (".index.sqlite3.*.tmp", ".manifest.json.*.tmp", ".ready.json.*.tmp"):
        for path in paths.slot.glob(pattern):
            path.unlink(missing_ok=True)


def _reclaim_legacy_cache_roots(current: Path) -> None:
    """Remove version-named predecessors before this process can publish or query.

    The stable reclamation lock coordinates current/future schemas. Every legacy slot lock
    is then taken exclusively, with a bounded wait, before its root is atomically quarantined
    and removed. A live legacy reader/builder is therefore never deleted underneath; failure
    to obtain its lock refuses this acquisition instead of weakening the global cache bound.

    An obsolete binary launched *after* reclamation can recreate its old version-named root
    because that binary does not know the stable lock. The next current acquisition reclaims
    it; simultaneous already-running legacy users are covered by their slot locks.
    """
    parent = current.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = parent / "citation-source-index.reclaim.lock"
    handle = lock_path.open("a+b")
    deadline = time.monotonic() + RECLAMATION_LOCK_TIMEOUT_SECONDS
    try:
        _exclusive_lock_with_timeout(handle, lock_path, deadline)
        for tombstone in sorted(parent.glob(".citation-source-index-v*.reclaim.*")):
            _reclamation_time_remaining(deadline, tombstone)
            _remove_cache_tree(tombstone)
        for candidate in sorted(parent.iterdir()):
            if LEGACY_CACHE_NAME.fullmatch(candidate.name):
                _reclamation_time_remaining(deadline, candidate)
                _reclaim_legacy_root(candidate, deadline)
        _reclamation_time_remaining(deadline, current)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _reclaim_legacy_root(root: Path, deadline: float) -> None:
    handles: list[BinaryIO] = []
    try:
        if root.is_dir() and not root.is_symlink():
            for lock_path in sorted(root.glob("slot-*/index.lock")):
                handle = lock_path.open("a+b")
                try:
                    _exclusive_lock_with_timeout(handle, lock_path, deadline)
                except BaseException:
                    handle.close()
                    raise
                handles.append(handle)
        tombstone = root.parent / f".{root.name}.reclaim.{os.getpid()}.{uuid4().hex}"
        atomic_replace(root, tombstone)
        _remove_cache_tree(tombstone)
    except FileNotFoundError:
        return
    except OSError as error:
        raise SourceIndexError(f"cannot reclaim legacy citation cache {root}: {error}") from error
    finally:
        for handle in reversed(handles):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _remove_cache_tree(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError as error:
        raise SourceIndexError(f"cannot reclaim legacy citation cache {path}: {error}") from error


def _exclusive_lock_with_timeout(handle: BinaryIO, path: Path, deadline: float) -> None:
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as error:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceIndexError(
                    f"timed out waiting to reclaim live citation cache lock {path}"
                ) from error
            time.sleep(min(0.05, remaining))


def _reclamation_time_remaining(deadline: float, path: Path) -> None:
    if time.monotonic() >= deadline:
        raise SourceIndexError(
            f"timed out before legacy citation cache reclamation completed at {path}"
        )


def _under(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    return resolved == root or root in resolved.parents
