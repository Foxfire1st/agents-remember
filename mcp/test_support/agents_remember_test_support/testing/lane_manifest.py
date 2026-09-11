"""Closed, explicit membership for every durable pytest evidence item."""

from __future__ import annotations

import ast
import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agents_remember_test_support.testing.dependency_facts import (
    RepositoryDependencyFacts,
    is_test_module,
)
from agents_remember_test_support.testing.evidence_lifecycle import EvidenceCategory

LANE_MANIFEST_PATH = Path("mcp/tests/test-evidence-lanes.toml")
LANE_MANIFEST_SCHEMA = "ar-test-evidence-lanes/v1"
ACCEPTING_CATEGORIES = frozenset(set(EvidenceCategory) - {EvidenceCategory.DIAGNOSTIC})


class LaneManifestError(RuntimeError):
    """The lane population is absent, ambiguous, stale, or otherwise incomplete."""


@dataclass(frozen=True)
class LaneOverride:
    """An explicit class or test-node exception to a file's base lane."""

    selector: str
    category: EvidenceCategory


@dataclass
class _OverrideLoadState:
    root: Path
    files: dict[Path, EvidenceCategory]
    seen: set[str]
    findings: list[str]


@dataclass(frozen=True)
class LaneManifest:
    """One complete, fail-closed evidence-lane population."""

    files: dict[Path, EvidenceCategory]
    overrides: tuple[LaneOverride, ...]
    digest: str

    def category_for_node(self, node_id: str) -> EvidenceCategory:
        """Resolve a collected node without an optimistic category fallback."""

        normalized = _unparameterized_node_id(node_id)
        path_text = normalized.partition("::")[0]
        path = Path(path_text)
        base = self.files.get(path)
        if base is None:
            raise LaneManifestError(f"{node_id}: test file has no explicit evidence lane")
        matches = tuple(
            override
            for override in self.overrides
            if normalized == override.selector or normalized.startswith(f"{override.selector}::")
        )
        if not matches:
            return base
        longest = max(len(item.selector) for item in matches)
        most_specific = tuple(item for item in matches if len(item.selector) == longest)
        categories = {item.category for item in most_specific}
        if len(categories) != 1:
            raise LaneManifestError(
                f"{node_id}: equally specific lane overrides conflict: "
                f"{sorted(item.value for item in categories)}"
            )
        return next(iter(categories))

    def population_for(self, category: EvidenceCategory) -> tuple[str, ...]:
        """Return the exact manifest population that binds retry/cadence identity."""

        files = (f"file:{path.as_posix()}" for path, lane in self.files.items() if lane is category)
        overrides = (
            f"override:{item.selector}" for item in self.overrides if item.category is category
        )
        return tuple(sorted((*files, *overrides)))

    def compatibility_population(
        self,
        accepting: frozenset[EvidenceCategory],
    ) -> tuple[str, ...]:
        """Render the full included/excluded population for retry identity."""

        allowed = f"accept={','.join(sorted(item.value for item in accepting))}"
        files = (
            f"file:{path.as_posix()}={category.value}" for path, category in self.files.items()
        )
        overrides = (f"override:{item.selector}={item.category.value}" for item in self.overrides)
        return (allowed, *tuple(sorted((*files, *overrides))))


def load_lane_manifest(project_root: Path) -> LaneManifest:
    """Load and independently prove the complete test-file and override population."""

    root = project_root.resolve()
    path = root / LANE_MANIFEST_PATH
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise LaneManifestError(f"cannot read evidence lane manifest {path}: {error}") from error
    findings: list[str] = []
    if raw.get("schema_version") != LANE_MANIFEST_SCHEMA:
        findings.append(f"schema_version must be {LANE_MANIFEST_SCHEMA!r}")
    unknown_top_level = set(raw) - {"schema_version", "files", "override"}
    if unknown_top_level:
        findings.append(f"unknown top-level fields: {sorted(unknown_top_level)}")
    files = _load_files(root, raw.get("files"), findings)
    overrides = _load_overrides(root, raw.get("override", []), files, findings)
    facts = RepositoryDependencyFacts.build(root)
    if facts.parse_error is not None:
        findings.append(f"test population could not be derived: {facts.parse_error}")
    expected = set(facts.tests)
    declared = set(files)
    missing = sorted(path.as_posix() for path in expected - declared)
    stale = sorted(path.as_posix() for path in declared - expected)
    if missing:
        findings.append(f"test files without an explicit lane: {missing}")
    if stale:
        findings.append(f"lane rows that are not current test files: {stale}")
    if findings:
        rendered = "\n".join(f"  - {finding}" for finding in findings)
        raise LaneManifestError(f"test evidence lanes have {len(findings)} finding(s):\n{rendered}")
    digest = hashlib.sha256(
        "\n".join(
            [
                *(
                    f"{path.as_posix()}={category.value}"
                    for path, category in sorted(files.items(), key=lambda item: item[0].as_posix())
                ),
                *(f"{item.selector}={item.category.value}" for item in overrides),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return LaneManifest(files, overrides, digest)


def _load_files(
    root: Path,
    raw: object,
    findings: list[str],
) -> dict[Path, EvidenceCategory]:
    if not isinstance(raw, dict):
        findings.append("[files] must map every accepting category to an explicit path list")
        return {}
    files: dict[Path, EvidenceCategory] = {}
    seen_categories: set[EvidenceCategory] = set()
    for category_text, values in raw.items():
        category = _load_file_category(root, category_text, values, files, findings)
        if category is not None:
            seen_categories.add(category)
    missing_categories = ACCEPTING_CATEGORIES - seen_categories
    if missing_categories:
        findings.append(
            "[files] omits accepting categories: "
            f"{sorted(item.value for item in missing_categories)}"
        )
    return files


def _load_file_category(
    root: Path,
    category_text: object,
    values: object,
    files: dict[Path, EvidenceCategory],
    findings: list[str],
) -> EvidenceCategory | None:
    try:
        category = EvidenceCategory(category_text)
    except (TypeError, ValueError):
        findings.append(f"unknown file category: {category_text!r}")
        return None
    if category not in ACCEPTING_CATEGORIES:
        findings.append(f"{category.value}: diagnostic items cannot enter accepting lanes")
        return None
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        findings.append(f"{category.value}: lane membership must be a string list")
        return category
    for value in values:
        _load_file_lane(root, value, category, files, findings)
    return category


def _load_file_lane(
    root: Path,
    value: str,
    category: EvidenceCategory,
    files: dict[Path, EvidenceCategory],
    findings: list[str],
) -> None:
    relative = _confined_path(value, findings)
    if relative is None:
        return
    previous = files.get(relative)
    if previous is not None:
        findings.append(
            f"{relative.as_posix()}: conflicting file lanes "
            f"{previous.value!r} and {category.value!r}"
        )
        return
    source = root / relative
    if not source.is_file() or not is_test_module(relative):
        findings.append(f"{relative.as_posix()}: lane member is not a current Python test file")
    files[relative] = category


def _load_overrides(
    root: Path,
    raw: object,
    files: dict[Path, EvidenceCategory],
    findings: list[str],
) -> tuple[LaneOverride, ...]:
    if not isinstance(raw, list):
        findings.append("[[override]] must be a list of explicit selector/category tables")
        return ()
    overrides: list[LaneOverride] = []
    state = _OverrideLoadState(root, files, set(), findings)
    for index, item in enumerate(raw):
        override = _load_override(state, item, index)
        if override is not None:
            overrides.append(override)
    return tuple(sorted(overrides, key=lambda item: item.selector))


def _load_override(
    state: _OverrideLoadState,
    item: object,
    index: int,
) -> LaneOverride | None:
    if not isinstance(item, dict) or set(item) != {"selector", "category"}:
        state.findings.append(f"override[{index}] must contain exactly selector and category")
        return None
    selector = item.get("selector")
    category_text = item.get("category")
    if not isinstance(selector, str) or not selector.strip():
        state.findings.append(f"override[{index}].selector must be non-empty")
        return None
    try:
        category = EvidenceCategory(category_text)
    except (TypeError, ValueError):
        state.findings.append(f"override[{index}] has unknown category {category_text!r}")
        return None
    if category not in ACCEPTING_CATEGORIES:
        state.findings.append(f"{selector}: diagnostic items cannot enter accepting lanes")
        return None
    normalized = _unparameterized_node_id(selector.strip())
    relative = _override_path(state.root, normalized, state.files, state.findings)
    if relative is None:
        return None
    if normalized in state.seen:
        state.findings.append(f"{normalized}: duplicate lane override")
    state.seen.add(normalized)
    return LaneOverride(normalized, category)


def _override_path(
    root: Path,
    normalized: str,
    files: dict[Path, EvidenceCategory],
    findings: list[str],
) -> Path | None:
    path_text, separator, node_selector = normalized.partition("::")
    relative = _confined_path(path_text, findings)
    if relative is None:
        return None
    if relative not in files:
        findings.append(f"{normalized}: override file has no base lane")
    if not separator or not node_selector or not _selector_exists(root / relative, node_selector):
        findings.append(f"{normalized}: override is not a current class or test node")
    return relative


def _confined_path(value: str, findings: list[str]) -> Path | None:
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        findings.append(f"{value!r}: path must be non-empty, repository-relative, and confined")
        return None
    return Path(*pure.parts)


def _unparameterized_node_id(node_id: str) -> str:
    return node_id.partition("[")[0]


def _selector_exists(path: Path, selector: str) -> bool:
    parts = tuple(selector.split("::"))
    if not parts or any(not part or "[" in part or "]" in part for part in parts):
        return False
    try:
        body = ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body
    except (OSError, SyntaxError, UnicodeError):
        return False
    for index, part in enumerate(parts):
        matches = [
            node
            for node in body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == part
        ]
        if len(matches) != 1:
            return False
        match = matches[0]
        if index == len(parts) - 1:
            return isinstance(match, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        if not isinstance(match, ast.ClassDef):
            return False
        body = match.body
    return False
