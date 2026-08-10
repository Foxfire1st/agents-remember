"""Helpers for invoking package-local command-style modules."""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Callable
from typing import Any


def run_package_main(
    *,
    operation: str,
    main: Callable[[list[str] | None], int],
    argv: list[str],
) -> dict[str, Any]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = 1
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            exit_code = int(main(argv))
        except SystemExit as error:
            code = error.code
            exit_code = code if isinstance(code, int) else 1

    stdout_text = stdout.getvalue()
    stderr_text = stderr.getvalue()
    payload = _json_payload(stdout_text)
    return {
        "ok": exit_code == 0,
        "operation": operation,
        "returncode": exit_code,
        "argv": argv,
        "payload": payload,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }


def _json_payload(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
