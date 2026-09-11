#!/usr/bin/env python3
"""Validate and report one exact Agents Remember Python runtime."""

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-base-prefix")
    parser.add_argument("--require-linux-pidfd", action="store_true")
    parser.add_argument("--source-url")
    parser.add_argument("--source-sha256")
    parser.add_argument("--builder-commit")
    return parser


def _refuse(detail: str) -> None:
    raise SystemExit(f"Agents Remember Python runtime refusal: {detail}")


def main() -> None:
    args = _parser().parse_args()
    expected = tuple(int(part) for part in args.expected_version.split("."))
    if len(expected) != 3:
        _refuse("--expected-version must be an exact major.minor.patch version")
    if sys.version_info[:3] != expected:
        _refuse(
            f"expected Python {args.expected_version}, observed "
            f"{'.'.join(str(part) for part in sys.version_info[:3])} at {sys.executable}"
        )

    base_prefix = Path(sys.base_prefix).resolve()
    if args.expected_base_prefix is not None:
        expected_prefix = Path(args.expected_base_prefix).expanduser().resolve()
        if base_prefix != expected_prefix:
            _refuse(f"expected base prefix {expected_prefix}, observed {base_prefix}")

    pidfd_open = callable(getattr(os, "pidfd_open", None))
    pidfd_send_signal = callable(getattr(signal, "pidfd_send_signal", None))
    if args.require_linux_pidfd:
        if sys.platform != "linux":
            _refuse("native pidfd proof was requested on a non-Linux interpreter")
        if not pidfd_open or not pidfd_send_signal:
            _refuse(
                "the Linux interpreter lacks os.pidfd_open or signal.pidfd_send_signal; "
                "run scripts/bootstrap-mcp-venv.sh to install the canonical source build"
            )

    provenance = {
        "sourceUrl": args.source_url,
        "sourceSha256": args.source_sha256,
        "builderCommit": args.builder_commit,
    }
    build_identity = {
        **provenance,
        "compiler": platform.python_compiler(),
        "configureArgs": sysconfig.get_config_var("CONFIG_ARGS"),
        "version": platform.python_version(),
    }
    build_fingerprint = hashlib.sha256(
        json.dumps(build_identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    report = {
        "schema": "ar-python-runtime-proof/v1",
        "version": platform.python_version(),
        "versionDetail": sys.version,
        "executable": str(Path(sys.executable).resolve()),
        "basePrefix": str(base_prefix),
        "implementation": platform.python_implementation(),
        "compiler": platform.python_compiler(),
        "platform": sys.platform,
        "configureArgs": sysconfig.get_config_var("CONFIG_ARGS"),
        "buildFingerprint": build_fingerprint,
        "provenance": provenance,
        "capabilities": {
            "os.pidfd_open": pidfd_open,
            "signal.pidfd_send_signal": pidfd_send_signal,
        },
        "modules": {
            "bz2": bz2.__file__,
            "ctypes": ctypes.__file__,
            "lzma": lzma.__file__,
            "readline": readline.__file__,
            "sqlite3": sqlite3.sqlite_version,
            "ssl": ssl.OPENSSL_VERSION,
            "zlib": zlib.ZLIB_VERSION,
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
