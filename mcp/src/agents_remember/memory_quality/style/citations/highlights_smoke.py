"""Out-of-process smoke compiler for tree-sitter highlight query bundles.

Invoked by :mod:`highlights_service` before compiling any catalog or custom highlight query
bundle in-process so a malformed query or incompatible native grammar fails cleanly in a
short-lived child process instead of crashing the serving host.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from tree_sitter import Query

from agents_remember.memory_quality.style.citations import (
    language_cache,
    language_catalog,
)


def _smoke_compile(payload: dict[str, Any]) -> None:
    kind = payload["kind"]
    queries: list[str] = payload["queries"]
    if not queries:
        raise ValueError("empty query bundle")
    if kind == "catalog":
        lock_path = Path(payload["lockPath"])
        queries_root = Path(payload["queriesRoot"])
        cache_root = Path(payload["cacheRoot"])
        catalog = language_catalog.load_language_catalog(
            lock_path=lock_path, queries_root=queries_root
        )
        spec = catalog.get_highlight_spec(payload["languageId"])
        if spec is None:
            raise LookupError(f"unknown catalog highlight language {payload['languageId']!r}")
        lang = language_cache.ensure_catalog_language(spec, catalog=catalog, cache_root=cache_root)
    elif kind == "custom":
        lang = language_cache.load_custom_library_language(
            Path(payload["libraryPath"]),
            payload["symbol"],
            language_id=payload["languageId"],
            min_abi=int(payload["minAbi"]),
            max_abi=int(payload["maxAbi"]),
        )
    else:
        raise ValueError(f"unsupported smoke-compile kind {kind!r}")
    compiled = [Query(lang, text) for text in queries]
    if len(compiled) != len(queries):
        raise RuntimeError("highlight query compilation count mismatch")


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        if not isinstance(payload, dict):
            return 2
        _smoke_compile(payload)
    except Exception as error:
        sys.stderr.write(f"highlights smoke-compile failed: {error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
