"""Passage renewal preserves semantic ownership and requires explicit changed-text binding."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents_remember_test_support.testing.digest_map import _assert_digest_places
from agents_remember_test_support.testing.instruction_renewal import renewal_module

MODULE = renewal_module("digest")
LIFECYCLE = Path("skills/l-01-agent-lifecycles")


def _facts(text: str, line: int) -> dict:
    return {"lines": [line, line], "sha256": sha256(text.encode()).hexdigest()}


def _world(root: Path) -> tuple[Path, Path, dict]:
    lifecycle = root / LIFECYCLE
    (lifecycle / "core").mkdir(parents=True)
    source = lifecycle / "core/authority.md"
    source.write_text("# Source\n\nA source rule.\n")
    digest = lifecycle / "core/every-role.md"
    digest.write_text("# Shared\n\nIntroduction.\n\n## Duties\n\nWorker: Follow the rule.\n")
    (lifecycle / "composition-manifest.json").write_text(json.dumps({"role_order": ["worker"]}))
    document = {
        "units": [
            {"id": "U1", "file": "core/authority.md", "disposition": "digest", "digestPlace": "p1"}
        ],
        "digest": {
            "path": "core/every-role.md",
            "places": [{"id": "p1", "units": ["U1"], "clauses": [{"units": ["U1"]}]}],
        },
        "derived": {
            "roles": ["worker"],
            "units": {"U1": {**_facts("A source rule.", 3), "audience": ["worker"]}},
            "evidence": {},
            "digest": {
                "sha256": sha256(digest.read_bytes()).hexdigest(),
                "sizeWords": len(digest.read_text().split()),
            },
            "places": {
                "p1": {
                    **_facts("Worker: Follow the rule.", 7),
                    "heading": "Duties",
                    "clauses": [{"lines": [7, 7], "audience": ["worker"]}],
                }
            },
            "introduction": _facts("Introduction.", 3),
        },
    }
    (lifecycle / "core/digest-map.json").write_text(json.dumps(document))
    return source, digest, document


def test_unchanged_passage_relocation_updates_ranges_without_writing(tmp_path: Path) -> None:
    source, digest, _ = _world(tmp_path)
    source.write_text(source.read_text().replace("\n\n", "\n\n\n", 1))
    digest.write_text(digest.read_text().replace("## Duties", "\n## Duties", 1))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    derived, notices = MODULE.plan(tmp_path, ())
    assert derived["units"]["U1"]["lines"] == [4, 4]
    assert derived["places"]["p1"]["lines"] == [8, 8]
    assert derived["places"]["p1"]["clauses"][0]["lines"] == [8, 8]
    assert not notices
    assert before == {path: path.read_bytes() for path in before}


@pytest.mark.parametrize("replacement", ["A changed rule.", "Another paragraph.", ""])
def test_changed_replaced_and_removed_passages_stop_with_unit_and_current_text(
    tmp_path: Path, replacement: str
) -> None:
    source, _, _ = _world(tmp_path)
    source.write_text("# Source\n\n" + replacement + "\n")
    with pytest.raises(ValueError, match="Unresolved unit U1") as caught:
        MODULE.plan(tmp_path, ())
    assert "Held sha256:" in str(caught.value)
    assert "core/digest-map.json: derived.units.U1.{lines,sha256}" in str(caught.value)
    assert "HEAD cannot supply" in str(caught.value)
    assert replacement in str(caught.value)
    assert "--bind-unit U1=" in str(caught.value)


def test_changed_source_and_digest_require_explicit_bindings_and_report_units(
    tmp_path: Path,
) -> None:
    source, digest, document = _world(tmp_path)
    source.write_text(source.read_text().replace("source", "changed"))
    digest.write_text(digest.read_text().replace("Follow", "Keep"))
    derived, notices = MODULE.plan(
        tmp_path,
        (
            "U1=core/authority.md:3-3",
            "U1=core/every-role.md:7-7",
            "U1@clause:p1:0=core/every-role.md:7-7",
        ),
    )
    assert derived["units"]["U1"]["sha256"] != document["derived"]["units"]["U1"]["sha256"]
    assert derived["places"]["p1"]["sha256"] != document["derived"]["places"]["p1"]["sha256"]
    assert len(notices) == 3 and all("U1" in line and "Worker's word" in line for line in notices)


def test_identical_windows_are_ambiguous_until_the_worker_names_the_range(tmp_path: Path) -> None:
    source, _, _ = _world(tmp_path)
    source.write_text(source.read_text() + "\nA source rule.\n")
    with pytest.raises(ValueError, match="2 hash matches"):
        MODULE.plan(tmp_path, ())
    derived, _ = MODULE.plan(tmp_path, ("U1=core/authority.md:5-5",))
    assert derived["units"]["U1"]["lines"] == [5, 5]


def test_repeated_evidence_associations_use_explicit_record_selectors(tmp_path: Path) -> None:
    _, _, document = _world(tmp_path)
    role = tmp_path / LIFECYCLE / "roles/worker.md"
    role.parent.mkdir()
    role.write_text("Changed first.\nChanged second.\n")
    path = (LIFECYCLE / "roles/worker.md").as_posix()
    document["units"][0]["evidence"] = [{"path": path, "capsule": "worker/implementation"}] * 2
    document["derived"]["evidence"] = {
        "U1": {"0": _facts("Old first.", 1), "1": _facts("Old second.", 2)}
    }
    (tmp_path / MODULE.MAP).write_text(json.dumps(document))
    with pytest.raises(ValueError, match="U1/evidence/0"):
        MODULE.plan(tmp_path, (f"U1={path}:1-1",))
    derived, _ = MODULE.plan(
        tmp_path,
        (
            f"U1@evidence:0={path}:2-2",
            f"U1@evidence:1={path}:1-1",
        ),
    )
    assert derived["evidence"]["U1"]["0"]["lines"] == [2, 2]
    assert derived["evidence"]["U1"]["1"]["lines"] == [1, 1]
    with pytest.raises(ValueError, match="U1/evidence/1"):
        MODULE.plan(tmp_path, (f"U1@evidence:0={path}:2-2", f"U1={path}:1-1"))


def test_head_text_is_displayed_only_when_it_proves_the_held_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _, _ = _world(tmp_path)
    held = source.read_bytes()
    source.write_text(source.read_text().replace("source", "changed"))
    monkeypatch.setattr(
        MODULE.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=held)
    )
    with pytest.raises(ValueError) as proven:
        MODULE.plan(tmp_path, ())
    assert "Held text proven from HEAD:\nA source rule." in str(proven.value)
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=b"# Source\n\nWrong text.\n"),
    )
    with pytest.raises(ValueError) as unproven:
        MODULE.plan(tmp_path, ())
    assert "HEAD cannot supply" in str(unproven.value)
    assert "Held text proven" not in str(unproven.value)


def _save_map(root: Path, document: dict) -> None:
    (root / MODULE.MAP).write_text(json.dumps(document))


def test_new_unit_requires_its_own_binding_then_derives_facts(tmp_path: Path) -> None:
    source, _, document = _world(tmp_path)
    source.write_text(source.read_text() + "\nA new source rule.\n")
    document["units"].append({"id": "U2", "file": "core/authority.md", "disposition": "reference"})
    _save_map(tmp_path, document)
    with pytest.raises(ValueError, match="U2") as caught:
        MODULE.plan(tmp_path, ())
    assert "--bind-unit U2=" in str(caught.value)
    derived, _ = MODULE.plan(tmp_path, ("U2=core/authority.md:5-5",))
    assert derived["units"]["U2"]["lines"] == [5, 5]


def test_new_evidence_requires_its_own_binding_then_derives_facts(tmp_path: Path) -> None:
    source, _, document = _world(tmp_path)
    document["units"][0]["evidence"] = [
        {"path": (LIFECYCLE / "core/authority.md").as_posix(), "capsule": "worker/implementation"}
    ]
    _save_map(tmp_path, document)
    with pytest.raises(ValueError, match="U1/evidence/0") as caught:
        MODULE.plan(tmp_path, ())
    assert "--bind-unit U1@evidence:0=" in str(caught.value)
    derived, _ = MODULE.plan(tmp_path, ("U1@evidence:0=core/authority.md:3-3",))
    assert derived["evidence"]["U1"]["0"] == _facts(source.read_text().splitlines()[2], 3)


def test_new_place_and_clause_require_their_own_bindings(tmp_path: Path) -> None:
    _, _, document = _world(tmp_path)
    document["units"][0]["digestPlace"] = "p2"
    document["digest"]["places"][0]["id"] = "p2"
    _save_map(tmp_path, document)
    bindings = ("U1=core/every-role.md:7-7",)
    with pytest.raises(ValueError, match=r"p2.*clause|clause.*p2") as caught:
        MODULE.plan(tmp_path, bindings)
    assert "U1@clause:p2:0=" in str(caught.value)
    derived, _ = MODULE.plan(tmp_path, (*bindings, "U1@clause:p2:0=core/every-role.md:7-7"))
    assert derived["places"]["p2"]["clauses"][0]["lines"] == [7, 7]


def test_plain_unit_binding_does_not_rebind_its_evidence(tmp_path: Path) -> None:
    source, _, document = _world(tmp_path)
    document["units"][0]["evidence"] = [
        {"path": (LIFECYCLE / "core/authority.md").as_posix(), "capsule": "worker/implementation"}
    ]
    document["derived"]["evidence"] = {"U1": {"0": _facts("# Source", 1)}}
    _save_map(tmp_path, document)
    source.write_text(source.read_text().replace("source rule", "changed rule"))
    derived, _ = MODULE.plan(tmp_path, ("U1=core/authority.md:3-3",))
    assert derived["evidence"]["U1"]["0"] == _facts("# Source", 1)
    document["derived"]["evidence"]["U1"]["0"] = {
        "lines": [1, 3],
        "sha256": sha256(b"# Source\n\nA source rule.").hexdigest(),
    }
    _save_map(tmp_path, document)
    with pytest.raises(ValueError, match="U1/evidence/0") as caught:
        MODULE.plan(tmp_path, ("U1=core/authority.md:3-3",))
    assert "U1@evidence:0=" in str(caught.value)


def test_changed_place_does_not_assign_clause_by_old_offset(tmp_path: Path) -> None:
    _, digest, _ = _world(tmp_path)
    digest.write_text(
        digest.read_text().replace("Worker: Follow the rule.", "Worker:\n\nFollow the rule.")
    )
    with pytest.raises(ValueError, match=r"p1.*clause|clause.*p1") as caught:
        MODULE.plan(tmp_path, ("U1=core/every-role.md:7-9",))
    assert "U1@clause:p1:0=" in str(caught.value)
    derived, _ = MODULE.plan(
        tmp_path, ("U1=core/every-role.md:7-9", "U1@clause:p1:0=core/every-role.md:7-9")
    )
    assert derived["places"]["p1"]["clauses"][0]["lines"] == [7, 9]


def test_own_clause_hash_finds_text_inside_an_explicitly_changed_place(tmp_path: Path) -> None:
    _, digest, document = _world(tmp_path)
    document["derived"], _ = MODULE.plan(tmp_path, ())
    _save_map(tmp_path, document)
    digest.write_text(
        digest.read_text().replace(
            "Worker: Follow the rule.", "New preface.\n\nWorker: Follow the rule."
        )
    )
    with pytest.raises(ValueError, match="unmapped clause prose") as caught:
        MODULE.plan(tmp_path, ("U1=core/every-role.md:7-9",))
    assert "p1" in str(caught.value) and "core/every-role.md:7" in str(caught.value)
    assert "Decide which authored clause" in str(caught.value)
    derived, _ = MODULE.plan(
        tmp_path, ("U1=core/every-role.md:7-9", "U1@clause:p1:0=core/every-role.md:7-9")
    )
    assert derived["places"]["p1"]["clauses"][0]["lines"] == [7, 9]


def test_headingless_place_stops_with_a_working_authored_remedy(tmp_path: Path) -> None:
    _, digest, document = _world(tmp_path)
    digest.write_text("Worker: Follow the rule.\n")
    document["derived"]["introduction"] = _facts("Worker: Follow the rule.", 1)
    _save_map(tmp_path, document)
    with pytest.raises(ValueError, match=r"p1.*heading|heading.*p1"):
        MODULE.plan(tmp_path, ("U1=core/every-role.md:1-1",))


def test_reference_audience_is_authored_and_never_emitted_as_derived(tmp_path: Path) -> None:
    source, _, document = _world(tmp_path)
    source.write_text(source.read_text() + "\nA reference rule.\n")
    document["units"].append(
        {
            "id": "U2",
            "file": "core/authority.md",
            "disposition": "reference",
            "audience": ["worker"],
        }
    )
    document["derived"]["units"]["U2"] = {**_facts("A reference rule.", 5), "audience": []}
    _save_map(tmp_path, document)
    derived, _ = MODULE.plan(tmp_path, ())
    assert "audience" not in derived["units"]["U2"]
    document["derived"] = derived
    assert MODULE.joined_map(document)["units"][1]["audience"] == ["worker"]


def _repository_world(root: Path) -> dict:
    checkout = Path(__file__).resolve().parents[2]
    document = json.loads((checkout / MODULE.MAP).read_text())
    paths = {
        MODULE.MAP,
        LIFECYCLE / "composition-manifest.json",
        LIFECYCLE / document["digest"]["path"],
    }
    for unit in document["units"]:
        if "retiredBinding" not in unit:
            paths.add(LIFECYCLE / unit["file"])
        for evidence in unit.get("evidence", []):
            if "path" in evidence and "symbol" not in evidence:
                paths.add(Path(evidence["path"]))
    for relative in paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((checkout / relative).read_bytes())
    document["derived"], _ = MODULE.plan(root, ())
    _save_map(root, document)
    return document


def test_lf03_source_binding_does_not_claim_its_overlapping_evidence(tmp_path: Path) -> None:
    document = _repository_world(tmp_path)
    source = tmp_path / LIFECYCLE / "core/lifecycle-frame.md"
    source.write_text(
        source.read_text().replace(
            "describes existing-task attachment", "explains existing-task attachment"
        )
    )
    binding = "LF0.3=core/lifecycle-frame.md:9-9"
    siblings = tuple(
        f"{identity}@evidence:{number}=core/lifecycle-frame.md:7-11"
        for identity, records in document["derived"]["evidence"].items()
        for number, facts in records.items()
        if identity != "LF0.3"
        and facts["sha256"] == document["derived"]["evidence"]["LF0.3"]["0"]["sha256"]
    )
    with pytest.raises(ValueError, match=r"LF0\.3/evidence/0") as caught:
        MODULE.plan(tmp_path, (binding, *siblings))
    assert "LF0.3@evidence:0=" in str(caught.value)
    derived, _ = MODULE.plan(
        tmp_path, (binding, *siblings, "LF0.3@evidence:0=core/lifecycle-frame.md:7-11")
    )
    assert derived["evidence"]["LF0.3"]["0"]["lines"] == [7, 11]


def test_au41_split_requires_its_clause_binding_without_widening_audience(tmp_path: Path) -> None:
    document = _repository_world(tmp_path)
    digest = tmp_path / LIFECYCLE / "core/every-role.md"
    digest.write_text(
        digest.read_text().replace("request identifier. If", "request identifier.\n\nIf", 1)
    )
    binding = "AU4.1=core/every-role.md:27-31"
    with pytest.raises(ValueError, match="place-04/clause/0") as caught:
        MODULE.plan(tmp_path, (binding,))
    assert "AU4.1@clause:place-04:0=" in str(caught.value)
    derived, _ = MODULE.plan(
        tmp_path, (binding, "AU4.1@clause:place-04:0=core/every-role.md:27-29")
    )
    assert derived["places"]["place-04"]["clauses"][1]["lines"] == [31, 31]
    assert (
        derived["units"]["AU4.1"]["audience"] == document["derived"]["units"]["AU4.1"]["audience"]
    )


@pytest.mark.parametrize(
    "case", ["unknown-clause-unit", "place-without-clauses", "unit-without-file"]
)
def test_authored_map_errors_name_manual_correction_before_deriving(
    tmp_path: Path, case: str
) -> None:
    source, _, document = _world(tmp_path)
    source.write_text(source.read_text().replace("source rule", "changed rule"))
    place = document["digest"]["places"][0]
    if case == "unknown-clause-unit":
        place["clauses"][0]["units"] = ["UNKNOWN"]
    elif case == "place-without-clauses":
        del place["clauses"]
    else:
        del document["units"][0]["file"]
    _save_map(tmp_path, document)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="Hand-written map") as caught:
        MODULE.plan(tmp_path, ())
    message = str(caught.value)
    assert "core/digest-map.json" in message and "by hand" in message
    assert ("U1" if case == "unit-without-file" else "p1") in message
    if case != "unit-without-file":
        assert "core/every-role.md" in message
    if case == "unknown-clause-unit":
        assert "UNKNOWN" in message and "clause 0" in message
    assert before == {path: path.read_bytes() for path in before}


def test_reader_names_clause_decision_for_uncovered_prose(tmp_path: Path) -> None:
    _, digest, document = _world(tmp_path)
    document["derived"], _ = MODULE.plan(tmp_path, ())
    digest.write_text(digest.read_text().replace("Worker:", "New preface.\n\nWorker:"))
    current = document["derived"]["places"]["p1"]
    current.update(
        lines=[7, 9], sha256=sha256(b"New preface.\n\nWorker: Follow the rule.").hexdigest()
    )
    current["clauses"][0]["lines"] = [9, 9]
    with pytest.raises(AssertionError, match="unmapped clause prose") as caught:
        _assert_digest_places(MODULE.joined_map(document), digest.read_text())
    assert "p1" in str(caught.value) and "core/every-role.md:7" in str(caught.value)
    assert "Decide which authored clause" in str(caught.value)
