"""Optional per-repo ``languages.json`` and user settings language configuration.

Supports ambiguous-suffix / path-glob overrides, enabling or disabling catalog
languages, and registering user-local custom tree-sitter shared libraries (for
example Bend2).

Security boundary: remote URLs are unconditionally rejected in ``languages.json``
and custom language specifications. Downloads occur strictly through the bundled
hash-pinned catalog lock; custom shared libraries are local paths only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.primitives.user_settings_location import (
    UserSettingsLocationError,
    user_settings_path,
)
from agents_remember.memory_quality.style.citations.language_set import (
    LEAN,
    RESERVED_SUFFIXES,
)
from agents_remember.memory_quality.style.citations.tags_engine import DestructuringSpec

REPO_LANGUAGES_FILENAME: Final = "languages.json"
USER_LANGUAGES_KEY: Final = "languages"

_ALLOWED_TOP_KEYS: Final = frozenset({"globs", "suffixes", "enable", "disable", "custom"})
_ALLOWED_CUSTOM_KEYS: Final = frozenset(
    {
        "id",
        "suffixes",
        "globs",
        "libraryPath",
        "symbol",
        "tagsQueryPath",
        "testsQueryPath",
        "highlightsQueryPath",
        "wrappers",
        "destructuring",
    }
)
_FORBIDDEN_URL_KEYS: Final = frozenset({"url", "downloadUrl", "wheelUrl", "remoteUrl", "sourceUrl"})
_REMOTE_URL_RE: Final = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*://|//)")


class LanguageConfigError(AgentsRememberError):
    """A per-repo ``languages.json`` or user ``languages`` config block is invalid or unsafe."""


@dataclass(frozen=True)
class CustomLanguageSpec:
    """One user-local custom tree-sitter language registration."""

    id: str
    library_path: Path
    symbol: str
    tags_query_path: Path
    suffixes: frozenset[str] = frozenset()
    globs: tuple[str, ...] = ()
    tests_query_path: Path | None = None
    highlights_query_path: Path | None = None
    wrappers: frozenset[str] = frozenset()
    destructuring: DestructuringSpec = field(default_factory=DestructuringSpec)
    source: str = ""

    def content_version(self) -> str:
        """Deterministic version string hashing local binary bytes for ``EXTRACTOR_VERSION``."""
        if not self.library_path.is_file():
            return f"custom:{self.id}:missing"
        digest = hashlib.sha256(self.library_path.read_bytes()).hexdigest()[:16]
        return f"custom:{self.id}:{digest}"

    def load_tags_query(self) -> str:
        try:
            return self.tags_query_path.read_text(encoding="utf-8")
        except OSError as error:
            raise LanguageConfigError(
                f"custom language {self.id!r} tagsQueryPath {self.tags_query_path} could not be read: {error}"
            ) from error

    def load_tests_query(self) -> str | None:
        if self.tests_query_path is None:
            return None
        try:
            return self.tests_query_path.read_text(encoding="utf-8")
        except OSError as error:
            raise LanguageConfigError(
                f"custom language {self.id!r} testsQueryPath {self.tests_query_path} could not be read: {error}"
            ) from error

    def load_highlights_query(self) -> str | None:
        if self.highlights_query_path is None:
            return None
        try:
            return self.highlights_query_path.read_text(encoding="utf-8")
        except OSError as error:
            raise LanguageConfigError(
                f"custom language {self.id!r} highlightsQueryPath {self.highlights_query_path} "
                f"could not be read: {error}"
            ) from error


@dataclass(frozen=True)
class LanguageOverrides:
    """Merged repository and user language overrides."""

    globs: tuple[tuple[str, str], ...] = ()
    suffixes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    enable: frozenset[str] = frozenset()
    disable: frozenset[str] = frozenset()
    custom: tuple[CustomLanguageSpec, ...] = ()
    sources: tuple[str, ...] = ()


def load_language_overrides(
    repo_root: Path | None = None,
    *,
    user_settings_file: Path | None = None,
    include_user_settings: bool = True,
) -> LanguageOverrides:
    """Load and merge optional user ``settings.json#languages`` and repo ``languages.json``.

    Repository config merges over user settings for globs/suffixes/enable/disable/custom
    entries (by language id), while preserving the strict security boundary in both layers.
    """
    layers: list[LanguageOverrides] = []
    if include_user_settings:
        user_path = _resolve_user_settings_file(user_settings_file)
        if user_path is not None and user_path.is_file():
            user_layer = _load_user_languages_block(user_path)
            if user_layer is not None:
                layers.append(user_layer)
    if repo_root is not None:
        repo_file = repo_root / REPO_LANGUAGES_FILENAME
        if repo_file.is_file():
            layers.append(load_repo_languages_file(repo_file, base_dir=repo_root))
    return _merge_overrides(layers)


def load_repo_languages_file(path: Path, *, base_dir: Path | None = None) -> LanguageOverrides:
    """Parse and validate a single ``languages.json`` file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LanguageConfigError(f"cannot parse {path}: {error}") from error
    if not isinstance(raw, dict):
        raise LanguageConfigError(f"{path} must contain a JSON object")
    return parse_language_overrides(
        raw,
        source=str(path),
        base_dir=path.parent if base_dir is None else base_dir,
    )


def parse_language_overrides(
    raw: Mapping[str, Any],
    *,
    source: str,
    base_dir: Path,
) -> LanguageOverrides:
    """Validate one ``languages`` configuration mapping."""
    _refuse_unknown_and_url_keys(raw, _ALLOWED_TOP_KEYS, source, "languages")
    globs = _parse_globs(raw.get("globs"), source)
    suffixes = _parse_suffixes(raw.get("suffixes"), source)
    enable = _parse_id_set(raw.get("enable"), f"{source}: enable")
    disable = _parse_id_set(raw.get("disable"), f"{source}: disable")
    if LEAN in disable:
        raise LanguageConfigError(
            f"{source}: built-in reserved language {LEAN!r} cannot be disabled"
        )
    custom = _parse_custom_languages(raw.get("custom"), source=source, base_dir=base_dir)
    return LanguageOverrides(
        globs=globs,
        suffixes=MappingProxyType(suffixes),
        enable=enable,
        disable=disable,
        custom=custom,
        sources=(source,),
    )


def _resolve_user_settings_file(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    try:
        return user_settings_path()
    except UserSettingsLocationError:
        return None


def _load_user_languages_block(path: Path) -> LanguageOverrides | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LanguageConfigError(f"cannot parse user settings {path}: {error}") from error
    if not isinstance(data, dict):
        raise LanguageConfigError(f"user settings {path} must be a JSON object")
    block = data.get(USER_LANGUAGES_KEY)
    if block is None:
        return None
    if not isinstance(block, dict):
        raise LanguageConfigError(f"{path}: {USER_LANGUAGES_KEY} must be a JSON object")
    return parse_language_overrides(
        block, source=f"{path}#{USER_LANGUAGES_KEY}", base_dir=path.parent
    )


def _refuse_unknown_and_url_keys(
    raw: Mapping[str, Any], allowed: frozenset[str], source: str, scope: str
) -> None:
    for key in sorted(raw):
        if key in _FORBIDDEN_URL_KEYS:
            raise LanguageConfigError(
                f"{source}: {scope}.{key} is forbidden; remote binary downloads are never permitted "
                "from languages.json (custom grammars must use local paths only)"
            )
        if key not in allowed:
            raise LanguageConfigError(f"{source}: unknown key {scope}.{key!r}")


def _refuse_remote_url(value: str, field_name: str, source: str) -> None:
    stripped = value.strip()
    if _REMOTE_URL_RE.match(stripped):
        raise LanguageConfigError(
            f"{source}: {field_name} rejects remote URL {value!r}; "
            "custom language artifacts must be user-local filesystem paths"
        )


def _refuse_reserved_suffix(suffix: str, source: str, owner: str) -> None:
    if suffix.lower() in RESERVED_SUFFIXES:
        raise LanguageConfigError(
            f"{source}: {owner} cannot override reserved suffix {suffix!r} "
            "(permanently reserved for the built-in Lean scanner)"
        )


def _refuse_reserved_glob(pattern: str, source: str, owner: str) -> None:
    lower = pattern.lower().strip()
    if any(lower.endswith(suffix) for suffix in RESERVED_SUFFIXES):
        raise LanguageConfigError(
            f"{source}: {owner} glob {pattern!r} cannot target reserved Lean suffix"
        )


def _parse_globs(raw: object, source: str) -> tuple[tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise LanguageConfigError(
            f"{source}: globs must be an object mapping glob patterns to language ids"
        )
    items: list[tuple[str, str]] = []
    for pattern, lang_id in raw.items():
        if not isinstance(pattern, str) or not pattern.strip():
            raise LanguageConfigError(f"{source}: globs keys must be non-empty pattern strings")
        if not isinstance(lang_id, str) or not lang_id.strip():
            raise LanguageConfigError(
                f"{source}: globs[{pattern!r}] must be a non-empty language id"
            )
        _refuse_reserved_glob(pattern, source, f"globs[{pattern!r}]")
        items.append((pattern, lang_id))
    return tuple(items)


def _parse_suffixes(raw: object, source: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise LanguageConfigError(
            f"{source}: suffixes must be an object mapping suffixes to language ids"
        )
    parsed: dict[str, str] = {}
    for suffix, lang_id in raw.items():
        if not isinstance(suffix, str) or not suffix.startswith(".") or suffix != suffix.lower():
            raise LanguageConfigError(
                f"{source}: suffix override {suffix!r} must be lowercase and start with '.'"
            )
        if not isinstance(lang_id, str) or not lang_id.strip():
            raise LanguageConfigError(
                f"{source}: suffixes[{suffix!r}] must be a non-empty language id"
            )
        _refuse_reserved_suffix(suffix, source, f"suffixes[{suffix!r}]")
        parsed[suffix] = lang_id
    return parsed


def _parse_id_set(raw: object, context: str) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise LanguageConfigError(f"{context} must be a list of non-empty language ids")
    return frozenset(raw)


def _resolve_local_path(raw_value: object, field_name: str, source: str, base_dir: Path) -> Path:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise LanguageConfigError(f"{source}: {field_name} must be a non-empty local path")
    _refuse_remote_url(raw_value, field_name, source)
    candidate = Path(raw_value).expanduser()
    return candidate.resolve() if candidate.is_absolute() else (base_dir / candidate).resolve()


def _collect_custom_entries(raw: object, source: str) -> list[tuple[str, Mapping[str, Any]]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        entries: list[tuple[str, Mapping[str, Any]]] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise LanguageConfigError(f"{source}: custom[{index}] must be an object")
            entries.append((f"custom[{index}]", item))
        return entries
    if isinstance(raw, dict):
        entries = []
        for key, item in raw.items():
            if not isinstance(item, dict):
                raise LanguageConfigError(f"{source}: custom.{key} must be an object")
            entries.append((f"custom.{key}", {"id": key, **item}))
        return entries
    raise LanguageConfigError(
        f"{source}: custom must be a list or mapping of custom language specs"
    )


def _parse_custom_selectors(
    entry: Mapping[str, Any], *, lang_id: str, source: str, scope: str
) -> tuple[frozenset[str], tuple[str, ...]]:
    suffixes_raw = entry.get("suffixes", [])
    if not isinstance(suffixes_raw, list) or not all(
        isinstance(item, str) and item.startswith(".") and item == item.lower()
        for item in suffixes_raw
    ):
        raise LanguageConfigError(
            f"{source}: {scope}.suffixes must be a list of lowercase '.'-prefixed suffixes"
        )
    for suffix in suffixes_raw:
        _refuse_reserved_suffix(suffix, source, f"{scope}.suffixes")

    globs_raw = entry.get("globs", [])
    if not isinstance(globs_raw, list) or not all(
        isinstance(item, str) and item.strip() for item in globs_raw
    ):
        raise LanguageConfigError(f"{source}: {scope}.globs must be a list of non-empty patterns")
    for pattern in globs_raw:
        _refuse_reserved_glob(pattern, source, f"{scope}.globs")
    if not suffixes_raw and not globs_raw:
        raise LanguageConfigError(
            f"{source}: {scope} ({lang_id!r}) must declare at least one suffix or glob"
        )
    return frozenset(suffixes_raw), tuple(globs_raw)


def _parse_one_custom_language(
    entry: Mapping[str, Any], *, scope: str, source: str, base_dir: Path
) -> CustomLanguageSpec:
    _refuse_unknown_and_url_keys(entry, _ALLOWED_CUSTOM_KEYS, source, scope)
    lang_id = entry.get("id")
    if not isinstance(lang_id, str) or not lang_id.strip():
        raise LanguageConfigError(f"{source}: {scope}.id must be a non-empty string")
    if lang_id == LEAN:
        raise LanguageConfigError(f"{source}: {scope}.id cannot reuse reserved id {LEAN!r}")
    suffixes, globs = _parse_custom_selectors(entry, lang_id=lang_id, source=source, scope=scope)
    symbol = entry.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise LanguageConfigError(f"{source}: {scope}.symbol must be a non-empty C symbol name")
    library_path = _resolve_local_path(
        entry.get("libraryPath"), f"{scope}.libraryPath", source, base_dir
    )
    tags_query_path = _resolve_local_path(
        entry.get("tagsQueryPath"), f"{scope}.tagsQueryPath", source, base_dir
    )
    tests_raw = entry.get("testsQueryPath")
    tests_query_path = (
        None
        if tests_raw is None
        else _resolve_local_path(tests_raw, f"{scope}.testsQueryPath", source, base_dir)
    )
    highlights_raw = entry.get("highlightsQueryPath")
    highlights_query_path = (
        None
        if highlights_raw is None
        else _resolve_local_path(highlights_raw, f"{scope}.highlightsQueryPath", source, base_dir)
    )
    wrappers_raw = entry.get("wrappers", [])
    if not isinstance(wrappers_raw, list) or not all(
        isinstance(item, str) and item for item in wrappers_raw
    ):
        raise LanguageConfigError(f"{source}: {scope}.wrappers must be a list of node type strings")
    return CustomLanguageSpec(
        id=lang_id,
        library_path=library_path,
        symbol=symbol,
        tags_query_path=tags_query_path,
        suffixes=suffixes,
        globs=globs,
        tests_query_path=tests_query_path,
        highlights_query_path=highlights_query_path,
        wrappers=frozenset(wrappers_raw),
        destructuring=_parse_custom_destructuring(entry.get("destructuring"), source, scope),
        source=source,
    )


def _parse_custom_languages(
    raw: object, *, source: str, base_dir: Path
) -> tuple[CustomLanguageSpec, ...]:
    seen_ids: set[str] = set()
    parsed: list[CustomLanguageSpec] = []
    for scope, entry in _collect_custom_entries(raw, source):
        spec = _parse_one_custom_language(entry, scope=scope, source=source, base_dir=base_dir)
        if spec.id in seen_ids:
            raise LanguageConfigError(f"{source}: duplicate custom language id {spec.id!r}")
        seen_ids.add(spec.id)
        parsed.append(spec)
    return tuple(parsed)


def _parse_custom_destructuring(raw: object, source: str, scope: str) -> DestructuringSpec:
    if raw is None:
        return DestructuringSpec()
    if not isinstance(raw, dict):
        raise LanguageConfigError(f"{source}: {scope}.destructuring must be an object")
    leaves = raw.get("leaves", [])
    recurse_children = raw.get("recurseChildren", [])
    recurse_fields = raw.get("recurseFields", {})
    if not isinstance(leaves, list) or not all(isinstance(x, str) and x for x in leaves):
        raise LanguageConfigError(
            f"{source}: {scope}.destructuring.leaves must be a list of strings"
        )
    if not isinstance(recurse_children, list) or not all(
        isinstance(x, str) and x for x in recurse_children
    ):
        raise LanguageConfigError(
            f"{source}: {scope}.destructuring.recurseChildren must be a list of strings"
        )
    if not isinstance(recurse_fields, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in recurse_fields.items()
    ):
        raise LanguageConfigError(
            f"{source}: {scope}.destructuring.recurseFields must map strings to strings"
        )
    return DestructuringSpec(
        leaves=frozenset(leaves),
        recurse_children=frozenset(recurse_children),
        recurse_fields=MappingProxyType(dict(recurse_fields)),
    )


def _merge_overrides(layers: list[LanguageOverrides]) -> LanguageOverrides:
    if not layers:
        return LanguageOverrides()
    globs: dict[str, str] = {}
    suffixes: dict[str, str] = {}
    enable: set[str] = set()
    disable: set[str] = set()
    custom: dict[str, CustomLanguageSpec] = {}
    sources: list[str] = []
    for layer in layers:
        for pattern, lang_id in layer.globs:
            globs[pattern] = lang_id
        suffixes.update(layer.suffixes)
        enable = (enable | set(layer.enable)) - set(layer.disable)
        disable = (disable | set(layer.disable)) - set(layer.enable)
        for spec in layer.custom:
            custom[spec.id] = spec
        sources.extend(layer.sources)
    return LanguageOverrides(
        globs=tuple(globs.items()),
        suffixes=MappingProxyType(suffixes),
        enable=frozenset(enable),
        disable=frozenset(disable),
        custom=tuple(custom.values()),
        sources=tuple(sources),
    )
