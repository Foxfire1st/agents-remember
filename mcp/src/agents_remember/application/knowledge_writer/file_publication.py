"""Publish one validated candidate and undo a partial filesystem failure (MIK-R54)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


class PublicationError(OSError):
    """The original publication failure with the complete rollback result."""

    def __init__(self, original: OSError, target: Path, failures: list[str]) -> None:
        self.rollback_failures = tuple(failures)
        detail = str(original)
        if failures:
            detail += "; rollback incomplete: " + "; ".join(failures)
        super().__init__(original.errno, detail, str(target))

    @property
    def restored(self) -> bool:
        return not self.rollback_failures


def publish_files(
    root: Path, previous: Mapping[str, bytes], files: Mapping[str, bytes]
) -> tuple[list[str], list[str]]:
    written = [path for path in sorted(files) if previous.get(path) != files[path]]
    removed = sorted(path for path in previous if path not in files)
    originals: dict[str, bytes | None] = {}
    changed: list[str] = []
    temporary: Path | None = None
    target = root
    try:
        for path in (*written, *removed):
            target = root / path
            originals[path] = target.read_bytes() if target.exists() else None
        for path in written:
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.ar-writer")
            temporary.write_bytes(files[path])
            temporary.replace(target)
            changed.append(path)
        for path in removed:
            target = root / path
            target.unlink(missing_ok=True)
            changed.append(path)
    except OSError as error:
        failures = _rollback(root, changed, originals, temporary)
        raise PublicationError(error, target, failures) from error
    return written, removed


def _rollback(
    root: Path, changed: list[str], originals: Mapping[str, bytes | None], temporary: Path | None
) -> list[str]:
    failures: list[str] = []
    for path in reversed(changed):
        destination, original = root / path, originals[path]
        try:
            if original is None:
                destination.unlink(missing_ok=True)
            else:
                destination.write_bytes(original)
        except OSError as error:
            failures.append(f"{destination}: {error}")
        try:
            actual = destination.read_bytes() if destination.exists() else None
            if actual != original:
                failures.append(f"{destination}: surviving change after rollback")
        except OSError as error:
            failures.append(f"{destination}: rollback could not be verified: {error}")
    if temporary is not None:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            failures.append(f"{temporary}: staging cleanup failed: {error}")
    return failures
