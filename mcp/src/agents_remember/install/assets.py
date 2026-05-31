"""Access package-owned runtime and benchmark assets."""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

PACKAGE_DATA_ROOT = "package_data"


def long_path(path: Path, *, resolve: bool = True) -> Path:
    if sys.platform != "win32":
        return path

    if resolve:
        resolved = path.resolve()
    elif path.is_absolute():
        resolved = path
    else:
        resolved = Path.cwd() / path
    text = str(resolved)
    if text.startswith("\\\\?\\"):
        return resolved
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text.lstrip("\\"))
    return Path("\\\\?\\" + text)


@contextmanager
def packaged_source_root() -> Iterator[Path]:
    """Yield a filesystem path containing packaged runtime and benchmark assets."""

    root = resources.files("agents_remember").joinpath(PACKAGE_DATA_ROOT)
    if isinstance(root, Path):
        yield root
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        destination = Path(temp_dir) / PACKAGE_DATA_ROOT
        copy_traversable_tree(root, destination)
        yield destination


def copy_traversable_tree(source: Traversable, destination: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"packaged asset root is missing: {source}")

    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            copy_traversable_tree(child, target)
        elif child.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            with child.open("rb") as source_file, target.open("wb") as target_file:
                shutil.copyfileobj(source_file, target_file)
