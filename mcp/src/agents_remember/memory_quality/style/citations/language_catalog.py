"""Data-driven hash-pinned global language catalog lock loader.

All supported catalog languages, their unambiguous suffixes/filenames/shebangs,
ambiguous suffixes, pinned wheel/pack artifacts, query hashes, wrapper node
lists, and destructuring rules live in ``package_data/citations/language_catalog.lock.json``.
No citation module hardcodes a language list.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal

from agents_remember.errors import AgentsRememberError
from agents_remember.memory_quality.style.citations.language_set import (
    LEAN,
    RESERVED_SUFFIXES,
)
from agents_remember.memory_quality.style.citations.tags_engine import (
    QUERIES_ROOT,
    DestructuringSpec,
    QualifierSpec,
)

CATALOG_SCHEMA: Final = "ar-language-catalog/v1"
CATALOG_LOCK_PATH: Final = (
    Path(__file__).resolve().parents[3]
    / "package_data"
    / "citations"
    / "language_catalog.lock.json"
)
ProviderKind = Literal["pypi_wheel", "language_pack", "builtin_lean", "display_only"]
GuaranteeTier = Literal["verified", "best_effort", "unverified"]
_GUARANTEE_TIERS: Final = frozenset({"verified", "best_effort", "unverified"})


@dataclass(frozen=True)
class QueryPin:
    """One hash-pinned tree-sitter query file path inside ``QUERIES_ROOT``."""

    path: str
    sha256: str


class LanguageCatalogError(AgentsRememberError):
    """The bundled or supplied language catalog lock is malformed or fails integrity checks."""


@dataclass(frozen=True)
class WheelArtifact:
    """One exact hash-pinned wheel artifact from PyPI."""

    filename: str
    url: str
    sha256: str


@dataclass(frozen=True)
class TreeSitterPin:
    """Core ``tree-sitter`` runtime pin and supported grammar ABI bounds."""

    package: str
    version: str
    min_abi: int
    max_abi: int


@dataclass(frozen=True)
class LanguagePackPin:
    """Pinned ``tree-sitter-language-pack`` package specification for long-tail languages."""

    package: str
    version: str
    module: str
    getter: str
    wheels: tuple[WheelArtifact, ...] = ()


@dataclass(frozen=True)
class CatalogLanguageSpec:
    """One language declared in the global hash-pinned catalog lock."""

    id: str
    provider_kind: ProviderKind
    version: str
    suffixes: frozenset[str] = frozenset()
    filenames: frozenset[str] = frozenset()
    shebangs: frozenset[str] = frozenset()
    package: str | None = None
    module: str | None = None
    symbol: str | None = None
    pack_language: str | None = None
    anchor_spelling: bool = False
    tags_query_relpath: str | None = None
    tags_query_sha256: str | None = None
    tests_query_relpath: str | None = None
    tests_query_sha256: str | None = None
    highlights_queries: tuple[QueryPin, ...] = ()
    wrappers: frozenset[str] = frozenset()
    leading_wrappers: frozenset[str] = frozenset()
    destructuring: DestructuringSpec = field(default_factory=DestructuringSpec)
    qualifiers: QualifierSpec = field(default_factory=QualifierSpec)
    guarantee_tier: GuaranteeTier = "verified"
    wheels: tuple[WheelArtifact, ...] = ()

    @property
    def grammar_version_label(self) -> str:
        if self.provider_kind in {"builtin_lean", "display_only"}:
            return self.version
        owner = self.package or self.id
        return f"{owner}={self.version}"

    def load_tags_query(self, queries_root: Path = QUERIES_ROOT) -> str:
        """Read and verify the pinned ``tags.scm`` query text for this language."""
        if self.tags_query_relpath is None or self.tags_query_sha256 is None:
            raise LanguageCatalogError(f"catalog language {self.id!r} declares no tagsQuery")
        return _read_verified_query(
            queries_root, self.tags_query_relpath, self.tags_query_sha256, self.id
        )

    def load_tests_query(self, queries_root: Path = QUERIES_ROOT) -> str | None:
        """Read and verify the optional pinned ``tests.scm`` query text for this language."""
        if self.tests_query_relpath is None:
            return None
        if self.tests_query_sha256 is None:
            raise LanguageCatalogError(
                f"catalog language {self.id!r} declares testsQuery without testsQuerySha256"
            )
        return _read_verified_query(
            queries_root, self.tests_query_relpath, self.tests_query_sha256, self.id
        )

    def load_highlights_queries(self, queries_root: Path = QUERIES_ROOT) -> tuple[str, ...]:
        """Read and verify the ordered pinned ``highlights.scm`` query texts for this language."""
        return tuple(
            _read_verified_query(queries_root, pin.path, pin.sha256, self.id)
            for pin in self.highlights_queries
        )

    def highlights_queries_digest(self) -> str | None:
        """Deterministic digest over pinned highlight query SHA-256s (independent of anchoring)."""
        if not self.highlights_queries:
            return None
        joined = "|".join(f"{pin.path}:{pin.sha256}" for pin in self.highlights_queries)
        return hashlib.sha256(joined.encode("ascii")).hexdigest()[:16]


@dataclass(frozen=True)
class LanguageCatalog:
    """Validated immutable view of the global hash-pinned language catalog lock."""

    schema: str
    tree_sitter: TreeSitterPin
    reserved_suffixes: frozenset[str]
    ambiguous_suffixes: frozenset[str]
    language_pack: LanguagePackPin
    languages: Mapping[str, CatalogLanguageSpec]
    highlight_languages: Mapping[str, CatalogLanguageSpec] = field(
        default_factory=lambda: MappingProxyType({})
    )
    display_suffixes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    by_suffix: Mapping[str, str] = field(init=False)
    by_filename: Mapping[str, str] = field(init=False)
    by_shebang: Mapping[str, str] = field(init=False)
    display_by_suffix: Mapping[str, str] = field(init=False)
    display_by_filename: Mapping[str, str] = field(init=False)

    def __post_init__(self) -> None:
        by_suffix, by_filename, by_shebang = _index_anchoring_languages(self.languages)
        display_by_suffix, display_by_filename = _index_display_languages(
            by_suffix,
            by_filename,
            self.languages,
            self.highlight_languages,
            self.display_suffixes,
        )
        object.__setattr__(self, "by_suffix", MappingProxyType(by_suffix))
        object.__setattr__(self, "by_filename", MappingProxyType(by_filename))
        object.__setattr__(self, "by_shebang", MappingProxyType(by_shebang))
        object.__setattr__(self, "display_by_suffix", MappingProxyType(display_by_suffix))
        object.__setattr__(self, "display_by_filename", MappingProxyType(display_by_filename))

    def get(self, lang_id: str) -> CatalogLanguageSpec | None:
        return self.languages.get(lang_id)

    def get_highlight_spec(self, lang_id: str) -> CatalogLanguageSpec | None:
        """Look up a language spec across anchoring and presentation-only highlight entries."""
        spec = self.languages.get(lang_id)
        return spec if spec is not None else self.highlight_languages.get(lang_id)

    def display_language_for(self, path: str | Path) -> str | None:
        """Resolve a path to its catalog display language id, or ``None`` if unlisted."""
        candidate = path if isinstance(path, Path) else Path(path)
        by_name = self.display_by_filename.get(candidate.name)
        if by_name is not None:
            return by_name
        return self.display_by_suffix.get(candidate.suffix.lower())


def _claim_unique(target: dict[str, str], key: str, lang_id: str, kind: str) -> None:
    if key in target:
        raise LanguageCatalogError(
            f"catalog {kind} {key!r} is claimed by both {target[key]!r} and {lang_id!r}"
        )
    target[key] = lang_id


def _index_anchoring_languages(
    languages: Mapping[str, CatalogLanguageSpec],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    by_suffix: dict[str, str] = {}
    by_filename: dict[str, str] = {}
    by_shebang: dict[str, str] = {}
    for lang_id, spec in languages.items():
        for suffix in spec.suffixes:
            _claim_unique(by_suffix, suffix, lang_id, "suffix")
        for filename in spec.filenames:
            _claim_unique(by_filename, filename, lang_id, "filename")
        for shebang in spec.shebangs:
            _claim_unique(by_shebang, shebang, lang_id, "shebang")
    return by_suffix, by_filename, by_shebang


def _index_display_languages(
    by_suffix: Mapping[str, str],
    by_filename: Mapping[str, str],
    languages: Mapping[str, CatalogLanguageSpec],
    highlight_languages: Mapping[str, CatalogLanguageSpec],
    display_suffixes: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    display_by_suffix = dict(by_suffix)
    display_by_filename = dict(by_filename)
    for lang_id, spec in highlight_languages.items():
        if lang_id in languages:
            raise LanguageCatalogError(
                f"highlightLanguages.{lang_id!r} duplicates anchoring languages.{lang_id!r}"
            )
        for suffix in spec.suffixes:
            _claim_unique(display_by_suffix, suffix, lang_id, "display suffix")
        for filename in spec.filenames:
            _claim_unique(display_by_filename, filename, lang_id, "display filename")
    for suffix, lang_id in display_suffixes.items():
        if lang_id not in languages and lang_id not in highlight_languages:
            raise LanguageCatalogError(
                f"displaySuffixes[{suffix!r}] targets unknown language {lang_id!r}"
            )
        _claim_unique(display_by_suffix, suffix, lang_id, "display suffix")
    return display_by_suffix, display_by_filename


def _read_verified_query(
    queries_root: Path, relpath: str, expected_sha256: str, lang_id: str
) -> str:
    query_path = (queries_root / relpath).resolve()
    root = queries_root.resolve()
    if not query_path.is_relative_to(root):
        raise LanguageCatalogError(f"catalog query path {relpath!r} escapes {queries_root}")
    if not query_path.is_file():
        raise LanguageCatalogError(
            f"catalog query file {relpath!r} for {lang_id!r} is missing at {query_path}"
        )
    raw = query_path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise LanguageCatalogError(
            f"catalog query {relpath!r} for {lang_id!r} failed SHA-256 verification: "
            f"expected {expected_sha256}, got {actual}"
        )
    return raw.decode("utf-8")


def _require_str(mapping: Mapping[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LanguageCatalogError(f"{context}.{key} must be a non-empty string")
    return value


def _require_sha256(mapping: Mapping[str, Any], key: str, context: str) -> str:
    value = _require_str(mapping, key, context)
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise LanguageCatalogError(f"{context}.{key} must be a 64-character lowercase SHA-256 hex")
    return value


def _parse_wheels(raw: object, context: str) -> tuple[WheelArtifact, ...]:
    if not isinstance(raw, list):
        raise LanguageCatalogError(f"{context} must be a list of wheel artifacts")
    artifacts: list[WheelArtifact] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise LanguageCatalogError(f"{context}[{index}] must be an object")
        item_ctx = f"{context}[{index}]"
        url = _require_str(entry, "url", item_ctx)
        if not url.startswith("https://"):
            raise LanguageCatalogError(f"{item_ctx}.url must use https://")
        artifacts.append(
            WheelArtifact(
                filename=_require_str(entry, "filename", item_ctx),
                url=url,
                sha256=_require_sha256(entry, "sha256", item_ctx),
            )
        )
    return tuple(artifacts)


def _parse_str_set(raw: object, context: str, *, require_dot_lower: bool = False) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise LanguageCatalogError(f"{context} must be a list of non-empty strings")
    if require_dot_lower:
        for item in raw:
            if not item.startswith(".") or item != item.lower():
                raise LanguageCatalogError(
                    f"{context} entry {item!r} must be lowercase and start with '.'"
                )
    return frozenset(raw)


def _parse_destructuring(raw: object, context: str) -> DestructuringSpec:
    if raw is None:
        return DestructuringSpec()
    if not isinstance(raw, dict):
        raise LanguageCatalogError(f"{context} must be an object")
    recurse_fields_raw = raw.get("recurseFields", {})
    if not isinstance(recurse_fields_raw, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in recurse_fields_raw.items()
    ):
        raise LanguageCatalogError(f"{context}.recurseFields must map strings to strings")
    return DestructuringSpec(
        leaves=_parse_str_set(raw.get("leaves", []), f"{context}.leaves"),
        recurse_children=_parse_str_set(
            raw.get("recurseChildren", []), f"{context}.recurseChildren"
        ),
        recurse_fields=MappingProxyType(dict(recurse_fields_raw)),
    )


def _parse_qualifiers(raw: object, context: str) -> QualifierSpec:
    if raw is None:
        return QualifierSpec()
    if not isinstance(raw, dict):
        raise LanguageCatalogError(f"{context} must be an object")
    seps_raw = raw.get("separators", ["."])
    if not isinstance(seps_raw, list) or not all(isinstance(s, str) and s for s in seps_raw):
        raise LanguageCatalogError(f"{context}.separators must be a list of non-empty strings")
    kinds_raw = raw.get("separatorKinds", {})
    if not isinstance(kinds_raw, dict):
        raise LanguageCatalogError(f"{context}.separatorKinds must be an object")
    separator_kinds: dict[str, frozenset[str]] = {}
    for sep, allowed in kinds_raw.items():
        if not isinstance(sep, str) or sep not in seps_raw:
            raise LanguageCatalogError(
                f"{context}.separatorKinds key {sep!r} must appear in separators"
            )
        separator_kinds[sep] = _parse_str_set(allowed, f"{context}.separatorKinds[{sep!r}]")
    return QualifierSpec(
        separators=tuple(seps_raw),
        separator_kinds=MappingProxyType(separator_kinds),
    )


def _parse_guarantee_tier(raw: object, context: str) -> GuaranteeTier:
    if raw is None:
        return "verified"
    if not isinstance(raw, str) or raw not in _GUARANTEE_TIERS:
        raise LanguageCatalogError(
            f"{context}.guaranteeTier must be one of {sorted(_GUARANTEE_TIERS)}, got {raw!r}"
        )
    return raw  # type: ignore[return-value]


@dataclass(frozen=True)
class _CatalogParseContext:
    reserved: frozenset[str]
    ambiguous: frozenset[str]
    pack: LanguagePackPin
    queries_root: Path
    verify_queries: bool


def _parse_highlights_queries(raw: object, context: str) -> tuple[QueryPin, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise LanguageCatalogError(f"{context} must be a list of query pins")
    pins: list[QueryPin] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise LanguageCatalogError(f"{context}[{index}] must be an object")
        item_ctx = f"{context}[{index}]"
        pins.append(
            QueryPin(
                path=_require_str(entry, "path", item_ctx),
                sha256=_require_sha256(entry, "sha256", item_ctx),
            )
        )
    return tuple(pins)


def _parse_highlight_only_spec(
    lang_id: str,
    raw: Mapping[str, Any],
    ctx: _CatalogParseContext,
) -> CatalogLanguageSpec:
    context = f"highlightLanguages.{lang_id}"
    kind = raw.get("providerKind")
    if kind not in {"pypi_wheel", "language_pack", "display_only"}:
        raise LanguageCatalogError(f"{context}.providerKind {kind!r} is unsupported")
    suffixes = _parse_str_set(
        raw.get("suffixes", []), f"{context}.suffixes", require_dot_lower=True
    )
    filenames = _parse_str_set(raw.get("filenames", []), f"{context}.filenames")
    shebangs = _parse_str_set(raw.get("shebangs", []), f"{context}.shebangs")
    if suffixes & ctx.reserved:
        raise LanguageCatalogError(
            f"{context} claims reserved suffix(es) {sorted(suffixes & ctx.reserved)}"
        )
    highlights_queries = _parse_highlights_queries(
        raw.get("highlightsQueries"), f"{context}.highlightsQueries"
    )
    if kind == "display_only":
        return CatalogLanguageSpec(
            id=lang_id,
            provider_kind="display_only",
            version=str(raw.get("version") or "display-only/1"),
            suffixes=suffixes,
            filenames=filenames,
            shebangs=shebangs,
        )
    package, version, module, symbol, pack_language, wheels = _parse_provider_artifact(
        lang_id, kind, raw, ctx.pack, context
    )
    spec = CatalogLanguageSpec(
        id=lang_id,
        provider_kind=kind,
        version=version,
        suffixes=suffixes,
        filenames=filenames,
        shebangs=shebangs,
        package=package,
        module=module,
        symbol=symbol,
        pack_language=pack_language,
        highlights_queries=highlights_queries,
        wheels=wheels,
    )
    if ctx.verify_queries and spec.highlights_queries:
        spec.load_highlights_queries(ctx.queries_root)
    return spec


def _parse_display_suffixes(raw: object) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise LanguageCatalogError("displaySuffixes must be an object mapping suffixes to ids")
    parsed: dict[str, str] = {}
    for suffix, lang_id in raw.items():
        if not isinstance(suffix, str) or not suffix.startswith(".") or suffix != suffix.lower():
            raise LanguageCatalogError(
                f"displaySuffixes key {suffix!r} must be lowercase and start with '.'"
            )
        if not isinstance(lang_id, str) or not lang_id.strip():
            raise LanguageCatalogError(
                f"displaySuffixes[{suffix!r}] must be a non-empty language id"
            )
        parsed[suffix] = lang_id
    return parsed


def _parse_query_pins(
    raw: Mapping[str, Any], context: str
) -> tuple[str, str, str | None, str | None]:
    tags_rel = _require_str(raw, "tagsQuery", context)
    tags_sha = _require_sha256(raw, "tagsQuerySha256", context)
    tests_rel = raw.get("testsQuery")
    tests_sha = raw.get("testsQuerySha256")
    if tests_rel is not None:
        if not isinstance(tests_rel, str) or not tests_rel.strip():
            raise LanguageCatalogError(f"{context}.testsQuery must be a non-empty string")
        tests_sha = _require_sha256(raw, "testsQuerySha256", context)
    elif tests_sha is not None:
        raise LanguageCatalogError(f"{context}.testsQuerySha256 requires testsQuery")
    return tags_rel, tags_sha, tests_rel, tests_sha


def _parse_provider_artifact(
    lang_id: str,
    kind: Literal["pypi_wheel", "language_pack"],
    raw: Mapping[str, Any],
    pack: LanguagePackPin,
    context: str,
) -> tuple[str, str, str, str, str | None, tuple[WheelArtifact, ...]]:
    if kind == "pypi_wheel":
        wheels = _parse_wheels(raw.get("wheels"), f"{context}.wheels")
        if not wheels:
            raise LanguageCatalogError(f"{context}.wheels must list at least one pinned wheel")
        return (
            _require_str(raw, "package", context),
            _require_str(raw, "version", context),
            _require_str(raw, "module", context),
            _require_str(raw, "symbol", context),
            None,
            wheels,
        )
    return (
        pack.package,
        str(raw.get("version") or pack.version),
        pack.module,
        pack.getter,
        str(raw.get("packLanguage") or lang_id),
        (),
    )


def _parse_language_spec(
    lang_id: str,
    raw: Mapping[str, Any],
    ctx: _CatalogParseContext,
) -> CatalogLanguageSpec:
    context = f"languages.{lang_id}"
    kind = raw.get("providerKind")
    if kind not in {"pypi_wheel", "language_pack", "builtin_lean"}:
        raise LanguageCatalogError(f"{context}.providerKind {kind!r} is unsupported")
    suffixes = _parse_str_set(
        raw.get("suffixes", []), f"{context}.suffixes", require_dot_lower=True
    )
    filenames = _parse_str_set(raw.get("filenames", []), f"{context}.filenames")
    shebangs = _parse_str_set(raw.get("shebangs", []), f"{context}.shebangs")
    if kind != "builtin_lean" and suffixes & ctx.reserved:
        raise LanguageCatalogError(
            f"{context} claims reserved suffix(es) {sorted(suffixes & ctx.reserved)}"
        )
    if suffixes & ctx.ambiguous:
        raise LanguageCatalogError(
            f"{context} claims ambiguous suffix(es) {sorted(suffixes & ctx.ambiguous)} by default"
        )
    if kind == "builtin_lean":
        if lang_id != LEAN:
            raise LanguageCatalogError(f"{context}: builtin_lean is reserved for {LEAN!r}")
        return CatalogLanguageSpec(
            id=lang_id,
            provider_kind="builtin_lean",
            version=_require_str(raw, "version", context),
            suffixes=suffixes,
            filenames=filenames,
            shebangs=shebangs,
        )
    tags_rel, tags_sha, tests_rel, tests_sha = _parse_query_pins(raw, context)
    highlights_queries = _parse_highlights_queries(
        raw.get("highlightsQueries"), f"{context}.highlightsQueries"
    )
    package, version, module, symbol, pack_language, wheels = _parse_provider_artifact(
        lang_id, kind, raw, ctx.pack, context
    )
    spec = CatalogLanguageSpec(
        id=lang_id,
        provider_kind=kind,
        version=version,
        suffixes=suffixes,
        filenames=filenames,
        shebangs=shebangs,
        package=package,
        module=module,
        symbol=symbol,
        pack_language=pack_language,
        anchor_spelling=bool(raw.get("anchorSpelling", False)),
        tags_query_relpath=tags_rel,
        tags_query_sha256=tags_sha,
        tests_query_relpath=tests_rel,
        tests_query_sha256=tests_sha,
        highlights_queries=highlights_queries,
        wrappers=_parse_str_set(raw.get("wrappers", []), f"{context}.wrappers"),
        leading_wrappers=_parse_str_set(
            raw.get("leadingWrappers", []), f"{context}.leadingWrappers"
        ),
        destructuring=_parse_destructuring(raw.get("destructuring"), f"{context}.destructuring"),
        qualifiers=_parse_qualifiers(raw.get("qualifiers"), f"{context}.qualifiers"),
        guarantee_tier=_parse_guarantee_tier(raw.get("guaranteeTier"), context),
        wheels=wheels,
    )
    if ctx.verify_queries:
        spec.load_tags_query(ctx.queries_root)
        spec.load_tests_query(ctx.queries_root)
        if spec.highlights_queries:
            spec.load_highlights_queries(ctx.queries_root)
    return spec


def _parse_highlight_languages_section(
    raw: object, ctx: _CatalogParseContext
) -> dict[str, CatalogLanguageSpec]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise LanguageCatalogError("highlightLanguages section must be an object")
    parsed: dict[str, CatalogLanguageSpec] = {}
    for lang_id, entry in raw.items():
        if not isinstance(lang_id, str) or not lang_id.strip() or not isinstance(entry, dict):
            raise LanguageCatalogError(
                f"highlightLanguages.{lang_id!r} must map a non-empty id to an object"
            )
        parsed[lang_id] = _parse_highlight_only_spec(lang_id, entry, ctx)
    return parsed


def parse_language_catalog(
    raw: Mapping[str, Any],
    *,
    queries_root: Path = QUERIES_ROOT,
    verify_queries: bool = True,
) -> LanguageCatalog:
    """Validate and construct a :class:`LanguageCatalog` from a decoded JSON object."""
    schema = raw.get("schema")
    if schema != CATALOG_SCHEMA:
        raise LanguageCatalogError(f"unsupported language catalog schema {schema!r}")
    ts_raw = raw.get("treeSitter")
    if not isinstance(ts_raw, dict):
        raise LanguageCatalogError("treeSitter section must be an object")
    min_abi = ts_raw.get("minAbi")
    max_abi = ts_raw.get("maxAbi")
    if not isinstance(min_abi, int) or not isinstance(max_abi, int) or min_abi > max_abi:
        raise LanguageCatalogError("treeSitter minAbi/maxAbi must be ordered integers")
    tree_sitter = TreeSitterPin(
        package=_require_str(ts_raw, "package", "treeSitter"),
        version=_require_str(ts_raw, "version", "treeSitter"),
        min_abi=min_abi,
        max_abi=max_abi,
    )
    reserved = _parse_str_set(
        raw.get("reservedSuffixes", sorted(RESERVED_SUFFIXES)),
        "reservedSuffixes",
        require_dot_lower=True,
    )
    if not RESERVED_SUFFIXES.issubset(reserved):
        raise LanguageCatalogError(f"reservedSuffixes must preserve {sorted(RESERVED_SUFFIXES)}")
    ambiguous = _parse_str_set(
        raw.get("ambiguousSuffixes", []), "ambiguousSuffixes", require_dot_lower=True
    )
    pack_raw = raw.get("languagePack")
    if not isinstance(pack_raw, dict):
        raise LanguageCatalogError("languagePack section must be an object")
    language_pack = LanguagePackPin(
        package=_require_str(pack_raw, "package", "languagePack"),
        version=_require_str(pack_raw, "version", "languagePack"),
        module=_require_str(pack_raw, "module", "languagePack"),
        getter=_require_str(pack_raw, "getter", "languagePack"),
        wheels=_parse_wheels(pack_raw.get("wheels", []), "languagePack.wheels"),
    )
    languages_raw = raw.get("languages")
    if not isinstance(languages_raw, dict) or not languages_raw:
        raise LanguageCatalogError("languages section must be a non-empty mapping")
    ctx = _CatalogParseContext(
        reserved=reserved,
        ambiguous=ambiguous,
        pack=language_pack,
        queries_root=queries_root,
        verify_queries=verify_queries,
    )
    languages: dict[str, CatalogLanguageSpec] = {}
    for lang_id, entry in languages_raw.items():
        if not isinstance(lang_id, str) or not lang_id.strip() or not isinstance(entry, dict):
            raise LanguageCatalogError(
                f"languages.{lang_id!r} must map a non-empty id to an object"
            )
        languages[lang_id] = _parse_language_spec(lang_id, entry, ctx)
    highlight_languages = _parse_highlight_languages_section(raw.get("highlightLanguages"), ctx)
    display_suffixes = _parse_display_suffixes(raw.get("displaySuffixes"))
    return LanguageCatalog(
        schema=schema,
        tree_sitter=tree_sitter,
        reserved_suffixes=reserved,
        ambiguous_suffixes=ambiguous,
        language_pack=language_pack,
        languages=MappingProxyType(languages),
        highlight_languages=MappingProxyType(highlight_languages),
        display_suffixes=MappingProxyType(display_suffixes),
    )


def catalog_lock_path() -> Path:
    """Return the path to the bundled global language catalog lock."""
    return CATALOG_LOCK_PATH


@cache
def load_language_catalog(
    lock_path: Path = CATALOG_LOCK_PATH,
    queries_root: Path = QUERIES_ROOT,
) -> LanguageCatalog:
    """Load, validate, and memoize the bundled hash-pinned language catalog lock."""
    try:
        raw = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LanguageCatalogError(
            f"cannot read language catalog lock {lock_path}: {error}"
        ) from error
    if not isinstance(raw, dict):
        raise LanguageCatalogError(f"language catalog lock {lock_path} must be a JSON object")
    return parse_language_catalog(raw, queries_root=queries_root, verify_queries=True)
