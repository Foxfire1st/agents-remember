"""The code objects a conversion resolves in: commit trees, blobs and symbol extents (MIK-R24 rule 2).

A conversion is a pure function of the memory tree, its database, the code objects it resolves in and
the conversion-format version (rule 6). :class:`CodeObjects` is the only door to the third input: it
reads commits, trees and blobs from one code repository's object store by exact identity, never a
working tree, so the same objects give the same answer on every machine and every line.

Symbols are bound by the shipped extractor (``memory_quality/style/citations``) through
:func:`extents.qualified_spans`, the same rule the curator writer (MIK-R12) uses: a name resolves only
when the file's language has a grammar and exactly one construct binds it. Parsing is cached per
(blob, grammar, path), since test-case eligibility also depends on the exact filename.

**Symbol answers across reads (D569).** Which extents bind a name in a blob is a function of the
blob's bytes, the path (its grammar and its test-case eligibility) and the name, so the answer --
:func:`extents.qualified_spans` over the blob's definitions -- is also kept per process in
:data:`SYMBOL_SPANS`, keyed by the repository, the complete blob id, the path and the name. A later
read of a comparison that changed one blob parses only that blob. The memo holds answers only, and
the blob is still read from the store before an answer is used, so whether the store holds it is
asked exactly as before. The table is also kept on disk between processes
(:mod:`agents_remember.application.review_memo_store`).
"""

from __future__ import annotations

import os
import re
import subprocess
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
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo, is_complete_object_id
from agents_remember.memory_quality.style.citations import extents, grammars
from agents_remember.models.knowledge_files import anchor_content
from agents_remember.models.knowledge_files.anchor_content import (
    RangeOutsideBlobError,
    content_identity,
    range_from_lines,
)

_FILE_MODES: Final = frozenset({"100644", "100755", "120000"})
"""Tree entry modes bound as source blobs: regular files and symlinks (MIK-R53 rule 3).

A symlink binds its target blob, so an extant link is never ``absent``; only a deletion is.
The blob is the link target's bytes, never parsed for symbol anchors."""
_BLOB_BATCH: Final = 1500


# ``git cat-file -e``: "Exit with zero status if <object> exists and is a valid object"; a valid name
# of an object the store does not hold exits 1 without an error, any failure exits 128.
_GIT_NOT_FOUND: Final = 1
# The names :meth:`CodeObjects.prefetch_presence` asks about in one batch: full object IDs, which
# ``cat-file --batch-check`` and ``cat-file -e``/``-t`` read as the same object. Any other spelling
# is left to :meth:`CodeObjects.has_blob`'s own two reads.
_FULL_OBJECT_ID: Final = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_OBJECT_TYPES: Final = frozenset({"blob", "tree", "commit", "tag"})
_BATCH_CHECK: Final = ("cat-file", "--batch-check=%(objectname) %(objecttype)")


Spans = tuple[tuple[int, int], ...]
SymbolKey = tuple[str, str, str, str]
"""The repository, the complete blob id, the path and the name one symbol answer is a function of."""

SYMBOL_SPANS: BoundedMemo[SymbolKey, Spans] = BoundedMemo(65_536)
"""Each name's extents in one blob, as :func:`extents.qualified_spans` binds them (one answer per
entry; well above the symbol anchors of several comparisons, so a review does not evict what the
next comparison is about to reuse)."""


class CodeObjectError(ValueError):
    """A code object the conversion needs cannot be read."""


@dataclass
class CodeObjects:
    """Read-only access to one code repository's commits, trees and blobs, with caches."""

    repository: Path
    object_inputs: Path | None = None
    """A second object store read beside the repository's, never written (MIK-R73 rule 7)."""
    _commits: dict[str, str | None] = field(default_factory=dict)
    _trees: dict[str, dict[str, str]] = field(default_factory=dict)
    _modes: dict[str, dict[str, str]] = field(default_factory=dict)
    """Tree entry modes by path, for each tree :meth:`tree` read (MIK-R53 rule 3)."""
    _blobs: dict[str, bytes] = field(default_factory=dict)
    _lines: dict[str, list[bytes]] = field(default_factory=dict)
    _presence: dict[str, bool] = field(default_factory=dict)
    """:meth:`has_blob` answers for objects the store holds: ``True`` for a blob, ``False`` for
    another type. A missing object or a failed read is never kept, so it is asked again."""
    _definitions: dict[tuple[str, str, str], dict[str, list[extents.Extent]]] = field(
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
                GitRunnerOptions(
                    timeout=GIT_METADATA_TIMEOUT_SECONDS, object_inputs=self.object_inputs
                ),
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
            GitRunnerOptions(
                timeout=GIT_METADATA_TIMEOUT_SECONDS, object_inputs=self.object_inputs
            ),
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
                object_inputs=self.object_inputs,
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
        """Map each regular file and symlink of ``commit``'s tree to its blob ID."""

        if commit not in self._trees:
            blobs: dict[str, str] = {}
            modes: dict[str, str] = {}
            rows = read_git_tree_bytes(self.repository, commit, object_inputs=self.object_inputs)
            for row in rows.split(b"\0"):
                if not row:
                    continue
                metadata, path = row.split(b"\t", 1)
                mode, kind, object_id = metadata.decode("ascii").split(" ")
                modes[os.fsdecode(path)] = mode
                if kind == "blob" and mode in _FILE_MODES:
                    blobs[os.fsdecode(path)] = object_id
            self._trees[commit] = blobs
            self._modes[commit] = modes
        return self._trees[commit]

    def tree_modes(self, commit: str) -> Mapping[str, str]:
        """Tree entry modes by path for ``commit``'s tree (``120000`` marks a symlink)."""

        self.tree(commit)
        return self._modes[commit]

    def tree_entry_mode(self, tree: str, path: str) -> str | None:
        """Observe an exact entry, including trees with no recursive leaf rows."""
        result = run_git(
            self.repository,
            ["ls-tree", "-z", tree, "--", path],
            GitRunnerOptions(
                timeout=GIT_METADATA_TIMEOUT_SECONDS, object_inputs=self.object_inputs
            ),
        )
        if result.returncode:
            raise CodeObjectError(f"{path}: tree entry cannot be read: {result.stderr.strip()}")
        return result.stdout.split(" ", 1)[0] if result.stdout else None

    @staticmethod
    def link_paths(modes: Mapping[str, str]) -> frozenset[str]:
        """Paths of ``modes`` whose tree entry is a symlink (MIK-R53 rule 3)."""

        return frozenset(path for path, mode in modes.items() if mode == "120000")

    def prefetch(self, blob_ids: Iterable[str]) -> None:
        """Read every named blob not yet cached, in batches of one ``cat-file --batch`` each."""

        wanted = sorted({blob for blob in blob_ids if blob not in self._blobs})
        for start in range(0, len(wanted), _BLOB_BATCH):
            try:
                self._blobs.update(
                    read_git_blobs_bytes(
                        self.repository,
                        wanted[start : start + _BLOB_BATCH],
                        object_inputs=self.object_inputs,
                    )
                )
            except Exception as error:
                raise CodeObjectError(f"code blobs cannot be read: {error}") from error

    def blob(self, blob_id: str) -> bytes:
        if blob_id not in self._blobs:
            self.prefetch((blob_id,))
        return self._blobs[blob_id]

    def holds(self, blob_id: str) -> bool:
        """Whether this store has already read ``blob_id``, or found it held as a blob, in this run.

        Nothing is asked of Git: a kept answer about the blob is used only behind this fact.
        """

        return blob_id in self._blobs or self._presence.get(blob_id) is True

    def has_blob(self, blob_id: str) -> bool:
        """Answer whether the object store holds ``blob_id`` as a blob.

        A missing object is Git's documented not-found answer (``cat-file -e`` exits 1): ``False``.
        Any other failure is Git's, never taken for a missing object: it raises
        :class:`CodeObjectError` naming it (a timeout raises ``subprocess.TimeoutExpired``), so a
        caller reports an unreadable input rather than an absent one (L09 review R3-2). The
        answer for an object the store holds is kept (an ID names its bytes); a missing object
        and a failure are asked again on the next call.
        """

        if blob_id in self._blobs:
            return True
        if blob_id in self._presence:
            return self._presence[blob_id]
        options = GitRunnerOptions(
            timeout=GIT_METADATA_TIMEOUT_SECONDS, object_inputs=self.object_inputs
        )
        exists = run_git(self.repository, ["cat-file", "-e", "--end-of-options", blob_id], options)
        if exists.returncode == _GIT_NOT_FOUND:
            return False
        kind = exists
        if exists.returncode == 0:
            kind = run_git(
                self.repository, ["cat-file", "-t", "--end-of-options", blob_id], options
            )
        if kind.returncode != 0:
            detail = kind.stderr.strip() or f"git exited {kind.returncode}"
            raise CodeObjectError(
                f"the object store cannot say whether it holds {blob_id}: {detail}"
            )
        self._presence[blob_id] = kind.stdout.strip() == "blob"
        return self._presence[blob_id]

    def prefetch_presence(self, blob_ids: Iterable[str]) -> None:
        """Answer :meth:`has_blob` ahead for many IDs, with two ``cat-file --batch-check`` reads.

        ``has_blob`` asks ``cat-file -e``, which looks at the stored object itself, and then
        ``cat-file -t``, which reports the type through any replace ref. The two batches ask the
        same two questions for every ID at once: one with ``--no-replace-objects`` (is the object
        itself stored), one as ``-t`` reads (its type). Only an object both batches find is kept;
        a missing object, an unreadable one, an unexpected reply or a failed batch keeps nothing,
        so ``has_blob`` asks Git one by one and answers or raises exactly where it did before.
        """

        wanted = sorted(
            {
                blob
                for blob in blob_ids
                if blob not in self._blobs
                and blob not in self._presence
                and _FULL_OBJECT_ID.fullmatch(blob)
            }
        )
        if not wanted:
            return
        stored = self._batch_types(wanted, ["--no-replace-objects", *_BATCH_CHECK])
        typed = self._batch_types(wanted, list(_BATCH_CHECK)) if stored else None
        if not stored or not typed:
            return
        for blob in wanted:
            if stored[blob] in _OBJECT_TYPES and typed[blob] in _OBJECT_TYPES:
                self._presence[blob] = typed[blob] == "blob"

    def _batch_types(self, blob_ids: list[str], args: list[str]) -> dict[str, str] | None:
        """Each ID's ``%(objecttype)`` (or ``missing``) in one batch; ``None`` when it fails."""

        try:
            result = run_git(
                self.repository,
                args,
                GitRunnerOptions(
                    timeout=GIT_METADATA_TIMEOUT_SECONDS,
                    input_text="".join(f"{blob}\n" for blob in blob_ids),
                    object_inputs=self.object_inputs,
                ),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        rows = [line.split(" ") for line in result.stdout.splitlines()]
        if result.returncode != 0 or len(rows) != len(blob_ids):
            return None
        if any(len(row) != 2 or row[0] != blob for row, blob in zip(rows, blob_ids, strict=True)):
            return None
        return {row[0]: row[1] for row in rows}

    def symbol_span(
        self,
        path: str,
        blob_id: str,
        name: str,
        *,
        top_level: bool = False,
        symlinks: frozenset[str] = frozenset(),
    ) -> tuple[int, int] | None:
        """The one extent the extractor binds for ``name`` in ``path`` at ``blob_id``, or ``None``.

        ``top_level`` is the export's reading of a recorded claim whose name the extractor binds
        more than once (a module-level ``def run`` and a local ``run = ...``): when exactly one of
        the spans is nested in no other definition of the file, that one is the claim's construct.

        ``symlinks`` names the tree paths whose entry is a symlink (mode 120000): a symbol
        anchor there is refused before the extractor runs, since link bytes are never parsed
        for symbol anchors (MIK-R53 rule 3).
        """

        if path in symlinks:
            raise CodeObjectError(
                f"{path!r} is a symlink (mode 120000): symbol anchors are never parsed "
                "from link bytes; cite a line_range or the file instead"
            )
        grammar = grammars.grammar_of(path)
        if grammar is None:
            return None
        try:
            spans = self.qualified_spans(path, blob_id, name)
            if len(spans) != 1 and top_level:
                spans = _outermost(spans, self._bound(path, blob_id, grammar))
        except GrammarUnavailableError as error:
            raise CodeObjectError(f"the grammar for {path!r} is unavailable: {error}") from error
        return spans[0] if len(spans) == 1 else None

    def qualified_spans(self, path: str, blob_id: str, name: str) -> Spans:
        """:func:`extents.qualified_spans` of ``name`` in ``blob_id`` read at ``path``.

        The blob is read first, as a parse needs it, so a store that no longer holds it fails here
        whether or not the answer is kept. A kept answer (:data:`SYMBOL_SPANS`) is the same
        function of the same bytes; a grammar that will not load raises
        :class:`GrammarUnavailableError` and keeps nothing.
        """

        data = self.blob(blob_id)
        key = (str(self.repository), blob_id, path, name)
        remembered = SYMBOL_SPANS.get(key) if is_complete_object_id(blob_id) else None
        if remembered is not None:
            return remembered[0]
        grammar = grammars.grammar_of(path)
        if grammar is None:
            return ()
        spans = extents.qualified_spans(name, self._bound(path, blob_id, grammar, data))
        if is_complete_object_id(blob_id):
            SYMBOL_SPANS.put(key, spans)
        return spans

    def known_spans(self, path: str, blob_id: str, name: str) -> Spans | None:
        """The kept answer of :meth:`qualified_spans`, if one is kept; nothing is read or parsed."""

        remembered = SYMBOL_SPANS.get((str(self.repository), blob_id, path, name))
        return None if remembered is None else remembered[0]

    def _bound(
        self, path: str, blob_id: str, grammar: str, data: bytes | None = None
    ) -> dict[str, list[extents.Extent]]:
        key = (blob_id, grammar, path)
        if key not in self._definitions:
            source = self.blob(blob_id) if data is None else data
            self._definitions[key] = extents.definitions(path, extents.source_lines(source))
        return self._definitions[key]

    def line_count(self, blob_id: str) -> int:
        return len(self._blob_lines(blob_id))

    def blob_lines(self, blob_id: str) -> list[bytes]:
        """The blob's lines as :func:`anchor_content.blob_lines` splits them, split once per blob.

        The list is this store's own: a caller reads it and never changes it.
        """

        return self._blob_lines(blob_id)

    def _blob_lines(self, blob_id: str) -> list[bytes]:
        """Split each blob once for this conversion, shared across paths and anchors."""

        if blob_id not in self._lines:
            self._lines[blob_id] = anchor_content.blob_lines(self.blob(blob_id))
        return self._lines[blob_id]

    def content(
        self,
        blob_id: str,
        locator: Mapping[str, object],
        path: str,
        *,
        top_level: bool = False,
        symlinks: frozenset[str] = frozenset(),
    ) -> str | None:
        """``content`` of ``locator`` in the blob, or ``None`` when it does not resolve there.

        ``symlinks`` names the tree paths whose entry is a symlink: a symbol locator there
        raises :class:`CodeObjectError` before the extractor runs (MIK-R53 rule 3); file and
        line-range locators read the link bytes as before.
        """

        data = self.blob(blob_id)
        kind = locator["kind"]
        if kind == "file":
            return content_identity(data)
        if kind == "symbol":
            span = self.symbol_span(
                path, blob_id, str(locator["name"]), top_level=top_level, symlinks=symlinks
            )
            if span is None:
                return None
            start, end = span
        else:
            start, end = int(str(locator["start"])), int(str(locator["end"]))
        try:
            return content_identity(range_from_lines(self._blob_lines(blob_id), start, end))
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
