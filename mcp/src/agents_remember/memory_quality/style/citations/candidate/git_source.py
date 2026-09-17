"""Exact Git membership and working-byte proof for candidate citation acquisition."""

from __future__ import annotations

import stat
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path, PurePosixPath

from agents_remember.kernel.git_command import run_git
from agents_remember.memory_quality.style.citations.exclusion_register import excluding_rule
from agents_remember.memory_quality.style.citations.source_index_state import (
    EXCLUDED_SAMPLE_LIMIT,
    CitationIndexCaps,
    ExclusionRegister,
    Identity,
    SourceFile,
    SourceIndexError,
    TreeState,
    apply_source_bounds,
)


@dataclass(frozen=True)
class GitSourceCandidate:
    """One immutable tree selects members; filesystem bytes must still prove that tree."""

    root: Path
    tree: str
    _verified: dict[str, Identity] = field(default_factory=dict, init=False, compare=False)

    def _git(self, args: list[str]) -> str:
        result = run_git(self.root, args)
        if result.returncode != 0:
            raise SourceIndexError("citation source candidate Git observation failed")
        return result.stdout

    @cached_property
    def members(self) -> dict[str, tuple[str, str]]:
        """Read the existing Git owner, never a caller-supplied filename population."""
        actual_root = self._git(["rev-parse", "--show-toplevel"]).strip()
        if Path(actual_root).resolve() != self.root.resolve():
            raise SourceIndexError("citation source candidate belongs to a different Git root")
        if self._git(["cat-file", "-t", self.tree]).strip() != "tree":
            raise SourceIndexError("citation source candidate identity is not a Git tree")
        entries: dict[str, tuple[str, str]] = {}
        for row in self._git(["ls-tree", "-r", "-z", "--full-tree", self.tree]).split("\0"):
            if not row:
                continue
            header, relative = row.split("\t", 1)
            mode, kind, blob = header.split(" ")
            if kind != "blob":
                continue
            parts = PurePosixPath(relative).parts
            if (
                not parts
                or PurePosixPath(relative).is_absolute()
                or ".." in parts
                or PurePosixPath(relative).as_posix() != relative
                or relative in entries
            ):
                raise SourceIndexError("citation source candidate has an unsafe member")
            entries[relative] = (mode, blob)
        return entries

    def _identity(self, relative: str) -> Identity:
        path = self.root
        try:
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise SourceIndexError("citation source candidate root is not a real directory")
            for part in PurePosixPath(relative).parts[:-1]:
                path = path / part
                if not stat.S_ISDIR(path.lstat().st_mode):
                    raise SourceIndexError(
                        f"citation source candidate has an unsafe parent: {relative}"
                    )
            path = self.root / relative
            if not stat.S_ISREG(path.lstat().st_mode):
                raise SourceIndexError(
                    f"citation source candidate member is not regular: {relative}"
                )
            return Identity.read(path, relative)
        except OSError as error:
            raise SourceIndexError(
                f"citation source candidate member is unavailable: {relative}"
            ) from error

    def verify(self, relatives: list[str]) -> tuple[Identity, ...]:
        """Batch actual Git hashing, retaining stat-checked results only for these bytes."""
        identities = {relative: self._identity(relative) for relative in relatives}
        pending = [
            relative
            for relative in relatives
            if self._verified.get(relative) != identities[relative]
        ]
        for offset in range(0, len(pending), 64):
            batch = pending[offset : offset + 64]
            hashes = self._git(["hash-object", "--no-filters", "--", *batch]).splitlines()
            if len(hashes) != len(batch):
                raise SourceIndexError("citation source candidate hash population differs")
            for relative, digest in zip(batch, hashes, strict=True):
                mode, expected = self.members[relative]
                if mode not in {"100644", "100755"} or digest != expected:
                    raise SourceIndexError(f"citation source candidate bytes differ: {relative}")
                if self._identity(relative) != identities[relative]:
                    raise SourceIndexError(
                        f"citation source candidate changed during proof: {relative}"
                    )
                self._verified[relative] = identities[relative]
        return tuple(identities[relative] for relative in relatives)

    def resolve(self, relative: str) -> Path | None:
        if relative not in self.members:
            return None
        self.verify([relative])
        return self.root / relative

    def state(
        self,
        memory: Path,
        skipped_suffixes: frozenset[str],
        caps: CitationIndexCaps,
        exclusions: ExclusionRegister,
    ) -> TreeState:
        """Git membership supersedes traversal-only directory skips, including build outputs.

        The explicit candidate route honours the same register and the same caps the default
        acquisition does: an excluded or oversized member is *reported* on the tree record, never
        silently absent and never a whole-tree refusal. Membership stays Git's answer; the caps
        decide only how much of it is read.
        """
        members = [
            relative
            for relative in sorted(self.members)
            if Path(relative).suffix.lower() not in skipped_suffixes
            and not (self.root / relative).is_relative_to(memory)
        ]
        candidates: list[str] = []
        excluded: list[str] = []
        for relative in members:
            if excluding_rule(exclusions, relative) is not None:
                excluded.append(relative)
                continue
            candidates.append(relative)
        identities = self.verify(candidates)
        kept, bounds = apply_source_bounds(identities, caps)
        directories = {self.root}
        for one in kept:
            parent = (self.root / one.path).parent
            while parent != self.root:
                directories.add(parent)
                parent = parent.parent
        return TreeState(
            directories=tuple(
                Identity.read(path, path.relative_to(self.root).as_posix())
                for path in sorted(directories)
            ),
            files=tuple(SourceFile(self.root / one.path, one) for one in kept),
            exclusions=exclusions,
            bounds=bounds,
            excluded_files=len(excluded),
            excluded_sample=tuple(sorted(excluded)[:EXCLUDED_SAMPLE_LIMIT]),
        )
