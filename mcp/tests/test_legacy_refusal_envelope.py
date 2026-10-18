"""MIK-R46 rule 1/3 refusal envelope, seven-row sync matrix, public start proof.

Round-2 repairs for L46-R1-B03 (public refusal writes nothing before the format
preflight) and L46-R1-B05 (class-complete seven-row and start evidence). Every
case runs on a real Git fixture repository through a public tool entry point;
refusals assert the central ``legacy-format`` message with the judged sides, and
every refusal asserts the complete public state is unchanged (activation and
contract files, code/memory contents, indexes and refs).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import agents_remember
import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.lifecycle.direct_landing import direct_landing_tool
from agents_remember.application.memory_quality.answer import quality_result_answer
from agents_remember.application.memory_quality.controller import (
    run_memory_quality_request,
)
from agents_remember.application.review_tree_comparison import (
    live_review_trees,
    memory_converted,
    official_line_converted,
)
from agents_remember.application.worktree_tool_requests import LandedCommits
from agents_remember.cli.knowledge_ingest import EXIT_REFUSED
from agents_remember.cli.knowledge_ingest import run as run_knowledge_ingest
from agents_remember.mcp.tools.memory import citation_fix_payload
from agents_remember.models.memory import MemoryQualitySyncRequest
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.activation.atomic_series_activation import (
    activation_path,
    observe_atomic_series,
)
from agents_remember.worktrees.cutover_lock import (
    is_legacy_format_refusal,
    legacy_format_refusal,
)
from agents_remember.worktrees.knowledge_gate import converted_memory, landing_gate_refusal
from agents_remember.worktrees.modules.record_landing import (
    _landing_request,
    _legacy_format_next_step,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from checkpoint_landing_test_support import close_out_leaf
from test_closeout_queue import MASTER_A, REPO, QueueFixture
from test_direct_landing import _converted_request, _converted_series_fixture
from test_tool_answer_table import ROW_CASE_FRAMEWORK_KEYS
from test_worktree_support import git
from tool_entry_point_world import EntryPointWorld

LAYOUT = '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"\n}\n'


def _refs(repo: Path) -> str:
    return git(repo, "for-each-ref")


def _clean_status(*roots: Path) -> dict[str, str]:
    return {root.as_posix(): git(root, "status", "--porcelain") for root in roots}


def _clean_index(*roots: Path) -> dict[str, str]:
    return {root.as_posix(): git(root, "diff", "--cached", "--name-only") for root in roots}


def _byte_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "/.git/objects/" not in path.as_posix()
    }


def _legacy_leaf_world(root: Path) -> QueueFixture:
    """One public-tool-ready legacy leaf world (task docs carry enclosures)."""
    return QueueFixture(root, memory_mode="external", converted=False)


def _commit_on_branch(repo: Path, branch: str, name: str, text: str) -> str:
    """Commit one file on an existing branch that no checkout holds."""
    work = repo.parent / f"scratch-{branch.replace('/', '-')}"
    git(repo, "worktree", "add", work.as_posix(), branch)
    try:
        (work / name).write_text(text, encoding="utf-8")
        git(work, "add", name)
        git(work, "commit", "-q", "-m", f"Advance {branch}")
        return git(work, "rev-parse", "HEAD")
    finally:
        git(repo, "worktree", "remove", "--force", work.as_posix())


def _leaf_sync(fixture: QueueFixture, leaf: WorktreeContract, **kwargs) -> dict:
    return worktree_tools.worktree_sync_tool(
        fixture.cfg, contract_path=leaf.contract_path.as_posix(), **kwargs
    )


def test_series_sync_refusal_publishes_no_activation_and_changes_nothing(tmp_path: Path) -> None:
    """L46-R1-B03: a diverged legacy series sync refuses before the reconciling write.

    Work and source tips diverge (merge topology, all sides unconverted), so the
    format preflight precedes the activation selection publication: the activation
    snapshot stays absent, the contract keeps its bytes, and code/memory refs,
    staged indexes and worktree files are unchanged. This world configures no
    upstreams.
    """

    fixture = QueueFixture(
        tmp_path / "world", atomic_b=True, memory_mode="external", converted=False
    )
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    assert series.memory_repo_path is not None
    _commit_on_branch(
        series.memory_repo_path, series.memory_work_branch, "series-note.md", "# series\n"
    )
    _commit_on_branch(
        series.memory_repo_path, series.memory_source_branch, "official-note.md", "# official\n"
    )
    activation = activation_path(fixture.coord, series)
    assert not activation.exists()
    contract_before = series.contract_path.read_bytes()
    refs_before = (
        _refs(fixture.code),
        _refs(series.memory_repo_path),
        _clean_status(fixture.code, series.memory_repo_path),
        _clean_index(fixture.code, series.memory_repo_path),
        _byte_tree(fixture.code),
        _byte_tree(series.memory_repo_path),
    )
    result = worktree_tools.worktree_sync_tool(
        fixture.cfg,
        contract_path=series.contract_path.as_posix(),
        memory_sync_choice="merge-memory",
    )
    assert result.get("state") == "sync-unconverted-memory-locked", result
    text = json.dumps(result)
    assert "code legacy-format" in text and "agents-remember knowledge-convert" in text, text
    assert "preSyncHead" in text and "sourceCommit" in text and "merge base" in text, text
    assert not activation.exists()  # no reconciling selection was published
    assert observe_atomic_series(series).state == "vacant"
    assert series.contract_path.read_bytes() == contract_before
    assert (
        _refs(fixture.code),
        _refs(series.memory_repo_path),
        _clean_status(fixture.code, series.memory_repo_path),
        _clean_index(fixture.code, series.memory_repo_path),
        _byte_tree(fixture.code),
        _byte_tree(series.memory_repo_path),
    ) == refs_before


def test_series_sync_source_ahead_refuses_before_activation(tmp_path: Path) -> None:
    """L46-R1-B03: a source-ahead legacy series sync refuses before any write.

    Fast-forward topology with both sides unconverted: the preflight refuses with
    the judged sides, and the activation snapshot is never published.
    """

    fixture = QueueFixture(
        tmp_path / "world", atomic_b=True, memory_mode="external", converted=False
    )
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    assert series.memory_repo_path is not None
    _commit_on_branch(
        series.memory_repo_path, series.memory_source_branch, "official-note.md", "# official\n"
    )
    activation = activation_path(fixture.coord, series)
    frame_before = (
        _repo_frame(fixture.code, series.memory_repo_path),
        series.contract_path.read_bytes(),
    )
    result = worktree_tools.worktree_sync_tool(
        fixture.cfg, contract_path=series.contract_path.as_posix()
    )
    assert result.get("state") == "sync-unconverted-memory-locked", result
    text = json.dumps(result)
    assert "code legacy-format" in text, text
    assert "preSyncHead" in text and "sourceCommit" in text, text
    assert "merge base" not in text, text  # fast-forward names its two sides only
    assert not activation.exists()
    assert observe_atomic_series(series).state == "vacant"
    assert _repo_frame(fixture.code, series.memory_repo_path) == frame_before[0]
    assert series.contract_path.read_bytes() == frame_before[1]


def test_series_sync_work_ahead_is_already_current(tmp_path: Path) -> None:
    """L46-R1-B05: a legacy series whose work tip contains its source is admitted.

    The source is an ancestor of the work tip, so there is no incoming memory
    movement: the transaction admits the line as today, exactly as the leaf
    planner does, instead of refusing it for differing tips.
    """

    fixture = QueueFixture(
        tmp_path / "world", atomic_b=True, memory_mode="external", converted=False
    )
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    assert series.memory_repo_path is not None
    _commit_on_branch(
        series.memory_repo_path, series.memory_work_branch, "series-note.md", "# series\n"
    )
    result = worktree_tools.worktree_sync_tool(
        fixture.cfg,
        contract_path=series.contract_path.as_posix(),
        memory_sync_choice="merge-memory",
    )
    assert result.get("ok") is True, result
    assert result.get("state") in {"synced", "already-current"}, result
    assert "legacy-format" not in json.dumps(result), result
    assert observe_atomic_series(series).state == "active"


def test_series_sync_out_of_format_refuses_before_activation(tmp_path: Path) -> None:
    """L46-R1-B03: converted-target/unconverted-source series refuses before any write.

    The work tip holds the marker while the source tip dropped it, so taking the
    source would leave the text format: the preflight refuses naming both sides,
    and the activation snapshot is never published.
    """

    fixture = QueueFixture(
        tmp_path / "world", atomic_b=True, memory_mode="external", converted=True
    )
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    assert series.memory_repo_path is not None
    memory = series.memory_repo_path
    scratch = memory.parent / "scratch-series-out"
    git(memory, "worktree", "add", scratch.as_posix(), series.memory_source_branch)
    try:
        git(scratch, "rm", "-q", "knowledge/layout.json")
        git(scratch, "commit", "-q", "-m", "Drop the marker on the official line")
    finally:
        git(memory, "worktree", "remove", "--force", scratch.as_posix())
    activation = activation_path(fixture.coord, series)
    frame_before = (
        _repo_frame(fixture.code, memory),
        series.contract_path.read_bytes(),
    )
    result = worktree_tools.worktree_sync_tool(
        fixture.cfg,
        contract_path=series.contract_path.as_posix(),
        memory_sync_choice="merge-memory",
    )
    assert result.get("state") == "sync-unconverted-memory-locked", result
    text = json.dumps(result)
    assert "out of the text format" in text and "code legacy-format" in text, text
    assert "preSyncHead" in text and "sourceCommit" in text, text
    assert not activation.exists()
    assert observe_atomic_series(series).state == "vacant"
    assert _repo_frame(fixture.code, memory) == frame_before[0]
    assert series.contract_path.read_bytes() == frame_before[1]


def test_integration_refusal_keeps_the_contract_byte_identical(tmp_path: Path) -> None:
    """L46-R1-B03: a legacy integration refusal persists no blocked contract."""

    fixture = QueueFixture(tmp_path / "world", memory_mode="external", converted=False)
    leaf = close_out_leaf(load_contract(fixture.contracts[MASTER_A].contract_path))
    assert leaf.memory_worktree is not None and leaf.code_worktree is not None
    contract_before = leaf.contract_path.read_bytes()
    refs_before = (
        _refs(fixture.code),
        _refs(fixture.memory),
        _clean_status(leaf.code_worktree, leaf.memory_worktree),
        _clean_index(fixture.code, fixture.memory, leaf.code_worktree, leaf.memory_worktree),
        _byte_tree(leaf.code_worktree),
        _byte_tree(leaf.memory_worktree),
    )
    result = worktree_tools.worktree_integrate_tool(
        fixture.cfg, contract_path=leaf.contract_path.as_posix()
    )
    assert result.get("state") == "knowledge-gate-refused", result
    assert "code legacy-format" in json.dumps(result), result
    assert leaf.contract_path.read_bytes() == contract_before  # no blocked status written
    assert load_contract(leaf.contract_path).integration_status == "not-started"
    assert (
        _refs(fixture.code),
        _refs(fixture.memory),
        _clean_status(leaf.code_worktree, leaf.memory_worktree),
        _clean_index(fixture.code, fixture.memory, leaf.code_worktree, leaf.memory_worktree),
        _byte_tree(leaf.code_worktree),
        _byte_tree(leaf.memory_worktree),
    ) == refs_before


def test_record_landing_converted_candidate_on_legacy_line_reaches_its_validator(
    tmp_path: Path,
) -> None:
    """L46-R1-B04: a converted landed commit on a legacy line reaches validation.

    The line's prior tip does not make a present converted candidate legacy:
    the owner routes it to the landing request and returns its own
    validator's named rejection (here the scratch commit is no closed-out
    leaf's). Guard-vs-real-validator equality is asserted against the
    identical owner/request/validator functions on the same objects -- no
    ``code legacy-format`` anywhere. No ref, file, index entry or contract
    byte moves either way.
    """

    fixture = QueueFixture(tmp_path / "world", memory_mode="external", converted=False)
    leaf = load_contract(fixture.contracts[MASTER_A].contract_path)
    assert leaf.memory_worktree is not None and leaf.code_worktree is not None
    code_tip = git(fixture.code, "rev-parse", "main")
    work = fixture.memory / "scratch-conv"
    git(fixture.memory, "worktree", "add", "--detach", work.as_posix(), "main")
    (work / "knowledge").mkdir(parents=True, exist_ok=True)
    (work / "knowledge" / "layout.json").write_text(LAYOUT, encoding="utf-8")
    git(work, "add", "knowledge/layout.json")
    git(work, "commit", "-q", "-m", "Converted scratch commit")
    converted_tip = git(work, "rev-parse", "HEAD")
    git(fixture.memory, "worktree", "remove", "--force", work.as_posix())
    git(fixture.memory, "branch", "-f", "scratch-converted", converted_tip)
    contract_before = leaf.contract_path.read_bytes()
    refs_before = (
        _refs(fixture.code),
        _refs(fixture.memory),
        _clean_status(leaf.code_worktree, leaf.memory_worktree),
        _clean_index(fixture.code, fixture.memory, leaf.code_worktree, leaf.memory_worktree),
        _byte_tree(leaf.code_worktree),
        _byte_tree(leaf.memory_worktree),
    )
    # Architect ruling L111-A-LEGACY-FORMAT (R6): the validator refusal is
    # answered, not raised; its nonlegacy fact is unchanged, so only the read
    # site moves to the answer while every compared substance stays.
    answer = worktree_tools.worktree_record_landing_tool(
        fixture.cfg,
        contract_path=leaf.contract_path.as_posix(),
        landed=LandedCommits(code=code_tip, memory_content=converted_tip),
    )
    assert answer["ok"] is False
    refusal = answer["detail"]
    assert not is_legacy_format_refusal(refusal)
    direct = _landing_request(leaf, fixture.memory, code_tip, converted_tip)
    assert not isinstance(direct, str)
    assert refusal == landing_gate_refusal(direct)
    assert leaf.contract_path.read_bytes() == contract_before
    assert (
        _refs(fixture.code),
        _refs(fixture.memory),
        _clean_status(leaf.code_worktree, leaf.memory_worktree),
        _clean_index(fixture.code, fixture.memory, leaf.code_worktree, leaf.memory_worktree),
        _byte_tree(leaf.code_worktree),
        _byte_tree(leaf.memory_worktree),
    ) == refs_before


def test_record_landing_converted_lines_elsewhere_names_sync_and_keeps_the_marker(
    tmp_path: Path,
) -> None:
    """Ruling L111-THE-NEXT-STEP branch 2: converted lines elsewhere name the sync.

    The official line and the landed commit are unconverted, but the repository holds
    converted memory on another line, so the answer names worktree_sync merge-memory.
    Following the named step keeps the converted lines it names held. The world path
    holds the sentence's own converted-lines words, so the branch follows the probe,
    not the words.
    """

    fixture = QueueFixture(
        tmp_path / "Converted memory of this repository is on scratch" / "world",
        memory_mode="external",
        converted=False,
    )
    leaf = load_contract(fixture.contracts[MASTER_A].contract_path)
    assert leaf.memory_worktree is not None and leaf.code_worktree is not None
    code_tip = git(fixture.code, "rev-parse", "main")
    memory_tip = git(fixture.memory, "rev-parse", "main")
    work = fixture.memory / "scratch-conv"
    git(fixture.memory, "worktree", "add", "--detach", work.as_posix(), "main")
    (work / "knowledge").mkdir(parents=True, exist_ok=True)
    (work / "knowledge" / "layout.json").write_text(LAYOUT, encoding="utf-8")
    git(work, "add", "knowledge/layout.json")
    git(work, "commit", "-q", "-m", "Converted scratch commit")
    converted_tip = git(work, "rev-parse", "HEAD")
    git(fixture.memory, "worktree", "remove", "--force", work.as_posix())
    git(fixture.memory, "branch", "-f", "scratch-converted", converted_tip)
    answer = worktree_tools.worktree_record_landing_tool(
        fixture.cfg,
        contract_path=leaf.contract_path.as_posix(),
        landed=LandedCommits(code=code_tip, memory_content=memory_tip),
    )
    assert answer["ok"] is False
    assert answer["code"] == "legacy-format"
    next_step = answer["nextStep"]
    assert next_step["nextTool"] == "worktree_sync"
    assert next_step["nextArgs"]["memory_sync_choice"] == "merge-memory"
    # The trap is set: the world path holds the sentence's converted-lines words, yet
    # the branch follows the probe, which found converted lines here.
    assert "Converted memory of this repository is on scratch" in answer["detail"]
    # Ruling L111-THE-STEP-IS-A-CALL (R13): call the tool that next names with exactly
    # the arguments that next carries; the converted lines it names stay held.
    tool_args = next_step["nextArgs"]
    worktree_tools.worktree_sync_tool(
        fixture.cfg,
        contract_path=tool_args["contract_path"],
        memory_sync_choice=tool_args["memory_sync_choice"],
    )
    assert converted_memory(fixture.memory, converted_tip)


def test_unprobable_official_line_is_named_never_collapsed(tmp_path: Path) -> None:
    """L46-R1-B04: an official line Git cannot answer is named, not unconverted.

    `official_line_converted` answers False only when the probe answers; a dead
    branch resolves to named error text, which `memory_converted` carries and
    `live_review_trees` returns as a refusal instead of a silent None.
    """

    fixture = QueueFixture(tmp_path / "world", memory_mode="external", converted=False)
    leaf = load_contract(fixture.contracts[MASTER_A].contract_path)
    assert leaf.memory_repo_path is not None
    assert official_line_converted(leaf.memory_repo_path, leaf.memory_source_branch) is False
    named = official_line_converted(leaf.memory_repo_path, "ar/does-not-exist")
    assert isinstance(named, str) and "rev-parse" in named, named
    assert "never read as" in named, named
    unknown = replace(leaf, memory_source_branch="ar/does-not-exist")
    carried = memory_converted(unknown)
    assert isinstance(carried, str) and "rev-parse" in carried, carried
    refused = live_review_trees(fixture.coord, unknown, "0" * 40)
    assert refused is not None, "an unanswered probe is never a silent None"
    assert getattr(refused, "code", "") == "candidate_unresolved", refused
    assert "rev-parse" in getattr(refused, "detail", ""), refused


def test_record_landing_refusal_writes_nothing(tmp_path: Path) -> None:
    """L46-R1-B03: a legacy record-landing refusal changes no ref, file or contract.

    The world path holds the sentence's own converted-lines words, so the branch
    follows the probe — which found no converted memory here — not the words. The
    roots hold a space, a single quote and a dollar sign, so the shown line must
    run as shown.
    """

    fixture = QueueFixture(
        tmp_path / "Converted memory of this repository is on scratch's $line" / "world",
        memory_mode="external",
        converted=False,
    )
    leaf = load_contract(fixture.contracts[MASTER_A].contract_path)
    assert leaf.memory_worktree is not None and leaf.code_worktree is not None
    code_tip = git(fixture.code, "rev-parse", "main")
    memory_tip = git(fixture.memory, "rev-parse", "main")
    contract_before = leaf.contract_path.read_bytes()
    refs_before = (
        _refs(fixture.code),
        _refs(fixture.memory),
        _clean_status(leaf.code_worktree, leaf.memory_worktree),
        _byte_tree(leaf.code_worktree),
        _byte_tree(leaf.memory_worktree),
    )
    # Architect ruling L111-A-LEGACY-FORMAT (R6): the legacy-format refusal of
    # this packet tool is answered with ok false, the code legacy-format, the
    # central sentence unchanged and the conversion named; only the read site
    # moves, and the envelope proof is that nothing was written before it.
    answer = worktree_tools.worktree_record_landing_tool(
        fixture.cfg,
        contract_path=leaf.contract_path.as_posix(),
        landed=LandedCommits(code=code_tip, memory_content=memory_tip),
    )
    assert answer["ok"] is False
    assert answer["code"] == "legacy-format"
    assert is_legacy_format_refusal(answer["error"])
    assert is_legacy_format_refusal(answer["detail"])
    # Ruling L111-THE-NEXT-STEP (R11), branch 3: this never-converted repository
    # takes the first conversion, named with the roots filled in as a step that
    # is no tool call of this server (MIK-R111 rule 7: the next step where there
    # is one; no tool is invented for a CLI conversion).
    next_step = answer["nextStep"]
    assert "nextTool" not in next_step
    memory_root = leaf.memory_worktree
    assert memory_root is not None
    # Ruling L111-THE-STEP-IS-A-CALL (R13): the step is the call with separate values.
    values = next_step["nextArgs"]
    assert values == [
        "agents-remember",
        "knowledge-convert",
        memory_root.as_posix(),
        "--code",
        leaf.code_repo_path.as_posix(),
    ]
    assert shlex.split(next_step["command"]) == values
    # The trap is set: the world path holds the sentence's converted-lines words, yet
    # the branch follows the probe, which found no converted memory here.
    assert "Converted memory of this repository is on scratch" in answer["detail"]
    assert leaf.contract_path.read_bytes() == contract_before
    assert (
        _refs(fixture.code),
        _refs(fixture.memory),
        _clean_status(leaf.code_worktree, leaf.memory_worktree),
        _byte_tree(leaf.code_worktree),
        _byte_tree(leaf.memory_worktree),
    ) == refs_before
    # Follow the shown step and rebuild nothing: the values come from the answer, the
    # program name alone is replaced by this build's interpreter, and the child
    # process converts the memory, which then holds the layout marker.
    src_root = Path(agents_remember.__file__).resolve().parent
    env = dict(os.environ)
    prior_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{src_root}{os.pathsep}{prior_pythonpath}" if prior_pythonpath else str(src_root)
    )
    run = subprocess.run(
        [sys.executable, "-m", "agents_remember.cli", *values[1:]],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert (memory_root / "knowledge" / "layout.json").is_file()


def test_record_landing_unknown_probe_carries_no_next_step(tmp_path: Path) -> None:
    """Ruling L111-THE-NEXT-STEP branch 4: an unanswerable probe names no step.

    The central sentence stays whole (it still carries the legacy code); with no
    decision in it, the mapping names no next step.
    """

    repo = tmp_path / "Converted memory of this repository is on scratch" / "mem"
    repo.mkdir(parents=True)
    (repo / ".git").write_text("gitdir: /nonexistent-ar-probe-target", encoding="utf-8")
    sentence = legacy_format_refusal(
        repo, operation="worktree_record_landing", subject="the memory"
    )
    assert is_legacy_format_refusal(sentence)
    assert "unknown" in sentence
    # The trap is set: the path holds the sentence's own converted-lines words, yet
    # the branch follows the probe's unknown case, not the words.
    assert "Converted memory of this repository is on scratch" in sentence
    assert (
        _legacy_format_next_step(
            contract_path="contract",
            memory_root="memory",
            code_repo="code",
            case=sentence.case,
        )
        is None
    )


def test_fast_forward_of_two_unconverted_sides_refuses_with_sides(tmp_path: Path) -> None:
    """Rule 3 row: fast-forward, target unconverted, source unconverted: refused."""

    fixture = _legacy_leaf_world(tmp_path / "ff-legacy")
    leaf = load_contract(fixture.contracts[MASTER_A].contract_path)
    assert leaf.memory_repo_path is not None and leaf.memory_worktree is not None
    _commit_on_branch(
        leaf.memory_repo_path, leaf.memory_source_branch, "official.md", "# official\n"
    )
    frame_before = (
        _refs(fixture.code),
        _refs(leaf.memory_repo_path),
        _clean_status(fixture.code, leaf.memory_repo_path),
        _byte_tree(fixture.code),
        _byte_tree(leaf.memory_repo_path),
    )
    result = _leaf_sync(fixture, leaf)
    assert result.get("state") == "sync-unconverted-memory-locked", result
    text = json.dumps(result)
    assert "code legacy-format" in text, text
    assert "preSyncHead" in text and "sourceCommit" in text, text
    assert (
        _refs(fixture.code),
        _refs(leaf.memory_repo_path),
        _clean_status(fixture.code, leaf.memory_repo_path),
        _byte_tree(fixture.code),
        _byte_tree(leaf.memory_repo_path),
    ) == frame_before


def test_fast_forward_from_legacy_source_onto_converted_target_is_refused(tmp_path: Path) -> None:
    """Rule 3 row: fast-forward, target converted, source unconverted: refused.

    The converted target holds the marker on an ancestor; the unconverted source
    tip deleted it, so taking the source would leave the text format.
    """

    fixture = _legacy_leaf_world(tmp_path / "ff-out")
    leaf = load_contract(fixture.contracts[MASTER_A].contract_path)
    memory = leaf.memory_repo_path
    worktree = leaf.memory_worktree
    assert memory is not None and worktree is not None
    # The work line converts first, inside its own checkout, so the later official
    # commit that drops the marker leaves a converted target behind an unconverted
    # source: taking it would leave the text format.
    (worktree / "knowledge").mkdir(parents=True, exist_ok=True)
    (worktree / "knowledge" / "layout.json").write_text(LAYOUT, encoding="utf-8")
    git(worktree, "add", "knowledge/layout.json")
    git(worktree, "commit", "-q", "-m", "Convert the work line")
    scratch = memory.parent / "scratch-ff-out"
    git(memory, "worktree", "add", scratch.as_posix(), leaf.memory_source_branch)
    try:
        git(scratch, "merge", "-q", "--ff-only", leaf.memory_work_branch)
        git(scratch, "rm", "-q", "knowledge/layout.json")
        git(scratch, "commit", "-q", "-m", "Drop the marker on the official line")
    finally:
        git(memory, "worktree", "remove", "--force", scratch.as_posix())
    frame_before = (
        _refs(fixture.code),
        _refs(memory),
        _clean_status(fixture.code, memory),
        _byte_tree(fixture.code),
        _byte_tree(memory),
    )
    result = _leaf_sync(fixture, leaf)
    assert result.get("state") == "sync-unconverted-memory-locked", result
    text = json.dumps(result)
    assert "out of the text format" in text and "code legacy-format" in text, text
    assert "preSyncHead" in text and "sourceCommit" in text, text
    assert (
        _refs(fixture.code),
        _refs(memory),
        _clean_status(fixture.code, memory),
        _byte_tree(fixture.code),
        _byte_tree(memory),
    ) == frame_before


def test_fast_forward_of_legacy_target_takes_a_converted_source(tmp_path: Path) -> None:
    """Rule 3 row: fast-forward, target unconverted, source converted: admitted.

    The line converts by taking the converted source; refs move by design here.
    """

    fixture = _legacy_leaf_world(tmp_path / "ff-adopt")
    leaf = load_contract(fixture.contracts[MASTER_A].contract_path)
    memory = leaf.memory_repo_path
    assert memory is not None
    scratch = memory.parent / "scratch-ff-adopt"
    git(memory, "worktree", "add", scratch.as_posix(), leaf.memory_source_branch)
    try:
        (scratch / "knowledge").mkdir(parents=True, exist_ok=True)
        (scratch / "knowledge" / "layout.json").write_text(LAYOUT, encoding="utf-8")
        git(scratch, "add", "knowledge/layout.json")
        git(scratch, "commit", "-q", "-m", "Convert the official line")
    finally:
        git(memory, "worktree", "remove", "--force", scratch.as_posix())
    result = _leaf_sync(fixture, leaf)
    assert result.get("ok") is True and result.get("state") == "synced", result
    work_tip = git(memory, "rev-parse", leaf.memory_work_branch)
    assert git(memory, "show", f"{work_tip}:knowledge/layout.json").strip() != ""


def test_legacy_already_current_and_skip_memory_syncs_are_admitted(tmp_path: Path) -> None:
    """Rule 3 rows: already-current and skip-memory on legacy sides: as today."""

    fixture = _legacy_leaf_world(tmp_path / "ff-current")
    leaf = load_contract(fixture.contracts[MASTER_A].contract_path)
    current = _leaf_sync(fixture, leaf)
    assert current.get("ok") is True, current
    assert current.get("state") in {"already-current", "synced"}, current
    skipped = _leaf_sync(fixture, leaf, memory_sync_choice="skip-memory")
    assert skipped.get("ok") is True, skipped
    assert skipped.get("state") in {"already-current", "synced"}, skipped


def test_skip_memory_with_pending_work_skips_without_judging(tmp_path: Path) -> None:
    """L46-R1-B05: skip-memory on diverged legacy sides is admitted, refs frozen.

    The skip row must hold pending work to skip: work and source tips diverge
    (merge topology), yet the sync is admitted and no memory ref moves, because
    the memory sides are never judged.
    """

    fixture = _legacy_leaf_world(tmp_path / "skip-pending")
    leaf = load_contract(fixture.contracts[MASTER_A].contract_path)
    memory = leaf.memory_repo_path
    worktree = leaf.memory_worktree
    assert memory is not None and worktree is not None
    (worktree / "leaf.md").write_text("# leaf\n", encoding="utf-8")
    git(worktree, "add", "leaf.md")
    git(worktree, "commit", "-q", "-m", "Leaf memory work")
    _commit_on_branch(memory, leaf.memory_source_branch, "official.md", "# official\n")
    refs_before = (_refs(fixture.code), _refs(memory))
    result = _leaf_sync(fixture, leaf, memory_sync_choice="skip-memory")
    assert result.get("ok") is True, result
    assert "legacy-format" not in json.dumps(result), result
    assert (_refs(fixture.code), _refs(memory)) == refs_before


def test_merge_of_three_unconverted_sides_refuses_with_sides_and_no_writes(
    tmp_path: Path,
) -> None:
    """Rule 3 row: merge, base/own/incoming all unconverted: refused, nothing moves."""

    fixture = _legacy_leaf_world(tmp_path / "merge-legacy")
    leaf = load_contract(fixture.contracts[MASTER_A].contract_path)
    memory = leaf.memory_repo_path
    worktree = leaf.memory_worktree
    assert memory is not None and worktree is not None
    (worktree / "leaf.md").write_text("# leaf\n", encoding="utf-8")
    git(worktree, "add", "leaf.md")
    git(worktree, "commit", "-q", "-m", "Leaf memory work")
    _commit_on_branch(memory, leaf.memory_source_branch, "official.md", "# official\n")
    refs_before = (_refs(fixture.code), _refs(memory))
    files_before = (
        _byte_tree(worktree),
        _clean_status(worktree, memory),
        _clean_index(worktree, memory),
    )
    result = _leaf_sync(fixture, leaf, memory_sync_choice="merge-memory")
    assert result.get("state") == "sync-unconverted-memory-locked", result
    text = json.dumps(result)
    assert "code legacy-format" in text, text
    assert "preSyncHead" in text and "sourceCommit" in text and "merge base" in text, text
    assert (_refs(fixture.code), _refs(memory)) == refs_before
    assert (
        _byte_tree(worktree),
        _clean_status(worktree, memory),
        _clean_index(worktree, memory),
    ) == files_before


def test_series_closeout_preview_and_apply_refuse_legacy(tmp_path: Path) -> None:
    """L46-R1-B01: series preview/apply refuse unconverted memory, moving nothing.

    The preview answers ``knowledge-gate-refused`` (never ``would-closeout``);
    the apply raises the same central message. Both leave every ref, workdir
    file, staged blob, index entry and contract byte where it was. This is the
    ORDINARY series closeout route only -- not the distinct certified/prepared
    owner (`prepared_certification`), which has its own guard and its own
    witness below.
    """

    fixture = QueueFixture(
        tmp_path / "world", atomic_b=True, memory_mode="external", converted=False
    )
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    assert series.memory_repo_path is not None
    assert series.code_worktree is not None
    frame_before = (
        _refs(fixture.code),
        _refs(series.memory_repo_path),
        _clean_status(series.code_worktree, series.memory_repo_path),
        _clean_index(fixture.code, series.memory_repo_path, series.code_worktree),
        _staged_stream(series.code_worktree, series.memory_repo_path),
        _byte_tree(series.code_worktree),
        _byte_tree(series.memory_repo_path),
        series.contract_path.read_bytes(),
    )
    messages = worktree_tools.CloseoutCommitMessages(
        code="atomic master code", memory="atomic master memory"
    )
    preview = worktree_tools.worktree_closeout_preview_tool(
        fixture.cfg, series.contract_path.as_posix(), messages
    )
    assert preview.get("state") == "knowledge-gate-refused", preview
    assert "code legacy-format" in json.dumps(preview), preview
    # The apply's preflight precedes publication, so no completion facts are needed.
    with pytest.raises(RuntimeError, match="code legacy-format"):
        worktree_tools.worktree_closeout_apply_tool(
            fixture.cfg,
            series.contract_path.as_posix(),
            messages,
            worktree_tools.CloseoutApproval(intent_note="developer approved"),
        )
    assert series.contract_path.read_bytes() == frame_before[7]
    assert (
        _refs(fixture.code),
        _refs(series.memory_repo_path),
        _clean_status(series.code_worktree, series.memory_repo_path),
        _clean_index(fixture.code, series.memory_repo_path, series.code_worktree),
        _staged_stream(series.code_worktree, series.memory_repo_path),
        _byte_tree(series.code_worktree),
        _byte_tree(series.memory_repo_path),
    ) == frame_before[:7]


def test_worktree_start_on_unconverted_memory_reports_legacy_format(tmp_path: Path) -> None:
    """Rule 6 recovery fields: a public start works and says legacy-format.

    The start result carries ``memoryFormat: "legacy-format"`` with the first-
    conversion procedure naming the conversion command.
    """

    world = EntryPointWorld()
    try:
        world.build()
        kind, payload = world.started
        assert kind == "RETURNED" and payload is not None, (kind, payload)
        memory = payload.get("memory")
        assert isinstance(memory, dict), payload
        assert memory.get("memoryFormat") == "legacy-format", memory
        procedure = json.dumps(memory.get("memoryFormatProcedure"))
        assert "agents-remember knowledge-convert" in procedure, procedure
    finally:
        world.close()


def _repo_frame(*repos: Path) -> tuple:
    """The complete per-route state frame: refs, workdir status and file bytes."""

    return tuple((_refs(repo), _clean_status(repo), _byte_tree(repo)) for repo in repos)


def _staged_stream(*roots: Path) -> dict[str, str]:
    """The full staged-index content stream: paths with their staged blob hashes.

    Path-only cached diffs cannot prove a changed staged blob at the same
    path survived; `ls-files --stage` names every staged entry with its blob,
    including through a linked worktree's own index.
    """

    return {root.as_posix(): git(root, "ls-files", "-s") for root in roots}


def test_direct_landing_legacy_refusal_preserves_all_state(tmp_path: Path) -> None:
    """L46-R1-B05: public direct landing on legacy memory refuses, moving nothing.

    The cutover lock probes the memory line before anything is captured or
    written, so the public tool answers a refusal and every ref, workdir file
    and contract byte is unchanged.
    """

    fixture = _converted_series_fixture(tmp_path / "legacy-direct")
    memory = fixture["memory"]
    # The checkout stays on the accepted work branch; its new tip drops the
    # marker, so the line is legacy while the branch binding still holds.
    git(memory, "rm", "-r", "-q", "knowledge")
    git(memory, "commit", "-q", "-m", "legacy line")
    assert git(memory, "rev-parse", "--abbrev-ref", "HEAD") == "leaf"
    contract = fixture["contract"]
    request = _converted_request(fixture, fixture["world"].code_base)
    frame_before = (
        _repo_frame(fixture["code"], memory),
        contract.contract_path.read_bytes(),
    )
    result = direct_landing_tool(fixture["config"], request)
    assert result.get("ok") is False, result
    assert "legacy-format" in json.dumps(result), result
    assert _repo_frame(fixture["code"], memory) == frame_before[0]
    assert contract.contract_path.read_bytes() == frame_before[1]


def test_checkpoint_landing_legacy_refusal_preserves_all_state(
    tmp_path: Path,
) -> None:
    """L46-R1-B05: public checkpoint landing on legacy memory refuses, moving nothing.

    An unfinished master shares the integration preflight, so its partial line
    is refused with the central message and every ref, workdir file and
    contract byte is unchanged.
    """

    fixture = QueueFixture(
        tmp_path / "world", atomic_b=True, memory_mode="external", converted=False
    )
    master = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    assert master.memory_repo_path is not None
    frame_before = (
        _repo_frame(fixture.code, master.memory_repo_path),
        master.contract_path.read_bytes(),
    )
    result = worktree_tools.worktree_checkpoint_landing_tool(
        fixture.cfg, contract_path=master.contract_path.as_posix()
    )
    assert result.get("ok") is False, result
    assert "legacy-format" in json.dumps(result), result
    assert _repo_frame(fixture.code, master.memory_repo_path) == frame_before[0]
    assert master.contract_path.read_bytes() == frame_before[1]


def test_knowledge_ingest_legacy_refusal_preserves_all_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """L46-R1-B05: the public knowledge writer refuses legacy memory, moving nothing.

    The ingest CLI answers the unconverted-write refusal with the conversion
    step and exit-refused; every ref, workdir file and contract byte is
    unchanged.
    """

    fixture = QueueFixture(tmp_path / "world", memory_mode="external", converted=False)
    leaf = close_out_leaf(load_contract(fixture.contracts[MASTER_A].contract_path))
    assert leaf.memory_worktree is not None and leaf.code_worktree is not None
    frame_before = (
        _repo_frame(fixture.code, fixture.memory),
        _clean_index(fixture.code, fixture.memory),
        _byte_tree(leaf.code_worktree),
        _byte_tree(leaf.memory_worktree),
        _staged_stream(
            fixture.code,
            fixture.memory,
            leaf.code_worktree,
            leaf.memory_worktree,
        ),
        leaf.contract_path.read_bytes(),
    )
    rc = run_knowledge_ingest(argparse.Namespace(contract=leaf.contract_path.as_posix()))
    assert rc == EXIT_REFUSED, rc
    out = capsys.readouterr().out
    assert "legacy-format" in out, out
    assert _repo_frame(fixture.code, fixture.memory) == frame_before[0]
    assert _clean_index(fixture.code, fixture.memory) == frame_before[1]
    assert _byte_tree(leaf.code_worktree) == frame_before[2]
    assert _byte_tree(leaf.memory_worktree) == frame_before[3]
    assert (
        _staged_stream(fixture.code, fixture.memory, leaf.code_worktree, leaf.memory_worktree)
        == frame_before[4]
    )
    assert leaf.contract_path.read_bytes() == frame_before[5]


def test_completed_master_integration_legacy_refusal_preserves_all_state(
    tmp_path: Path,
) -> None:
    """L46-R1-B05: a completed master's final integration refuses legacy, moving nothing.

    Distinct from the unfinished-master checkpoint pair: every task document
    reads Completed, so the only remaining bar is the memory format, and the
    shared integration preflight refuses it with the central message. Every
    ref, workdir file, index entry and contract byte is unchanged.
    """

    fixture = QueueFixture(
        tmp_path / "world", atomic_b=True, memory_mode="external", converted=False
    )
    series = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    for name in ("task.json", "leaf-b.json"):
        path = fixture.tasks / "master-b" / name
        document = read_task_doc(path)
        write_task_doc(path.parent, document.model_copy(update={"status": "Completed"}))
    # The master is fully closed out on paper (documents Completed, closeout
    # recorded with the live branch tips), so the only remaining bar is the
    # memory format -- exactly the completed-master shape the checkpoint
    # (unfinished-master) pair does not cover.
    series = load_contract(series.contract_path)
    memory_repo = series.memory_repo_path
    assert memory_repo is not None
    code_tip = git(fixture.code, "rev-parse", series.code_work_branch)
    memory_tip = git(memory_repo, "rev-parse", series.memory_work_branch)
    closed = replace(
        series,
        human_review_status="approved",
        approved_for_commit=True,
        closeout_status="completed",
        code_commit=code_tip,
        memory_content_commit=memory_tip,
    )
    write_contract(closed.contract_path, closed)
    series = load_contract(series.contract_path)
    memory_repo = series.memory_repo_path
    code_worktree = series.code_worktree
    assert memory_repo is not None and code_worktree is not None
    frame_before = (
        _repo_frame(fixture.code, memory_repo),
        _clean_index(fixture.code, memory_repo, code_worktree),
        _byte_tree(code_worktree),
        _byte_tree(memory_repo),
        series.contract_path.read_bytes(),
    )
    result = worktree_tools.worktree_integrate_tool(
        fixture.cfg, contract_path=series.contract_path.as_posix()
    )
    assert result.get("ok") is False, result
    assert "legacy-format" in json.dumps(result), result
    assert _repo_frame(fixture.code, memory_repo) == frame_before[0]
    assert _clean_index(fixture.code, memory_repo, code_worktree) == frame_before[1]
    assert _byte_tree(code_worktree) == frame_before[2]
    assert _byte_tree(memory_repo) == frame_before[3]
    assert series.contract_path.read_bytes() == frame_before[4]


def test_citation_fix_legacy_refusal_carries_code_and_detail_to_the_caller(
    tmp_path: Path,
) -> None:
    """MIK-R46 rule 2 at the MCP boundary: the caller learns how it converts.

    The application refusal carries `code legacy-format` with the conversion
    message, and the public `citation_fix` answer projection must carry both
    to the caller -- the logged whole result alone does not satisfy the rule.
    """

    fixture = QueueFixture(tmp_path / "world", memory_mode="external", converted=False)
    leaf = close_out_leaf(load_contract(fixture.contracts[MASTER_A].contract_path))
    assert leaf.memory_worktree is not None
    (leaf.memory_worktree / "onboarding").mkdir(exist_ok=True)
    result = citation_fix_payload(fixture.cfg, REPO, contract_path=leaf.contract_path.as_posix())
    assert result.get("ok") is False, result
    assert result.get("code") == "legacy-format", result
    detail = result.get("detail")
    assert isinstance(detail, str) and "knowledge-convert" in detail, result


def test_memory_quality_check_unconverted_refusal_answer_validates(tmp_path: Path) -> None:
    """The ruled repair: the unconverted refusal whole carries the answer's keys.

    On an unconverted world the check refuses (code ``unconverted-memory``); the whole
    result carries ``status``, ``blockingCount`` and ``nextAction`` with the projection's
    own values, so the delivered answer validates against it.
    """

    fixture = QueueFixture(tmp_path / "world", memory_mode="external", converted=False)
    whole = run_memory_quality_request(
        fixture.cfg, MemoryQualitySyncRequest(mode="sync", repo_id=REPO)
    )
    assert whole.get("ok") is False, whole
    assert whole.get("code") == "unconverted-memory", whole
    delivered = quality_result_answer(dict(whole))
    assert delivered["status"] == whole["status"] == "failed", (delivered, whole)
    assert delivered["blockingCount"] == whole["blockingCount"] == 0, (delivered, whole)
    assert delivered["nextAction"] == whole["nextAction"] == "worktree_sync", (
        delivered,
        whole,
    )
    outside = sorted(set(delivered) - set(whole) - ROW_CASE_FRAMEWORK_KEYS)
    assert outside == [], (outside, delivered, whole)
