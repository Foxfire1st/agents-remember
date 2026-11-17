"""Resolve the current passage bindings of the shared-rule audit map."""

from __future__ import annotations

import copy
import json
import re
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from agents_remember.errors import AgentsRememberError

LIFECYCLE = Path("skills/l-01-agent-lifecycles")
MAP = LIFECYCLE / "core/digest-map.json"
COMMAND = "mcp/.venv/bin/python scripts/renew-instructions.py"


@dataclass(frozen=True)
class Binding:
    unit: str
    path: str
    lines: tuple[int, int]
    selector: str | None = None

    def targets(self, passage: Passage) -> bool:
        return (
            self.unit in passage.units
            and self.path == passage.path
            and self.selector == passage.selector
        )


@dataclass(frozen=True)
class Passage:
    identity: str
    units: tuple[str, ...]
    path: str
    lines: tuple[int, int]
    digest: str
    storage: str
    selector: str | None = None
    scope: tuple[int, int] | None = None


def _hash(lines: list[str]) -> str:
    return sha256("\n".join(lines).encode()).hexdigest()


def _parse_binding(raw: str) -> Binding:
    match = re.fullmatch(r"([^=]+)=(.+):(\d+)-(\d+)", raw)
    if match is None:
        raise ValueError(f"Invalid unit binding {raw!r}; use --bind-unit UNIT=FILE:START-END")
    unit, path, start, end = match.groups()
    unit, marker, selector = unit.partition("@")
    if marker and re.fullmatch(r"(?:evidence:\d+|clause:[^:]+:\d+)", selector) is None:
        raise ValueError(f"Invalid passage selector {selector!r} in unit binding {raw!r}")
    path = Path(path).as_posix()
    if not path.startswith("skills/"):
        path = (LIFECYCLE / path).as_posix()
    extent = (int(start), int(end))
    if extent[0] < 1 or extent[1] < extent[0]:
        raise ValueError(f"Invalid unit binding range {raw!r}")
    return Binding(unit, path, extent, selector if marker else None)


class Resolver:
    """Share file reads and hash-window indexes across the map's passage population."""

    def __init__(self, root: Path, bindings: tuple[str, ...], roles: list[str]):
        self.root = root
        self.bindings = tuple(_parse_binding(value) for value in bindings)
        self.files: dict[str, list[str]] = {}
        self.windows: dict[tuple[str, int], dict[str, list[tuple[int, int]]]] = {}
        self.notices: list[str] = []
        self.roles = sorted(roles)

    def lines(self, path: str) -> list[str]:
        if path not in self.files:
            self.files[path] = (self.root / path).read_text(encoding="utf-8").splitlines()
        return self.files[path]

    def _matches(self, passage: Passage) -> list[tuple[int, int]]:
        if not passage.digest:
            return []
        width = passage.lines[1] - passage.lines[0] + 1
        key = (passage.path, width)
        if key not in self.windows:
            try:
                lines = self.lines(passage.path)
            except FileNotFoundError:
                return []
            index: dict[str, list[tuple[int, int]]] = {}
            for start in range(len(lines) - width + 1):
                digest = _hash(lines[start : start + width])
                index.setdefault(digest, []).append((start + 1, start + width))
            self.windows[key] = index
        matches = self.windows[key].get(passage.digest, [])
        if passage.scope is None:
            return matches
        start, end = passage.scope
        return [extent for extent in matches if start <= extent[0] <= extent[1] <= end]

    def _extent(self, passage: Passage) -> tuple[int, int]:
        matches = self._matches(passage)
        explicit = {binding.lines for binding in self.bindings if binding.targets(passage)}
        if len(explicit) == 1:
            return explicit.pop()
        if not explicit and len(matches) == 1:
            return matches[0]
        reason = "multiple explicit ranges" if explicit else f"{len(matches)} hash matches"
        raise ValueError(self._unresolved(passage, reason))

    def resolve(self, passage: Passage) -> dict:
        """Find identical text or use the explicitly supplied semantic unit binding."""
        extent = self._extent(passage)
        try:
            lines = self.lines(passage.path)
        except FileNotFoundError:
            raise ValueError(self._unresolved(passage, "source file is absent")) from None
        if extent[1] > len(lines):
            raise ValueError(self._unresolved(passage, "supplied range is outside the file"))
        if passage.scope and not (passage.scope[0] <= extent[0] <= extent[1] <= passage.scope[1]):
            raise ValueError(
                self._unresolved(passage, "supplied clause range is outside its place")
            )
        digest = _hash(lines[extent[0] - 1 : extent[1]])
        if digest != passage.digest:
            self.notices.append(
                f"Bound units {', '.join(passage.units)} on the Worker's word: "
                f"{passage.identity} -> {passage.path}:{extent[0]}-{extent[1]}"
            )
        return {"lines": list(extent), "sha256": digest}

    def _unresolved(self, passage: Passage, reason: str) -> str:
        try:
            current = self.lines(passage.path)
        except FileNotFoundError:
            current = ["The source file is absent."]
        start, end = passage.lines
        text = "\n".join(current[start - 1 : end])
        held = (
            _head_text(self.root, passage) if passage.digest else "No held passage binding exists."
        )
        selector = f"@{passage.selector}" if passage.selector else ""
        return (
            f"{MAP}: {passage.storage}.{{lines,sha256}}\n"
            f"Unresolved unit {', '.join(passage.units)} ({passage.identity}): {reason}.\n"
            f"Held sha256: {passage.digest}\n{held}\n"
            f"Current text at {passage.path}:{start}-{end}:\n{text}\n"
            f"Name its passage with {COMMAND} --bind-unit "
            f"{passage.units[0]}{selector}"
            f"={passage.path}:START-END"
        )


def _head_text(root: Path, passage: Passage) -> str:
    """Git supplies explanatory text only after it proves the recorded passage hash."""
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{passage.path}"],
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        lines = result.stdout.decode("utf-8").splitlines()
        start, end = passage.lines
        text = lines[start - 1 : end]
        if _hash(text) == passage.digest:
            return "Held text proven from HEAD:\n" + "\n".join(text)
    return "HEAD cannot supply text proven by the held hash."


def _passage(
    identity: str, units: tuple[str, ...], path: str, prior: dict, storage: str
) -> Passage:
    return Passage(
        identity, units, path, tuple(prior.get("lines", (1, 1))), prior.get("sha256", ""), storage
    )


def _units(document: dict, previous: dict, resolver: Resolver) -> dict:
    result = {}
    for unit in document["units"]:
        if "retiredBinding" in unit:
            continue
        identity = unit["id"]
        prior = previous.get("units", {}).get(identity, {})
        path = (LIFECYCLE / unit["file"]).as_posix()
        passage = _passage(identity, (identity,), path, prior, f"derived.units.{identity}")
        result[identity] = resolver.resolve(passage)
    return result


def _evidence(document: dict, previous: dict, resolver: Resolver) -> dict:
    result = {}
    for unit in document["units"]:
        identity = unit["id"]
        for number, source in enumerate(unit.get("evidence", [])):
            if "path" not in source or "symbol" in source:
                continue
            prior = previous.get("evidence", {}).get(identity, {}).get(str(number), {})
            passage = Passage(
                f"{identity}/evidence/{number}",
                (identity,),
                source["path"],
                tuple(prior.get("lines", (1, 1))),
                prior.get("sha256", ""),
                f"derived.evidence.{identity}.{number}",
                f"evidence:{number}",
            )
            result.setdefault(identity, {})[str(number)] = resolver.resolve(passage)
    return result


def _clause_prior(previous: dict, current: dict, number: int, lines: list[str]) -> dict:
    clauses = previous.get("clauses", [])
    prior = clauses[number] if number < len(clauses) else {}
    if "sha256" in prior or previous.get("sha256") != current["sha256"]:
        return prior
    # Identical whole-place text proves every subsection before it gains its own hash.
    shift = current["lines"][0] - previous["lines"][0]
    start, end = (value + shift for value in prior.get("lines", (1, 0)))
    return {"lines": [start, end], "sha256": _hash(lines[start - 1 : end])} if prior else {}


def _clauses(
    place: dict, previous: dict, current: dict, resolver: Resolver, path: str
) -> list[dict]:
    """Resolve each clause by its own exact text or its own explicit binding."""
    lines = resolver.lines(path)
    result = []
    for number, clause in enumerate(place["clauses"]):
        prior = _clause_prior(previous, current, number, lines)
        passage = Passage(
            f"{place['id']}/clause/{number}",
            tuple(clause["units"]),
            path,
            tuple(prior.get("lines", (1, 1))),
            prior.get("sha256", ""),
            f"derived.places.{place['id']}.clauses.{number}",
            f"clause:{place['id']}:{number}",
            tuple(current["lines"]),
        )
        bound = resolver.resolve(passage)
        start, end = bound["lines"]
        body = " ".join(lines[start - 1 : end])
        result.append({**bound, "audience": _audience(body, resolver.roles)})
    return result


def _audience(body: str, roles: list[str]) -> list[str]:
    prefix = set(body.split(":", 1)[0].lower().split(", "))
    return sorted(prefix) if prefix and prefix <= set(roles) else roles


def _places(document: dict, previous: dict, resolver: Resolver) -> dict:
    result = {}
    path = (LIFECYCLE / document["digest"]["path"]).as_posix()
    lines = resolver.lines(path)
    for place in document["digest"]["places"]:
        identity = place["id"]
        prior = previous.get("places", {}).get(identity, {})
        passage = _passage(
            identity, tuple(place["units"]), path, prior, f"derived.places.{identity}"
        )
        bound = resolver.resolve(passage)
        start = bound["lines"][0]
        heading = next(
            (line.lstrip("#").strip() for line in reversed(lines[:start]) if line.startswith("#")),
            None,
        )
        if heading is None:
            raise ValueError(
                f"Place {identity} has no heading before {path}:{start}; add its authored heading "
                f"or use {COMMAND} --bind-unit {place['units'][0]}={path}:START-END "
                "to name its passage below the correct heading."
            )
        result[identity] = {
            "heading": heading,
            **bound,
            "clauses": _clauses(place, prior, bound, resolver, path),
        }
        error = clause_coverage_error(identity, path, lines, result[identity])
        if error:
            raise AgentsRememberError(error)
    return result


def clause_coverage_error(identity: str, path: str, lines: list[str], place: dict) -> str | None:
    """Name the authored clause decision when a bound place leaves prose uncovered."""
    start, end = place["lines"]
    covered = {
        number
        for clause in place["clauses"]
        for number in range(clause["lines"][0], clause["lines"][1] + 1)
    }
    uncovered = [
        number
        for number in range(start, end + 1)
        if lines[number - 1].strip() and number not in covered
    ]
    if not uncovered:
        return None
    return (
        f"Place {identity} has unmapped clause prose at {path}:"
        f"{', '.join(map(str, uncovered))}. "
        f"Decide which authored clause carries these lines in {MAP}, "
        f"digest.places entry {identity}; then bind that clause with {COMMAND} "
        f"--bind-unit UNIT@clause:{identity}:NUMBER={path}:START-END."
    )


def _validate_authored_map(document: dict) -> None:
    """Check the authored entries required before current passage facts can be derived."""
    live = {unit["id"] for unit in document["units"] if "retiredBinding" not in unit}
    for unit in document["units"]:
        if unit["id"] in live and not unit.get("file"):
            raise AgentsRememberError(
                f"Hand-written map {MAP}: unit {unit['id']} has no source file; "
                "set its authored file field by hand to the source file carrying that unit."
            )
    path = (LIFECYCLE / document["digest"]["path"]).as_posix()
    for place in document["digest"]["places"]:
        identity = place["id"]
        if not place.get("clauses"):
            raise AgentsRememberError(
                f"Hand-written map {MAP}: place {identity} at {path} has no clauses; "
                "add its authored clauses and their unit memberships by hand."
            )
        for number, clause in enumerate(place["clauses"]):
            unknown = set(clause["units"]) - live
            if unknown:
                raise AgentsRememberError(
                    f"Hand-written map {MAP}: place {identity}, clause {number} at {path} "
                    f"names unknown units {', '.join(sorted(unknown))}; "
                    "correct its authored units by hand to name existing live units."
                )


def _unit_audiences(document: dict, places: dict, units: dict) -> None:
    audiences: dict[str, set[str]] = {identity: set() for identity in units}
    for place in document["digest"]["places"]:
        clauses = places[place["id"]]["clauses"]
        for authored, current in zip(place["clauses"], clauses, strict=True):
            for identity in authored["units"]:
                audiences[identity].update(current["audience"])
    for unit in document["units"]:
        if unit["disposition"] == "digest":
            units[unit["id"]]["audience"] = sorted(audiences[unit["id"]])


def plan(root: Path, bindings: tuple[str, ...]) -> tuple[dict, list[str]]:
    """Compute every live map fact without publishing files or changing decisions."""
    document = json.loads((root / MAP).read_text(encoding="utf-8"))
    _validate_authored_map(document)
    manifest = json.loads((root / LIFECYCLE / "composition-manifest.json").read_text())
    previous = document["derived"]
    resolver = Resolver(root, bindings, manifest["role_order"])
    units = _units(document, previous, resolver)
    evidence = _evidence(document, previous, resolver)
    places = _places(document, previous, resolver)
    _unit_audiences(document, places, units)
    path = (LIFECYCLE / document["digest"]["path"]).as_posix()
    text = (root / path).read_text(encoding="utf-8")
    intro = previous["introduction"]
    introduction = resolver.resolve(
        _passage("introduction", ("introduction",), path, intro, "derived.introduction")
    )
    derived = {
        "roles": manifest["role_order"],
        "units": units,
        "evidence": evidence,
        "digest": {"sha256": sha256(text.encode()).hexdigest(), "sizeWords": len(text.split())},
        "places": places,
        "introduction": introduction,
    }
    return derived, list(dict.fromkeys(resolver.notices))


def joined_map(document: dict) -> dict:
    """Compose authored map ownership and its one generated current-facts part."""
    result = copy.deepcopy(document)
    derived = result.pop("derived")
    result["roles"] = derived["roles"]
    for unit in result["units"]:
        identity = unit["id"]
        if "retiredBinding" in unit:
            continue
        unit.update(derived["units"][identity])
        for number, evidence in enumerate(unit.get("evidence", [])):
            evidence.update(derived["evidence"].get(identity, {}).get(str(number), {}))
    result["digest"].update(derived["digest"])
    result["digest"]["introduction"] = derived["introduction"]
    for place in result["digest"]["places"]:
        current = derived["places"][place["id"]]
        for clause, facts in zip(place["clauses"], current["clauses"], strict=True):
            clause.update(facts)
        place.update({key: value for key, value in current.items() if key != "clauses"})
    return result
