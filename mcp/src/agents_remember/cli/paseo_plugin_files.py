"""The files Agents Remember owns inside the Paseo daemon home.

Everything lives under ``<home>/agents-remember/``: the installed copy of the AR plugin, the
embed list the plugin reads, and a stamp of what the running plugin last loaded. The plugin is
installed from the copy and never from the build's source tree, so the daemon references only
paths inside its own home and its content changes only when provision replaces the copy.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from agents_remember.kernel.atomic_write import atomic_write_text

PLUGIN_ID = "ar-plugin"
AR_HOME_DIRECTORY = "agents-remember"
_SKIPPED_DIRECTORIES = frozenset({"node_modules", "__pycache__"})


def plugin_source_root() -> Path:
    """The AR plugin source directory that ships inside the package."""
    return Path(__file__).resolve().parent.parent / "package_data" / "paseo_plugin"


def installed_plugin_path(home: Path) -> Path:
    return home / AR_HOME_DIRECTORY / "plugin"


def embed_path(home: Path) -> Path:
    return home / AR_HOME_DIRECTORY / "embed.json"


def loaded_stamp_path(home: Path) -> Path:
    return home / AR_HOME_DIRECTORY / "plugin-loaded.json"


def tree_digest(root: Path) -> str | None:
    """SHA-256 over the relative path and bytes of every plugin file; ``None`` when absent."""
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in _plugin_files(root):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def sync_plugin_copy(source: Path, target: Path) -> bool:
    """Make ``target`` hold exactly the plugin files of ``source``; report whether it changed."""
    if tree_digest(source) == tree_digest(target):
        return False
    staging = target.with_name(target.name + ".next")
    shutil.rmtree(staging, ignore_errors=True)
    for path in _plugin_files(source):
        destination = staging / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    return True


def write_embed(home: Path, entries: list[dict[str, str]]) -> bool:
    """Publish the embed list for the plugin; report whether the file changed."""
    if read_embed(home) == entries:
        return False
    atomic_write_text(embed_path(home), json.dumps({"embed": entries}, indent=2) + "\n")
    return True


def read_embed(home: Path) -> list[dict[str, str]] | None:
    """The embed list the plugin reads from this home; ``None`` when none was published."""
    document = _read_json(embed_path(home))
    entries = document.get("embed") if isinstance(document, dict) else None
    return entries if isinstance(entries, list) else None


def read_loaded_stamp(home: Path) -> Any:
    return _read_json(loaded_stamp_path(home))


def write_loaded_stamp(home: Path, stamp: dict[str, str | None]) -> None:
    atomic_write_text(loaded_stamp_path(home), json.dumps(stamp, indent=2) + "\n")


def _plugin_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and _SKIPPED_DIRECTORIES.isdisjoint(path.relative_to(root).parts)
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
