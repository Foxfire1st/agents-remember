"""Per-target realization rationale and role, through the real curator writer.

A realization claim stores the author's own explanation of what one cited place does for the
obligation. These cases drive ``ingest_curator_list`` over the sibling's real repository pair and
read the result back through the public invariant view or the stored rows:

* two targets of one entry each store their **own** rationale and role, over an entry-level default;
* a target that states nothing inherits the entry's **explicit default**, and the role and the
  rationale resolve independently;
* a target with **no rationale at either level** refuses its entry by name, before any identity is
  minted or any row is written, and the writer generates no sentence in its place;
* an **exact replay** of per-target rationale writes nothing and leaves the stored rows and the
  allocation journal byte-identical, while a changed rationale under the same key is refused;
* a claim holding the **old generated sentence** stays readable exactly as stored;
* a value that is not a string, a role outside the vocabulary (``"absent"`` included) and an
  over-long rationale each refuse only their own entry, by name, while the sibling entry commits;
* an operation the **base writer committed without any rationale** replays and publishes under this
  writer exactly as it did before, and changed content under its key still conflicts. The base writer
  is the real one: its source is exported from the base commit and run in a child interpreter.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application import knowledge_curator_ingest
from agents_remember.application.curator_realization_authoring import (
    EntryRealization,
    TargetRealization,
)
from agents_remember.application.knowledge_curator_ingest import (
    IngestPublication,
    IngestReport,
    IngestSelection,
    ingest_curator_list,
)
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH
from test_curator_family_authoring import committed_revision, database_of, invariant_view
from test_knowledge_curator_ingest_list import (
    AUTHORIZATION,
    CODE_FILE,
    CODE_OTHER_SYMBOL,
    CODE_SYMBOL,
    SourcePair,
    entry,
    pair,
    run,
    symbol,
    target,
)

__all__ = ["pair"]

pytestmark = pytest.mark.evidence_unit

# The sentence the writer used to generate for a target with no rationale, as stored rows hold it.
GENERATED_BEFORE = f"The statement is realized at {CODE_FILE}."


def placed(symbol_name: str, rationale: str | None = None, role: str | None = None) -> dict:
    """One target at a construct of the fixture's file, with whatever the producer stated for it."""

    one = target(CODE_FILE, locator=symbol(symbol_name), route="pkg")
    if rationale is not None:
        one["rationale"] = rationale
    if role is not None:
        one["role"] = role
    return one


def realized(report: IngestReport, entry_id: str) -> dict[str, tuple[str | None, str | None]]:
    """Each stored realization of one committed entry, keyed by its construct: (role, rationale)."""

    rows = invariant_view(
        database_of(Path(report.candidate_directory)),
        report.repository_id,
        committed_revision(report, entry_id),
    ).rows
    return {
        str(getattr(row.locator, "qualified_name", None)): (row.role, row.statement)
        for row in rows
        if row.fact_kind == "realization"
    }


def claim_rows(report: IngestReport) -> list[tuple[Any, ...]]:
    """Every stored realization claim row, exactly as the table holds it."""

    return claim_rows_at(Path(report.candidate_directory))


def claim_rows_at(candidate: Path) -> list[tuple[Any, ...]]:
    """Every realization claim row one candidate directory's dataset stores."""

    database = database_of(candidate)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return list(connection.execute("SELECT * FROM realization_claim ORDER BY claim_id"))
    finally:
        connection.close()


def journal_keys(report: IngestReport) -> list[str]:
    """The entry ids the allocation journal holds an identity for."""

    records = json.loads(Path(report.allocation_journal).read_text(encoding="utf-8"))
    return sorted(str(one["retryKey"]).rsplit("|", 1)[1] for one in records)


def test_each_target_of_one_entry_stores_its_own_authored_rationale_and_role(
    pair: SourcePair, tmp_path: Path
) -> None:
    """Two constructs cited by one entry keep two explanations; the entry's default loses to both.

    A target that writes ``unclassified`` itself keeps it rather than being handed the entry's role:
    the producer said nobody assessed this place, and that is not the entry's word.
    """

    listed = [
        {
            **entry(
                "E-two",
                rationale="An entry-level default that every target here overrides.",
                targets=[
                    placed(CODE_SYMBOL, "It computes the remaining budget.", "primary-authority"),
                    placed(CODE_OTHER_SYMBOL, "It only reports the budget.", "unclassified"),
                ],
            ),
            "realization_role": "enforcement",
        }
    ]

    report = run(pair, tmp_path, listed)

    assert [one.entry_id for one in report.committed] == ["E-two"], report.refused
    assert realized(report, "E-two") == {
        CODE_SYMBOL: ("primary-authority", "It computes the remaining budget."),
        CODE_OTHER_SYMBOL: ("unclassified", "It only reports the budget."),
    }


def test_a_target_that_states_nothing_inherits_the_entry_level_default(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The entry's explicit default fills each half a target left unstated, independently."""

    default = "Both constructs carry the budget this obligation bounds."
    listed = [
        {
            **entry(
                "E-default",
                rationale=default,
                targets=[
                    placed(CODE_SYMBOL, "Its own explanation, and the entry's role."),
                    placed(CODE_OTHER_SYMBOL),
                ],
            ),
            "realization_role": "support",
        }
    ]

    report = run(pair, tmp_path, listed)

    assert [one.entry_id for one in report.committed] == ["E-default"], report.refused
    assert realized(report, "E-default") == {
        CODE_SYMBOL: ("support", "Its own explanation, and the entry's role."),
        CODE_OTHER_SYMBOL: ("support", default),
    }


def test_a_target_with_no_rationale_refuses_its_entry_by_name_and_nothing_is_generated(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The refusal names the entry and each unexplained target; nothing is minted or written for it.

    A blank rationale is no rationale, at either level. The entry beside the refused ones still
    commits, which is what makes the refusal an admission decision and not a failed run.
    """

    listed = [
        entry("E-fine", targets=[placed(CODE_SYMBOL)]),
        entry(
            "E-missing",
            rationale=None,
            targets=[placed(CODE_SYMBOL, "This one is explained."), placed(CODE_OTHER_SYMBOL)],
        ),
        entry("E-blank", rationale="   ", targets=[placed(CODE_OTHER_SYMBOL, "  ")]),
    ]

    report = run(pair, tmp_path, listed)

    assert [one.entry_id for one in report.committed] == ["E-fine"]
    refusals = {one.entry_id: one.refusal for one in report.refused}
    assert set(refusals) == {"E-missing", "E-blank"}
    missing = refusals["E-missing"]
    assert missing.startswith("realization_rationale_absent: entry 'E-missing'"), missing
    assert "1 of its 2 target(s)" in missing
    assert f"target 2 (path '{CODE_FILE}', locator " in missing
    assert f'"value": "{CODE_OTHER_SYMBOL}"' in missing
    assert "target 1 " not in missing
    assert refusals["E-blank"].startswith("realization_rationale_absent: entry 'E-blank'")
    assert journal_keys(report) == ["E-fine"]
    stored = [row[5] for row in claim_rows(report)]
    assert stored == ["The cited place carries this obligation in the fixture."]


def test_an_exact_replay_of_per_target_rationale_is_idempotent_and_a_changed_one_is_refused(
    pair: SourcePair, tmp_path: Path
) -> None:
    """Each target's own rationale is retry content: repeating it replays, rewording it conflicts."""

    def listed(first_rationale: str) -> list[dict[str, Any]]:
        return [
            entry(
                "E-replay",
                rationale=None,
                targets=[
                    placed(CODE_SYMBOL, first_rationale, "support"),
                    placed(CODE_OTHER_SYMBOL, "The second construct's own explanation."),
                ],
            )
        ]

    first = run(pair, tmp_path, listed("The first construct's own explanation."))
    assert [one.entry_id for one in first.committed] == ["E-replay"], first.refused
    rows = claim_rows(first)
    journal = Path(first.allocation_journal).read_bytes()

    second = run(pair, tmp_path, listed("The first construct's own explanation."))

    assert second.batch_state == "replayed"
    assert second.counts.commands_sent == 0
    assert claim_rows(second) == rows
    assert Path(second.allocation_journal).read_bytes() == journal

    changed = run(pair, tmp_path, listed("The first construct's explanation, reworded."))

    assert [one.entry_id for one in changed.refused] == ["E-replay"]
    assert changed.refused[0].refusal.startswith("allocation_content_conflict: ")
    assert claim_rows(changed) == rows


def test_a_claim_holding_the_old_generated_sentence_reads_back_exactly_as_stored(
    pair: SourcePair, tmp_path: Path
) -> None:
    """Rows the writer once filled with its path sentence stay readable, unclassified and unchanged.

    The admission check asks only whether an explanation was stated. Existing rows are never re-read
    against it, so a claim holding the old sentence reads through the public view byte for byte.
    """

    listed = [entry("E-legacy", rationale=GENERATED_BEFORE, targets=[placed(CODE_SYMBOL)])]

    report = run(pair, tmp_path, listed)

    assert [one.entry_id for one in report.committed] == ["E-legacy"], report.refused
    assert realized(report, "E-legacy") == {CODE_SYMBOL: ("unclassified", GENERATED_BEFORE)}
    assert [(row[4], row[5]) for row in claim_rows(report)] == [("unclassified", GENERATED_BEFORE)]


def test_a_value_that_is_not_text_an_unknown_role_or_an_over_long_rationale_refuses_its_own_entry(
    pair: SourcePair, tmp_path: Path
) -> None:
    """Each defect refuses only the entry that carries it, by name, before any identity is minted.

    Nothing is rendered into text: a list, a number or a boolean is never stored as its printed form.
    There is no ``absent`` role word, so writing one is refused rather than read as "not stated". An
    over-long rationale no longer escapes the per-entry report as an exception that aborts the list.
    """

    listed = [
        entry("E-ok", targets=[placed(CODE_SYMBOL)]),
        entry(
            "E-array", rationale=None, targets=[{**placed(CODE_SYMBOL), "rationale": ["a", "b"]}]
        ),
        {**entry("E-entry-number", targets=[placed(CODE_SYMBOL)]), "realization_rationale": 7},
        entry("E-absent-role", targets=[placed(CODE_SYMBOL, role="absent")]),
        {**entry("E-entry-role", targets=[placed(CODE_SYMBOL)]), "realization_role": "absent"},
        entry(
            "E-long", rationale=None, targets=[placed(CODE_SYMBOL, "x" * (PROSE_MAX_LENGTH + 1))]
        ),
    ]

    report = run(pair, tmp_path, listed)

    assert [one.entry_id for one in report.committed] == ["E-ok"]
    refusals = {one.entry_id: one.refusal.split(": ", 1) for one in report.refused}
    assert {entry_id: code for entry_id, (code, _) in refusals.items()} == {
        "E-array": "realization_value_not_text",
        "E-entry-number": "realization_value_not_text",
        "E-absent-role": "realization_role_unknown",
        "E-entry-role": "realization_role_unknown",
        "E-long": "realization_rationale_too_long",
    }
    assert "target 1 (path 'pkg/module.py'" in refusals["E-array"][1]
    assert "'rationale' is a JSON array" in refusals["E-array"][1]
    assert "entry-level" in refusals["E-entry-number"][1]
    assert "'role' is 'absent'" in refusals["E-absent-role"][1]
    assert f": {PROSE_MAX_LENGTH + 1} characters" in refusals["E-long"][1]
    assert journal_keys(report) == ["E-ok"]
    assert [row[5] for row in claim_rows(report)] == [
        "The cited place carries this obligation in the fixture."
    ]


BASE_COMMIT = "a0b2c18d2b8d08ac1242a13f65bde900a190df7a"

# The base writer, run in a child interpreter whose import path is the exported base source only.
_BASE_INGEST = """
import json, sys
import agents_remember
from agents_remember.application.knowledge_curator_ingest import IngestSelection, ingest_curator_list
contract, listed, candidate, authorization = sys.argv[1:5]
report = ingest_curator_list(
    contract, json.loads(listed), IngestSelection(candidate, authorization, dry_run=False)
)
print(json.dumps({
    "source": agents_remember.__file__,
    "batch": report.batch_state,
    "committed": [one.entry_id for one in report.committed],
}))
"""


@pytest.fixture(scope="module")
def base_writer(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The base commit's ``mcp/src``, exported with ``git archive`` from this checkout's history."""

    root = Path(__file__).resolve().parents[2]
    probe = ["git", "-C", str(root), "cat-file", "-e", f"{BASE_COMMIT}^{{commit}}"]
    if subprocess.run(probe, capture_output=True, check=False).returncode != 0:
        pytest.skip(f"the base commit {BASE_COMMIT} is not in this checkout's history")
    archive = subprocess.run(
        ["git", "-C", str(root), "archive", BASE_COMMIT, "mcp/src"], capture_output=True, check=True
    )
    exported = tmp_path_factory.mktemp("base-writer")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        bundle.extractall(exported, filter="data")
    return exported / "mcp" / "src"


def test_an_operation_the_base_writer_committed_without_rationale_replays_and_publishes(
    pair: SourcePair, tmp_path: Path, base_writer: Path
) -> None:
    """A committed operation writes nothing on retry, so it is not admitted again.

    The base writer, run from the base commit's own source, commits an entry with no rationale
    anywhere and stores its old generated sentence; this writer then retries it.
    """

    candidate = tmp_path / "candidate"
    legacy = [
        entry("LEG-1", rationale=None, targets=[placed(CODE_SYMBOL), placed(CODE_OTHER_SYMBOL)])
    ]
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            _BASE_INGEST,
            str(pair.contract_path),
            json.dumps(legacy),
            str(candidate),
            AUTHORIZATION,
        ],
        capture_output=True,
        check=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(base_writer)},
    )
    based = json.loads(child.stdout.strip().splitlines()[-1])
    assert Path(based["source"]).is_relative_to(base_writer), based
    assert (based["batch"], based["committed"]) == ("changed", ["LEG-1"])
    assert_the_committed_legacy_operation_replays(pair, tmp_path, legacy)


def test_a_committed_operation_without_rationale_replays_with_no_base_history_needed(
    pair: SourcePair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The committed-operation exemption, guarded wherever the base commit is not in history."""

    legacy = [
        entry("LEG-2", rationale=None, targets=[placed(CODE_SYMBOL), placed(CODE_OTHER_SYMBOL)])
    ]
    commit_like_the_base_writer(monkeypatch, pair, tmp_path, legacy)

    assert_the_committed_legacy_operation_replays(pair, tmp_path, legacy)


def test_a_recorded_but_uncommitted_allocation_without_rationale_is_still_refused(
    pair: SourcePair, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exemption is for what the candidate COMMITTED, not for what the journal merely records.

    The journal is written before the batch, so a recorded allocation says nothing about whether its
    rows landed. Copying a committed operation's journal into an empty candidate leaves the allocation
    recorded and the revision absent: its retry would write new realizations, so it is admitted again
    and refused for having no rationale, and the copied journal is left exactly as it was.
    """

    legacy = [
        entry("LEG-3", rationale=None, targets=[placed(CODE_SYMBOL), placed(CODE_OTHER_SYMBOL)])
    ]
    commit_like_the_base_writer(monkeypatch, pair, tmp_path, legacy)
    journal = tmp_path / "candidate" / "curator-allocation-journal.json"
    recorded_only = tmp_path / "recorded-only"
    recorded_only.mkdir()
    shutil.copyfile(journal, recorded_only / journal.name)

    report = ingest_curator_list(
        pair.contract_path,
        legacy,
        IngestSelection(
            candidate_directory=recorded_only, authorization_ref=AUTHORIZATION, dry_run=False
        ),
    )

    assert report.committed == ()
    assert [(one.entry_id, one.refusal.split(":", 1)[0]) for one in report.refused] == [
        ("LEG-3", "realization_rationale_absent")
    ]
    assert (recorded_only / journal.name).read_bytes() == journal.read_bytes()


def commit_like_the_base_writer(
    monkeypatch: pytest.MonkeyPatch, pair: SourcePair, tmp_path: Path, legacy: list[dict[str, Any]]
) -> None:
    """Commit a rationale-less list into ``tmp_path / "candidate"`` the way the base writer did.

    The state the base writer left behind is rebuilt in process through this writer, with only its two
    base behaviours restored for that one run: no realization check at admission, and the generated
    sentence as the text of a target with no rationale. Nothing a target states is added, so the
    recorded content is exactly what the base writer recorded. Every later run is the real writer.
    """

    resolve = EntryRealization.for_target

    def generated(self: EntryRealization, target: dict[str, Any]) -> TargetRealization:
        made = resolve(self, target)
        return made if made.rationale else replace(made, rationale=GENERATED_BEFORE)

    with monkeypatch.context() as base_shaped:
        base_shaped.setattr(knowledge_curator_ingest, "realization_refusal", lambda *_: None)
        base_shaped.setattr(EntryRealization, "for_target", generated)
        committed = run(pair, tmp_path, legacy)
    assert [one.entry_id for one in committed.committed] == [legacy[0]["id"]], committed.refused


def assert_the_committed_legacy_operation_replays(
    pair: SourcePair, tmp_path: Path, legacy: list[dict[str, Any]]
) -> None:
    """A committed rationale-less operation replays and publishes; changed or new content does not.

    The exact retry replays, publishes, and leaves the journal and the stored claims byte-identical.
    The same key carrying a rationale is changed content and still conflicts, and a new entry without
    a rationale beside it is still refused before it is minted.
    """

    candidate = tmp_path / "candidate"
    entry_id = str(legacy[0]["id"])
    journal = (candidate / "curator-allocation-journal.json").read_bytes()
    rows = claim_rows_at(candidate)
    assert [row[5] for row in rows] == [GENERATED_BEFORE, GENERATED_BEFORE]

    published = tmp_path / "published" / "knowledge.sqlite"
    retried = ingest_curator_list(
        pair.contract_path,
        legacy,
        IngestSelection(
            candidate_directory=candidate,
            authorization_ref=AUTHORIZATION,
            dry_run=False,
            publication=IngestPublication(destination_path=published),
        ),
    )

    assert (retried.batch_state, [one.entry_id for one in retried.committed]) == (
        "replayed",
        [entry_id],
    ), retried.refused
    assert retried.publication is not None and retried.publication.state == "published"
    assert published.is_file()
    assert (candidate / "curator-allocation-journal.json").read_bytes() == journal
    assert claim_rows_at(candidate) == rows

    changed = [
        entry(
            entry_id,
            rationale=None,
            targets=[placed(CODE_SYMBOL, "Now explained."), placed(CODE_OTHER_SYMBOL, "Also.")],
        ),
        entry("NEW-1", rationale=None, targets=[placed(CODE_SYMBOL)]),
    ]
    refused = run(pair, tmp_path, changed)

    codes = {one.entry_id: one.refusal.split(":", 1)[0] for one in refused.refused}
    assert codes == {
        entry_id: "allocation_content_conflict",
        "NEW-1": "realization_rationale_absent",
    }
    assert (candidate / "curator-allocation-journal.json").read_bytes() == journal


def test_the_word_absent_as_a_governing_route_is_refused_and_an_omitted_route_stays_ungoverned(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The placeholder word is refused where it is written; leaving the route out governs nothing.

    Read as a path, a copied ``"absent"`` would author a route named ``absent``. An omitted key and a
    ``null`` route are the honest spellings, and both leave the anchor ungoverned, with no route row.
    """

    omitted = placed(CODE_SYMBOL, "Its own explanation, with no route named.")
    del omitted["governing_route"]
    listed = [
        entry("E-route-word", targets=[{**placed(CODE_SYMBOL), "governing_route": "absent"}]),
        {
            **entry("E-authority-word", targets=[placed(CODE_SYMBOL)]),
            "authority": {"governing_route": "absent", "task_document": "ingest_case"},
        },
        entry(
            "E-no-route", targets=[omitted, {**placed(CODE_OTHER_SYMBOL), "governing_route": None}]
        ),
    ]

    report = run(pair, tmp_path, listed)

    assert [one.entry_id for one in report.committed] == ["E-no-route"], report.refused
    refusals = {one.entry_id: one.refusal for one in report.refused}
    code = "realization_governing_route_absent_literal"
    assert refusals["E-route-word"].startswith(f"{code}: entry 'E-route-word'")
    assert "target 1 (path 'pkg/module.py'" in refusals["E-route-word"]
    assert "'governing_route' is 'absent'" in refusals["E-route-word"]
    assert refusals["E-authority-word"].startswith(f"{code}: entry 'E-authority-word'")
    assert "entry-level" in refusals["E-authority-word"]
    assert "'authority.governing_route' is 'absent'" in refusals["E-authority-word"]
    assert journal_keys(report) == ["E-no-route"]
    assert [(one.route_path, one.route_state) for one in report.committed[0].targets] == [
        (None, "ungoverned"),
        (None, "ungoverned"),
    ]
    connection = sqlite3.connect(f"file:{database_of(tmp_path / 'candidate')}?mode=ro", uri=True)
    try:
        routes = [
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("route", "source_anchor_route")
        ]
    finally:
        connection.close()
    assert routes == [0, 0]


def route_paths(tmp_path: Path) -> list[str]:
    """Every route path the candidate's dataset stores, in order."""

    connection = sqlite3.connect(f"file:{database_of(tmp_path / 'candidate')}?mode=ro", uri=True)
    try:
        return [str(row[0]) for row in connection.execute("SELECT path FROM route ORDER BY path")]
    finally:
        connection.close()


def test_a_governing_route_that_is_not_text_is_refused_where_it_is_written(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A number, a list or a boolean never becomes a route named by its printed form."""

    listed = [
        entry("E-ok", targets=[placed(CODE_SYMBOL)]),
        entry("E-route-number", targets=[{**placed(CODE_SYMBOL), "governing_route": 7}]),
        entry("E-route-array", targets=[{**placed(CODE_SYMBOL), "governing_route": ["pkg"]}]),
        {
            **entry("E-authority-boolean", targets=[placed(CODE_SYMBOL)]),
            "authority": {"governing_route": True, "task_document": "ingest_case"},
        },
    ]

    report = run(pair, tmp_path, listed)

    assert [one.entry_id for one in report.committed] == ["E-ok"], report.refused
    refusals = {one.entry_id: one.refusal for one in report.refused}
    assert {key: value.split(":", 1)[0] for key, value in refusals.items()} == {
        "E-route-number": "realization_value_not_text",
        "E-route-array": "realization_value_not_text",
        "E-authority-boolean": "realization_value_not_text",
    }
    assert "target 1 (path 'pkg/module.py'" in refusals["E-route-number"]
    assert "'governing_route' is a JSON number" in refusals["E-route-number"]
    assert "'governing_route' is a JSON array" in refusals["E-route-array"]
    assert "entry-level" in refusals["E-authority-boolean"]
    assert "'authority.governing_route' is a JSON boolean" in refusals["E-authority-boolean"]
    assert journal_keys(report) == ["E-ok"]
    assert route_paths(tmp_path) == ["pkg"]


def test_the_word_absent_is_refused_as_a_governing_route_in_any_case_once_trimmed(
    pair: SourcePair, tmp_path: Path
) -> None:
    """``"Absent"`` and ``" ABSENT "`` are the same placeholder word and are refused like it."""

    listed = [
        entry("E-ok", targets=[placed(CODE_SYMBOL)]),
        entry("E-route-title", targets=[{**placed(CODE_SYMBOL), "governing_route": "Absent"}]),
        {
            **entry("E-authority-upper", targets=[placed(CODE_SYMBOL)]),
            "authority": {"governing_route": " ABSENT ", "task_document": "ingest_case"},
        },
    ]

    report = run(pair, tmp_path, listed)

    assert [one.entry_id for one in report.committed] == ["E-ok"], report.refused
    refusals = {one.entry_id: one.refusal for one in report.refused}
    code = "realization_governing_route_absent_literal"
    assert refusals["E-route-title"].startswith(f"{code}: entry 'E-route-title'")
    assert "'governing_route' is 'Absent'" in refusals["E-route-title"]
    assert refusals["E-authority-upper"].startswith(f"{code}: entry 'E-authority-upper'")
    assert "'authority.governing_route' is 'ABSENT'" in refusals["E-authority-upper"]
    assert journal_keys(report) == ["E-ok"]
    assert route_paths(tmp_path) == ["pkg"]
