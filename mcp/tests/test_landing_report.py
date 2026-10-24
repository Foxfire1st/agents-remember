"""A landing shows what it cuts from a card and what it adds (MIK-R120@v1).

One test per protection: a card over the measure is reported with both
numbers; a card under it is not; the card of a deleted source is not; an
added file outside the three folders is reported with its size. Both reports
refuse nothing and count as nothing actionable. The answer carries the two
counts with the first rows inside its bound and names the file with the full
list. The role texts hold the rules-4/5 sentences in the compiled texts of
every harness.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.memory_quality.answer import quality_result_answer
from agents_remember.application.memory_quality.converted_base import (
    leaf_base_request,
)
from agents_remember.application.role_capsules.compilation import (
    compile_admitted_capsule,
)
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.memory_quality import landing_report
from agents_remember.memory_quality.converted_check import converted_knowledge_check
from agents_remember.memory_quality.curator_checklist import (
    write_curator_checklist,
)
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_answer import TOOL_ANSWER_STATUS_ENTRY_MAX
from agents_remember.worktrees.worktree_contract import load_contract
from agents_remember_test_support.testing.leaf_instruction_wording import delivered_step
from landing_report_test_support import (
    CURATOR_SENTENCE_EQUIVALENTS,
    CURATOR_SENTENCES,
    REVIEWER_SENTENCES,
    SIDECAR,
    _checklist,
    _commit_all,
    _curator_landing_missing,
    _landing_counts,
    _leaf_pair,
    _leaf_port,
    _operation_contract,
    _rename_pair,
    _report,
    _reviewer_landing_roots,
)
from test_worktree_support import git, init_repo


def test_card_over_measure_reports_both_numbers() -> None:
    base_text = "# Card\n\n" + "\n".join(f"line {index}" for index in range(50)) + "\n"
    kept = "# Card\n\n" + "\n".join(f"line {index}" for index in range(50) if index % 5) + "\n"
    report = _report(
        candidate_cards={"onboarding/a.py.md": (kept + "one new line\n").encode()},
        base_cards={"onboarding/a.py.md": base_text.encode()},
        base_problem=None,
        code_has_file=None,
    )
    assert report["ok"] is True
    assert report["findingCount"] == 0
    assert report["findings"] == []
    assert report["cutCardCount"] == 1
    (row,) = report["cutCards"]
    assert row["path"] == "onboarding/a.py.md"
    assert (row["linesRemoved"], row["linesAdded"]) == (10, 1)
    assert report["reportOnlyFindings"] == report["cutCards"]


def test_card_cut_counts_removed_against_added() -> None:
    base_text = "\n".join(f"line {index}" for index in range(60))
    kept = "\n".join(f"line {index}" for index in range(60) if index % 2)
    candidate_text = f"{kept}\nadded one\nadded two\n"
    removed, added = landing_report.count_line_delta(base_text, candidate_text)
    assert (removed, added) == (30, 2)
    report = _report(
        candidate_cards={"onboarding/a.py.md": candidate_text.encode()},
        base_cards={"onboarding/a.py.md": base_text.encode()},
        base_problem=None,
        code_has_file=None,
    )
    assert report["cutCardCount"] == 1
    (row,) = report["cutCards"]
    assert (row["linesRemoved"], row["linesAdded"]) == (30, 2)


def test_card_under_measure_not_reported() -> None:
    base_text = "\n".join(f"line {index}" for index in range(30))
    kept = "\n".join(f"line {index}" for index in range(30) if index >= 7)
    report = _report(
        candidate_cards={"onboarding/a.py.md": (kept + "\n").encode()},
        base_cards={"onboarding/a.py.md": (base_text + "\n").encode()},
        base_problem=None,
        code_has_file=None,
    )
    assert report["cutCardCount"] == 0
    assert report["cutCards"] == []
    assert report["reportOnlyFindings"] == []
    # Exactly twice what is added is not more than twice: not a cut.
    boundary = _report(
        candidate_cards={"onboarding/b.py.md": b"kept\nadded\nadded\n"},
        base_cards={"onboarding/b.py.md": b"kept\nr1\nr2\nr3\nr4\n"},
        base_problem=None,
        code_has_file=None,
    )
    assert boundary["cutCardCount"] == 0


def test_only_proven_deleted_source_card_not_reported() -> None:
    base_text = "\n".join(f"line {index}" for index in range(40))
    candidate = {"onboarding/gone.py.md": b"# Card\n\none line\n"}
    base = {
        "onboarding/gone.py.md": (base_text + "\n").encode(),
        "onboarding/gone.py.json": (SIDECAR % "gone.py").encode(),
    }
    # The base code tree is unknown, so the deletion is unproven: reported (F05).
    report = _report(
        candidate_cards=candidate,
        base_cards=base,
        base_problem=None,
        code_has_file=lambda source: False,
    )
    assert report["cutCardCount"] == 1
    # A source the base code holds and the candidate drops is deleted: exempt.
    deleted_report = _report(
        candidate_cards=candidate,
        base_cards=base,
        base_problem=None,
        code_has_file=lambda source: False,
        base_code_has_file=lambda source: True,
    )
    assert deleted_report["cutCardCount"] == 0
    # A source the code never held is not a deleted source: still reported.
    kept_report = _report(
        candidate_cards=candidate,
        base_cards=base,
        base_problem=None,
        code_has_file=lambda source: False,
        base_code_has_file=lambda source: False,
    )
    assert kept_report["cutCardCount"] == 1


def test_overview_cut_reported_without_source() -> None:
    base_text = "\n".join(f"line {index}" for index in range(40))
    report = _report(
        candidate_cards={"onboarding/route/overview.md": b"# Overview\n\nshort\n"},
        base_cards={"onboarding/route/overview.md": (base_text + "\n").encode()},
        base_problem=None,
        code_has_file=lambda source: False,
    )
    assert report["cutCardCount"] == 1


def test_renamed_card_compared_across_move() -> None:
    base_text = "\n".join(f"line {index}" for index in range(40))
    kept = "\n".join(f"line {index}" for index in range(40) if index % 2)
    report = _report(
        candidate_cards={"onboarding/new.py.md": (kept + "\n").encode()},
        base_cards={"onboarding/old.py.md": (base_text + "\n").encode()},
        base_problem=None,
        code_has_file=lambda source: True,
        renames={"onboarding/new.py.md": "onboarding/old.py.md"},
    )
    assert report["cutCardCount"] == 1
    # Unnamed with a known candidate code tree: the old card's deletion is
    # unproven, so the full removal is reported, not assumed deleted (F05).
    visible = _report(
        candidate_cards={"onboarding/new.py.md": (kept + "\n").encode()},
        base_cards={"onboarding/old.py.md": (base_text + "\n").encode()},
        base_problem=None,
        code_has_file=lambda source: source != "old.py",
    )
    assert visible["cutCardCount"] == 1
    (row,) = visible["cutCards"]
    assert (row["linesRemoved"], row["linesAdded"]) == (40, 0)
    # Proven deleted (the base code holds the source): still exempt.
    silent = _report(
        candidate_cards={"onboarding/new.py.md": (kept + "\n").encode()},
        base_cards={"onboarding/old.py.md": (base_text + "\n").encode()},
        base_problem=None,
        code_has_file=lambda source: source != "old.py",
        base_code_has_file=lambda source: True,
    )
    assert silent["cutCardCount"] == 0
    assert silent["reportOnlyFindings"] == []
    # No code tree on either side: the vanished card counts as deleted.
    unplaced = _report(
        candidate_cards={"onboarding/new.py.md": (kept + "\n").encode()},
        base_cards={"onboarding/old.py.md": (base_text + "\n").encode()},
        base_problem=None,
        code_has_file=None,
    )
    assert unplaced["cutCardCount"] == 0


def test_added_file_reported_with_size_and_sum() -> None:
    candidate_listing = {
        "notes/evidence.bin": 100,
        "knowledge.sqlite": 8,
        "onboarding/a.py.md": 10,
        "knowledge/records/r.json": 5,
        "system/settings.md": 5,
    }
    rows, total = landing_report.added_file_rows(candidate_listing, set())
    assert [row["path"] for row in rows] == ["knowledge.sqlite", "notes/evidence.bin"]
    assert total == 108
    report = _report(
        candidate_cards={},
        base_cards={},
        base_problem=None,
        code_has_file=None,
        candidate_listing=candidate_listing,
        base_paths=set(),
    )
    assert report["addedFileCount"] == 2
    assert report["addedFileBytes"] == 108
    assert report["ok"] is True
    assert report["findingCount"] == 0


def test_unreadable_base_names_it_and_reports_none() -> None:
    report = _report(
        candidate_cards={},
        base_cards=None,
        base_problem="HEAD (abc123) cannot be read",
        code_has_file=None,
    )
    assert report["cutCardCount"] == 0
    assert report["cutCards"] == []
    assert report["addedFileCount"] == 0
    (note,) = report["reportOnlyFindings"]
    assert "HEAD (abc123) cannot be read" in note["message"]
    assert "no cut is reported as none" in note["message"]
    assert report["ok"] is True
    assert report["findingCount"] == 0


def test_checklist_renders_landing_rows_without_refusing(tmp_path: Path) -> None:
    report = _report(
        candidate_cards={"onboarding/a.py.md": b"short\n"},
        base_cards={"onboarding/a.py.md": ("\n".join(f"l{index}" for index in range(30))).encode()},
        base_problem=None,
        code_has_file=None,
        candidate_listing={"notes/working.txt": 12},
        base_paths=set(),
    )
    assert report["cutCardCount"] == 1
    assert report["addedFileCount"] == 1
    summary = write_curator_checklist(
        _checklist(tmp_path / "curator-memory-quality.md", report["reportOnlyFindings"])
    )
    assert summary["checklistStatus"] == "ready-for-closeout"
    assert summary["curatorActionableCount"] == 0
    rendered = (tmp_path / "curator-memory-quality.md").read_text(encoding="utf-8")
    assert "onboarding/a.py.md" in rendered
    assert "notes/working.txt" in rendered


def test_converted_check_carries_landing_counts(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    code = tmp_path / "code"
    (memory / "knowledge").mkdir(parents=True)
    (memory / "onboarding").mkdir(parents=True)
    code.mkdir(parents=True)
    (memory / "knowledge" / "layout.json").write_text(
        '{"conversion": "1", "schema": "ar-memory-layout/v2"}\n', encoding="utf-8"
    )
    base_card = "\n".join(f"line {index}" for index in range(40))
    (memory / "onboarding" / "a.py.md").write_text(f"# Card\n\n{base_card}\n")
    (memory / "onboarding" / "a.py.json").write_text(SIDECAR % "a.py")
    (code / "a.py").write_text("VALUE = 1\n")
    init_repo(memory)
    _commit_all(memory, "base")
    (memory / "onboarding" / "a.py.md").write_text("# Card\n\nshort\n")
    (memory / "notes").mkdir(exist_ok=True)
    (memory / "notes" / "working.txt").write_text("scratch\n")
    result = converted_knowledge_check(memory, code, base=None)
    assert result["cutCardCount"] == 1
    assert result["addedFileCount"] == 1
    assert result["addedFileBytes"] == len("scratch\n")
    assert result["findingCount"] == len(result["findings"])
    landing_rows = [
        row
        for row in result["reportOnlyFindings"]
        if str(row.get("check", "")).startswith("knowledge.landing.")
    ]
    assert len(landing_rows) == 2
    assert result["ok"] == (result["findingCount"] == 0)


def test_answer_carries_two_counts_first_rows_within_bound() -> None:
    cuts = [
        {
            "check": "knowledge.landing.cut_cards",
            "path": f"onboarding/card-{index:03d}.py.md",
            "code": "landing-cut-card",
            "linesRemoved": 40,
            "linesAdded": 2,
            "message": "card cut",
        }
        for index in range(200)
    ]
    whole: dict[str, Any] = {
        "ok": True,
        "checks": {
            "knowledge.converted": {
                "ok": True,
                "check": "knowledge.converted",
                "findingCount": 0,
                "findings": [],
                "cutCardCount": 200,
                "cutCards": cuts,
                "addedFileCount": 0,
                "addedFileBytes": 0,
                "addedFiles": [],
            }
        },
        "findingCount": 0,
        "findings": [],
    }
    answer = quality_result_answer(whole)
    assert answer["cutCardCount"] == 200
    assert answer["addedFileCount"] == 0
    assert len(answer["cutCards"]) == TOOL_ANSWER_STATUS_ENTRY_MAX
    assert "addedFiles" not in answer
    assert set(answer["cutCards"][0]) == {"path", "linesRemoved", "linesAdded"}
    assert count_response_tokens(answer) <= 1500


def test_role_texts_hold_landing_sentences_everywhere() -> None:
    roots = {REPO_ROOT / "skills"}
    roots.update(
        path.parent.parent.parent
        for path in REPO_ROOT.glob("**/skills/l-01-agent-lifecycles/roles/curator.md")
        if ".venv" not in path.parts
    )
    # MIK-R196 rule 7: the canonical tree and its packaged serving copy are the only two
    # deliveries of the corpus. The eight harness mirrors are gone, so the equality this
    # test can still claim is that both surviving copies carry the sentences.
    assert len(roots) == 2
    for root in sorted(roots):
        curator = (root / "l-01-agent-lifecycles" / "roles" / "curator.md").read_text()
        assert _curator_landing_missing(curator) == [], root
        reading = " ".join(curator.replace("**", "").split())
        for sentence in CURATOR_SENTENCES:
            mutant = reading
            for variant in (sentence, CURATOR_SENTENCE_EQUIVALENTS.get(sentence, sentence)):
                mutant = mutant.replace(" ".join(variant.split()), "")
            assert mutant != reading
            assert sentence in _curator_landing_missing(mutant)

    lifecycle = REPO_ROOT / "skills" / "l-01-agent-lifecycles"
    manifest_bytes = (lifecycle / "composition-manifest.json").read_bytes()
    parsed = parse_composition_manifest(manifest_bytes)
    for role, operation, sentences in (("curator", "curation", CURATOR_SENTENCES),):
        seat = CapsuleRoleSeat(role=role, altitude=parsed.roles[role].altitude)  # type: ignore[arg-type]
        request = routed_admission_for(
            lifecycle,
            "composition-manifest.json",
            manifest_bytes,
            CapsuleSeatAddress(role=role, operation=operation),  # type: ignore[arg-type]
        )
        binding = CapsuleBinding(
            operation=operation,  # type: ignore[arg-type]
            admitted=CapsuleAdmittedFacts(
                task_reference="agents-remember/tasks/demo/task.json",
                task_document_digest=compute_content_digest(b'{"id": "demo"}'),
                seat=seat,
                repository_id="agents-remember",
                work_branch="fixture",
                tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
            ),
        )
        outcome = compile_admitted_capsule(binding, request)
        assert outcome.ok, outcome.render_explanation()
        assert outcome.result is not None
        text = outcome.result.render_instructions()
        assert sentences == CURATOR_SENTENCES
        assert _curator_landing_missing(text) == [], f"compiled {role}/{operation}"


@pytest.mark.parametrize(
    "root", _reviewer_landing_roots(), ids=lambda root: root.relative_to(REPO_ROOT).as_posix()
)
def test_reviewer_landing_sentences_are_delivered_before_memory_review(
    root: Path, tmp_path: Path
) -> None:
    """Both original findings live in the exact declared/delivered step-8 file."""
    instructions, text = delivered_step(
        root / "l-01-agent-lifecycles",
        ("reviewer", "review"),
        "reference/reviewer-memory-d301.md",
        "when you reach step 8 (memory review), not before",
        tmp_path / "review.md",
    )
    assert "Read [memory checks]" in instructions
    for sentence in REVIEWER_SENTENCES:
        flat_sentence = " ".join(sentence.split())
        reading = " ".join(text.split())
        assert flat_sentence in reading, f"{sentence!r} missing in delivered {root}"
        mutant = reading.replace(flat_sentence, "")
        assert mutant != reading
        assert flat_sentence not in mutant


def test_leaf_base_reports_committed_and_keeps_older_base_out(tmp_path: Path) -> None:
    """F01: the check still reports both after a mid-leaf memory commit."""

    pair = _leaf_pair(tmp_path)
    memory = pair["memory"]
    (memory / "onboarding" / "a.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
    (memory / "notes" / "working.txt").write_text("scratch\n", encoding="utf-8")
    before = converted_knowledge_check(memory, pair["code"], landing_base=_leaf_port(pair))
    assert _landing_counts(before) == (1, 1)
    _commit_all(memory, "leaf progress")
    after = converted_knowledge_check(memory, pair["code"], landing_base=_leaf_port(pair))
    assert _landing_counts(after) == (1, 1)
    assert [row["path"] for row in after["cutCards"]] == ["onboarding/a.py.md"]
    assert [row["path"] for row in after["addedFiles"]] == ["notes/working.txt"]
    # The run's comparison base alone loses both once committed.
    head_only = converted_knowledge_check(memory, pair["code"])
    assert _landing_counts(head_only) == (0, 0)


def test_leaf_base_update_excludes_older_base_files(tmp_path: Path) -> None:
    """F01: after a base update the census excludes files the new base holds."""

    pair = _leaf_pair(tmp_path)
    memory = pair["memory"]
    (memory / "onboarding" / "a.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
    (memory / "notes" / "working.txt").write_text("scratch\n", encoding="utf-8")
    _commit_all(memory, "leaf progress")
    new_base = git(memory, "rev-parse", "HEAD").strip()
    (memory / "notes" / "later.txt").write_text("more\n", encoding="utf-8")
    result = converted_knowledge_check(
        memory, pair["code"], landing_base=_leaf_port(pair, memory_base=new_base)
    )
    assert _landing_counts(result) == (0, 1)
    assert [row["path"] for row in result["addedFiles"]] == ["notes/later.txt"]


def test_named_rename_cut_reported_across_move(tmp_path: Path) -> None:
    """F02: the R-name the code change records compares the moved card."""

    pair = _rename_pair(tmp_path)
    result = converted_knowledge_check(pair["memory"], pair["code"], landing_base=_leaf_port(pair))
    assert result["cutCardCount"] == 1
    (row,) = result["cutCards"]
    assert row["path"] == "onboarding/new.py.md"
    assert "onboarding/old.py.md" in row["message"]


def test_duplicate_blob_copies_name_no_rename(tmp_path: Path) -> None:
    """F02: unchanged duplicate-blob files are copies, never renames."""

    pair = _leaf_pair(tmp_path)
    code = pair["code"]
    (code / "a.py").write_text("SAME = 1\n", encoding="utf-8")
    (code / "b.py").write_text("SAME = 1\n", encoding="utf-8")
    body = "# Card\n\n" + "".join(f"line {index}\n" for index in range(40))
    memory = pair["memory"]
    (memory / "onboarding" / "a.py.md").write_text(body, encoding="utf-8")
    (memory / "onboarding" / "b.py.md").write_text(body, encoding="utf-8")
    (memory / "onboarding" / "b.py.json").write_text(SIDECAR % "b.py", encoding="utf-8")
    _commit_all(code, "copies")
    _commit_all(memory, "copies")
    pair["code_base"] = git(code, "rev-parse", "HEAD").strip()
    pair["memory_base"] = git(memory, "rev-parse", "HEAD").strip()
    blobs = {
        "a.py": git(code, "rev-parse", f"{pair['code_base']}:a.py").strip(),
        "b.py": git(code, "rev-parse", f"{pair['code_base']}:b.py").strip(),
    }
    assert blobs["a.py"] == blobs["b.py"]
    assert (
        landing_report.code_renames(
            blobs,
            dict(blobs),
            {"onboarding/a.py.md", "onboarding/b.py.md"},
            {"onboarding/a.py.md", "onboarding/b.py.md"},
        )
        == {}
    )
    result = converted_knowledge_check(memory, code, landing_base=_leaf_port(pair))
    assert result["cutCardCount"] == 0


def test_linked_worktree_pointer_is_not_an_addition(tmp_path: Path) -> None:
    """F03: a clean linked worktree reports no added file and no cut."""

    pair = _leaf_pair(tmp_path)
    memory = pair["memory"]
    linked = tmp_path / "linked"
    git(
        memory,
        "worktree",
        "add",
        "--detach",
        str(linked),
        pair["memory_base"],
    )
    try:
        assert (linked / ".git").is_file()
        assert ".git" not in landing_report.walk_candidate_listing(linked)
        result = converted_knowledge_check(linked, pair["code"], landing_base=_leaf_port(pair))
        assert _landing_counts(result) == (0, 0)
        assert result["reportOnlyFindings"] == []
    finally:
        git(memory, "worktree", "remove", "--force", str(linked))


def test_removed_cards_answer_the_same_measure() -> None:
    """F04: full removals report at eight lines, never under; deleted stays out."""

    base = {
        "onboarding/small.py.md": (b"# Card\n\none\ntwo\nthree\n"),
        "onboarding/small.py.json": (SIDECAR % "small.py").encode(),
        "onboarding/medium.py.md": (
            "# Card\n\n" + "".join(f"line {index}\n" for index in range(5))
        ).encode(),
        "onboarding/medium.py.json": (SIDECAR % "medium.py").encode(),
        "onboarding/big.py.md": (
            "# Card\n\n" + "".join(f"line {index}\n" for index in range(6))
        ).encode(),
        "onboarding/big.py.json": (SIDECAR % "big.py").encode(),
        "onboarding/route/overview.md": (
            "# Overview\n\n" + "".join(f"line {index}\n" for index in range(6))
        ).encode(),
        "onboarding/gone.py.md": (
            "# Card\n\n" + "".join(f"line {index}\n" for index in range(40))
        ).encode(),
        "onboarding/gone.py.json": (SIDECAR % "gone.py").encode(),
        "onboarding/never.py.md": (
            "# Card\n\n" + "".join(f"line {index}\n" for index in range(40))
        ).encode(),
        "onboarding/never.py.json": (SIDECAR % "never.py").encode(),
    }
    present = {"small.py", "medium.py", "big.py"}
    base_held = {"small.py", "medium.py", "big.py", "gone.py"}
    report = landing_report.check_landing_report(
        landing_report.LandingReportInputs(
            candidate_cards={},
            base_cards=base,
            base_problem=None,
            code_has_file=present.__contains__,
            base_code_has_file=base_held.__contains__,
        )
    )
    assert report["cutCardCount"] == 3
    assert [row["path"] for row in report["cutCards"]] == [
        "onboarding/big.py.md",
        "onboarding/never.py.md",
        "onboarding/route/overview.md",
    ]
    (big,) = [row for row in report["cutCards"] if row["path"] == "onboarding/big.py.md"]
    assert (big["linesRemoved"], big["linesAdded"]) == (8, 0)


def test_leaf_run_proves_deletion_with_both_code_trees(tmp_path: Path) -> None:
    """F05: the ordinary leaf check exempts only the actually deleted source."""

    pair = _leaf_pair(tmp_path)
    memory = pair["memory"]
    code = pair["code"]
    (memory / "onboarding" / "ghost.py.md").write_text(
        "# Card\n\n" + "".join(f"line {index}\n" for index in range(40)), encoding="utf-8"
    )
    (memory / "onboarding" / "ghost.py.json").write_text(SIDECAR % "ghost.py", encoding="utf-8")
    (memory / "onboarding" / "gone.py.md").write_text(
        "# Card\n\n" + "".join(f"line {index}\n" for index in range(40)), encoding="utf-8"
    )
    (memory / "onboarding" / "gone.py.json").write_text(SIDECAR % "gone.py", encoding="utf-8")
    (code / "gone.py").write_text("GONE = 1\n", encoding="utf-8")
    _commit_all(code, "add gone source")
    _commit_all(memory, "add ghost and gone cards")
    pair["code_base"] = git(code, "rev-parse", "HEAD").strip()
    pair["memory_base"] = git(memory, "rev-parse", "HEAD").strip()
    (memory / "onboarding" / "a.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
    (memory / "onboarding" / "ghost.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
    (code / "gone.py").unlink()
    (memory / "onboarding" / "gone.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
    result = converted_knowledge_check(memory, code, landing_base=_leaf_port(pair))
    assert result["cutCardCount"] == 2
    assert sorted(row["path"] for row in result["cutCards"]) == [
        "onboarding/a.py.md",
        "onboarding/ghost.py.md",
    ]


def test_leaf_base_request_reads_contract_and_none_without_base(tmp_path: Path) -> None:
    """The contract builder binds the admitted base, else nothing to bind."""

    pair = _leaf_pair(tmp_path)
    assert leaf_base_request(None, tmp_path) is None
    contract = SimpleNamespace(
        memory_base_commit="",
        memory_repo_path=None,
        memory_worktree=None,
        code_worktree=None,
        code_repo_path=None,
        code_base_commit="",
    )
    assert leaf_base_request(contract, tmp_path) is None  # type: ignore[arg-type]
    root = tmp_path / "contract"
    root.mkdir()
    contract_path = _operation_contract(root, pair)
    request = leaf_base_request(load_contract(contract_path), root / "coordination")
    assert request is not None
    assert request.base_commit == pair["memory_base"]
    assert request.memory_repository == pair["memory"]
    assert request.code_repository == pair["code"]
    assert request.code_base_commit == pair["code_base"]


def test_symlinks_reported_without_following_targets(tmp_path: Path) -> None:
    """F08: dangling and live links are additions at their own lstat size."""

    pair = _leaf_pair(tmp_path)
    memory = pair["memory"]
    live_target = memory / "notes" / "kept.txt"
    live = memory / "notes" / "live-link.txt"
    dangling = memory / "notes" / "dangling-link.txt"
    try:
        live.symlink_to(live_target.name)
    except OSError:
        pytest.skip("the fixture cannot create symlinks here")
    dangling.symlink_to("no-such-target.txt")
    listing = landing_report.walk_candidate_listing(memory)
    assert listing["notes/live-link.txt"] == len(live_target.name)
    assert listing["notes/dangling-link.txt"] == len("no-such-target.txt")
    result = converted_knowledge_check(memory, pair["code"], landing_base=_leaf_port(pair))
    assert result["addedFileCount"] == 2
    assert sorted(row["path"] for row in result["addedFiles"]) == [
        "notes/dangling-link.txt",
        "notes/live-link.txt",
    ]
    _commit_all(memory, "commit the links")
    pair["memory_base"] = git(memory, "rev-parse", "HEAD").strip()
    stored = landing_report.git_candidate_listing(memory, pair["memory_base"])
    assert stored["notes/live-link.txt"] == len(live_target.name)
    assert stored["notes/dangling-link.txt"] == len("no-such-target.txt")


def test_line_delta_work_is_linear_in_card_size() -> None:
    """F09: the multiset comparison hashes each line once at two sizes."""

    class CountedStr(str):
        hashes = 0

        def __hash__(self) -> int:  # type: ignore[override]
            type(self).hashes += 1
            return super().__hash__()

    def counted_delta(total: int, kept: int) -> tuple[tuple[int, int], int]:
        CountedStr.hashes = 0
        base = [CountedStr("same line") for _ in range(total - 8)]
        base += [CountedStr(f"gone {index}") for index in range(8)]
        candidate = [CountedStr("same line") for _ in range(kept)]
        delta = landing_report.multiset_line_delta(base, candidate)
        return delta, CountedStr.hashes

    small, small_ops = counted_delta(1000, 992)
    large, large_ops = counted_delta(2000, 1992)
    assert small == (8, 0)
    assert large == (8, 0)
    assert landing_report.is_cut_card(*small)
    assert large_ops < small_ops * 2.5
