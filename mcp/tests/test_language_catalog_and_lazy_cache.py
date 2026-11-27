"""End-to-end proofs for the uniform language catalog, lazy cache, custom registration, and dependency flip."""

from __future__ import annotations

import ast
import ctypes
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_currentness import observe as currentness_observe
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from agents_remember.application.review_memo_store import memo_namespace
from agents_remember.cli import __main__ as cli_main
from agents_remember.errors import GrammarUnavailableError
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.knowledge import read_anchors
from agents_remember.memory.knowledge.read_anchor_memo import BLOB_DEFINITIONS
from agents_remember.memory.knowledge_index import Entry
from agents_remember.memory_quality import reference_state
from agents_remember.memory_quality.style.citations import (
    extents,
    grammars,
    language_cache,
    language_catalog,
    language_config,
    language_scanner,
)
from agents_remember.memory_quality.style.citations.language_catalog import (
    CatalogLanguageSpec,
    LanguageCatalog,
    LanguageCatalogError,
    LanguagePackPin,
    WheelArtifact,
)
from agents_remember.memory_quality.style.citations.language_config import LanguageConfigError
from agents_remember.memory_quality.style.citations.language_set import LEAN
from knowledge_conversion_test_support import commit_files, init_repository
from packaging.tags import sys_tags

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_PYPROJECT = REPO_ROOT / "mcp" / "pyproject.toml"
MCP_UV_LOCK = REPO_ROOT / "mcp" / "uv.lock"
CITATIONS_DIR = (
    REPO_ROOT / "mcp" / "src" / "agents_remember" / "memory_quality" / "style" / "citations"
)
PRODUCT_SRC = REPO_ROOT / "mcp" / "src" / "agents_remember"


def _build_zip_wheel(files: dict[str, bytes]) -> tuple[bytes, str]:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    raw = buf.getvalue()
    return raw, hashlib.sha256(raw).hexdigest()


def _current_wheel_filename(package_mod: str, version: str) -> str:
    tag = next(iter(sys_tags()))
    return f"{package_mod}-{version}-{tag.interpreter}-{tag.abi}-{tag.platform}.whl"


def _cached_python_binding_so() -> Path:
    cache_root = language_cache.resolve_language_cache_root()
    pkg_dir = cache_root / "python" / "tree_sitter_python"
    candidates = sorted(pkg_dir.glob("_binding*.so"))
    assert candidates, f"expected pre-warmed python grammar under {pkg_dir}"
    return candidates[0]


def _extract_language_pointer(language_id: str) -> int:
    """Return the raw ``TSLanguage *`` pointer of a cached grammar for C fixture wrapping."""
    catalog = language_catalog.load_language_catalog()
    spec = catalog.get(language_id)
    assert spec is not None
    cache_root = language_cache.resolve_language_cache_root()
    target_dir = cache_root / spec.id
    pkg_dir = target_dir / (spec.module or "")
    mod = language_cache._import_isolated_module(spec, target_dir, pkg_dir, spec.module or "")
    capsule = getattr(mod, spec.symbol or "language")()
    get_name = ctypes.pythonapi.PyCapsule_GetName
    get_name.restype = ctypes.c_char_p
    get_name.argtypes = [ctypes.py_object]
    get_ptr = ctypes.pythonapi.PyCapsule_GetPointer
    get_ptr.restype = ctypes.c_void_p
    get_ptr.argtypes = [ctypes.py_object, ctypes.c_char_p]
    ptr = get_ptr(capsule, get_name(capsule))
    assert ptr
    return int(ptr)


_FORBIDDEN_TABLE_NAMES = frozenset({"GRAMMAR_PACKAGES", "SUFFIX_GRAMMARS", "MEASURED_VERSIONS"})
_SUFFIX_LITERAL_RE = re.compile(r"^\.[a-z][a-z0-9]{0,7}$")
_LANG_ID_LITERAL_RE = re.compile(r"^[a-z][a-z0-9_-]{1,24}$")
_GRAMMAR_PACKAGE_RE = re.compile(r"^tree[-_]sitter[-_][a-z0-9_-]+$")
_NON_LANGUAGE_DOT_LITERALS = frozenset(
    {
        ".git",
        ".venv",
        ".tox",
        ".json",
        ".md",
        ".lock",
        ".so",
        ".dylib",
        ".dll",
        ".whl",
        ".scm",
        ".tmp",
    }
)


def _literal_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_source_suffix_literal(value: str) -> bool:
    return bool(_SUFFIX_LITERAL_RE.fullmatch(value)) and value not in _NON_LANGUAGE_DOT_LITERALS


def _check_assignment_node(node: ast.Assign | ast.AnnAssign, filename: str) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [
        f"{filename}:{node.lineno}: forbidden table {target.id}"
        for target in targets
        if isinstance(target, ast.Name) and target.id in _FORBIDDEN_TABLE_NAMES
    ]


def _check_dict_node(node: ast.Dict, filename: str) -> list[str]:
    found: list[str] = []
    for key_node, val_node in zip(node.keys, node.values, strict=False):
        key_str = _literal_str(key_node)
        val_str = _literal_str(val_node)
        if key_str is None or val_str is None:
            continue
        if _is_source_suffix_literal(key_str) and _LANG_ID_LITERAL_RE.fullmatch(val_str):
            found.append(
                f"{filename}:{node.lineno}: hardcoded suffix mapping {key_str!r}->{val_str!r}"
            )
        elif _LANG_ID_LITERAL_RE.fullmatch(key_str) and _GRAMMAR_PACKAGE_RE.fullmatch(val_str):
            found.append(
                f"{filename}:{node.lineno}: hardcoded grammar package mapping {key_str!r}->{val_str!r}"
            )
    return found


def _check_collection_node(node: ast.Set | ast.List | ast.Tuple, filename: str) -> list[str]:
    strings = [s for elt in node.elts if (s := _literal_str(elt)) is not None]
    found: list[str] = []
    suffixes = [s for s in strings if _is_source_suffix_literal(s)]
    if len(suffixes) >= 2:
        found.append(f"{filename}:{node.lineno}: hardcoded suffix collection {suffixes}")
    grammar_pkgs = [s for s in strings if _GRAMMAR_PACKAGE_RE.fullmatch(s)]
    if grammar_pkgs:
        found.append(
            f"{filename}:{node.lineno}: hardcoded grammar package collection {grammar_pkgs}"
        )
    return found


def _check_import_node(node: ast.Import | ast.ImportFrom, filename: str) -> list[str]:
    if isinstance(node, ast.Import):
        tops = [alias.name.split(".", 1)[0] for alias in node.names]
    else:
        tops = [node.module.split(".", 1)[0]] if node.module else []
    return [
        f"{filename}:{node.lineno}: direct grammar import {top}"
        for top in tops
        if _GRAMMAR_PACKAGE_RE.fullmatch(top)
    ]


def _find_hardcoded_language_violations(source: str, filename: str) -> list[str]:
    """Walk the full AST (module, class, and function scopes) for hardcoded language tables."""
    tree = ast.parse(source, filename=filename)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            violations.extend(_check_assignment_node(node, filename))
        elif isinstance(node, ast.Dict):
            violations.extend(_check_dict_node(node, filename))
        elif isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            violations.extend(_check_collection_node(node, filename))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            violations.extend(_check_import_node(node, filename))
    return violations


def test_dependency_purity_pyproject_uv_lock_and_zero_hardcoded_language_lists() -> None:
    """AC-1: zero tree-sitter-* grammar packages in pyproject/uv.lock and pure data catalog."""
    declared = tomllib.loads(MCP_PYPROJECT.read_text(encoding="utf-8"))
    deps = list(declared["project"]["dependencies"])
    assert any(dep.startswith("tree-sitter>=") for dep in deps)
    grammar_deps = [dep for dep in deps if dep.lower().startswith("tree-sitter-")]
    assert grammar_deps == [], f"privileged grammar deps remain in pyproject.toml: {grammar_deps}"

    lock = tomllib.loads(MCP_UV_LOCK.read_text(encoding="utf-8"))
    locked_grammars = [
        pkg["name"]
        for pkg in lock.get("package", ())
        if isinstance(pkg, dict) and str(pkg.get("name", "")).startswith("tree-sitter-")
    ]
    assert locked_grammars == [], f"grammar packages remain in mcp/uv.lock: {locked_grammars}"

    # Self-test the AST checker against module-level AnnAssign, class-body, function-local, and direct import probes.
    for synthetic in (
        'from typing import Final\nSUFFIX_MAP: Final[dict[str, str]] = {".py": "python"}\n',
        'class Holder:\n    TABLE = {".ts": "typescript"}\n',
        'def helper():\n    local = {".js": "javascript"}\n    return local\n',
        "SUFFIX_GRAMMARS: dict[str, str] = {}\n",
        'def f():\n    return {".py", ".ts"}\n',
        "import tree_sitter_rust\n",
    ):
        assert _find_hardcoded_language_violations(synthetic, "<probe>"), (
            f"AST purity checker missed synthetic hardcoded language probe:\n{synthetic}"
        )

    for py_file in sorted(CITATIONS_DIR.glob("*.py")):
        violations = _find_hardcoded_language_violations(
            py_file.read_text(encoding="utf-8"), py_file.name
        )
        assert violations == [], f"hardcoded language lists found in {py_file.name}: {violations}"

    for py_file in sorted(PRODUCT_SRC.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    assert not _GRAMMAR_PACKAGE_RE.fullmatch(top), (
                        f"{py_file} directly imports grammar package {top}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".", 1)[0]
                assert not _GRAMMAR_PACKAGE_RE.fullmatch(top), (
                    f"{py_file} directly imports from grammar package {top}"
                )

    catalog = language_catalog.load_language_catalog()
    assert catalog.tree_sitter.version == "0.26.1"
    assert ".lean" in catalog.reserved_suffixes
    assert {".h", ".m", ".v"}.issubset(catalog.ambiguous_suffixes)

    tampered = json.loads(language_catalog.catalog_lock_path().read_text(encoding="utf-8"))
    tampered["languages"]["python"]["tagsQuerySha256"] = "0" * 64
    with pytest.raises(LanguageCatalogError, match="SHA-256 verification"):
        language_catalog.parse_language_catalog(tampered, verify_queries=True)


def test_lazy_pinned_download_and_warm_cache_reuse_without_mutating_venv(tmp_path: Path) -> None:
    """AC-2: cold parse lazily downloads and verifies into AR_LANGUAGE_CACHE; warm parse uses zero network."""
    so_path = _cached_python_binding_so()
    wheel_bytes, wheel_sha = _build_zip_wheel(
        {
            f"tree_sitter_python/{so_path.name}": so_path.read_bytes(),
            "tree_sitter_python/__init__.py": b"from ._binding import language\n",
        }
    )
    wheel_name = _current_wheel_filename("tree_sitter_python", "0.25.0")
    catalog = language_catalog.load_language_catalog()
    base_py = catalog.get("python")
    assert base_py is not None
    wheel_url = f"https://files.pythonhosted.org/synthetic/{wheel_name}"
    py_spec = CatalogLanguageSpec(
        id="python",
        provider_kind="pypi_wheel",
        version=base_py.version,
        suffixes=base_py.suffixes,
        package=base_py.package,
        module=base_py.module,
        symbol=base_py.symbol,
        tags_query_relpath=base_py.tags_query_relpath,
        tags_query_sha256=base_py.tags_query_sha256,
        wrappers=base_py.wrappers,
        destructuring=base_py.destructuring,
        wheels=(WheelArtifact(filename=wheel_name, url=wheel_url, sha256=wheel_sha),),
    )
    synthetic_catalog = LanguageCatalog(
        schema=catalog.schema,
        tree_sitter=catalog.tree_sitter,
        reserved_suffixes=catalog.reserved_suffixes,
        ambiguous_suffixes=catalog.ambiguous_suffixes,
        language_pack=catalog.language_pack,
        languages={"python": py_spec},
    )

    cold_cache = tmp_path / "cold-grammars"
    downloads: list[str] = []

    def fake_download(url: str) -> bytes:
        downloads.append(url)
        return wheel_bytes

    lang_set = language_scanner.build_language_set(
        catalog=synthetic_catalog,
        cache_root=cold_cache,
        downloader=fake_download,
    )
    venv_before = sorted(p.name for p in (Path(sys.prefix) / "lib").rglob("tree_sitter_*"))

    with grammars.language_set_scope(lang_set):
        bound = extents.definitions("demo.py", ["def lazy_target():", "    return 42"])
    assert "lazy_target" in bound
    assert downloads == [wheel_url]
    assert (cold_cache / "python" / language_cache.RECEIPT_FILENAME).is_file()
    venv_after = sorted(p.name for p in (Path(sys.prefix) / "lib").rglob("tree_sitter_*"))
    assert venv_before == venv_after

    # Clear in-memory cache and re-parse with a network-forbidding downloader to prove warm reuse.
    language_cache._LOADED_LANGUAGES.clear()

    def forbid_network(url: str) -> bytes:
        raise AssertionError(f"warm cache must not touch network: {url}")

    warm_set = language_scanner.build_language_set(
        catalog=synthetic_catalog,
        cache_root=cold_cache,
        downloader=forbid_network,
    )
    with grammars.language_set_scope(warm_set):
        warm_bound = extents.definitions("demo.py", ["def second_call():", "    return 7"])
    assert "second_call" in warm_bound


def test_language_pack_provider_lazy_install_and_hash_verification(tmp_path: Path) -> None:
    """Rule 1/2: language_pack provider installs into isolated _language_pack dir with SHA-256 verification."""
    so_path = _cached_python_binding_so()
    pack_init = (
        b"from ._binding import language as _py_lang\n"
        b"def get_language(name):\n"
        b"    if name == 'py_pack':\n"
        b"        return _py_lang()\n"
        b"    raise KeyError(name)\n"
    )
    wheel_bytes, wheel_sha = _build_zip_wheel(
        {
            f"tree_sitter_language_pack/{so_path.name}": so_path.read_bytes(),
            "tree_sitter_language_pack/__init__.py": pack_init,
        }
    )
    wheel_name = _current_wheel_filename("tree_sitter_language_pack", "1.0.0")
    wheel_url = f"https://files.pythonhosted.org/synthetic/{wheel_name}"
    catalog = language_catalog.load_language_catalog()
    base_py = catalog.get("python")
    assert base_py is not None
    pack_pin = LanguagePackPin(
        package="tree-sitter-language-pack",
        version="1.0.0",
        module="tree_sitter_language_pack",
        getter="get_language",
        wheels=(WheelArtifact(filename=wheel_name, url=wheel_url, sha256=wheel_sha),),
    )
    pack_spec = CatalogLanguageSpec(
        id="py_pack",
        provider_kind="language_pack",
        version="1.0.0",
        suffixes=frozenset({".pypack"}),
        package=pack_pin.package,
        module=pack_pin.module,
        symbol=pack_pin.getter,
        pack_language="py_pack",
        tags_query_relpath=base_py.tags_query_relpath,
        tags_query_sha256=base_py.tags_query_sha256,
    )
    pack_catalog = LanguageCatalog(
        schema=catalog.schema,
        tree_sitter=catalog.tree_sitter,
        reserved_suffixes=catalog.reserved_suffixes,
        ambiguous_suffixes=catalog.ambiguous_suffixes,
        language_pack=pack_pin,
        languages={"py_pack": pack_spec},
    )
    cache_dir = tmp_path / "pack-cache"
    lang_set = language_scanner.build_language_set(
        catalog=pack_catalog,
        cache_root=cache_dir,
        downloader=lambda _url: wheel_bytes,
    )
    with grammars.language_set_scope(lang_set):
        bound = extents.definitions("script.pypack", ["def from_pack():", "    return 1"])
    assert "from_pack" in bound
    assert (cache_dir / "_language_pack" / language_cache.RECEIPT_FILENAME).is_file()


def test_corrupt_hash_and_download_failure_preserve_read_write_asymmetry(tmp_path: Path) -> None:
    """AC-2: hash/download errors degrade gracefully on reads and refuse actionably on writes."""
    catalog = language_catalog.load_language_catalog()
    py_spec = catalog.get("python")
    assert py_spec is not None
    cache_dir = tmp_path / "broken-cache"

    # Corrupt hash refuses before unpacking anything into AR_LANGUAGE_CACHE.
    with pytest.raises(GrammarUnavailableError, match="SHA-256 mismatch"):
        language_cache.ensure_catalog_language(
            py_spec,
            catalog=catalog,
            cache_root=cache_dir,
            downloader=lambda _url: b"corrupt-wheel-bytes",
        )
    assert not (cache_dir / "python").exists()

    # Network failure degrades on read_anchors and refuses actionably on CodeSnapshot.resolve.
    def fail_download(url: str) -> bytes:
        raise GrammarUnavailableError(f"network unreachable for {url}")

    failing_set = language_scanner.build_language_set(
        catalog=catalog,
        cache_root=cache_dir,
        downloader=fail_download,
    )
    repo = tmp_path / "repo"
    init_repository(repo)
    commit = commit_files(repo, {"mod.py": "def fn():\n    return 1\n"}, "add mod.py")
    snapshot = CodeSnapshot.at_commit(repo, commit)
    blob = snapshot.blobs["mod.py"]
    claim = {
        "anchor_id": "00000000-0000-0000-0000-000000000099",
        "path": "mod.py",
        "source_identity": {"object_id": blob},
        "locator": {"kind": "symbol", "language": "python", "qualified_name": "fn"},
    }
    BLOB_DEFINITIONS.clear()
    try:
        with grammars.language_set_scope(failing_set):
            observed = read_anchors.observe_anchor(
                claim, repository_root=repo, tree_id=snapshot.tree
            )
            assert observed.resolution == "unsupported_locator"
            assert "network unreachable" in (observed.detail or "")

            with pytest.raises(AnchorResolutionError) as exc_info:
                snapshot.resolve("mod.py", {"kind": "symbol", "name": "fn"})
            message = str(exc_info.value)
            assert "network unreachable" in message
            assert str(cache_dir) in message
            assert "languages warm" in message
    finally:
        BLOB_DEFINITIONS.clear()


def test_repo_auto_scan_ambiguous_suffixes_overrides_shebangs_and_lean_reservation(
    tmp_path: Path,
) -> None:
    """AC-2: ambiguous suffixes default to occurrence, overrides work, and .lean stays reserved."""
    repo = tmp_path / "sample-repo"
    init_repository(repo)
    commit_files(
        repo,
        {
            "src/app.py": "def main():\n    return 1\n",
            "src/ui.ts": "export function view() { return 1; }\n",
            "proofs/Main.lean": "def theoremOne : Nat := 1\n",
            "include/header.h": "int x = 1;\n",
            "legacy/module.m": "% matlab or objc\n",
            "bin/tool": "#!/usr/bin/env python3\ndef cli():\n    pass\n",
        },
        "seed repo",
    )

    report = language_scanner.scan_repository_languages(repo, include_user_settings=False)
    assert set(report.detected) == {"lean", "python", "typescript"}
    assert report.builtin_languages == ("lean",)
    assert set(report.catalog_languages) == {"python", "typescript"}
    assert report.ambiguous_suffixes == (".h", ".m")

    default_repo_set = language_scanner.build_repository_language_set(
        repo, include_user_settings=False
    )
    assert default_repo_set.resolve("include/header.h") is None
    assert default_repo_set.resolve("legacy/module.m") is None
    assert default_repo_set.grammar_of("proofs/Main.lean") == LEAN

    # Opt .h in via languages.json glob override and disable typescript.
    (repo / "languages.json").write_text(
        json.dumps(
            {
                "globs": {"**/*.h": "python"},
                "disable": ["typescript"],
            }
        ),
        encoding="utf-8",
    )
    overridden_report = language_scanner.scan_repository_languages(
        repo, include_user_settings=False
    )
    assert "typescript" not in overridden_report.detected
    assert ".h" not in overridden_report.ambiguous_suffixes
    assert overridden_report.ambiguous_suffixes == (".m",)

    overridden_set = language_scanner.build_repository_language_set(
        repo, include_user_settings=False
    )
    assert overridden_set.grammar_of("include/header.h") == "python"
    assert overridden_set.grammar_of("src/ui.ts") is None
    assert overridden_set.extractor_version != default_repo_set.extractor_version

    # .lean cannot be overridden or disabled via languages.json.
    for bad_cfg in (
        {"suffixes": {".lean": "python"}},
        {"globs": {"**/*.lean": "python"}},
        {"disable": ["lean"]},
    ):
        (repo / "languages.json").write_text(json.dumps(bad_cfg), encoding="utf-8")
        with pytest.raises(LanguageConfigError, match="reserved"):
            language_config.load_language_overrides(repo, include_user_settings=False)


def test_custom_local_language_bend2_end_to_end_and_abi_check(tmp_path: Path) -> None:
    """AC-3: custom local .so + tags.scm + ABI check + EXTRACTOR_VERSION hashing end-to-end."""
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    assert cc is not None, "a C compiler is required to build the custom local .so fixture"

    # Compile a tiny real shared library exporting a valid TSLanguage pointer wrapper and a bad-ABI stub.
    c_src = tmp_path / "bend2_fixture.c"
    c_src.write_text(
        """
#include <stdint.h>
static const void *g_lang_ptr = 0;
void ar_test_set_bend2_lang(const void *ptr) { g_lang_ptr = ptr; }
const void *tree_sitter_bend2(void) { return g_lang_ptr; }

struct FakeTsLanguage {
    uint32_t abi_version;
    uint32_t pad[64];
};
static struct FakeTsLanguage g_bad_abi_lang = { 99u };
const void *tree_sitter_bend2_bad_abi(void) { return &g_bad_abi_lang; }
""",
        encoding="utf-8",
    )
    so_path = tmp_path / "libtree-sitter-bend2.so"
    subprocess.run(
        [cc, "-shared", "-fPIC", "-O2", str(c_src), "-o", str(so_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    # Wire the compiled shared library's tree_sitter_bend2() symbol to a real valid TSLanguage*.
    real_ptr = _extract_language_pointer("python")
    handle = ctypes.CDLL(str(so_path))
    setter = handle.ar_test_set_bend2_lang
    setter.argtypes = [ctypes.c_void_p]
    setter.restype = None
    setter(real_ptr)

    tags_path = tmp_path / "bend2-tags.scm"
    tags_path.write_text(
        "(function_definition name: (identifier) @name) @definition.function\n",
        encoding="utf-8",
    )

    repo = tmp_path / "bend2-repo"
    init_repository(repo)
    (repo / "languages.json").write_text(
        json.dumps(
            {
                "custom": [
                    {
                        "id": "bend2",
                        "suffixes": [".bend"],
                        "libraryPath": str(so_path),
                        "symbol": "tree_sitter_bend2",
                        "tagsQueryPath": str(tags_path),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    commit = commit_files(
        repo,
        {
            "languages.json": (repo / "languages.json").read_text(encoding="utf-8"),
            "src/eval.bend": "def bend_eval(x):\n    return x\n",
        },
        "add bend2 source",
    )

    scan = language_scanner.scan_repository_languages(repo, include_user_settings=False)
    assert "bend2" in scan.custom_languages

    repo_set = language_scanner.build_repository_language_set(repo, include_user_settings=False)
    bend_provider = repo_set.resolve("src/eval.bend")
    assert bend_provider is not None
    assert bend_provider.key == "bend2"
    so_digest = hashlib.sha256(so_path.read_bytes()).hexdigest()[:16]
    assert f"custom:bend2:{so_digest}" in bend_provider.version
    assert f"custom:bend2:{so_digest}" in repo_set.extractor_version

    snapshot = CodeSnapshot.at_commit(repo, commit)
    BLOB_DEFINITIONS.clear()
    try:
        with grammars.language_set_scope(repo_set):
            anchor = snapshot.resolve("src/eval.bend", {"kind": "symbol", "name": "bend_eval"})
            assert anchor.blob == snapshot.blobs["src/eval.bend"]
            claim = {
                "anchor_id": "00000000-0000-0000-0000-000000000101",
                "path": "src/eval.bend",
                "source_identity": {"object_id": anchor.blob},
                "locator": {
                    "kind": "symbol",
                    "language": "bend2",
                    "qualified_name": "bend_eval",
                },
            }
            observed = read_anchors.observe_anchor(
                claim, repository_root=repo, tree_id=snapshot.tree
            )
            assert observed.resolution == "exact_recorded_blob"
            assert observed.resolved_ranges is not None
            assert [(r.start_line, r.end_line) for r in observed.resolved_ranges] == [(1, 2)]
    finally:
        BLOB_DEFINITIONS.clear()

    # ABI mismatch (ABI 99 outside 13..15) is refused with GrammarUnavailableError.
    with pytest.raises(GrammarUnavailableError, match="ABI"):
        language_cache.load_custom_library_language(
            so_path,
            "tree_sitter_bend2_bad_abi",
            language_id="bend2_bad",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "custom": [
                {
                    "id": "evil",
                    "suffixes": [".evil"],
                    "libraryPath": "https://attacker.example/evil.so",
                    "symbol": "tree_sitter_evil",
                    "tagsQueryPath": "/tmp/tags.scm",
                }
            ]
        },
        {
            "custom": [
                {
                    "id": "evil",
                    "suffixes": [".evil"],
                    "libraryPath": "/tmp/evil.so",
                    "symbol": "tree_sitter_evil",
                    "tagsQueryPath": "http://attacker.example/tags.scm",
                }
            ]
        },
        {
            "custom": [
                {
                    "id": "evil",
                    "suffixes": [".evil"],
                    "libraryPath": "//attacker.example/evil.so",
                    "symbol": "tree_sitter_evil",
                    "tagsQueryPath": "/tmp/tags.scm",
                }
            ]
        },
        {
            "custom": [
                {
                    "id": "evil",
                    "suffixes": [".evil"],
                    "libraryPath": "/tmp/evil.so",
                    "downloadUrl": "https://attacker.example/evil.whl",
                    "symbol": "tree_sitter_evil",
                    "tagsQueryPath": "/tmp/tags.scm",
                }
            ]
        },
        {"url": "https://attacker.example/catalog.json"},
    ],
)
def test_languages_json_security_boundary_rejects_remote_urls(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    """AC-3: repo languages.json must never specify a remote binary or query URL."""
    (tmp_path / "languages.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LanguageConfigError, match=r"remote|forbidden"):
        language_config.load_language_overrides(tmp_path, include_user_settings=False)


def test_cli_languages_scan_warm_and_ci_bootstrap_contracts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC-4: CLI scan/warm works cleanly and Dagger/profile/bootstrap declare pre-warm contracts."""
    repo = tmp_path / "cli-repo"
    init_repository(repo)
    commit_files(repo, {"mod.py": "x = 1\n", "app.ts": "export const y = 2;\n"}, "init")

    assert cli_main.main(["languages", "scan", "--repo", str(repo), "--json"]) == 0
    scan_out = json.loads(capsys.readouterr().out)
    assert set(scan_out["detected"]) == {"python", "typescript"}

    assert (
        cli_main.main(
            [
                "languages",
                "warm",
                "--repo",
                str(repo),
                "--cache-dir",
                str(language_cache.resolve_language_cache_root()),
                "--json",
            ]
        )
        == 0
    )
    warm_out = json.loads(capsys.readouterr().out)
    assert warm_out["ok"] is True
    assert set(warm_out["alreadyCached"]) >= {"python", "typescript"}

    dagger_main = (REPO_ROOT / ".dagger/src/agents_remember_quality/main.py").read_text(
        encoding="utf-8"
    )
    assert 'LANGUAGE_CACHE_ROOT = "/opt/ar-language-cache"' in dagger_main
    assert 'with_env_variable("AR_LANGUAGE_CACHE", LANGUAGE_CACHE_ROOT)' in dagger_main
    assert dagger_main.count("LANGUAGE_CACHE_VOLUME") >= 3
    assert '"languages",\n                "warm",' in dagger_main

    profile = json.loads((REPO_ROOT / "mcp/certification-profile-v1.json").read_text("utf-8"))
    for rail in profile["rails"]:
        assert "AR_LANGUAGE_CACHE" in rail["execution"]["environmentContract"], (
            f"rail {rail['identity']['railId']} missing AR_LANGUAGE_CACHE in environmentContract"
        )

    bootstrap = (REPO_ROOT / "scripts/bootstrap-mcp-venv.sh").read_text(encoding="utf-8")
    assert 'languages warm --repo "$repo_root"' in bootstrap
    assert "AR_SKIP_LANGUAGE_WARM" not in bootstrap
    trap_release = bootstrap.index("trap - EXIT")
    warm_index = bootstrap.index('languages warm --repo "$repo_root"')
    assert trap_release < warm_index, (
        "bootstrap languages warm must execute after trap - EXIT outside the venv transaction"
    )


def test_unknown_enable_disable_ids_and_catalog_shadowing_are_refused(tmp_path: Path) -> None:
    """Rule 3 config identity: typos in enable/disable and catalog-ID shadowing fail loudly."""
    repo_typo_disable = tmp_path / "typo-disable"
    repo_typo_disable.mkdir()
    (repo_typo_disable / "languages.json").write_text(
        json.dumps({"disable": ["typescrip"]}), encoding="utf-8"
    )
    with pytest.raises(LanguageConfigError, match="unknown language id 'typescrip'"):
        language_scanner.scan_repository_languages(repo_typo_disable, include_user_settings=False)
    with pytest.raises(LanguageConfigError, match="unknown language id 'typescrip'"):
        language_scanner.build_repository_language_set(
            repo_typo_disable, include_user_settings=False
        )

    repo_typo_enable = tmp_path / "typo-enable"
    repo_typo_enable.mkdir()
    (repo_typo_enable / "languages.json").write_text(
        json.dumps({"enable": ["pythn"]}), encoding="utf-8"
    )
    with pytest.raises(LanguageConfigError, match="unknown language id 'pythn'"):
        language_scanner.scan_repository_languages(repo_typo_enable, include_user_settings=False)
    with pytest.raises(LanguageConfigError, match="unknown language id 'pythn'"):
        language_scanner.build_repository_language_set(
            repo_typo_enable, include_user_settings=False
        )

    repo_shadow = tmp_path / "shadow-catalog"
    repo_shadow.mkdir()
    (repo_shadow / "libfake.so").write_bytes(b"\x7fELF")
    (repo_shadow / "tags.scm").write_text("(identifier) @name\n", encoding="utf-8")
    (repo_shadow / "languages.json").write_text(
        json.dumps(
            {
                "custom": [
                    {
                        "id": "python",
                        "suffixes": [".py"],
                        "libraryPath": "libfake.so",
                        "symbol": "tree_sitter_fake",
                        "tagsQueryPath": "tags.scm",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(LanguageConfigError, match="cannot shadow catalog language 'python'"):
        language_scanner.scan_repository_languages(repo_shadow, include_user_settings=False)
    with pytest.raises(LanguageConfigError, match="cannot shadow catalog language 'python'"):
        language_scanner.build_repository_language_set(repo_shadow, include_user_settings=False)


def test_repository_languages_json_and_custom_bend2_wire_through_production_consumers(
    tmp_path: Path,
) -> None:
    """Rule 3 / AC-2 / AC-3: production read/write/conversion/currentness/memo bind repo languages.json."""
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("C compiler unavailable to compile custom Bend2 shared library fixture")
        return

    repo = tmp_path / "wired-repo"
    init_repository(repo)
    grammars_dir = repo / "grammars"
    grammars_dir.mkdir()

    py_ptr = _extract_language_pointer("python")
    bend_c = grammars_dir / "bend2_prod.c"
    bend_so = grammars_dir / "libbend2_prod.so"
    bend_c.write_text(
        f"#include <stdint.h>\n"
        f"void *tree_sitter_bend2_prod(void) {{ return (void *)(uintptr_t){py_ptr}ULL; }}\n",
        encoding="utf-8",
    )
    subprocess.run([cc, "-shared", "-fPIC", str(bend_c), "-o", str(bend_so)], check=True)
    bend_tags = grammars_dir / "bend2-tags.scm"
    bend_tags.write_text(
        "(function_definition name: (identifier) @name) @definition.function\n",
        encoding="utf-8",
    )

    config_payload = {
        "globs": {"**/*.h": "python"},
        "disable": ["typescript"],
        "custom": [
            {
                "id": "bend2",
                "suffixes": [".bend2"],
                "libraryPath": "grammars/libbend2_prod.so",
                "symbol": "tree_sitter_bend2_prod",
                "tagsQueryPath": "grammars/bend2-tags.scm",
            }
        ],
    }
    commit_files(
        repo,
        {
            "languages.json": json.dumps(config_payload, indent=2),
            "include/header.h": "def header_symbol():\n    return 1\n",
            "src/app.ts": "export function appSymbol() { return 2; }\n",
            "src/demo.bend2": "def bend_prod_fn():\n    return 3\n",
        },
        "init wired repo",
    )

    # 1. Write path (CodeSnapshot.capture -> resolve) without any manual language_set_scope.
    snapshot = CodeSnapshot.capture(repo)
    header_anchor = snapshot.resolve(
        "include/header.h", {"kind": "symbol", "name": "header_symbol"}
    )
    bend_anchor = snapshot.resolve("src/demo.bend2", {"kind": "symbol", "name": "bend_prod_fn"})
    with pytest.raises(AnchorResolutionError, match="no grammar or scanner"):
        snapshot.resolve("src/app.ts", {"kind": "symbol", "name": "appSymbol"})

    # 2. Read path (read_anchors.observe_anchor) without any manual language_set_scope.
    header_obs = read_anchors.observe_anchor(
        {
            "anchor_id": "11111111-1111-4111-8111-111111111111",
            "path": "include/header.h",
            "source_identity": {"object_id": header_anchor.blob},
            "locator": {"kind": "symbol", "language": "python", "qualified_name": "header_symbol"},
        },
        repository_root=repo,
        tree_id=snapshot.tree,
    )
    assert header_obs.resolution == "exact_recorded_blob"

    bend_obs = read_anchors.observe_anchor(
        {
            "anchor_id": "22222222-2222-4222-8222-222222222222",
            "path": "src/demo.bend2",
            "source_identity": {"object_id": bend_anchor.blob},
            "locator": {"kind": "symbol", "language": "bend2", "qualified_name": "bend_prod_fn"},
        },
        repository_root=repo,
        tree_id=snapshot.tree,
    )
    assert bend_obs.resolution == "exact_recorded_blob"

    ts_obs = read_anchors.observe_anchor(
        {
            "anchor_id": "33333333-3333-4333-8333-333333333333",
            "path": "src/app.ts",
            "source_identity": {"object_id": snapshot.blobs["src/app.ts"]},
            "locator": {"kind": "symbol", "language": "typescript", "qualified_name": "appSymbol"},
        },
        repository_root=repo,
        tree_id=snapshot.tree,
    )
    assert ts_obs.resolution == "unsupported_locator"

    # 3. Conversion / CodeObjects path without any manual language_set_scope.
    objects = CodeObjects(repo)
    assert objects.symbol_span("include/header.h", header_anchor.blob, "header_symbol") == (1, 2)
    assert objects.symbol_span("src/demo.bend2", bend_anchor.blob, "bend_prod_fn") == (1, 2)
    assert objects.symbol_span("src/app.ts", snapshot.blobs["src/app.ts"], "appSymbol") is None

    # 4. Currentness observation path (changed blob, preserved symbol content).
    commit_files(
        repo,
        {
            "src/demo.bend2": "# comment shifted line\ndef bend_prod_fn():\n    return 3\n",
            "src/app.ts": "// shifted\nexport function appSymbol() { return 2; }\n",
        },
        "shift lines",
    )
    shifted = CodeSnapshot.capture(repo)
    opened = currentness_observe.open_code_tree(
        currentness_observe.CodeTree(repository=repo, tree=shifted.tree)
    )
    bend_entry = Entry(
        id="RLZ-BEND01",
        kind="realization",
        invariant="INV-BEND01",
        path="src/demo.bend2",
        sidecar="onboarding/src/demo.bend2.json",
        document={
            "anchor": {
                "blob": bend_anchor.blob,
                "content": bend_anchor.content,
                "locator": {"kind": "symbol", "name": "bend_prod_fn"},
            }
        },
    )
    bend_currentness = currentness_observe.observe_entry(bend_entry, opened)
    assert bend_currentness.state == "current"

    ts_entry = Entry(
        id="RLZ-TS01",
        kind="realization",
        invariant="INV-TS01",
        path="src/app.ts",
        sidecar="onboarding/src/app.ts.json",
        document={
            "anchor": {
                "blob": snapshot.blobs["src/app.ts"],
                "content": "sha256:" + "0" * 64,
                "locator": {"kind": "symbol", "name": "appSymbol"},
            }
        },
    )
    ts_currentness = currentness_observe.observe_entry(ts_entry, opened)
    assert ts_currentness.state == "unverifiable"

    # 5. Reference state check path (working tree check_references).
    memory_root = tmp_path / "memory"
    sidecar_path = memory_root / "onboarding" / "src" / "demo.bend2.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(
        json.dumps(
            {
                "schema": "ar-onboarding-file/v1",
                "path": "src/demo.bend2",
                "references": {
                    "1": {
                        "targets": [
                            {
                                "kind": "code",
                                "anchor": {
                                    "blob": bend_anchor.blob,
                                    "content": bend_anchor.content,
                                    "locator": {"kind": "symbol", "name": "bend_prod_fn"},
                                },
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    ref_result = reference_state.check_references(memory_root, repo)
    assert ref_result["states"] == {"current": 1}

    # 6. Review-memo namespace includes the repository's configured extractor identity.
    default_ns = memo_namespace("build-digest", repo, language_set=grammars.default_language_set())
    repo_ns = memo_namespace("build-digest", repo, code_root=repo)
    assert repo_ns != default_ns
