"""Manifest and POSIX identity values for a citation source snapshot.

This module owns the *values*: the caps one acquisition enforces, the exclusion register it
was filtered by, the skip report it produced, and the manifest that records all three. The
behaviour that decides them lives beside it -- :mod:`citation_index_settings` reads the memory
layer's settings keys and :mod:`exclusion_register` matches a path against the register -- so
a record can be read back without importing the machinery that wrote it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

SCHEMA_VERSION = 10
MAX_READINESS_BYTES = 16 * 1024
MAX_SOURCE_FILES = 100_000
# Ruled by the developer on 2026-08-20: 512 MiB applied to the post-exclusion, post-skip set.
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 4 * 1024 * 1024
# A hard stop remains only at an absurd extreme (~2 GiB total), reported as an actionable
# error naming offenders. It is not a second skip threshold: past it the tree is not a source
# population any more, and an index built by skipping most of it would describe nothing.
MAX_SOURCE_HARD_STOP_BYTES = 2 * 1024 * 1024 * 1024
MAX_DATABASE_BYTES = 256 * 1024 * 1024
SKIP_SAMPLE_LIMIT = 50
EXCLUDED_SAMPLE_LIMIT = 50
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")

SKIP_PER_FILE_CAP = "per-file-cap"
SKIP_TOTAL_CAP = "total-cap"
SKIP_UNREADABLE = "unreadable"
SKIP_REASONS = (SKIP_PER_FILE_CAP, SKIP_TOTAL_CAP, SKIP_UNREADABLE)

STATUS_WITHIN_CAPS = "within-caps"
STATUS_CAPPED = "capped"
# An explicit frozen lease reads a published generation without inspecting the source tree, so it
# has no current skip list to report. It says so rather than reporting a default that would read
# as "nothing was skipped".
STATUS_NOT_EVALUATED = "not-evaluated"

GITIGNORE_AUTHORITY_GIT = "git"
GITIGNORE_AUTHORITY_REGISTER = "register"
GITIGNORE_AUTHORITY_ABSENT = "absent"
GITIGNORE_AUTHORITIES = (
    GITIGNORE_AUTHORITY_GIT,
    GITIGNORE_AUTHORITY_REGISTER,
    GITIGNORE_AUTHORITY_ABSENT,
)

EXCLUSION_SOURCE_PATH_RULES = "pathRules.exclude"
EXCLUSION_SOURCE_GITIGNORE = "gitignore"
EXCLUSION_SOURCE_CALLER = "caller"
EXCLUSION_SOURCES = (
    EXCLUSION_SOURCE_PATH_RULES,
    EXCLUSION_SOURCE_GITIGNORE,
    EXCLUSION_SOURCE_CALLER,
)


class SourceIndexError(ValueError):
    """The requested source snapshot cannot be indexed safely."""


@dataclass(frozen=True)
class CitationIndexCaps:
    """The four bounds one acquisition enforces, with the module constants as the defaults.

    ``overridden`` names the keys the memory layer's settings actually supplied, so a record
    can say which bounds were configured rather than leaving a reader to guess whether a value
    came from the settings file or from the shipped default.
    """

    max_file_bytes: int = MAX_SOURCE_FILE_BYTES
    max_source_bytes: int = MAX_SOURCE_BYTES
    max_source_files: int = MAX_SOURCE_FILES
    hard_stop_bytes: int = MAX_SOURCE_HARD_STOP_BYTES
    overridden: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "maxFileBytes": self.max_file_bytes,
            "maxSourceBytes": self.max_source_bytes,
            "maxSourceFiles": self.max_source_files,
            "hardStopBytes": self.hard_stop_bytes,
            "overridden": list(self.overridden),
        }

    @classmethod
    def from_dict(cls, payload: object) -> CitationIndexCaps:
        if not isinstance(payload, dict):
            raise SourceIndexError("citation source-index caps record is malformed")
        return cls(
            max_file_bytes=_recorded_integer(payload, "maxFileBytes"),
            max_source_bytes=_recorded_integer(payload, "maxSourceBytes"),
            max_source_files=_recorded_integer(payload, "maxSourceFiles"),
            hard_stop_bytes=_recorded_integer(payload, "hardStopBytes"),
            overridden=tuple(str(one) for one in payload.get("overridden", ())),
        )


@dataclass(frozen=True)
class ExclusionRule:
    """One exclusion pattern and the source that supplied it."""

    source: str
    pattern: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "pattern": self.pattern}

    @classmethod
    def from_dict(cls, payload: object) -> ExclusionRule:
        if not isinstance(payload, dict):
            raise SourceIndexError("citation index exclusion rule is malformed")
        source = str(payload.get("source", ""))
        pattern = str(payload.get("pattern", ""))
        if source not in EXCLUSION_SOURCES or not pattern:
            raise SourceIndexError("citation index exclusion rule is malformed")
        return cls(source=source, pattern=pattern)


@dataclass(frozen=True)
class ExclusionRegister:
    """The rule set one acquisition is filtered by, plus how its ``.gitignore`` was honoured."""

    rules: tuple[ExclusionRule, ...] = ()
    gitignore_authority: str = GITIGNORE_AUTHORITY_ABSENT
    gitignore_patterns: tuple[str, ...] = ()
    settings_path: str | None = None
    caps: CitationIndexCaps = CitationIndexCaps()

    @property
    def settings_excludes(self) -> tuple[str, ...]:
        return tuple(one.pattern for one in self.rules if one.source == EXCLUSION_SOURCE_PATH_RULES)

    @property
    def caller_excludes(self) -> tuple[str, ...]:
        return tuple(one.pattern for one in self.rules if one.source == EXCLUSION_SOURCE_CALLER)

    @property
    def gitignore_excludes(self) -> tuple[str, ...]:
        return tuple(one.pattern for one in self.rules if one.source == EXCLUSION_SOURCE_GITIGNORE)

    def to_dict(self) -> dict[str, object]:
        return {
            "rules": [one.to_dict() for one in self.rules],
            "gitignoreAuthority": self.gitignore_authority,
            "gitignorePatterns": list(self.gitignore_patterns),
            "settingsPath": self.settings_path,
            "caps": self.caps.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> ExclusionRegister:
        if not isinstance(payload, dict):
            raise SourceIndexError("citation index exclusion register is malformed")
        authority = str(payload.get("gitignoreAuthority", GITIGNORE_AUTHORITY_ABSENT))
        if authority not in GITIGNORE_AUTHORITIES:
            raise SourceIndexError("citation index exclusion register has an unknown authority")
        return cls(
            rules=tuple(ExclusionRule.from_dict(one) for one in payload.get("rules", ())),
            gitignore_authority=authority,
            gitignore_patterns=tuple(str(one) for one in payload.get("gitignorePatterns", ())),
            settings_path=(
                None if payload.get("settingsPath") is None else str(payload["settingsPath"])
            ),
            caps=CitationIndexCaps.from_dict(payload.get("caps", {})),
        )


@dataclass(frozen=True)
class SourceSkip:
    """One file the index did not read, and why, with the size that decided it."""

    path: str
    size: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "size": self.size, "reason": self.reason}

    @classmethod
    def from_dict(cls, payload: object) -> SourceSkip:
        if not isinstance(payload, dict):
            raise SourceIndexError("citation source-index skip entry is malformed")
        reason = str(payload.get("reason", ""))
        if reason not in SKIP_REASONS:
            raise SourceIndexError("citation source-index skip entry has an unknown reason")
        return cls(
            path=str(payload.get("path", "")),
            size=int(str(payload.get("size", 0))),
            reason=reason,
        )


@dataclass(frozen=True)
class SourceBoundsReport:
    """What the caps did to one acquisition: the exact counts and a bounded offender sample.

    ``skipped_count`` is exact and ``skipped`` is bounded, the same shape the quality surface
    already uses for report-only findings: a reader who needs to know whether anything was
    skipped never has to read the whole list, and a reader who needs the offenders gets the
    largest ones first.
    """

    caps: CitationIndexCaps = CitationIndexCaps()
    candidate_files: int = 0
    indexed_files: int = 0
    indexed_bytes: int = 0
    skipped_count: int = 0
    skipped: tuple[SourceSkip, ...] = ()
    status: str = STATUS_WITHIN_CAPS

    @property
    def capped(self) -> bool:
        return self.status == STATUS_CAPPED

    def with_skips(self, extra: Sequence[SourceSkip]) -> SourceBoundsReport:
        """The same acquisition after reading, with unreadable members added to the report."""
        if not extra:
            return self
        skipped = _bounded_skips((*self.skipped, *extra))
        return replace(
            self,
            indexed_files=max(self.indexed_files - len(extra), 0),
            indexed_bytes=max(self.indexed_bytes - sum(one.size for one in extra), 0),
            skipped_count=self.skipped_count + len(extra),
            skipped=skipped,
            status=STATUS_CAPPED,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "caps": self.caps.to_dict(),
            "candidateFiles": self.candidate_files,
            "indexedFiles": self.indexed_files,
            "indexedBytes": self.indexed_bytes,
            "skippedCount": self.skipped_count,
            "skippedSampleLimit": SKIP_SAMPLE_LIMIT,
            "skippedFiles": [one.to_dict() for one in self.skipped],
        }

    @classmethod
    def from_dict(cls, payload: object) -> SourceBoundsReport:
        if not isinstance(payload, dict):
            raise SourceIndexError("citation source-index bounds record is malformed")
        status = str(payload.get("status", STATUS_WITHIN_CAPS))
        if status not in {STATUS_WITHIN_CAPS, STATUS_CAPPED, STATUS_NOT_EVALUATED}:
            raise SourceIndexError("citation source-index bounds record has an unknown status")
        return cls(
            caps=CitationIndexCaps.from_dict(payload.get("caps", {})),
            candidate_files=int(str(payload.get("candidateFiles", 0))),
            indexed_files=int(str(payload.get("indexedFiles", 0))),
            indexed_bytes=int(str(payload.get("indexedBytes", 0))),
            skipped_count=int(str(payload.get("skippedCount", 0))),
            skipped=tuple(SourceSkip.from_dict(one) for one in payload.get("skippedFiles", ())),
            status=status,
        )


def _bounded_skips(skips: Sequence[SourceSkip]) -> tuple[SourceSkip, ...]:
    """The largest offenders first, capped at the sample limit, in a deterministic order."""
    ordered = sorted(skips, key=lambda one: (-one.size, one.reason, one.path))
    return tuple(ordered[:SKIP_SAMPLE_LIMIT])


def apply_source_bounds(
    identities: Sequence[Identity],
    caps: CitationIndexCaps,
) -> tuple[tuple[Identity, ...], SourceBoundsReport]:
    """Apply the ruled caps and return the readable population **plus** the report.

    Exceeding a cap is a reported skip naming the file and its size -- never a silent omission
    and never a whole-tree refusal. Only two conditions refuse, both actionable and both named:

    * more files than the file-count cap (unchanged at 100k), or
    * an aggregate past the hard stop (~2 GiB), which is not a source population any more.

    Between the total cap and the hard stop the largest files are skipped, deterministically,
    until the remainder fits: the index stays buildable and the report says exactly what it does
    not cover.
    """
    oversized = [one for one in identities if one.size > caps.max_file_bytes]
    skips = [
        SourceSkip(path=one.path, size=one.size, reason=SKIP_PER_FILE_CAP) for one in oversized
    ]
    remaining = [one for one in identities if one.size <= caps.max_file_bytes]
    if len(remaining) > caps.max_source_files:
        raise SourceIndexError(
            f"citation source-index input has {len(remaining)} files under the per-file cap, "
            f"above its {caps.max_source_files}-file cap. Exclude whole trees through "
            f"onboarding.pathRules.exclude (or raise onboarding.citationIndex.maxSourceFiles) "
            f"before indexing; the largest directories are the ones to look at first."
        )
    total = sum(one.size for one in remaining)
    if total > caps.hard_stop_bytes:
        offenders = sorted(remaining, key=lambda one: (-one.size, one.path))[:3]
        raise SourceIndexError(
            f"citation source-index input is {total} bytes, past the {caps.hard_stop_bytes}-byte "
            f"hard stop. Largest offenders: "
            f"{[{'path': one.path, 'size': one.size} for one in offenders]}. Exclude them through "
            f"onboarding.pathRules.exclude, or raise onboarding.citationIndex.hardStopBytes "
            f"deliberately, before this tree can be indexed."
        )
    if total > caps.max_source_bytes:
        kept, dropped, total = _drop_until_within_cap(remaining, total, caps.max_source_bytes)
        skips.extend(dropped)
    else:
        kept = remaining
    report = SourceBoundsReport(
        caps=caps,
        candidate_files=len(identities),
        indexed_files=len(kept),
        indexed_bytes=total,
        skipped_count=len(skips),
        skipped=_bounded_skips(skips),
        status=STATUS_CAPPED if skips else STATUS_WITHIN_CAPS,
    )
    return tuple(kept), report


def _drop_until_within_cap(
    identities: list[Identity],
    total: int,
    cap: int,
) -> tuple[list[Identity], list[SourceSkip], int]:
    """Skip the largest files until the remainder fits, reporting every one of them."""
    ordered = sorted(identities, key=lambda one: (-one.size, one.path))
    dropped: list[SourceSkip] = []
    index = 0
    while total > cap and index < len(ordered):
        one = ordered[index]
        index += 1
        total -= one.size
        dropped.append(SourceSkip(path=one.path, size=one.size, reason=SKIP_TOTAL_CAP))
    return ordered[index:], dropped, total


def _recorded_integer(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value <= 0:
        raise SourceIndexError(f"citation source-index caps record has an invalid {key}")
    return value


def candidate_tree(value: object) -> str | None:
    """A null filesystem selection or one exact canonical Git tree identity."""
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise SourceIndexError("citation source candidate tree must be 40 lowercase hex digits")
    return value


class SourceIndexManifestError(ValueError):
    """A source-index manifest is obsolete or malformed."""


def canonical_hash(value: object) -> bool:
    """Whether ``value`` is one canonical SHA-256 spelling."""
    return isinstance(value, str) and HASH_PATTERN.fullmatch(value) is not None


def _bounded_integer(value: object, *, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SourceIndexManifestError(f"citation source-index readiness has invalid {name}")
    return value


def _canonical_root(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise SourceIndexManifestError(f"citation source-index readiness has invalid {name}")
    root = Path(value)
    if (
        not root.is_absolute()
        or root.as_posix() != value
        or any(part == ".." for part in root.parts)
    ):
        raise SourceIndexManifestError(f"citation source-index readiness has invalid {name}")
    return value


@dataclass(frozen=True)
class ReadyGeneration:
    """Constant-size authority that makes one database generation queryable.

    The full per-file manifest remains the default dirty-safety input. Frozen readers use
    only this bounded marker and matching database metadata, so they never deserialize a
    tree-sized payload. The random generation id prevents an old marker from blessing a
    subsequently replaced database that happens to describe the same roots.
    """

    generation_id: str
    snapshot_id: str
    code_root: str
    memory_root: str
    files_indexed: int
    source_bytes: int
    database_bytes: int
    candidate_tree: str | None = None

    @classmethod
    def from_json(cls, path: Path) -> ReadyGeneration:
        with path.open("rb") as handle:
            raw = handle.read(MAX_READINESS_BYTES + 1)
        if len(raw) > MAX_READINESS_BYTES:
            raise SourceIndexManifestError("citation source-index readiness marker is oversized")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {
            "schemaVersion",
            "state",
            "generationId",
            "snapshotId",
            "codeRoot",
            "memoryRoot",
            "filesIndexed",
            "sourceBytes",
            "databaseBytes",
            "candidateTree",
        }:
            raise SourceIndexManifestError("citation source-index readiness marker is malformed")
        if type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != SCHEMA_VERSION:
            raise SourceIndexManifestError("citation source index schema is obsolete")
        if payload["state"] != "ready":
            raise SourceIndexManifestError("citation source-index generation is not ready")
        generation_id = payload["generationId"]
        snapshot_id = payload["snapshotId"]
        if not canonical_hash(generation_id) or not canonical_hash(snapshot_id):
            raise SourceIndexManifestError(
                "citation source-index readiness has a noncanonical generation identity"
            )
        return cls(
            generation_id=generation_id,
            snapshot_id=snapshot_id,
            code_root=_canonical_root(payload["codeRoot"], "code root"),
            memory_root=_canonical_root(payload["memoryRoot"], "memory root"),
            files_indexed=_bounded_integer(
                payload["filesIndexed"], minimum=0, maximum=MAX_SOURCE_FILES, name="file count"
            ),
            source_bytes=_bounded_integer(
                payload["sourceBytes"], minimum=0, maximum=MAX_SOURCE_BYTES, name="source bytes"
            ),
            database_bytes=_bounded_integer(
                payload["databaseBytes"],
                minimum=1,
                maximum=MAX_DATABASE_BYTES,
                name="database bytes",
            ),
            candidate_tree=candidate_tree(payload["candidateTree"]),
        )

    def to_json(self) -> str:
        payload = (
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "state": "ready",
                    "generationId": self.generation_id,
                    "snapshotId": self.snapshot_id,
                    "codeRoot": self.code_root,
                    "memoryRoot": self.memory_root,
                    "filesIndexed": self.files_indexed,
                    "sourceBytes": self.source_bytes,
                    "databaseBytes": self.database_bytes,
                    "candidateTree": self.candidate_tree,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        if len(payload.encode()) > MAX_READINESS_BYTES:
            raise SourceIndexManifestError("citation source-index readiness marker is oversized")
        return payload


@dataclass(frozen=True)
class Identity:
    """POSIX metadata used as the cheap trigger for authoritative content hashing."""

    path: str
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def read(cls, path: Path, relative: str) -> Identity:
        stat = path.stat()
        return cls(
            path=relative,
            device=stat.st_dev,
            inode=stat.st_ino,
            mode=stat.st_mode,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            ctime_ns=stat.st_ctime_ns,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> Identity:
        return cls(
            path=str(payload["path"]),
            device=int(str(payload["device"])),
            inode=int(str(payload["inode"])),
            mode=int(str(payload["mode"])),
            size=int(str(payload["size"])),
            mtime_ns=int(str(payload["mtimeNs"])),
            ctime_ns=int(str(payload["ctimeNs"])),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "device": self.device,
            "inode": self.inode,
            "mode": self.mode,
            "size": self.size,
            "mtimeNs": self.mtime_ns,
            "ctimeNs": self.ctime_ns,
        }


@dataclass(frozen=True)
class SourceFile:
    """One indexed file's path, current metadata, and authoritative content digest."""

    absolute: Path
    identity: Identity
    content_sha256: str = ""

    @classmethod
    def from_dict(cls, root: Path, payload: dict[str, object]) -> SourceFile:
        identity = Identity.from_dict(payload)
        return cls(
            absolute=root / identity.path,
            identity=identity,
            content_sha256=str(payload["contentSha256"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {**self.identity.to_dict(), "contentSha256": self.content_sha256}


@dataclass(frozen=True)
class TreeState:
    """Every relevant directory entry and readable-file candidate in deterministic order.

    The tree record carries the three things a later reader cannot reconstruct from the file
    list: the **exclusion register** it was filtered by, the **bounds report** the caps produced,
    and the paths the register actually removed. A file absent from ``files`` is therefore never
    a mystery -- either a rule named in ``excluded_sample`` (with the exact count in
    ``excluded_files``) excluded it, or an entry in ``bounds.skipped`` skipped it.

    ``excluded_sample`` is bounded like the skip report and for the same reason: a reader who
    needs to know *whether* the register removed anything reads the count, and a reader who needs
    the offenders reads the sample. Computing the count and consuming it nowhere is what made the
    claim "excluded paths are reported in the run" unbacked (L14R-8).
    """

    directories: tuple[Identity, ...]
    files: tuple[SourceFile, ...]
    exclusions: ExclusionRegister = ExclusionRegister()
    bounds: SourceBoundsReport = SourceBoundsReport()
    excluded_files: int = 0
    excluded_sample: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifest:
    """The atomic metadata companion for one immutable database generation."""

    code_root: str
    memory_root: str
    snapshot_id: str
    source_bytes: int
    directories: tuple[Identity, ...]
    files: tuple[SourceFile, ...]
    candidate_tree: str | None = None
    exclusions: ExclusionRegister = ExclusionRegister()
    bounds: SourceBoundsReport = SourceBoundsReport()
    excluded_files: int = 0
    excluded_sample: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, path: Path) -> Manifest:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload["schemaVersion"]) != SCHEMA_VERSION:
            raise SourceIndexManifestError("citation source index schema is obsolete")
        root = Path(str(payload["codeRoot"]))
        return cls(
            code_root=root.as_posix(),
            memory_root=str(payload["memoryRoot"]),
            snapshot_id=str(payload["snapshotId"]),
            source_bytes=int(payload["sourceBytes"]),
            directories=tuple(Identity.from_dict(one) for one in payload["directories"]),
            files=tuple(SourceFile.from_dict(root, one) for one in payload["files"]),
            candidate_tree=candidate_tree(payload["candidateTree"]),
            exclusions=ExclusionRegister.from_dict(payload["exclusions"]),
            bounds=SourceBoundsReport.from_dict(payload["bounds"]),
            excluded_files=int(str(payload["excludedFiles"])),
            excluded_sample=tuple(str(one) for one in payload["excludedSample"]),
        )

    def to_json(self) -> str:
        return (
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "codeRoot": self.code_root,
                    "memoryRoot": self.memory_root,
                    "snapshotId": self.snapshot_id,
                    "sourceBytes": self.source_bytes,
                    "directories": [one.to_dict() for one in self.directories],
                    "files": [one.to_dict() for one in self.files],
                    "candidateTree": self.candidate_tree,
                    "exclusions": self.exclusions.to_dict(),
                    "bounds": self.bounds.to_dict(),
                    "excludedFiles": self.excluded_files,
                    "excludedSample": list(self.excluded_sample),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )


@dataclass(frozen=True)
class Validation:
    """Whether a generation is current, content-stale, or metadata-equivalent."""

    state: TreeState
    stale: bool
    metadata_changed: bool
