"""The planned binding's one exact record: the master line, the four sides, and the Update counts.

Each case composes the real seat service over a real series contract and two real Git
repositories -- only the host and the comparison owner's answer are fake -- and protects one
promise of the explainer's planned-preparation path:

* a master tip that moves between the comparison read and the preparation refuses the build and
  the Update before any effect, and an unreadable master line refuses them by name (E03;
  MIK-R185 v2, MIK-R186 rule 4, MIK-R103 v2);
* the written packet, grants, binding and checked-out worktrees all name the same four sides, measured through
  the owners (E03);
* the planned comparison binds the master line -- an atomic master's integration branch, not its
  fork source -- and the initial read, the Update and the moved watcher all follow that line
  (I06; MIK-R189 rule 4);
* an Update answers its measured counts: changed blocks, the grades a grade-only leaf holds, and
  an explicit unavailability when a count cannot be read (G17; MIK-R186 rule 6, MIK-R187 rule 4).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.review_planned_comparison import (
    explainer_application_ports,
)
from agents_remember.application.review_tree_comparison import _code_sides
from agents_remember.canvas.explainer_grades import GradeStore, ScalePoint, grades_path
from agents_remember.canvas.explainer_paths import generation_path, leaf_workspace
from agents_remember.canvas.explainer_service import ExplainerDeps, LeafScope
from agents_remember.canvas.explainer_state import generation, read_binding
from agents_remember.models.knowledge.review_evidence import EvidencePacket
from agents_remember.models.knowledge.review_frame import (
    PLANNED_OWNER,
    planned_reference,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewKnowledgeSide,
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
    ReviewTreesResult,
)
from agents_remember.serving.explainer import (
    ExplainerComposition,
    ExplainerWiring,
    explainer_composition,
)
from agents_remember.serving.explainer_routes import register_explainer_routes
from agents_remember.serving.explainer_watcher import LeafSelection, notice_for
from agents_remember.serving.review_trees import ReviewTreesPort
from agents_remember_test_support.testing.prepared_comparison import prepared_from_port
from canvas_host_test_support import REPO, TASK, config, task
from fastapi import FastAPI
from fastapi.testclient import TestClient

LEAF = "260928-MIK-L183"
NOW = "2026-10-22T09:00:00+00:00"
WATCHER_PORTS = explainer_application_ports().watcher


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(root: Path, name: str, files: dict[str, str]) -> Path:
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


def _commit(repo: Path, path: str, text: str, message: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _world(tmp_path: Path):
    code = _repository(tmp_path, "repo", {"src/app.py": "x = 1\n"})
    memory = _repository(tmp_path, "memory", {"system/settings.md": "s\n"})
    runtime = config(tmp_path)
    root = task(runtime)
    return runtime, root, code, memory


def _canonical_contract(
    root: Path, code: Path, memory: Path, *, source_branch: str, work_branch: str
) -> Path:
    """One canonical series contract naming each side's fork base and integration line."""

    contract = root / "series-contract.md"
    coordination_root = root.parents[2]
    contract.write_text(
        "---\n"
        "schema: ar-series-contract/v1\n"
        f"task_id: {TASK.upper()}\n"
        f"task_name: {TASK}\n"
        f"repo_name: {REPO}\n"
        "kind: series\n"
        "workflow_kind: light-task\n"
        "memory_mode: external\n"
        "coordination:\n"
        f"  root: {coordination_root}\n"
        f"  task_root: {root}\n"
        f"  series_contract_path: {contract}\n"
        f"  task_artifact: {root / 'task.md'}\n"
        f"  worktree_group: {coordination_root / 'worktrees' / REPO / TASK}\n"
        "code:\n"
        f"  repo_path: {code}\n"
        f"  source_branch: {source_branch}\n"
        f"  work_branch: {work_branch}\n"
        f"  base_commit: {_git(code, 'rev-parse', 'main')}\n"
        f"  worktree: {code}\n"
        "memory:\n"
        "  mode: external\n"
        f"  repo_path: {memory}\n"
        f"  source_branch: {source_branch}\n"
        f"  work_branch: {work_branch}\n"
        f"  base_commit: {_git(memory, 'rev-parse', 'main')}\n"
        f"  ledger: {memory / 'memory.md'}\n"
        "human_review:\n"
        "  status: pending-review\n"
        "  approved_for_commit: no\n"
        "closeout:\n"
        "  status: not-started\n"
        "integration:\n"
        "  status: not-started\n"
        "  cleanup: pending\n"
        "---\n",
        encoding="utf-8",
    )
    return contract


def _planning_world(tmp_path: Path):
    """A planning leaf whose atomic master's integration line diverges from its fork base."""

    runtime, root, code, memory = _world(tmp_path)
    contract = _canonical_contract(
        root, code, memory, source_branch="main", work_branch="ar/master"
    )
    _git(code, "checkout", "-q", "-b", "ar/master")
    _git(memory, "checkout", "-q", "-b", "ar/master")
    _commit(code, "src/app.py", "x = 2\n", "the master line's code tip")
    _commit(memory, "system/settings.md", "s2\n", "the master line's memory tip")
    (root / f"{LEAF}.json").write_text(json.dumps({"id": LEAF, "status": "planning"}))
    return runtime, root, code, memory, contract


class _RecordingHost:
    """A host that records what it was asked and answers live."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.sent: list[str] = []
        self.archived: list[str] = []

    def create_seat(self, *, agent_id, workspace=None, title=None, capsule=None, labels=None):
        del workspace, title, capsule, labels
        self.created.append(agent_id)

    def send(self, agent_id: str, text: str, *, message_id: str | None = None) -> None:
        del agent_id, message_id
        self.sent.append(text)

    def resume(self, agent_id: str) -> None:
        del agent_id

    def archive(self, agent_id: str) -> None:
        self.archived.append(agent_id)

    def seat_state(self, agent_id: str) -> str:
        return "live" if agent_id in self.created else "missing"

    def cancel(self, agent_id: str, *, request_id: str):
        del agent_id, request_id
        return {
            "status": "cancelled",
            "turnStopped": True,
            "permissionClosed": True,
            "requestUncertain": False,
        }

    def in_turn(self, agent_id: str) -> bool:
        del agent_id
        return False


def _recorded_port_guard() -> ReviewTreesPort:
    def port(_query: object):
        raise AssertionError("the recorded comparison owner is not consulted for a planning leaf")

    return port


def _composition(
    runtime,
    host: _RecordingHost,
    contract: Path | None,
    *,
    port: ReviewTreesPort | None = None,
):
    composed = explainer_composition(
        runtime,
        port or _recorded_port_guard(),
        wiring=ExplainerWiring(
            host=host,
            application=explainer_application_ports(),
            comparison_prepare=prepared_from_port(port or _recorded_port_guard()),
            contract_for=None if contract is None else lambda _scope: contract,
        ),
    )
    assert composed.deps is not None
    return composed


def _deps(composition: ExplainerComposition) -> ExplainerDeps:
    """The composed dependencies, non-optional once the composition asserts them."""

    assert composition.deps is not None
    return composition.deps


def _client(composition: ExplainerComposition) -> TestClient:
    app = FastAPI()
    register_explainer_routes(app, composition)
    return TestClient(app)


def _scope() -> LeafScope:
    return LeafScope(repository_id=REPO, task=TASK, leaf_id=LEAF)


def _url(tail: str) -> str:
    return f"/api/explainer/{REPO}/{TASK}/{LEAF}/{tail}"


def _comparison_record(code: Path, memory: Path, number: int) -> ReviewTreeComparisonRecord:
    """One recorded comparison at the two repositories' current tips."""

    code_tree = _git(code, "rev-parse", "HEAD^{tree}")
    memory_tree = _git(memory, "rev-parse", "HEAD^{tree}")
    return ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=number,
        code_base=ReviewTreeSide(repository=str(code), tree=code_tree),
        code_candidate=ReviewTreeSide(repository=str(code), tree=code_tree),
        memory_base=ReviewTreeSide(repository=str(memory), tree=memory_tree),
        memory_candidate=ReviewTreeSide(repository=str(memory), tree=memory_tree),
        recorded_at=NOW,
    )


# --- E03: one exact record, refused before effects -------------------------------------------------


@pytest.mark.parametrize(
    ("interpose", "expected"),
    [
        ("move-tips", "binding-moved"),
        ("unreadable-memory", "evidence-source-unavailable"),
    ],
)
def test_a_master_line_change_between_comparison_and_prepare_refuses_the_build_before_effects(
    tmp_path: Path, interpose: str, expected: str
) -> None:
    """E03: the build refuses a master line that changed or vanished before any environment effect."""

    runtime, _root, code, memory, contract = _planning_world(tmp_path)
    host = _RecordingHost()
    composition = _composition(runtime, host, contract)
    deps = _deps(composition)
    original_prepare = deps.prepare

    def interposed_prepare(scope, facts, bound):
        if interpose == "move-tips":
            _commit(code, "src/app.py", "x = 3\n", "the master line moved")
            _commit(memory, "system/settings.md", "s3\n", "the master line moved")
        else:
            memory.rename(memory.with_name("memory-gone"))
        return original_prepare(scope, facts, bound)

    moved = replace(composition, deps=replace(deps, prepare=interposed_prepare))
    answer = _client(moved).post(
        _url("build"), json={"requestId": "r1", "message": "explain the plan"}
    )

    assert answer.status_code == 409
    assert answer.json()["reason"] == expected
    assert host.created == [] and host.sent == []
    # The leaf's state lives inside its folder (MIK-R211 v5 rule 12): the refused build leaves its
    # lock and the failure the page reads, and nothing of an environment.
    state = leaf_workspace(runtime, REPO, TASK, LEAF) / "state"
    assert sorted(
        path.relative_to(state.parent).as_posix() for path in state.parent.rglob("*")
    ) == [
        "state",
        "state/leaf.lock",
        "state/requests.json",
    ]
    assert generation(generation_path(runtime, REPO, TASK, LEAF)) == 0


def test_a_master_tip_move_between_comparison_and_prepare_refuses_the_update_before_effects(
    tmp_path: Path,
) -> None:
    """E03: the Update refuses a moved master line before it writes anything."""

    runtime, _root, code, memory, contract = _planning_world(tmp_path)
    host = _RecordingHost()
    composition = _composition(runtime, host, contract)
    client = _client(composition)
    built = client.post(_url("build"), json={"requestId": "r1", "message": "explain the plan"})
    assert built.status_code == 200 and host.created

    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    before = read_binding(workspace)
    assert before is not None
    before_names = sorted(path.name for path in workspace.iterdir())

    deps = _deps(composition)
    original_prepare = deps.prepare

    def interposed_prepare(scope, facts, bound):
        _commit(code, "src/app.py", "x = 3\n", "the master line moved")
        _commit(memory, "system/settings.md", "s3\n", "the master line moved")
        return original_prepare(scope, facts, bound)

    moved = replace(composition, deps=replace(deps, prepare=interposed_prepare))
    answer = _client(moved).post(_url("update"), json={})

    assert answer.status_code == 409
    assert answer.json()["reason"] == "binding-moved"
    assert read_binding(workspace) == before
    assert sorted(path.name for path in workspace.iterdir()) == before_names


def test_the_written_planned_environment_binds_one_exact_record_across_packet_grants_and_worktrees(
    tmp_path: Path,
) -> None:
    """E03: the packet, grants, binding and checked-out worktrees all name the same four measured sides."""

    runtime, _root, code, memory, contract = _planning_world(tmp_path)
    host = _RecordingHost()
    answer = _client(_composition(runtime, host, contract)).post(
        _url("build"), json={"requestId": "r1", "message": "explain the plan"}
    )
    assert answer.status_code == 200 and host.created

    workspace = leaf_workspace(runtime, REPO, TASK, LEAF)
    stored = read_binding(workspace)
    assert stored is not None
    binding = stored.binding
    code_tip = _git(code, "rev-parse", "ar/master")
    memory_tip = _git(memory, "rev-parse", "ar/master")
    code_tree = _git(code, "rev-parse", "ar/master^{tree}")
    memory_tree = _git(memory, "rev-parse", "ar/master^{tree}")

    evidence = json.loads((workspace / "instructions" / "evidence.json").read_text("utf-8"))
    packet = EvidencePacket.model_validate(evidence)
    comparison = packet.comparison
    assert comparison.comparison_owner == PLANNED_OWNER
    assert comparison.comparison_reference == planned_reference(TASK, LEAF)
    for side in (comparison.code_base, comparison.code_candidate):
        assert (side.repository, side.tree, side.commit) == (
            str(code),
            code_tree,
            code_tip,
        )
        assert side.state == "available"
    for side in (comparison.memory_base, comparison.memory_candidate):
        assert (side.repository, side.tree, side.commit) == (
            str(memory),
            memory_tree,
            memory_tip,
        )
        assert side.state == "available"

    grants = json.loads((workspace / "instructions" / "grants.json").read_text("utf-8"))
    grants_binding = grants["binding"]
    assert grants_binding["comparison_owner"] == PLANNED_OWNER
    assert grants_binding["code_base_tree"] == grants_binding["code_candidate_tree"] == code_tree
    assert (
        grants_binding["memory_base_tree"] == grants_binding["memory_candidate_tree"] == memory_tree
    )

    assert binding.code_base_tree == binding.code_candidate_tree == code_tree
    assert binding.code_base_commit == binding.code_candidate_commit == code_tip
    assert binding.memory_base_tree == binding.memory_candidate_tree == memory_tree
    assert binding.memory_base_commit == binding.memory_candidate_commit == memory_tip

    assert stored.snapshot is None
    checked_out = workspace / "trees"
    for side, source_repository, expected_tree in (
        ("code", code, code_tree),
        ("memory", memory, memory_tree),
    ):
        checkout = checked_out / side
        assert _git(checkout, "rev-parse", "HEAD^{tree}") == expected_tree
        assert _git(checkout, "branch", "--show-current") == ""
        assert str(checkout) in _git(source_repository, "worktree", "list", "--porcelain")
    assert (checked_out / "code" / "src" / "app.py").read_text("utf-8") == "x = 2\n"


# --- I06: the master line, not the source ----------------------------------------------------------


def test_the_planned_read_follows_the_master_line_not_the_source(
    tmp_path: Path,
) -> None:
    """I06: the planned comparison binds the integration branch's tips on both sides."""

    runtime, _root, code, memory, contract = _planning_world(tmp_path)
    composition = _composition(runtime, _RecordingHost(), contract)
    source_code = _git(code, "rev-parse", "main")
    source_memory = _git(memory, "rev-parse", "main")
    work_code = _git(code, "rev-parse", "ar/master")
    work_memory = _git(memory, "rev-parse", "ar/master")
    assert work_code != source_code and work_memory != source_memory

    deps = _deps(composition)
    bound = deps.comparison(_scope(), deps.facts(_scope()))

    assert bound.planned is not None
    assert bound.planned.code_tip == work_code != source_code
    assert bound.planned.memory_tip == work_memory != source_memory
    assert bound.binding.code_candidate_commit == work_code
    assert bound.binding.memory_candidate_commit == work_memory


def test_an_update_of_a_planned_environment_follows_the_master_line(
    tmp_path: Path,
) -> None:
    """I06: an Update rebinds the integration branch's new tips, never the unmoved source."""

    runtime, _root, code, memory, contract = _planning_world(tmp_path)
    host = _RecordingHost()
    client = _client(_composition(runtime, host, contract))
    built = client.post(_url("build"), json={"requestId": "r1", "message": "explain the plan"})
    assert built.status_code == 200 and host.created
    source_code = _git(code, "rev-parse", "main")
    source_memory = _git(memory, "rev-parse", "main")

    _commit(code, "src/app.py", "x = 3\n", "the master line moved")
    _commit(memory, "system/settings.md", "s3\n", "the master line moved")

    answer = client.post(_url("update"), json={})

    assert answer.status_code == 200
    updated = answer.json()["update"]
    binding = updated["binding"]["binding"]
    assert binding["code_candidate_commit"] == _git(code, "rev-parse", "ar/master") != source_code
    assert binding["code_candidate_tree"] == _git(code, "rev-parse", "ar/master^{tree}")
    assert (
        binding["memory_candidate_commit"] == _git(memory, "rev-parse", "ar/master")
    ) != source_memory
    assert binding["memory_candidate_tree"] == _git(memory, "rev-parse", "ar/master^{tree}")
    assert answer.json()["counts"]["blocks"] >= 1


def test_the_moved_watcher_follows_the_master_line_not_the_source(
    tmp_path: Path,
) -> None:
    """I06: the watcher's master-moved notice measures the integration branch's own move."""

    runtime, _root, code, memory, contract = _planning_world(tmp_path)
    host = _RecordingHost()
    client = _client(_composition(runtime, host, contract))
    built = client.post(_url("build"), json={"requestId": "r1", "message": "explain the plan"})
    assert built.status_code == 200 and host.created
    source_code = _git(code, "rev-parse", "main")
    work_from = _git(code, "rev-parse", "ar/master")

    _commit(code, "src/app.py", "x = 3\n", "the master line moved")
    _commit(memory, "system/settings.md", "s3\n", "the master line moved")

    notice = notice_for(runtime, LeafSelection(REPO, TASK, LEAF), WATCHER_PORTS)

    assert notice is not None
    assert notice.kind == "master-moved"
    assert notice.commit_from == work_from
    assert notice.commit_to == _git(code, "rev-parse", "ar/master")
    assert _git(code, "rev-parse", "main") == source_code


def test_an_organizational_master_binds_its_sprint_integration_line(
    tmp_path: Path,
) -> None:
    """I06: an organizational master's planned comparison binds its sprint integration branch.

    The canonical ``executionNature`` cell decides the line: for an organizational master the
    contract's source branch is the sprint's integration branch, which may have advanced past the
    master's own work branch.
    """

    runtime, root, code, memory = _world(tmp_path)
    contract = _canonical_contract(
        root, code, memory, source_branch="main", work_branch="ar/master"
    )
    _git(code, "checkout", "-q", "-b", "ar/master")
    _git(memory, "checkout", "-q", "-b", "ar/master")
    _commit(code, "src/app.py", "x = 2\n", "the master's own work")
    _commit(memory, "system/settings.md", "s2\n", "the master's own work")
    _git(code, "checkout", "-q", "main")
    _git(memory, "checkout", "-q", "main")
    _commit(code, "src/app.py", "x = 3\n", "the sprint integration line advanced")
    _commit(memory, "system/settings.md", "s3\n", "the sprint integration line advanced")
    (root / "task.json").write_text(
        json.dumps(
            {
                "id": TASK,
                "kind": "master",
                "executionNature": "organizational",
                "seriesContractPath": str(contract),
            }
        )
    )
    (root / f"{LEAF}.json").write_text(json.dumps({"id": LEAF, "status": "planning"}))
    own_code = _git(code, "rev-parse", "ar/master")
    own_memory = _git(memory, "rev-parse", "ar/master")
    sprint_code = _git(code, "rev-parse", "main")
    sprint_memory = _git(memory, "rev-parse", "main")
    assert own_code != sprint_code and own_memory != sprint_memory

    composition = _composition(runtime, _RecordingHost(), contract)
    deps = _deps(composition)
    bound = deps.comparison(_scope(), deps.facts(_scope()))

    assert bound.planned is not None
    assert bound.planned.code_tip == sprint_code != own_code
    assert bound.planned.memory_tip == sprint_memory != own_memory


# --- G17: the Update's measured counts -------------------------------------------------------------


def _recorded_world(tmp_path: Path):
    """A live leaf with a recorded comparison, whose owner's answer a test may replace."""

    runtime, root, code, memory = _world(tmp_path)
    (root / f"{LEAF}.json").write_text(json.dumps({"id": LEAF, "status": "inProgress"}))
    state = {"record": _comparison_record(code, memory, 7)}

    def port(_query):
        return ReviewTreesResult(
            state="trees",
            repository_id=REPO,
            master=TASK,
            leaf_id=LEAF,
            comparison=state["record"],
            code_sides=_code_sides(state["record"]),
            knowledge_sides=(
                ReviewKnowledgeSide(
                    side="before",
                    state="available",
                    tree=state["record"].memory_base.tree,
                    index_state="complete",
                ),
                ReviewKnowledgeSide(
                    side="after",
                    state="available",
                    tree=state["record"].memory_candidate.tree,
                    index_state="complete",
                ),
            ),
        )

    return runtime, root, code, memory, state, port


def test_an_update_of_a_grade_only_leaf_reports_measured_blocks_and_stale_grades(
    tmp_path: Path,
) -> None:
    """G17: a grade-only leaf's Update names its changed blocks and its stale grades."""

    runtime, root, code, memory, state, port = _recorded_world(tmp_path)
    host = _RecordingHost()
    client = _client(_composition(runtime, host, None, port=port))
    built = client.post(_url("build"), json={"requestId": "r1", "message": "explain this"})
    assert built.status_code == 200 and host.created
    stored = read_binding(leaf_workspace(runtime, REPO, TASK, LEAF))
    assert stored is not None

    store = GradeStore(grades_path(root, LEAF), LEAF)
    store.define(
        "risk",
        (
            ScalePoint(grade="high", meaning="high risk"),
            ScalePoint(grade="low", meaning="low risk"),
        ),
    )
    store.grade(
        references=("file:src/app.py@a",),
        category="risk",
        grade="high",
        reason="checked",
        fingerprints={"file:src/app.py@a": "fp-old"},
        binding=stored.binding,
        at=NOW,
    )

    _commit(code, "src/app.py", "x = 2\n", "the candidate moved")
    state["record"] = _comparison_record(code, memory, 8)

    answer = client.post(_url("update"), json={})

    assert answer.status_code == 200
    counts = answer.json()["counts"]
    assert counts["blocks"] >= 1
    assert counts["grades"] == 1
    assert counts["removedGrades"] == 0
    assert "countsUnavailable" not in counts


def test_an_update_with_an_unreadable_grade_store_marks_its_counts_unavailable(
    tmp_path: Path,
) -> None:
    """G17: a count that cannot be read is unavailable, never a silent zero."""

    runtime, root, code, memory, state, port = _recorded_world(tmp_path)
    host = _RecordingHost()
    client = _client(_composition(runtime, host, None, port=port))
    built = client.post(_url("build"), json={"requestId": "r1", "message": "explain this"})
    assert built.status_code == 200 and host.created
    grades_path(root, LEAF).parent.mkdir(parents=True, exist_ok=True)
    grades_path(root, LEAF).write_text("{ not json", encoding="utf-8")

    _commit(code, "src/app.py", "x = 2\n", "the candidate moved")
    state["record"] = _comparison_record(code, memory, 8)

    answer = client.post(_url("update"), json={})

    assert answer.status_code == 200
    counts = answer.json()["counts"]
    assert counts["blocks"] >= 1
    assert counts.get("parts") is None
    assert counts.get("grades") is None
    assert counts["countsUnavailable"] is True
    assert isinstance(counts["unavailable"], str) and counts["unavailable"]
    update_record = answer.json()["update"]
    assert update_record["counts"] == counts
    assert update_record["countsUnavailable"] is True
