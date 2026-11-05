"""Recomputable identities for deterministic and judged evaluation results."""

from __future__ import annotations

import importlib.metadata
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError

from .artifacts import json_digest, sha256_file

SCHEMA = "ar-eval-result-identity/v1"
SOURCE_ROOTS = ("scripts/agent_evals", "mcp/src/agents_remember", "mcp/test_support")
CONFIG_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "mcp/pyproject.toml",
    "mcp/requirements.txt",
    "mcp/uv.lock",
)


def source_snapshot(root: Path) -> dict[str, Any]:
    """Hash a conservative runtime source closure, including product history models."""
    paths = {
        path for name in SOURCE_ROOTS for path in (root / name).rglob("*.py") if path.is_file()
    }
    paths.update(root / name for name in CONFIG_FILES if (root / name).is_file())
    if not paths:
        raise AuthorityError("Grader source closure is empty")
    files = [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
        for path in sorted(paths)
    ]
    packages = sorted(
        {
            (distribution.metadata["Name"], distribution.version)
            for distribution in importlib.metadata.distributions()
            if distribution.metadata["Name"]
        }
    )
    runtime: dict[str, Any] = {
        "pythonVersion": sys.version,
        "pythonImplementation": sys.implementation.name,
        "pythonExecutableSha256": sha256_file(Path(sys.executable).resolve()),
        "dependencies": [{"name": name, "version": version} for name, version in packages],
    }
    git = shutil.which("git")
    runtime["git"] = (
        {"path": str(Path(git).resolve()), "sha256": sha256_file(Path(git))}
        if git
        else {"state": "absent"}
    )
    values = {"sourceRoots": SOURCE_ROOTS, "files": files, "runtime": runtime}
    return {**values, "sha256": json_digest(values)}


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise AuthorityError(f"Result identity {field} must be a non-null SHA-256")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthorityError(f"Result identity {field} must be nonempty text")
    return value


def _no_null(value: Any) -> None:
    if value is None:
        raise AuthorityError("Null is not a result identity")
    if isinstance(value, dict):
        for item in value.values():
            _no_null(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _no_null(item)


def _make(kind: str, components: dict[str, str]) -> dict[str, Any]:
    payload = {"schema": SCHEMA, "kind": kind, "components": components}
    return {**payload, "digest": json_digest(payload)}


def deterministic_identity(
    *,
    grader_id: str,
    source: dict[str, Any],
    capture: dict[str, Any],
    effects: dict[str, Any],
    admission: dict[str, Any],
) -> dict[str, Any]:
    values = {key: value for key, value in source.items() if key != "sha256"}
    if _digest(source.get("sha256"), "graderSourceSha256") != json_digest(values):
        raise AuthorityError("Grader source snapshot digest changed")
    return _make(
        "deterministic",
        {
            "graderId": _text(grader_id, "graderId"),
            "graderSourceSha256": source["sha256"],
            "captureSha256": json_digest(capture),
            "effectTreeInputSha256": json_digest(effects),
            "admissionConfigSha256": json_digest(admission),
        },
    )


def judged_identity(package_sha256: str, judge_model: str) -> dict[str, Any]:
    return _make(
        "judged",
        {
            "packageSha256": _digest(package_sha256, "packageSha256"),
            "judgeModel": _text(judge_model, "judgeModel"),
        },
    )


def validate_result_identity(value: Any) -> dict[str, Any]:
    _no_null(value)
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise AuthorityError("A recorded result identity is required")
    if set(value) != {"schema", "kind", "components", "digest"}:
        raise AuthorityError("Result identity has undeclared or missing fields")
    kind, components = value["kind"], value["components"]
    fields = {
        "deterministic": {
            "graderId",
            "graderSourceSha256",
            "captureSha256",
            "effectTreeInputSha256",
            "admissionConfigSha256",
        },
        "judged": {"packageSha256", "judgeModel"},
    }
    if (
        not isinstance(kind, str)
        or kind not in fields
        or not isinstance(components, dict)
        or set(components) != fields[kind]
    ):
        raise AuthorityError("Result identity components do not match its kind")
    for key, item in components.items():
        _digest(item, key) if key.endswith("Sha256") else _text(item, key)
    expected = _make(kind, components)
    if _digest(value["digest"], "digest") != expected["digest"]:
        raise AuthorityError("Result identity digest does not match its components")
    return value
