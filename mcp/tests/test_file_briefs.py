"""The brief of a file: its parts and order, the folder it lies in, and the short answer's bound.

Which files get a brief, its parts and order, the folder's writings and the short answer's fit,
on a converted fixture memory and on hand-built briefs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from agents_remember.application.file_briefs.facts import BriefSource, CodeSide
from agents_remember.application.file_briefs.folder import (
    BRIEFS,
    INDEX_FILE,
    TASK_FOLDER,
    TREE_FILE,
    BriefFolder,
    WriteReport,
    brief_location,
    tree_named,
)
from agents_remember.application.file_briefs.model import (
    BriefCounts,
    BriefHead,
    FileBrief,
    RuleBlock,
    RuleHead,
    RuleTitle,
    Standing,
)
from agents_remember.application.file_briefs.ordering import (
    RulePlaces,
    WeaknessSite,
    place_weaknesses,
)
from agents_remember.application.file_briefs.render_brief import NONE_LINE, brief_markdown
from agents_remember.application.file_briefs.short_answer import (
    LEFT_OUT,
    RULE_LEFT_OUT,
    BriefLocation,
    Candidate,
    counts_only,
    fit,
    whole,
)
from agents_remember.application.file_briefs.titles import rule_title
from agents_remember.application.file_briefs.triggers import (
    refresh_written_memory_folder,
    source_paths_of,
)
from agents_remember.application.memory_tools import memory_init_tool
from agents_remember.application.published_intent import select_knowledge_dataset
from agents_remember.application.read_files_briefs import BatchBriefs
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.kernel.memory_ignore import BRIEFS_IGNORE_LINE, ignore_briefs
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory.knowledge_index import KnowledgeIndexCache
from agents_remember.models.knowledge_files.documents import file_sidecar_path, record_path
from agents_remember.models.read_files import ShortBrief
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import bound_for
from agents_remember.worktrees.knowledge_validation import PairedCode
from knowledge_conversion_test_support import code_repository
from knowledge_index_test_support import (
    DECISION,
    FAMILY,
    REVIEW_INVARIANT,
    REVIEW_PATH,
    SIBLING_INVARIANT,
    SIBLING_PATHS,
    TEST_PATH,
    anchor,
    write_document,
    write_review_tree,
)
from knowledge_review_fact_test_support import history_document, review_fact, stamp, written

RETIRED = "INV-RET1RD"
THIRD = "INV-THRD03"
WEAK_BURDEN = "WKN-BRDN01"
WEAK_SYMBOL = "WKN-SYMB02"
WEAK_LOOSE = "WKN-1005E3"
DOCUMENT = "DPD-D0CS01"


def _weakness(identifier: str, symbol: str, links: list[dict[str, str]] | None = None) -> dict:
    return {
        "schema": "ar-weakness/v1",
        "id": identifier,
        "revision": 1,
        "kind": "debt",
        "statement": f"{identifier} costs a reader time.",
        "anchor": anchor(symbol, path=REVIEW_PATH),
        "seen": {"task": "261112-KNO-L4", "role": "worker"},
        "state": "open",
        "origin": {"task": "261112-KNO"},
        "links": links or [],
    }


def _invariant(identifier: str, statement: str, status: str = "proposed") -> dict:
    return {
        "schema": "ar-invariant/v1",
        "id": identifier,
        "origin": {"task": "261112-KNO"},
        "revision": 1,
        "status": status,
        "statement": statement,
        "applicability": "Every review.",
        "conditions": ["A condition."],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
    }


def _realization(identifier: str, invariant: str, symbol: str) -> dict:
    return {
        "id": identifier,
        "invariant": invariant,
        "anchor": anchor(symbol),
        "role": "enforcement",
        "rationale": f"{symbol} enforces {invariant}.",
    }


@pytest.fixture
def memory(tmp_path: Path) -> Path:
    """The review tree, with a third and a retired rule, three weaknesses and a document on it."""

    root = tmp_path / "memory"
    write_review_tree(root)
    write_document(root, record_path("invariant", THIRD, "third"), _invariant(THIRD, "Third rule."))
    write_document(
        root,
        record_path("invariant", RETIRED, "retired"),
        _invariant(RETIRED, "A retired rule.", status="retired"),
    )
    write_document(
        root,
        file_sidecar_path(REVIEW_PATH),
        {
            "schema": "ar-onboarding-file/v1",
            "path": REVIEW_PATH,
            "references": {},
            "realizes": [
                _realization("RLZ-RVW001", REVIEW_INVARIANT, "loadReview"),
                _realization("RLZ-RVW002", SIBLING_INVARIANT, "familyContext"),
                _realization("RLZ-RVW003", SIBLING_INVARIANT, "loadReview"),
                _realization("RLZ-THRD01", THIRD, "loadReview"),
                _realization("RLZ-RET001", RETIRED, "loadReview"),
            ],
        },
    )
    write_document(
        root,
        record_path("weakness", WEAK_BURDEN, "burden"),
        _weakness(WEAK_BURDEN, "elsewhere", [{"target": THIRD, "relation": "burdens"}]),
    )
    write_document(
        root, record_path("weakness", WEAK_SYMBOL, "symbol"), _weakness(WEAK_SYMBOL, "loadReview")
    )
    write_document(
        root, record_path("weakness", WEAK_LOOSE, "loose"), _weakness(WEAK_LOOSE, "nowhere")
    )
    write_document(
        root,
        record_path("dependent", DOCUMENT, "docs"),
        {
            "schema": "ar-dependent/v1",
            "id": DOCUMENT,
            "revision": 1,
            "document": "docs/review.md",
            "stands_on": [{"kind": "file", "path": REVIEW_PATH, "blob": "a" * 40}],
            "confirmed": {
                "task": "261112-KNO-L4",
                "role": "curator",
                "statement": "The page describes the code.",
                "commit": "b" * 40,
            },
            "origin": {"task": "261112-KNO"},
        },
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _source(memory: Path, cache: Path) -> BriefSource:
    index = KnowledgeIndexCache(cache).for_directory(memory)
    return BriefSource(index=index, memory_root=memory, code=CodeSide(None, None, "unmeasured"))


def test_a_brief_holds_its_parts_in_the_developers_order(memory: Path, tmp_path: Path) -> None:
    source = _source(memory, tmp_path / "cache")
    brief = source.assemble(REVIEW_PATH)
    assert brief is not None
    # Most places first (the sibling rule has two), then by id.
    assert [rule.head.id for rule in brief.rules] == [SIBLING_INVARIANT, REVIEW_INVARIANT, THIRD]
    assert brief.retired_left_out == 1
    text = brief_markdown(brief)
    parts = [
        "## What the file is for",
        "## The file's own rules (3)",
        "## Weaknesses at this file that belong to no rule (1)",
        "## Decisions at this file that belong to no rule (0)",
        "## Documents that stand on this file (1)",
        "## Families (1)",
    ]
    positions = [text.index(part) for part in parts]
    assert positions == sorted(positions)
    assert text.splitlines()[0] == f"<!-- memory tree {brief.head.memory_tree} -->"
    # A part the memory holds nothing for says so in one line; the retired rule is only counted.
    assert f"## Decisions at this file that belong to no rule (0)\n\n{NONE_LINE}" in text
    assert RETIRED not in text and "1 retired rule left out" in text
    # The decision that formed the review rule stands in its block, chosen and rejected.
    assert f"`{DECISION}` (constrains, reconsider_on). Chosen: Local routes" in text
    assert "Rejected: One route — Collapses to the root." in text


def test_a_weakness_stands_by_its_burden_link_its_symbol_or_alone(
    memory: Path, tmp_path: Path
) -> None:
    brief = _source(memory, tmp_path / "cache").assemble(REVIEW_PATH)
    assert brief is not None
    by_rule = {rule.head.id: rule for rule in brief.rules}
    assert [one.id for one in by_rule[THIRD].weaknesses] == [WEAK_BURDEN]
    # loadReview realizes all three rules: the first in the brief's order holds the weakness,
    # the later ones name it by id.
    assert [one.id for one in by_rule[SIBLING_INVARIANT].weaknesses] == [WEAK_SYMBOL]
    assert by_rule[REVIEW_INVARIANT].weaknesses_named == (WEAK_SYMBOL,)
    assert [one.id for one in brief.loose_weaknesses] == [WEAK_LOOSE]


def test_brief_and_short_answer_use_the_same_two_records_standing(
    memory: Path, tmp_path: Path
) -> None:
    leaf = "261112-KNO-L3"
    path = next((memory / "knowledge/invariants").glob(f"{REVIEW_INVARIANT}-*.json"))
    reviewed = json.loads(path.read_text(encoding="utf-8"))
    reviewed.pop("status")
    reviewed.update(revision=2, writer=stamp(leaf))
    write_document(memory, path.relative_to(memory).as_posix(), reviewed)
    history, fact = history_document(
        leaf, written_rows=(written(REVIEW_INVARIANT, 2, leaf),), review=review_fact(leaf)
    )
    write_document(memory, history, fact)

    ignore_briefs(memory)
    source = _source(memory, tmp_path / "cache")
    BriefFolder(memory).write_all(source)
    batch = BatchBriefs(memory, source, str)
    row: dict[str, object] = {"path": REVIEW_PATH}
    batch.answer_row(row, REVIEW_PATH)
    batch.fit_into({"files": [row]})
    answer = ShortBrief.model_validate(row["brief"])
    assert answer.rules is not None
    by_id = {rule.id: rule for rule in answer.rules}
    document = brief_location(memory, REVIEW_PATH).read_text(encoding="utf-8")
    for identifier, state, writer in (
        (REVIEW_INVARIANT, "reviewed", "curator/fixture-model"),
        (SIBLING_INVARIANT, "proposed", "unknown"),
    ):
        assert (by_id[identifier].state, by_id[identifier].writer) == (state, writer)
        section = document.split(f"· `{identifier}`\n", 1)[1].split("\n### ", 1)[0]
        assert f"State {state} · writer: {writer} ·" in section


def test_placement_policy_keeps_a_burden_link_to_another_files_rule_out() -> None:
    head = RuleHead("INV-A", RuleTitle("a", True), Standing("proposed", "unknown"), 0, 1, 1, True)
    placed = place_weaknesses(
        [RulePlaces(head, ("f",))], [WeaknessSite("WKN-1", "g", ("INV-OTHER",))]
    )
    assert placed.loose == ("WKN-1",) and placed.standing["INV-A"] == ()


def test_which_files_get_a_brief(memory: Path, tmp_path: Path) -> None:
    source = _source(memory, tmp_path / "cache")
    paths = set(source.brief_paths())
    assert {REVIEW_PATH, TEST_PATH, *SIBLING_PATHS} <= paths
    # A test file's own rules are the rules it proves.
    test_brief = source.assemble(TEST_PATH)
    assert test_brief is not None and [r.head.id for r in test_brief.rules] == [REVIEW_INVARIANT]
    assert source.assemble("README.md") is None


def test_an_untitled_rule_is_named_by_its_first_six_words(memory: Path, tmp_path: Path) -> None:
    named = rule_title({"statement": "One two three four five six seven eight."})
    assert named == RuleTitle("One two three four five six …", given=False)
    assert rule_title({"title": "Briefs lie beside cards", "statement": "x"}).given
    # The heading of an untitled rule is those words alone; the short answer says it is untitled.
    brief = _source(memory, tmp_path / "cache").assemble(REVIEW_PATH)
    assert brief is not None
    number, rule = next((n, r) for n, r in enumerate(brief.rules, 1) if not r.head.title.given)
    heading = f"### {number}. {rule.head.title.text} · `{rule.head.id}`"
    assert heading in brief_markdown(brief).splitlines()
    location = BriefLocation(f"briefs/{REVIEW_PATH}.md", True)
    (listed,) = [one for one in whole(brief, location)["rules"] if one["id"] == rule.head.id]
    assert listed["titleRecorded"] is False


def test_a_full_writing_replaces_the_folder_and_keeps_the_task_folder(
    memory: Path, tmp_path: Path
) -> None:
    folder = BriefFolder(memory)
    task_file = memory / BRIEFS / TASK_FOLDER / "review-start.md"
    task_file.parent.mkdir(parents=True)
    task_file.write_text("kept\n", encoding="utf-8")
    stale = memory / BRIEFS / "gone" / "renamed.py.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("old\n", encoding="utf-8")
    ignore_briefs(memory)
    source = _source(memory, tmp_path / "cache")
    report = folder.write_all(source)
    assert report.removed == 1 and not stale.exists() and not stale.parent.exists()
    assert task_file.read_text(encoding="utf-8") == "kept\n"
    assert not (memory / BRIEFS / TASK_FOLDER / INDEX_FILE).exists()
    assert (memory / BRIEFS / TREE_FILE).read_text(encoding="utf-8").strip() == source.memory_tree
    review = brief_location(memory, REVIEW_PATH)
    assert tree_named(review) == source.memory_tree
    index = (review.parent / INDEX_FILE).read_text(encoding="utf-8")
    assert "`review.ts` — 3 rules, 3 weaknesses, 1 document —" in index
    # A second writing of the same tree changes no file.
    assert folder.write_all(_source(memory, tmp_path / "cache")).written == 0


def test_a_folder_gets_its_briefs_whoever_built_the_shared_index(
    memory: Path, tmp_path: Path
) -> None:
    """An explicit refresh writes each folder's briefs even when it shares a cached index."""

    code = code_repository(tmp_path / "code")
    coordination = tmp_path / "coordination"
    coordination.mkdir()
    ignore_briefs(memory)
    sibling = tmp_path / "sibling-memory"
    shutil.copytree(memory, sibling)

    selected = select_knowledge_dataset(memory, coordination_root=coordination)
    assert selected.memory_tree is not None
    key = selected.memory_tree.tree_key
    assert not (memory / BRIEFS / TREE_FILE).exists()

    for root in (memory, sibling):
        report = refresh_written_memory_folder(root, PairedCode(code.root, code.head), coordination)
        assert isinstance(report, WriteReport) and report.briefs > 0
        assert (root / BRIEFS / TREE_FILE).read_text(encoding="utf-8").strip() == key
        assert tree_named(brief_location(root, REVIEW_PATH)) == key
        assert tree_named(brief_location(root, TEST_PATH)) == key


def test_two_writings_of_one_folder_never_interleave(memory: Path, tmp_path: Path) -> None:
    errors: list[BaseException] = []

    def write() -> None:
        try:
            BriefFolder(memory).write_all(_source(memory, tmp_path / "cache"))
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=write) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    leftovers = [one for one in (memory / BRIEFS).rglob("*.tmp")]
    assert leftovers == []
    assert tree_named(brief_location(memory, REVIEW_PATH)) is not None


def test_the_ignore_line_is_added_once(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    (root / ".gitignore").write_text("/memory.md", encoding="utf-8")
    assert ignore_briefs(root) is True
    assert ignore_briefs(root) is False
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "/memory.md" and lines.count(BRIEFS_IGNORE_LINE) == 1


def test_a_new_memory_ignores_the_briefs_folder_from_its_first_commit(tmp_path: Path) -> None:
    """memory_init writes the ignore line into the memory it creates."""

    code = code_repository(tmp_path / "code").root
    (tmp_path / "coordination").mkdir()
    memory = tmp_path / "coordination" / "memory-repos" / "ar-demo"
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path / "coordination",
        workspace_root=tmp_path,
        transcript_root=tmp_path / "coordination" / "logs" / "mcp",
        repositories={"demo": RepositoryScope(repo_id="demo", path=code, memory_root=memory)},
    )
    memory.mkdir(parents=True)
    (memory / ".gitignore").write_text("/memory.md", encoding="utf-8")
    preview = memory_init_tool(config, repo_id="demo", initial_branch="main", dry_run=True)
    assert preview["ok"] and (memory / ".gitignore").read_bytes() == b"/memory.md"
    assert not (memory / BRIEFS).exists()
    initialized = memory_init_tool(config, repo_id="demo", initial_branch="main")
    assert initialized["ok"], initialized
    assert BRIEFS_IGNORE_LINE in (memory / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert (memory / ".gitignore").read_text(encoding="utf-8").startswith("/memory.md\n")
    selected = select_knowledge_dataset(memory, coordination_root=config.coordination_root)
    assert selected.memory_tree is not None
    assert (memory / BRIEFS / TREE_FILE).read_text().strip() == selected.memory_tree.tree_key
    (memory / "briefs" / "app.py.md").write_text("derived\n", encoding="utf-8")
    ignored = subprocess.run(
        ["git", "-C", str(memory), "check-ignore", "briefs/app.py.md"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ignored.returncode == 0, ignored


def test_changed_memory_files_name_their_source_files(memory: Path, tmp_path: Path) -> None:
    index = KnowledgeIndexCache(tmp_path / "cache").for_directory(memory)
    paths = source_paths_of(
        index,
        [
            file_sidecar_path(REVIEW_PATH),
            "onboarding/dashboard/src/overview.md",
            record_path("weakness", WEAK_LOOSE, "loose"),
            record_path("invariant", SIBLING_INVARIANT, "family-context-source"),
        ],
    )
    assert paths == {REVIEW_PATH, *SIBLING_PATHS}

    family_paths = source_paths_of(
        index, [record_path("family", FAMILY, "comparison-bound-context")]
    )
    assert family_paths == {REVIEW_PATH, TEST_PATH, *SIBLING_PATHS}


# -- the short answer and its bound (rules 7 and 8) ---------------------------------------------


def _rule(identifier: str, words: int, *, moved: bool = False) -> RuleBlock:
    head = RuleHead(
        id=identifier,
        title=RuleTitle(identifier.lower(), True),
        standing=Standing("proposed", "unknown"),
        proofs=0,
        anchors_current=0 if moved else 1,
        anchors_total=1,
        anchors_measured=True,
    )
    return RuleBlock(
        head=head,
        statement=" ".join(["word"] * words),
        applicability=None,
        conditions=(),
        exclusions=(),
        here=(),
        elsewhere=(),
        proofs=(),
        decisions=(),
        weaknesses=(),
        weaknesses_named=(),
        facets=(),
        families=(),
        record_path="",
    )


def _brief(path: str, rules: list[RuleBlock]) -> FileBrief:
    return FileBrief(
        head=BriefHead(
            path, "a" * 40, None, "unmeasured", None, BriefCounts(len(rules), 0, 0, 0, 0)
        ),
        purpose=None,
        rules=tuple(rules),
        loose_weaknesses=(),
        loose_decisions=(),
        documents=(),
        families=(),
        retired_left_out=0,
    )


def _candidates(briefs: list[FileBrief]) -> tuple[dict, list[Candidate]]:
    payload: dict = {"files": []}
    candidates = []
    for brief in briefs:
        row = {"path": brief.head.path}
        payload["files"].append(row)
        location = BriefLocation(f"briefs/{brief.head.path}.md", True)
        candidates.append(Candidate(row, whole(brief, location), counts_only(brief, location)))
    return payload, candidates


def test_one_file_over_the_bound_keeps_its_moved_rule_in_front() -> None:
    rules = [_rule(f"INV-{n:06d}", 120) for n in range(6)] + [_rule("INV-MOVED9", 120, moved=True)]
    payload, candidates = _candidates([_brief("big.py", rules)])
    bound = 700
    fit(candidates, fits=lambda: count_response_tokens(payload) <= bound)
    answer = payload["files"][0]["brief"]
    assert count_response_tokens(payload) <= bound
    assert answer["rules"][0]["id"] == "INV-MOVED9" and answer["rules"][0]["moved"] is True
    assert answer["rules"][0]["rule"].startswith("word")
    assert answer["rules"][-1]["rule"] == RULE_LEFT_OUT and answer["leftOut"] == LEFT_OUT


def test_brief_counts_can_exceed_the_bound_while_the_legacy_rows_fit() -> None:
    briefs = [_brief(f"f{n}.py", [_rule(f"INV-{n:06d}", 60)]) for n in range(20)]
    payload, candidates = _candidates(briefs)
    bound = 400
    assert count_response_tokens(payload) <= bound
    assert fit(candidates, fits=lambda: count_response_tokens(payload) <= bound) is None
    assert count_response_tokens(payload) > bound


def test_overbound_card_rows_keep_every_requested_path_in_the_delivered_answer(
    memory: Path, tmp_path: Path
) -> None:
    batch = BatchBriefs(memory, _source(memory, tmp_path / "cache"), str)
    paths = [REVIEW_PATH, *(f"pkg/{n:02d}/app.py" for n in range(18))]
    rows = [
        {
            "path": path,
            "exists": True,
            "cardStatus": "sidecar-only" if n == 1 else "found",
            "card": str(memory / "onboarding" / f"{path}.md"),
            "actionNeeded": [
                {
                    "id": f"INV-{n:02d}{i:04d}",
                    "kind": "invariant",
                    "state": "stale",
                    "heading": "Check the operation before choosing its deployment route",
                }
                for i in range(4)
            ],
        }
        for n, path in enumerate(paths)
    ]
    rows.append({"path": "missing.py", "exists": False})
    batch.answer_row(rows[0], REVIEW_PATH)
    bound = bound_for("read_ar_files")
    assert count_response_tokens({"files": rows}) > bound
    payload = {
        "ok": True,
        "repoId": "demo",
        "state": "named",
        "memoryRoot": str(memory),
        "files": rows,
    }
    reply = batch.prepare_reply(payload)
    answer = complete_tool_response("read_ar_files", reply.payload, fit_reply=reply.fit_reply)
    assert "cut" not in answer and count_response_tokens(answer) <= bound
    assert [row["path"] for row in answer["files"]] == [*paths, "missing.py"]
    assert answer["files"][0] == {
        "path": REVIEW_PATH,
        "exists": True,
        "cardStatus": "found",
        "brief": {
            "path": f"briefs/{REVIEW_PATH}.md",
            "freshness": "missing",
        },
    }
    assert not (memory / BRIEFS).exists()
    assert answer["files"][1] == {"path": paths[1], "exists": True, "cardStatus": "sidecar-only"}
    assert answer["files"][-1] == {"path": "missing.py", "exists": False, "cardStatus": "missing"}
    assert "under memoryRoot with your own file tool" in answer["briefsLeftOut"]
    assert "next write covering the file or the whole folder" in answer["briefsLeftOut"]
    assert reply.fit_reply is not None
    assert reply.fit_reply({**answer, "files": rows}, bound) == answer


@pytest.mark.parametrize("paths", [4, 20])
def test_several_paths_get_their_texts_in_the_order_asked_until_the_bound(paths: int) -> None:
    briefs = [_brief(f"f{n}.py", [_rule(f"INV-{n:06d}", 60)]) for n in range(paths)]
    payload, candidates = _candidates(briefs)
    bound = 1200
    count_only = fit(candidates, fits=lambda: count_response_tokens(payload) <= bound)
    answers = [row["brief"] for row in payload["files"]]
    assert count_only is not None and (count_only > 0) == (paths == 20)
    whole_ones = [n for n, one in enumerate(answers) if "rules" in one]
    assert whole_ones == list(range(len(whole_ones))) and whole_ones
    for later in answers[len(whole_ones) :]:
        assert later["counts"].startswith("1 rules") and later["path"].startswith("briefs/")
    assert count_response_tokens(payload) <= bound
