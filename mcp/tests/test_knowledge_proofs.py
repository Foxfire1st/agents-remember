"""MIK-R28: tests that prove an invariant are first-class proof entries.

* **From the hand-off (rule 2).** The evidence names a test as a test ID ``path::name`` or as a path
  plus symbol (``path -k name``). The writer prepares a proof and writes it only once ``proofs``
  carries the curator's facet; evidence that names no resolvable test is reported, never dropped.
* **Views (rule 4).** ``knowledge_read``'s ``invariant`` and ``family`` responses of a converted tree
  carry the proofs of the invariant, or of every family member; a database read has no proofs.
* **Without proof (rules 5, 6).** The index lists the live invariants no proof names; the curator
  checklist shows the list as information that moves no count. A migrated record's evidence names
  its tests, and a curator pass through the writer turns them into proofs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_proofs import invariants_without_proof
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.application.knowledge_writer.handoff import (
    TestFileMention as FileMention,
)
from agents_remember.application.knowledge_writer.handoff import (
    TestReference as Reference,
)
from agents_remember.application.knowledge_writer.handoff import (
    tests_named_in as named_in,
)
from agents_remember.application.memory_quality.controller import (
    _without_proof as checklist_without_proof,  # pyright: ignore[reportPrivateUsage]
)
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.memory.knowledge_index import (
    KnowledgeIndex,
    build_index,
    directory_snapshot,
    text_uuid,
)
from agents_remember.memory_quality.curator_checklist import (
    ATTESTATION_FILE_NAME,
    CuratorChecklist,
    WithoutProof,
    report_path_for,
    write_curator_checklist,
)
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.documents import file_sidecar_path
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from knowledge_index_test_support import (
    FAMILY,
    OUTSIDER_INVARIANT,
    REVIEW_INVARIANT,
    SIBLING_INVARIANT,
    TEST_PATH,
    commit_all,
    init_repository,
    write_review_tree,
)
from knowledge_writer_test_support import (
    ADMISSION,
    BASE_INVARIANT,
    LEAF_ID,
    SCOPE,
    TASK_ID,
    TEST_FILE,
    World,
    build_world,
    entry,
    read_json,
    target,
    write,
)

OWNER = Owner(task=TASK_ID, kind="leaf", id=LEAF_ID)


def _write(world: World, document: Any, *, commit: bool = True):
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="notes/handoff.json",
            document=document,
            commit=commit,
        )
    )


# --- rule 2: from the hand-off ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("evidence", "named"),
    [
        (
            "pytest mcp/tests/test_a.py::Holder::test_one",
            (Reference("mcp/tests/test_a.py", "Holder.test_one"),),
        ),
        ("pytest mcp/tests/test_a.py -k test_one", (Reference("mcp/tests/test_a.py", "test_one"),)),
        (
            "pytest mcp/tests/test_a.py -k 'test_one'.",
            (Reference("mcp/tests/test_a.py", "test_one"),),
        ),
        # An expression names no one test, so only the file is named.
        ("pytest mcp/tests/test_a.py -k 'a and not b'", (FileMention("mcp/tests/test_a.py"),)),
        ("pytest mcp/tests/test_a.py -k one or two", (FileMention("mcp/tests/test_a.py"),)),
        (
            "pinned in mcp/tests/test_a.py, see pkg/landing.py",
            (FileMention("mcp/tests/test_a.py"),),
        ),
        ("the report path notes/reports/run.md", ()),
        # Markdown and prose around a command end the selection like whitespace does.
        (
            "`pytest mcp/tests/test_a.py -k test_one`",
            (Reference("mcp/tests/test_a.py", "test_one"),),
        ),
        (
            "(pytest mcp/tests/test_a.py -k test_one)",
            (Reference("mcp/tests/test_a.py", "test_one"),),
        ),
        (
            "[pytest mcp/tests/test_a.py -k test_one]",
            (Reference("mcp/tests/test_a.py", "test_one"),),
        ),
        (
            '"pytest mcp/tests/test_a.py -k test_one"',
            (Reference("mcp/tests/test_a.py", "test_one"),),
        ),
        # Helper modules hold no tests, so naming one is not a test file without a test.
        (
            "see mcp/tests/conftest.py, mcp/tests/knowledge_index_test_support.py and "
            "mcp/tests/helpers.py",
            (),
        ),
    ],
)
def test_evidence_names_a_test_as_a_test_id_or_as_a_path_plus_symbol(
    evidence: str, named: tuple[object, ...]
) -> None:
    assert named_in([evidence]) == named


def test_a_file_named_with_a_test_anywhere_in_the_evidence_is_not_also_reported_bare() -> None:
    evidence = ["pytest mcp/tests/test_a.py::test_x", "the pin in mcp/tests/test_a.py holds"]
    assert named_in(evidence) == (Reference("mcp/tests/test_a.py", "test_x"),)
    assert named_in(list(reversed(evidence))) == (Reference("mcp/tests/test_a.py", "test_x"),)


def test_both_forms_become_proofs_once_faceted_and_unresolvable_evidence_is_reported(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    evidence = [
        f"pytest {TEST_FILE} -k test_plain",
        f"pytest {TEST_FILE}::LandingTests::test_pair_lands_together",
        f"pytest {TEST_FILE} -k test_absent",
        "the pin in tests/test_gone.py records it",
    ]
    report = _write(
        world,
        [
            entry(
                "P-1",
                target=[target("land_pair")],
                evidence=evidence,
                scope=SCOPE,
                admission=ADMISSION,
                proofs=[
                    {"test": {"path": TEST_FILE, "symbol": "test_plain"}, "facet": "plain lands"},
                    {
                        "test": f"{TEST_FILE}::LandingTests::test_pair_lands_together",
                        "facet": "both halves land as one value",
                    },
                ],
            )
        ],
    )
    assert report.state == "written", report.render()
    (outcome,) = report.evidence
    states = {test.test: test.state for test in outcome.tests}
    assert states == {
        f"{TEST_FILE}::test_plain": "proof_written",
        f"{TEST_FILE}::LandingTests::test_pair_lands_together": "proof_written",
        f"{TEST_FILE}::test_absent": "unresolvable",
        "tests/test_gone.py": "unresolvable",
    }
    proves = read_json(world.memory, f"onboarding/{TEST_FILE}.json")["proves"]
    assert sorted((one["anchor"]["locator"]["name"], one["facet"]) for one in proves) == [
        ("LandingTests.test_pair_lands_together", "both halves land as one value"),
        ("test_plain", "plain lands"),
    ]
    assert all(one["id"].startswith("PRF-") and "path" not in one["anchor"] for one in proves)
    rendered = report.render()
    assert "tests/test_gone.py" in rendered and "no test in it" in rendered


def test_a_proof_waits_for_the_curator_facet_and_is_offered_the_statement_as_a_draft(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    cited = entry(
        "P-2",
        target=[target("land_pair")],
        evidence=f"pytest {TEST_FILE} -k test_plain",
        scope=SCOPE,
        admission=ADMISSION,
    )
    report = _write(world, [cited])
    assert report.state == "written", report.render()
    ((test,),) = [outcome.tests for outcome in report.evidence]
    assert test.state == "needs_facet"
    assert "draft from the statement" in test.detail and "Statement of P-2" in test.detail
    assert not (world.memory / f"onboarding/{TEST_FILE}.json").exists()

    blank = _write(world, [{**cited, "proofs": [{"test": f"{TEST_FILE}::test_plain"}]}])
    assert blank.state == "refused"
    assert "the curator authors the proof's 'facet'" in blank.render()
    assert not (world.memory / f"onboarding/{TEST_FILE}.json").exists()


# --- rule 4: views ------------------------------------------------------------------------------


def _review_tree(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    commit_all(root)
    return root


def _read(root: Path, coordination: Path, **request: Any) -> dict[str, Any]:
    return knowledge_read_payload(
        ReadToolRequest(memory_root=str(root), **request),
        coordination_root=str(coordination),
    )


def test_the_invariant_and_family_views_carry_their_proofs(tmp_path: Path) -> None:
    root = _review_tree(tmp_path)
    coordination = tmp_path / "coordination"
    proven = _read(
        root,
        coordination,
        view="invariant",
        invariant_revision_id=text_uuid("revision", f"{REVIEW_INVARIANT}@1"),
    )
    assert proven["state"] == "view", proven
    (proof,) = proven["proofs"]
    assert proof["id"] == "PRF-T3ST0K"
    assert proof["invariant"] == REVIEW_INVARIANT
    assert proof["path"] == "mcp/tests/test_review_family_context.py"
    assert proof["anchor"]["locator"] == {"kind": "symbol", "name": "test_unchanged_context"}
    assert proof["facet"] == "unchanged realizations are shown"
    # A proof states what its test demonstrates; nothing claims the test passed.
    assert not {"passed", "status", "result", "satisfied"} & set(proof)

    unproven = _read(
        root,
        coordination,
        view="invariant",
        invariant_revision_id=text_uuid("revision", f"{SIBLING_INVARIANT}@1"),
    )
    assert unproven["state"] == "view", unproven
    assert unproven["proofs"] == []

    family = _read(
        root, coordination, view="family", family_revision_id=text_uuid("revision", f"{FAMILY}@1")
    )
    assert family["state"] == "view", family
    assert [one["id"] for one in family["proofs"]] == ["PRF-T3ST0K"]


def test_the_other_views_carry_no_proofs(tmp_path: Path) -> None:
    root = _review_tree(tmp_path)
    queue = _read(root, tmp_path / "coordination", view="curation_queue")
    assert queue["state"] == "view", queue
    assert "proofs" not in queue


def test_a_family_whose_members_have_no_proof_shows_an_empty_list(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    init_repository(root)
    write_review_tree(root)
    sidecar = root / file_sidecar_path(TEST_PATH)
    document = json.loads(sidecar.read_text("utf-8"))
    sidecar.write_text(canonical_text({**document, "proves": []}), encoding="utf-8")
    commit_all(root)
    family = _read(
        root,
        tmp_path / "coordination",
        view="family",
        family_revision_id=text_uuid("revision", f"{FAMILY}@1"),
    )
    assert family["state"] == "view", family
    assert family["proofs"] == []


# --- rules 5 and 6: without proof --------------------------------------------------------------


def test_the_index_lists_live_invariants_without_proof(tmp_path: Path) -> None:
    root = _review_tree(tmp_path)
    outsider = next((root / "knowledge" / "invariants").glob(f"{OUTSIDER_INVARIANT}-*.json"))
    document = json.loads(outsider.read_text("utf-8"))
    outsider.write_text(canonical_text({**document, "status": "retired"}), encoding="utf-8")
    snapshot = directory_snapshot(root)
    destination = tmp_path / "index.sqlite"
    build_index(snapshot, destination)
    with KnowledgeIndex(destination, expected_key=snapshot.key) as index:
        listed = index.invariants_without_proof()
        assert [record.id for record in listed.value] == [SIBLING_INVARIANT]
        assert listed.index.complete
        proofs = index.proofs_of((REVIEW_INVARIANT, SIBLING_INVARIANT)).value
        assert [one.id for one in proofs] == ["PRF-T3ST0K"]


def _pair() -> MemoryCandidatePairIdentity:
    return MemoryCandidatePairIdentity(
        repoId="repo-a",
        contractPath="/coordination/tasks/repo-a/task/series-contract.md",
        contractDigest="7" * 64,
        codeRoot="/code",
        memoryRoot="/memory",
        codeSourceBranch="ar/series",
        codeWorkBranch="ar/leaf",
        codeBaseCommit="1" * 40,
        memorySourceBranch="ar/series",
        memoryWorkBranch="ar/leaf",
        memoryBaseCommit="2" * 40,
        onboardingRoot="/memory/onboarding",
        ledgerPath="memory.md",
    )


def _checklist(report_path: Path, without_proof: WithoutProof | None) -> CuratorChecklist:
    return CuratorChecklist(
        report_path=report_path,
        repo_id="repo-a",
        code_root=report_path.parent / "code",
        onboarding_root=report_path.parent / "memory" / "onboarding",
        pair_identity=_pair(),
        code_candidate_tree="a" * 40,
        memory_candidate_tree="b" * 40,
        quality={"findingCount": 0},
        repair_findings=[],
        commit_owned_findings=[],
        missing_onboarding={"missing": [], "missingCount": 0},
        stale_route_indexes=[],
        source_candidates=(),
        drift_rows=[],
        report_only_findings=[],
        without_proof=without_proof,
    )


def test_the_checklist_shows_the_list_as_information_that_moves_no_count(tmp_path: Path) -> None:
    unconverted = report_path_for(tmp_path / "unconverted")
    converted = report_path_for(tmp_path / "converted")
    first = write_curator_checklist(_checklist(unconverted, None))
    row = {
        "invariant": "INV-A1B2C3",
        "status": "accepted",
        "path": "knowledge/invariants/INV-A1B2C3-pair.json",
        "evidenceTests": ["tests/test_landing.py::test_plain"],
    }
    second = write_curator_checklist(_checklist(converted, WithoutProof(rows=(row,))))
    assert first == {
        **second,
        "reportPath": first["reportPath"],
        "attestationPath": first["attestationPath"],
    }
    assert (
        second["curatorActionableCount"] == 0 and second["checklistStatus"] == "ready-for-closeout"
    )
    before = unconverted.read_text(encoding="utf-8")
    after = converted.read_text(encoding="utf-8")
    assert "Invariants without proof" not in before
    assert "## Invariants without proof" in after and "not a gate" in after
    assert (
        "| INV-A1B2C3 | accepted | knowledge/invariants/INV-A1B2C3-pair.json | "
        "`tests/test_landing.py::test_plain` |"
    ) in after
    attestation = json.loads((converted.parent / ATTESTATION_FILE_NAME).read_text("utf-8"))
    assert attestation["curatorActionableCount"] == 0


def test_migrated_evidence_is_listed_then_turned_into_a_proof_by_a_curator_pass(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    assert invariants_without_proof(world.code) is None  # not a converted memory tree
    record = next((world.memory / "knowledge" / "invariants").glob(f"{BASE_INVARIANT}-*.json"))
    document = json.loads(record.read_text("utf-8"))
    # What the conversion (MIK-R24) makes of "Evidence: ..." text in the legacy conditions.
    document["origin"]["handoff"] = {
        "evidence": [f"Evidence: pinned by {TEST_FILE}::test_plain in the landing suite."]
    }
    write(world.memory, {record.relative_to(world.memory).as_posix(): canonical_text(document)})

    coverage = invariants_without_proof(world.memory)
    assert coverage is not None and coverage.problem is None
    (listed,) = coverage.unproven
    assert (listed.id, listed.evidence_tests) == (BASE_INVARIANT, (f"{TEST_FILE}::test_plain",))
    # The memory-quality run hands exactly this list to the checklist, and nothing for unconverted.
    section = checklist_without_proof(world.memory)
    assert section is not None and [row["invariant"] for row in section.rows] == [BASE_INVARIANT]
    assert checklist_without_proof(world.code) is None

    report = _write(
        world,
        [
            entry(
                "M-1",
                invariant_id=BASE_INVARIANT,
                statement=document["statement"],
                proofs=[{"test": f"{TEST_FILE}::test_plain", "facet": "a plain landing lands"}],
            )
        ],
    )
    assert report.state == "written", report.render()
    (proof,) = read_json(world.memory, f"onboarding/{TEST_FILE}.json")["proves"]
    assert (proof["invariant"], proof["facet"]) == (BASE_INVARIANT, "a plain landing lands")
    after = invariants_without_proof(world.memory)
    assert after is not None and after.unproven == ()


def test_the_list_reuses_the_cached_index_and_reports_an_unreadable_one(tmp_path: Path) -> None:
    root = _review_tree(tmp_path)
    coordination = tmp_path / "coordination"
    first = invariants_without_proof(root, coordination_root=coordination)
    second = invariants_without_proof(root, coordination_root=coordination)
    assert first == second and first is not None and first.problem is None
    assert [one.id for one in first.unproven] == [OUTSIDER_INVARIANT, SIBLING_INVARIANT]
    cached = list((coordination / "runtime" / "knowledge-index").glob("*.sqlite"))
    assert len(cached) == 1

    # A cache location inside a Git working tree is refused by the cache; the list reports it.
    refused = invariants_without_proof(root, coordination_root=root / "coordination")
    assert refused is not None and refused.unproven == ()
    assert refused.index_state == "unreadable" and "inside a Git working tree" in str(
        refused.problem
    )
    assert not (root / "coordination").exists()
