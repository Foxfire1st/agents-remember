"""MIK-R20 rules 3 to 5: the measures, the governing route status, and the report command.

The fixture census is the packet's conforming example: wave 1 reports 42 claims (31 T, 4 F, 5 U,
2 P) over six routes, every route ``migrated`` against one named tree.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agents_remember.cli.__main__ import main
from agents_remember.memory_quality.knowledge_census import (
    Counts,
    census_reports,
    claims_path,
    compute_measures,
    read_censuses,
    route_status_path,
)
from agents_remember.models.knowledge_files.census import CensusRoute, governing_status
from knowledge_census_test_support import (
    CENSUS,
    CENSUS_CODE_PATHS,
    MIGRATED_TREE,
    ROUTE_ARTIFACT,
    claim,
    claims_document,
    status_document,
    wave_one_files,
)
from knowledge_validator_test_support import edit_json, encode, write_tree


def _measure(counts: Counts, name: str) -> Any:
    return next(measure for measure in compute_measures(counts) if measure.name == name)


def test_the_measures_of_the_conforming_example_carry_their_counts() -> None:
    counts = Counts(supported=31, contradicted=4, unresolved=5, pending=2)
    assert counts.total == 42
    rendered = [measure.render() for measure in compute_measures(counts)]
    assert rendered == [
        "verified truth share: 73.8% (31 of 42)",
        "correctness among resolved claims: 88.6% (31 of 35; U 5, P 2)",
        "contradiction share: 9.5% (4 of 42)",
        "assessment completion: 95.2% (40 of 42)",
        "relevant truth coverage (C/K): not applicable (no reference inventory)",
        "realization coverage: not applicable (no reference inventory)",
    ]
    assert _measure(counts, "verified_truth_share").value == pytest.approx(31 / 42)
    assert _measure(counts, "correctness_among_resolved").value == pytest.approx(31 / 35)


def test_a_zero_denominator_is_not_applicable_never_a_perfect_score() -> None:
    empty = compute_measures(Counts())
    assert all(measure.state == "not_applicable" and measure.value is None for measure in empty)
    assert empty[0].render() == "verified truth share: not applicable (zero denominator: 0 of 0)"
    unresolved_only = Counts(unresolved=3, pending=1)
    correctness = _measure(unresolved_only, "correctness_among_resolved")
    assert (correctness.state, correctness.numerator, correctness.denominator) == (
        "not_applicable",
        0,
        0,
    )
    assert correctness.render() == (
        "correctness among resolved claims: not applicable (zero denominator: 0 of 0; U 3, P 1)"
    )
    assert _measure(unresolved_only, "assessment_completion").value == pytest.approx(3 / 4)


def test_the_cohort_is_the_assessable_claims_and_the_latest_assessment_governs() -> None:
    artifact = ROUTE_ARTIFACT["skills"]
    claims = [
        claim(1, artifact, "unresolved", "no_concern_found"),
        claim(2, artifact, "no_concern_found", "concern_found", disposition="discarded_false"),
        claim(3, artifact, applicability="non_claim"),
        claim(4, artifact, "no_concern_found", applicability="historical_non_applicable"),
        claim(5, artifact),
    ]
    files = {
        **wave_one_files(),
        claims_path(CENSUS, "skills"): encode(claims_document("skills", claims)),
    }
    (report,) = census_reports(read_censuses(files))
    skills = next(item for item in report.by_route if item.key == "skills")
    assert skills.counts == Counts(
        supported=1, contradicted=1, pending=1, non_claim=1, historical_non_applicable=1
    )
    assert report.counts.total == report.counts.supported + report.counts.contradicted + (
        report.counts.unresolved + report.counts.pending
    )


def test_the_report_on_the_fixture_census_matches_the_conforming_example() -> None:
    (report,) = census_reports(read_censuses(wave_one_files()))
    document = report.to_document()
    assert document["counts"] == {
        "N": 42,
        "T": 31,
        "F": 4,
        "U": 5,
        "P": 2,
        "nonClaim": 0,
        "historicalNonApplicable": 0,
    }
    assert document["routeStatuses"]["migrated"] == 6 == document["inventory"]["routes"]
    assert {line["governingEntry"]["tree"] for line in document["routes"]} == {MIGRATED_TREE}
    assert all(len(line["history"]) == 2 for line in document["routes"])
    assert [item["key"] for item in document["byRoute"]] == sorted(ROUTE_ARTIFACT)
    assert {item["key"]: item["counts"]["N"] for item in document["byKind"]} == {
        "accepted_invariant": 21,
        "current_behavior": 21,
    }
    assert document["dispositions"]["discarded_false"] == 4
    text = report.render()
    assert "claims: N 42 (T 31, F 4, U 5, P 2)" in text
    assert "verified truth share: 73.8% (31 of 42)" in text
    assert "correctness among resolved claims: 88.6% (31 of 35; U 5, P 2)" in text
    assert "routes: 6 of 6 migrated" in text
    assert f"skills: migrated against {MIGRATED_TREE}" in text


def test_the_governing_status_is_the_latest_entry_across_every_census() -> None:
    def history(census: str, *entries: tuple[str, str]) -> CensusRoute:
        return CensusRoute.model_validate({**status_document("skills", *entries), "census": census})

    assert governing_status("skills", ()).status == "pending"
    first = history("wave-1", ("migrated", "2026-09-29T09:00:00+02:00"))
    later = history("wave-0", ("blocked", "2026-09-29T08:30:00+00:00"))
    assert governing_status("skills", (first, later)).census == "wave-0"
    tie = history("wave-2", ("in_progress", "2026-09-29T07:00:00+00:00"))
    assert governing_status("skills", (first, tie)).status == "in_progress"
    assert governing_status("scripts", (first,)).status == "pending"

    files = wave_one_files()
    other = "wave-3"
    files = {
        **files,
        **{
            path.replace(f"/{CENSUS}/", f"/{other}/"): data.replace(
                f'"census": "{CENSUS}"'.encode(), f'"census": "{other}"'.encode()
            )
            for path, data in files.items()
            if path.startswith(f"knowledge/census/{CENSUS}/")
            and "/claims/" not in path
            and "/routes/" not in path
        },
    }
    files[route_status_path(other, "skills")] = encode(
        {
            **status_document("skills", ("blocked", "2026-09-30T08:00:00+02:00")),
            "census": other,
        }
    )
    first_report, second_report = census_reports(read_censuses(files))
    skills = next(line for line in first_report.routes if line.route == "skills")
    assert (skills.governing.status, skills.governing.census, len(skills.history)) == (
        "blocked",
        other,
        2,
    )
    # The governing status spans censuses: wave-3's report sees wave-1's other five migrations.
    assert second_report.counts.total == 0 and "routes: 5 of 6 migrated" in second_report.render()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_the_report_and_inventory_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    memory = tmp_path / "memory"
    write_tree(memory, wave_one_files())
    assert main(["knowledge-census", "report", str(memory)]) == 0
    assert "verified truth share: 73.8% (31 of 42)" in capsys.readouterr().out
    assert main(["knowledge-census", "report", str(memory), "--census", CENSUS, "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["censuses"][0]["counts"]["N"] == 42 and document["problems"] == []
    assert main(["knowledge-census", "report", str(memory), "--census", "nope"]) == 2
    capsys.readouterr()
    path = memory / claims_path(CENSUS, "skills")
    path.write_bytes(edit_json({"p": path.read_bytes()}, "p", lambda d: d.pop("route"))["p"])
    assert main(["knowledge-census", "report", str(memory)]) == 1
    assert "invalid census file" in capsys.readouterr().out

    code = tmp_path / "code"
    write_tree(code, {path: b"x\n" for path in CENSUS_CODE_PATHS})
    for root in (code, memory):
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "base")
    arguments = ["knowledge-census", "inventory", str(memory), "--census", "wave-2"]
    assert main([*arguments, "--code", str(code), "--code-commit", "missing"]) == 2
    assert "unreadable baseline" in capsys.readouterr().out
    assert not (memory / "knowledge/census/wave-2").exists()
    assert main([*arguments, "--code", str(code), "--scope", "dashboard"]) == 0
    assert "2 source files" in capsys.readouterr().out
    assert (memory / "knowledge/census/wave-2/inventory.json").is_file()
