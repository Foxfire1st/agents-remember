"""Strict contract for the content-sealed direct-diagnostic cohort."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agents_remember.testing.selection_contract import (
    DependencyObservation,
    DirectRefusalCode,
    UnsafeEffectFamily,
)

COHORT_MANIFEST_PATH = Path("mcp/tests/python-direct-cohort.toml")
COHORT_MANIFEST_SCHEMA = "python-direct-cohort/v2"
POLICY_VERSION = "python-direct-eligibility/v2"
MAX_DIRECT_NODES = 8
MAX_COHORT_FILES = 16
REQUIRED_CONFIGURATION_PATHS = (
    "pyproject.toml",
    "mcp/tests/evidence-lifecycle.toml",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class AuditedPythonFile:
    """One exact file and its reviewed dependency/effect facts."""

    path: str
    sha256: str
    symbols: tuple[str, ...]
    local_imports: tuple[str, ...]
    effects_known: bool
    effects: tuple[UnsafeEffectFamily, ...]
    purpose: str


@dataclass(frozen=True)
class AuditedConfiguration:
    """One execution configuration pinned by the cohort audit."""

    path: str
    sha256: str
    purpose: str


@dataclass(frozen=True)
class CohortNode:
    """One exact node plus its reviewed execution-symbol closure."""

    node_id: str
    closure: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class DirectCohortManifest:
    """The complete bounded policy consumed by the canonical classifier."""

    nodes: tuple[CohortNode, ...]
    python_files: tuple[AuditedPythonFile, ...]
    configuration: tuple[AuditedConfiguration, ...]
    payload: bytes

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(item.node_id for item in self.nodes)


class CohortManifestError(RuntimeError):
    """A controlled policy refusal translated by the classifier once."""

    def __init__(
        self,
        code: DirectRefusalCode,
        message: str,
        *,
        observation: DependencyObservation | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.observation = observation


def load_direct_cohort_manifest(candidate_root: Path) -> DirectCohortManifest:
    """Load the one repository-owned manifest with no compatibility reader."""

    path = candidate_root / COHORT_MANIFEST_PATH
    try:
        payload = path.read_bytes()
        raw = tomllib.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise _error(
            DirectRefusalCode.INVALID_CANDIDATE,
            f"cannot read the canonical direct cohort manifest: {error}",
        ) from error
    if not isinstance(raw, Mapping):
        raise _error(DirectRefusalCode.INVALID_CANDIDATE, "manifest root must be a table")
    _exact_keys(
        raw,
        {
            "schema_version",
            "policy_version",
            "max_selection",
            "python_file",
            "configuration",
            "node",
        },
        "manifest",
    )
    if raw.get("schema_version") != COHORT_MANIFEST_SCHEMA:
        raise _invalid(f"schema_version must be {COHORT_MANIFEST_SCHEMA!r}")
    if raw.get("policy_version") != POLICY_VERSION:
        raise _invalid(f"policy_version must be {POLICY_VERSION!r}")
    if raw.get("max_selection") != MAX_DIRECT_NODES:
        raise _invalid(f"max_selection must remain {MAX_DIRECT_NODES}")
    python_files = tuple(_python_file(item, index) for index, item in _tables(raw, "python_file"))
    configuration = tuple(
        _configuration(item, index) for index, item in _tables(raw, "configuration")
    )
    nodes = tuple(_node(item, index) for index, item in _tables(raw, "node"))
    _validate_population(python_files, configuration, nodes)
    return DirectCohortManifest(nodes, python_files, configuration, payload)


def _python_file(raw: Mapping[str, object], index: int) -> AuditedPythonFile:
    label = f"python_file[{index}]"
    _exact_keys(
        raw,
        {
            "path",
            "sha256",
            "symbols",
            "local_imports",
            "effects_known",
            "effects",
            "purpose",
        },
        label,
    )
    effects_known = raw.get("effects_known")
    if not isinstance(effects_known, bool):
        raise _invalid(f"{label}.effects_known must be a boolean")
    try:
        effects = tuple(UnsafeEffectFamily(value) for value in _texts(raw, "effects", label))
    except ValueError as error:
        raise _invalid(f"{label}.effects contains an unknown family: {error}") from error
    return AuditedPythonFile(
        path=_path(raw, "path", label, suffix=".py"),
        sha256=_digest(raw, label),
        symbols=_texts(raw, "symbols", label),
        local_imports=tuple(
            _normalized_path(value, f"{label}.local_imports", suffix=".py")
            for value in _texts(raw, "local_imports", label)
        ),
        effects_known=effects_known,
        effects=effects,
        purpose=_text(raw, "purpose", label),
    )


def _configuration(raw: Mapping[str, object], index: int) -> AuditedConfiguration:
    label = f"configuration[{index}]"
    _exact_keys(raw, {"path", "sha256", "purpose"}, label)
    return AuditedConfiguration(
        path=_path(raw, "path", label),
        sha256=_digest(raw, label),
        purpose=_text(raw, "purpose", label),
    )


def _node(raw: Mapping[str, object], index: int) -> CohortNode:
    label = f"node[{index}]"
    _exact_keys(raw, {"id", "closure", "rationale"}, label)
    return CohortNode(
        node_id=_text(raw, "id", label),
        closure=_texts(raw, "closure", label),
        rationale=_text(raw, "rationale", label),
    )


def _validate_population(
    python_files: tuple[AuditedPythonFile, ...],
    configuration: tuple[AuditedConfiguration, ...],
    nodes: tuple[CohortNode, ...],
) -> None:
    _require_population_limits(python_files, nodes)
    _require_unique_population(python_files, configuration, nodes)
    _require_configuration_paths(configuration)
    files = {item.path: item for item in python_files}
    symbols = {f"{item.path}::{symbol}" for item in python_files for symbol in item.symbols}
    _require_local_import_closure(python_files, files)
    _require_node_closure(nodes, files, symbols)
    _require_reachable_python_files(files, nodes)


def _require_population_limits(
    python_files: tuple[AuditedPythonFile, ...],
    nodes: tuple[CohortNode, ...],
) -> None:
    if len(python_files) > MAX_COHORT_FILES:
        raise _invalid(f"cohort may audit at most {MAX_COHORT_FILES} Python files")
    if len(nodes) > MAX_DIRECT_NODES:
        raise _invalid(f"cohort may contain at most {MAX_DIRECT_NODES} exact nodes")


def _require_unique_population(
    python_files: tuple[AuditedPythonFile, ...],
    configuration: tuple[AuditedConfiguration, ...],
    nodes: tuple[CohortNode, ...],
) -> None:
    _unique((item.path for item in python_files), "python_file paths")
    _unique((item.path for item in configuration), "configuration paths")
    _unique((item.node_id for item in nodes), "node ids")


def _require_configuration_paths(configuration: tuple[AuditedConfiguration, ...]) -> None:
    config_paths = tuple(item.path for item in configuration)
    if config_paths != REQUIRED_CONFIGURATION_PATHS:
        raise _invalid(f"configuration paths must be {REQUIRED_CONFIGURATION_PATHS!r}")


def _require_local_import_closure(
    python_files: tuple[AuditedPythonFile, ...],
    files: Mapping[str, AuditedPythonFile],
) -> None:
    for item in python_files:
        missing = sorted(set(item.local_imports) - set(files))
        if missing:
            raise _error(
                DirectRefusalCode.UNRESOLVED_DEPENDENCY,
                f"{item.path} declares unaudited local imports: {missing}",
                path=item.path,
            )


def _require_node_closure(
    nodes: tuple[CohortNode, ...],
    files: Mapping[str, AuditedPythonFile],
    symbols: set[str],
) -> None:
    for node in nodes:
        path = node.node_id.split("::", maxsplit=1)[0]
        if path not in files:
            raise _invalid(f"node {node.node_id!r} has no audited Python file")
        missing = sorted(set(node.closure) - symbols)
        if missing:
            raise _error(
                DirectRefusalCode.UNRESOLVED_DEPENDENCY,
                f"node {node.node_id!r} has unresolved closure symbols: {missing}",
                path=path,
            )
        if node.node_id not in node.closure:
            raise _invalid(f"node {node.node_id!r} must include itself in closure")


def _require_reachable_python_files(
    files: Mapping[str, AuditedPythonFile],
    nodes: tuple[CohortNode, ...],
) -> None:
    """Reject audited files that do not belong to any admitted execution closure."""

    pending = [reference.split("::", maxsplit=1)[0] for node in nodes for reference in node.closure]
    reachable: set[str] = set()
    while pending:
        path = pending.pop()
        if path in reachable:
            continue
        reachable.add(path)
        pending.extend(files[path].local_imports)
    unused = sorted(set(files) - reachable)
    if unused:
        raise _error(
            DirectRefusalCode.UNRESOLVED_DEPENDENCY,
            f"audited Python files are unreachable from every cohort node: {unused}",
            path=unused[0],
        )


def _tables(
    raw: Mapping[str, object],
    key: str,
) -> tuple[tuple[int, Mapping[str, object]], ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise _invalid(f"[[{key}]] must be a non-empty table list")
    tables: list[tuple[int, Mapping[str, object]]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise _invalid(f"{key}[{index}] must be a table")
        tables.append((index, item))
    return tuple(tables)


def _exact_keys(raw: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(raw)
    if actual != expected:
        raise _invalid(
            f"{label} fields differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _text(raw: Mapping[str, object], key: str, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{label}.{key} must be non-empty text")
    return value.strip()


def _texts(raw: Mapping[str, object], key: str, label: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise _invalid(f"{label}.{key} must be a string list")
    normalized = tuple(item.strip() for item in value)
    _unique(normalized, f"{label}.{key}")
    return normalized


def _path(
    raw: Mapping[str, object],
    key: str,
    label: str,
    *,
    suffix: str | None = None,
) -> str:
    return _normalized_path(_text(raw, key, label), f"{label}.{key}", suffix=suffix)


def _normalized_path(value: str, label: str, *, suffix: str | None = None) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise _invalid(f"{label} must be a normalized repository-relative POSIX path")
    if suffix is not None and path.suffix != suffix:
        raise _invalid(f"{label} must end in {suffix}")
    if value == COHORT_MANIFEST_PATH.as_posix():
        raise _invalid("the manifest cannot fingerprint itself")
    return value


def _digest(raw: Mapping[str, object], label: str) -> str:
    value = _text(raw, "sha256", label)
    if _SHA256.fullmatch(value) is None:
        raise _invalid(f"{label}.sha256 must be a lowercase SHA-256 digest")
    return value


def _unique(values: Iterable[str], label: str) -> None:
    materialized = tuple(values)
    if len(set(materialized)) != len(materialized):
        raise _invalid(f"{label} must be unique")


def _invalid(message: str) -> CohortManifestError:
    return _error(DirectRefusalCode.INVALID_CANDIDATE, message)


def _error(
    code: DirectRefusalCode,
    message: str,
    *,
    path: str = COHORT_MANIFEST_PATH.as_posix(),
) -> CohortManifestError:
    return CohortManifestError(
        code,
        message,
        observation=DependencyObservation(
            path=path,
            line=1,
            symbol="direct-cohort-manifest",
            detail=message,
        ),
    )
