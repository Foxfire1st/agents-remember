"""Lossless Git text primitives shared by publication and message cleanup."""

from __future__ import annotations


def _transport_safe_git_diagnostic(text: str) -> str:
    """Render surrogateescaped Git bytes without leaking invalid Unicode to MCP."""

    return text.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _nul_records(raw: str) -> list[str]:
    """The NUL-delimited records of one ``-z`` read. No record of these commands is empty."""

    return [record for record in raw.split("\0") if record]
