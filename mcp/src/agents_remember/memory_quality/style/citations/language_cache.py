"""Isolated hash-pinned grammar cache, lazy wheel installer, and custom library loader.

Pinned PyPI grammar wheels and ``tree-sitter-language-pack`` artifacts are
downloaded lazily on demand, verified against the SHA-256 digests in the global
catalog lock, and unpacked into isolated subdirectories under
``AR_LANGUAGE_CACHE`` (default ``~/.local/state/agents-remember/grammars``)
without ever mutating the active Python virtual environment.

Custom local grammars (``.so`` / ``.dylib``) are loaded strictly from local
paths with ABI range validation (``MIN_COMPATIBLE_LANGUAGE_VERSION`` through
``LANGUAGE_VERSION``).
"""

from __future__ import annotations

import ctypes
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from packaging.tags import sys_tags
from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from tree_sitter import LANGUAGE_VERSION, MIN_COMPATIBLE_LANGUAGE_VERSION, Language

from agents_remember.errors import GrammarUnavailableError
from agents_remember.memory_quality.style.citations.language_catalog import (
    CatalogLanguageSpec,
    LanguageCatalog,
    WheelArtifact,
    load_language_catalog,
)

CACHE_ENV_VAR: Final = "AR_LANGUAGE_CACHE"
RECEIPT_FILENAME: Final = ".ar-grammar-receipt.json"
LANGUAGE_PACK_DIRNAME: Final = "_language_pack"
DOWNLOAD_TIMEOUT_SECONDS: Final = 60

WheelDownloader = Callable[[str], bytes]

_CACHE_LOCK = threading.Lock()
_LOADED_LANGUAGES: dict[tuple[str, str, str, str], Language] = {}
_CUSTOM_DLL_HANDLES: dict[str, ctypes.CDLL] = {}


def resolve_language_cache_root(override: Path | None = None) -> Path:
    """Return the active isolated grammar cache directory."""
    if override is not None:
        return override.expanduser().resolve()
    configured = os.environ.get(CACHE_ENV_VAR, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    xdg_state = os.environ.get("XDG_STATE_HOME", "").strip()
    state_home = Path(xdg_state).expanduser() if xdg_state else Path.home() / ".local" / "state"
    return (state_home / "agents-remember" / "grammars").resolve()


def select_platform_wheel(wheels: Sequence[WheelArtifact]) -> WheelArtifact | None:
    """Pick the highest-priority wheel compatible with the running interpreter and platform."""
    supported = {tag: priority for priority, tag in enumerate(sys_tags())}
    best: tuple[int, WheelArtifact] | None = None
    for wheel in wheels:
        try:
            _name, _version, _build, tags = parse_wheel_filename(wheel.filename)
        except InvalidWheelFilename:
            continue
        priorities = [supported[tag] for tag in tags if tag in supported]
        if not priorities:
            continue
        rank = min(priorities)
        if best is None or rank < best[0]:
            best = (rank, wheel)
    return None if best is None else best[1]


def default_wheel_downloader(url: str) -> bytes:
    """Fetch a pinned HTTPS wheel artifact from PyPI."""
    if not url.startswith("https://"):
        raise GrammarUnavailableError(f"refusing non-HTTPS grammar artifact URL {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": "agents-remember-grammar-cache/1"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            return bytes(response.read())
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise GrammarUnavailableError(f"failed to download {url}: {error}") from error


def is_cached_language_warm(
    spec: CatalogLanguageSpec,
    *,
    catalog: LanguageCatalog | None = None,
    cache_root: Path | None = None,
) -> bool:
    """Whether ``spec``'s pinned artifact is already verified and present in ``AR_LANGUAGE_CACHE``."""
    active_catalog = load_language_catalog() if catalog is None else catalog
    wheel = _selected_wheel_for_spec(spec, active_catalog)
    if wheel is None:
        return False
    target_dir = _target_dir_for_spec(spec, resolve_language_cache_root(cache_root))
    return _receipt_matches(target_dir, spec, active_catalog, wheel)


@dataclass(frozen=True)
class _WheelInstallTarget:
    spec: CatalogLanguageSpec
    catalog: LanguageCatalog
    wheel: WheelArtifact
    root: Path
    target_dir: Path


def warm_catalog_language(
    spec: CatalogLanguageSpec,
    *,
    catalog: LanguageCatalog | None = None,
    cache_root: Path | None = None,
    downloader: WheelDownloader | None = None,
) -> bool:
    """Ensure ``spec`` is downloaded, verified, and extracted into ``AR_LANGUAGE_CACHE``.

    Returns ``True`` when newly downloaded/installed and ``False`` when already warm.
    """
    active_catalog = load_language_catalog() if catalog is None else catalog
    root = resolve_language_cache_root(cache_root)
    wheel = _require_wheel_for_spec(spec, active_catalog, root)
    target_dir = _target_dir_for_spec(spec, root)
    if _receipt_matches(target_dir, spec, active_catalog, wheel):
        return False
    target = _WheelInstallTarget(spec, active_catalog, wheel, root, target_dir)
    return _install_pinned_wheel(
        target,
        default_wheel_downloader if downloader is None else downloader,
    )


def ensure_catalog_language(
    spec: CatalogLanguageSpec,
    *,
    catalog: LanguageCatalog | None = None,
    cache_root: Path | None = None,
    downloader: WheelDownloader | None = None,
) -> Language:
    """Return the verified ``tree_sitter.Language`` for ``spec``, lazily caching on miss."""
    active_catalog = load_language_catalog() if catalog is None else catalog
    root = resolve_language_cache_root(cache_root)
    wheel = _require_wheel_for_spec(spec, active_catalog, root)
    cache_key = (str(root), spec.id, spec.version, wheel.sha256)
    with _CACHE_LOCK:
        cached = _LOADED_LANGUAGES.get(cache_key)
        if cached is not None:
            return cached
    target_dir = _target_dir_for_spec(spec, root)
    if not _receipt_matches(target_dir, spec, active_catalog, wheel):
        target = _WheelInstallTarget(spec, active_catalog, wheel, root, target_dir)
        _install_pinned_wheel(
            target,
            default_wheel_downloader if downloader is None else downloader,
        )
    loaded = _load_language_from_cache(spec, target_dir, active_catalog, root)
    with _CACHE_LOCK:
        _LOADED_LANGUAGES[cache_key] = loaded
    return loaded


def load_custom_library_language(
    library_path: Path,
    symbol: str,
    *,
    language_id: str,
    min_abi: int = MIN_COMPATIBLE_LANGUAGE_VERSION,
    max_abi: int = LANGUAGE_VERSION,
) -> Language:
    """Load and ABI-check a local compiled tree-sitter shared library (``.so`` / ``.dylib``)."""
    resolved = library_path.expanduser().resolve()
    if not resolved.is_file():
        raise GrammarUnavailableError(
            f"custom language {language_id!r} library not found at {resolved}; "
            "custom grammars are machine-local and must be provisioned locally on this host"
        )
    try:
        lib = _load_cdll(resolved)
        factory = getattr(lib, symbol)
    except (OSError, AttributeError) as error:
        raise GrammarUnavailableError(
            f"custom language {language_id!r} could not load symbol {symbol!r} from {resolved}: {error}"
        ) from error
    factory.restype = ctypes.c_void_p
    factory.argtypes = []
    ptr = factory()
    if not ptr:
        raise GrammarUnavailableError(
            f"custom language {language_id!r} symbol {symbol!r} in {resolved} returned a null pointer"
        )
    capsule = _wrap_language_capsule(ptr)
    return _construct_and_verify_abi(
        capsule,
        language_id=language_id,
        origin=str(resolved),
        min_abi=min_abi,
        max_abi=max_abi,
    )


def _load_cdll(resolved: Path) -> ctypes.CDLL:
    key = str(resolved)
    with _CACHE_LOCK:
        existing = _CUSTOM_DLL_HANDLES.get(key)
        if existing is not None:
            return existing
        loaded = ctypes.CDLL(key)
        _CUSTOM_DLL_HANDLES[key] = loaded
        return loaded


def _wrap_language_capsule(ptr: int) -> Any:
    pycapsule_new = ctypes.pythonapi.PyCapsule_New
    pycapsule_new.restype = ctypes.py_object
    pycapsule_new.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    return pycapsule_new(ptr, b"tree_sitter.Language", None)


def _construct_and_verify_abi(
    raw_language: Any,
    *,
    language_id: str,
    origin: str,
    min_abi: int,
    max_abi: int,
) -> Language:
    try:
        lang = raw_language if isinstance(raw_language, Language) else Language(raw_language)
    except (TypeError, ValueError) as error:
        raise GrammarUnavailableError(
            f"grammar {language_id!r} from {origin} failed tree-sitter Language/ABI check "
            f"(supported ABI {min_abi}-{max_abi}): {error}"
        ) from error
    if not (min_abi <= lang.abi_version <= max_abi):
        raise GrammarUnavailableError(
            f"grammar {language_id!r} from {origin} reports ABI {lang.abi_version}, "
            f"outside supported range {min_abi}-{max_abi}"
        )
    return lang


def _selected_wheel_for_spec(
    spec: CatalogLanguageSpec, catalog: LanguageCatalog
) -> WheelArtifact | None:
    if spec.provider_kind == "pypi_wheel":
        return select_platform_wheel(spec.wheels)
    if spec.provider_kind == "language_pack":
        return select_platform_wheel(catalog.language_pack.wheels)
    return None


def _require_wheel_for_spec(
    spec: CatalogLanguageSpec, catalog: LanguageCatalog, cache_root: Path
) -> WheelArtifact:
    wheel = _selected_wheel_for_spec(spec, catalog)
    if wheel is None:
        package = spec.package or catalog.language_pack.package
        raise GrammarUnavailableError(
            f"no compatible pinned wheel for {spec.id!r} ({package}=={spec.version}) on this platform; "
            f"cache root: {cache_root}. Retry after adding a matching wheel pin or pre-warming "
            f"`agents-remember languages warm --cache-dir {cache_root}`."
        )
    return wheel


def _target_dir_for_spec(spec: CatalogLanguageSpec, cache_root: Path) -> Path:
    if spec.provider_kind == "language_pack":
        return cache_root / LANGUAGE_PACK_DIRNAME
    return cache_root / spec.id


def _receipt_matches(
    target_dir: Path,
    spec: CatalogLanguageSpec,
    catalog: LanguageCatalog,
    wheel: WheelArtifact,
) -> bool:
    receipt_path = target_dir / RECEIPT_FILENAME
    if not receipt_path.is_file():
        return False
    try:
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    expected_version = (
        catalog.language_pack.version if spec.provider_kind == "language_pack" else spec.version
    )
    expected_package = (
        catalog.language_pack.package if spec.provider_kind == "language_pack" else spec.package
    )
    return (
        data.get("package") == expected_package
        and data.get("version") == expected_version
        and data.get("wheelFilename") == wheel.filename
        and data.get("wheelSha256") == wheel.sha256
    )


@contextmanager
def _cache_entry_lock(cache_root: Path, lock_name: str) -> Iterator[None]:
    lock_dir = cache_root / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{lock_name}.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _install_pinned_wheel(
    target: _WheelInstallTarget,
    downloader: WheelDownloader,
) -> bool:
    spec, catalog, wheel, root, target_dir = (
        target.spec,
        target.catalog,
        target.wheel,
        target.root,
        target.target_dir,
    )
    lock_name = LANGUAGE_PACK_DIRNAME if spec.provider_kind == "language_pack" else spec.id
    root.mkdir(parents=True, exist_ok=True)
    with _cache_entry_lock(root, lock_name):
        if _receipt_matches(target_dir, spec, catalog, wheel):
            return False
        try:
            payload = downloader(wheel.url)
        except GrammarUnavailableError as error:
            raise GrammarUnavailableError(
                f"unable to fetch pinned grammar {spec.id!r} ({wheel.filename}) into {root}: "
                f"{error}. Check network access or pre-warm with "
                f"`agents-remember languages warm --cache-dir {root}`."
            ) from error
        actual_sha = hashlib.sha256(payload).hexdigest()
        if actual_sha != wheel.sha256:
            raise GrammarUnavailableError(
                f"SHA-256 mismatch for pinned grammar {spec.id!r} ({wheel.filename}): "
                f"expected {wheel.sha256}, got {actual_sha}. Refusing to unpack into {root}."
            )
        _extract_wheel_atomically(payload, target)
        return True


def _extract_wheel_atomically(payload: bytes, target: _WheelInstallTarget) -> None:
    spec, catalog, wheel, root, target_dir = (
        target.spec,
        target.catalog,
        target.wheel,
        target.root,
        target.target_dir,
    )
    staging_parent = root / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f"{target_dir.name}-", dir=staging_parent))
    backup_dir: Path | None = None
    try:
        _safe_extract_zip(payload, staging_dir, wheel.filename)
        receipt = {
            "schema": "ar-grammar-cache-receipt/v1",
            "providerKind": spec.provider_kind,
            "languageId": spec.id if spec.provider_kind == "pypi_wheel" else LANGUAGE_PACK_DIRNAME,
            "package": (
                catalog.language_pack.package
                if spec.provider_kind == "language_pack"
                else spec.package
            ),
            "version": (
                catalog.language_pack.version
                if spec.provider_kind == "language_pack"
                else spec.version
            ),
            "wheelFilename": wheel.filename,
            "wheelSha256": wheel.sha256,
        }
        (staging_dir / RECEIPT_FILENAME).write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if target_dir.exists():
            backup_dir = Path(
                tempfile.mkdtemp(prefix=f"{target_dir.name}-old-", dir=staging_parent)
            )
            backup_dir.rmdir()
            os.replace(target_dir, backup_dir)
        os.replace(staging_dir, target_dir)
    except Exception:
        if backup_dir is not None and backup_dir.exists() and not target_dir.exists():
            os.replace(backup_dir, target_dir)
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    else:
        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)


def _safe_extract_zip(payload: bytes, destination: Path, filename: str) -> None:
    resolved_dest = destination.resolve()
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise GrammarUnavailableError(
            f"wheel {filename!r} is not a valid zip archive: {error}"
        ) from error
    with archive:
        for info in archive.infolist():
            member = Path(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise GrammarUnavailableError(
                    f"wheel {filename!r} contains unsafe member path {info.filename!r}"
                )
            # Refuse symlinks inside wheel archives.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise GrammarUnavailableError(
                    f"wheel {filename!r} contains disallowed symlink member {info.filename!r}"
                )
            out_path = (resolved_dest / member).resolve()
            if not out_path.is_relative_to(resolved_dest):
                raise GrammarUnavailableError(
                    f"wheel {filename!r} member {info.filename!r} escapes staging directory"
                )
            if info.is_dir():
                out_path.mkdir(parents=True, exist_ok=True)
            else:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as src, out_path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def _find_pack_binding(pkg_dir: Path, pack_language: str) -> Path | None:
    bindings_dir = pkg_dir / "bindings"
    if not bindings_dir.is_dir():
        return None
    matches = sorted(
        path
        for path in bindings_dir.iterdir()
        if path.is_file()
        and path.name.split(".", 1)[0] == pack_language
        and path.suffix in {".so", ".pyd"}
    )
    return matches[0] if matches else None


def _load_extension_module(synthetic_name: str, ext_path: Path) -> ModuleType:
    ext_spec = importlib.util.spec_from_file_location(synthetic_name, ext_path)
    if ext_spec is None or ext_spec.loader is None:
        raise GrammarUnavailableError(f"cannot create import spec for {ext_path}")
    module = importlib.util.module_from_spec(ext_spec)
    ext_spec.loader.exec_module(module)
    return module


def _load_language_from_cache(
    spec: CatalogLanguageSpec,
    target_dir: Path,
    catalog: LanguageCatalog,
    cache_root: Path,
) -> Language:
    module_name = spec.module or catalog.language_pack.module
    symbol_name = spec.symbol or catalog.language_pack.getter
    pkg_dir = target_dir / module_name
    if not pkg_dir.is_dir():
        raise GrammarUnavailableError(
            f"cached grammar {spec.id!r} in {target_dir} is missing package directory {module_name!r}; "
            f"remove {target_dir} and retry `agents-remember languages warm --cache-dir {cache_root}`"
        )
    try:
        pack_lang = spec.pack_language or spec.id
        pack_binding = (
            _find_pack_binding(pkg_dir, pack_lang)
            if spec.provider_kind == "language_pack"
            else None
        )
        if pack_binding is not None:
            # Load bundled language-pack C extensions directly so optional third-party imports in
            # the pack's __init__.py never require installing grammar packages into the MCP venv.
            binding_mod = _load_extension_module(
                f"_ar_grammar_cache_pack.{pack_lang}", pack_binding
            )
            raw = binding_mod.language()
        else:
            loaded_module = _import_isolated_module(spec, target_dir, pkg_dir, module_name)
            factory = getattr(loaded_module, symbol_name)
            raw = factory(pack_lang) if spec.provider_kind == "language_pack" else factory()
    except GrammarUnavailableError:
        raise
    except Exception as error:
        raise GrammarUnavailableError(
            f"failed to load cached grammar {spec.id!r} ({module_name}.{symbol_name}) "
            f"from {target_dir}: {error}"
        ) from error
    return _construct_and_verify_abi(
        raw,
        language_id=spec.id,
        origin=str(target_dir),
        min_abi=max(MIN_COMPATIBLE_LANGUAGE_VERSION, catalog.tree_sitter.min_abi),
        max_abi=min(LANGUAGE_VERSION, catalog.tree_sitter.max_abi),
    )


def _import_isolated_module(
    spec: CatalogLanguageSpec,
    target_dir: Path,
    pkg_dir: Path,
    module_name: str,
) -> ModuleType:
    if spec.provider_kind == "pypi_wheel":
        bindings = sorted(
            path
            for path in pkg_dir.iterdir()
            if path.is_file()
            and path.name.startswith("_binding")
            and path.suffix in {".so", ".pyd"}
        )
        if bindings:
            # Official tree-sitter wheels export PyInit__binding; using ``_binding`` as the leaf
            # module name loads the extension directly without touching sys.path or the venv.
            return _load_extension_module(f"_ar_grammar_cache_{spec.id}._binding", bindings[0])

    init_py = pkg_dir / "__init__.py"
    if not init_py.is_file():
        raise GrammarUnavailableError(f"cached package {pkg_dir} has no __init__.py")
    synthetic_pkg = f"_ar_grammar_cache_{spec.id}_{module_name}"
    pkg_spec = importlib.util.spec_from_file_location(
        synthetic_pkg,
        init_py,
        submodule_search_locations=[str(pkg_dir)],
    )
    if pkg_spec is None or pkg_spec.loader is None:
        raise GrammarUnavailableError(f"cannot create import spec for {init_py}")
    module = importlib.util.module_from_spec(pkg_spec)
    target_str = str(target_dir)
    added_path = target_str not in sys.path
    if added_path:
        sys.path.insert(0, target_str)
    sys.modules[synthetic_pkg] = module
    try:
        pkg_spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(synthetic_pkg, None)
        raise
    finally:
        if added_path and target_str in sys.path:
            sys.path.remove(target_str)
    return module
