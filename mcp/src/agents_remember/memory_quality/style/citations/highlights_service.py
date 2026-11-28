"""Catalog-driven syntax highlighting service with out-of-process smoke-compile and LRU caching.

Resolves display language ids and computes :class:`SyntaxHighlights` from the single
hash-pinned language catalog plus optional per-repo ``languages.json`` custom languages.
All caches are bounded and registered in ``PROCESS_MUTABLE_STATES``. Any missing grammar,
unwarmed cache entry, or failing query degrades cleanly to ``None`` without failing reads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from tree_sitter import Language, Query, QueryError

from agents_remember.errors import AgentsRememberError, GrammarUnavailableError
from agents_remember.memory_quality.style.citations import (
    grammars,
    language_cache,
    language_catalog,
)
from agents_remember.memory_quality.style.citations.highlights_engine import (
    HIGHLIGHT_VERSION,
    compose_highlight_queries,
    compute_highlights,
)
from agents_remember.memory_quality.style.citations.language_catalog import (
    CATALOG_LOCK_PATH,
    CatalogLanguageSpec,
    LanguageCatalog,
)
from agents_remember.memory_quality.style.citations.language_set import (
    LanguageSet,
    TreeSitterLanguageProvider,
)
from agents_remember.memory_quality.style.citations.tags_engine import QUERIES_ROOT
from agents_remember.models.highlights import HIGHLIGHT_MAX_BYTES, SyntaxHighlights

__all__ = [
    "HIGHLIGHT_VERSION",
    "highlight_cache_stats",
    "highlight_text",
    "resolve_display_language",
]

logger = logging.getLogger(__name__)

_SMOKE_TIMEOUT_SECONDS: Final = 15
_VERIFIED_BUNDLE_LIMIT: Final = 256
_COMPILED_BUNDLE_LIMIT: Final = 128
_RESULT_CACHE_MAX_ENTRIES: Final = 512
_RESULT_CACHE_MAX_BYTES: Final = 16 * 1024 * 1024

_CACHE_LOCK = threading.Lock()
_VERIFIED_BUNDLES: OrderedDict[tuple[str, str, str], bool] = OrderedDict()
_COMPILED_BUNDLES: OrderedDict[tuple[str, str, str], tuple[Query, ...]] = OrderedDict()
_HIGHLIGHT_CACHE: OrderedDict[tuple[str, str, str, str], tuple[int, SyntaxHighlights | None]] = (
    OrderedDict()
)
_HIGHLIGHT_CACHE_STATS: dict[str, int] = {}


@dataclass(frozen=True)
class _HighlightTarget:
    language_id: str
    grammar_version: str
    query_texts: tuple[str, ...]
    query_digest: str
    load_language: Callable[[], Language]
    smoke_payload: dict[str, Any]

    @property
    def bundle_key(self) -> tuple[str, str, str]:
        return (self.language_id, self.grammar_version, self.query_digest)

    @property
    def version_tag(self) -> str:
        return f"{self.grammar_version}+hl:{self.query_digest}"


def _active_language_set(repo_root: Path | None) -> LanguageSet:
    if repo_root is None:
        return grammars.active_language_set()
    try:
        return grammars.repository_language_set(repo_root)
    except AgentsRememberError:
        return grammars.active_language_set()


def resolve_display_language(path: str | Path, *, repo_root: Path | None = None) -> str:
    """Resolve ``path`` to its catalog or custom language id (``text`` fallback)."""
    candidate = path if isinstance(path, Path) else Path(path)
    rel_posix = candidate.as_posix()
    lang_set = _active_language_set(repo_root)
    resolved = lang_set.grammar_of(rel_posix)
    if resolved is not None:
        return resolved
    catalog = language_catalog.load_language_catalog()
    from_catalog = catalog.display_language_for(candidate)
    if from_catalog is not None:
        return from_catalog
    return "text"


def _query_bundle_digest(queries: Sequence[str]) -> str:
    joined = "\n---\n".join(queries)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _resolve_parent_queries(
    parent_id: str,
    *,
    catalog: LanguageCatalog,
    queries_root: Path,
) -> tuple[str, ...]:
    spec = catalog.get_highlight_spec(parent_id)
    if spec is None or not spec.highlights_queries:
        return ()
    return spec.load_highlights_queries(queries_root)


def _target_from_custom_provider(
    provider: TreeSitterLanguageProvider,
    *,
    catalog: LanguageCatalog,
    queries_root: Path,
) -> _HighlightTarget | None:
    if not provider.highlights_query_texts:
        return None
    if provider.custom_library_path is None or provider.custom_symbol is None:
        return None
    composed = compose_highlight_queries(
        provider.key,
        provider.highlights_query_texts,
        lambda parent_id: _resolve_parent_queries(
            parent_id, catalog=catalog, queries_root=queries_root
        ),
    )
    if not composed:
        return None
    return _HighlightTarget(
        language_id=provider.key,
        grammar_version=provider.grammar_version,
        query_texts=composed,
        query_digest=_query_bundle_digest(composed),
        load_language=provider.load_language,
        smoke_payload={
            "kind": "custom",
            "languageId": provider.key,
            "libraryPath": provider.custom_library_path,
            "symbol": provider.custom_symbol,
            "minAbi": catalog.tree_sitter.min_abi,
            "maxAbi": catalog.tree_sitter.max_abi,
            "queries": list(composed),
        },
    )


@dataclass(frozen=True)
class HighlightOptions:
    """Optional configuration overrides for :func:`highlight_text`."""

    language: str | None = None
    repo_root: Path | None = None
    cache_root: Path | None = None
    allow_download: bool = False


@dataclass(frozen=True)
class _TargetContext:
    catalog: LanguageCatalog
    queries_root: Path
    lock_path: Path
    options: HighlightOptions


def _target_from_catalog_spec(
    spec: CatalogLanguageSpec,
    ctx: _TargetContext,
) -> _HighlightTarget | None:
    if spec.provider_kind not in {"pypi_wheel", "language_pack"} or not spec.highlights_queries:
        return None
    resolved_cache = language_cache.resolve_language_cache_root(ctx.options.cache_root)
    if not ctx.options.allow_download and not language_cache.is_cached_language_warm(
        spec, catalog=ctx.catalog, cache_root=resolved_cache
    ):
        return None
    own_queries = spec.load_highlights_queries(ctx.queries_root)
    composed = compose_highlight_queries(
        spec.id,
        own_queries,
        lambda parent_id: _resolve_parent_queries(
            parent_id, catalog=ctx.catalog, queries_root=ctx.queries_root
        ),
    )
    if not composed:
        return None

    def _load() -> Language:
        return language_cache.ensure_catalog_language(
            spec, catalog=ctx.catalog, cache_root=resolved_cache
        )

    return _HighlightTarget(
        language_id=spec.id,
        grammar_version=spec.grammar_version_label,
        query_texts=composed,
        query_digest=_query_bundle_digest(composed),
        load_language=_load,
        smoke_payload={
            "kind": "catalog",
            "languageId": spec.id,
            "lockPath": str(ctx.lock_path),
            "queriesRoot": str(ctx.queries_root),
            "cacheRoot": str(resolved_cache),
            "queries": list(composed),
        },
    )


def _resolve_target(path: Path, ctx: _TargetContext) -> _HighlightTarget | None:
    lang_set = _active_language_set(ctx.options.repo_root)
    provider = (
        lang_set.provider_for(ctx.options.language)
        if ctx.options.language is not None
        else lang_set.resolve(path.as_posix())
    )
    if isinstance(provider, TreeSitterLanguageProvider) and provider.custom_library_path:
        return _target_from_custom_provider(
            provider, catalog=ctx.catalog, queries_root=ctx.queries_root
        )

    lang_id = (
        ctx.options.language
        if ctx.options.language is not None
        else resolve_display_language(path, repo_root=ctx.options.repo_root)
    )
    spec = ctx.catalog.get_highlight_spec(lang_id)
    if spec is None:
        return None
    return _target_from_catalog_spec(spec, ctx)


def _run_smoke_compile(payload: dict[str, Any]) -> bool:
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "agents_remember.memory_quality.style.citations.highlights_smoke",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=_SMOKE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.debug("highlights smoke-compile failed to launch: %s", error)
        return False
    if proc.returncode != 0:
        logger.debug(
            "highlights smoke-compile rejected %s (rc=%d): %s",
            payload.get("languageId"),
            proc.returncode,
            proc.stderr.strip(),
        )
        return False
    return True


def _verify_bundle(target: _HighlightTarget) -> tuple[bool, tuple[Query, ...] | None]:
    key = target.bundle_key
    with _CACHE_LOCK:
        verified = _VERIFIED_BUNDLES.get(key)
        if verified is not None:
            _VERIFIED_BUNDLES.move_to_end(key)
        compiled = _COMPILED_BUNDLES.get(key)
        if compiled is not None:
            _COMPILED_BUNDLES.move_to_end(key)
    if verified is not None:
        return verified, compiled
    ok = _run_smoke_compile(target.smoke_payload)
    with _CACHE_LOCK:
        _VERIFIED_BUNDLES[key] = ok
        _VERIFIED_BUNDLES.move_to_end(key)
        while len(_VERIFIED_BUNDLES) > _VERIFIED_BUNDLE_LIMIT:
            _VERIFIED_BUNDLES.popitem(last=False)
    return ok, None


def _ensure_verified_and_compiled(
    target: _HighlightTarget,
) -> tuple[Language, tuple[Query, ...]] | None:
    ok, compiled = _verify_bundle(target)
    if not ok:
        return None
    try:
        lang = target.load_language()
    except (GrammarUnavailableError, AgentsRememberError, OSError) as error:
        logger.debug("highlights grammar load skipped for %s: %s", target.language_id, error)
        return None
    if compiled is not None:
        return lang, compiled
    key = target.bundle_key
    try:
        built = tuple(Query(lang, text) for text in target.query_texts)
    except (QueryError, ValueError) as error:
        logger.debug("highlights query compile failed for %s: %s", target.language_id, error)
        with _CACHE_LOCK:
            _VERIFIED_BUNDLES[key] = False
        return None
    with _CACHE_LOCK:
        _COMPILED_BUNDLES[key] = built
        _COMPILED_BUNDLES.move_to_end(key)
        while len(_COMPILED_BUNDLES) > _COMPILED_BUNDLE_LIMIT:
            _COMPILED_BUNDLES.popitem(last=False)
    return lang, built


def _estimate_payload_bytes(payload: SyntaxHighlights | None) -> int:
    if payload is None:
        return 64
    return 128 + len(payload.spans) * 8 + sum(len(k) for k in payload.kinds)


def _cache_get(cache_key: tuple[str, str, str, str]) -> tuple[bool, SyntaxHighlights | None]:
    with _CACHE_LOCK:
        hit = _HIGHLIGHT_CACHE.get(cache_key)
        if hit is None:
            _HIGHLIGHT_CACHE_STATS["misses"] = _HIGHLIGHT_CACHE_STATS.get("misses", 0) + 1
            return False, None
        _HIGHLIGHT_CACHE.move_to_end(cache_key)
        _HIGHLIGHT_CACHE_STATS["hits"] = _HIGHLIGHT_CACHE_STATS.get("hits", 0) + 1
        return True, hit[1]


def _cache_put(cache_key: tuple[str, str, str, str], value: SyntaxHighlights | None) -> None:
    size = _estimate_payload_bytes(value)
    with _CACHE_LOCK:
        existing = _HIGHLIGHT_CACHE.pop(cache_key, None)
        used = _HIGHLIGHT_CACHE_STATS.get("bytes", 0)
        if existing is not None:
            used -= existing[0]
        _HIGHLIGHT_CACHE[cache_key] = (size, value)
        used += size
        while (
            len(_HIGHLIGHT_CACHE) > _RESULT_CACHE_MAX_ENTRIES or used > _RESULT_CACHE_MAX_BYTES
        ) and len(_HIGHLIGHT_CACHE) > 1:
            _, (evicted_size, _) = _HIGHLIGHT_CACHE.popitem(last=False)
            used -= evicted_size
        _HIGHLIGHT_CACHE_STATS["bytes"] = max(0, used)


def highlight_cache_stats() -> dict[str, int]:
    """Snapshot of highlight LRU cache counts for diagnostics and tests."""
    with _CACHE_LOCK:
        return {
            "entries": len(_HIGHLIGHT_CACHE),
            "bytes": _HIGHLIGHT_CACHE_STATS.get("bytes", 0),
            "hits": _HIGHLIGHT_CACHE_STATS.get("hits", 0),
            "misses": _HIGHLIGHT_CACHE_STATS.get("misses", 0),
            "verifiedBundles": len(_VERIFIED_BUNDLES),
            "compiledBundles": len(_COMPILED_BUNDLES),
        }


def highlight_text(
    path: str | Path,
    text: str,
    *,
    language: str | None = None,
    repo_root: Path | None = None,
    options: HighlightOptions | None = None,
) -> SyntaxHighlights | None:
    """Compute (or return cached) UTF-16 syntax highlights for ``text`` at ``path``.

    Degrades cleanly to ``None`` when the language has no highlight query, the grammar is not
    warmed locally, ``text`` exceeds ``HIGHLIGHT_MAX_BYTES``, or compilation fails.
    """
    if not text or len(text.encode("utf-8")) > HIGHLIGHT_MAX_BYTES:
        return None
    resolved_options = (
        options if options is not None else HighlightOptions(language=language, repo_root=repo_root)
    )
    candidate = path if isinstance(path, Path) else Path(path)
    try:
        catalog = language_catalog.load_language_catalog()
        target = _resolve_target(
            candidate,
            _TargetContext(
                catalog=catalog,
                queries_root=QUERIES_ROOT,
                lock_path=CATALOG_LOCK_PATH,
                options=resolved_options,
            ),
        )
    except (AgentsRememberError, OSError) as error:
        logger.debug("highlights target resolution failed for %s: %s", candidate, error)
        return None
    if target is None:
        return None

    text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    cache_key = (HIGHLIGHT_VERSION, target.language_id, target.version_tag, text_digest)
    found, cached = _cache_get(cache_key)
    if found:
        return cached

    loaded = _ensure_verified_and_compiled(target)
    if loaded is None:
        return None
    lang, compiled_queries = loaded
    result = compute_highlights(
        lang,
        compiled_queries,
        text,
        language_id=target.language_id,
        version_tag=target.version_tag,
    )
    _cache_put(cache_key, result)
    return result
