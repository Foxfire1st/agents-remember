"""Truthful converted onboarding drift (MIK-R44@v2): one test per rule.

The behavioural cases discriminate their change: each named scenario fails
before its fix and passes after (rules 1-4, the rule-7 transaction cases, and
the L44-C1 repair cases below). The description/parity/packaging cases assert
exact surfaces rather than behaviour: every required statement on every named
surface and its generated copies (rule 8 / L44-C1-10), equal numbers at the
three entrances with `include_drift=false` (rule 5), and report/snapshot
content from one measurement (rule 6).

L44-C1 repair cases: the staged refusal restores the prepared ignore line and
index (02) and preserves a concurrent ignore edit while returning the refusal
(02); a validation exception restores state and keeps its name, on dry run and
on real adoption (03), a preparation failure restores nothing and keeps its
name (03), a Git failure after the owned ignore write still restores (03), a
tree-capture failure after staging still restores (03), an unreadable ignore
at restore preserves the file and the refusal (03), and a concurrent edit
under a validation error keeps the named error (03); a malicious hook cannot change the recorded tree (04); the context leaf
route binds the leaf base and agrees with the drift tool (05); the baseline
drift block carries the full checked envelope (06); a file onboarding root is
an error through the tool (07); empty and malformed sidecars count truthfully
(08); an unbuildable base withholds new/carried refusals (09); unreadable
history reports unavailable on a converted tree (11).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.context_packet import (
    ContextPacketRequest,
    build_context_packet,
)
from agents_remember.application.memory_tools import (
    MemoryBranches,
    drift_check_tool,
    memory_baseline_adopt_tool,
    memory_baseline_status_tool,
)
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.memory import baseline
from agents_remember.memory_quality.converted_check import CONVERTED_BASE_RULE
from agents_remember.memory_quality.knowledge_validator.trees import (
    knowledge_tree_from_directory,
)
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.worktrees.services import (
    reset_worktree_services,
)
from converted_onboarding_drift_support import (
    LAYOUT_MARKER,
    REPO_ID,
    _adoptable_converted_world,
    _anchor,
    _code_tree,
    _commit,
    _context,
    _converted_memory,
    _current_anchor,
    _fingerprint,
    _stale_anchor,
    _summarize,
    _write_card,
    bound_validation_services,
)
from test_worktree_support import git


@pytest.fixture
def bound_validation() -> Any:
    """The validator binding the baseline cases need (see the support module)."""

    with bound_validation_services():
        yield


def test_converted_trees_take_the_converted_branch_and_skip_legacy_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 1: the converted branch is taken and legacy discovery is not called."""

    code = _code_tree(tmp_path)
    memory = _converted_memory(tmp_path)
    _write_card(memory, "pkg/alpha.py", [_current_anchor(code, "pkg/alpha.py")])
    _write_card(memory, "pkg/beta.py", [_stale_anchor()])
    _commit(memory)

    packet = _summarize(code_repository_root=code, context=_context(memory, tmp_path))

    assert packet["status"] == "checked", packet
    assert packet["basis"] == "converted-references", packet
    assert packet["count"] == 2, packet
    assert packet["actionableCount"] == 1, packet
    assert packet["states"] == {"current": 1, "stale": 1}, packet
    for entry in packet["actionableSample"]:
        assert "classification" not in entry, entry


def test_converted_result_names_every_measured_field(tmp_path: Path) -> None:
    """Rule 2: every converted field is present, in anchors, with bounded samples only."""

    code = _code_tree(tmp_path)
    memory = _converted_memory(tmp_path)
    _write_card(memory, "pkg/alpha.py", [_current_anchor(code, "pkg/alpha.py")])
    _write_card(memory, "pkg/beta.py", [_stale_anchor(), _stale_anchor()])
    _commit(memory)

    packet = _summarize(
        code_repository_root=code, context=_context(memory, tmp_path), detail_limit=1
    )

    assert packet["status"] == "checked", packet
    assert packet["memoryRoot"] == memory.resolve().as_posix(), packet
    assert packet["codeRoot"] == code.resolve().as_posix(), packet
    assert packet["sidecarCount"] == 2, packet
    assert packet["count"] == 3, packet
    assert packet["states"] == {"current": 1, "stale": 2}, packet
    assert packet["actionableCount"] == 2, packet
    assert len(packet["actionableSample"]) == 1, packet
    sample = packet["actionableSample"][0]
    assert (sample["path"], sample["field"], sample["target"], sample["state"]) == (
        "onboarding/pkg/beta.py.json",
        "references.1.targets.0",
        "pkg/beta.py",
        "stale",
    ), sample
    assert packet["violationCount"] == 0, packet
    assert packet["violationSample"] == [], packet
    assert packet["reportOnlyCount"] == 2, packet
    assert packet["unreadableSidecars"] == [], packet


def test_empty_missing_and_unbuildable_base_trees(tmp_path: Path) -> None:
    """Rule 3: zero means empty, unread is error, an unbuildable base is one refusal."""

    code = _code_tree(tmp_path)

    empty = _converted_memory(tmp_path, "empty")
    _commit(empty)
    packet = _summarize(code_repository_root=code, context=_context(empty, tmp_path))
    assert packet["status"] == "checked", packet
    assert packet["sidecarCount"] == 0 and packet["count"] == 0, packet
    assert packet["violationCount"] == 0, packet

    missing = _context(_converted_memory(tmp_path, "gone"), tmp_path)
    missing.onboarding_root = missing.memory_root / "onboarding"

    shutil.rmtree(missing.memory_root, ignore_errors=True)
    packet = _summarize(code_repository_root=code, context=missing)
    assert packet["status"] == "error", packet
    assert "count" not in packet and "sidecarCount" not in packet, packet

    memory = _converted_memory(tmp_path, "unbased")
    _write_card(memory, "pkg/alpha.py", [_current_anchor(code, "pkg/alpha.py")])
    packet = _summarize(
        code_repository_root=code,
        context=_context(memory, tmp_path),
        base=lambda _root, _candidate: (None, "the base cache is unreachable"),
    )
    assert packet["violationCount"] == 1, packet
    (finding,) = packet["violationSample"]
    assert (finding["path"], finding["rule"]) == (
        "knowledge/layout.json",
        CONVERTED_BASE_RULE,
    ), finding


def test_carried_anchor_is_refused_as_carried_stale_never_as_new(tmp_path: Path) -> None:
    """Rule 4 as decided by MIK-R53 rule 11: with the leaf base a carried deleted anchor is
    stale, refused as carried-stale at the leaf route -- never as a new anchor-path refusal."""

    code = _code_tree(tmp_path)
    memory = _converted_memory(tmp_path, "candidate")
    carried = _anchor(blob="0" * 40, content="sha256:" + "1" * 64)
    _write_card(memory, "pkg/gone.py", [carried])
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (base_dir / "knowledge").mkdir()
    (base_dir / "layout.json").write_text(LAYOUT_MARKER, encoding="utf-8")
    (base_dir / "knowledge" / "layout.json").write_text(LAYOUT_MARKER, encoding="utf-8")
    (base_dir / "onboarding" / "pkg").mkdir(parents=True)
    (base_dir / "onboarding" / "pkg" / "gone.py.json").write_text(
        (memory / "onboarding" / "pkg" / "gone.py.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (base_dir / "onboarding" / "pkg" / "gone.py.md").write_text(
        "# gone\n\nSee [1].\n", encoding="utf-8"
    )

    plain = _summarize(code_repository_root=code, context=_context(memory, tmp_path))
    assert plain["states"] == {"current": 0, "stale": 1}, plain
    assert plain["violationCount"] == 1, plain
    assert plain["violationSample"][0]["rule"] == "R22.6-anchor-path", plain

    def _leaf_base(_root: Path, _candidate: Any) -> tuple[Any, str | None]:
        return knowledge_tree_from_directory(base_dir), None

    based = _summarize(
        code_repository_root=code, context=_context(memory, tmp_path), base=_leaf_base
    )
    assert based["states"] == {"current": 0, "stale": 1}, based
    assert based["actionableCount"] == 1 and based["violationCount"] == 1, based
    assert based["violationSample"][0]["rule"] == "R22.6-carried-stale", based


def test_three_consumers_return_one_answer(tmp_path: Path) -> None:
    """Rule 5: drift check, context drift and both baseline tools share one summary."""

    config, _code, _memory = _adoptable_converted_world(tmp_path, stale=True)

    checked = drift_check_tool(config, repo_id=REPO_ID, detail_limit=10)
    assert checked["ok"] is True, checked
    assert checked["basis"] == "converted-references", checked

    packet = build_context_packet(
        config,
        ContextPacketRequest(repo_id=REPO_ID, include_drift=True),
    )
    assert packet["drift"]["count"] == checked["count"], packet["drift"]
    assert packet["drift"]["actionableCount"] == checked["actionableCount"], packet["drift"]
    assert packet["drift"]["violationCount"] == checked["violationCount"], packet["drift"]

    status = memory_baseline_status_tool(config, repo_id=REPO_ID)
    assert status["drift"]["count"] == checked["count"], status
    assert status["drift"]["actionableCount"] == checked["actionableCount"], status
    assert status["drift"]["violationCount"] == checked["violationCount"], status

    quiet = build_context_packet(
        config,
        ContextPacketRequest(repo_id=REPO_ID, include_drift=False),
    )
    assert quiet["drift"] == {"status": "notChecked"}, quiet


def test_report_and_snapshot_come_from_the_same_result(tmp_path: Path) -> None:
    """Rule 6: the Markdown report and the observer snapshot carry one measurement."""

    code = _code_tree(tmp_path)
    memory = _converted_memory(tmp_path)
    _write_card(memory, "pkg/alpha.py", [_current_anchor(code, "pkg/alpha.py")])
    _write_card(memory, "pkg/beta.py", [_stale_anchor()])
    _commit(memory)

    packet = _summarize(code_repository_root=code, context=_context(memory, tmp_path))

    report = Path(str(packet["reportPath"]))
    text = report.read_text(encoding="utf-8")
    assert "converted-references" in text, text
    assert "current: 1, stale: 1" in text, text
    assert "validator violations (refusing): 0" in text, text

    snapshot = drift_snapshot_path(
        _context(memory, tmp_path).coordination_root,
        repository=code.name,
        branch="main",
    )
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["schema"] == "ar-drift-snapshot/v1", payload
    assert payload["counts"] == {"current": 1, "stale": 1, "violation": 0}, payload
    assert payload["actionableCount"] == 1, payload


def test_complete_drift_fingerprint_changes_beyond_equal_counts_and_sample(tmp_path: Path) -> None:
    """R166 amendment 3: the producer fingerprints findings before the detail limit."""
    code = _code_tree(tmp_path)
    memory = _converted_memory(tmp_path)
    _write_card(memory, "pkg/alpha.py", [_stale_anchor()])
    _write_card(memory, "pkg/beta.py", [_stale_anchor()])
    _commit(memory)
    context = _context(memory, tmp_path)
    snapshot = drift_snapshot_path(context.coordination_root, repository=code.name, branch="main")
    _summarize(code_repository_root=code, context=context, detail_limit=1)
    first = json.loads(snapshot.read_text())
    _summarize(code_repository_root=code, context=context, detail_limit=1)
    again = json.loads(snapshot.read_text())
    assert first["actionableFingerprint"] == again["actionableFingerprint"]
    # Move beta's authored reference slot beyond alpha's unchanged sample.
    # Its stale target and every count stay the same; the finding's field changes.
    sidecar = memory / "onboarding/pkg/beta.py.json"
    document = json.loads(sidecar.read_text())
    document["references"]["2"] = document["references"].pop("1")
    sidecar.write_text(canonical_text(document))
    (memory / "onboarding/pkg/beta.py.md").write_text("# pkg/beta.py\n\nSee [2].\n")
    _summarize(code_repository_root=code, context=context, detail_limit=1)
    changed = json.loads(snapshot.read_text())
    assert first["counts"] == changed["counts"]
    assert first["actionableCount"] == changed["actionableCount"] == 2
    assert first["rows"] == changed["rows"]
    assert first["actionableFingerprint"] != changed["actionableFingerprint"]
    _summarize(code_repository_root=code, context=context, detail_limit=0)
    unsampled = json.loads(snapshot.read_text())
    assert unsampled["rows"] == []
    assert changed["actionableFingerprint"] == unsampled["actionableFingerprint"]
    (memory / "onboarding/pkg/beta.py.json").write_text("{")
    _summarize(code_repository_root=code, context=context, detail_limit=1)
    assert json.loads(snapshot.read_text())["actionableFingerprint"] is None


def test_baseline_states_for_converted_trees(tmp_path: Path) -> None:
    """Rule 7: status reports blocked-invalid, blocked-drift and unavailable distinctly."""

    config, _code, _memory = _adoptable_converted_world(tmp_path)
    assert memory_baseline_status_tool(config, repo_id=REPO_ID)["state"] == "ready"

    config, _code, _memory = _adoptable_converted_world(tmp_path / "stale", stale=True)
    assert memory_baseline_status_tool(config, repo_id=REPO_ID)["state"] == "blocked-drift"

    config, _code, _memory = _adoptable_converted_world(tmp_path / "bad", violation=True)
    refused = memory_baseline_status_tool(config, repo_id=REPO_ID)
    assert refused["state"] == "blocked-invalid", refused
    assert refused["ok"] is False, refused


def test_accept_drift_never_clears_a_validator_violation(
    tmp_path: Path, bound_validation: Any
) -> None:
    """Rule 7: `accept_drift` accepts stale anchors only."""

    config, _code, _memory = _adoptable_converted_world(tmp_path, violation=True)
    for accept in (False, True):
        refused = memory_baseline_adopt_tool(
            config,
            repo_id=REPO_ID,
            accept_drift=accept,
            branches=MemoryBranches(source_branch="main", work_branch="main"),
        )
        assert refused["ok"] is False and refused["state"] == "blocked-invalid", refused


def test_stale_anchors_adopt_once_accepted(tmp_path: Path, bound_validation: Any) -> None:
    """Rule 7: accepted stale anchors adopt; the committed tree is attributed."""

    config, code, memory = _adoptable_converted_world(tmp_path, stale=True)
    refused = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert refused["state"] == "blocked-drift", refused

    adopted = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert adopted["ok"] is True and adopted["state"] == "adopted", adopted
    message = git(memory, "log", "-1", "--format=%B")
    assert f"Code-Commit: {git(code, 'rev-parse', 'main')}" in message, adopted


def test_first_baseline_commits_exactly_the_validated_tree(
    tmp_path: Path, bound_validation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 7: the commit records exactly the staged tree validation passed."""

    seen: list[str] = []
    real_refusal = baseline.memory_commit_refusal

    def _spy(**kwargs: Any) -> Any:
        seen.append(kwargs["candidate_tree"])
        return real_refusal(**kwargs)

    monkeypatch.setattr(baseline, "memory_commit_refusal", _spy)
    config, _code, memory = _adoptable_converted_world(tmp_path, stale=True)
    adopted = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert adopted["state"] == "adopted", adopted
    assert seen, "the staged tree was never validated"
    assert seen[-1] == git(memory, "rev-parse", "HEAD^{tree}"), (seen, adopted)


def test_dry_run_and_refusal_change_nothing(tmp_path: Path, bound_validation: Any) -> None:
    """Rule 7: a dry run and a refusal leave the index, the refs and every file."""

    config, _code, memory = _adoptable_converted_world(tmp_path, stale=True)
    before = _fingerprint(memory)
    preview = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
        dry_run=True,
    )
    assert preview["state"] == "would-adopt", preview
    assert _fingerprint(memory) == before, "a dry run changed the repository"

    config, _code, memory = _adoptable_converted_world(tmp_path / "bad", violation=True)
    before = _fingerprint(memory)
    refused = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert refused["state"] == "blocked-invalid", refused
    assert _fingerprint(memory) == before, "a refused adoption changed the repository"


def test_adoption_refuses_where_the_validator_is_not_bound(tmp_path: Path) -> None:
    """Rule 7: without a bound validator the adoption refuses; it never commits."""

    reset_worktree_services()
    config, _code, memory = _adoptable_converted_world(tmp_path)
    before = _fingerprint(memory)
    refused = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
        dry_run=True,
    )
    assert refused["ok"] is False and refused["state"] == "blocked-invalid", refused
    assert "not bound" in str(refused["message"]), refused
    assert _fingerprint(memory) == before


def test_baseline_command_line_validates(
    tmp_path: Path, bound_validation: Any, capsys: Any
) -> None:
    """Rule 7: the module command line validates like the tool."""

    config, code, _memory = _adoptable_converted_world(tmp_path, stale=True)
    coordination = config.coordination_root.as_posix()
    common = [
        "--code-repository-name",
        REPO_ID,
        "--workspace-root",
        str(code.parent),
        "--code-repository-root",
        str(code),
        "--coordination-root",
        coordination,
        "--topology",
        "external",
    ]
    assert baseline.main(["status", *common]) == 0
    assert baseline.main(["adopt", *common, "--accept-drift", "--dry-run"]) == 0
    assert "would-adopt" in capsys.readouterr().out
