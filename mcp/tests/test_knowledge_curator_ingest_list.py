"""The curator's reachable ingest: one hand-off list becomes one admitted batch and one report.

The write half already has its own cases (``test_knowledge_curator_ingest.py``), so nothing here
re-protects a citation round trip. What this module adds is four operations those cases cannot
reach, and each case below measures one of them:

* **the three outcomes in one run**, on one list: an entry whose citation resolved is committed, a
  ruling that carries no target is ``skipped`` with its own verdict carried, and a target that does
  not resolve is ``refused`` with the reason that distinguishes the three ways it can fail -- a path
  outside the two admitted roots, a real top-level entry whose file is gone, and a construct that is
  not in the named file;
* **the whole path of a producer's bare symbol name**: derived language, the stored two-part
  locator, the read surface handing it back typed, and the rail refusing to resolve it;
* **identity derived from the enclosure**, so a re-run mints the same invariant rather than a second
  one, and a run that fails still names what it would have written;
* **the route leg**, which is the one part of the citation the closed command union cannot carry.

The fixture is a real pair of Git repositories beside a real contract, because the ingest resolves
paths through a recorded tree and derives each blob identity from that tree's own membership answer:
a synthetic path table would measure the fixture instead of the resolver.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_curator_ingest import (
    COMMITTED,
    SKIPPED,
    IngestReport,
    ingest_curator_list,
)
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.application.knowledge_views import read_knowledge_view
from agents_remember.models.knowledge.source import SymbolLocator
from agents_remember.models.knowledge.view import SourceContextView, ViewRequest

pytestmark = pytest.mark.evidence_unit

AUTHORIZATION = "authorization:ks-l25-ingest-case"
CODE_FILE = "pkg/module.py"
CODE_SYMBOL = "resolve_budget"
MEMORY_CARD = "pkg/module.md"
GONE_PATH = "pkg/retired_module.py"
OUTSIDE_PATH = "notes/reports/terminal-report.md"
DEPENDENCY_PATH = "vendor/third_party.py"

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


@pytest.fixture(scope="session")
def pair(tmp_path_factory: pytest.TempPathFactory) -> SourcePair:
    """The session's repositories, built once because no case mutates them.

    Every case writes into its own candidate directory under its own ``tmp_path``, so the sources
    can be shared: a case that mutated these bytes would be measuring a different tree than the
    contract records, which is exactly what the resolver refuses.
    """

    root = tmp_path_factory.mktemp("curator-ingest")
    code_root = root / "code"
    memory_root = root / "memory"
    task_root = root / "tasks" / "sprint"
    _write_files(
        code_root,
        {
            CODE_FILE: _CODE_TEXT,
            "pkg/batch.py": "# batch\none transaction\n",
        },
    )
    _write_files(
        memory_root,
        {
            f"onboarding/{MEMORY_CARD}": _MEMORY_CARD_TEXT,
            "onboarding/overview.md": "# onboarding overview\n",
        },
    )
    # The third root. It is a real file the producer can see, and the citation machinery admits
    # exactly two roots, so it is refused as out of scope rather than as gone.
    _write_files(task_root, {OUTSIDE_PATH: "# terminal report\nlanded\n"})
    _git(code_root, ["init", "-q", "--initial-branch=main"])
    _git(memory_root, ["init", "-q", "--initial-branch=main"])
    code_commit = _commit(code_root, "code tree")
    memory_commit = _commit(memory_root, "memory tree")
    contract = root / "series-contract.md"
    layout = _Layout(root=root, code_root=code_root, memory_root=memory_root, task_root=task_root)
    contract.write_text(_contract_text(layout, code_commit, memory_commit), encoding="utf-8")
    return SourcePair(
        code_root=code_root,
        memory_root=memory_root,
        contract_path=contract,
        task_root=task_root,
        code_tree_id=_git(code_root, ["rev-parse", "HEAD^{tree}"]),
        memory_tree_id=_git(memory_root, ["rev-parse", "HEAD^{tree}"]),
        code_blobs={
            path: _git(code_root, ["rev-parse", f"HEAD:{path}"])
            for path in (CODE_FILE, "pkg/batch.py")
        },
        memory_blobs={
            path: _git(memory_root, ["rev-parse", f"HEAD:{path}"])
            for path in (f"onboarding/{MEMORY_CARD}", "onboarding/overview.md")
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
        candidate_directory=tmp_path / "candidate",
        authorization_ref=AUTHORIZATION,
        dry_run=dry_run,
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
            entry(
                "E-not-in-file",
                targets=[target("pkg/batch.py", locator=symbol("resolve_budget"))],
            ),
        ],
    )

    assert report.counts.entries_read == 6
    assert {one.state for one in report.committed} == {COMMITTED}
    assert [one.entry_id for one in report.committed] == ["E-committed"]
    assert [one.entry_id for one in report.rulings] == ["E-ruling"]
    assert {one.entry_id for one in report.refused} == {
        "E-gone",
        "E-outside",
        "E-dependency",
        "E-not-in-file",
    }
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
    assert refused["E-not-in-file"].refusal.startswith("construct_not_in_named_file")
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
    # A symbol is observed, and the rail refuses the kind rather than resolving it as a file.
    assert committed.targets[0].observation == "unsupported_locator"
    assert report.counts.anchors_observed_unsupported == 1

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


def test_the_definition_check_refuses_a_name_the_named_file_does_not_define(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A name the file merely mentions is refused, so a symbol is never cited wherever it landed.

    The ingest verifies at ingest time that the named file *defines* the name. The rail cannot
    re-verify a symbol kind (D-41), so this is the only check there is, and a name that is only
    mentioned -- or that lives in another file -- must refuse rather than commit.
    """

    report = run(
        pair,
        tmp_path,
        [
            entry(
                "E-mentioned",
                # ``attempt`` occurs in the file's bytes but is not defined there.
                targets=[target(CODE_FILE, locator=symbol("attempt"))],
            ),
            entry(
                "E-elsewhere",
                # ``other`` is defined in ``pkg/module.py``, not in the file this target names.
                targets=[target("pkg/batch.py", locator=symbol("other"))],
            ),
        ],
    )

    assert report.committed == ()
    assert {one.entry_id for one in report.refused} == {"E-mentioned", "E-elsewhere"}
    for outcome in report.refused:
        assert outcome.refusal.startswith("construct_not_in_named_file")


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


def test_a_dry_run_reports_what_would_happen_and_writes_nothing(
    pair: SourcePair, tmp_path: Path
) -> None:
    """A dry run is the run's own report with the commit withheld, and no byte on disk.

    The report must not read as an operation that failed to reach its batch: the entry outcomes and
    the counts are what the batch *would* carry, and the state says the commit was withheld.
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
    assert [one.state for one in dry.committed] == [COMMITTED]
    assert dry.committed[0].targets[0].step == "code-tree"
    assert [one.state for one in dry.rulings] == [SKIPPED]
    # Four batch commands carry the entry (invariant, revision, anchor, claim), and the route leg
    # adds the scope's own row and the anchor's association: six rows, none of them written.
    assert dry.counts.commands_sent == 4
    assert dry.counts.records_written == 6
    assert dry.counts.targets_completed == 1
    # Nothing was written, and no candidate directory was even created.
    assert not (tmp_path / "candidate").exists()

    # The real run carries exactly what the dry one said it would: the prediction is the report's
    # own counts, not a second estimate.
    real = run(pair, tmp_path, entries)
    assert real.batch_state == "changed"
    assert real.counts.commands_sent == dry.counts.commands_sent
    assert real.counts.records_written == dry.counts.records_written


def test_every_empty_target_is_skipped_and_its_producer_kind_travels_with_it(
    pair: SourcePair, tmp_path: Path
) -> None:
    """The empty target is read as the ruling it is, whatever ``kind`` the producer wrote.

    The contract describes the honest encoding as ``decision`` or ``measurement``, and the fixture's
    eight rulings measure a wider hand than that: six are ``finding`` or ``carried-defect``. Refusing
    one for wearing the wrong kind would lose the verdict the producer authored, so the kind travels
    into the report beside the disposition instead of gating the read.
    """

    report = run(
        pair,
        tmp_path,
        [
            entry("E-clause", kind="clause", disposition="nothing to do"),
            entry("E-finding", kind="finding", disposition="discharged"),
        ],
    )

    assert report.refused == ()
    assert report.committed == ()
    assert [one.state for one in report.rulings] == [SKIPPED, SKIPPED]
    assert [(one.entry_id, one.kind) for one in report.rulings] == [
        ("E-clause", "clause"),
        ("E-finding", "finding"),
    ]
    assert [one.disposition for one in report.rulings] == ["nothing to do", "discharged"]


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
            candidate_directory=tmp_path / "candidate",
            authorization_ref="   ",
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
    assert report.candidate_receipt == str(candidate / "candidate-receipt.json")
    assert Path(report.candidate_receipt).is_file()
    receipt = json.loads(Path(report.candidate_receipt).read_text(encoding="utf-8"))
    assert receipt["lane"] == report.lane == "draft-candidate"
    assert receipt["code"]["tree_id"] == report.code_tree_id == pair.code_tree_id
    assert receipt["memory"]["tree_id"] == report.memory_tree_id == pair.memory_tree_id
    assert receipt["repository_id"] == report.repository_id
    assert report.derived_identities
    assert report.entries_read == ("E-named",)
