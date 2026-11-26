"""Revision inventory builder and one-line dashboard summary for explainer pages.

Computes figure counts, normalized visual techniques, kit libraries used, code excerpt counts,
and unreachable draft assets without guessing over raw script text.
"""

from __future__ import annotations

import posixpath
from collections import deque
from collections.abc import Collection, Mapping, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from agents_remember.canvas.kit import (
    KIT_CONTRACT_PREFIX,
    KitRefused,
    kit_manifest,
)
from agents_remember.canvas.kit_references import (
    extract_references,
    resolve_reference,
)

__all__ = [
    "build_inventory",
    "inventory_summary",
    "kit_libraries_used",
    "normalize_technique",
    "unused_draft_assets",
]

_CLAIM_MAP_PATH = "claim-map.json"
_KIT_CODE_STYLE_SUFFIXES = (".js", ".mjs", ".css")
_INTERACTIVE_TAGS = frozenset({"button", "input", "select", "textarea", "details"})


def normalize_technique(raw: str) -> str:
    """Trim, lowercase, and collapse internal whitespace to single hyphens."""

    return "-".join(raw.strip().lower().split())


def _is_licence_or_notice_path(path: str) -> bool:
    parts = [segment for segment in path.split("/") if segment]
    if not parts:
        return False
    if any(segment.lower() == "licenses" for segment in parts[:-1]):
        return True
    name = parts[-1].upper()
    return (
        name == "LICENSE"
        or name.startswith("LICENSE.")
        or name.startswith("LICENCE")
        or name.startswith("NOTICE")
    )


def _normalize_asset_path(raw: str) -> str | None:
    cleaned = raw.strip().split("#", maxsplit=1)[0].split("?", maxsplit=1)[0]
    if not cleaned or "\\" in cleaned:
        return None
    normalized = posixpath.normpath(cleaned.lstrip("/"))
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        return None
    return normalized


def _kit_companion_contract_paths(
    root: Path | None = None,
    *,
    used_libraries: Collection[str] | None = None,
) -> frozenset[str]:
    """Contract paths of kit notices and non-JS/non-CSS companion files for used libraries."""

    try:
        entries = kit_manifest(root)
    except KitRefused:
        return frozenset()
    companions: set[str] = set()
    for entry in entries:
        if used_libraries is not None and entry.id not in used_libraries:
            continue
        for notice in entry.notices:
            companions.add(notice.contract_path)
        for item in entry.files:
            if not item.path.lower().endswith(_KIT_CODE_STYLE_SUFFIXES):
                companions.add(item.contract_path)
    return frozenset(companions)


def _reachable_draft_graph(
    draft_assets: Mapping[str, bytes],
    *,
    entrypoint: str = "index.html",
    observed_assets: Sequence[str] = (),
) -> tuple[set[str], set[str], set[str]]:
    """Return ``(visited_assets, referenced_targets, observed_normalized)`` from entry and loads."""

    visited: set[str] = set()
    queue: deque[str] = deque()
    observed_normalized: set[str] = set()
    if entrypoint in draft_assets:
        visited.add(entrypoint)
        queue.append(entrypoint)
    for raw in observed_assets:
        normalized = _normalize_asset_path(raw)
        if normalized is None:
            continue
        observed_normalized.add(normalized)
        if normalized in draft_assets and normalized not in visited:
            visited.add(normalized)
            queue.append(normalized)
    referenced_targets: set[str] = set()
    while queue:
        current = queue.popleft()
        for reference in extract_references(current, draft_assets[current]):
            target = resolve_reference(current, reference)
            if target is None:
                continue
            referenced_targets.add(target)
            if target in draft_assets and target not in visited:
                visited.add(target)
                queue.append(target)
    return visited, referenced_targets, observed_normalized


def _kit_libraries_from_targets(
    referenced_targets: Collection[str],
    observed_normalized: Collection[str],
    carried_paths: Sequence[str],
    *,
    allow_carried_fallback: bool = False,
    kit_root: Path | None = None,
) -> tuple[str, ...]:
    try:
        entries = kit_manifest(kit_root)
    except KitRefused:
        return ()
    known_prefixes = {f"{entry.id}/{entry.version}/": entry.id for entry in entries}
    candidate_paths: set[str] = {
        *(path for path in referenced_targets if path.startswith(KIT_CONTRACT_PREFIX)),
        *(path for path in observed_normalized if path.startswith(KIT_CONTRACT_PREFIX)),
    }
    if allow_carried_fallback:
        candidate_paths.update(carried_paths)
    found: set[str] = set()
    for path in candidate_paths:
        library_id = _kit_library_for_code_style_path(path, known_prefixes)
        if library_id is not None:
            found.add(library_id)
    return tuple(sorted(found))


def _unused_from_graph(
    draft_assets: Mapping[str, bytes],
    *,
    entrypoint: str,
    visited: Collection[str],
    used_libraries: Collection[str],
    kit_root: Path | None = None,
) -> list[str]:
    kit_companions = _kit_companion_contract_paths(kit_root, used_libraries=used_libraries)
    return [
        path
        for path in sorted(draft_assets)
        if path not in visited
        and path not in {entrypoint, _CLAIM_MAP_PATH}
        and not _is_licence_or_notice_path(path)
        and path not in kit_companions
    ]


def _analyze_draft_graph(
    draft_assets: Mapping[str, bytes],
    *,
    entrypoint: str = "index.html",
    observed_assets: Sequence[str] = (),
    carried_kit_paths: Sequence[str] = (),
    kit_root: Path | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Compute ``(used_kit_libraries, unused_draft_assets)`` in one draft-graph traversal."""

    visited, referenced_targets, observed_normalized = _reachable_draft_graph(
        draft_assets, entrypoint=entrypoint, observed_assets=observed_assets
    )
    used_libraries = _kit_libraries_from_targets(
        referenced_targets,
        observed_normalized,
        carried_kit_paths,
        allow_carried_fallback=entrypoint not in draft_assets and not observed_normalized,
        kit_root=kit_root,
    )
    unused = tuple(
        _unused_from_graph(
            draft_assets,
            entrypoint=entrypoint,
            visited=visited,
            used_libraries=used_libraries,
            kit_root=kit_root,
        )
    )
    return used_libraries, unused


def unused_draft_assets(
    draft_assets: Mapping[str, bytes],
    *,
    entrypoint: str = "index.html",
    observed_assets: Sequence[str] = (),
    carried_kit_paths: Sequence[str] = (),
    kit_root: Path | None = None,
) -> tuple[str, ...]:
    """Return sorted relative paths in ``draft_assets`` unreachable from entry or browser loads."""

    _, unused = _analyze_draft_graph(
        draft_assets,
        entrypoint=entrypoint,
        observed_assets=observed_assets,
        carried_kit_paths=carried_kit_paths,
        kit_root=kit_root,
    )
    return unused


def _kit_library_for_code_style_path(path: str, known_prefixes: Mapping[str, str]) -> str | None:
    normalized = path.removeprefix(KIT_CONTRACT_PREFIX)
    if not normalized.lower().endswith(_KIT_CODE_STYLE_SUFFIXES):
        return None
    for prefix, library_id in known_prefixes.items():
        if normalized.startswith(prefix):
            return library_id
    return None


def kit_libraries_used(
    assets: Mapping[str, bytes],
    carried_paths: Sequence[str] = (),
    *,
    entrypoint: str = "index.html",
    observed_assets: Sequence[str] = (),
    kit_root: Path | None = None,
) -> tuple[str, ...]:
    """Return sorted distinct shipped kit library IDs reachable or observed for the page."""

    used_libraries, _ = _analyze_draft_graph(
        assets,
        entrypoint=entrypoint,
        observed_assets=observed_assets,
        carried_kit_paths=carried_paths,
        kit_root=kit_root,
    )
    return used_libraries


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _normalize_techniques_map(raw: Mapping[str, Any] | None) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {}
    counts: dict[str, int] = {}
    for key, value in raw.items():
        normalized = normalize_technique(str(key))
        count = _non_negative_int(value)
        if normalized and count > 0:
            counts[normalized] = counts.get(normalized, 0) + count
    return dict(sorted(counts.items()))


class _StaticDomScanner(HTMLParser):
    """Approximate figure and code-excerpt counts when no browser DOM inventory is supplied."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.total_figures = 0
        self.svg_figures = 0
        self.canvas_figures = 0
        self.driven_figures = 0
        self.techniques: dict[str, int] = {}
        self.highlighted_code = 0
        self.plain_code = 0
        self._figure_depth = 0
        self._figure_flags: list[dict[str, bool]] = []
        self._svg_depth = 0
        self._pre_depth = 0
        self._pre_highlighted = False
        self._interactive_ancestor = 0

    def _record_technique(self, attrs: dict[str, str]) -> None:
        raw = attrs.get("data-technique", "")
        normalized = normalize_technique(raw)
        if normalized:
            self.techniques[normalized] = self.techniques.get(normalized, 0) + 1

    def _handle_figure_child(self, lower: str, attr_map: dict[str, str]) -> None:
        current = self._figure_flags[-1]
        if lower == "svg":
            current["svg"] = True
        elif lower == "canvas":
            current["canvas"] = True
        if lower in _INTERACTIVE_TAGS or "data-driven" in attr_map:
            current["driven"] = True

    def _handle_standalone_visual(self, lower: str, attr_map: dict[str, str]) -> None:
        if lower == "svg" and self._svg_depth == 0 and self._interactive_ancestor == 0:
            self.total_figures += 1
            self.svg_figures += 1
        elif lower == "canvas":
            self.total_figures += 1
            self.canvas_figures += 1
            if "data-driven" in attr_map:
                self.driven_figures += 1

    def _handle_code_start(self, lower: str, attr_map: dict[str, str]) -> None:
        if lower == "shiki-magic-move":
            self.highlighted_code += 1
        elif lower == "pre":
            self._pre_depth += 1
            if self._pre_depth == 1:
                classes = set(attr_map.get("class", "").lower().split())
                self._pre_highlighted = "shiki" in classes
        elif self._pre_depth > 0 and not self._pre_highlighted:
            style = attr_map.get("style", "").lower()
            classes = set(attr_map.get("class", "").lower().split())
            if "color:" in style or "shiki" in classes:
                self._pre_highlighted = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        attr_map = {name.lower(): "" if val is None else val for name, val in attrs}
        self._record_technique(attr_map)
        if lower == "figure":
            self._figure_depth += 1
            self._figure_flags.append(
                {
                    "svg": False,
                    "canvas": False,
                    "driven": "data-driven" in attr_map,
                }
            )
            return
        if lower in {"button", "a"}:
            self._interactive_ancestor += 1
        if self._figure_depth > 0:
            self._handle_figure_child(lower, attr_map)
        else:
            self._handle_standalone_visual(lower, attr_map)
        if lower == "svg":
            self._svg_depth += 1
        self._handle_code_start(lower, attr_map)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "figure" and self._figure_depth > 0:
            self._figure_depth -= 1
            flags = self._figure_flags.pop()
            self.total_figures += 1
            if flags["svg"]:
                self.svg_figures += 1
            if flags["canvas"]:
                self.canvas_figures += 1
            if flags["driven"]:
                self.driven_figures += 1
            return
        if lower in {"button", "a"} and self._interactive_ancestor > 0:
            self._interactive_ancestor -= 1
        if lower == "svg" and self._svg_depth > 0:
            self._svg_depth -= 1
        if lower == "pre" and self._pre_depth > 0:
            self._pre_depth -= 1
            if self._pre_depth == 0:
                if self._pre_highlighted:
                    self.highlighted_code += 1
                else:
                    self.plain_code += 1
                self._pre_highlighted = False


def _static_dom_inventory(
    draft_assets: Mapping[str, bytes], entrypoint: str
) -> tuple[dict[str, Any], dict[str, int]]:
    payload = draft_assets.get(entrypoint)
    if payload is None:
        return (
            {
                "total": 0,
                "byKind": {"svg": 0, "canvas": 0, "driven": 0},
                "byTechnique": {},
            },
            {"highlighted": 0, "plain": 0},
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return (
            {
                "total": 0,
                "byKind": {"svg": 0, "canvas": 0, "driven": 0},
                "byTechnique": {},
            },
            {"highlighted": 0, "plain": 0},
        )
    scanner = _StaticDomScanner()
    scanner.feed(text)
    scanner.close()
    return (
        {
            "total": scanner.total_figures,
            "byKind": {
                "svg": scanner.svg_figures,
                "canvas": scanner.canvas_figures,
                "driven": scanner.driven_figures,
            },
            "byTechnique": dict(sorted(scanner.techniques.items())),
        },
        {
            "highlighted": scanner.highlighted_code,
            "plain": scanner.plain_code,
        },
    )


def _figures_from_dom(dom_inventory: Mapping[str, Any]) -> dict[str, Any]:
    raw_figures = dom_inventory.get("figures")
    if not isinstance(raw_figures, Mapping):
        raw_figures = {}
    raw_kind = raw_figures.get("byKind")
    if not isinstance(raw_kind, Mapping):
        raw_kind = {}
    raw_techniques = raw_figures.get("byTechnique")
    return {
        "total": _non_negative_int(raw_figures.get("total")),
        "byKind": {
            "svg": _non_negative_int(raw_kind.get("svg")),
            "canvas": _non_negative_int(raw_kind.get("canvas")),
            "driven": _non_negative_int(raw_kind.get("driven")),
        },
        "byTechnique": _normalize_techniques_map(
            raw_techniques if isinstance(raw_techniques, Mapping) else None
        ),
    }


def _code_excerpts_from_dom(dom_inventory: Mapping[str, Any]) -> dict[str, int]:
    raw_code = dom_inventory.get("codeExcerpts")
    if not isinstance(raw_code, Mapping):
        raw_code = {}
    return {
        "highlighted": _non_negative_int(raw_code.get("highlighted")),
        "plain": _non_negative_int(raw_code.get("plain")),
    }


def build_inventory(
    draft_assets: Mapping[str, bytes],
    *,
    entrypoint: str = "index.html",
    dom_inventory: Mapping[str, Any] | None = None,
    carried_kit_paths: Sequence[str] = (),
    observed_assets: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the revision inventory payload from draft assets and optional browser DOM facts."""

    if dom_inventory is None:
        figures, code_excerpts = _static_dom_inventory(draft_assets, entrypoint)
    else:
        figures = _figures_from_dom(dom_inventory)
        code_excerpts = _code_excerpts_from_dom(dom_inventory)
    used_libraries, unused = _analyze_draft_graph(
        draft_assets,
        entrypoint=entrypoint,
        observed_assets=observed_assets,
        carried_kit_paths=carried_kit_paths,
    )
    payload: dict[str, Any] = {
        "figures": figures,
        "kitLibraries": list(used_libraries),
        "codeExcerpts": code_excerpts,
        "unusedAssets": list(unused),
    }
    if dom_inventory is not None and bool(dom_inventory.get("incomplete")):
        payload["incomplete"] = True
    return payload


def _figures_segment(figures: Mapping[str, Any]) -> str:
    total = _non_negative_int(figures.get("total"))
    label = f"{total} figure" if total == 1 else f"{total} figures"
    raw_kind = figures.get("byKind")
    by_kind: Mapping[str, Any] = raw_kind if isinstance(raw_kind, Mapping) else {}
    kind_parts = [
        f"{count} {suffix}"
        for key, suffix in (("svg", "SVG"), ("canvas", "canvas"), ("driven", "driven"))
        if (count := _non_negative_int(by_kind.get(key))) > 0
    ]
    raw_tech = figures.get("byTechnique")
    techniques = sorted(
        key
        for key, value in (raw_tech.items() if isinstance(raw_tech, Mapping) else ())
        if str(key).strip() and _non_negative_int(value) > 0
    )
    details: list[str] = []
    if kind_parts:
        details.append(", ".join(kind_parts))
    if techniques:
        details.append(", ".join(techniques))
    if details:
        return f"{label} ({'; '.join(details)})"
    return label


def inventory_summary(inventory: Mapping[str, Any] | None) -> str | None:
    """Format one concise dashboard summary line for an explainer revision inventory."""

    if not isinstance(inventory, Mapping) or not inventory:
        return None
    figures = inventory.get("figures")
    parts: list[str] = [_figures_segment(figures if isinstance(figures, Mapping) else {})]
    raw_libs = inventory.get("kitLibraries")
    libs = (
        [str(item) for item in raw_libs if str(item)]
        if isinstance(raw_libs, Sequence) and not isinstance(raw_libs, (str, bytes))
        else []
    )
    parts.append(f"kit: {', '.join(libs)}" if libs else "kit: none")

    raw_code = inventory.get("codeExcerpts")
    code = raw_code if isinstance(raw_code, Mapping) else {}
    highlighted = _non_negative_int(code.get("highlighted"))
    plain = _non_negative_int(code.get("plain"))
    code_parts: list[str] = []
    if highlighted > 0:
        code_parts.append(f"{highlighted} highlighted")
    if plain > 0:
        code_parts.append(f"{plain} plain")
    if code_parts:
        parts.append(f"code: {', '.join(code_parts)}")

    raw_unused = inventory.get("unusedAssets")
    unused_count = (
        len(raw_unused)
        if isinstance(raw_unused, Sequence) and not isinstance(raw_unused, (str, bytes))
        else 0
    )
    if unused_count == 1:
        parts.append("1 unused asset")
    elif unused_count > 1:
        parts.append(f"{unused_count} unused assets")

    if inventory.get("incomplete") is True:
        parts.append("incomplete")

    return " · ".join(parts)
