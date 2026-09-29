"""The code objects a conversion resolves in: commit trees, blobs and symbol extents (MIK-R24 rule 2).

A conversion is a pure function of the memory tree, its database, the code objects it resolves in and
the conversion-format version (rule 6). :class:`CodeObjects` is the only door to the third input: it
reads commits, trees and blobs from one code repository's object store by exact identity, never a
working tree, so the same objects give the same answer on every machine and every line.

Symbols are bound by the shipped extractor (``memory_quality/style/citations``) through
:func:`extents.qualified_spans`, the same rule the curator writer (MIK-R12) uses: a name resolves only
when the file's language has a grammar and exactly one construct binds it. Parsing is cached per
(blob, grammar), so a blob cited by many cards is parsed once.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from agents_remember.errors import GrammarUnavailableError
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    read_git_blobs_bytes,
    read_git_tree_bytes,
    run_git,
)
from agents_remember.memory_quality.style.citations import extents, grammars
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    content_identity,
    line_count,
    range_bytes,
)

_FILE_MODES: Final = frozenset({"100644", "100755"})
_BLOB_BATCH: Final = 1500


class CodeObjectError(ValueError):
    """A code object the conversion needs cannot be read."""


@dataclass
class CodeObjects:
    """Read-only access to one code repository's commits, trees and blobs, with caches."""

    repository: Path
    _commits: dict[str, str | None] = field(default_factory=dict)
    _trees: dict[str, dict[str, str]] = field(default_factory=dict)
    _blobs: dict[str, bytes] = field(default_factory=dict)
    _definitions: dict[tuple[str, str], dict[str, list[extents.Extent]]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        self.repository = self.repository.resolve()

    def commit(self, name: str) -> str | None:
        """Return the full commit ID ``name`` denotes in the object store, or ``None``."""

        if name not in self._commits:
            result = run_git(
                self.repository,
                ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{name}^{{commit}}"],
                GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
            )
            value = result.stdout.strip()
            self._commits[name] = value if result.returncode == 0 and value else None
        return self._commits[name]

    def commits_with_prefix(self, prefix: str) -> tuple[str, ...]:
        """Every commit of the object store whose ID starts with ``prefix`` (sorted).

        An abbreviated commit is resolved by listing *all* candidates rather than asking Git to pick
        one, so a prefix that becomes ambiguous as the store grows is seen as ambiguous, never
        silently read as missing.
        """

        listed = run_git(
            self.repository,
            ["rev-parse", f"--disambiguate={prefix}"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        candidates = sorted(line for line in listed.stdout.split() if line)
        if not candidates:
            return ()
        typed = run_git(
            self.repository,
            ["cat-file", "--batch-check=%(objectname) %(objecttype)"],
            GitRunnerOptions(
                timeout=GIT_METADATA_TIMEOUT_SECONDS,
                input_text="".join(f"{c}\n" for c in candidates),
            ),
        )
        return tuple(
            name
            for name, kind in (
                line.split(" ", 1) for line in typed.stdout.splitlines() if " " in line
            )
            if kind == "commit"
        )

    def tree(self, commit: str) -> Mapping[str, str]:
        """Map each regular file of ``commit``'s tree to its blob ID."""

        if commit not in self._trees:
            blobs: dict[str, str] = {}
            for row in read_git_tree_bytes(self.repository, commit).split(b"\0"):
                if not row:
                    continue
                metadata, path = row.split(b"\t", 1)
                mode, kind, object_id = metadata.decode("ascii").split(" ")
                if kind == "blob" and mode in _FILE_MODES:
                    blobs[os.fsdecode(path)] = object_id
            self._trees[commit] = blobs
        return self._trees[commit]

    def prefetch(self, blob_ids: Iterable[str]) -> None:
        """Read every named blob not yet cached, in batches of one ``cat-file --batch`` each."""

        wanted = sorted({blob for blob in blob_ids if blob not in self._blobs})
        for start in range(0, len(wanted), _BLOB_BATCH):
            try:
                self._blobs.update(
                    read_git_blobs_bytes(self.repository, wanted[start : start + _BLOB_BATCH])
                )
            except Exception as error:
                raise CodeObjectError(f"code blobs cannot be read: {error}") from error

    def blob(self, blob_id: str) -> bytes:
        if blob_id not in self._blobs:
            self.prefetch((blob_id,))
        return self._blobs[blob_id]

    def has_blob(self, blob_id: str) -> bool:
        """Answer whether the object store holds ``blob_id`` as a blob."""

        if blob_id in self._blobs:
            return True
        result = run_git(
            self.repository,
            ["cat-file", "-t", "--end-of-options", blob_id],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        return result.returncode == 0 and result.stdout.strip() == "blob"

    def symbol_span(
        self, path: str, blob_id: str, name: str, *, top_level: bool = False
    ) -> tuple[int, int] | None:
        """The one extent the extractor binds for ``name`` in ``path`` at ``blob_id``, or ``None``.

        ``top_level`` is the export's reading of a recorded claim whose name the extractor binds
        more than once (a module-level ``def run`` and a local ``run = ...``): when exactly one of
        the spans is nested in no other definition of the file, that one is the claim's construct.
        """

        grammar = grammars.grammar_of(path)
        if grammar is None:
            return None
        key = (blob_id, grammar)
        if key not in self._definitions:
            lines = self.blob(blob_id).decode("utf-8", errors="surrogateescape").split("\n")
            try:
                self._definitions[key] = extents.definitions(path, lines)
            except GrammarUnavailableError as error:
                raise CodeObjectError(
                    f"the grammar for {path!r} is unavailable: {error}"
                ) from error
        bound = self._definitions[key]
        spans = extents.qualified_spans(name, bound)
        if len(spans) != 1 and top_level:
            spans = _outermost(spans, bound)
        return spans[0] if len(spans) == 1 else None

    def line_count(self, blob_id: str) -> int:
        return line_count(self.blob(blob_id))

    def content(
        self,
        blob_id: str,
        locator: Mapping[str, object],
        path: str,
        *,
        top_level: bool = False,
    ) -> str | None:
        """``content`` of ``locator`` in the blob, or ``None`` when it does not resolve there."""

        data = self.blob(blob_id)
        kind = locator["kind"]
        if kind == "file":
            return content_identity(data)
        if kind == "symbol":
            span = self.symbol_span(path, blob_id, str(locator["name"]), top_level=top_level)
            if span is None:
                return None
            start, end = span
        else:
            start, end = int(str(locator["start"])), int(str(locator["end"]))
        try:
            return content_identity(range_bytes(data, start, end))
        except RangeOutsideBlobError:
            return None


def _outermost(
    spans: tuple[tuple[int, int], ...], bound: Mapping[str, list[extents.Extent]]
) -> tuple[tuple[int, int], ...]:
    """The spans nested inside no other definition of the file."""

    others = {(one.start, one.end) for extents_ in bound.values() for one in extents_}
    return tuple(
        span
        for span in spans
        if not any(
            other != span and other[0] <= span[0] and span[1] <= other[1] for other in others
        )
    )
