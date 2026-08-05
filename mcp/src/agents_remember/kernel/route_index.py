"""Generated route-level onboarding indexes."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.kernel.route_index_census import route_index_source_snapshot

SCHEMA_VERSION = 1
INDEX_FILE_NAME = "overview.index.json"
ROUTE_OVERVIEW_NAME = "overview.md"
ENTITY_CATALOG_NAME = "entities.md"
HOT_PATH_SUMMARY_HEADING = "Hot Path Summary"
HOT_PATH_SUMMARY_LIMIT = 600
CANDIDATE_HINT_LIMIT = 24
ANCHOR_HINT_LIMIT = 48

TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
CODE_SPAN_PATTERN = re.compile(r"`([^`\n]{2,120})`")
FILE_TOKEN_PATTERN = re.compile(
    r"(?<![\w.-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_-]+\.[A-Za-z0-9_+-]{1,12}(?![\w.-])"
)

GENERIC_ANCHOR_WORDS = {
    "about",
    "across",
    "also",
    "area",
    "areas",
    "because",
    "before",
    "belongs",
    "candidate",
    "candidates",
    "change",
    "changing",
    "child",
    "code",
    "config",
    "context",
    "covered",
    "covers",
    "current",
    "data",
    "docs",
    "each",
    "entry",
    "file",
    "files",
    "flow",
    "flows",
    "from",
    "governs",
    "here",
    "index",
    "local",
    "main",
    "memory",
    "model",
    "needs",
    "overview",
    "path",
    "paths",
    "read",
    "repo",
    "route",
    "routes",
    "scope",
    "source",
    "summary",
    "this",
    "through",
    "update",
    "uses",
    "when",
}


@dataclass(frozen=True)
class RouteIndexBuildResult:
    routes: int
    written: int
    unchanged: int
    indexes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "routes": self.routes,
            "written": self.written,
            "unchanged": self.unchanged,
            "indexes": self.indexes,
        }


@dataclass(frozen=True)
class _RouteIndexSurvey:
    """Everything discovered about the onboarding tree before a single index is rendered.

    One survey feeds every route's document, which is why it is taken once and passed on:
    each field is a whole-tree read (the overview walk, the Git-tracked source snapshot),
    and doing them per route would turn a linear build into a quadratic one.
    """

    onboarding_root: Path
    repository: str
    route_overviews: dict[str, str]
    covered_by_route: dict[str, list[str]]
    child_routes: dict[str, list[str]]
    source_counts: dict[str, int]


def _survey_routes(
    code_root: Path, onboarding_root: Path, repository: str, storage: StorageSettings
) -> _RouteIndexSurvey:
    """Read the onboarding tree and the Git-tracked source once."""
    route_overviews = _discover_route_overviews(onboarding_root)
    source_snapshot = route_index_source_snapshot(
        code_root=code_root,
        storage=storage,
        scoped_repo_path=repository,
    )
    return _RouteIndexSurvey(
        onboarding_root=onboarding_root,
        repository=repository,
        route_overviews=route_overviews,
        covered_by_route=_discover_covered_files(
            onboarding_root,
            route_overviews.keys(),
            repository_files=frozenset(source_snapshot.repository_paths),
        ),
        child_routes=_discover_child_routes(route_overviews.keys()),
        source_counts=_count_source_files_by_route(
            route_overviews.keys(),
            source_files=source_snapshot.eligible_paths,
        ),
    )


def _route_index_document(survey: _RouteIndexSurvey, route: str) -> dict[str, Any]:
    """The index document for one route, exactly as it is written to disk."""
    overview_rel = survey.route_overviews[route]
    covered = survey.covered_by_route.get(route, [])
    children = survey.child_routes.get(route, [])
    overview_path = survey.onboarding_root / overview_rel
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "agents-remember-route-index",
        "repository": survey.repository,
        "route": route,
        "overview": overview_rel,
        "index": _route_index_path(route),
        "sourceScope": [_source_scope(route)],
        "childRoutes": [
            {
                "route": child,
                "overview": survey.route_overviews[child],
                "index": _route_index_path(child),
            }
            for child in children
        ],
        "coveredFiles": covered,
        "coverageCounts": {
            "sourceFilesInScope": survey.source_counts.get(route, 0),
            "fileSidecars": len(covered),
            "childRoutes": len(children),
        },
        "routingTerms": _routing_terms(overview_path, route, covered, children),
        "hotPath": _hot_path(overview_path, route, covered, children),
        "fallback": {
            "governingOverview": overview_rel,
            "sidecarAbsence": "inferFromCoveredFiles",
        },
    }


def build_route_indexes(
    *,
    code_root: Path,
    onboarding_root: Path,
    repository: str,
    storage: StorageSettings,
    dry_run: bool = False,
) -> RouteIndexBuildResult:
    """Build route indexes using explicit Git and onboarding-storage authority.

    ``sourceFilesInScope`` counts existing Git-tracked and nonignored candidate
    files whose configured storage is not disabled. ``code_root`` must be the
    repository root; the generator never falls back to a filesystem walk.
    """

    code_root = code_root.resolve()
    onboarding_root = onboarding_root.resolve()
    if not code_root.is_dir():
        raise FileNotFoundError(f"code repository root does not exist: {code_root}")
    if not onboarding_root.is_dir():
        raise FileNotFoundError(f"onboarding root does not exist: {onboarding_root}")

    survey = _survey_routes(code_root, onboarding_root, repository, storage)

    written = 0
    unchanged = 0
    indexes: list[str] = []

    for route in sorted(survey.route_overviews.keys(), key=_route_sort_key):
        index_rel = _route_index_path(route)
        rendered = (
            json.dumps(_route_index_document(survey, route), indent=2, sort_keys=False) + "\n"
        )
        index_path = onboarding_root / index_rel
        indexes.append(index_rel)
        if index_path.exists() and index_path.read_text(encoding="utf-8") == rendered:
            unchanged += 1
            continue
        if not dry_run:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(rendered, encoding="utf-8")
        written += 1

    return RouteIndexBuildResult(
        routes=len(survey.route_overviews),
        written=written,
        unchanged=unchanged,
        indexes=indexes,
    )


def sidecar_status(source_path: str, route_index: dict[str, Any]) -> str:
    """Return present, absent, or out-of-scope for a source path."""

    normalized = _normalize_posix(source_path)
    covered = set(route_index.get("coveredFiles", []))
    if normalized in covered:
        return "present"
    if _matches_any_scope(normalized, route_index.get("sourceScope", [])):
        return "absent"
    return "out-of-scope"


def _discover_route_overviews(onboarding_root: Path) -> dict[str, str]:
    route_overviews: dict[str, str] = {}
    for path in onboarding_root.rglob(ROUTE_OVERVIEW_NAME):
        if not path.is_file():
            continue
        rel = _relative_posix(path, onboarding_root)
        route = _route_from_overview_rel(rel)
        route_overviews[route] = rel
    return route_overviews


def _discover_covered_files(
    onboarding_root: Path,
    routes: Iterable[str],
    *,
    repository_files: frozenset[str],
) -> dict[str, list[str]]:
    route_set = set(routes)
    covered: dict[str, list[str]] = {route: [] for route in route_set}

    for sidecar in onboarding_root.rglob("*.md"):
        if not sidecar.is_file() or not _is_file_sidecar(sidecar, onboarding_root):
            continue
        source_rel = _source_path_from_sidecar(sidecar, onboarding_root)
        if source_rel not in repository_files:
            continue
        route = _nearest_route(source_rel, route_set)
        covered.setdefault(route, []).append(source_rel)

    for values in covered.values():
        values.sort()
    return covered


def _discover_child_routes(routes: Iterable[str]) -> dict[str, list[str]]:
    route_set = set(routes)
    children: dict[str, list[str]] = {route: [] for route in route_set}
    for route in route_set:
        if route == "":
            continue
        parent = _nearest_route_parent(route, route_set)
        children.setdefault(parent, []).append(route)
    for values in children.values():
        values.sort(key=_route_sort_key)
    return children


def _count_source_files_by_route(
    routes: Iterable[str],
    *,
    source_files: Iterable[str],
) -> dict[str, int]:
    counts = {route: 0 for route in routes}
    for route in counts:
        counts[route] = sum(1 for source_path in source_files if _route_covers(route, source_path))
    return counts


def _routing_terms(
    _overview_path: Path,
    route: str,
    covered_files: list[str],
    child_routes: list[str],
) -> list[str]:
    terms: set[str] = set()
    for path in [route, *child_routes, *covered_files]:
        terms.update(_extract_terms(path.replace("/", " ").replace(".", " ")))
    return sorted(terms)[:80]


def _hot_path(
    overview_path: Path,
    route: str,
    covered_files: list[str],
    child_routes: list[str],
) -> dict[str, Any]:
    overview_text = _read_text_optional(overview_path)
    summary = _extract_hot_path_summary(overview_text)
    return {
        "summary": summary,
        "candidateHints": _candidate_hints(route, covered_files, child_routes),
        "anchorHints": _anchor_hints(summary, overview_text, covered_files, child_routes),
    }


def _extract_hot_path_summary(overview_text: str) -> str:
    if not overview_text:
        return ""

    lines = overview_text.splitlines()
    in_section = False
    summary_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _is_level_two_heading(stripped, HOT_PATH_SUMMARY_HEADING):
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section:
            summary_lines.append(line)
    return _normalize_hot_path_summary("\n".join(summary_lines))


def _is_level_two_heading(line: str, heading: str) -> bool:
    return line.lower() == f"## {heading.lower()}"


def _normalize_hot_path_summary(text: str) -> str:
    parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        parts.append(re.sub(r"\s+", " ", stripped))

    summary = " ".join(parts).strip()
    if len(summary) <= HOT_PATH_SUMMARY_LIMIT:
        return summary
    return summary[: HOT_PATH_SUMMARY_LIMIT - 3].rstrip() + "..."


def _candidate_hints(route: str, covered_files: list[str], child_routes: list[str]) -> list[str]:
    hints: list[str] = []
    if route:
        hints.append(route)
    hints.extend(sorted(child_routes, key=_route_sort_key))
    for source_path in sorted(covered_files):
        parent = source_path.rsplit("/", 1)[0] if "/" in source_path else ""
        if parent:
            hints.append(parent)
        hints.append(source_path)
    return _dedupe_preserve_order(hints)[:CANDIDATE_HINT_LIMIT]


def _anchor_hints(
    summary: str,
    overview_text: str,
    covered_files: list[str],
    child_routes: list[str],
) -> list[str]:
    hints: list[str] = []
    for source_path in sorted(covered_files):
        path = Path(source_path)
        hints.append(path.name)
        hints.append(path.stem)

    for child_route in sorted(child_routes, key=_route_sort_key):
        if child_route:
            hints.append(child_route.rsplit("/", 1)[-1])

    for text in (summary, overview_text):
        hints.extend(_code_span_hints(text))
        hints.extend(_file_token_hints(text))
        hints.extend(_identifier_hints(text))

    return _dedupe_preserve_order(hints)[:ANCHOR_HINT_LIMIT]


def _code_span_hints(text: str) -> list[str]:
    hints: list[str] = []
    for match in CODE_SPAN_PATTERN.finditer(text):
        token = _clean_hint(match.group(1))
        if token and not token.startswith("<") and len(token) <= 80:
            hints.append(token)
    return hints


def _file_token_hints(text: str) -> list[str]:
    return [_clean_hint(match.group(0)) for match in FILE_TOKEN_PATTERN.finditer(text)]


def _identifier_hints(text: str) -> list[str]:
    hints: list[str] = []
    for match in TOKEN_PATTERN.finditer(text):
        token = _clean_hint(match.group(0))
        if _is_source_anchor(token):
            hints.append(token)
    return hints


def _is_source_anchor(token: str) -> bool:
    """A hint worth indexing: long enough, not a generic word, and shaped like code."""
    if len(token) < 3 or token.lower() in GENERIC_ANCHOR_WORDS:
        return False
    return _has_code_shape(token)


def _has_code_shape(token: str) -> bool:
    """True when the token's own spelling marks it as an identifier rather than prose.

    Any one signal is enough: a dotted or slashed path, snake_case, an embedded digit,
    an all-caps constant, or an interior capital (camelCase / PascalCase).
    """
    return (
        "." in token
        or "/" in token
        or "_" in token
        or any(char.isdigit() for char in token)
        or token.isupper()
        or any(char.isupper() for char in token[1:])
    )


def _clean_hint(value: str) -> str:
    return value.strip().strip(".,;:()[]{}\"'")


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = _clean_hint(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _read_text_optional(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _extract_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for match in TOKEN_PATTERN.finditer(text):
        token = match.group(0).lower()
        terms.add(token)
        for part in token.split("_"):
            if len(part) >= 3:
                terms.add(part)
    return terms


def _is_file_sidecar(path: Path, onboarding_root: Path) -> bool:
    rel = _relative_posix(path, onboarding_root)
    name = path.name
    if name in {ROUTE_OVERVIEW_NAME, ENTITY_CATALOG_NAME}:
        return False
    if name == INDEX_FILE_NAME:
        return False
    if rel.startswith("bootstrap/"):
        return False
    return rel.endswith(".md")


def _source_path_from_sidecar(path: Path, onboarding_root: Path) -> str:
    rel = _relative_posix(path, onboarding_root)
    return rel[:-3]


def _route_from_overview_rel(overview_rel: str) -> str:
    if overview_rel == ROUTE_OVERVIEW_NAME:
        return ""
    return overview_rel[: -len(f"/{ROUTE_OVERVIEW_NAME}")]


def _route_index_path(route: str) -> str:
    if route == "":
        return INDEX_FILE_NAME
    return f"{route}/{INDEX_FILE_NAME}"


def _source_scope(route: str) -> str:
    if route == "":
        return "**"
    return f"{route}/**"


def _nearest_route(source_path: str, routes: set[str]) -> str:
    candidates = [route for route in routes if _route_covers(route, source_path)]
    if not candidates:
        return ""
    return max(candidates, key=lambda route: (len(route), route))


def _nearest_route_parent(route: str, routes: set[str]) -> str:
    parent = route.rsplit("/", 1)[0] if "/" in route else ""
    while parent:
        if parent in routes:
            return parent
        parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
    return ""


def _route_covers(route: str, source_path: str) -> bool:
    if route == "":
        return True
    return source_path.startswith(f"{route}/")


def _matches_any_scope(source_path: str, scopes: Iterable[str]) -> bool:
    for scope in scopes:
        if scope == "**":
            return True
        if scope.endswith("/**") and source_path.startswith(scope[:-3] + "/"):
            return True
        if source_path == scope:
            return True
    return False


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _normalize_posix(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _route_sort_key(route: str) -> tuple[int, str]:
    return (0 if route == "" else route.count("/") + 1, route)
