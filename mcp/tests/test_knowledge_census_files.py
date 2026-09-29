"""MIK-R20: census file formats, the mechanical inventory, the writer, and the validator's rules.

The fixture census (``knowledge_census_test_support``) sits in the converted MIK-R22 fixture tree:
six onboarding routes, a pinned baseline and a mechanically built inventory.
"""

from __future__ import annotations

import gc
import subprocess
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from agents_remember.memory.knowledge_census import (
    BaselineSide,
    CensusBaselineError,
    CensusWriteError,
    CensusWriter,
    build_inventory,
    take_inventory,
)
from agents_remember.memory.knowledge_census import writer as writer_module
from agents_remember.memory_quality.knowledge_census import (
    CENSUS_RULES,
    claims_path,
    inventory_path,
    read_censuses,
    route_status_path,
)
from agents_remember.memory_quality.knowledge_validator import (
    ValidationReport,
    registered_rules,
    rules_census,
    validate_tree,
)
from agents_remember.memory_quality.knowledge_validator.trees import knowledge_tree_from_directory
from agents_remember.models.knowledge_files import canonical_text, parse_document_text
from agents_remember.models.knowledge_files.census import (
    Assessment,
    CensusClaim,
    CensusClaims,
    CensusRoute,
    StatusEntry,
    route_slug,
)
from knowledge_census_test_support import (
    CENSUS,
    CENSUS_CODE_PATHS,
    ROUTE_ARTIFACT,
    WORKTREES,
    assessment,
    census_tree_files,
    claim,
    claims_document,
    json_of,
    provenance,
    status_document,
    wave_one_files,
)
from knowledge_validator_test_support import code, edit_json, encode, tree, write_tree
from pydantic import ValidationError

SKILLS_CARD = ROUTE_ARTIFACT["skills"]


def _validate(
    files: dict[str, bytes], bases: tuple[dict[str, bytes], ...] = ()
) -> ValidationReport:
    return validate_tree(
        tree(files),
        bases=[tree(base, label=f"base {index}") for index, base in enumerate(bases)],
        code=code(CENSUS_CODE_PATHS),
    )


def _census_rules(report: ValidationReport) -> set[str]:
    return {violation.rule for violation in report.refusals}


def _with_claims(files: dict[str, bytes], route: str, *claims: dict[str, Any]) -> dict[str, bytes]:
    return {**files, claims_path(CENSUS, route): encode(claims_document(route, list(claims)))}


# --------------------------------------------------------------------------------------------------
# Formats (rule 1)
# --------------------------------------------------------------------------------------------------


def test_every_census_file_parses_by_schema_and_is_canonical() -> None:
    files = wave_one_files()
    census_files = {
        path: data for path, data in files.items() if path.startswith("knowledge/census/")
    }
    assert len(census_files) == 2 + 6 + 6
    for path, data in census_files.items():
        text = data.decode("utf-8")
        model = parse_document_text(text)
        assert canonical_text(model.to_document()) == text, path
    assert claims_path(CENSUS, ".") == "knowledge/census/wave-1/claims/@root.json"
    assert route_status_path(CENSUS, WORKTREES) == (
        "knowledge/census/wave-1/routes/mcp+src+agents_remember+worktrees.json"
    )
    with pytest.raises(ValueError, match="unknown knowledge schema"):
        parse_document_text('{"schema": "ar-census/v1"}')


def test_route_slugs_are_readable_and_injective() -> None:
    routes = [".", "root", "@root", "a/b", "a+b", "a%2Bb", "a b", ".github", "x/.hidden", "é/b"]
    slugs = [route_slug(route) for route in routes]
    assert len(set(slugs)) == len(routes)
    assert slugs[:4] == ["@root", "root", "%40root", "a+b"]
    assert route_slug(".github") == "%2Egithub"
    assert all("/" not in slug and not slug.startswith(".") for slug in slugs)


def test_claim_and_status_models_refuse_inconsistent_records() -> None:
    artifact = SKILLS_CARD
    refusals = [
        claim(1, artifact, disposition="admitted_as_invariant"),
        claim(2, artifact, disposition="admitted_as_family", records=["INV-7K3F9Q"]),
        claim(3, artifact, "unresolved", disposition="discarded_false"),
        claim(4, artifact, disposition="pending", records=["INV-7K3F9Q"]),
        {**claim(5, artifact), "id": "CLM-0143"},
        {**claim(6, artifact), "location": {"artifact": "mcp/src/x.py.md"}},
        {**claim(7, artifact), "assessments": [{**assessment("no_concern_found"), "evidence": []}]},
    ]
    for document in refusals:
        with pytest.raises(ValidationError):
            CensusClaim.model_validate(document)
    corrected = claim(8, artifact, "unresolved", "concern_found", disposition="discarded_false")
    assert CensusClaim.model_validate(corrected).cell == "F"
    demoted = claim(9, artifact, disposition="demoted", records=["INV-7K3F9Q"])
    assert CensusClaim.model_validate(demoted).records == ("INV-7K3F9Q",)
    with pytest.raises(ValidationError, match="UTC offset"):
        StatusEntry.model_validate(
            {
                "status": "migrated",
                "reason": "r",
                "tree": "e" * 40,
                "provenance": provenance("2026-09-29T08:00:00"),
            }
        )
    with pytest.raises(ValidationError, match="exactly one of leaf or wave"):
        Assessment.model_validate(
            {**assessment("unresolved"), "provenance": provenance(leaf="L1", wave="W1")}
        )
    with pytest.raises(ValidationError, match="time order"):
        CensusRoute.model_validate(
            status_document(
                "skills",
                ("migrated", "2026-09-29T09:00:00+02:00"),
                ("in_progress", "2026-09-29T08:00:00+02:00"),
            )
        )


# --------------------------------------------------------------------------------------------------
# The mechanical inventory (rule 1, failure behaviour)
# --------------------------------------------------------------------------------------------------


def test_inventory_rows_carry_their_governing_onboarding_route() -> None:
    memory = [
        "onboarding/overview.md",
        "onboarding/mcp/overview.md",
        "onboarding/mcp/src/deep/x.py.md",
        "onboarding/mcp/src/deep/x.py.json",
        "onboarding/mcp/overview.index.json",
        "onboarding/.ar-index/cache.md",
        "knowledge/layout.json",
    ]
    inventory = build_inventory(
        "c1",
        code_paths=["mcp/src/deep/x.py", "top.txt", "docs/a.md"],
        memory_paths=memory,
        scope=["mcp", "top.txt"],
    )
    assert [(row.path, row.route) for row in inventory.sources] == [
        ("mcp/src/deep/x.py", "mcp"),
        ("top.txt", "."),
    ]
    assert [(row.path, row.route) for row in inventory.artifacts] == [
        ("onboarding/mcp/overview.md", "mcp"),
        ("onboarding/mcp/src/deep/x.py.json", "mcp"),
        ("onboarding/mcp/src/deep/x.py.md", "mcp"),
        ("onboarding/overview.md", "."),
    ]
    without_root = build_inventory("c1", code_paths=["top.txt"], memory_paths=memory[1:2])
    assert without_root.sources[0].route is None
    assert "route" not in without_root.to_document()["sources"][0]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repository(root: Path, files: dict[str, bytes]) -> str:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    write_tree(root, files)
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "baseline")
    return _git(root, "rev-parse", "HEAD")


def test_take_inventory_pins_exact_commits_and_refuses_an_unreadable_baseline(
    tmp_path: Path,
) -> None:
    code_commit = _repository(tmp_path / "code", {path: b"x\n" for path in CENSUS_CODE_PATHS})
    memory_commit = _repository(tmp_path / "memory", census_tree_files())
    baseline, inventory = take_inventory(
        "wave-2",
        code=BaselineSide(tmp_path / "code"),
        memory=BaselineSide(tmp_path / "memory"),
    )
    assert (baseline.code.commit, baseline.memory.commit) == (code_commit, memory_commit)
    assert {row.path for row in inventory.sources} == CENSUS_CODE_PATHS
    assert inventory.routes == tuple(sorted(ROUTE_ARTIFACT))
    for revision in ("no-such-branch", "0" * 40):
        with pytest.raises(CensusBaselineError, match="unreadable baseline"):
            take_inventory(
                "wave-2",
                code=BaselineSide(tmp_path / "code", revision),
                memory=BaselineSide(tmp_path / "memory"),
            )


# --------------------------------------------------------------------------------------------------
# The writer (Scope: the writer section)
# --------------------------------------------------------------------------------------------------


def test_the_writer_appends_observations_and_its_tree_passes_the_validator(tmp_path: Path) -> None:
    files = census_tree_files()
    for path in [path for path in files if path.startswith("knowledge/census/")]:
        del files[path]
    write_tree(tmp_path, files)
    baseline, inventory = (
        parse_document_text(census_tree_files()[path].decode())
        for path in ("knowledge/census/wave-1/baseline.json", inventory_path(CENSUS))
    )
    writer = CensusWriter(tmp_path)
    writer.create(baseline, inventory)  # type: ignore[arg-type]
    first = CensusClaim.model_validate(claim(1, SKILLS_CARD))
    writer.add_claims(CENSUS, "skills", [first])
    writer.append_assessment(CENSUS, first.id, Assessment.model_validate(assessment("unresolved")))
    writer.append_assessment(
        CENSUS, first.id, Assessment.model_validate(assessment("no_concern_found"))
    )
    writer.set_disposition(CENSUS, first.id, "admitted_as_invariant", ["INV-7K3F9Q"])
    writer.set_disposition(CENSUS, first.id, "kept_as_prose")
    entry = status_document("skills", ("migrated", "2026-09-29T09:00:00+02:00"))["statuses"][0]
    writer.append_status(CENSUS, "skills", StatusEntry.model_validate(entry))

    written = knowledge_tree_from_directory(tmp_path)
    stored = CensusClaims.model_validate(
        json_of(dict(written.files), claims_path(CENSUS, "skills"))
    )
    assert [item.verdict for item in stored.claims[0].assessments] == [
        "unresolved",
        "no_concern_found",
    ]
    assert stored.claims[0].disposition == "kept_as_prose" and stored.claims[0].records is None
    report = validate_tree(written, bases=[tree(files)], code=code(CENSUS_CODE_PATHS))
    assert report.ok, report.render()


def test_the_writer_refuses_and_writes_nothing(tmp_path: Path) -> None:
    unconverted = tmp_path / "unconverted"
    write_tree(unconverted, {"onboarding/overview.md": b"# root\n"})
    with pytest.raises(CensusWriteError, match="converted memory tree"):
        CensusWriter(unconverted).add_claims(CENSUS, ".", [])
    converted = tmp_path / "converted"
    write_tree(converted, census_tree_files())
    writer = CensusWriter(converted)
    before = dict(knowledge_tree_from_directory(converted).files)
    refusals = [
        ("skills", claim(1, "onboarding/skills/missing.md.md"), "no inventory row"),
        ("scripts", claim(2, SKILLS_CARD), "governed by route 'skills', not 'scripts'"),
        (
            "skills",
            claim(3, SKILLS_CARD, disposition="admitted_as_decision", records=["DEC-ZZZZZZ"]),
            "does not exist",
        ),
    ]
    for route, document, message in refusals:
        with pytest.raises(CensusWriteError, match=message):
            writer.add_claims(CENSUS, route, [CensusClaim.model_validate(document)])
    with pytest.raises(CensusWriteError, match="already exists"):
        census = read_censuses(before).censuses[CENSUS]
        writer.create(census.baseline, census.inventory)  # type: ignore[arg-type]
    with pytest.raises(CensusWriteError, match="no claim"):
        writer.append_assessment(
            CENSUS, "CLM-000009", Assessment.model_validate(assessment("unresolved"))
        )
    assert dict(knowledge_tree_from_directory(converted).files) == before


# --------------------------------------------------------------------------------------------------
# The validator's census rules (MIK-R22 rule 9; rules 1, 2, 3 and 6)
# --------------------------------------------------------------------------------------------------


def test_census_rules_are_registered_and_refusing() -> None:
    registered = {rule.id: rule for rule in registered_rules()}
    assert set(CENSUS_RULES) <= set(registered)
    assert not any(registered[rule].report_only for rule in CENSUS_RULES)
    assert _validate(wave_one_files()).ok


def test_a_recorded_assessment_is_never_edited_and_a_correction_appends() -> None:
    base = wave_one_files()
    path = claims_path(CENSUS, "skills")

    def set_verdict(document: dict[str, Any]) -> None:
        document["claims"][0]["assessments"][0]["verdict"] = "concern_found"

    edited = edit_json(base, path, set_verdict)
    report = _validate(edited, (base,))
    assert _census_rules(report) == {"R20.6-assessment-append-only"}, report.render()
    assert "a correction appends a new assessment" in report.render()

    def correct(document: dict[str, Any]) -> None:
        document["claims"][0]["assessments"].append(assessment("concern_found"))
        document["claims"][0]["disposition"] = "discarded_false"

    assert _validate(edit_json(base, path, correct), (base,)).ok

    def drop_claim(document: dict[str, Any]) -> None:
        del document["claims"][0]

    report = _validate(edit_json(base, path, drop_claim), (base,))
    assert _census_rules(report) == {"R20.6-assessment-append-only"}
    assert "was removed" in report.render()


def test_route_statuses_are_append_only_and_merges_keep_both_parents() -> None:
    base = wave_one_files()
    path = route_status_path(CENSUS, "skills")

    def rewrite(document: dict[str, Any]) -> None:
        document["statuses"][0]["reason"] = "rewritten"

    assert _census_rules(_validate(edit_json(base, path, rewrite), (base,))) == {
        "R20.3-status-append-only"
    }
    deleted = {key: value for key, value in base.items() if key != path}
    assert _census_rules(_validate(deleted, (base,))) == {"R20.3-status-append-only"}

    def append(status: str, at: str) -> Any:
        def change(document: dict[str, Any]) -> None:
            document["statuses"].append(status_document("skills", (status, at))["statuses"][0])

        return change

    left = edit_json(base, path, append("blocked", "2026-09-29T10:00:00+02:00"))
    right = edit_json(base, path, append("excluded", "2026-09-29T11:00:00+02:00"))
    both = edit_json(left, path, append("excluded", "2026-09-29T11:00:00+02:00"))
    assert _validate(both, (left, right)).ok
    assert _census_rules(_validate(left, (left, right))) == {"R20.3-status-append-only"}


def test_the_baseline_and_inventory_are_pinned() -> None:
    base = wave_one_files()

    def drop_row(document: dict[str, Any]) -> None:
        document["sources"].pop()

    report = _validate(edit_json(base, inventory_path(CENSUS), drop_row), (base,))
    assert _census_rules(report) == {"R20.1-census-pinned"}
    assert "a new baseline is a new census" in report.render()


@pytest.mark.parametrize(
    ("change", "rule", "message"),
    [
        (
            lambda f: {**f, "knowledge/census/wave-1/notes.md": b"# notes\n"},
            "R20.1-census-shape",
            "not a census file location",
        ),
        (
            lambda f: {
                **f,
                claims_path(CENSUS, "skills"): f[claims_path(CENSUS, "skills")].replace(
                    b"  ", b"   "
                ),
            },
            "R20.1-census-canonical",
            "knowledge-format",
        ),
        (
            lambda f: {
                **f,
                "knowledge/census/wave-1/claims/skills2.json": f[claims_path(CENSUS, "skills")],
            },
            "R20.1-census-shape",
            "is filed at claims/skills.json",
        ),
        (
            lambda f: {
                **f,
                "knowledge/census/wave-9/claims/skills.json": f[claims_path(CENSUS, "skills")],
            },
            "R20.1-census-shape",
            "belongs to census 'wave-9'",
        ),
        (
            lambda f: _with_claims(f, "skills", claim(99, "onboarding/nowhere.md")),
            "R20.2-claim-rows",
            "is no inventory row",
        ),
        (
            lambda f: _with_claims(f, "skills", claim(1, SKILLS_CARD), claim(99, SKILLS_CARD)),
            "R20.2-claim-rows",
            "already defined",
        ),
        (
            lambda f: _with_claims(
                f,
                "skills",
                claim(99, SKILLS_CARD, disposition="admitted_as_family", records=["FAM-ZZZZZZ"]),
            ),
            "R20.2-claim-records",
            "FAM-ZZZZZZ, which does not exist",
        ),
        (
            lambda f: {
                **f,
                route_status_path(CENSUS, "docs"): encode(
                    status_document("docs", ("excluded", "2026-09-29T08:00:00+02:00"))
                ),
            },
            "R20.3-route-known",
            "governs no row",
        ),
    ],
    ids=["location", "canonical", "slug", "census", "row", "duplicate", "records", "route"],
)
def test_census_integrity_rules_name_file_field_and_rule(
    change: Any, rule: str, message: str
) -> None:
    report = _validate(change(wave_one_files()))
    assert rule in _census_rules(report), report.render()
    assert message in report.render(), report.render()


# --------------------------------------------------------------------------------------------------
# Fix round 1 (review R1)
# --------------------------------------------------------------------------------------------------

LOOKALIKE_ROUTES = ("skills/c-10-adopt-memory-baseline", "skills/inventory")


def _census_on(tmp_path: Path, extra: dict[str, bytes]) -> tuple[CensusWriter, dict[str, bytes]]:
    """A converted tree with ``extra`` onboarding files, and a census ``wave-1`` created over it."""

    files = {
        path: data
        for path, data in census_tree_files().items()
        if not path.startswith("knowledge/census/")
    }
    files.update(extra)
    write_tree(tmp_path, files)
    census = read_censuses(census_tree_files()).censuses[CENSUS]
    inventory = build_inventory(CENSUS, code_paths=CENSUS_CODE_PATHS, memory_paths=files)
    writer = CensusWriter(tmp_path)
    writer.create(census.baseline, inventory)  # type: ignore[arg-type]
    return writer, files


def test_routes_whose_slug_ends_like_a_pinned_file_still_append(tmp_path: Path) -> None:
    extra = {
        f"onboarding/{route}/{name}": f"# {route}\n".encode()
        for route in LOOKALIKE_ROUTES
        for name in ("overview.md", "card.md")
    }
    writer, _ = _census_on(tmp_path, extra)
    assert route_status_path(CENSUS, LOOKALIKE_ROUTES[0]).endswith("baseline.json")
    assert claims_path(CENSUS, LOOKALIKE_ROUTES[1]).endswith("inventory.json")
    snapshots = []
    for number, route in enumerate(LOOKALIKE_ROUTES, start=1):
        document = claim(number, f"onboarding/{route}/card.md")
        for status, at in (("in_progress", "08:00"), ("migrated", "09:00")):
            entry = status_document(route, (status, f"2026-09-29T{at}:00+02:00"))["statuses"][0]
            writer.append_status(CENSUS, route, StatusEntry.model_validate(entry))
            snapshots.append(dict(knowledge_tree_from_directory(tmp_path).files))
        writer.add_claims(CENSUS, route, [CensusClaim.model_validate(document)])
        writer.append_assessment(
            CENSUS, document["id"], Assessment.model_validate(assessment("unresolved"))
        )
        snapshots.append(dict(knowledge_tree_from_directory(tmp_path).files))
    for before, after in pairwise(snapshots):
        report = _validate(after, (before,))
        assert report.ok, report.render()


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [
        ("text", "A reworded claim.", "R20.2-claim-stable"),
        (
            "location",
            {"artifact": SKILLS_CARD, "lines": {"start": 1, "end": 1}},
            "R20.2-claim-stable",
        ),
        ("kind", "historical_rationale", "R20.2-claim-stable"),
        ("applicability", "non_claim", "R20.2-claim-stable"),
        ("id", "CLM-ZZZZZZ", "R20.6-assessment-append-only"),
    ],
)
def test_a_recorded_claims_identity_and_cohort_fields_are_stable(
    field: str, value: Any, rule: str
) -> None:
    base = wave_one_files()

    def change(document: dict[str, Any]) -> None:
        document["claims"][0][field] = value

    report = _validate(edit_json(base, claims_path(CENSUS, "skills"), change), (base,))
    assert _census_rules(report) == {rule}, report.render()
    if rule == "R20.2-claim-stable":
        assert f"'s {field} changed" in report.render()

    def settle(document: dict[str, Any]) -> None:
        document["claims"][0]["disposition"] = "pending"

    assert _validate(edit_json(base, claims_path(CENSUS, "skills"), settle), (base,)).ok


def test_an_interrupted_create_leaves_no_baseline_and_can_be_repeated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        path: data
        for path, data in census_tree_files().items()
        if not path.startswith("knowledge/census/")
    }
    write_tree(tmp_path, files)
    census = read_censuses(census_tree_files()).censuses[CENSUS]
    real_write = writer_module.atomic_write_bytes

    def fail_on_baseline(path: Path, data: bytes) -> None:
        if path.name == "baseline.json":
            raise OSError("disk full")
        real_write(path, data)

    monkeypatch.setattr(writer_module, "atomic_write_bytes", fail_on_baseline)
    writer = CensusWriter(tmp_path)
    with pytest.raises(OSError, match="disk full"):
        writer.create(census.baseline, census.inventory)  # type: ignore[arg-type]
    directory = tmp_path / "knowledge/census" / CENSUS
    assert sorted(item.name for item in directory.iterdir()) == ["inventory.json"]
    monkeypatch.setattr(writer_module, "atomic_write_bytes", real_write)
    writer.create(census.baseline, census.inventory)  # type: ignore[arg-type]
    assert sorted(item.name for item in directory.iterdir()) == ["baseline.json", "inventory.json"]
    with pytest.raises(CensusWriteError, match="already exists"):
        writer.create(census.baseline, census.inventory)  # type: ignore[arg-type]


def test_model_refusals_in_the_writer_are_census_write_errors(tmp_path: Path) -> None:
    writer, _ = _census_on(tmp_path, {})
    first = CensusClaim.model_validate(claim(1, SKILLS_CARD, "unresolved"))
    writer.add_claims(CENSUS, "skills", [first])
    before = dict(knowledge_tree_from_directory(tmp_path).files)
    with pytest.raises(CensusWriteError, match="claim ids must not repeat"):
        writer.add_claims(CENSUS, "skills", [first])
    with pytest.raises(CensusWriteError, match="latest assessment is concern_found"):
        writer.set_disposition(CENSUS, first.id, "discarded_false")
    late = status_document("skills", ("migrated", "2026-09-29T09:00:00+02:00"))["statuses"][0]
    early = status_document("skills", ("blocked", "2026-09-29T08:00:00+02:00"))["statuses"][0]
    writer.append_status(CENSUS, "skills", StatusEntry.model_validate(late))
    with pytest.raises(CensusWriteError, match="time order"):
        writer.append_status(CENSUS, "skills", StatusEntry.model_validate(early))
    after = dict(knowledge_tree_from_directory(tmp_path).files)
    assert {path for path in after if after[path] != before.get(path)} == {
        route_status_path(CENSUS, "skills")
    }


def test_the_census_rule_cache_keeps_no_tree_after_validation() -> None:
    report = _validate(wave_one_files())
    assert report.ok
    gc.collect()
    assert rules_census._FINDINGS == {}
