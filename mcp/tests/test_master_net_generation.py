"""The master NET comparison is endpoint-correct and bound to a generation -- real Git.

``ICR-R13@v1`` requires a master review to expose one exact, generation-bound net comparison
between its declared source/knowledge base and its selected result, computed between the master
endpoints rather than summed from leaf counters. These cases measure that through the operations
the dashboard really calls: a real series contract on disk, real leaf enclosure contracts, real
code and memory repositories with real branches, and the real ``master_changeset`` /
``master_file_diff`` resolution plus the HTTP routes that serve them. Nothing here injects a
preconstructed payload or a hand-built comparison.

The load-bearing properties, one case each:

* two leaves that add and then remove one file net to exactly zero while both leaf counters
  stay nonzero -- the packet's conforming example, and the falsifier for its non-conforming
  one (child counters summed and labelled the master's net diff);
* a net-zero source result still reports the memory half's effects -- the packet's boundary;
* a pinned generation reopens byte-identically after the source branch advances while the live
  view moves on, with ``superseded`` currentness on the pinned read and ``current`` on the
  live one -- the completed master's recorded result;
* an opened file expansion stays bound to the generation the listing published;
* an unreadable leaf contract and an unresolvable leaf commit never invalidate the net;
* a master with no integrated result, and a pin nothing holds, are refused by name -- no
  substitution with a later branch tip -- while an unknown master keeps degrading to empty;
* a live leaf is labelled ``working`` beside the integrated net, and its uncommitted delta
  never leaks into the net.

The change inventory's own addresses are measured here too, because this is the module that owns
"the change-set the dashboard really calls" over real Git: a name holding a tab or a newline is
the row ``ACCEPTANCE.md`` A24 requires, and the four cases below drive it through the operations
that serve it -- the enumeration functions and the ``/api/changeset/task`` route -- rather than
through a private helper.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.changeset import (
    MasterFileRef,
    master_changeset,
    master_file_diff,
    register_changeset_routes,
)
from agents_remember.serving.changeset_endpoints import RecordedEndpointAbsent
from agents_remember.serving.master_net_generation import (
    MasterEndpointAbsent,
    MasterNetPins,
    select_master_net,
)
from agents_remember.worktrees.modules.git import (
    changed_files_with_counts,
    changed_worktree_paths,
    committed_changed_paths,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.evidence_unit

REPO = "master-net-fixture"
MASTER = "fixture-master"
LEAF_ONE = "fixture-l1"
LEAF_TWO = "fixture-l2"
FEATURE_PATH = "src/feature.py"
FEATURE_TEXT = "# added by the first leaf\nFEATURE = True\n"
KEEP_PATH = "src/keep.py"
KEEP_TEXT = "# untouched by both leaves\nKEEP = 1\n"
KEEP_EDITED_TEXT = "# edited after the recorded result\nKEEP = 2\n"
MEMORY_PATH = "memory.md"
MEMORY_TEXT = "# fixture memory ledger\n"
MEMORY_EDITED_TEXT = "# fixture memory ledger\n# a knowledge-side effect\n"


def _git(repo: Path, args: list[str]) -> str:
    """Run one git command against ``repo`` and return its stripped stdout."""

    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(path: Path, files: dict[str, str]) -> Path:
    """One real repository with one commit holding ``files``."""

    path.mkdir(parents=True, exist_ok=True)
    _git(path, ["init", "-q"])
    _git(path, ["config", "user.email", "master-net@example.invalid"])
    _git(path, ["config", "user.name", "master net fixture"])
    for relative, text in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(path, ["add", "-A"])
    _git(path, ["commit", "-q", "-m", "fixture base"])
    return path


def _commit(repo: Path, message: str) -> str:
    """Commit the current index state of ``repo`` and return the new commit id."""

    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-q", "-m", message])
    return _git(repo, ["rev-parse", "HEAD"])


@dataclass(frozen=True)
class MasterFixture:
    """One master-shaped world: real repos, branches, series contract and two leaf contracts."""

    config: McpRuntimeConfig
    code_repo: Path
    memory_repo: Path
    base_code: str
    base_memory: str
    tip_one: str
    tip_two: str


def _contract_path(task_root: Path, leaf: str) -> Path:
    return task_root / "enclosures" / leaf / "series-contract.md"


def _leaf_contract(
    series: WorktreeContract,
    leaf_id: str,
    *,
    base: str,
    landed: str,
    live_worktree: Path | None = None,
) -> WorktreeContract:
    """One cleaned leaf enclosure: recorded base and landed commit, no live worktree."""

    worktree = live_worktree or (series.worktree_group / f"wt-{leaf_id}")
    return WorktreeContract(
        task_id=series.task_id,
        task_name=series.task_name,
        repo_name=series.repo_name,
        workflow_kind="light-task",
        memory_mode="disabled",
        coordination_root=series.coordination_root,
        task_root=series.task_root,
        contract_path=_contract_path(series.task_root, leaf_id),
        task_artifact=series.task_artifact,
        worktree_group=series.worktree_group,
        code_repo_path=series.code_repo_path,
        code_source_branch=series.code_work_branch,
        code_work_branch=f"ar/{leaf_id}",
        code_base_commit=base,
        code_worktree=worktree,
        code_commit=landed,
        integrated_code_commit=landed,
        kind="leaf",
        leaf_id=leaf_id,
        parent_task_name=series.task_name,
        parent_contract_path=series.contract_path,
    )


def build_master_fixture(directory: Path) -> MasterFixture:
    """The packet's conforming scenario as committed history.

    Leaf one adds ``src/feature.py`` on the series branch; leaf two removes it again. The
    master net ``base -> series`` is therefore exactly zero while both leaf counters are not.
    """

    code_repo = _init_repo(directory / "code", {KEEP_PATH: KEEP_TEXT})
    memory_repo = _init_repo(directory / "memory", {MEMORY_PATH: MEMORY_TEXT})
    base_code = _git(code_repo, ["rev-parse", "HEAD"])
    base_memory = _git(memory_repo, ["rev-parse", "HEAD"])
    for repo in (code_repo, memory_repo):
        _git(repo, ["branch", "super", "HEAD"])
        _git(repo, ["branch", "series", "HEAD"])

    _git(code_repo, ["checkout", "-q", "series"])
    (code_repo / FEATURE_PATH).write_text(FEATURE_TEXT, encoding="utf-8")
    tip_one = _commit(code_repo, "leaf one adds the feature")
    (code_repo / FEATURE_PATH).unlink()
    tip_two = _commit(code_repo, "leaf two removes the feature again")

    coordination = directory / "coordination"
    task_root = coordination / "tasks" / REPO / MASTER
    series = WorktreeContract(
        task_id="260921_TEST_MASTER_NET",
        task_name=MASTER,
        repo_name=REPO,
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=coordination,
        task_root=task_root,
        contract_path=task_root / "series-contract.md",
        task_artifact=task_root / "task.md",
        worktree_group=directory / "worktrees",
        code_repo_path=code_repo,
        code_source_branch="super",
        code_work_branch="series",
        code_base_commit=base_code,
        code_worktree=code_repo,
        memory_repo_path=memory_repo,
        memory_source_branch="super",
        memory_work_branch="series",
        memory_base_commit=base_memory,
        memory_worktree=memory_repo,
        ledger_path=memory_repo / MEMORY_PATH,
        kind="series",
    )
    write_contract(series.contract_path, series)
    for leaf_id, base, landed in ((LEAF_ONE, base_code, tip_one), (LEAF_TWO, tip_one, tip_two)):
        leaf = _leaf_contract(series, leaf_id, base=base, landed=landed)
        leaf.contract_path.parent.mkdir(parents=True, exist_ok=True)
        write_contract(leaf.contract_path, leaf)
    return MasterFixture(
        config=McpRuntimeConfig(
            workspace_root=directory,
            coordination_root=coordination,
            config_path=directory / "config.json",
            transcript_root=directory / "transcripts",
        ),
        code_repo=code_repo,
        memory_repo=memory_repo,
        base_code=base_code,
        base_memory=base_memory,
        tip_one=tip_one,
        tip_two=tip_two,
    )


@pytest.fixture
def master_fixture(tmp_path: Path) -> MasterFixture:
    """One fresh master-shaped world per case; no case observes another's branch state."""

    return build_master_fixture(tmp_path / "master-net")


def test_two_leaves_that_add_then_remove_a_file_net_to_exactly_zero(
    master_fixture: MasterFixture,
) -> None:
    """The conforming example: net zero, both leaf counters nonzero, digest deterministic."""

    view = master_changeset(master_fixture.config, REPO, MASTER)

    assert view["code"] == []
    assert view["counters"]["code"] == {"files": 0, "insertions": 0, "deletions": 0}
    assert view["scope"] == "integrated"
    assert view["currentness"] == "current"
    generation = view["generation"]
    assert generation["codeBase"] == master_fixture.base_code
    assert generation["codeTip"] == master_fixture.tip_two
    assert generation["digest"]
    assert view == master_changeset(master_fixture.config, REPO, MASTER)

    leaves = {row["leafId"]: row for row in view["leaves"]}
    assert set(leaves) == {LEAF_ONE, LEAF_TWO}
    assert all(row["state"] == "committed" for row in leaves.values())
    assert leaves[LEAF_ONE]["counters"]["code"]["insertions"] > 0
    assert leaves[LEAF_TWO]["counters"]["code"]["deletions"] > 0
    # The falsifier for the non-conforming behavior: summed child counters are nonzero while
    # the master's net diff is exactly zero, so the net was computed between the master
    # endpoints and not summed from the leaves.
    summed_insertions = sum(row["counters"]["code"]["insertions"] for row in leaves.values())
    assert summed_insertions > 0
    assert view["counters"]["code"]["insertions"] == 0


def test_a_net_zero_source_result_still_reports_memory_effects(
    master_fixture: MasterFixture,
) -> None:
    """The boundary: knowledge net effects survive a zero source net."""

    _git(master_fixture.memory_repo, ["checkout", "-q", "series"])
    (master_fixture.memory_repo / MEMORY_PATH).write_text(MEMORY_EDITED_TEXT, encoding="utf-8")
    memory_tip = _commit(master_fixture.memory_repo, "a knowledge-side effect")

    view = master_changeset(master_fixture.config, REPO, MASTER)

    assert view["code"] == []
    assert [entry["path"] for entry in view["memory"]] == [MEMORY_PATH]
    assert view["counters"]["memory"]["insertions"] > 0
    assert view["generation"]["memoryTip"] == memory_tip
    assert view["currentness"] == "current"


def test_a_pinned_generation_reopens_after_the_branch_advances(
    master_fixture: MasterFixture,
) -> None:
    """The recorded result keeps resolving once the source branch moves on."""

    recorded = master_changeset(master_fixture.config, REPO, MASTER)
    generation = recorded["generation"]
    pins = MasterNetPins(code_base=generation["codeBase"], code_tip=generation["codeTip"])

    _git(master_fixture.code_repo, ["checkout", "-q", "series"])
    (master_fixture.code_repo / KEEP_PATH).write_text(KEEP_EDITED_TEXT, encoding="utf-8")
    moved_tip = _commit(master_fixture.code_repo, "a later leaf edits a kept file")
    assert moved_tip != master_fixture.tip_two

    pinned = master_changeset(master_fixture.config, REPO, MASTER, pins=pins)
    assert pinned["code"] == []
    assert pinned["generation"]["digest"] == generation["digest"]
    assert pinned["currentness"] == "superseded"

    live = master_changeset(master_fixture.config, REPO, MASTER)
    assert [entry["path"] for entry in live["code"]] == [KEEP_PATH]
    assert live["generation"]["codeTip"] == moved_tip
    assert live["generation"]["digest"] != generation["digest"]
    assert live["currentness"] == "current"


def test_an_opened_file_stays_bound_to_the_listed_generation(
    master_fixture: MasterFixture,
) -> None:
    """Expansion pins the AFTER side: the recorded bytes survive the branch advance."""

    pins = MasterNetPins(code_base=master_fixture.base_code, code_tip=master_fixture.tip_one)
    ref = MasterFileRef(repo=REPO, master=MASTER, kind="code", path=FEATURE_PATH, pins=pins)

    pinned = master_file_diff(master_fixture.config, ref)
    assert pinned["before"] is None
    assert pinned["after"] == {"content": FEATURE_TEXT}

    # The branch already advanced past the recorded tip (leaf two removed the file), and the
    # pinned read is unchanged while the live read follows the branch.
    live = master_file_diff(
        master_fixture.config,
        MasterFileRef(repo=REPO, master=MASTER, kind="code", path=FEATURE_PATH),
    )
    assert live["after"] is None
    assert master_file_diff(master_fixture.config, ref) == pinned

    with pytest.raises(MasterEndpointAbsent):
        master_file_diff(
            master_fixture.config,
            MasterFileRef(
                repo=REPO,
                master=MASTER,
                kind="code",
                path=FEATURE_PATH,
                pins=MasterNetPins(code_base=master_fixture.base_code, code_tip="0" * 40),
            ),
        )


def test_an_unreadable_child_never_invalidates_the_net(master_fixture: MasterFixture) -> None:
    """R24's child-navigation failure leaves the independently available net comparison intact."""

    series_root = master_fixture.config.coordination_root / "tasks" / REPO / MASTER
    _contract_path(series_root, LEAF_ONE).write_text("not a contract\n", encoding="utf-8")

    view = master_changeset(master_fixture.config, REPO, MASTER)
    assert view["code"] == []
    assert [row["leafId"] for row in view["leaves"]] == [LEAF_TWO]

    # An unresolvable leaf commit degrades the same way: the row drops, the net stays exact.
    leaf_two = _contract_path(series_root, LEAF_TWO).read_text(encoding="utf-8")
    _contract_path(series_root, LEAF_TWO).write_text(
        leaf_two.replace(master_fixture.tip_two, "0" * 40), encoding="utf-8"
    )
    degraded = master_changeset(master_fixture.config, REPO, MASTER)
    assert degraded["code"] == []
    assert degraded["leaves"] == []


def test_a_missing_code_endpoint_is_refused_never_substituted(
    master_fixture: MasterFixture,
) -> None:
    """No silent fallback to a later branch tip; an unknown master still degrades to empty."""

    selection = select_master_net(master_fixture.config, REPO, MASTER)
    assert selection is not None

    with pytest.raises(MasterEndpointAbsent) as caught:
        select_master_net(
            master_fixture.config,
            REPO,
            MASTER,
            MasterNetPins(code_base=selection.endpoints.code_base, code_tip="f" * 40),
        )
    assert "no branch or working tree is substituted" in str(caught.value)
    assert isinstance(caught.value, RecordedEndpointAbsent)

    series_root = master_fixture.config.coordination_root / "tasks" / REPO / MASTER
    series_path = series_root / "series-contract.md"
    unintegrated = replace(
        load_contract(series_path), code_work_branch="series-that-was-never-created"
    )
    write_contract(series_path, unintegrated)
    with pytest.raises(MasterEndpointAbsent) as missing:
        master_changeset(master_fixture.config, REPO, MASTER)
    assert "not substituted" in str(missing.value)

    unknown = master_changeset(master_fixture.config, REPO, "no-such-master")
    assert unknown["code"] == [] and unknown["memory"] == []
    assert unknown["generation"] is None
    assert unknown["currentness"] == "unmeasured"


def test_a_live_leaf_is_labelled_working_and_its_draft_stays_out_of_the_net(
    master_fixture: MasterFixture,
) -> None:
    """In-flight preview is labelled separately and never mixed into the integrated net."""

    live_dir = master_fixture.config.workspace_root / "worktrees" / "wt-live"
    live_dir.parent.mkdir(parents=True, exist_ok=True)
    _git(
        master_fixture.code_repo,
        ["worktree", "add", "-b", "ar/fixture-live", str(live_dir), "series"],
    )
    (live_dir / "src" / "draft.py").write_text("# uncommitted draft\n", encoding="utf-8")
    series_root = master_fixture.config.coordination_root / "tasks" / REPO / MASTER
    series = WorktreeContract(
        task_id="260921_TEST_MASTER_NET",
        task_name=MASTER,
        repo_name=REPO,
        workflow_kind="light-task",
        memory_mode="disabled",
        coordination_root=master_fixture.config.coordination_root,
        task_root=series_root,
        contract_path=series_root / "series-contract.md",
        task_artifact=series_root / "task.md",
        worktree_group=master_fixture.config.workspace_root / "worktrees",
        code_repo_path=master_fixture.code_repo,
        code_source_branch="series",
        code_work_branch="ar/fixture-live",
        code_base_commit=master_fixture.tip_one,
        code_worktree=live_dir,
        kind="leaf",
        leaf_id="fixture-live",
        parent_task_name=MASTER,
    )
    live_contract_path = _contract_path(series_root, "fixture-live")
    live_contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(live_contract_path, series)

    view = master_changeset(master_fixture.config, REPO, MASTER)
    assert view["code"] == []
    rows = {row["leafId"]: row for row in view["leaves"]}
    assert rows["fixture-live"]["state"] == "working"
    assert rows[LEAF_ONE]["state"] == "committed"


def test_the_master_routes_carry_the_generation_and_its_named_refusal(
    tmp_path: Path,
) -> None:
    """The HTTP layer publishes the bound generation and maps a bad pin to 404."""

    fixture = build_master_fixture(tmp_path / "master-net-routes")
    app = FastAPI()
    register_changeset_routes(app, fixture.config)
    client = TestClient(app)

    response = client.get("/api/changeset/master", params={"repo": REPO, "master": MASTER})
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "integrated"
    assert body["currentness"] == "current"
    assert body["generation"]["codeTip"] == fixture.tip_two

    refused = client.get(
        "/api/changeset/master",
        params={"repo": REPO, "master": MASTER, "codeTip": "0" * 40},
    )
    assert refused.status_code == 404
    assert refused.json()["status"] == "not-found"

    unknown = client.get("/api/changeset/master", params={"repo": REPO, "master": "no-such-master"})
    assert unknown.status_code == 200
    assert unknown.json()["code"] == []


def test_a_diff_failure_after_validation_is_refused_never_reported_as_zero(
    master_fixture: MasterFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable validated range is a named refusal, not an exact-looking empty net."""

    def fail_diff(repo: Path, base: str, head: str | None = None) -> list[dict[str, object]]:
        raise RuntimeError("simulated unreadable tree between the pinned endpoints")

    monkeypatch.setattr("agents_remember.serving.changeset.changed_files_with_counts", fail_diff)
    with pytest.raises(MasterEndpointAbsent) as caught:
        master_changeset(master_fixture.config, REPO, MASTER, include_leaves=False)
    assert caught.value.kind == "unresolvable"
    message = str(caught.value)
    assert "was validated but cannot be read" in message
    assert "simulated unreadable tree" in message
    assert "refused rather than reported as an empty range" in message
    print(f"L13EVIDENCE post_validation_diff_refused=True kind={caught.value.kind}")


# --- the change inventory's own addresses: names Git must quote (A24) ----------------------
#
# Git C-quotes a name holding a tab, a newline or a non-ASCII byte on every interface that is not
# NUL-delimited -- so a reader of such an interface receives `"tab\tname.py"`, an escaped spelling
# of the name rather than the name. These cases hold the inventory to the address Git itself
# reports for those files.

TAB_NAME = "src/tab\tname.py"
NEWLINE_NAME = "src/new\nline.py"
UNTRACKED_TAB_NAME = "src/untracked\ttab.py"
UNTRACKED_PLAIN_NAME = "src/plain_untracked.py"
PLAIN_NAME = "src/plain.py"
RENAME_SOURCE = "src/orig.py"
RENAMED_TAB_NAME = "src/renamed\torig.py"
# A name Git does not quote, but whose FIRST byte is a space: read as a stripped line it loses that
# space and stops naming the file. That is the other half of the same defect -- the line-oriented
# read is not only a quoting problem, and `git ls-files` puts this path first.
LEADING_SPACE_NAME = " leading_space.py"
# A name holding a LITERAL BACKSLASH -- a legal POSIX filename character, and the one class that
# separates the two mechanisms of the original defect. `-z` stops Git quoting a tab or a newline, so
# the trailing `.replace("\\", "/")` became a no-op for THOSE names and looked harmless; for a name
# that really contains a backslash it is still a corruption, rewriting a legal byte into a separator
# and then dropping the file through the `is_file` guard. Both spellings are here: one the tracked
# half reports (name-status/numstat) and one the untracked half reports (ls-files).
BACKSLASH_NAME = "src/back\\slash.py"
UNTRACKED_BACKSLASH_NAME = "src/untracked\\back.py"
INVENTORY_NAMES = frozenset(
    {
        TAB_NAME,
        NEWLINE_NAME,
        UNTRACKED_TAB_NAME,
        UNTRACKED_PLAIN_NAME,
        PLAIN_NAME,
        LEADING_SPACE_NAME,
        BACKSLASH_NAME,
        UNTRACKED_BACKSLASH_NAME,
    }
)


def _nul_git_paths(repo: Path, args: list[str]) -> set[str]:
    """Git's own answer to a path question, read NUL-delimited: the oracle these cases compare to."""

    completed = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return {record for record in completed.stdout.split("\0") if record}


def _quoted_name_repo(path: Path) -> Path:
    """One real repository holding eight changed files, six a line read cannot carry."""

    repo = _init_repo(path, {RENAME_SOURCE: "a\nb\nc\n", PLAIN_NAME: "unchanged\n"})
    (repo / PLAIN_NAME).write_text("changed\n", encoding="utf-8")
    (repo / TAB_NAME).write_text("one\ntwo\n", encoding="utf-8")
    (repo / NEWLINE_NAME).write_text("x\ny\nz\n", encoding="utf-8")
    (repo / BACKSLASH_NAME).write_text("b1\n", encoding="utf-8")
    _git(repo, ["add", "-A"])
    (repo / UNTRACKED_PLAIN_NAME).write_text("p1\np2\n", encoding="utf-8")
    (repo / UNTRACKED_TAB_NAME).write_text("u1\n", encoding="utf-8")
    (repo / LEADING_SPACE_NAME).write_text("s1\n", encoding="utf-8")
    (repo / UNTRACKED_BACKSLASH_NAME).write_text("b2\n", encoding="utf-8")
    return repo


def test_a_quoted_name_reaches_the_change_inventory_as_its_own_address(tmp_path: Path) -> None:
    """Eight real changed files in, eight rows out, each naming the file it says it names.

    The falsifier is the measurement that filed this defect: on the same fixture the inventory
    returned FOUR rows for five changes, two of them addresses (``"new/nline.py"``,
    ``"tab/tname.py"``) that resolve to nothing because the escape's backslash had been rewritten
    into a separator, and one real untracked change was absent entirely with nothing said about it.
    """

    repo = _quoted_name_repo(tmp_path / "quoted-names")

    rows = changed_files_with_counts(repo, "HEAD", None)
    by_path = {str(row["path"]): row for row in rows}

    oracle = _nul_git_paths(repo, ["diff", "--name-only", "-z", "HEAD", "--"])
    oracle |= _nul_git_paths(repo, ["ls-files", "--others", "--exclude-standard", "-z"])
    assert oracle == INVENTORY_NAMES
    assert set(by_path) == oracle, "the inventory must account for every path Git reports"
    assert len(rows) == 8
    for name in by_path:
        assert (repo / name).is_file(), name
        assert '"' not in name, name
    # The literal backslashes survive as THEMSELVES, which is the property the old trailing
    # ``.replace("\\", "/")`` destroyed and which ``-z`` alone does not defend: with the quoting
    # gone, that rewrite is a no-op for a tab or a newline and a corruption for a real backslash.
    assert BACKSLASH_NAME in by_path and UNTRACKED_BACKSLASH_NAME in by_path
    assert {name: by_path[name]["status"] for name in INVENTORY_NAMES} == {
        TAB_NAME: "A",
        NEWLINE_NAME: "A",
        UNTRACKED_TAB_NAME: "A",
        UNTRACKED_PLAIN_NAME: "A",
        LEADING_SPACE_NAME: "A",
        BACKSLASH_NAME: "A",
        UNTRACKED_BACKSLASH_NAME: "A",
        PLAIN_NAME: "M",
    }
    assert by_path[TAB_NAME]["insertions"] == 2
    assert by_path[NEWLINE_NAME]["insertions"] == 3
    assert by_path[BACKSLASH_NAME]["insertions"] == 1
    assert by_path[PLAIN_NAME] == {
        "path": PLAIN_NAME,
        "insertions": 1,
        "deletions": 1,
        "status": "M",
    }
    assert [str(row["path"]) for row in rows] == sorted(str(row["path"]) for row in rows)


def test_the_closeout_worklists_keep_a_quoted_name(tmp_path: Path) -> None:
    """Both changed-path worklists carry every deliverable a line read cannot name.

    A tab, a newline, a leading space or a literal backslash: ``changed_worktree_paths`` is the
    uncommitted half of the closeout's worklist and ``committed_changed_paths`` the committed one, and
    a deliverable that vanishes from either is a silent omission, which is the shape this master has
    already paid for once.
    """

    repo = _quoted_name_repo(tmp_path / "quoted-worklists")

    assert set(changed_worktree_paths(repo)) == INVENTORY_NAMES

    base = _git(repo, ["rev-parse", "HEAD"])
    _commit(repo, "the quoted names and the plain edit, committed")
    committed = set(committed_changed_paths(repo, base, ""))
    assert committed == INVENTORY_NAMES
    assert committed == _nul_git_paths(repo, ["diff", "--name-only", "-z", f"{base}..HEAD", "--"])


def test_a_rename_into_a_quoted_name_reports_its_destination(tmp_path: Path) -> None:
    """The two-field rename forms are paired positionally, not re-split on tabs.

    ``--numstat -z`` reports a rename as its counts, an EMPTY path field and then the source and
    the destination as two further records; ``--name-status -z`` reports the status and then both
    paths. A parser that splits either on tabs, or that reconstructs the ``a => b`` spelling the
    line-oriented form uses, cannot pair them once the destination itself holds a tab.
    """

    repo = _init_repo(tmp_path / "quoted-rename", {RENAME_SOURCE: "a\nb\nc\n"})
    base = _git(repo, ["rev-parse", "HEAD"])
    _git(repo, ["mv", RENAME_SOURCE, RENAMED_TAB_NAME])
    tip = _commit(repo, "rename into a name holding a tab")

    rows = changed_files_with_counts(repo, base, tip)

    assert [str(row["path"]) for row in rows] == [RENAMED_TAB_NAME]
    assert rows[0]["status"] == "R"
    assert rows[0]["insertions"] == 0 and rows[0]["deletions"] == 0
    assert (repo / str(rows[0]["path"])).is_file()
    assert RENAME_SOURCE not in {str(row["path"]) for row in rows}
    assert set(committed_changed_paths(repo, base, "")) == {RENAMED_TAB_NAME}


def test_the_change_set_route_serves_a_quoted_name_as_an_address(tmp_path: Path) -> None:
    """The route the dashboard's committed/working buttons call publishes the quoted addresses."""

    fixture = build_master_fixture(tmp_path / "quoted-route")
    live_dir = fixture.config.workspace_root / "worktrees" / "wt-quoted"
    live_dir.parent.mkdir(parents=True, exist_ok=True)
    _git(fixture.code_repo, ["worktree", "add", "-b", "ar/fixture-quoted", str(live_dir), "series"])
    (live_dir / TAB_NAME).write_text("one\ntwo\n", encoding="utf-8")
    (live_dir / NEWLINE_NAME).write_text("x\ny\nz\n", encoding="utf-8")
    series_root = fixture.config.coordination_root / "tasks" / REPO / MASTER
    live_contract_path = _contract_path(series_root, "fixture-quoted")
    live_contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(
        live_contract_path,
        WorktreeContract(
            task_id="260921_TEST_MASTER_NET",
            task_name=MASTER,
            repo_name=REPO,
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=fixture.config.coordination_root,
            task_root=series_root,
            contract_path=live_contract_path,
            task_artifact=series_root / "task.md",
            worktree_group=fixture.config.workspace_root / "worktrees",
            code_repo_path=fixture.code_repo,
            code_source_branch="series",
            code_work_branch="ar/fixture-quoted",
            code_base_commit=fixture.tip_two,
            code_worktree=live_dir,
            kind="leaf",
            leaf_id="fixture-quoted",
            parent_task_name=MASTER,
        ),
    )

    app = FastAPI()
    register_changeset_routes(app, fixture.config)
    client = TestClient(app)
    response = client.get(
        "/api/changeset/task",
        params={"repo": REPO, "master": MASTER, "leaf": "fixture-quoted", "mode": "working"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "working"
    assert {entry["path"] for entry in body["code"]} == {TAB_NAME, NEWLINE_NAME}
    for entry in body["code"]:
        assert (live_dir / entry["path"]).is_file(), entry["path"]
    assert body["counters"]["code"] == {"files": 2, "insertions": 5, "deletions": 0}
