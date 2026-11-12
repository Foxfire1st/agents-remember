"""Explicit grade producers and their concrete local Python dependency sources."""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError

from .artifacts import sha256_file

SCHEMA = "ar-grade-producer/v1"
_ROOT = Path(__file__).resolve().parents[2]
_PACKAGES = {
    "agent_evals": "scripts/agent_evals",
    "agents_remember": "mcp/src/agents_remember",
    "agents_remember_test_support": "mcp/test_support/agents_remember_test_support",
}


def _relative(producer: str | Path, root: Path) -> Path:
    path = Path(producer)
    if not path.is_absolute():
        path = root / path
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise AuthorityError("Grade producer must be inside the code root") from error
    if relative.suffix != ".py" or not path.is_file():
        raise AuthorityError(f"Grade producer source is unavailable: {relative}")
    return relative


def _module(relative: Path) -> tuple[str, bool]:
    value = relative.as_posix()
    for package, directory in _PACKAGES.items():
        prefix = directory + "/"
        if value.startswith(prefix):
            parts = value.removeprefix(prefix).removesuffix(".py").split("/")
            is_package = parts[-1] == "__init__"
            if is_package:
                parts.pop()
            return ".".join((package, *parts)), is_package
    raise AuthorityError(f"Grade source is outside supported local packages: {relative}")


def _module_files(name: str, root: Path) -> set[Path]:
    """Resolve a local import and the package initializers Python loads for it."""
    parts = name.split(".")
    directory = _PACKAGES.get(parts[0])
    if directory is None:
        return set()  # External packages are not local source files.
    base = Path(directory)
    stem = base.joinpath(*parts[1:])
    module = stem.with_suffix(".py")
    package = stem / "__init__.py"
    resolved = module if (root / module).is_file() else package
    if not (root / resolved).is_file():
        return set()
    files = {resolved}
    for length in range(len(parts)):
        init = base.joinpath(*parts[1 : length + 1], "__init__.py")
        if (root / init).is_file():
            files.add(init)
    return files


@lru_cache(maxsize=512)
def _import_names(module: str, is_package: bool, text: str) -> tuple[str, ...]:
    """Only cache parsing of identical bytes; file hashes and resolution stay fresh."""
    context = module.split(".") if is_package else module.split(".")[:-1]
    names: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > len(context):
                    raise AuthorityError(f"Invalid relative import in grade source: {module}")
                base = context[: len(context) - node.level + 1]
                name = ".".join((*base, *((node.module or "").split(".") if node.module else ())))
            else:
                name = node.module or ""
            names.add(name)
            for alias in node.names:
                if alias.name != "*":
                    names.add(name + "." + alias.name)
    return tuple(sorted(names))


def _imports(relative: Path, root: Path) -> set[Path]:
    module, is_package = _module(relative)
    names = _import_names(module, is_package, (root / relative).read_text(encoding="utf-8"))
    return {path for name in names for path in _module_files(name, root)}


def current_grade_sources(producer: str | Path, *, code_root: Path | None = None) -> dict[str, Any]:
    """Capture the producer's conservative transitive local Python import closure.

    The closure follows imported modules, including product implementation helpers;
    it never enumerates every source file merely because it shares the repository.
    """
    root = code_root or _ROOT
    primary = _relative(producer, root)
    visited: set[Path] = set()
    pending = {primary}
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        pending.update(_imports(path, root) - visited)
    sources = [
        {"path": path.as_posix(), "sha256": sha256_file(root / path)}
        for path in sorted(visited - {primary})
    ]
    return {
        "schema": SCHEMA,
        "path": primary.as_posix(),
        "sha256": sha256_file(root / primary),
        "shared_sources": sources,
    }


def _valid_sources(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"schema", "path", "sha256", "shared_sources"}:
        return False
    if value["schema"] != SCHEMA or not isinstance(value["shared_sources"], list):
        return False
    files = [{"path": value["path"], "sha256": value["sha256"]}, *value["shared_sources"]]
    paths = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            return False
        path, digest = item["path"], item["sha256"]
        if (
            not isinstance(path, str)
            or not path.endswith(".py")
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            return False
        paths.append(path)
    return len(paths) == len(set(paths))


def require_grade_sources(value: object) -> None:
    if not _valid_sources(value):
        raise AuthorityError("New grade records require valid explicit producer source metadata")


def inspect_grade_sources(
    record: dict[str, Any],
    *,
    code_root: Path,
    expected_producer: str | Path | None = None,
    current_sources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare recorded bytes with current sources; missing metadata is always stale.

    A caller can capture current_sources once for a census. It is the caller's
    snapshot of the current checkout, never metadata recovered from an old result.
    """
    recorded = record.get("producer")
    if not _valid_sources(recorded):
        return {"status": "stale", "reasons": ["grade producer metadata is missing or invalid"]}
    assert isinstance(recorded, dict)
    try:
        primary = _relative(
            recorded["path"] if expected_producer is None else expected_producer, code_root
        ).as_posix()
        current = (
            current_grade_sources(primary, code_root=code_root)
            if current_sources is None
            else current_sources
        )
    except (OSError, ValueError, SyntaxError, AuthorityError) as error:
        return {"status": "stale", "reasons": [f"current grade source unavailable: {error}"]}
    if not _valid_sources(current) or current["path"] != primary:
        raise AuthorityError("Current grade source snapshot does not match the expected producer")
    reasons = []
    if recorded["path"] != primary:
        reasons.append(f"grade producer changed: {recorded['path']} -> {primary}")
    old = {item["path"]: item["sha256"] for item in recorded["shared_sources"]}
    old[recorded["path"]] = recorded["sha256"]
    new = {item["path"]: item["sha256"] for item in current["shared_sources"]}
    new[current["path"]] = current["sha256"]
    for path in sorted(old.keys() | new.keys()):
        if old.get(path) != new.get(path):
            change = "added" if path not in old else "removed" if path not in new else "changed"
            reasons.append(f"grade source {change}: {path}")
    return {
        "status": "stale" if reasons else "current",
        "reasons": reasons,
        "currentProducer": current,
    }
