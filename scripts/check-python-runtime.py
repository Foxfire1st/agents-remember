#!/usr/bin/env python3
"""Validate and report an Agents Remember uv-managed Python runtime."""

from __future__ import annotations

import argparse
import bz2
import ctypes
import hashlib
import json
import lzma
import os
import platform
import readline
import signal
import sqlite3
import ssl
import sys
import sysconfig
import zlib
from pathlib import Path
from typing import NoReturn

# The managed base can run this proof before the package is installed.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp" / "src"))
from agents_remember.python_runtime import is_uv_minor_base, read_venv_home


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-base-prefix", type=Path)
    parser.add_argument("--base-interpreter", action="store_true")
    parser.add_argument("--require-linux-pidfd", action="store_true")
    parser.add_argument("--uv-version")
    parser.add_argument("--uv-image-digest")
    return parser


def _refuse(detail: str) -> NoReturn:
    raise SystemExit(f"Agents Remember Python runtime refusal: {detail}")


def _check_version(minor: str) -> None:
    parts = minor.split(".")
    if len(parts) != 2 or not all(part.isascii() and part.isdecimal() for part in parts):
        _refuse("--expected-version must be a major.minor version")
    if sys.version_info[:2] != tuple(int(part) for part in parts):
        observed = ".".join(str(part) for part in sys.version_info[:3])
        _refuse(f"expected Python {minor}, observed {observed} at {sys.executable}")


def _base_prefix(args: argparse.Namespace) -> Path:
    if args.base_interpreter:
        base = Path(sys.executable).parent.parent
    else:
        home = read_venv_home(Path(sys.prefix))
        if home is None or home.name != "bin":
            _refuse(
                f"expected a uv-managed Python base in {sys.prefix}/pyvenv.cfg, observed {home}"
            )
        base = home.parent
    if not is_uv_minor_base(base, args.expected_version):
        _refuse(f"expected a uv-managed Python {args.expected_version} minor link, observed {base}")
    if args.expected_base_prefix is not None and base != args.expected_base_prefix:
        _refuse(f"expected base prefix {args.expected_base_prefix}, observed {base}")
    return base


def _pidfd_capabilities(*, required: bool) -> dict[str, bool]:
    capabilities: dict[str, bool] = {
        "os.pidfd_open": callable(getattr(os, "pidfd_open", None)),
        "signal.pidfd_send_signal": callable(getattr(signal, "pidfd_send_signal", None)),
    }
    if required:
        if sys.platform != "linux":
            _refuse("native pidfd proof was requested on a non-Linux interpreter")
        if not all(capabilities.values()):
            _refuse(
                "the Linux interpreter lacks os.pidfd_open or signal.pidfd_send_signal. "
                "Run scripts/bootstrap-mcp-venv.sh to install the required uv-managed Python."
            )
    return capabilities


def _read_build(base: Path) -> str | None:
    try:
        return (base / "BUILD").read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def main() -> None:
    args = _parser().parse_args()
    _check_version(args.expected_version)
    base = _base_prefix(args)
    capabilities = _pidfd_capabilities(required=args.require_linux_pidfd)
    provenance: dict[str, str | None] = {
        "uvVersion": args.uv_version,
        "pythonBuild": _read_build(base),
    }
    if args.uv_image_digest is not None:
        provenance["uvImageDigest"] = args.uv_image_digest
    build_identity = {
        **provenance,
        "compiler": platform.python_compiler(),
        "configureArgs": sysconfig.get_config_var("CONFIG_ARGS"),
        "version": platform.python_version(),
    }
    build_fingerprint = hashlib.sha256(
        json.dumps(build_identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    readline_origin = getattr(readline, "__file__", "built-in")
    report = {
        "schema": "ar-python-runtime-proof/v1",
        "version": platform.python_version(),
        "versionDetail": sys.version,
        "executable": str(Path(sys.executable).resolve()),
        "basePrefix": str(base),
        "implementation": platform.python_implementation(),
        "compiler": platform.python_compiler(),
        "platform": sys.platform,
        "configureArgs": sysconfig.get_config_var("CONFIG_ARGS"),
        "buildFingerprint": build_fingerprint,
        "provenance": provenance,
        "capabilities": capabilities,
        "modules": {
            "bz2": bz2.__file__,
            "ctypes": ctypes.__file__,
            "lzma": lzma.__file__,
            "readline": readline_origin,
            "sqlite3": sqlite3.sqlite_version,
            "ssl": ssl.OPENSSL_VERSION,
            "zlib": zlib.ZLIB_VERSION,
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
