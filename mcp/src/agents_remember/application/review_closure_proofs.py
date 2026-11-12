"""Consumed object closures, kept on disk by the storage witnesses they were proven under.

A recorded comparison's closure is proven readable once: Git traverses it and consumes every body.
That proof stays good exactly while every object of the closure is still held where it was -- the
same loose file (device, inode, size, mtime, ctime) or the same pack set -- and the references are
unchanged; :class:`RepositoryLayout` observes both. A process that starts again re-observes those
witnesses and takes the proof only when they all agree; otherwise Git proves the closure again.
Nothing here decides by age. The store keeps a bounded number of proofs, the least recently used
leaving first, and a proof it cannot read is simply absent.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.serving.changeset_generation import RepositoryLayout

_FORMAT = "ar-review-closure-proof/v1"
_SUFFIX = ".closure.json"
DEFAULT_CAPACITY = 64


@dataclass(frozen=True)
class StoredClosure:
    objects: tuple[str, ...]
    witnesses: str
    references: str
    layout: str


@dataclass(frozen=True)
class ProvenClosure:
    """What one proof consumed: the closure's objects, their witnesses, references and layout."""

    objects: tuple[str, ...]
    witnesses: tuple[str, ...]
    references: str
    layout: RepositoryLayout


def witnesses_digest(witnesses: tuple[str, ...]) -> str:
    return sha256_digest([_FORMAT, list(witnesses)])


def layout_digest(layout: RepositoryLayout) -> str:
    """Git's locations, configuration and replace references as observed when the proof was made."""
    return sha256_digest(
        [
            _FORMAT,
            repr(
                (
                    layout.root,
                    layout.git,
                    layout.common,
                    layout.configuration,
                    layout.object_directories,
                    layout.observed,
                    layout.environment,
                )
            ),
        ]
    )


@dataclass(frozen=True)
class ClosureProofStore:
    """Storage only: the caller re-observes every witness before it relies on a stored proof."""

    directory: Path
    namespace: str
    capacity: int = DEFAULT_CAPACITY

    def _path(self, repository: Path, trees: tuple[str, ...]) -> Path:
        digest = sha256_digest([_FORMAT, self.namespace, str(repository), list(trees)])
        return self.directory / f"{digest}{_SUFFIX}"

    def load(self, repository: Path, trees: tuple[str, ...]) -> StoredClosure | None:
        path = self._path(repository, trees)
        try:
            envelope = json.loads(path.read_bytes())
            if (
                not isinstance(envelope, dict)
                or envelope.get("format") != _FORMAT
                or envelope.get("namespace") != self.namespace
                or envelope.get("repository") != str(repository)
                or envelope.get("trees") != list(trees)
            ):
                return None
            objects, witnesses, references, layout = (
                envelope.get("objects"),
                envelope.get("witnesses"),
                envelope.get("references"),
                envelope.get("layout"),
            )
            if not (
                isinstance(objects, str)
                and isinstance(witnesses, str)
                and isinstance(references, str)
                and isinstance(layout, str)
            ):
                return None
            os.utime(path, None)
            return StoredClosure(
                tuple(objects.split("\n")) if objects else (), witnesses, references, layout
            )
        except (OSError, ValueError, TypeError):
            return None

    def save(self, repository: Path, trees: tuple[str, ...], proven: ProvenClosure) -> None:
        envelope = {
            "format": _FORMAT,
            "namespace": self.namespace,
            "repository": str(repository),
            "trees": list(trees),
            "objects": "\n".join(proven.objects),
            "witnesses": witnesses_digest(proven.witnesses),
            "references": proven.references,
            "layout": layout_digest(proven.layout),
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(envelope, stream)
                os.replace(temporary, self._path(repository, trees))
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
            self._trim()
        except OSError:
            return

    def _trim(self) -> None:
        kept = sorted(
            self.directory.glob(f"*{_SUFFIX}"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for path in kept[self.capacity :]:
            path.unlink(missing_ok=True)
