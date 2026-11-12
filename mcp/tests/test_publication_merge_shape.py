"""The publication-merge shape rule in the finished-master chain proof.

The master chain proof admits a pull-request merge only when it publishes one recorded leaf
landing exactly: two parents, the first the step's start, the second the leaf's landed commit,
and the merge's tree exactly the landed tree. Everything else falls through and is refused.

A master line that synced from the repository's default branch keeps its base
pair; its sync-log entry names the default line's tip it reconciled with, and that tip admits the
default line's history, which enters the step from the side. A series contract has no memory
worktree, so its landing workflow is read from the memory work branch tip.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.series_closeout import _require_empty_landings_on_chain
from agents_remember.worktrees.series_merge_admission import (
    _landing_workflow,
    _merge_admission,
    _MergeAdmission,
    _published_landing,
    _require_admitted_merges,
    _require_admitted_step,
    _SpineStep,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_master_chain_offenders import _git

STEP = _SpineStep(side="code", step="leaf fixture-leaf-1", task_id="fixture-leaf-1")
LEAF = "fixture-leaf-1"


def _line(root: Path) -> tuple[Path, str, str]:
    repo = root / "line"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "line")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", f"land/{LEAF}", base)
    (repo / "leaf.txt").write_text("leaf\n", encoding="utf-8")
    _git(repo, "add", "leaf.txt")
    _git(repo, "commit", "-q", "-m", "leaf work")
    landed = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "line")
    return repo, base, landed


def _admission(base: str, landed: str) -> _MergeAdmission:
    return _MergeAdmission(
        heads=(),
        leaf_ranges=((LEAF, base, landed),),
        positions=(),
        legacy=False,
        authority_ranges=(),
        workflow="pull-request",
    )


def test_a_finished_master_admits_a_published_pr_merge(tmp_path: Path) -> None:
    repo, base, landed = _line(tmp_path)
    _git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "-m",
        f"Merge pull request #1 from land/{LEAF}",
        landed,
    )
    merge = _git(repo, "rev-parse", "HEAD")
    _require_admitted_step(repo, base, merge, STEP, _admission(base, landed))


def test_a_merge_no_rule_admits_is_refused_by_name(tmp_path: Path) -> None:
    """Both parents are admitted, the leaf's landing and a recorded official position, but the
    merge joining them is no sync head, no published landing and lies in no admitted history."""

    repo, base, landed = _line(tmp_path)
    _git(repo, "checkout", "-q", "-b", "official", base)
    (repo / "official.txt").write_text("official\n", encoding="utf-8")
    _git(repo, "add", "official.txt")
    _git(repo, "commit", "-q", "-m", "official work")
    position = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-B", "line", landed)
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge the official line by hand", position)
    merge = _git(repo, "rev-parse", "HEAD")
    admission = replace(_admission(base, landed), positions=(position,))
    with pytest.raises(CloseoutQueueError) as refused:
        _require_admitted_merges(repo, base, merge, admission, STEP)
    assert merge in refused.value.detail
    assert refused.value.detail.endswith(
        "A master line holds only leaf landings and its parent line; take the commit off the "
        "master branch and make the change in a leaf"
    )


def test_a_merge_with_content_of_its_own_is_refused(tmp_path: Path) -> None:
    repo, base, landed = _line(tmp_path)
    _git(repo, "merge", "-q", "--no-ff", "--no-commit", landed)
    (repo / "smuggled.txt").write_text("smuggled\n", encoding="utf-8")
    _git(repo, "add", "smuggled.txt")
    _git(repo, "commit", "-q", "-m", f"Merge pull request #1 from land/{LEAF}")
    merge = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(CloseoutQueueError) as refused:
        _require_admitted_step(repo, base, merge, STEP, _admission(base, landed))
    assert "adds history beyond the exact leaf landing chain" in refused.value.detail


def test_a_second_parent_that_is_not_the_leaf_landing_is_refused(tmp_path: Path) -> None:
    repo, base, landed = _line(tmp_path)
    _git(repo, "checkout", "-q", "-b", "other", base)
    (repo / "other.txt").write_text("other\n", encoding="utf-8")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-q", "-m", "other work")
    other = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "line")
    _git(repo, "merge", "-q", "--no-ff", "-m", "hand merge", other)
    merge = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(CloseoutQueueError):
        _require_admitted_step(repo, base, merge, STEP, _admission(base, landed))


def test_an_octopus_merge_is_refused(tmp_path: Path) -> None:
    repo, base, landed = _line(tmp_path)
    _git(repo, "checkout", "-q", "-b", "extra", base)
    (repo / "extra.txt").write_text("extra\n", encoding="utf-8")
    _git(repo, "add", "extra.txt")
    _git(repo, "commit", "-q", "-m", "extra work")
    _git(repo, "checkout", "-q", "line")
    _git(repo, "merge", "-q", "--no-ff", "-m", "octopus", landed, "extra")
    merge = _git(repo, "rev-parse", "HEAD")
    assert len(_git(repo, "rev-list", "--parents", "-n", "1", merge).split()) == 4
    with pytest.raises(CloseoutQueueError):
        _require_admitted_step(repo, base, merge, STEP, _admission(base, landed))


def _commit(repo: Path, name: str) -> str:
    (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    _git(repo, "add", f"{name}.txt")
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _series(
    root: Path,
    base: str,
    sync_log: tuple[dict[str, str], ...] = (),
    memory_repo: Path | None = None,
) -> WorktreeContract:
    """A series contract: no memory worktree, the memory side only a repository and a branch."""

    return WorktreeContract(
        kind="series",
        task_id="FIXTURE_MASTER",
        task_name="fixture-master",
        repo_name="agents-remember",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=root,
        task_root=root / "tasks",
        contract_path=root / "tasks" / "series-contract.md",
        task_artifact=root / "tasks" / "master.md",
        worktree_group=root / "worktrees",
        code_repo_path=root / "line",
        code_source_branch="ar/sprint",
        code_work_branch="line",
        code_base_commit=base,
        code_worktree=root / "line",
        memory_repo_path=memory_repo,
        memory_work_branch="ar/master" if memory_repo is not None else "",
        sync_log=sync_log,
    )


def _default_line_crossing(root: Path) -> tuple[Path, dict[str, str]]:
    """A master line whose last landing merged the default branch: a merge on main included."""

    repo, base, _landed = _line(root)
    landed = _commit(repo, "leaf landing")
    _git(repo, "checkout", "-q", "-b", "main", base)
    main_work = _commit(repo, "main work")
    _git(repo, "checkout", "-q", "-b", "feature", "main")
    feature = _commit(repo, "feature work")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge pull request #2 from feature", "feature")
    source = _commit(repo, "main tip")
    _git(repo, "checkout", "-q", "line")
    _git(repo, "merge", "-q", "--no-ff", "-m", "sync from main", source)
    facts = {
        "base": base,
        "landed": landed,
        "main_work": main_work,
        "feature": feature,
        "source": source,
        "head": _git(repo, "rev-parse", "HEAD"),
    }
    return repo, facts


def _crossing_entry(facts: dict[str, str], source: str = "") -> dict[str, str]:
    """The sync-log entry a repository-default sync writes: the base pair stays unchanged."""

    return {
        "codeBaseFrom": facts["base"],
        "codeBaseTo": facts["base"],
        "memoryBaseFrom": "",
        "memoryBaseTo": "",
        "code": "merge",
        "memory": "no-external-memory",
        "codeHeadTo": facts["head"],
        "memoryHeadTo": "",
        "syncSource": "repository-default",
        "codeSourceBranch": "main",
        "codeSourceCommit": source or facts["source"],
        "memorySourceBranch": "",
        "memorySourceCommit": "",
    }


SERIES_STEP = _SpineStep(side="code", step="the series ref", task_id="fixture-master")


def test_a_default_line_crossing_passes_the_chain_proof(tmp_path: Path) -> None:
    repo, facts = _default_line_crossing(tmp_path)
    series = _series(tmp_path, facts["base"], (_crossing_entry(facts),))
    admission = _merge_admission(series, [], side="code")
    _require_admitted_step(repo, facts["landed"], facts["head"], SERIES_STEP, admission)


def test_the_same_history_without_the_sync_record_is_refused(tmp_path: Path) -> None:
    repo, facts = _default_line_crossing(tmp_path)
    admission = _merge_admission(_series(tmp_path, facts["base"]), [], side="code")
    with pytest.raises(CloseoutQueueError) as refused:
        _require_admitted_step(repo, facts["landed"], facts["head"], SERIES_STEP, admission)
    for commit in (facts["main_work"], facts["feature"], facts["source"]):
        assert commit in refused.value.detail


def test_a_foreign_commit_beside_the_crossing_is_still_refused(tmp_path: Path) -> None:
    """Only the recorded tip's own history is admitted; a position past the step admits nothing."""

    repo, facts = _default_line_crossing(tmp_path)
    _git(repo, "checkout", "-q", "main")
    beyond_source = _commit(repo, "main after the recorded tip")
    _git(repo, "checkout", "-q", "line")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge past the recorded tip", beyond_source)
    direct = _commit(repo, "direct master commit")
    past = _commit(repo, "past the step")
    _git(repo, "reset", "-q", "--hard", direct)
    entries = (_crossing_entry(facts), _crossing_entry(facts, source=past))
    admission = _merge_admission(_series(tmp_path, facts["base"], entries), [], side="code")
    with pytest.raises(CloseoutQueueError) as refused:
        _require_admitted_step(repo, facts["landed"], direct, SERIES_STEP, admission)
    detail = refused.value.detail
    assert beyond_source in detail and direct in detail
    for admitted in (facts["main_work"], facts["feature"], facts["source"]):
        assert admitted not in detail


def test_an_empty_landing_on_a_default_line_source_pair_stands_on_the_chain(tmp_path: Path) -> None:
    """After a fast-forward default sync the master tip IS the source pair; a leaf may start there."""

    base, source, memory_base, memory_source = "1" * 40, "2" * 40, "3" * 40, "4" * 40
    entry = {
        "codeBaseFrom": base,
        "codeBaseTo": base,
        "memoryBaseFrom": memory_base,
        "memoryBaseTo": memory_base,
        "codeHeadTo": source,
        "memoryHeadTo": memory_source,
        "syncSource": "repository-default",
        "codeSourceCommit": source,
        "memorySourceCommit": memory_source,
    }
    series = replace(_series(tmp_path, base, (entry,)), memory_base_commit=memory_base)
    leaf = replace(
        series,
        kind="leaf",
        leaf_id="fixture-leaf-2",
        sync_log=(),
        code_base_commit=source,
        memory_base_commit=memory_source,
        integrated_code_commit=source,
        integrated_memory_content_commit=memory_source,
    )
    _require_empty_landings_on_chain(series, [], {"fixture-leaf-2": leaf})
    unrecorded = {key: value for key, value in entry.items() if "Source" not in key}
    with pytest.raises(CloseoutQueueError):
        _require_empty_landings_on_chain(
            replace(series, sync_log=(unrecorded,)), [], {"fixture-leaf-2": leaf}
        )


def _memory_repo(root: Path, workflow: str) -> Path:
    """A memory repository whose master branch commits the setting; its checkout has none."""

    memory = root / "memory"
    memory.mkdir()
    _git(memory, "init", "-q", "-b", "main")
    _git(memory, "config", "user.email", "t@example.invalid")
    _git(memory, "config", "user.name", "t")
    _commit(memory, "memory base")
    _git(memory, "checkout", "-q", "-b", "ar/master")
    (memory / "system").mkdir()
    settings = f'{{"version": 2, "landing": {{"workflow": "{workflow}"}}}}'
    (memory / "system" / "settings.json").write_text(settings, encoding="utf-8")
    _git(memory, "add", "system/settings.json")
    _git(memory, "commit", "-q", "-m", "landing workflow")
    _git(memory, "checkout", "-q", "main")
    return memory


def _pr_merge_at_the_tip(root: Path, workflow: str) -> tuple[Path, str, str, _MergeAdmission]:
    repo, base, landed = _line(root)
    _git(repo, "merge", "-q", "--no-ff", "-m", f"Merge pull request #3 from land/{LEAF}", landed)
    series = _series(root, base, memory_repo=_memory_repo(root, workflow))
    admission = _MergeAdmission(
        heads=(),
        leaf_ranges=((LEAF, base, landed),),
        positions=(base,),
        legacy=False,
        authority_ranges=((LEAF, base, landed),),
        workflow=_landing_workflow(series),
    )
    return repo, landed, _git(repo, "rev-parse", "HEAD"), admission


def test_a_series_reads_its_landing_workflow_from_its_memory_branch(tmp_path: Path) -> None:
    for workflow in ("pull-request", "local"):
        (tmp_path / workflow).mkdir()
        _, _, _, admission = _pr_merge_at_the_tip(tmp_path / workflow, workflow)
        assert admission.workflow == workflow


def test_the_real_route_admits_a_published_merge_at_the_tip_by_shape(tmp_path: Path) -> None:
    """The master's own step (task id of the master, no integrate record left) from the leaf's
    landing to the merge that published it: admitted by shape, and the merge check admits it too."""

    repo, landed, merge, admission = _pr_merge_at_the_tip(tmp_path, "local")
    archived = replace(admission, authority_ranges=())
    _require_admitted_step(repo, landed, merge, SERIES_STEP, archived)
    assert _published_landing(repo, merge, archived) == landed
    unlanded = replace(archived, leaf_ranges=((LEAF, landed, merge),))
    assert _published_landing(repo, merge, unlanded) is None
