"""Repository language auto-scanner, cache pre-warmer, and per-repo ``LanguageSet`` builder."""

from __future__ import annotations

import os
import stat
import subprocess
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from agents_remember.errors import GrammarUnavailableError
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory_quality.style.citations import (
    language_cache,
    language_catalog,
    language_config,
)
from agents_remember.memory_quality.style.citations.language_cache import WheelDownloader
from agents_remember.memory_quality.style.citations.language_catalog import (
    CatalogLanguageSpec,
    LanguageCatalog,
)
from agents_remember.memory_quality.style.citations.language_config import (
    REPO_LANGUAGES_FILENAME,
    CustomLanguageSpec,
    LanguageOverrides,
    user_settings_path,
)
from agents_remember.memory_quality.style.citations.language_set import (
    LEAN,
    LanguageProvider,
    LanguageSet,
    LeanLanguageProvider,
    TreeSitterLanguageProvider,
    matches_glob,
)
from agents_remember.memory_quality.style.citations.tags_engine import QUERIES_ROOT

_SKIP_DIRS: Final = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".tox"})
_REPO_LANGUAGE_SET_CACHE_LIMIT: Final = 256
_StatToken = tuple[int, int] | None
_RepoLanguageSetEntry = tuple[
    tuple[_StatToken, _StatToken],
    tuple[Path, ...],
    tuple[_StatToken, ...],
    LanguageSet,
]
_REPO_LANGUAGE_SETS_LOCK = threading.Lock()
_REPO_LANGUAGE_SETS: dict[tuple[Path, bool], _RepoLanguageSetEntry] = {}


@dataclass(frozen=True)
class LanguageScanReport:
    """Detected languages and overrides for one repository."""

    repo_root: str
    detected: tuple[str, ...]
    catalog_languages: tuple[str, ...]
    builtin_languages: tuple[str, ...]
    custom_languages: tuple[str, ...]
    ambiguous_suffixes: tuple[str, ...]
    disabled_languages: tuple[str, ...]
    config_sources: tuple[str, ...]
    file_count: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "repoRoot": self.repo_root,
            "fileCount": self.file_count,
            "detected": list(self.detected),
            "catalogLanguages": list(self.catalog_languages),
            "builtinLanguages": list(self.builtin_languages),
            "customLanguages": list(self.custom_languages),
            "ambiguousSuffixes": list(self.ambiguous_suffixes),
            "disabledLanguages": list(self.disabled_languages),
            "configSources": list(self.config_sources),
        }


@dataclass(frozen=True)
class LanguageWarmReport:
    """Outcome of pre-warming detected languages into ``AR_LANGUAGE_CACHE``."""

    cache_root: str
    scan: LanguageScanReport
    warmed: tuple[str, ...]
    already_cached: tuple[str, ...]
    failed: Mapping[str, str]
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dryRun": self.dry_run,
            "cacheRoot": self.cache_root,
            "detected": list(self.scan.detected),
            "warmed": list(self.warmed),
            "alreadyCached": list(self.already_cached),
            "builtin": list(self.scan.builtin_languages),
            "custom": list(self.scan.custom_languages),
            "ambiguousSuffixes": list(self.scan.ambiguous_suffixes),
            "failed": dict(self.failed),
            "configSources": list(self.scan.config_sources),
        }


def build_provider_from_catalog(
    spec: CatalogLanguageSpec,
    *,
    catalog: LanguageCatalog,
    queries_root: Path = QUERIES_ROOT,
    cache_root: Path | None = None,
    downloader: WheelDownloader | None = None,
) -> LanguageProvider:
    """Create a ``LanguageProvider`` for one catalog lock entry."""
    if spec.provider_kind == "builtin_lean":
        return LeanLanguageProvider(key=spec.id, suffixes=spec.suffixes, version=spec.version)
    tags_text = spec.load_tags_query(queries_root)
    tests_text = spec.load_tests_query(queries_root)

    def _load() -> Any:
        return language_cache.ensure_catalog_language(
            spec,
            catalog=catalog,
            cache_root=cache_root,
            downloader=downloader,
        )

    return TreeSitterLanguageProvider(
        key=spec.id,
        suffixes=spec.suffixes,
        filenames=spec.filenames,
        shebangs=spec.shebangs,
        grammar_version=spec.grammar_version_label,
        load_language=_load,
        tags_query_text=tags_text,
        wrappers=spec.wrappers,
        leading_wrappers=spec.leading_wrappers,
        destructuring=spec.destructuring,
        qualifiers=spec.qualifiers,
        guarantee_tier=spec.guarantee_tier,
        tests_query_text=tests_text,
        anchor_spelling=spec.anchor_spelling,
    )


def build_provider_from_custom(
    spec: CustomLanguageSpec,
    *,
    catalog: LanguageCatalog,
) -> TreeSitterLanguageProvider:
    """Create a ``TreeSitterLanguageProvider`` for one local custom language registration."""
    tags_text = spec.load_tags_query()
    tests_text = spec.load_tests_query()
    min_abi = catalog.tree_sitter.min_abi
    max_abi = catalog.tree_sitter.max_abi

    def _load() -> Any:
        return language_cache.load_custom_library_language(
            spec.library_path,
            spec.symbol,
            language_id=spec.id,
            min_abi=min_abi,
            max_abi=max_abi,
        )

    return TreeSitterLanguageProvider(
        key=spec.id,
        suffixes=spec.suffixes,
        globs=spec.globs,
        grammar_version=spec.content_version(),
        load_language=_load,
        tags_query_text=tags_text,
        wrappers=spec.wrappers,
        destructuring=spec.destructuring,
        tests_query_text=tests_text,
    )


def build_language_set(
    *,
    catalog: LanguageCatalog | None = None,
    overrides: LanguageOverrides | None = None,
    queries_root: Path = QUERIES_ROOT,
    cache_root: Path | None = None,
    downloader: WheelDownloader | None = None,
) -> LanguageSet:
    """Build a ``LanguageSet`` from the global catalog lock and optional overrides."""
    active_catalog = language_catalog.load_language_catalog() if catalog is None else catalog
    active_overrides = LanguageOverrides() if overrides is None else overrides
    _validate_override_identities(active_overrides, active_catalog)
    providers: dict[str, LanguageProvider] = {}
    for lang_id, spec in active_catalog.languages.items():
        if lang_id in active_overrides.disable and lang_id != LEAN:
            continue
        providers[lang_id] = build_provider_from_catalog(
            spec,
            catalog=active_catalog,
            queries_root=queries_root,
            cache_root=cache_root,
            downloader=downloader,
        )
    for custom_spec in active_overrides.custom:
        if custom_spec.id in active_overrides.disable:
            continue
        providers[custom_spec.id] = build_provider_from_custom(custom_spec, catalog=active_catalog)

    _validate_override_targets(active_overrides, providers)
    return LanguageSet(
        providers=tuple(providers.values()),
        tree_sitter_version=active_catalog.tree_sitter.version,
        glob_overrides=active_overrides.globs,
        suffix_overrides=active_overrides.suffixes,
    )


def build_repository_language_set(
    repo_root: Path,
    *,
    catalog: LanguageCatalog | None = None,
    include_user_settings: bool = True,
    cache_root: Path | None = None,
    downloader: WheelDownloader | None = None,
) -> LanguageSet:
    """Build the ``LanguageSet`` configured for ``repo_root`` (catalog + ``languages.json`` + user settings)."""
    overrides = language_config.load_language_overrides(
        repo_root,
        include_user_settings=include_user_settings,
    )
    return build_language_set(
        catalog=catalog,
        overrides=overrides,
        cache_root=cache_root,
        downloader=downloader,
    )


def _stat_token(path: Path) -> _StatToken:
    try:
        info = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return (info.st_mtime_ns, info.st_size)


def _overrides_are_empty(overrides: LanguageOverrides) -> bool:
    return not (
        overrides.globs
        or overrides.suffixes
        or overrides.enable
        or overrides.disable
        or overrides.custom
    )


def _custom_artifact_paths(overrides: LanguageOverrides) -> tuple[Path, ...]:
    paths: list[Path] = []
    for spec in overrides.custom:
        paths.append(spec.library_path)
        paths.append(spec.tags_query_path)
        if spec.tests_query_path is not None:
            paths.append(spec.tests_query_path)
    return tuple(paths)


def _remember_repo_language_set(
    cache_key: tuple[Path, bool], entry: _RepoLanguageSetEntry
) -> LanguageSet:
    with _REPO_LANGUAGE_SETS_LOCK:
        if (
            cache_key not in _REPO_LANGUAGE_SETS
            and len(_REPO_LANGUAGE_SETS) >= _REPO_LANGUAGE_SET_CACHE_LIMIT
        ):
            _REPO_LANGUAGE_SETS.pop(next(iter(_REPO_LANGUAGE_SETS)))
        _REPO_LANGUAGE_SETS[cache_key] = entry
    return entry[3]


def repository_language_set(
    repo_root: Path,
    *,
    default: LanguageSet | None = None,
    include_user_settings: bool = True,
) -> LanguageSet:
    """Return ``repo_root``'s configured ``LanguageSet``, cached by config file stats."""
    resolved_root = repo_root.expanduser().resolve()
    base_fingerprint = (
        _stat_token(resolved_root / REPO_LANGUAGES_FILENAME),
        _stat_token(user_settings_path()) if include_user_settings else None,
    )
    cache_key = (resolved_root, include_user_settings)
    with _REPO_LANGUAGE_SETS_LOCK:
        cached = _REPO_LANGUAGE_SETS.get(cache_key)
    if (
        cached is not None
        and cached[0] == base_fingerprint
        and tuple(_stat_token(path) for path in cached[1]) == cached[2]
    ):
        return cached[3]

    if base_fingerprint == (None, None) and default is not None:
        return _remember_repo_language_set(cache_key, (base_fingerprint, (), (), default))

    overrides = language_config.load_language_overrides(
        resolved_root,
        include_user_settings=include_user_settings,
    )
    if _overrides_are_empty(overrides) and default is not None:
        return _remember_repo_language_set(cache_key, (base_fingerprint, (), (), default))

    built = build_language_set(overrides=overrides)
    custom_paths = _custom_artifact_paths(overrides)
    custom_fingerprint = tuple(_stat_token(path) for path in custom_paths)
    return _remember_repo_language_set(
        cache_key, (base_fingerprint, custom_paths, custom_fingerprint, built)
    )


def _partition_detected(
    detected: set[str],
    catalog: LanguageCatalog,
    custom_ids: set[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    catalog_langs: list[str] = []
    builtin_langs: list[str] = []
    custom_langs: list[str] = []
    for lang_id in sorted(detected):
        if lang_id in custom_ids:
            custom_langs.append(lang_id)
            continue
        spec = catalog.get(lang_id)
        if spec is None:
            continue
        if spec.provider_kind == "builtin_lean":
            builtin_langs.append(lang_id)
        else:
            catalog_langs.append(lang_id)
    return tuple(catalog_langs), tuple(builtin_langs), tuple(custom_langs)


def scan_repository_languages(
    repo_root: Path,
    *,
    catalog: LanguageCatalog | None = None,
    user_settings_file: Path | None = None,
    include_user_settings: bool = True,
) -> LanguageScanReport:
    """Scan ``repo_root``'s tracked files and overrides to detect active languages."""
    active_catalog = language_catalog.load_language_catalog() if catalog is None else catalog
    resolved_root = repo_root.expanduser().resolve()
    overrides = language_config.load_language_overrides(
        resolved_root,
        user_settings_file=user_settings_file,
        include_user_settings=include_user_settings,
    )
    _validate_override_identities(overrides, active_catalog)
    active_ids = (frozenset(active_catalog.languages) | {spec.id for spec in overrides.custom}) - (
        overrides.disable - {LEAN}
    )
    _validate_override_target_ids(overrides, active_ids)
    tracked = _enumerate_repo_paths(resolved_root)
    custom_ids = {spec.id for spec in overrides.custom if spec.id not in overrides.disable}
    detected: set[str] = set()
    observed_ambiguous: set[str] = set()
    for rel_path in tracked:
        matched, ambiguous = _classify_path(
            rel_path,
            repo_root=resolved_root,
            catalog=active_catalog,
            overrides=overrides,
        )
        if matched is not None and matched not in overrides.disable:
            detected.add(matched)
        if ambiguous is not None:
            observed_ambiguous.add(ambiguous)
    detected.update(overrides.enable)
    catalog_langs, builtin_langs, custom_langs = _partition_detected(
        detected, active_catalog, custom_ids
    )
    return LanguageScanReport(
        repo_root=str(resolved_root),
        detected=tuple(sorted(detected)),
        catalog_languages=catalog_langs,
        builtin_languages=builtin_langs,
        custom_languages=custom_langs,
        ambiguous_suffixes=tuple(sorted(observed_ambiguous)),
        disabled_languages=tuple(sorted(overrides.disable)),
        config_sources=overrides.sources,
        file_count=len(tracked),
    )


def all_catalog_language_ids(catalog: LanguageCatalog | None = None) -> tuple[str, ...]:
    """Return every tree-sitter language ID declared in the global catalog."""
    active_catalog = language_catalog.load_language_catalog() if catalog is None else catalog
    return tuple(
        sorted(
            lang_id
            for lang_id, spec in active_catalog.languages.items()
            if spec.provider_kind != "builtin_lean"
        )
    )


def warm_repository_languages(
    repo_root: Path,
    *,
    languages: Iterable[str] | None = None,
    cache_root: Path | None = None,
    downloader: WheelDownloader | None = None,
    dry_run: bool = False,
) -> LanguageWarmReport:
    """Scan ``repo_root`` (or warm explicit ``languages``) into ``AR_LANGUAGE_CACHE``."""
    active_catalog = language_catalog.load_language_catalog()
    resolved_cache = language_cache.resolve_language_cache_root(cache_root)
    scan = scan_repository_languages(repo_root, catalog=active_catalog)
    targets = tuple(sorted(set(languages))) if languages is not None else scan.catalog_languages
    warmed: list[str] = []
    already_cached: list[str] = []
    failed: dict[str, str] = {}
    for lang_id in targets:
        spec = active_catalog.get(lang_id)
        if spec is None:
            failed[lang_id] = f"unknown catalog language {lang_id!r}"
            continue
        if spec.provider_kind == "builtin_lean":
            continue
        if language_cache.is_cached_language_warm(
            spec, catalog=active_catalog, cache_root=resolved_cache
        ):
            already_cached.append(lang_id)
            continue
        if dry_run:
            warmed.append(lang_id)
            continue
        try:
            installed = language_cache.warm_catalog_language(
                spec,
                catalog=active_catalog,
                cache_root=resolved_cache,
                downloader=downloader,
            )
        except GrammarUnavailableError as error:
            failed[lang_id] = str(error)
        else:
            if installed:
                warmed.append(lang_id)
            else:
                already_cached.append(lang_id)
    return LanguageWarmReport(
        cache_root=str(resolved_cache),
        scan=scan,
        warmed=tuple(warmed),
        already_cached=tuple(already_cached),
        failed=failed,
        dry_run=dry_run,
    )


def _validate_override_identities(overrides: LanguageOverrides, catalog: LanguageCatalog) -> None:
    for custom_spec in overrides.custom:
        if custom_spec.id in catalog.languages:
            raise language_config.LanguageConfigError(
                f"custom language id {custom_spec.id!r} cannot shadow catalog language "
                f"{custom_spec.id!r}"
            )
    known_ids = frozenset(catalog.languages) | {spec.id for spec in overrides.custom}
    for lang_id in sorted(overrides.enable):
        if lang_id not in known_ids:
            raise language_config.LanguageConfigError(
                f"enable references unknown language id {lang_id!r}"
            )
    for lang_id in sorted(overrides.disable):
        if lang_id not in known_ids:
            raise language_config.LanguageConfigError(
                f"disable references unknown language id {lang_id!r}"
            )


def _validate_override_target_ids(overrides: LanguageOverrides, active_ids: Iterable[str]) -> None:
    allowed = frozenset(active_ids)
    for pattern, lang_id in overrides.globs:
        if lang_id not in allowed:
            raise language_config.LanguageConfigError(
                f"glob override {pattern!r} targets unknown or disabled language {lang_id!r}"
            )
    for suffix, lang_id in overrides.suffixes.items():
        if lang_id not in allowed:
            raise language_config.LanguageConfigError(
                f"suffix override {suffix!r} targets unknown or disabled language {lang_id!r}"
            )


def _validate_override_targets(
    overrides: LanguageOverrides, providers: Mapping[str, LanguageProvider]
) -> None:
    _validate_override_target_ids(overrides, providers.keys())


def _enumerate_repo_paths(repo_root: Path) -> tuple[str, ...]:
    if not repo_root.is_dir():
        return ()
    try:
        result = run_git(
            repo_root,
            ["ls-files", "-z"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0 and result.stdout:
        return tuple(path for path in result.stdout.split("\0") if path)
    paths: list[str] = []
    for current_root, dirs, files in os.walk(repo_root):
        dirs[:] = sorted(name for name in dirs if name not in _SKIP_DIRS)
        rel_dir = Path(current_root).relative_to(repo_root)
        for filename in sorted(files):
            rel = (rel_dir / filename).as_posix() if rel_dir != Path(".") else filename
            paths.append(rel)
    return tuple(paths)


def _match_override_or_custom(
    rel_path: str, suffix: str, overrides: LanguageOverrides
) -> str | None:
    for pattern, lang_id in overrides.globs:
        if matches_glob(rel_path, pattern):
            return lang_id
    if suffix and suffix in overrides.suffixes:
        return overrides.suffixes[suffix]
    for custom_spec in overrides.custom:
        if custom_spec.id in overrides.disable:
            continue
        if (suffix and suffix in custom_spec.suffixes) or any(
            matches_glob(rel_path, pattern) for pattern in custom_spec.globs
        ):
            return custom_spec.id
    return None


def _classify_path(
    rel_path: str,
    *,
    repo_root: Path,
    catalog: LanguageCatalog,
    overrides: LanguageOverrides,
) -> tuple[str | None, str | None]:
    posix = PurePosixPath(rel_path)
    suffix = posix.suffix.lower()
    if suffix in catalog.reserved_suffixes:
        return catalog.by_suffix.get(suffix), None
    override_match = _match_override_or_custom(rel_path, suffix, overrides)
    if override_match is not None:
        return override_match, None
    by_name = catalog.by_filename.get(posix.name)
    if by_name is not None:
        return by_name, None
    if suffix:
        if suffix in catalog.ambiguous_suffixes:
            return None, suffix
        return catalog.by_suffix.get(suffix), None
    interpreter = _read_shebang_interpreter(repo_root / rel_path)
    return (None if interpreter is None else catalog.by_shebang.get(interpreter)), None


def _read_shebang_interpreter(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            first_line = handle.readline(256)
    except OSError:
        return None
    if not first_line.startswith(b"#!"):
        return None
    parts = first_line[2:].decode("utf-8", errors="ignore").strip().split()
    if not parts:
        return None
    head = PurePosixPath(parts[0]).name
    if head != "env":
        return head
    return next(
        (PurePosixPath(token).name for token in parts[1:] if not token.startswith("-")),
        None,
    )
