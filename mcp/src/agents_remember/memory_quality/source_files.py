"""Git-visible source text for a synchronous memory-quality check.

Git owns the listing. Citations retain bounded content verification even
outside that listing, with an ignored or binary reason reported separately.
Only cited content is retained for the check; all per-run state dies with it,
with no persistent source index.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from agents_remember.errors import SourceFileUnavailableError
from agents_remember.kernel.git_command import run_git
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodePathSet,
    code_tree_from_working_directory,
)

MAX_SOURCE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class QualitySourceFiles:
    """Read cited bytes without following links or exceeding the size bound.

    Existence remains a filesystem fact for structural validators. A reference
    to an unreadable file gets its precise reason through the reference-anchor
    validator; readable ignored and binary files retain verification and a note.
    """

    root: Path
    _read: dict[str, bytes | None] = field(default_factory=dict, compare=False, repr=False)
    _omitted: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    _notes: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    def source_note(self, path: str) -> str | None:
        return self._notes.get(path)

    @property
    def label(self) -> str:
        return self.root.as_posix()

    @cached_property
    def files(self) -> CodePathSet:
        return code_tree_from_working_directory(self.root)

    def has_file(self, path: str) -> bool:
        return (self.root / path).is_file()

    def has_directory(self, path: str) -> bool:
        return (self.root / path).is_dir()

    def read(self, path: str) -> bytes | None:
        if path not in self._read:
            self._read[path] = self._load(path)
        return self._read[path]

    def _load(self, path: str) -> bytes | None:
        if path in self._omitted:
            raise SourceFileUnavailableError(self._omitted[path])
        try:
            return self._read_source(path)
        except SourceFileUnavailableError as error:
            self._omitted[path] = str(error)
            raise
        except FileNotFoundError:
            return None
        except OSError as error:
            reason = f"not indexed: cannot read source file ({error.strerror})"
            self._omitted[path] = reason
            raise SourceFileUnavailableError(reason) from error

    def report(self) -> dict[str, object]:
        """Classify the Git listing without retaining uncited file bodies.

        There is no file-count refusal: memory holds the Git path set and
        the reasons to report, while at most one uncited body is inspected
        at a time within the per-file bound.
        """
        indexed = 0
        skipped: list[dict[str, str]] = []
        for path in sorted(self.files.paths):
            try:
                data = self._read[path] if path in self._read else self._load(path)
                if data is None:
                    raise SourceFileUnavailableError("not indexed: no longer present")
                if _is_binary(data):
                    raise SourceFileUnavailableError("not indexed: binary")
            except SourceFileUnavailableError as error:
                skipped.append({"path": path, "reason": str(error)})
            else:
                indexed += 1
        return {
            "listedCount": indexed + len(skipped),
            "indexedCount": indexed,
            "skippedCount": len(skipped),
            "skippedFiles": skipped,
        }

    def _read_source(self, path: str) -> bytes | None:
        target = self.root / path
        self._check_path(path)
        if path not in self.files.paths:
            if self._ignored(path):
                self._notes[path] = "not indexed: ignored by Git"
            elif not target.exists():
                return None
        descriptor = self._open_source(path)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise SourceFileUnavailableError("not indexed: not a regular file")
            if metadata.st_size > MAX_SOURCE_BYTES:
                raise SourceFileUnavailableError("not indexed: over 4 MiB")
            data = stream.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise SourceFileUnavailableError("not indexed: over 4 MiB")
        if _is_binary(data):
            self._notes.setdefault(path, "not indexed: binary")
        return data

    def _check_path(self, path: str) -> None:
        parts = Path(path).parts
        if not parts or Path(path).is_absolute() or ".." in parts:
            raise SourceFileUnavailableError("not indexed: path outside repository")
        # Check parents first, before asking Git or inspecting a linked child.
        target = self.root
        for part in parts:
            target /= part
            if target.is_symlink():
                raise SourceFileUnavailableError("not indexed: symbolic link")

    def _open_source(self, path: str) -> int:
        # The descriptor walk also closes replacement races after _check_path.
        parts = Path(path).parts
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
                )
                os.close(directory)
                directory = child
            return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)

    def _ignored(self, path: str) -> bool:
        result = run_git(self.root, ["check-ignore", "--quiet", "--", path])
        if result.returncode not in (0, 1):
            raise SourceFileUnavailableError(
                f"not indexed: Git cannot classify path ({result.stderr.strip()})"
            )
        return result.returncode == 0


def _is_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
