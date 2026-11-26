"""Read the uv-managed Python base without resolving its minor-version link."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path


def read_venv_home(venv: Path) -> Path | None:
    """Return the home written by the environment producer."""
    try:
        lines = (venv / "pyvenv.cfg").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    for line in lines:
        key, _, value = line.partition("=")
        if key.strip() == "home" and value.strip():
            return Path(value.strip())
    return None


def uv_python_directory(environ: Mapping[str, str] = os.environ) -> Path:
    """Apply uv's Python storage precedence for Linux and macOS."""
    if install_dir := environ.get("UV_PYTHON_INSTALL_DIR"):
        return Path(install_dir).expanduser().absolute()
    data_home = environ.get("XDG_DATA_HOME")
    user_home = Path(environ["HOME"]) if "HOME" in environ else Path.home()
    data_dir = Path(data_home) if data_home else user_home / ".local" / "share"
    return data_dir.absolute() / "uv" / "python"


def is_uv_minor_base(base: Path, minor: str) -> bool:
    """Check the lexical uv minor path and that its link reaches a patch there."""
    prefix = f"cpython-{minor}"
    if base.parent != uv_python_directory() or not base.name.startswith(f"{prefix}-"):
        return False
    if not base.is_symlink():
        return False
    target = base.readlink()
    platform_key = base.name.removeprefix(f"{prefix}-")
    patch_name = rf"{re.escape(prefix)}\.\d+-{re.escape(platform_key)}"
    target_directory = target.parent if target.is_absolute() else base.parent / target.parent
    return (
        target_directory == base.parent
        and re.fullmatch(patch_name, target.name) is not None
        and base.is_dir()
    )
