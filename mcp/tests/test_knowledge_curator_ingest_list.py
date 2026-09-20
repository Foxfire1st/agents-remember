"""The curator's reachable ingest: one hand-off list becomes one admitted batch and one report.

The write half already has its own cases (``test_knowledge_curator_ingest.py``), so nothing here
re-protects a citation round trip. What this module adds is five operations those cases cannot
reach, and each case below measures one of them:

* **a producer citing the code its own leaf is producing** -- the case the whole operation exists
  for, and the one that was broken: the leaf's own line is the tree the citations resolve against, so
  a file the leaf **added** commits and reads back (R-1) and a file the leaf **modified** commits
  too (R-2) rather than making the run raise before it has a report;
* **the three outcomes in one run**, on one list: an entry whose citation resolved is committed, a
  ruling that carries no target is ``skipped`` with its own verdict carried, and a target that does
  not resolve is ``refused`` with the reason that distinguishes the ways it can fail -- a spelling
  outside the two admitted roots, a place in a third root, a real top-level entry whose file is
  gone, a dependency's source, and a construct that is not defined in the named file;
* **the whole path of a producer's bare symbol name**: derived language, the stored two-part
  locator, the read surface handing it back typed, and the rail refusing to resolve it;
* **identity derived from the enclosure**, so a re-run mints the same invariant rather than a second
  one, and a run that fails still names what it would have written;
* **the route leg**, which is the one part of the citation the closed command union cannot carry.

The fixture is a real pair of Git repositories beside a real contract, because the ingest resolves
paths through a recorded tree and derives each blob identity from the bytes the tree names: a
synthetic path table would measure the fixture instead of the resolver. The code repository carries
**two commits** -- the base the contract records and the leaf's own work on top of it -- because the
difference between those two trees is exactly what a leaf's citations are about.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_curator_ingest import (
    COMMITTED,
    SKIPPED,
    IngestReport,
    IngestSelection,
    _admitted_candidate,
    _EntryFields,
    _observation_id,
    _repository_identity,
    _Resolved,
    _RouteLedger,
    _Run,
    ingest_curator_list,
)
from agents_remember.application.knowledge_ingest import CuratorEntry, curator_entry_commands
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_views import read_knowledge_view
from agents_remember.cli.__main__ import main
from agents_remember.mcp.tools.knowledge import (
    DECLARED_CHANGE_KINDS,
    WRITE_ENTRY_POINT,
    ChangeToolRequest,
    knowledge_change_payload,
)
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.models.knowledge.snapshot import candidate_database_path
from agents_remember.models.knowledge.source import SymbolLocator
from agents_remember.models.knowledge.view import SourceContextView, ViewRequest
from agents_remember.worktrees.worktree_contract import WorktreeContract
from snapshot_lifecycle_test_support import build_case, create, write_record

pytestmark = pytest.mark.evidence_unit

AUTHORIZATION = "authorization:ks-l25-ingest-case"
CODE_FILE = "pkg/module.py"
CODE_SYMBOL = "resolve_budget"
MEMORY_CARD = "pkg/module.md"
GONE_PATH = "pkg/retired_module.py"
OUTSIDE_PATH = "notes/reports/terminal-report.md"
DEPENDENCY_PATH = "vendor/third_party.py"
# The two files the leaf's own line holds and the recorded base commit does not: the module the leaf
# added, and the pre-existing module the leaf edited. Both are what a leaf's own hand-off list cites.
LEAF_ADDED_FILE = "pkg/added_by_the_leaf.py"
LEAF_ADDED_SYMBOL = "the_thing_the_leaf_built"
LEAF_EDITED_FILE = "pkg/batch.py"
LEAF_EDITED_SYMBOL = "leaf_added_symbol"
# A file whose docstring, comment and neighbouring definition all contain the names this module's
# cases cite, so "a mention is not a definition" is measured against bytes that really hold them.
MENTIONS_FILE = "pkg/mentions.py"
MENTION_DOCSTRING_ONLY = "documented_only"
MENTION_COMMENT_ONLY = "commented_only"
MENTION_SUBSTRING = "resolve_budget"
_SUBSTRING_DEFINITION = "resolve_budget_v"
# A real structured data file, at the repository root, whose key is a name and not a definition.
TOML_FILE = "pyproject.toml"
_TOML_TEXT = "[tool.pytest.ini_options]\nunit_case_budget = 2300\n"
_SEPARATOR = "\n\n\n"
_MENTIONS_TEXT = (
    '"""A module that documents names it does not define.\n'
    "\n"
    f"The {MENTION_DOCSTRING_ONLY} construct is described here and defined nowhere in this file.\n"
    '"""\n'
    "\n"
    "\n"
    f"# The {MENTION_COMMENT_ONLY} hook is mentioned in this comment and defined nowhere either.\n"
    "\n"
    "\n"
    f"def {_SUBSTRING_DEFINITION}() -> None:\n"
    "    return None\n"
)

_CODE_TEXT = (
    "# module\n"
    "\n"
    "\n"
    "def resolve_budget(attempt: int) -> int:\n"
    "    return attempt\n"
    "\n"
    "\n"
    "def other() -> None:\n"
    "    return None\n"
)
_MEMORY_CARD_TEXT = "# pkg module\n\nThe card for the module.\n"


@dataclass(frozen=True)
class _Layout:
    """The four directories the contract's cells name, so building it names one value."""

    root: Path
    code_root: Path
    memory_root: Path
    task_root: Path


@dataclass(frozen=True)
class SourcePair:
    """One real code repository, one real memory repository, and the contract naming them."""

    code_root: Path
    memory_root: Path
    contract_path: Path
    task_root: Path
    code_tree_id: str
    memory_tree_id: str
    code_blobs: dict[str, str]
    memory_blobs: dict[str, str]
    code_commit: str
    leaf_tree_id: str
    leaf_blobs: dict[str, str]


@pytest.fixture(scope="session")
def pair(tmp_path_factory: pytest.TempPathFactory) -> SourcePair:
    """The session's repositories, built once because no case mutates them.

    Every case writes into its own candidate directory under its own ``tmp_path``, so the sources
    can be shared: a case that mutated these bytes would be measuring a different tree than the
    contract records, which is exactly what the resolver refuses.

    The code repository is committed **twice**: the base the contract records, and then the leaf's
    own work on top of it -- one file added and one file edited, both left in the worktree as the
    leaf's line. That is the state a leaf is always in when the curator ingests its list, and it is
    the state the resolver has to resolve against.
    """

    root = tmp_path_factory.mktemp("curator-ingest")
    code_root = root / "code"
    memory_root = root / "memory"
    task_root = root / "tasks" / "sprint"
    batch_base = "# batch\none transaction\n"
    _write_files(code_root, {CODE_FILE: _CODE_TEXT, LEAF_EDITED_FILE: batch_base})
    _write_files(
        memory_root,
        {
            f"onboarding/{MEMORY_CARD}": _MEMORY_CARD_TEXT,
            "onboarding/overview.md": "# onboarding overview\n",
        },
    )
    # The third root. It is a place under the coordination root, which the citation machinery does
    # not admit, so a target naming it is refused as out of scope rather than as gone -- and it is
    # refused that way whether or not the file is still on disk when the run happens, because the
    # reason is read from the recorded roots rather than from a live directory.
    _write_files(root, {OUTSIDE_PATH: "# terminal report\nlanded\n"})
    _git(code_root, ["init", "-q", "--initial-branch=main"])
    _git(memory_root, ["init", "-q", "--initial-branch=main"])
    code_commit = _commit(code_root, "code tree")
    memory_commit = _commit(memory_root, "memory tree")
    base_blobs = {
        path: _git(code_root, ["rev-parse", f"{code_commit}:{path}"])
        for path in (CODE_FILE, LEAF_EDITED_FILE)
    }
    base_tree = _git(code_root, ["rev-parse", f"{code_commit}^{{tree}}"])
    # The leaf's own work, committed on the line the worktree stands on. Nothing is left dirty:
    # the base tree and the line tree then differ only by the leaf's change, which is what makes a
    # case here a measurement about the two trees rather than about a working directory.
    _write_files(
        code_root,
        {
            LEAF_EDITED_FILE: batch_base
            + _SEPARATOR
            + f"def {LEAF_EDITED_SYMBOL}() -> None:\n    return None\n",
            LEAF_ADDED_FILE: "# added by the leaf\n"
            + _SEPARATOR
            + "class Holder:\n"
            + f"    def {LEAF_ADDED_SYMBOL}(self) -> None:\n        return None\n",
            MENTIONS_FILE: _MENTIONS_TEXT,
            TOML_FILE: _TOML_TEXT,
        },
    )
    leaf_commit = _commit(code_root, "the leaf's own work")
    contract = root / "series-contract.md"
    layout = _Layout(root=root, code_root=code_root, memory_root=memory_root, task_root=task_root)
    contract.write_text(_contract_text(layout, code_commit, memory_commit), encoding="utf-8")
    return SourcePair(
        code_root=code_root,
        memory_root=memory_root,
        contract_path=contract,
        task_root=task_root,
        code_tree_id=base_tree,
        code_commit=code_commit,
        memory_tree_id=_git(memory_root, ["rev-parse", f"{memory_commit}^{{tree}}"]),
        code_blobs=base_blobs,
        memory_blobs={
            path: _git(memory_root, ["rev-parse", f"{memory_commit}:{path}"])
            for path in (f"onboarding/{MEMORY_CARD}", "onboarding/overview.md")
        },
        leaf_tree_id=_git(code_root, ["rev-parse", f"{leaf_commit}^{{tree}}"]),
        leaf_blobs={
            path: _git(code_root, ["rev-parse", f"{leaf_commit}:{path}"])
            for path in (LEAF_ADDED_FILE, LEAF_EDITED_FILE, MENTIONS_FILE, CODE_FILE)
        },
    )


def _write_files(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _commit(root: Path, message: str) -> str:
    _git(root, ["config", "user.email", "fixture@example.invalid"])
    _git(root, ["config", "user.name", "curator ingest fixture"])
    _git(root, ["add", "-A"])
    _git(root, ["commit", "-q", "-m", message])
    return _git(root, ["rev-parse", "HEAD"])


def _git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _contract_text(layout: _Layout, code: str, memory: str) -> str:
    """One contract carrying exactly the cells the ingest reads, with no defaults assumed."""

    root, code_root, memory_root, task_root = (
        layout.root,
        layout.code_root,
        layout.memory_root,
        layout.task_root,
    )
    return (
        "---\n"
        "schema: ar-series-contract/v1\n"
        "schemaVersion: 1.0\n"
        "kind: leaf\n"
        "task_id: 260915_INGEST-CASE\n"
        "task_name: ingest_case\n"
        "repo_name: agents-remember\n"
        "workflow_kind: light-task\n"
        "memory_mode: external\n"
        "\n"
        "coordination:\n"
        f"  root: {root}\n"
        f"  task_root: {task_root}\n"
        f"  task_artifact: {task_root / 'task.md'}\n"
        f"  worktree_group: {root}\n"
        "  leaf_id: 260915-INGEST-CASE-L1\n"
        "  parent_task_name: ingest_case\n"
        "\n"
        "code:\n"
        f"  repo_path: {code_root}\n"
        "  source_branch: main\n"
        "  work_branch: ar/ingest-case\n"
        f"  base_commit: {code}\n"
        f"  worktree: {code_root}\n"
        "\n"
        "memory:\n"
        "  mode: external\n"
        f"  repo_path: {memory_root}\n"
        "  source_branch: main\n"
        "  work_branch: ar/ingest-case\n"
        f"  base_commit: {memory}\n"
        f"  worktree: {memory_root}\n"
        f"  ledger: {memory_root / 'memory.md'}\n"
        "---\n"
    )


# --------------------------------------------------------------------------------------------
# The hand-off entries these cases hand over
# --------------------------------------------------------------------------------------------


def entry(
    entry_id: str,
    *,
    kind: str = "clause",
    disposition: str = "satisfied",
    targets: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One hand-off entry in revision 1's shape, with the curator-side fields null."""

    return {
        "id": entry_id,
        "statement": f"The obligation {entry_id} records.",
        "kind": kind,
        "target": targets if targets is not None else [],
        "found_at": [],
        "disposition": disposition,
        "disposition_source": None,
        "evidence": f"{entry_id} terminal report",
        "resolution": None,
        "validated_at": None,
        "record_action": None,
        "supersedes": None,
        "authority": {"governing_route": "pkg", "task_document": "ingest_case"},
    }


def target(path: str, *, locator: dict[str, Any] | None = None, route: str | None = None) -> dict:
    """One target place, with the locator and route the producer wrote or their absence."""

    return {"path": path, "locator": locator, "governing_route": route}


def _range(start: int, end: int) -> dict[str, Any]:
    """One producer-written line range, so the cases below name two numbers rather than a dict."""

    return {"kind": "line_range", "start": start, "end": end}


def symbol(value: str) -> dict[str, Any]:
    """The producer's spelling of a symbol: a bare name, no language and no file."""

    return {"kind": "symbol", "value": value}


def run(
    pair: SourcePair, tmp_path: Path, entries: list[dict[str, Any]], *, dry_run: bool = False
) -> IngestReport:
    """Ingest one list into a candidate directory this case owns."""

    return ingest_curator_list(
        pair.contract_path,
        entries,
        IngestSelection(
            candidate_directory=tmp_path / "candidate",
            authorization_ref=AUTHORIZATION,
            dry_run=dry_run,
        ),
    )


def by_id(outcomes: tuple[Any, ...]) -> dict[str, Any]:
    """The outcomes of one report, keyed by entry id."""

    return {outcome.entry_id: outcome for outcome in outcomes}


def counts(pair: SourcePair, database: Path) -> dict[str, int]:
    """The row count of every table this operation writes, read from the database itself."""

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "invariant",
                "invariant_revision",
                "source_anchor",
                "realization_claim",
                "route",
                "source_anchor_route",
            )
        }
    finally:
        connection.close()


# --------------------------------------------------------------------------------------------
# The mixture: committed, skipped and refused in one report
# --------------------------------------------------------------------------------------------


def test_one_run_reports_committed_skipped_and_each_refusal_reason(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The three outcomes, and the three ways a path fails, all distinguishable in one report.

    A report showing only successes has not read a real list: the mixture is what the operation
    exists to produce, and each refusal must carry the reason that distinguishes it from the other
    two rather than one shared word for "did not resolve".
    """

    report = run(
        pair,
        tmp_path,
        [
            entry(
                "E-committed", targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL), route="pkg")]
            ),
            entry("E-ruling", kind="decision", disposition="nothing to do"),
            entry("E-gone", targets=[target(GONE_PATH, locator={"kind": "file"})]),
            entry("E-outside", targets=[target(OUTSIDE_PATH, locator={"kind": "file"})]),
            entry(
                "E-dependency",
                targets=[
                    target(DEPENDENCY_PATH, locator=symbol("Missing")),
                ],
            ),
        ],
    )

    assert report.counts.entries_read == 5
    assert {one.state for one in report.committed} == {COMMITTED}
    assert [one.entry_id for one in report.committed] == ["E-committed"]
    assert [one.entry_id for one in report.rulings] == ["E-ruling"]
    assert {one.entry_id for one in report.refused} == {"E-gone", "E-outside", "E-dependency"}
    # The ruling is skipped **with its own verdict carried**, so the producer's decision survives a
    # run that authored nothing about code.
    ruling = report.rulings[0]
    assert ruling.state == SKIPPED
    assert ruling.disposition == "nothing to do"
    assert ruling.kind == "decision"

    refused = by_id(report.refused)
    assert refused["E-gone"].refusal.startswith("target_path_unresolved: top_level_entry_file_gone")
    assert refused["E-outside"].refusal.startswith(
        "target_path_unresolved: third_root_out_of_scope"
    )
    assert refused["E-dependency"].refusal.startswith(
        "target_path_unresolved: dependency_source_not_ours"
    )
    # The three path reasons are distinct words, not three spellings of one.
    reasons = {
        refused[one].refusal.split(":", 1)[1].strip().split(":", 1)[0]
        for one in ("E-gone", "E-outside", "E-dependency")
    }
    assert len(reasons) == 3
    assert report.batch_state == "changed"


def test_a_ruling_is_never_committed_as_an_obligation_with_no_realization(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The empty-target case is skipped, not filed as an authored claim about code.

    ``CuratorEntry.citations`` permits an empty list, and that path is deliberately not used here:
    recording a ruling as an invariant would invent a claim the producer never made.
    """

    report = run(pair, tmp_path, [entry("E-only-ruling", kind="decision", disposition="recorded")])

    assert report.committed == ()
    assert [one.state for one in report.rulings] == [SKIPPED]
    assert report.refused == ()
    assert report.counts.targets_completed == 0
    # Nothing was written, which is what makes the skip a skip rather than a silent commit.
    assert counts(pair, tmp_path / "candidate" / "knowledge-candidate.sqlite")["invariant"] == 0

    # The rule is about the *empty target* and not about the three kinds the contract names as its
    # commonest producers: an entry whose kind is ``clause`` or ``finding`` is still a ruling that
    # applies nowhere, and refusing one for wearing the wrong kind would lose the verdict the
    # producer authored. This was a second case until the two were merged, because both measure one
    # reading of ``target: []``.
    second = run(
        pair,
        tmp_path / "kinds",
        [
            entry("E-clause", kind="clause", disposition="nothing to do"),
            entry("E-finding", kind="finding", disposition="discharged"),
        ],
    )

    assert second.refused == ()
    assert second.committed == ()
    assert [one.state for one in second.rulings] == [SKIPPED, SKIPPED]
    assert [(one.entry_id, one.kind) for one in second.rulings] == [
        ("E-clause", "clause"),
        ("E-finding", "finding"),
    ]
    assert [one.disposition for one in second.rulings] == ["nothing to do", "discharged"]


# --------------------------------------------------------------------------------------------
# The leaf's own line: a producer citing the code its leaf is producing
# --------------------------------------------------------------------------------------------


def test_a_producer_citing_a_file_its_own_leaf_created_commits_and_reads_back(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The leaf's own new file is the case the operation exists for, and it must not be refused.

    The contract records the tree the enclosure was cut from; the leaf's deliverable is not in it.
    Resolving against the recorded base therefore made this entry a refusal that said the file was
    *gone* -- a false statement about a file the producer had just written. The run resolves against
    the leaf's own line, so the entry commits, the stored identity is that line's blob, and the
    receipt binds the candidate to the tree the citations were really read from.
    """

    report = run(
        pair,
        tmp_path,
        [
            entry(
                "E-leaf-created",
                targets=[
                    target(
                        LEAF_ADDED_FILE,
                        locator=symbol(LEAF_ADDED_SYMBOL),
                        route="pkg",
                    )
                ],
            )
        ],
    )

    assert [one.entry_id for one in report.committed] == ["E-leaf-created"]
    assert report.refused == ()
    assert report.code_tree_id == pair.leaf_tree_id
    # The recorded source identity is the line's blob for the path, and the base tree has no member
    # there at all -- which is the whole difference between the two trees.
    target_outcome = report.committed[0].targets[0]
    assert target_outcome.source_identity == pair.leaf_blobs[LEAF_ADDED_FILE]
    assert LEAF_ADDED_FILE not in pair.code_blobs

    database = tmp_path / "candidate" / "knowledge-candidate.sqlite"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        stored = connection.execute(
            "SELECT source_identity, locator FROM source_anchor WHERE path = ?",
            (LEAF_ADDED_FILE,),
        ).fetchone()
    finally:
        connection.close()
    assert stored is not None
    assert json.loads(stored[0])["object_id"] == pair.leaf_blobs[LEAF_ADDED_FILE]
    assert json.loads(stored[1]) == {
        "kind": "symbol",
        "language": "python",
        "qualified_name": LEAF_ADDED_SYMBOL,
    }


def test_a_producer_citing_a_file_its_own_leaf_modified_gets_a_report_not_an_exception(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The ordinary state of a leaf -- a changed file on its line -- must produce a report.

    The citation machinery proves working bytes against the tree it resolved, and it signals a
    disagreement by raising. Bound to the base commit, that raise escaped the operation: the caller
    got a traceback and no :class:`IngestReport`, which violates the operation's first promise that
    every entry's outcome is reported. Resolving against the leaf's own line is what makes the
    ordinary case ordinary, and the identity recorded is that line's blob for the edited file.
    """

    report = run(
        pair,
        tmp_path,
        [
            entry(
                "E-leaf-edited",
                targets=[target(LEAF_EDITED_FILE, locator=symbol(LEAF_EDITED_SYMBOL))],
            )
        ],
    )

    assert [one.entry_id for one in report.committed] == ["E-leaf-edited"]
    assert report.refused == ()
    assert report.committed[0].targets[0].source_identity == pair.leaf_blobs[LEAF_EDITED_FILE]
    # The two trees genuinely disagree about this file, which is what made the old binding raise.
    assert pair.leaf_blobs[LEAF_EDITED_FILE] != pair.code_blobs[LEAF_EDITED_FILE]


# --------------------------------------------------------------------------------------------
# The reason vocabulary: distinct failures, distinct reasons, and no live-filesystem state
# --------------------------------------------------------------------------------------------


def test_each_locator_failure_carries_the_reason_that_is_true_of_it(
    pair: SourcePair, tmp_path: Path
) -> None:
    """Four different things can be wrong with a range, and four different names say which.

    One shared code whose words assert "the construct is not in the named file" is false for all
    four of these: non-integer bounds are a spelling the model cannot read, ``0`` is a zero-based
    range in a one-based model, a reversed pair names no ordered extent, and a range past the last
    line is a range the file cannot hold. A producer that wrote one of them needs to be told which,
    because each has a different fix and none of them is "search somewhere else".
    """

    report = run(
        pair,
        tmp_path,
        [
            entry(
                "E-not-integer",
                targets=[
                    target(CODE_FILE, locator={"kind": "line_range", "start": "1", "end": "2"})
                ],
            ),
            entry(
                "E-reversed",
                targets=[target(CODE_FILE, locator={"kind": "line_range", "start": 9, "end": 2})],
            ),
            entry(
                "E-zero",
                targets=[target(CODE_FILE, locator={"kind": "line_range", "start": 0, "end": 0})],
            ),
            entry(
                "E-past-end",
                targets=[
                    target(CODE_FILE, locator={"kind": "line_range", "start": 1, "end": 999999})
                ],
            ),
            entry(
                "E-unknown-kind",
                targets=[target(CODE_FILE, locator={"kind": "column", "value": "3"})],
            ),
        ],
    )

    assert report.committed == ()
    refused = by_id(report.refused)
    assert set(refused) == {
        "E-not-integer",
        "E-reversed",
        "E-zero",
        "E-past-end",
        "E-unknown-kind",
    }
    codes = {one: refused[one].refusal.split(":", 1)[0] for one in sorted(refused)}
    assert codes == {
        "E-not-integer": "line_range_not_integer_bounds",
        "E-reversed": "line_range_not_ordered",
        "E-zero": "line_range_not_one_based",
        "E-past-end": "line_range_past_last_line",
        "E-unknown-kind": "unsupported_locator_kind",
    }
    # A one-based range inside the file is accepted, so the four refusals are not a blanket refusal
    # of ranges: the distinction is what is being measured, not a tightening of the rule.
    accepted = run(
        pair,
        tmp_path / "accepted",
        [
            entry(
                "E-one-based",
                targets=[target(CODE_FILE, locator={"kind": "line_range", "start": 1, "end": 2})],
            )
        ],
    )
    assert [one.entry_id for one in accepted.committed] == ["E-one-based"]

    # An entry refused at its *second* target still completed its first, and the report says so:
    # ``targets_completed`` counts the place the read really measured, while ``locators_resolved``
    # counts only the places that reached the batch. The two agree on a list where nothing is
    # refused, which is exactly why one list that refuses has to show them apart.
    partial = run(
        pair,
        tmp_path / "partial",
        [
            entry(
                "E-second-target-refused",
                targets=[
                    target(CODE_FILE, locator=_range(1, 2)),
                    target(CODE_FILE, locator=_range(1, 999999)),
                ],
            )
        ],
    )
    assert partial.committed == ()
    assert [one.entry_id for one in partial.refused] == ["E-second-target-refused"]
    assert [one.completed_path for one in partial.refused[0].targets] == [CODE_FILE]
    assert partial.counts.targets_completed == 1
    assert partial.counts.locators_resolved == 0


def test_a_path_reason_is_read_from_the_trees_and_not_from_the_live_directories(
    tmp_path: Path,
) -> None:
    """Every path reason is a fact about the citation, and deleting the cited file does not move it.

    A classifier that asks ``is_file()`` on the third root answers differently before and after the
    enclosure's cleanup, so the reason a hand-off entry earns stops being reproducible from the
    record the citation was written against. This case builds its own enclosure, deletes the cited
    report between two runs, and asserts every reason is unchanged -- and it separates the four
    spellings that are *not* "a place under the coordination root" from it: an absolute path
    pointing inside an admitted root, an absolute path pointing at nothing it admits, a traversal, a
    dependency's path, and a real top-level entry whose file is gone. The one thing the
    classification does read from the enclosure is which top-level directories the coordination root
    holds, so that its first-segment test can tell a third root from a dependency; the last pair of
    assertions states that limit rather than hiding it.
    """

    root = tmp_path / "enclosure"
    code_root = root / "code"
    memory_root = root / "memory"
    task_root = root / "tasks" / "sprint"
    _write_files(code_root, {CODE_FILE: _CODE_TEXT, "pkg/batch.py": "# batch\none transaction\n"})
    _write_files(memory_root, {f"onboarding/{MEMORY_CARD}": _MEMORY_CARD_TEXT})
    _write_files(root, {OUTSIDE_PATH: "# terminal report\nlanded\n"})
    _git(code_root, ["init", "-q", "--initial-branch=main"])
    _git(memory_root, ["init", "-q", "--initial-branch=main"])
    code_commit = _commit(code_root, "code tree")
    memory_commit = _commit(memory_root, "memory tree")
    contract = root / "series-contract.md"
    layout = _Layout(root=root, code_root=code_root, memory_root=memory_root, task_root=task_root)
    contract.write_text(_contract_text(layout, code_commit, memory_commit), encoding="utf-8")
    entries = [
        entry("E-third-root", targets=[target(OUTSIDE_PATH, locator={"kind": "file"})]),
        entry(
            "E-absolute",
            # Absolute, and pointing at a file that really is inside the admitted code root: the
            # refusal names that, rather than calling a file the producer can see out of scope.
            targets=[target(str(code_root / CODE_FILE), locator={"kind": "file"})],
        ),
        entry(
            "E-absolute-outside",
            targets=[target(str(root / OUTSIDE_PATH), locator={"kind": "file"})],
        ),
        entry(
            "E-traversal", targets=[target("../../../../etc/hostname", locator={"kind": "file"})]
        ),
        entry("E-gone", targets=[target(GONE_PATH, locator={"kind": "file"})]),
        entry("E-dependency", targets=[target(DEPENDENCY_PATH, locator={"kind": "file"})]),
    ]

    def reason_codes(candidate: Path) -> dict[str, str]:
        report = ingest_curator_list(
            contract,
            entries,
            IngestSelection(
                candidate_directory=candidate,
                authorization_ref=AUTHORIZATION,
                dry_run=False,
            ),
        )
        # A spelling refusal is the entry's own code; a resolution refusal carries the code inside
        # its reason, after the step that produced it. Both spellings are read here so the case
        # measures the code rather than the punctuation around it.
        return {
            one.entry_id: one.refusal.split(":", 1)[1].strip().split(":", 1)[0]
            if one.refusal.startswith("target_path_unresolved:")
            else one.refusal.split(":", 1)[0]
            for one in report.refused
        }

    expected = {
        "E-third-root": "third_root_out_of_scope",
        # An absolute path that points inside an admitted root is named as exactly that, rather than
        # called out of scope -- a false statement about a file the producer can see. A traversal is
        # refused for its spelling wherever it points.
        "E-absolute": "path_inside_admitted_root_spelled_absolutely",
        "E-absolute-outside": "path_outside_admitted_roots",
        "E-traversal": "path_not_confined_by_spelling",
        "E-gone": "top_level_entry_file_gone",
        "E-dependency": "dependency_source_not_ours",
    }
    assert reason_codes(tmp_path / "before-one") == expected
    assert reason_codes(tmp_path / "before-two") == expected

    # The cleanup a leaf's enclosure goes through deletes the report the citation names. Nothing
    # about the citation has changed and neither have the two trees, so nothing about its reason may
    # change either: the third-root answer comes from the directory the spelling belongs to, not from
    # the file's presence.
    (root / OUTSIDE_PATH).unlink()
    assert reason_codes(tmp_path / "after-file-deleted") == expected

    # The limit, stated rather than left to be discovered: if the third root's own top-level
    # directory disappears as well, the enclosure no longer holds anything the spelling could name,
    # and the reason falls back to the classification that needs no third root at all.
    shutil.rmtree(root / "notes")
    assert reason_codes(tmp_path / "after-directory-deleted")["E-third-root"] == (
        "dependency_source_not_ours"
    )


def test_a_symbol_locator_resolves_a_qualified_name_and_refuses_an_invented_prefix(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The model's field is called ``qualified_name``, so a qualified name has to resolve.

    A producer that spells the name the way the model's own field implies -- ``Holder.method`` --
    must not be told the file does not contain it. The resolution is by the name's two real halves:
    the last segment has to be defined, and every segment before it has to be defined too, which is
    what keeps the accepted form from becoming a licence for an invented prefix.
    """

    report = run(
        pair,
        tmp_path,
        [
            entry(
                "E-qualified",
                targets=[target(LEAF_ADDED_FILE, locator=symbol(f"Holder.{LEAF_ADDED_SYMBOL}"))],
            ),
            entry(
                "E-invented-prefix",
                targets=[target(LEAF_ADDED_FILE, locator=symbol(f"Nowhere.{LEAF_ADDED_SYMBOL}"))],
            ),
        ],
    )

    assert [one.entry_id for one in report.committed] == ["E-qualified"]
    committed = report.committed[0].targets[0]
    assert committed.locator == f"Holder.{LEAF_ADDED_SYMBOL}"

    database = tmp_path / "candidate" / "knowledge-candidate.sqlite"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        stored = connection.execute(
            "SELECT locator FROM source_anchor WHERE path = ?", (LEAF_ADDED_FILE,)
        ).fetchone()
    finally:
        connection.close()
    assert stored is not None
    assert json.loads(stored[0]) == {
        "kind": "symbol",
        "language": "python",
        "qualified_name": f"Holder.{LEAF_ADDED_SYMBOL}",
    }

    refused = by_id(report.refused)
    assert set(refused) == {"E-invented-prefix"}
    assert refused["E-invented-prefix"].refusal.startswith("symbol_not_a_definition")


def test_the_recorded_blob_identity_is_measured_and_a_working_edit_is_a_typed_refusal(
    tmp_path: Path,
) -> None:
    """The identity is read from the file and checked against the tree, so a mismatch is reportable.

    The resolution tree's membership answer is already known by the time the identity is read, so a
    run that derived the identity *from* that answer would be restating it: the observation could
    then only ever be ``exact_recorded_blob``, and the mismatch state would be unreachable from this
    caller. It is read from the working bytes instead, so the two can disagree -- and when they do
    the run refuses with ``recorded_blob_mismatch`` instead of raising or recording an identity the
    bytes do not have. This is what the ordinary case (both agree, identity exact) is measured
    against, and it is a separate enclosure because the case edits a cited file.
    """

    root = tmp_path / "enclosure"
    code_root = root / "code"
    memory_root = root / "memory"
    _write_files(code_root, {CODE_FILE: _CODE_TEXT})
    _write_files(memory_root, {f"onboarding/{MEMORY_CARD}": _MEMORY_CARD_TEXT})
    _git(code_root, ["init", "-q", "--initial-branch=main"])
    _git(memory_root, ["init", "-q", "--initial-branch=main"])
    code_commit = _commit(code_root, "code tree")
    memory_commit = _commit(memory_root, "memory tree")
    contract = root / "series-contract.md"
    layout = _Layout(
        root=root,
        code_root=code_root,
        memory_root=memory_root,
        task_root=root / "tasks" / "sprint",
    )
    contract.write_text(_contract_text(layout, code_commit, memory_commit), encoding="utf-8")
    committed_blob = _git(code_root, ["rev-parse", f"HEAD:{CODE_FILE}"])
    entries = [
        entry("E-exact", targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL))]),
    ]

    exact = ingest_curator_list(
        contract,
        entries,
        IngestSelection(
            candidate_directory=tmp_path / "exact",
            authorization_ref=AUTHORIZATION,
            dry_run=False,
        ),
    )
    assert [one.entry_id for one in exact.committed] == ["E-exact"]
    assert exact.counts.anchors_observed_exact == 1
    assert exact.committed[0].targets[0].source_identity == committed_blob

    # An uncommitted edit to the cited file: the working bytes are no longer the bytes the line
    # holds, so there is no committed identity to record and the run says which failure that is.
    original = (code_root / CODE_FILE).read_text(encoding="utf-8")
    (code_root / CODE_FILE).write_text(original + "\n\nedited after the line\n", encoding="utf-8")
    try:
        edited = ingest_curator_list(
            contract,
            entries,
            IngestSelection(
                candidate_directory=tmp_path / "edited",
                authorization_ref=AUTHORIZATION,
                dry_run=False,
            ),
        )
    finally:
        (code_root / CODE_FILE).write_text(original, encoding="utf-8")

    assert edited.committed == ()
    assert [one.entry_id for one in edited.refused] == ["E-exact"]
    assert edited.refused[0].refusal.startswith("recorded_blob_mismatch")
    assert edited.counts.records_written == 0


# --------------------------------------------------------------------------------------------
# The producer's bare symbol name, resolved into the model's locator
# --------------------------------------------------------------------------------------------


def test_a_producer_symbol_name_is_resolved_stored_and_read_back_typed(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A bare name becomes a two-part locator, and the read surface hands it back typed.

    The producers write ``{"kind": "symbol", "value": "<name>"}``; the model wants a language and a
    qualified name. Deriving those is curator work, and this case measures every step of it: the
    stored row, the view that renders it, and the rail that cannot resolve the kind.
    """

    report = run(
        pair,
        tmp_path,
        [
            entry(
                "E-symbol",
                targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL), route="pkg")],
            )
        ],
    )

    assert [one.state for one in report.committed] == [COMMITTED]
    committed = report.committed[0]
    assert committed.targets[0].locator_kind == "symbol"
    assert committed.targets[0].locator == CODE_SYMBOL
    # A symbol IS observed: the rail resolves it through the shipped extractor against the exact
    # recorded blob, which is the same answer a file locator earns and the answer that makes the
    # stored citation re-verifiable on the read path.
    assert committed.targets[0].observation == "exact_recorded_blob"
    assert report.counts.anchors_observed_exact == 1

    database = tmp_path / "candidate" / "knowledge-candidate.sqlite"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        stored = connection.execute(
            "SELECT locator FROM source_anchor WHERE path = ?", (CODE_FILE,)
        ).fetchone()
    finally:
        connection.close()
    assert stored is not None
    assert json.loads(stored[0]) == {
        "kind": "symbol",
        "language": "python",
        "qualified_name": CODE_SYMBOL,
    }

    # The mounted view decodes it, and hands back the union member rather than the stored text.
    context = open_read_context(database, report.repository_id)
    result = read_knowledge_view(
        database,
        context,
        ViewRequest(view="source_context", repository_id=report.repository_id),
    )
    assert result.state == "view"
    assert isinstance(result.payload, SourceContextView)
    rows = [row for row in result.payload.rows if row.path == CODE_FILE]
    assert len(rows) == 1
    assert isinstance(rows[0].locator, SymbolLocator), rows[0].locator
    assert rows[0].locator.qualified_name == CODE_SYMBOL
    assert rows[0].locator.language == "python"


def test_the_definition_check_refuses_a_mention_and_every_kind_of_non_definition(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A name that is *mentioned* is not a definition, and a symbol locator is never manufactured.

    The ingest verifies at ingest time that the named file **defines** the name, and the rail cannot
    re-verify a symbol kind afterwards (D-41), so this is the only check there is. Five shapes reach
    it here and each earns the reason that is true of it: a name in a docstring, a name in a comment,
    a name that is a substring of another definition, a name defined in a different file, and a name
    whose target carries no locator at all. A check that read the raw bytes would accept the first
    two -- which is exactly the fabrication this case exists to refuse.
    """

    report = run(
        pair,
        tmp_path,
        [
            entry(
                "E-docstring",
                targets=[target(MENTIONS_FILE, locator=symbol(MENTION_DOCSTRING_ONLY))],
            ),
            entry(
                "E-comment",
                targets=[target(MENTIONS_FILE, locator=symbol(MENTION_COMMENT_ONLY))],
            ),
            entry(
                "E-substring",
                # The file defines ``resolve_budget_v``; the citation names the name inside it.
                targets=[target(MENTIONS_FILE, locator=symbol(MENTION_SUBSTRING))],
            ),
            entry(
                "E-elsewhere",
                # ``other`` is defined in ``pkg/module.py``, not in the file this target names.
                targets=[target(LEAF_EDITED_FILE, locator=symbol("other"))],
            ),
            entry(
                "E-no-locator",
                # A target with no locator names a path without naming the construct inside it. It
                # is reported as the omission it is rather than promoted to a whole-file citation,
                # which Rule 1 forbids a producer from making by saying nothing.
                targets=[target(CODE_FILE)],
            ),
            entry(
                "E-prose",
                # A memory card is prose: no construct is defined in it, so no symbol can be cited
                # there at all. The symbol that a raw-bytes check accepts is the fabricated one.
                targets=[target(MEMORY_CARD, locator=symbol("module"))],
            ),
            entry(
                "E-toml",
                # A structured data file's key is a value's name, not a definition of anything.
                targets=[target(TOML_FILE, locator=symbol("unit_case_budget"))],
            ),
        ],
    )

    assert report.committed == ()
    refused = by_id(report.refused)
    assert set(refused) == {
        "E-docstring",
        "E-comment",
        "E-substring",
        "E-elsewhere",
        "E-no-locator",
        "E-prose",
        "E-toml",
    }
    # A name the file's bytes contain and its code does not define.
    for one in ("E-docstring", "E-comment", "E-substring"):
        assert refused[one].refusal.startswith("symbol_not_a_definition"), one
    # A name the file does not contain at all.
    assert refused["E-elsewhere"].refusal.startswith("construct_not_in_named_file")
    # A target that names no construct, and two file forms that cannot define one.
    assert refused["E-no-locator"].refusal.startswith("target_locator_missing")
    assert refused["E-prose"].refusal.startswith("symbol_target_is_prose")
    assert refused["E-toml"].refusal.startswith("symbol_target_is_prose")


def test_a_path_in_the_memory_root_carries_the_memory_tree_identity(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A memory card resolves through the second completion step and its blob is the memory tree's.

    The two roots hold different trees, so reading a memory path's identity out of the code tree
    would be the disagreement this case exists to catch.
    """

    report = run(
        pair,
        tmp_path,
        [
            entry(
                "E-memory",
                targets=[target(MEMORY_CARD, locator={"kind": "line_range", "start": 1, "end": 1})],
            )
        ],
    )

    assert [one.state for one in report.committed] == [COMMITTED]
    resolved = report.committed[0].targets[0]
    assert resolved.step == "memory-onboarding"
    assert resolved.completed_path == f"onboarding/{MEMORY_CARD}"
    assert resolved.source_identity == pair.memory_blobs[f"onboarding/{MEMORY_CARD}"]
    assert resolved.source_identity != pair.code_blobs.get(CODE_FILE)
    assert resolved.observation == "exact_recorded_blob"


# --------------------------------------------------------------------------------------------
# Identity, dry runs and the report
# --------------------------------------------------------------------------------------------


def test_identity_is_derived_from_the_enclosure_so_a_second_run_is_diagnosable(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The same list mints the same identities, and a re-run refuses instead of duplicating.

    A run that failed must still name what it would have written, and a second run must not file a
    second invariant under a fresh identity: both follow from deriving the ids rather than drawing
    them.
    """

    entries = [
        entry("E-derived", targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL), route="pkg")])
    ]
    first = run(pair, tmp_path, entries)
    first_anchor = first.committed[0].targets[0].source_identity
    assert first.batch_state == "changed"

    database = tmp_path / "candidate" / "knowledge-candidate.sqlite"
    before = counts(pair, database)
    assert before["invariant"] == 1

    second = run(pair, tmp_path, entries)
    # The batch is all-or-nothing, so a re-run that would re-assert the same rows refuses: the
    # second run is diagnosable, and the refusal names the precondition rather than a duplicate.
    assert second.batch_state == "refused"
    assert second.batch_refusal is not None
    assert second.batch_refusal.code == "stale_precondition"
    assert second.committed == ()
    assert {one.entry_id for one in second.refused} == {"E-derived"}
    assert second.refused[0].refusal.startswith("batch_stale_precondition")
    assert counts(pair, database) == before
    # The identity the first run wrote is the one the file's own bytes hash to, so the anchor
    # attributes the statement to the code it names rather than to a freshly drawn id.
    assert first_anchor == pair.code_blobs[CODE_FILE]


def test_dry_and_real_runs_report_the_same_written_rows_and_a_refused_run_reports_none(
    pair: SourcePair, tmp_path: Path
) -> None:
    """``records_written`` is what the run wrote, in both modes, and zero when it wrote nothing.

    Three facts share one subject here, which is why they are one case rather than three: the
    number's meaning. It has to agree between the dry run and the real one that follows it, it has
    to include the route leg's rows (which are not candidate commands and so are absent from the
    batch receipt), and a run that refused has to report zero rather than the rows it did not write.
    A count that failed any of the three would be unusable as the number a reader sizes work with.
    """

    entries = [
        entry("E-dry", targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL), route="pkg")]),
        entry("E-dry-ruling", kind="measurement", disposition="stated, not built"),
    ]
    dry = run(pair, tmp_path, entries, dry_run=True)

    assert dry.dry_run is True
    assert dry.batch_state == "dry_run_not_committed"
    assert dry.batch_refusal is None
    assert dry.batch_digest_before is None and dry.batch_digest_after is None
    # A dry run names both trees it read, exactly as the real run does: the report is the run's own
    # report with the commit withheld, so it cannot be vaguer about its inputs than the real one.
    assert dry.code_tree_id == pair.leaf_tree_id
    assert dry.code_base_commit == pair.code_commit
    assert [one.state for one in dry.committed] == [COMMITTED]
    assert dry.committed[0].targets[0].step == "code-tree"
    assert [one.state for one in dry.rulings] == [SKIPPED]
    # Four batch rows carry the entry (invariant, revision, anchor, claim), and the route leg adds
    # the scope's own row and the anchor's association: six rows, none of them written. The
    # association is counted because it is a row the real run writes -- a count that omitted the
    # state the association is recorded under would be five, which is the arithmetic defect pinned
    # here, and the database below holds exactly the six rows the count names.
    assert dry.counts.commands_sent == 4
    assert dry.counts.records_written == 6
    assert dry.counts.targets_completed == 1
    # Nothing was written, and no candidate directory was even created.
    assert not (tmp_path / "candidate").exists()

    # The real run carries exactly what the dry one said it would: the prediction is the report's
    # own counts, not a second estimate -- and the database holds that many rows.
    real = run(pair, tmp_path, entries)
    assert real.batch_state == "changed"
    assert real.counts.commands_sent == dry.counts.commands_sent
    assert real.counts.records_written == dry.counts.records_written == 6
    database = tmp_path / "candidate" / "knowledge-candidate.sqlite"
    written = counts(pair, database)
    assert (
        written["invariant"]
        + written["invariant_revision"]
        + written["source_anchor"]
        + written["realization_claim"]
        + written["route"]
        + written["source_anchor_route"]
    ) == real.counts.records_written

    # A refused re-run whose route leg had nothing to author wrote nothing at all, and says so: the
    # routes already exist, so reporting them as written would be reporting rows this run did not
    # write.
    refused = run(pair, tmp_path, entries)
    assert refused.batch_state == "refused"
    assert refused.committed == ()
    assert refused.counts.records_written == 0
    assert refused.counts.routes_authored == 0
    assert counts(pair, database) == written

    # But "the batch refused" is not the same claim as "this run wrote nothing", and conflating the
    # two is the defect pinned next: a route leg runs BEFORE the batch, so a scope it authored is a
    # row in the candidate even when the batch then refuses. The count is over rows the run wrote,
    # not over citations it rewrote -- the citation claim is carried by ``committed`` and the batch
    # state -- and a report saying ``records_written == 0`` while the candidate holds that row is a
    # count of nothing. ``written`` is asserted directly here because the reachable end-to-end shape
    # needs a route whose scope changed between runs, which this fixture's shared source tree cannot
    # produce without a second candidate.
    ledger = _RouteLedger(
        expected={"pkg": ""},
        answered={"pkg": ""},
        attached={},
        authored={"pkg"},
    )
    assert _Run(batch_state="not_attempted", ledger=ledger).written == 1
    assert _Run(batch_state="not_attempted").written == 0


def test_an_empty_authorization_is_refused_by_name_before_anything_is_read(
    pair: SourcePair, tmp_path: Path
) -> None:
    """An admitted write needs its authorization, and a blank one names itself.

    ``Authorship`` refuses a blank reference, so the operation validates it up front: a caller that
    omitted it gets a sentence naming the missing authorization rather than a model error from deep
    inside the envelope.
    """

    with pytest.raises(ValueError, match="authorization_ref"):
        ingest_curator_list(
            pair.contract_path,
            [entry("E-unused")],
            IngestSelection(
                candidate_directory=tmp_path / "candidate",
                authorization_ref="   ",
                dry_run=False,
            ),
        )
    assert not (tmp_path / "candidate").exists()


# --------------------------------------------------------------------------------------------
# The route leg: the one citation element the closed command union cannot carry
# --------------------------------------------------------------------------------------------


def test_the_governing_route_is_recorded_once_per_scope_and_attached_to_each_anchor(
    pair: SourcePair, tmp_path: Path
) -> None:
    """Every route the list names costs one row, and each governed anchor carries its own.

    ``author_route`` is idempotent by path and ``set_governing_route`` allows one route per governed
    row, so many places naming one scope must produce one route and many associations -- and an
    entry that names no route must stay explicitly ungoverned rather than inherit one.
    """

    report = run(
        pair,
        tmp_path,
        [
            entry(
                "E-governed-a",
                targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL), route="pkg")],
            ),
            entry(
                "E-governed-b",
                targets=[
                    target("pkg/batch.py", locator={"kind": "file"}, route="pkg"),
                    target(CODE_FILE, locator={"kind": "file"}),
                ],
            ),
        ],
    )

    assert {one.state for one in report.committed} == {COMMITTED}
    assert report.counts.route_paths == 1
    assert report.counts.routes_authored == 1
    database = tmp_path / "candidate" / "knowledge-candidate.sqlite"
    written = counts(pair, database)
    assert written["route"] == 1
    assert written["source_anchor_route"] == 2
    assert written["source_anchor"] == 3

    states = [
        (target_outcome.route_path, target_outcome.route_state)
        for outcome in report.committed
        for target_outcome in outcome.targets
    ]
    assert states == [("pkg", "authored"), ("pkg", "authored"), (None, "ungoverned")]

    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        associated = connection.execute(
            "SELECT route_id, count(*) FROM source_anchor_route GROUP BY route_id"
        ).fetchall()
        ungoverned = connection.execute(
            "SELECT count(*) FROM source_anchor a WHERE NOT EXISTS "
            "(SELECT 1 FROM source_anchor_route r WHERE r.anchor_id = a.anchor_id)"
        ).fetchone()[0]
    finally:
        connection.close()
    assert len(associated) == 1 and associated[0][1] == 2
    assert ungoverned == 1


def test_re_authoring_an_existing_route_reuses_its_row_rather_than_writing_a_second(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A route already authored for a scope is reused, which is what keeps one scope one route.

    ``author_route`` is idempotent by path and ``set_governing_route`` is idempotent for the same
    association, so the same route leg is safe to repeat -- and the report says which leg answered
    rather than calling every outcome "authored".
    """

    first = run(
        pair,
        tmp_path,
        [entry("E-first", targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL), route="pkg")])],
    )
    assert first.counts.routes_authored == 1

    # A second, different entry naming the same scope: the route row already exists.
    second = run(
        pair,
        tmp_path,
        [
            entry(
                "E-second", targets=[target("pkg/batch.py", locator={"kind": "file"}, route="pkg")]
            )
        ],
    )
    assert second.counts.route_paths == 1
    assert second.counts.routes_authored == 0
    assert second.counts.routes_reused == 1
    assert second.committed[0].targets[0].route_state == "reused"

    database = tmp_path / "candidate" / "knowledge-candidate.sqlite"
    assert counts(pair, database)["route"] == 1


# --------------------------------------------------------------------------------------------
# What the report names: the candidate directory, its receipt, the lane and the trees
# --------------------------------------------------------------------------------------------


def test_the_report_names_the_candidate_its_receipt_the_lane_and_the_exact_inputs(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The run is auditable after the fact: where it committed, under which admitted inputs.

    The candidate directory is a parameter and not a convention, so the report has to name it, and
    the receipt is the object that binds that directory to the trees the citations were read from.
    """

    report = run(
        pair,
        tmp_path,
        [entry("E-named", targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL), route="pkg")])],
    )

    candidate = tmp_path / "candidate"
    assert report.candidate_directory == str(candidate)
    # The receipt path is the one place a report names a file it did not write, so the field is
    # narrowed before it is used as a path: an absent receipt has to fail the equality above rather
    # than reach ``Path`` as ``None`` and turn a missing receipt into a type error.
    receipt_path = report.candidate_receipt
    assert isinstance(receipt_path, str)
    assert receipt_path == str(candidate / "candidate-receipt.json")
    assert Path(receipt_path).is_file()
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    assert receipt["lane"] == report.lane == "draft-candidate"
    # The tree the citations were read from is the leaf's own line, and the report says where that
    # tree came from beside the base the enclosure recorded -- the two facts a reader needs to tell
    # a run that resolved the leaf's work from one that fell back to the tree it was cut from.
    assert receipt["code"]["tree_id"] == report.code_tree_id == pair.leaf_tree_id
    assert report.code_tree_id != pair.code_tree_id
    # A commit is not a tree, and the two facts are reported side by side: the tree the citations
    # were read from is the line's, and the recorded base commit is the one the enclosure named.
    assert report.code_base_commit == pair.code_commit
    assert report.code_tree_source.startswith("work-line:")
    assert receipt["memory"]["tree_id"] == report.memory_tree_id == pair.memory_tree_id
    assert receipt["repository_id"] == report.repository_id
    assert report.derived_identities
    assert report.entries_read == ("E-named",)


# --------------------------------------------------------------------------------------------
# The production caller: the operator's own entry point, driven end to end
# --------------------------------------------------------------------------------------------


def _cli_argv(argv: list[str], candidate: Path) -> list[str]:
    """The same shipped invocation aimed at another candidate directory.

    The case below has to run the command more than once, and a second run over a candidate that
    already holds the list is a refused batch -- so each of its runs needs a candidate of its own,
    and rewriting only that one argument is what keeps the rest of the invocation identical.
    """

    index = argv.index("--candidate-directory")
    return [*argv[:index], "--candidate-directory", str(candidate), *argv[index + 2 :]]


def test_the_cli_subcommand_is_a_production_caller_that_writes_the_rows(
    pair: SourcePair, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ingest has an operator-reachable entry point, and it is the one that writes.

    Every case above calls the operation **in process**, which proves the operation works and
    proves nothing about whether anything can reach it. This case drives the shipped command line
    instead -- ``agents-remember knowledge-ingest`` through the umbrella ``main``, exactly as an
    operator runs it -- over the same real enclosure, and then reads the candidate database back
    to show that rows exist because of that run.

    The exit code, the printed report, the row counts and the dry-run direction are all measured,
    because "reachable" is a claim about the whole path: an entry point that exists but writes
    nothing, or that writes without saying so, would still leave the write plane unreachable.
    """

    list_path = tmp_path / "hand-off-list.json"
    list_path.write_text(
        json.dumps(
            [
                entry(
                    "E-cli",
                    targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL), route="pkg")],
                ),
                entry("E-cli-ruling", kind="decision", disposition="nothing to do"),
            ]
        ),
        encoding="utf-8",
    )
    candidate = tmp_path / "cli-candidate"
    argv = [
        "knowledge-ingest",
        "--contract",
        str(pair.contract_path),
        "--list",
        str(list_path),
        "--candidate-directory",
        str(candidate),
        "--authorization-ref",
        AUTHORIZATION,
    ]

    # The dry run is the default: the command reports and writes nothing, so there is no candidate
    # database to count rows in afterwards. That is the difference the operator's commit word buys.
    assert main(argv) == 0
    assert not (candidate / "knowledge-candidate.sqlite").exists()

    assert main([*argv, "--commit"]) == 0
    database = candidate / "knowledge-candidate.sqlite"
    assert database.is_file()
    written = counts(pair, database)
    assert written["source_anchor"] == 1
    assert written["realization_claim"] == 1
    assert written["route"] == 1
    # The stored anchor is the co-resolved pair the operator's list named: the path the resolver
    # chose, and the symbol the shipped extractor bound inside those exact recorded bytes.
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        anchor = connection.execute("SELECT path, locator FROM source_anchor").fetchone()
    finally:
        connection.close()
    assert anchor is not None
    assert anchor[0] == CODE_FILE
    assert json.loads(anchor[1]) == {
        "kind": "symbol",
        "language": "python",
        "qualified_name": CODE_SYMBOL,
    }

    # PUBLICATION IS REACHABLE FROM THE SAME COMMAND LINE, and it publishes the candidate this very
    # run committed. The destination is admitted ABSENT, which is the first publication into a
    # worktree; the report names the state and the destination it reached, so an operator learns that
    # the dataset moved rather than inferring it from a file that appeared. Each run below gets its
    # own candidate directory, because a second run over a candidate that already holds the list is a
    # refused batch and a refused batch publishes nothing.
    published = tmp_path / "memory" / "knowledge.sqlite"
    published.parent.mkdir()
    publishing = _cli_argv(argv, tmp_path / "publish-candidate")
    capsys.readouterr()
    assert main([*publishing, "--commit", "--publish-to", str(published), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["publication"]["state"] == "published"
    assert payload["publication"]["destination_ref"] == str(published)
    assert published.is_file()

    # A PLANNING RUN PUBLISHES NOTHING even when a destination is named: with no committed batch
    # there is nothing to publish, and no file appears.
    never = tmp_path / "memory" / "never.sqlite"
    assert main([*publishing, "--publish-to", str(never)]) == 0
    assert not never.exists()

    # A refused invocation is its own exit code and writes nothing: an unreadable list is refused
    # before the contract is even loaded, so no candidate directory is created at all.
    refused_candidate = tmp_path / "refused-candidate"
    absent_argv = [
        *argv[: argv.index("--list")],
        "--list",
        str(tmp_path / "absent.json"),
        "--candidate-directory",
        str(refused_candidate),
        "--authorization-ref",
        AUTHORIZATION,
        "--commit",
    ]
    assert main(absent_argv) == 2
    assert not refused_candidate.exists()

    # The other half of the same claim: the one MOUNTED mutation name is not a write path, and it
    # says so rather than implying one. ``knowledge_change`` is published, so a caller reaching for
    # it must get a typed refusal rather than "no such tool" -- but its description used to advertise
    # that it records while every kind returned a refusal telling the caller to supply an argument
    # its signature cannot carry. The description and the refusal now agree with the code, and both
    # name the entry point that does write, which is the subcommand driven above.
    assert DECLARED_CHANGE_KINDS, "the surface declares the kinds it may be asked about"
    for kind in (*DECLARED_CHANGE_KINDS, "census_claim"):
        body = knowledge_change_payload(
            ChangeToolRequest(
                database_path=str(tmp_path / "knowledge.sqlite"),
                repository_id="repo",
                record_kind=kind,
            )
        )
        assert body["ok"] is True
        assert body["state"] == "refused", body
        assert body["refusalCode"] == "registration_absent", body
        assert body["recordKind"] == kind, body
        assert WRITE_ENTRY_POINT in body["refusalDetail"], body


# --------------------------------------------------------------------------------------------
# CYCLE-01 — the repository namespace is a STORED identity, not a function of the code baseline.
#
# The defect this class seals: `_repository_identity` derived the namespace UUID from
# `contract.code_base_commit` (via `_enclosure`, knowledge_curator_ingest.py:921). The model's own
# contract (models/knowledge/repository.py) says a namespace "is not a filesystem root, a branch
# name or a repository display name: those all change while the knowledge they scope does not, and
# a namespace that moved with them would silently re-scope every revision underneath it". A code
# base commit is exactly such a value. So one repository ingested at a later baseline was handed a
# different namespace, its candidate held only the new entry, and the prior invariant was stranded
# under a namespace nothing would look in again.
#
# With no candidate and no baseline to read from there is nothing to inherit, and the operation
# must still answer -- that is the cold-start path, which derives and says so.
# --------------------------------------------------------------------------------------------
def _cycle01_contract(root: Path, *, code: str, memory: str, repo_name: str = "agents-remember"):
    """One enclosure contract that differs from its siblings in nothing but the recorded baseline.

    Nothing here touches the filesystem: the identity under test is a property of the contract's
    recorded facts, not of the trees those facts name.
    """

    return WorktreeContract(
        task_id="260915_TEST",
        task_name="cycle01-identity",
        repo_name=repo_name,
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=root,
        task_root=root / "tasks",
        contract_path=root / "tasks" / "contract.md",
        task_artifact=root / "tasks" / "task.md",
        worktree_group=root / "worktrees",
        code_repo_path=root / "code",
        code_source_branch="line",
        code_work_branch="ar/cycle01",
        code_base_commit=code,
        code_worktree=root / "worktrees" / "code",
        memory_repo_path=root / "memory",
        memory_source_branch="line",
        memory_work_branch="ar/cycle01",
        memory_base_commit=memory,
        memory_worktree=root / "worktrees" / "memory",
        leaf_id="260915-TEST-L1",
    )


def _cycle01_command_kinds(case, *, declares: bool) -> list[str]:
    """The command names one curator entry contributes, declared or revised."""

    return [
        type(command).__name__
        for command in curator_entry_commands(
            case.write_destination(),
            CuratorEntry(
                invariant_id=str(uuid4()),
                display_label="the obligation",
                revision_id=str(uuid4()),
                display_version="v2" if not declares else "v1",
                statement="the obligation",
                applicability="Every admitted candidate write in this namespace.",
                predecessors=() if declares else (str(uuid4()),),
                declares_invariant=declares,
            ),
        )
    ]


def _cycle01_forked_candidate(tmp_path: Path, case) -> tuple[str, Path]:
    """Fork one baseline into an absent candidate and return the prior invariant and the copy."""

    prior_invariant, written = write_record(case, "the prior obligation")
    assert written.state == "changed", written
    assert written.after is not None, "the baseline write returned no resulting identity"
    target = tmp_path / "forked-candidate"
    admission = _admitted_candidate(
        target, case.repository, case.resolution, baseline=case.database_path
    )
    assert admission.state == "created", admission
    return prior_invariant, target


def _cycle01_candidate_rows(database: Path) -> tuple[set[str], set[str]]:
    """The invariant and revision identities one candidate database actually holds."""

    connection = open_read_only_database(database)
    try:
        return (
            {str(row[0]) for row in connection.execute("SELECT invariant_id FROM invariant")},
            {
                str(row[0])
                for row in connection.execute("SELECT revision_id FROM invariant_revision")
            },
        )
    finally:
        connection.close()


class RepositoryIdentityStabilityTests:
    """CYCLE-01 — repository knowledge is continuous across tasks and baselines.

    Seven cases became one. Each pair below was two assertions about a single rule, so they were
    merged rather than kept as separate functions: the rule is what is protected, and the unit
    ceiling is a hard 2300 that this change set had already breached. Every original assertion
    survives -- what is gone is only the separate test function wrapped around each one. The
    ``CYCLE-01``-marked steps on ``260915-KS-L30`` record which assertion came from which finding.
    """

    def test_repository_knowledge_continues_across_baselines_and_tasks(
        self, tmp_path: Path
    ) -> None:
        """Identity, its storage, the fork, the successor path, and multiple anchors per file.

        (a) The namespace belongs to the repository, not the baseline it is read at. The old
            derivation keyed on the enclosure's code base commit, so one repository read at two
            baselines produced two namespaces -- and two repositories sharing a base commit produced
            the SAME one. ``models/knowledge/repository.py``: a namespace "is not a filesystem root, a
            branch name or a repository display name: those all change while the knowledge they scope
            does not".
        (b) It is STORED, and read before it is ever derived. A real candidate database carrying a
            namespace unrelated to anything derivable from the contract must win, so agreement can
            only come from having read the row; and an unreadable baseline is an ordinary input for
            an operation allowed to create one, so it falls back rather than raising. The fallback's
            limit is asserted honestly: the contract carries no stable repository key, so a derived
            value cannot tell two repositories apart -- the stored row does that.
        (c) An absent candidate FORKS the selected baseline instead of starting empty, which is the
            continuity property itself: an empty candidate holds only the new task's entry, and the
            run that follows cannot see knowledge the repository already recorded.
        (d) An entry declares a new obligation or names the one it revises. The adapter always emitted
            ``AddInvariant``, and the batch's precondition for that command is that the invariant is
            ABSENT, so a changed statement was refused with ``batch_stale_precondition``: the
            repository could accumulate obligations and never evolve one. Naming the invariant is the
            other half, because a derived id is per-enclosure and cannot name an existing obligation.
        (e) A file may carry more than one anchor. The refusal was ``duplicate_target_path``, and the
            guard was telling the truth: the anchor identity keyed on the path and the locator KIND
            and never on which construct was named. The cure is in the identity, and the guard's own
            purpose is asserted intact rather than weakened away.
        """

        # (a) one namespace per repository, across baselines
        first = _repository_identity(_cycle01_contract(tmp_path, code="a" * 40, memory="c" * 40))
        later = _repository_identity(_cycle01_contract(tmp_path, code="b" * 40, memory="d" * 40))
        assert first.repository_id == later.repository_id, (
            "one repository read at two code baselines produced two namespaces, so the knowledge "
            "recorded at the first baseline is unreachable from the second"
        )
        assert first.authority_home == "agents-remember"

        # (b) the stored namespace wins; an unreadable or absent baseline still answers
        case = build_case(tmp_path / "baseline")
        assert create(case).state == "created", "the fixture did not produce a real database"
        contract = _cycle01_contract(tmp_path, code="a" * 40, memory="c" * 40)
        stored = _repository_identity(contract, case.database_path)
        assert stored.repository_id == case.repository.repository_id, (
            "the operation answered with a derived namespace instead of the one the dataset records"
        )
        assert stored.authority_home == case.repository.authority_home
        nonsense = tmp_path / "not-a-database.sqlite"
        nonsense.write_text("this is not sqlite\n", encoding="utf-8")
        assert (
            _repository_identity(contract, nonsense).repository_id
            == _repository_identity(contract).repository_id
        )

        # (c) an absent candidate forks the selected baseline and carries its invariant
        prior_invariant, target = _cycle01_forked_candidate(tmp_path, case)
        forked = candidate_database_path(target)
        assert forked.is_file(), "no candidate database was produced"
        invariants, revisions = _cycle01_candidate_rows(forked)
        assert prior_invariant in invariants, (
            "the forked candidate does not carry the baseline's invariant, so the next task starts "
            "blind to knowledge the repository already recorded"
        )
        assert revisions, "the forked candidate carries no revision at all"

        # (d) declaring versus revising, and the command list a commit is built from
        named = "22222222-2222-2222-2222-222222222222"
        predecessor = "33333333-3333-3333-3333-333333333333"
        declared_fields = _EntryFields.read({"id": "E1", "statement": "a first obligation"})
        assert declared_fields.declares_invariant is True
        assert declared_fields.predecessors == ()
        assert declared_fields.named_invariant_id is None
        successor_fields = _EntryFields.read(
            {
                "id": "E1",
                "statement": "the revised obligation",
                "invariant_id": named,
                "predecessor_revision_ids": [predecessor],
            }
        )
        assert successor_fields.declares_invariant is False, (
            "an entry naming a predecessor revision must not re-declare its invariant; the batch "
            "refuses to create an invariant that already exists"
        )
        assert successor_fields.predecessors == (predecessor,)
        assert successor_fields.named_invariant_id == named
        blank = _EntryFields.read(
            {"id": "E1", "statement": "x", "predecessor_revision_ids": ["", "   "]}
        )
        assert blank.declares_invariant is True
        assert blank.predecessors == ()

        successor_kinds = _cycle01_command_kinds(case, declares=False)
        assert "AddInvariant" not in successor_kinds, (
            "a successor re-declared its invariant; the batch refuses to create an invariant that "
            "already exists, which is the batch_stale_precondition the review reproduced"
        )
        assert "AddInvariantRevision" in successor_kinds
        declared_kinds = _cycle01_command_kinds(case, declares=True)
        assert "AddInvariant" in declared_kinds
        assert "AddInvariantRevision" in declared_kinds

        # (e) several anchors in one file, with the duplicate guard still doing its job
        resolved = _Resolved(
            step="own-line", path=CODE_FILE, blob="a" * 40, root=tmp_path, tree_id="t"
        )
        alpha = _observation_id(resolved, SymbolLocator(language="python", qualified_name="alpha"))
        beta = _observation_id(resolved, SymbolLocator(language="python", qualified_name="beta"))
        assert alpha != beta, (
            "two different symbols in one file still share an anchor identity, so the second is "
            "refused as a duplicate of the first"
        )
        assert alpha == _observation_id(
            resolved, SymbolLocator(language="python", qualified_name="alpha")
        )
