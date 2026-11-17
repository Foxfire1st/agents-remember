"""Current audit-map composition and structural traceability assertions."""

import json
import re
from hashlib import sha256
from pathlib import Path

from agents_remember_test_support.testing.instruction_renewal import RENEW_COMMAND, renewal_module

RENEW = f"Run {RENEW_COMMAND}"
MAP_DECISION = (
    "In the hand-written part of core/digest-map.json, map the line to a unit or "
    "record its disposition; resolve overlapping place ownership there."
)


def load_digest_map(path: Path) -> dict:
    """Read authored ownership with the generated current binding facts."""
    return renewal_module("digest").joined_map(json.loads(path.read_text(encoding="utf-8")))


_D700_RETIRED_CORE_UNITS = frozenset({"AU2.5", "LO0.4", "AC1.4", "AC1.5"})
_D700_RETIREMENT_DECISION = {
    "path": "/home/firefox/projects/ar-coordination/notes/reports/paseo-native-executions/architect/messages/20261107T145802Z_architect-97e0320d_to_manager-96726ec5.md",
    "messageId": "bc7dacb7-24b8-40c9-9922-a5ddb930ef50",
    "sections": ["Q1", "Q2"],
    "sha256": "ebd655e1327fb427386324d7ad383ffe63398d8daeab1a4e138e19d74c298e68",
}


def _assert_core_retirements(document: dict) -> None:
    retired = {
        row["id"] for row in document["units"] if row["disposition"] == "retired (D699, D700)"
    }
    assert retired == _D700_RETIRED_CORE_UNITS, "unknown or missing retired unit"
    for row in document["units"]:
        if row["id"] in retired:
            assert row.get("retirementDecision") == _D700_RETIREMENT_DECISION, (
                row["id"],
                "missing or wrong retirement decision",
            )
            assert "digestPlace" not in row and "evidence" not in row, (
                row["id"],
                "retired unit has live bindings",
            )
            assert row["retiredBinding"]["file"] and row["retiredBinding"]["lines"]
            assert row["retiredBinding"]["sha256"], "missing retired binding history"


def _assert_core_source_map(root: Path, document: dict) -> None:
    """Every current non-heading source line belongs to exactly one frozen/new unit."""
    _assert_core_retirements(document)
    files: dict[str, list] = {}
    for row in document["units"]:
        if row["disposition"] == "retired (D699, D700)":
            continue
        source = (root / row["file"]).read_text().splitlines()
        start, end = row["lines"]
        assert 1 <= start <= end <= len(source), (row["id"], RENEW)
        assert sha256("\n".join(source[start - 1 : end]).encode()).hexdigest() == row["sha256"], (
            row["id"],
            RENEW,
        )
        files.setdefault(row["file"], []).append(row)
    for path, rows in files.items():
        extents = [row["lines"] for row in rows]
        assert extents == sorted(extents), (path, RENEW)
        covered = [number for start, end in extents for number in range(start, end + 1)]
        assert len(covered) == len(set(covered)), (path, RENEW)
        lines = (root / path).read_text().splitlines()
        for number, line in enumerate(lines, 1):
            following = lines[number] if number < len(lines) else ""
            table_header = line.startswith("|") and re.fullmatch(r"\|[\s:|\-]+\|", following)
            structural = (
                not line.strip()
                or line.startswith("#")
                or table_header
                or re.fullmatch(r"\|[\s:|\-]+\|", line)
            )
            assert structural or number in covered, (
                path,
                number,
                "unmapped source line",
                MAP_DECISION,
            )


def _digest_range(lines: list[str], extent: list[int]) -> set[int]:
    assert isinstance(extent, list) and len(extent) == 2, ("invalid digest range", RENEW)
    start, end = extent
    assert type(start) is int and type(end) is int, ("invalid digest range", RENEW)
    assert 1 <= start <= end <= len(lines), ("invalid digest range", RENEW)
    assert lines[start - 1].strip() and lines[end - 1].strip(), ("empty digest range", RENEW)
    return set(range(start, end + 1))


def _assert_digest_structure(text: str) -> None:
    lines = text.splitlines()
    assert lines and lines[0].startswith("# "), "missing digest title"
    headings = [i for i, line in enumerate(lines) if line.startswith("#")]
    assert any(lines[i].startswith("## ") for i in headings), "missing digest sections"
    for start, end in zip(headings, [*headings[1:], len(lines)], strict=True):
        assert lines[start].lstrip("#").strip(), "empty heading"
        assert any(line.strip() for line in lines[start + 1 : end]), "empty section"
    assert not re.search(r"\b(?:AU|IN|LF|LO|AC)\d+\.\d+\b", text), "unit code in prose"


def _assert_digest_places(document: dict, text: str) -> None:
    _assert_digest_structure(text)
    lines = text.splitlines()
    rows = {row["id"]: row for row in document["units"] if row["disposition"] == "digest"}
    places = document["digest"]["places"]
    by_id = {place["id"]: place for place in places}
    assert len(by_id) == len(places), "duplicate place"
    intro = document["digest"]["introduction"]
    covered = list(_digest_range(lines, intro["lines"]))
    first_section = next(i for i, line in enumerate(lines, 1) if line.startswith("## "))
    assert set(covered) == {i for i in range(2, first_section) if lines[i - 1].strip()}, RENEW
    assert (
        sha256("\n".join(lines[i - 1] for i in sorted(covered)).encode()).hexdigest()
        == intro["sha256"]
    ), RENEW
    assert [p["lines"] for p in places] == sorted(p["lines"] for p in places), RENEW
    homes: dict[str, list[str]] = {}
    for place in places:
        extent = _digest_range(lines, place["lines"])
        assert not any(lines[i - 1].startswith("#") for i in extent), (
            "heading inside place",
            RENEW,
        )
        heading = next(
            line.lstrip("#").strip()
            for line in reversed(lines[: min(extent)])
            if line.startswith("#")
        )
        assert heading == place["heading"], ("wrong place heading", RENEW)
        body = "\n".join(lines[i - 1] for i in sorted(extent))
        assert sha256(body.encode()).hexdigest() == place["sha256"], ("stale place hash", RENEW)
        units = place["units"]
        assert units and len(units) == len(set(units)), "invalid place units"
        assert set(units) <= rows.keys(), "unknown place unit"
        for identity in units:
            homes.setdefault(identity, []).append(place["id"])
        clauses = place["clauses"]
        assert clauses, "missing clauses"
        clause_lines, clause_units = [], set()
        assert [c["lines"] for c in clauses] == sorted(c["lines"] for c in clauses), RENEW
        for clause in clauses:
            selected = _digest_range(lines, clause["lines"])
            assert selected <= extent, ("clause outside place", RENEW)
            assert clause["units"] and len(clause["units"]) == len(set(clause["units"]))
            assert set(clause["units"]) <= set(units), "clause unit outside place"
            clause_lines.extend(selected)
            clause_units.update(clause["units"])
        assert clause_units == set(units), "missing clause unit"
        assert len(clause_lines) == len(set(clause_lines)), ("overlapping clauses", RENEW)
        error = renewal_module("digest").clause_coverage_error(
            place["id"], document["digest"]["path"], lines, place
        )
        assert error is None, error
        covered.extend(extent)
    assert len(covered) == len(set(covered)), ("overlapping places", MAP_DECISION)
    assert set(covered) >= {
        i for i, line in enumerate(lines, 1) if line.strip() and not line.startswith("#")
    }, ("unmapped digest prose", MAP_DECISION)
    assert set(homes) == set(rows), "missing unit home"
    for identity, row in rows.items():
        assert "digestLines" not in row and "digestAudiences" not in row
        assert homes[identity] == [row["digestPlace"]], "unit home mismatch"


def _assert_core_dispositions(root: Path, document: dict, text: str) -> None:
    _assert_core_retirements(document)
    rows = {row["id"]: row for row in document["units"]}
    assert len(rows) == len(document["units"]), "duplicate ID"
    reasons = {
        "rationale or history",
        "stated already",
        "enforced by the product",
        "stage procedure reached through its operation file",
        "no action",
        "binds no started role",
        "lead-in",
    }
    for row in rows.values():
        if row["disposition"] == "retired (D699, D700)":
            continue
        if row["disposition"] == "digest":
            assert row["digestPlace"], row["id"]
        else:
            assert row["disposition"] == "reference" and row["reason"] in reasons, row["id"]
            assert row["evidence"], row["id"]
            for evidence in row["evidence"]:
                if "sha256" in evidence:
                    source = (root.parents[1] / evidence["path"]).read_text().splitlines()
                    start, end = evidence["lines"]
                    assert (
                        sha256("\n".join(source[start - 1 : end]).encode()).hexdigest()
                        == evidence["sha256"]
                    ), (row["id"], RENEW)
    _assert_digest_places(document, text)


def _assert_digest_audiences(document: dict, text: str) -> None:
    roles = set(document["roles"])
    admitted_roles = roles
    assert admitted_roles == {
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "reviewer",
        "investigator",
        "bootstrap",
    }
    rows = {row["id"]: row for row in document["units"] if row["disposition"] == "digest"}
    lines = text.splitlines()
    audiences: dict[str, set[str]] = {identity: set() for identity in rows}
    for place in document["digest"]["places"]:
        for clause in place["clauses"]:
            start, end = clause["lines"]
            body = " ".join(lines[start - 1 : end])
            prefix = set(body.split(":", 1)[0].lower().split(", "))
            expected = prefix if prefix <= roles else admitted_roles
            actual = set(clause["audience"])
            assert len(actual) == len(clause["audience"]) and actual <= roles
            assert actual == expected, ("clause audience union", RENEW)
            for identity in clause["units"]:
                audiences[identity].update(actual)
    for identity, row in rows.items():
        assert audiences[identity] == set(row["audience"]), (identity, "unit audience union", RENEW)
