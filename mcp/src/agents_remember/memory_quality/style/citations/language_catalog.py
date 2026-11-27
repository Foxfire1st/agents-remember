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
)

CATALOG_SCHEMA: Final = "ar-language-catalog/v1"
CATALOG_LOCK_PATH: Final = (
    Path(__file__).resolve().parents[3]
    / "package_data"
    / "citations"
    / "language_catalog.lock.json"
)
ProviderKind = Literal["pypi_wheel", "language_pack", "builtin_lean"]


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
    wrappers: frozenset[str] = frozenset()
    destructuring: DestructuringSpec = field(default_factory=DestructuringSpec)
    wheels: tuple[WheelArtifact, ...] = ()

    @property
    def grammar_version_label(self) -> str:
        if self.provider_kind == "builtin_lean":
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


@dataclass(frozen=True)
class LanguageCatalog:
    """Validated immutable view of the global hash-pinned language catalog lock."""

    schema: str
    tree_sitter: TreeSitterPin
    reserved_suffixes: frozenset[str]
    ambiguous_suffixes: frozenset[str]
    language_pack: LanguagePackPin
    languages: Mapping[str, CatalogLanguageSpec]
    by_suffix: Mapping[str, str] = field(init=False)
    by_filename: Mapping[str, str] = field(init=False)
    by_shebang: Mapping[str, str] = field(init=False)

    def __post_init__(self) -> None:
        by_suffix: dict[str, str] = {}
        by_filename: dict[str, str] = {}
        by_shebang: dict[str, str] = {}
        for lang_id, spec in self.languages.items():
            for suffix in spec.suffixes:
                _claim_unique(by_suffix, suffix, lang_id, "suffix")
            for filename in spec.filenames:
                _claim_unique(by_filename, filename, lang_id, "filename")
            for shebang in spec.shebangs:
                _claim_unique(by_shebang, shebang, lang_id, "shebang")
        object.__setattr__(self, "by_suffix", MappingProxyType(by_suffix))
        object.__setattr__(self, "by_filename", MappingProxyType(by_filename))
        object.__setattr__(self, "by_shebang", MappingProxyType(by_shebang))

    def get(self, lang_id: str) -> CatalogLanguageSpec | None:
        return self.languages.get(lang_id)


def _claim_unique(target: dict[str, str], key: str, lang_id: str, kind: str) -> None:
    if key in target:
        raise LanguageCatalogError(
            f"catalog {kind} {key!r} is claimed by both {target[key]!r} and {lang_id!r}"
        )
    target[key] = lang_id


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


@dataclass(frozen=True)
class _CatalogParseContext:
    reserved: frozenset[str]
    ambiguous: frozenset[str]
    pack: LanguagePackPin
    queries_root: Path
    verify_queries: bool


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
        wrappers=_parse_str_set(raw.get("wrappers", []), f"{context}.wrappers"),
        destructuring=_parse_destructuring(raw.get("destructuring"), f"{context}.destructuring"),
        wheels=wheels,
    )
    if ctx.verify_queries:
        spec.load_tags_query(ctx.queries_root)
        spec.load_tests_query(ctx.queries_root)
    return spec


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
    return LanguageCatalog(
        schema=schema,
        tree_sitter=tree_sitter,
        reserved_suffixes=reserved,
        ambiguous_suffixes=ambiguous,
        language_pack=language_pack,
        languages=MappingProxyType(languages),
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
