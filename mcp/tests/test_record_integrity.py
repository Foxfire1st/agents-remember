"""Record-integrity checks, each proved against the historical artifact it was written for.

The subject is the drift shape `260918-TSIP-L2` records: a document that was true when written and
silently stopped being true. Every case below therefore runs in **both** directions — the shipped
function must FAIL on the artifact as it stood when the drift was live, and PASS on the artifact
that replaced it. A check with only the refusal direction cannot be shown to be non-vacuous, which
is the fault the sibling module in this package exists to catch.

Three artifacts are read from the record itself rather than reconstructed, and the paths are named
in each case:

* `260712_task-reader-body-priority-rc5` — seven leaves, every contract `completed/completed`, every
  document still `planning` or `inProgress`, and a master whose rows read `Completed` over them.
  This is the historical case for requirement 4 and for `D42`'s class.
* `260918_tool-surface-and-process-integrity` — this master, whose contracts and documents agree.
  Every growing part of it is read through a frozen snapshot rather than the live file. The register
  was the first: a case whose expected disagreement count is the live file's defect count dies the
  moment that defect is repaired — which is exactly what happened to `R11`'s six `→ L20` arrows
  between the first run of this suite and its review. The leaf population, the leaf statuses and the
  master's rows were the same fault one class over (`T51`): a sibling leaf opening adds an enclosure,
  a document and a row, and a sibling document moving moves a status, so a case asserting any of
  those was asserting the world. `_frozen_leaf_snapshot` writes them instead.
* `260915-CAPS-L1`'s own memory prose (`T45`) — the two route documents that stated
  `instrument_discipline.py` at 361 lines and `test_instrument_discipline.py` at 350 lines / 16
  cases while the repaired sources carried 432 / 537 / 27. They are read **by Git object** at
  `e116e5ee`, the revision that committed them, so the artifact cannot move under the case.

The control worlds are built by **copying** those task roots into a temporary directory, so the real
coordination tree is only ever read. When the tree is not reachable the cases that need it are
skipped with the reason named, rather than passing on a fixture that no longer resembles the
artifact.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest
from agents_remember_test_support.code_quality import record_integrity as integrity

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The historical case for the leaf/contract comparison and for D42's class.
HISTORICAL_MASTER = "260712_task-reader-body-priority-rc5"
# This master: contracts and documents agree, and its register is read as a frozen snapshot.
CURRENT_MASTER = "260918_tool-surface-and-process-integrity"
# The leaf population `_frozen_leaf_snapshot` declares for the three "this master is clean" cases,
# as (leaf id, document stem, the status the fixture writes for the document and its row alike).
# The live master is a *growing* record, so these numbers are the case's own rather than the
# world's: the next leaf to open adds a fourth enclosure and the ninth row, and nothing here moves.
DECLARED_LEAVES: tuple[tuple[str, str, str], ...] = (
    ("260918-TSIP-L1", "1_instrument-integrity", "Completed"),
    ("260918-TSIP-L2", "2_record-integrity", "Completed"),
)
DECLARED_LEAF_COUNT = len(DECLARED_LEAVES)
# The historical case for the prose-figure comparison: L1's two route documents at the revision
# that recorded the stale figures, and the sources they describe.
T45_FREEZE = "e116e5ee"
T45_ROUTE_DOCUMENT = (
    "onboarding/mcp/test_support/agents_remember_test_support/code_quality/overview.md"
)
T45_SUITE_DOCUMENT = "onboarding/mcp/tests/overview.md"
INSTRUMENT_MODULE = (
    REPO_ROOT
    / "mcp/test_support/agents_remember_test_support/code_quality/instrument_discipline.py"
)
INSTRUMENT_SUITE = REPO_ROOT / "mcp/tests/test_instrument_discipline.py"
# The two labels are spelled so neither can match the tail of the other's name: a bare
# `instrument_discipline\.py` matches inside `test_instrument_discipline.py`, which would credit the
# suite's own figure to the module the suite tests.
INSTRUMENT_LABEL = r"(?<![\w.])instrument_discipline\.py"
SUITE_LABEL = r"test_instrument_discipline\.py"


def _coordination_root() -> pathlib.Path:
    """The coordination root the session was told about, or a skip with the reason named."""
    configured = os.environ.get(integrity.COORDINATION_ROOT_ENV)
    if configured:
        root = pathlib.Path(configured).expanduser()
    else:
        root = REPO_ROOT.parents[1] / "ar-coordination"
    if not (root / "tasks").is_dir():
        pytest.skip(
            f"no coordination tree at {root}; set {integrity.COORDINATION_ROOT_ENV} to a "
            f"coordination root to run the historical controls"
        )
    return root


def _task_root(coordination_root: pathlib.Path, master: str) -> pathlib.Path:
    root = coordination_root / "tasks" / "agents-remember" / master
    if not root.is_dir():
        pytest.skip(f"the historical artifact {master} is not present under {coordination_root}")
    return root


def _control_world(destination: pathlib.Path, *masters: str) -> pathlib.Path:
    """Copy whole task roots into a disposable coordination tree and return its root."""
    coordination_root = _coordination_root()
    target = destination / "coordination" / "tasks" / "agents-remember"
    target.mkdir(parents=True, exist_ok=True)
    for master in masters:
        shutil.copytree(_task_root(coordination_root, master), target / master)
    return destination / "coordination"


def _memory_repository() -> pathlib.Path:
    """The external memory repository, or a skip naming the root it was looked for under."""
    memory_root = _coordination_root() / "memory-repos" / "ar-agents-remember"
    if not (memory_root / ".git").exists():
        pytest.skip(f"the memory repository is not present under {_coordination_root()}")
    return memory_root


def _frozen_document(path_in_memory: str, destination: pathlib.Path, index: int) -> pathlib.Path:
    """Write out the bytes this revision committed, so the case reads history rather than the tree.

    The working tree moves under a test and then the test lies about what it measured; a Git object
    does not. The revision is named in the skip so a reader knows which artifact was unavailable.
    The index keeps two route cards that share a basename in separate files, so scanning both really
    does scan two documents.
    """
    probe = subprocess.run(
        ["git", "-C", str(_memory_repository()), "show", f"{T45_FREEZE}:{path_in_memory}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip(
            f"{T45_FREEZE}:{path_in_memory} does not resolve in the memory repository: "
            f"{probe.stderr.strip()}"
        )
    document = destination / f"{index}-{pathlib.Path(path_in_memory).name}"
    document.write_text(probe.stdout, encoding="utf-8")
    return document


def _frozen_register_snapshot(world: pathlib.Path) -> pathlib.Path:
    """Replace the copied master's register with the snapshot carrying `R11`'s six `→ L20` arrows.

    Pinning the *live* register would make this case assert the world's defect count, so repairing
    the world fails the suite: the case would be red precisely when the check it protects is right.
    The snapshot carries the defect by construction, and the clean direction is the sibling case.
    """
    register = world / "tasks" / "agents-remember" / CURRENT_MASTER / "notes" / "defect-index.md"
    register.write_text(
        "| ID | Owner | Class | What it was | State | Where |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        + "".join(
            f"| T{index} | L6 | record | a stale arrow to the previous master's leaf | "
            f"`open → L20` | the note |\n"
            for index in range(1, 6)
        ),
        encoding="utf-8",
    )
    return register


def _write_declared_document(path: pathlib.Path, status: str) -> None:
    """Write one copied leaf document's status and step states, so its own row is justified."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = status
    for step in payload.get("steps") or ():
        if not isinstance(step, dict):
            continue
        step["status"] = "done"
        for substep in step.get("substeps") or ():
            if isinstance(substep, dict):
                substep["status"] = "done"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _prune_to_declared_leaves(
    task_root: pathlib.Path, declared: dict[str, tuple[str, str]]
) -> set[str]:
    """Drop every enclosure and leaf document the fixture did not declare; return the ids kept."""
    keep = {integrity.leaf_key(leaf_id) for leaf_id in declared}
    enclosures = task_root / "enclosures"
    if enclosures.is_dir():
        for enclosure in sorted(entry for entry in enclosures.iterdir() if entry.is_dir()):
            if integrity.leaf_key(enclosure.name) not in keep:
                shutil.rmtree(enclosure)
    present: set[str] = set()
    for path in sorted(task_root.glob("*.json")):
        if path.name == "task.json":  # the master document is an ar-task-document/v1 too
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != "ar-task-document/v1":
            continue
        document_id = str(payload.get("id", ""))
        if document_id not in declared:
            path.unlink()
            continue
        _write_declared_document(path, declared[document_id][1])
        present.add(document_id)
    return present


def _reduce_to_declared_rows(
    master_json: pathlib.Path, declared: dict[str, tuple[str, str]]
) -> int:
    """Reduce the copied master's ``subTasks[]`` to the declared rows, with the declared status."""
    master = json.loads(master_json.read_text(encoding="utf-8"))
    rows = [row for row in master.get("subTasks") or () if str(row.get("number")) in declared]
    for row in rows:
        row["status"] = declared[str(row["number"])][1]
    master["subTasks"] = rows
    master_json.write_text(json.dumps(master, indent=2) + "\n", encoding="utf-8")
    return len(rows)


def _frozen_leaf_snapshot(world: pathlib.Path) -> pathlib.Path:
    """Freeze the copied master's leaf record to `DECLARED_LEAVES`, by construction.

    This is `T51`'s class, one case-set over from `_frozen_register_snapshot`. Three things in the
    live master move without a case's knowledge, and each of the three cases below asserted one of
    them: the enclosure population gains a leaf, every sibling leaf document's status moves as its
    work moves, and the master's ``subTasks[]`` gains a row. A case asserting any of those is
    asserting the world, so it goes red when a sibling leaf opens — which is not a fact about the
    check it protects.

    The fixture therefore writes all three: it prunes the enclosures and the leaf documents the case
    did not declare, writes each declared document's status and step states, and reduces the
    master's rows to the declared set with the declared status. Every number and every verdict
    below is then one this case produced. The contracts that remain are the real artifacts' own
    bytes; the documents' statuses are the fixture's.

    A copy that cannot supply the declared population is skipped with the reason named, rather than
    asserting a count it did not build.
    """
    task_root = world / "tasks" / "agents-remember" / CURRENT_MASTER
    declared = {leaf_id: (stem, status) for leaf_id, stem, status in DECLARED_LEAVES}
    present = _prune_to_declared_leaves(task_root, declared)
    rows = _reduce_to_declared_rows(task_root / "task.json", declared)
    enclosures = task_root / "enclosures"
    remaining = [entry for entry in enclosures.iterdir() if entry.is_dir()]
    if len(remaining) != DECLARED_LEAF_COUNT or rows != DECLARED_LEAF_COUNT:
        pytest.skip(
            f"the {CURRENT_MASTER} copy cannot supply the declared leaf population "
            f"({DECLARED_LEAF_COUNT} enclosures and rows, found {len(remaining)} and {rows}; "
            f"documents present: {sorted(present)})"
        )
    return task_root


# --------------------------------------------------------------------------------------------
# Check 1 — leaf document status against its own contract's cells (requirement 4)
# --------------------------------------------------------------------------------------------


class LeafDocumentAgainstContractTests:
    def test_the_historical_master_is_reported_and_the_counts_are_stated(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The artifact that motivated requirement 4: seven leaves landed, seven documents unmarked."""
        world = _control_world(tmp_path, HISTORICAL_MASTER)
        result = integrity.check_leaf_document_against_contract(world)

        assert result.subjects == 7, "every contract in the historical root must be compared"
        assert result.authority == 7
        assert result.disagreements == 7
        assert result.detail.startswith("7 enclosure contracts matched")
        assert "0 contracts carry no matching document" in result.detail
        assert {finding.rule for finding in result.findings} == {integrity.RULE_LEAF_CONTRACT}
        # The two sides are both named on every finding, which is what makes it actionable.
        for finding in result.findings:
            assert finding.declared.startswith("document status=")
            assert "closeout=" in finding.measured and "integration=" in finding.measured

    def test_the_current_master_passes_and_says_what_it_compared(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The corrected side: this master's contracts and documents agree, and the count is visible.

        The world is the live master copied and then frozen to `DECLARED_LEAVES`: the population and
        the statuses are the fixture's, so the sibling leaf that opened its own enclosure cannot
        move the numbers asserted here (`T51`).
        """
        world = _control_world(tmp_path, CURRENT_MASTER)
        _frozen_leaf_snapshot(world)
        result = integrity.check_leaf_document_against_contract(world)

        assert result.ok, [finding.message for finding in result.findings]
        assert result.subjects == DECLARED_LEAF_COUNT, "the fixture declares two leaf contracts"
        assert result.authority == DECLARED_LEAF_COUNT
        assert f"{DECLARED_LEAF_COUNT} enclosure contracts matched a leaf document" in result.detail

    def test_a_strict_spelling_match_under_reports_by_the_keyed_cases(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The instrument fault this leaf found: six instances were invisible to an exact-id match.

        Older masters write `leaf_id: 260703-l1` in the contract and `"id": "260703-L1"` in the
        document. An exact-string comparison therefore matches nothing and reports a clean zero,
        which is the fault class the whole master is about. The check must count both calibrations
        and say how many instances the stricter one misses.
        """
        coordination_root = _coordination_root()
        older = coordination_root / "tasks" / "agents-remember" / "260703_memory-hygiene-reform"
        if not older.is_dir():
            pytest.skip(f"the older-master artifact is not present under {coordination_root}")
        world = _control_world(tmp_path, "260703_memory-hygiene-reform")
        result = integrity.check_leaf_document_against_contract(world)

        assert result.disagreements >= 1
        assert any(
            finding.rule == integrity.RULE_LEAF_CONTRACT_KEYED for finding in result.findings
        )
        assert "a strict spelling match under-reports by" in result.detail

    def test_a_contract_with_no_leaf_document_is_counted_rather_than_failed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A finalized master's leaf documents are gone by design; that is not a disagreement."""
        world = _control_world(tmp_path, "260831_closeout-certification-reform")
        result = integrity.check_leaf_document_against_contract(world)

        assert result.subjects > 0
        assert result.authority > result.subjects or "no matching document" in result.detail
        assert result.detail.count("counted, not failed") == 1

    def test_a_document_completed_before_its_closeout_is_a_finding_not_a_count(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The other historical branch: 22 documents read `Completed` while closeout never started.

        This is the `unlanded-completion` direction, and it is most of the historical population —
        so the artifact case above, which asserts subject counts and a detail substring, would stay
        green if the branch were deleted. The findings are what the branch produces, and they are
        what this case reads.
        """
        world = _control_world(tmp_path, "260831_closeout-certification-reform")
        result = integrity.check_leaf_document_against_contract(world)

        completed = [
            finding
            for finding in result.findings
            if finding.declared == "document status=Completed"
        ]
        assert len(completed) == 22, [finding.subject for finding in result.findings]
        assert len(completed) == result.disagreements, (
            "this artifact's findings are all of one kind"
        )
        assert all(finding.rule == integrity.RULE_LEAF_CONTRACT for finding in completed)
        assert all("closeout=not-started" in finding.measured for finding in completed)
        assert all("has not started" in finding.message for finding in completed)


# --------------------------------------------------------------------------------------------
# Check 2 — master row status against the leaf document it names (D42)
# --------------------------------------------------------------------------------------------


class MasterRowAgainstLeafDocumentTests:
    def test_the_historical_master_rows_read_completed_over_unlanded_documents(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`D42`'s class on the real artifact: rows Completed while their documents never landed."""
        world = _control_world(tmp_path, HISTORICAL_MASTER)
        result = integrity.check_master_rows_against_leaf_documents(world)

        assert result.subjects == 7
        assert result.disagreements == 5
        early = [f for f in result.findings if f.rule == integrity.RULE_MASTER_EARLY]
        assert len(early) == 5, "every historical disagreement is a row over an unlanded document"
        assert all(finding.declared == "row status=Completed" for finding in early)
        assert "Of 5 disagreements, 5 are a row reading Completed" in result.detail

    def test_the_current_master_rows_agree_with_their_documents(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The corrected side for `D42`: the rows this fixture declares agree with their documents.

        Both live halves of this case move under it — a sibling document's status moves
        `result.ok`, and a sibling leaf's new row moves the "rows name no document" count — so the
        fixture writes both (`T51`).
        """
        world = _control_world(tmp_path, CURRENT_MASTER)
        _frozen_leaf_snapshot(world)
        result = integrity.check_master_rows_against_leaf_documents(world)

        assert result.ok, [finding.message for finding in result.findings]
        assert result.subjects == DECLARED_LEAF_COUNT, "the fixture declares two leaves"
        assert "0 rows name no document" in result.detail, (
            "every row the fixture wrote names its own document, so an unmatched row is a fault "
            "the fixture did not write"
        )

    def test_a_row_is_never_completed_on_step_state_alone(self) -> None:
        """The rule itself, at the boundary `D42` crossed: all steps done, document not landed."""
        document = integrity.LeafDocument(
            path=pathlib.Path("leaf.json"),
            task_root=pathlib.Path("."),
            doc_id="260918-TSIP-L9",
            slug="9_leaf",
            kind="subTask",
            status="planning",
            steps=(("S1", "done", None), ("S2", "done", None)),
        )
        assert integrity.derived_master_status(document) == "inProgress"

        landed = integrity.LeafDocument(
            path=pathlib.Path("leaf.json"),
            task_root=pathlib.Path("."),
            doc_id="260918-TSIP-L9",
            slug="9_leaf",
            kind="subTask",
            status="Completed",
            steps=(("S1", "done", None), ("S2", "done", None)),
        )
        assert integrity.derived_master_status(landed) == "Completed"

    def test_a_completed_document_with_an_open_step_is_not_completed(self) -> None:
        """The other half of the same conjunct: the document's status is not the whole rule.

        The sibling case pins the direction `D42` crossed — every step marked, the document not
        landed. This one pins the direction only the step-completeness conjunct can see: a document
        that *reads* ``Completed`` while one of its own steps is still open is not a landing either,
        so the row it justifies is ``inProgress``. Deleting that conjunct from
        ``derived_master_status`` leaves every other case in this class green, which is why the
        boundary is measured here rather than inferred from the ones beside it.
        """
        partially_done = integrity.LeafDocument(
            path=pathlib.Path("leaf.json"),
            task_root=pathlib.Path("."),
            doc_id="260918-TSIP-L9",
            slug="9_leaf",
            kind="subTask",
            status="Completed",
            steps=(("S1", "done", None), ("S2", "pending", None)),
        )
        assert integrity.derived_master_status(partially_done) == "inProgress"

        nothing_done = integrity.LeafDocument(
            path=pathlib.Path("leaf.json"),
            task_root=pathlib.Path("."),
            doc_id="260918-TSIP-L9",
            slug="9_leaf",
            kind="subTask",
            status="Completed",
            steps=(("S1", "pending", None),),
        )
        assert integrity.derived_master_status(nothing_done) == "inProgress"

    def test_an_abandoned_leaf_stays_abandoned_on_its_own_row(self) -> None:
        """A decision is terminal; the step-state rule must not reopen it."""
        document = integrity.LeafDocument(
            path=pathlib.Path("leaf.json"),
            task_root=pathlib.Path("."),
            doc_id="260918-TSIP-L9",
            slug="9_leaf",
            kind="subTask",
            status="abandoned",
            steps=(("S1", "done", None),),
        )
        assert integrity.derived_master_status(document) == "abandoned"

    def test_a_row_completed_without_its_document_is_reported(self, tmp_path: pathlib.Path) -> None:
        """`D42` inside the comparison itself: every step done, the document not landed.

        The rule-level case above pins what the derivation returns; this one pins that the
        comparison reports the row that disagrees with it, which is the half a reader acts on.
        """
        task_root = tmp_path / "260918_tsip_l9"
        task_root.mkdir()
        (task_root / "task.json").write_text(
            json.dumps(
                {
                    "schema": "ar-task-document/v1",
                    "subTasks": [{"number": "260918-TSIP-L9", "status": "Completed"}],
                }
            ),
            encoding="utf-8",
        )
        (task_root / "9_leaf.json").write_text(
            json.dumps(
                {
                    "schema": "ar-task-document/v1",
                    "id": "260918-TSIP-L9",
                    "slug": "9_leaf",
                    "kind": "subTask",
                    "status": "planning",
                    "steps": [
                        {"id": "S1", "title": "a step", "status": "done"},
                        {"id": "S2", "title": "another", "status": "done"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = integrity.check_master_rows_against_leaf_documents(
            tmp_path, task_roots_scope=[task_root]
        )

        assert result.subjects == 1
        assert result.disagreements == 1
        finding = result.findings[0]
        assert finding.rule == integrity.RULE_MASTER_EARLY
        assert finding.declared == "row status=Completed"
        assert finding.measured == "derived=inProgress (document status=planning)"
        assert "1 are a row reading Completed over an unlanded document" in result.detail

    def test_a_row_completed_over_a_document_with_an_open_step_is_reported(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The reported half of the conjunct: the document agrees with the row, the steps do not.

        Here the document itself reads ``Completed``, so the status side raises no question and only
        the steps can answer it. The row is reported against the status the steps justify. The case
        above cannot reach this: there the document reads ``planning``, so it is the status — not the
        step-completeness conjunct — that produces the finding.
        """
        task_root = tmp_path / "260918_tsip_l9"
        task_root.mkdir()
        (task_root / "task.json").write_text(
            json.dumps(
                {
                    "schema": "ar-task-document/v1",
                    "subTasks": [{"number": "260918-TSIP-L9", "status": "Completed"}],
                }
            ),
            encoding="utf-8",
        )
        (task_root / "9_leaf.json").write_text(
            json.dumps(
                {
                    "schema": "ar-task-document/v1",
                    "id": "260918-TSIP-L9",
                    "slug": "9_leaf",
                    "kind": "subTask",
                    "status": "Completed",
                    "steps": [
                        {"id": "S1", "title": "a step", "status": "done"},
                        {"id": "S2", "title": "another", "status": "pending"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = integrity.check_master_rows_against_leaf_documents(
            tmp_path, task_roots_scope=[task_root]
        )

        assert result.subjects == 1
        assert result.disagreements == 1
        finding = result.findings[0]
        assert finding.rule == integrity.RULE_MASTER_ROW
        assert finding.declared == "row status=Completed"
        assert finding.measured == "derived=inProgress (document status=Completed)"


# --------------------------------------------------------------------------------------------
# Check 3 — register owner arrows against the owning master's leaf set
# --------------------------------------------------------------------------------------------


class RegisterOwnerArrowTests:
    def test_the_current_register_is_reported_and_the_arrow_count_is_stated(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`R11` on the frozen artifact: five State cells still point at the previous master's L20.

        Only the master is copied from the live tree — its leaf set is what makes the arrows
        unresolved — and that set is frozen to `DECLARED_LEAVES` like the sibling cases', because
        the ninth row the next leaf adds moves the count this case asserts (`T51`). The register rows
        come from a snapshot, so this case tests the check rather than the current state of the
        register: `R11` has since been repaired, and a case that pinned the live file's five
        disagreements went red the moment it was.
        """
        world = _control_world(tmp_path, CURRENT_MASTER)
        _frozen_leaf_snapshot(world)
        register = _frozen_register_snapshot(world)
        result = integrity.check_register_row_ownership(register)

        assert result.authority == DECLARED_LEAF_COUNT, "the fixture declares two leaves"
        assert result.subjects == 5, "every State-cell arrow is compared, not only the failures"
        assert result.disagreements == 5
        assert {finding.declared for finding in result.findings} == {"L20"}
        assert all(finding.rule == integrity.RULE_OWNER for finding in result.findings)
        assert all(finding.line is not None for finding in result.findings)

    def test_a_register_whose_arrows_all_resolve_is_clean(self, tmp_path: pathlib.Path) -> None:
        """The corrected side: the same register with every arrow naming a declared leaf."""
        register = tmp_path / "notes" / "defect-index.md"
        register.parent.mkdir(parents=True)
        (tmp_path / "task.json").write_text(
            '{"schema": "ar-task-document/v1", "subTasks": '
            '[{"number": "260918-TSIP-L1"}, {"number": "260918-TSIP-L2"}]}',
            encoding="utf-8",
        )
        register.write_text(
            "| ID | Owner | Class | What it was | State | Where |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| T1 | L1 | process | a thing | `open → L1` | the note |\n"
            "| T2 | L2 | record | another | `open → L2` | the note |\n",
            encoding="utf-8",
        )
        result = integrity.check_register_row_ownership(register)

        assert result.ok, [finding.message for finding in result.findings]
        assert result.subjects == 2
        assert result.authority == 2

    def test_an_arrow_to_an_undeclared_leaf_is_reported_with_both_sets(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The refusal direction, as a minimal control rather than the whole register."""
        register = tmp_path / "notes" / "defect-index.md"
        register.parent.mkdir(parents=True)
        (tmp_path / "task.json").write_text(
            '{"schema": "ar-task-document/v1", "subTasks": [{"number": "260918-TSIP-L1"}]}',
            encoding="utf-8",
        )
        register.write_text(
            "| ID | Owner | Class | What it was | State | Where |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| T1 | L1 | process | a thing | `open → L20` | the note |\n",
            encoding="utf-8",
        )
        result = integrity.check_register_row_ownership(register)

        assert result.disagreements == 1
        assert result.findings[0].declared == "L20"
        assert result.findings[0].measured == "L1"

    def test_only_the_state_cell_is_graded_because_the_owner_cell_is_the_rows_own_name(
        self,
    ) -> None:
        """`T46 | L6 / L7 | …` names owners, not leaf-set members; grading it reported 45 zeros.

        The first run of this check read the Owner cell too and reported every row. This case pins
        the column the check grades, so a column move fails here rather than in a report.
        """
        cells = integrity._cells("| T46 | L6 / L7 | class | what | `open → L6` | note |")
        assert cells[0] == "T46"
        assert cells[integrity.STATE_COLUMN] == "`open → L6`"
        assert "→" not in cells[1]

    def test_a_master_with_no_leaf_set_refuses_rather_than_reporting_a_zero(
        self, tmp_path: pathlib.Path
    ) -> None:
        register = tmp_path / "notes" / "defect-index.md"
        register.parent.mkdir(parents=True)
        (tmp_path / "task.json").write_text(
            '{"schema": "ar-task-document/v1", "subTasks": []}', encoding="utf-8"
        )
        register.write_text(
            "| ID | Owner | Class | What it was | State | Where |\n"
            "| --- | --- | --- | --- | --- | --- |\n"
            "| T1 | L1 | process | a thing | `open → L1` | the note |\n",
            encoding="utf-8",
        )
        with pytest.raises(integrity.RecordIntegrityError, match="no leaf set"):
            integrity.check_register_row_ownership(register)


# --------------------------------------------------------------------------------------------
# Check 4 — a figure written in prose against the source at the revision the prose describes
# --------------------------------------------------------------------------------------------


class DeclaredFigureCurrencyTests:
    def _instrument_claims(self) -> tuple[integrity.FigureClaim, integrity.FigureClaim]:
        """The two authority-side declarations `T45` needs: the module's lines, the suite's cases."""
        return (
            integrity.FigureClaim(
                source=INSTRUMENT_MODULE,
                label_pattern=INSTRUMENT_LABEL,
                shape="line_count",
                stated_by="260918-TSIP-L1 curator report (T45)",
            ),
            integrity.FigureClaim(
                source=INSTRUMENT_SUITE,
                label_pattern=SUITE_LABEL,
                shape="case_count",
                stated_by="260918-TSIP-L1 curator report (T45)",
            ),
        )

    def test_the_two_shapes_are_measured_from_the_source_not_declared_by_the_test(self) -> None:
        """The authority side is the source file; a hard-coded expectation would prove nothing.

        Both figures are compared with a count this case makes itself, from the file. The case
        counted the suite by applying the module's own ``TEST_DEFINITION`` a second time, which is
        true by construction: a counter that matched every ``def`` — or none — satisfied it, so a
        case named for the counter's ability to see a test function could not fail when that
        ability was removed. The independently written pattern does not move when the constant
        does, which is what makes the equality an observation rather than a restatement.
        """
        assert INSTRUMENT_MODULE.is_file() and INSTRUMENT_SUITE.is_file()
        lines = integrity.FigureClaim(
            source=INSTRUMENT_MODULE, label_pattern="x", shape="line_count", stated_by="test"
        ).measured()
        cases = integrity.FigureClaim(
            source=INSTRUMENT_SUITE, label_pattern="x", shape="case_count", stated_by="test"
        ).measured()
        source = INSTRUMENT_SUITE.read_text(encoding="utf-8")
        assert lines == len(INSTRUMENT_MODULE.read_text(encoding="utf-8").splitlines())
        assert cases == len(re.findall(r"^\s*def test_", source, re.M)), (
            "the case counter must see exactly the suite's test functions"
        )

    def test_the_historical_route_documents_are_read_from_the_revision_that_committed_them(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`T45`'s real artifact, at both of its documents, with each branch's reason named.

        Four figures are compared and only one is drift. The route card's correction sentence reads
        `instrument_discipline.py` **361 → 432 lines**, and the figure bound to its unit is the
        repaired 432, so it agrees with the source. The suite card's `:16` sentence — *"537 lines,
        27 cases"* — states both of the suite's real values; the first run of this check reported it
        as `declared=537 … measured=27`, a disagreement the prose never made. The route card then
        passes it too. What survives is the history entry at `:1304`, which tells its readers the
        suite is 350 lines while the source carries 537 — and it is reachable only through a claim
        that names its subject the way the sentence does, because the sentence names no file.
        """
        module_claim, case_claim = self._instrument_claims()
        route_document = _frozen_document(T45_ROUTE_DOCUMENT, tmp_path, 0)
        suite_document = _frozen_document(T45_SUITE_DOCUMENT, tmp_path, 1)
        if module_claim.measured() != 432 or case_claim.measured() != 27:
            pytest.skip(
                "the instrument has moved since this artifact was frozen "
                f"({module_claim.measured()} lines, {case_claim.measured()} cases)"
            )
        result = integrity.check_declared_figure_currency(
            [route_document, suite_document],
            [
                module_claim,
                case_claim,
                integrity.FigureClaim(
                    source=INSTRUMENT_SUITE,
                    label_pattern=SUITE_LABEL,
                    shape="line_count",
                    stated_by="260918-TSIP-L1 curator report (T45)",
                ),
                integrity.FigureClaim(
                    source=INSTRUMENT_SUITE,
                    label_pattern=r"(?<![\w.])the suite",
                    shape="line_count",
                    stated_by="260918-TSIP-L1 curator report (T45)",
                ),
            ],
        )

        assert result.subjects == 4, "the artifact states four figures about these two sources"
        assert result.authority == 4
        assert result.disagreements == 1, [finding.subject for finding in result.findings]
        stale = result.findings[0]
        assert stale.subject.endswith(f"{pathlib.Path(T45_SUITE_DOCUMENT).name}:1304")
        assert stale.declared == "350 of 'the suite was 350 lines'"
        assert stale.measured == "537 (line_count)"

    def test_a_correct_pair_of_figures_is_not_a_disagreement(self, tmp_path: pathlib.Path) -> None:
        """The artifact's `:16` sentence states both figures correctly, and both must pass.

        This is the false positive the review found: the case-count claim read the line count that
        preceded its unit, so correct prose was reported as `declared=537 … measured=27`. The two
        numbers are written exactly as the artifact writes them, in the order it writes them.
        """
        module_claim, case_claim = self._instrument_claims()
        if module_claim.measured() != 432 or case_claim.measured() != 27:
            pytest.skip("the instrument has moved from the figures this case reproduces")
        document = tmp_path / "route-overview.md"
        document.write_text(
            "This route gained one module. `mcp/tests/test_instrument_discipline.py` (**537 lines,\n"
            "27 cases** — re-measured on the current candidate) pins the conditions shipped in\n"
            "`instrument_discipline.py` (**432 lines**).\n",
            encoding="utf-8",
        )
        result = integrity.check_declared_figure_currency(
            [document],
            [
                module_claim,
                case_claim,
                integrity.FigureClaim(
                    source=INSTRUMENT_SUITE,
                    label_pattern=SUITE_LABEL,
                    shape="line_count",
                    stated_by="test",
                ),
            ],
        )

        assert result.subjects == 3, "each figure is bound to its own unit"
        assert result.ok, [finding.message for finding in result.findings]

    def test_a_stale_prose_figure_is_reported_against_the_current_source(
        self, tmp_path: pathlib.Path
    ) -> None:
        """`T45`'s historical case: the document states the pre-repair size as the current one."""
        lines = integrity.FigureClaim(
            source=INSTRUMENT_MODULE, label_pattern="x", shape="line_count", stated_by="test"
        ).measured()
        claim = integrity.FigureClaim(
            source=INSTRUMENT_MODULE,
            label_pattern=INSTRUMENT_LABEL,
            shape="line_count",
            stated_by="260918-TSIP-L1 curator report (T45)",
        )
        # The module grew by 71 lines and the document never followed.
        document = tmp_path / "route-overview.md"
        document.write_text(
            "The route's governor is `instrument_discipline.py` at "
            f"**{lines - 71} lines**, and the section above is out of date.\n",
            encoding="utf-8",
        )
        result = integrity.check_declared_figure_currency([document], [claim])

        assert result.subjects == 1, "the figure is bound to the unit it is written in"
        assert result.disagreements == 1, "the pre-repair figure must be reported"
        assert result.findings[0].declared.startswith(f"{lines - 71} ")
        assert result.findings[0].measured.startswith(f"{lines} ")

    def test_a_correction_sentence_is_graded_at_the_value_it_corrected_to(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The `361 -> 432 lines` sentence: the figure after the arrow is the one that must hold.

        The historical artifact writes its correction this way, and it is why this direction cannot
        simply report every number the clause contains: the 361 belongs to the state the prose is
        describing as superseded, and grading it reports a correct repair as drift.
        """
        lines = integrity.FigureClaim(
            source=INSTRUMENT_MODULE, label_pattern="x", shape="line_count", stated_by="test"
        ).measured()
        claim = integrity.FigureClaim(
            source=INSTRUMENT_MODULE,
            label_pattern=INSTRUMENT_LABEL,
            shape="line_count",
            stated_by="test",
        )
        corrected = tmp_path / "corrected.md"
        corrected.write_text(
            f"`instrument_discipline.py` **{lines - 71} → {lines} lines**, so the row was fixed.\n",
            encoding="utf-8",
        )
        approved = integrity.check_declared_figure_currency([corrected], [claim])

        assert approved.subjects == 1
        assert approved.ok, [finding.message for finding in approved.findings]

        # One line further on: the prose now states the superseded figure as the current one.
        superseded = tmp_path / "superseded.md"
        superseded.write_text(
            f"`instrument_discipline.py` **{lines - 71} → {lines + 71} lines** once the repair "
            f"landed.\n",
            encoding="utf-8",
        )
        refused = integrity.check_declared_figure_currency([superseded], [claim])

        assert refused.disagreements == 1
        assert refused.findings[0].declared.startswith(f"{lines + 71} ")

    def test_a_stated_figure_in_the_frontmatter_is_not_a_body_claim(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A metadata row states the candidate a card was verified against, not a measurement.

        The body carries the same sentence, so a check that scanned the frontmatter too would
        report two occurrences of one claim instead of one.
        """
        lines = integrity.FigureClaim(
            source=INSTRUMENT_MODULE, label_pattern="x", shape="line_count", stated_by="test"
        ).measured()
        claim = integrity.FigureClaim(
            source=INSTRUMENT_MODULE,
            label_pattern=INSTRUMENT_LABEL,
            shape="line_count",
            stated_by="test",
        )
        document = tmp_path / "card.md"
        document.write_text(
            "---\n"
            f"verifiedAgainst: `instrument_discipline.py` was {lines - 71} lines at the base\n"
            "---\n"
            f"`instrument_discipline.py` is {lines} lines today.\n",
            encoding="utf-8",
        )
        result = integrity.check_declared_figure_currency([document], [claim])

        assert result.subjects == 1, "only the body states a figure about the source"
        assert result.ok, [finding.message for finding in result.findings]
        assert result.findings == ()

    def test_an_unknown_figure_shape_refuses_rather_than_reporting_a_zero(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A shape with no measurement rule is a refusal, never a clean comparison."""
        source = tmp_path / "source.py"
        source.write_text("x = 1\n", encoding="utf-8")
        claim = integrity.FigureClaim(
            source=source, label_pattern="source.py", shape="paragraph_count", stated_by="test"
        )
        with pytest.raises(integrity.RecordIntegrityError, match="unknown figure shape"):
            claim.measured()

    def test_the_corrected_prose_passes(self, tmp_path: pathlib.Path) -> None:
        """The corrected side: the same sentence restated at the repaired size."""
        lines = integrity.FigureClaim(
            source=INSTRUMENT_MODULE, label_pattern="x", shape="line_count", stated_by="test"
        ).measured()
        claim = integrity.FigureClaim(
            source=INSTRUMENT_MODULE,
            label_pattern=INSTRUMENT_LABEL,
            shape="line_count",
            stated_by="260918-TSIP-L1 curator report (T45)",
        )
        document = tmp_path / "corrected-route-overview.md"
        document.write_text(
            f"The route gained a module; `instrument_discipline.py` is **{lines} lines** today.\n",
            encoding="utf-8",
        )
        result = integrity.check_declared_figure_currency([document], [claim])

        assert result.ok, [finding.message for finding in result.findings]
        assert result.subjects == 1

    def test_a_bare_line_count_for_another_file_is_not_this_source_s_figure(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The first run of this check reported twelve disagreements for one source.

        A pattern of ``\\d+ lines`` matches every size on the page. The label is in the pattern for
        that reason, and this case pins it: a neighbouring file's size must not be attributed to the
        source under comparison.
        """
        claim = integrity.FigureClaim(
            source=INSTRUMENT_MODULE,
            label_pattern=INSTRUMENT_LABEL,
            shape="line_count",
            stated_by="260918-TSIP-L1 curator report (T45)",
        )
        document = tmp_path / "neighbour.md"
        document.write_text(
            "`structural_limits.py` is 1,153 lines and `wire_contract.py` is 697 lines.\n",
            encoding="utf-8",
        )
        result = integrity.check_declared_figure_currency([document], [claim])

        assert result.subjects == 0
        assert result.ok

    def test_a_claim_scoped_to_one_document_does_not_grade_another(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A restatement that names no file needs an explicit scope, or it is a guess."""
        scoped = tmp_path / "scoped.md"
        scoped.write_text("`instrument_discipline.py` is 1 line long.\n", encoding="utf-8")
        other = tmp_path / "other.md"
        other.write_text("`instrument_discipline.py` is 1 line long.\n", encoding="utf-8")
        claim = integrity.FigureClaim(
            source=INSTRUMENT_MODULE,
            label_pattern=INSTRUMENT_LABEL,
            shape="line_count",
            stated_by="test",
            documents=(scoped,),
        )
        result = integrity.check_declared_figure_currency([scoped, other], [claim])

        assert result.subjects == 1
        assert result.disagreements == 1
        assert result.findings[0].path == str(scoped)

    def test_an_unresolvable_base_commit_refuses_rather_than_reporting_a_zero(
        self, tmp_path: pathlib.Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        source = repo / "source.py"
        source.write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "add", "source.py"], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add"],
            cwd=repo,
            check=True,
        )
        claim = integrity.FigureClaim(
            source=source,
            label_pattern="source.py",
            shape="line_count",
            stated_by="test",
            base_commit="0" * 40,
        )
        with pytest.raises(integrity.RecordIntegrityError, match="does not resolve"):
            claim.measured()

    def test_a_source_outside_any_git_tree_refuses_when_a_base_is_declared(
        self, tmp_path: pathlib.Path
    ) -> None:
        source = tmp_path / "source.py"
        source.write_text("x = 1\n", encoding="utf-8")
        claim = integrity.FigureClaim(
            source=source,
            label_pattern="source.py",
            shape="line_count",
            stated_by="test",
            base_commit="a" * 40,
        )
        with pytest.raises(integrity.RecordIntegrityError, match="not inside a Git work tree"):
            claim.measured()


# --------------------------------------------------------------------------------------------
# The checks as a set
# --------------------------------------------------------------------------------------------


class RecordIntegrityReportTests:
    def test_every_comparison_states_its_two_populations(self, tmp_path: pathlib.Path) -> None:
        """A zero is admissible only with the population it was drawn from; that is requirement 3."""
        world = _control_world(tmp_path, HISTORICAL_MASTER)
        for result in integrity.run_all(world):
            assert result.detail, f"{result.check} reported no comparison"
            assert result.subjects > 0, f"{result.check} compared nothing"
            payload = result.to_dict()
            assert payload["disagreements"] == result.disagreements
            assert payload["detail"] == result.detail
            assert payload["disagreements"] == len(result.findings)

    def test_said_what_it_compared_reads_as_the_comparison_not_as_a_status(
        self, tmp_path: pathlib.Path
    ) -> None:
        world = _control_world(tmp_path, HISTORICAL_MASTER)
        rendered = "\n".join(result.render() for result in integrity.run_all(world))

        assert "compared:" in rendered
        assert "populations:" in rendered
        assert "disagreements:" in rendered

    def test_the_cli_names_its_input_when_none_is_supplied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A check that cannot find its input must refuse, not report a clean tree."""
        monkeypatch.delenv(integrity.COORDINATION_ROOT_ENV, raising=False)
        assert integrity.build_parser().parse_args([]).coordination_root is None
        with pytest.raises(
            integrity.RecordIntegrityError, match="no coordination root was supplied"
        ):
            integrity.coordination_root_from_environment()

    def test_the_cli_refuses_a_root_that_carries_no_tasks_tree(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(integrity.COORDINATION_ROOT_ENV, str(tmp_path))
        with pytest.raises(integrity.RecordIntegrityError, match="carries no tasks/ directory"):
            integrity.coordination_root_from_environment()

    def test_the_cli_runs_from_a_task_root_and_writes_nothing(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Requirement 3: runnable at any moment from the task root, and read-only."""
        world = _control_world(tmp_path, HISTORICAL_MASTER)
        monkeypatch.setenv(integrity.COORDINATION_ROOT_ENV, str(world))
        monkeypatch.chdir(world / "tasks" / "agents-remember" / HISTORICAL_MASTER)
        before = sorted(path.relative_to(world).as_posix() for path in world.rglob("*"))

        exit_code = integrity.main(["--format", "json"])

        after = sorted(path.relative_to(world).as_posix() for path in world.rglob("*"))
        assert exit_code == 1, "the historical world disagrees, so the check must exit non-zero"
        assert before == after, "the check wrote to the tree it measured"

    def test_a_clean_world_exits_zero(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A clean world exits zero, over the population the run itself reports.

        The exit code alone is not evidence that the selected check ran: a `--check` that selects
        nothing also prints nothing and exits zero. The payload is read back and the comparison's
        own population asserted, so the zero is licensed by what it was drawn from — the fixture's
        declared leaves, not the live master's (`T51`).
        """
        world = _control_world(tmp_path, CURRENT_MASTER)
        _frozen_leaf_snapshot(world)
        exit_code = integrity.main(
            [
                "--coordination-root",
                str(world),
                "--check",
                "leaf-document-vs-contract",
                "--format",
                "json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)

        assert exit_code == 0
        assert [row["check"] for row in payload] == ["leaf-document-vs-contract"]
        assert payload[0]["subjects"] == DECLARED_LEAF_COUNT
        assert payload[0]["disagreements"] == 0

    def test_the_cli_runs_the_prose_figure_check_from_the_task_root_and_writes_nothing(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Requirement 3 for the fourth check: declared on the command line, run from a task root.

        Its authority side is a per-project declaration rather than something the coordination tree
        implies, so the route is a repeatable flag; `--check` must then be able to select it by the
        name the comparison reports.
        """
        world = _control_world(tmp_path, HISTORICAL_MASTER)
        task_root = world / "tasks" / "agents-remember" / HISTORICAL_MASTER
        document = tmp_path / "route-overview.md"
        stale = len(INSTRUMENT_MODULE.read_text(encoding="utf-8").splitlines()) - 71
        document.write_text(
            f"`instrument_discipline.py` was {stale} lines when the route was written.\n",
            encoding="utf-8",
        )
        monkeypatch.setenv(integrity.COORDINATION_ROOT_ENV, str(world))
        monkeypatch.chdir(task_root)
        before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

        exit_code = integrity.main(
            [
                "--check",
                "declared-figure-vs-source",
                "--figure-claim",
                f"{r'instrument_discipline\.py'}:line_count:{INSTRUMENT_MODULE}:{document}",
                "--format",
                "json",
            ]
        )

        after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
        assert exit_code == 1, "a stale prose figure must fail the named check"
        assert before == after, "the check wrote to the tree it measured"

    def test_a_figure_claim_that_cannot_be_read_refuses_rather_than_being_skipped(self) -> None:
        """A malformed declaration is an input fault; silently dropping it reports a clean run."""
        with pytest.raises(integrity.RecordIntegrityError, match="PATTERN:SHAPE:SOURCE"):
            integrity.parse_figure_claim("instrument_discipline.py:line_count")
        with pytest.raises(integrity.RecordIntegrityError, match="the shipped shapes are"):
            integrity.parse_figure_claim("x:paragraph_count:/tmp/source.py:/tmp/document.md")
