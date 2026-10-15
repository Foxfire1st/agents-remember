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

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from knowledge_conversion_test_support import code_repository, memory_repository

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.context_packet import (
    ContextPacketRequest,
    _drift_packet,
    build_context_packet,
)
from agents_remember.application.memory_scope import resolve_memory_scope
from agents_remember.application.memory_tools import (
    MemoryBranches,
    drift_check_tool,
    memory_baseline_adopt_tool,
    memory_baseline_status_tool,
    memory_init_tool,
)
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.memory import baseline
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import convert_memory
from agents_remember.memory.conversion.inputs import memory_from_directory, write_changed
from agents_remember.memory_quality.converted_check import CONVERTED_BASE_RULE
from agents_remember.memory_quality.integrity.onboarding_drift_check import drift
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    run_drift_summary,
)
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.memory_quality.knowledge_validator.trees import (
    knowledge_tree_from_directory,
)
from agents_remember.memory_quality.reference_state import git_blob_id
from agents_remember.models.knowledge_files.anchor_content import content_identity
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from test_worktree_support import git, init_repo

REPO_ID = "repo"
LAYOUT_MARKER = '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"\n}\n'


def _commit(repo: Path, message: str = "init") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def _code_tree(root: Path) -> Path:
    """A git code checkout with two tracked source files."""

    code = root / "code"
    init_repo(code, "main")
    (code / "pkg").mkdir(parents=True, exist_ok=True)
    (code / "pkg" / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
    (code / "pkg" / "beta.py").write_text("print('beta')\n", encoding="utf-8")
    _commit(code)
    return code


def _converted_memory(root: Path, name: str = "mem") -> Path:
    """A git memory checkout holding the converted layout marker."""

    memory = root / name
    init_repo(memory, "main")
    (memory / "knowledge").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "knowledge" / "layout.json").write_text(LAYOUT_MARKER, encoding="utf-8")
    return memory


def _anchor(*, blob: str, content: str) -> dict[str, Any]:
    return {
        "kind": "code",
        "anchor": {"blob": blob, "locator": {"kind": "file"}, "content": content},
    }


def _current_anchor(code: Path, rel: str) -> dict[str, Any]:
    data = (code / rel).read_bytes()
    return _anchor(blob=git_blob_id(data), content=content_identity(data))


def _stale_anchor() -> dict[str, Any]:
    return _anchor(blob="0" * 40, content="sha256:" + "0" * 64)


def _write_card(memory: Path, rel: str, targets: list[dict[str, Any]]) -> Path:
    """A canonical file sidecar at `onboarding/<rel>.json` plus its marked card."""

    document = {
        "path": rel,
        "schema": "ar-onboarding-file/v1",
        "realizes": [],
        "references": {"1": {"targets": targets}},
    }
    sidecar = memory / "onboarding" / f"{rel}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(canonical_text(document), encoding="utf-8")
    card = memory / "onboarding" / f"{rel}.md"
    card.write_text(f"# {rel}\n\nSee [1].\n", encoding="utf-8")
    return sidecar


def _context(memory: Path, root: Path) -> SimpleNamespace:
    coordination = root / "coordination"
    temp = root / "temp"
    coordination.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        onboarding_root=memory / "onboarding",
        memory_root=memory,
        coordination_root=coordination,
        temp_root=temp,
    )


def _summarize(*args: Any, **kwargs: Any) -> Any:
    """`run_drift_summary` with its packet as a plain mapping for assertions."""

    return run_drift_summary(*args, **kwargs)


@pytest.fixture
def bound_validation() -> Any:
    """Bind real worktree services so the first baseline validates before it commits."""

    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=mock.Mock(),
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
        )
    )
    yield
    reset_worktree_services()


def test_converted_trees_take_the_converted_branch_and_skip_legacy_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 1: the converted branch is taken and legacy discovery is not called."""

    code = _code_tree(tmp_path)
    memory = _converted_memory(tmp_path)
    _write_card(memory, "pkg/alpha.py", [_current_anchor(code, "pkg/alpha.py")])
    _write_card(memory, "pkg/beta.py", [_stale_anchor()])
    _commit(memory)

    def _unread(_root: Path) -> list[Path]:
        raise AssertionError("legacy discovery must not run on a converted tree")

    monkeypatch.setattr(drift, "discover_onboarding_files", _unread)
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


def _world(tmp_path: Path) -> tuple[Path, Path]:
    """A code repository, a coordination root, and a settings file outside both."""

    root = tmp_path / "world"
    code = root / REPO_ID
    init_repo(code, "main")
    (code / "pkg").mkdir(parents=True, exist_ok=True)
    (code / "pkg" / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
    _commit(code)
    (root / "coordination").mkdir(parents=True, exist_ok=True)
    path = root / "settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coordination").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO_ID: {}},
            }
        ),
        encoding="utf-8",
    )
    return code, path


def _adoptable_converted_world(
    tmp_path: Path, *, stale: bool = False, violation: bool = False
) -> tuple[Any, Path, Path]:
    code, path = _world(tmp_path)
    memory_init_tool(load_config(path), repo_id=REPO_ID, initial_branch="main")
    memory = load_config(path).repositories[REPO_ID].memory_root
    assert memory is not None
    if stale or violation:
        target = "pkg/gone.py" if violation else "pkg/alpha.py"
        anchor = _anchor(blob="0" * 40, content="sha256:" + "2" * 64)
        document = {
            "path": target,
            "schema": "ar-onboarding-file/v1",
            "realizes": [],
            "references": {"1": {"targets": [anchor]}},
        }
        sidecar = memory / "onboarding" / f"{target}.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(canonical_text(document), encoding="utf-8")
        (memory / "onboarding" / f"{target}.md").write_text(
            "# card\n\nSee [1].\n", encoding="utf-8"
        )
    return load_config(path), code, memory


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


def _fingerprint(repo: Path) -> tuple[str, str, str, str]:
    """The index, refs, status and file bytes a no-write run must preserve."""

    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    index = subprocess.run(
        ["git", "write-tree"], cwd=repo, text=True, capture_output=True, check=False
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, text=True, capture_output=True, check=False
    )
    digest = hashlib.sha256()
    for path in sorted(repo.rglob("*")):
        if ".git" in path.parts or not path.is_file():
            continue
        digest.update(path.relative_to(repo).as_posix().encode())
        digest.update(path.read_bytes())
    return (
        head.stdout.strip() if head.returncode == 0 else "unborn",
        index.stdout.strip(),
        status.stdout,
        digest.hexdigest(),
    )


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


_HARNESS_SKILL_ROOTS = (
    ".agents/skills",
    ".claude/skills",
    ".codex/skills",
    ".cursor/skills",
    ".github-vscode/skills",
    ".hermes/skills",
    ".openclaw/workspace/skills",
    ".pi/skills",
)

#: The four statements every converted-drift surface carries (L44-C1-10):
#: what the check measures, where currentness is answered, the advisory
#: staleness duty with its repair route, and that nothing is repaired here.
_MEASUREMENT = ("reference anchors of onboarding cards", "validity of the tree")
_CURRENTNESS = ("knowledge_read", "knowledge_integrity_check")
_ADVISORY = ("never blocks by itself", "MIK-R30", "citation_fix", "curator")


def _normalized_text(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _skill_copies(skill: str) -> list[Path]:
    return (
        [REPO_ROOT / "skills" / skill / "SKILL.md"]
        + [REPO_ROOT / root / skill / "SKILL.md" for root in _HARNESS_SKILL_ROOTS]
        + [
            REPO_ROOT
            / "mcp"
            / "src"
            / "agents_remember"
            / "package_data"
            / "runtime"
            / "skills"
            / skill
            / "SKILL.md"
        ]
    )


def test_descriptions_say_what_the_check_is() -> None:
    """Rule 8 / L44-C1-10: every live surface states measurement, currentness, duty, repair.

    MIK-R44 as amended (D126) with MIK-R112 rule 5: the `drift_check` tool
    is removed while the check stays, so what the packet says of the tool
    holds for the check and the drift part of `context_packet`, and the
    rule-8 sentence for the description of the removed tool falls away.
    The phrase assertions below therefore cover only the surviving
    surfaces (context_packet's include_drift and the skills); the removed
    tool's registration has no live surface left to assert.
    """

    registration = (
        REPO_ROOT / "mcp" / "src" / "agents_remember" / "mcp" / "registration" / "memory.py"
    ).read_text(encoding="utf-8")
    assert "Task-start gate: classify how far onboarding has drifted" not in registration
    assert "def drift_check" not in registration, (
        "the removed tool must not come back as a registration"
    )

    core = (
        REPO_ROOT / "mcp" / "src" / "agents_remember" / "mcp" / "registration" / "core.py"
    ).read_text(encoding="utf-8")
    assert "include_drift" in core and "nothing is measured" in core
    assert "docs/tool-details.md" in core, (
        "the surviving description must name where the longer explanation is (R112 rule 7)"
    )
    for phrase in (*_MEASUREMENT, *_CURRENTNESS, *_ADVISORY):
        assert phrase in core, phrase

    copies = _skill_copies("c-02-memory-quality-control")
    assert len(copies) == 10, [str(path) for path in copies]
    for copy in copies:
        text = _normalized_text(copy)
        for phrase in (*_MEASUREMENT, *_CURRENTNESS, *_ADVISORY):
            assert phrase in text, (copy, phrase)
        # F12: converted drift doctrine requires the current row; a counted card edit alone
        # never answers.
        assert "a current `onboarding_trace` row about the file" in text, copy
        assert "a counted change of its card alone never answers" in text, copy

    acceptance_copies = _skill_copies("c-10-adopt-memory-baseline")
    assert len(acceptance_copies) == 10, [str(path) for path in acceptance_copies]
    for copy in acceptance_copies:
        text = _normalized_text(copy)
        for phrase in (*_MEASUREMENT, *_CURRENTNESS, *_ADVISORY):
            assert phrase in text, (copy, phrase)
        assert "accepts stale anchors only" in text, copy
        assert "blocked-invalid" in text, copy


_REAL_LEAF_CONTRACT = Path(
    "/home/firefox/projects/ar-coordination/tasks/agents-remember"
    "/260928_maintained-invariant-knowledge/enclosures/260928-mik-l44/series-contract.md"
)


def _live_code_base_commit(text: str) -> str:
    """The live contract's code-cell base commit, read live.

    The helper copies the real enclosure contract, so its ``base_commit``
    literal must come from that file as it stands — pinning a base hash here
    would stale on the next sync. Only the ``code:`` cell's line qualifies;
    the memory cell's base is untouched by this helper's replacements.
    """

    code_cell = re.search(r"^code:\n((?:  .*\n)+)", text, re.MULTILINE)
    assert code_cell is not None, "live leaf contract has no code cell"
    base = re.search(r"^  base_commit: ([0-9a-f]{40})$", code_cell.group(1), re.MULTILINE)
    assert base is not None, "live leaf contract code cell has no base commit"
    return base.group(1)


def _leaf_contract(coord: Path, *, code: Path, memory: Path, code_commit: str) -> Path:
    """A loadable leaf enclosure contract pointing at fixture checkouts (L44-C1-05).

    A copy of the real L44 enclosure contract with every root remapped under
    ``coord`` and the code/memory cells pointed at the fixtures, so the leaf
    scope machinery (scope resolution, leaf base, cache authority) runs
    hermetically inside tmp.
    """

    side = coord.parent
    task = side / "task"
    group = side / "group"
    for path in (coord, task, group):
        path.mkdir(parents=True, exist_ok=True)
    (task / "task.md").write_text("# fixture task\n", encoding="utf-8")
    contract = coord / "leaf-contract.md"
    text = _REAL_LEAF_CONTRACT.read_text(encoding="utf-8")
    replacements = {
        "repo_name: agents-remember": f"repo_name: {REPO_ID}",
        "root: /home/firefox/projects/ar-coordination": f"root: {coord.as_posix()}",
        "task_root: /home/firefox/projects/ar-coordination/tasks/agents-remember"
        "/260928_maintained-invariant-knowledge": f"task_root: {task.as_posix()}",
        "series_contract_path: /home/firefox/projects/ar-coordination/tasks/agents-remember"
        "/260928_maintained-invariant-knowledge/enclosures/260928-mik-l44/series-contract.md": (
            f"series_contract_path: {contract.as_posix()}"
        ),
        "task_artifact: /home/firefox/projects/ar-coordination/tasks/agents-remember"
        "/260928_maintained-invariant-knowledge/task.md": (
            f"task_artifact: {task.as_posix()}/task.md"
        ),
        "worktree_group: /home/firefox/projects/ar-coordination/worktrees/agents-remember"
        "/44_truthful-converted-onboarding-drift-97edbca63c-ar": (
            f"worktree_group: {group.as_posix()}"
        ),
        "repo_path: /home/firefox/projects/agents-remember": (f"repo_path: {code.as_posix()}"),
        f"base_commit: {_live_code_base_commit(text)}": (f"base_commit: {code_commit}"),
        "worktree: /home/firefox/projects/ar-coordination/worktrees/agents-remember"
        "/44_truthful-converted-onboarding-drift-97edbca63c-ar"
        "/44_truthful-converted-onboarding-drift-97edbca63c": (f"worktree: {code.as_posix()}"),
        "worktree: /home/firefox/projects/ar-coordination/worktrees/agents-remember"
        "/44_truthful-converted-onboarding-drift-97edbca63c-ar"
        "/memory-44_truthful-converted-onboarding-drift-97edbca63c": (
            f"worktree: {memory.as_posix()}"
        ),
        "ledger: /home/firefox/projects/ar-coordination/worktrees/agents-remember"
        "/44_truthful-converted-onboarding-drift-97edbca63c-ar"
        "/memory-44_truthful-converted-onboarding-drift-97edbca63c/memory.md": (
            f"ledger: {memory.as_posix()}/memory.md"
        ),
    }
    for old, new in sorted(replacements.items(), key=lambda item: -len(item[0])):
        assert old in text, old
        text = text.replace(old, new)
    contract.write_text(text, encoding="utf-8")
    return contract


def test_context_packet_leaf_route_binds_the_leaf_base(tmp_path: Path) -> None:
    """L44-C1-05: the context leaf route compares against the leaf base, not HEAD."""

    code = _code_tree(tmp_path)
    memory = _converted_memory(tmp_path, "candidate")
    _write_card(memory, "pkg/gone.py", [_stale_anchor()])
    _commit(memory)
    real = git(memory, "rev-parse", "HEAD")
    header, message = git(memory, "cat-file", "-p", real).split("\n\n", 1)
    rest = "\n".join(line for line in header.split("\n")[1:] if not line.startswith("tree "))
    treeless = tmp_path / "treeless.commit"
    treeless.write_text(f"tree {'f' * 40}\n{rest}\n\n{message}\n", encoding="utf-8")
    broken = git(memory, "hash-object", "-t", "commit", "-w", str(treeless))
    (memory / ".git" / "refs" / "heads" / "main").write_text(f"{broken}\n", encoding="utf-8")
    _code, settings = _world(tmp_path / "cfg")
    config = load_config(settings)
    memory_init_tool(config, repo_id=REPO_ID, initial_branch="main")
    contract = _leaf_contract(
        config.coordination_root,
        code=code,
        memory=memory,
        code_commit=git(code, "rev-parse", "HEAD"),
    )

    tooled = drift_check_tool(config, repo_id=REPO_ID, contract_path=contract.as_posix())
    scope = resolve_memory_scope(config, repo_id=REPO_ID, contract_path=contract.as_posix())
    fold = cast(
        "dict[str, Any]",
        _drift_packet(
            ContextPacketRequest(repo_id=REPO_ID, include_drift=True),
            scope.context,
            cast(Any, SimpleNamespace(path=scope.code_root)),
        ),
    )
    for packet in (tooled, fold):
        assert packet["violationCount"] == 1, packet
        (finding,) = packet["violationSample"]
        assert finding["rule"] == CONVERTED_BASE_RULE, finding
        assert packet["states"] == {"current": 0, "stale": 1}, packet
        assert packet["actionableCount"] == 1, packet
    assert fold["violationSample"] == tooled["violationSample"], (fold, tooled)

    plain = _summarize(code_repository_root=code, context=_context(memory, tmp_path))
    assert plain["violationCount"] == 1, plain
    assert plain["violationSample"][0]["rule"] == "R22.6-anchor-path", plain


def test_context_packet_agrees_with_the_summary_on_carried_anchors(tmp_path: Path) -> None:
    """L44-C1-05 as amended (MIK-R53 rule 11, Supersedes names MIK-R44 v2 for this kind):
    leaf route and summary agree a carried deleted anchor is stale, and both refuse it as
    R22.6-carried-stale."""

    scratch = tmp_path / "chain"
    scratch.mkdir()
    code = code_repository(scratch / "code")
    memory_repository(scratch / "memory", code)
    memory = scratch / "memory"
    outcome = convert_memory(
        memory_from_directory(memory, label="memory"),
        CodeObjects(code.root),
        paired_commit=code.first,
    )
    assert outcome.state == "converted" and outcome.changed, outcome.report
    write_changed(memory, outcome.changed)
    (code.root / "docs" / "guide.md").unlink()

    _code, settings = _world(tmp_path / "cfg")
    config = load_config(settings)
    memory_init_tool(config, repo_id=REPO_ID, initial_branch="main")
    contract = _leaf_contract(
        config.coordination_root, code=code.root, memory=memory, code_commit=code.first
    )
    tooled = drift_check_tool(config, repo_id=REPO_ID, contract_path=contract.as_posix())
    scope = resolve_memory_scope(config, repo_id=REPO_ID, contract_path=contract.as_posix())
    fold = cast(
        "dict[str, Any]",
        _drift_packet(
            ContextPacketRequest(repo_id=REPO_ID, include_drift=True),
            scope.context,
            cast(Any, SimpleNamespace(path=scope.code_root)),
        ),
    )
    tooled_rules = {entry["rule"] for entry in tooled["violationSample"]}
    fold_rules = {entry["rule"] for entry in fold["violationSample"]}
    assert tooled_rules == {"R22.6-carried-stale"}, tooled
    assert fold_rules == {"R22.6-carried-stale"}, fold
    assert tooled["violationSample"] == fold["violationSample"], (tooled, fold)
    # Exact fixture count: two stale reference targets (references.1/.8 at the deleted
    # docs/guide.md) plus the realizes anchor at the gone src/pkg/deleted.py.
    assert tooled["violationCount"] == fold["violationCount"] == 3, (tooled, fold)
    assert tooled["actionableCount"] >= 1, tooled
    assert fold["states"] == tooled["states"], (fold, tooled)
    assert fold["actionableCount"] == tooled["actionableCount"], (fold, tooled)


def test_baseline_drift_block_carries_the_full_checked_envelope(tmp_path: Path) -> None:
    """L44-C1-06: baseline status drift carries status plus every rule-2 field."""

    config, _code, _memory = _adoptable_converted_world(tmp_path, stale=True)
    checked = drift_check_tool(config, repo_id=REPO_ID, detail_limit=10)
    assert checked["ok"] is True, checked
    drift = memory_baseline_status_tool(config, repo_id=REPO_ID)["drift"]
    assert drift["status"] == "checked", drift
    for key in (
        "basis",
        "memoryRoot",
        "codeRoot",
        "sidecarCount",
        "count",
        "states",
        "actionableCount",
        "actionableSample",
        "violationCount",
        "violationSample",
        "reportOnlyCount",
        "unreadableSidecars",
    ):
        assert drift[key] == checked[key], key


def test_file_onboarding_root_is_an_error_through_the_tool(tmp_path: Path) -> None:
    """L44-C1-07: a converted root whose onboarding path is a file errors, not zero."""

    config, _code, memory = _adoptable_converted_world(tmp_path)
    shutil.rmtree(memory / "onboarding")
    (memory / "onboarding").write_text("not a directory", encoding="utf-8")
    result = drift_check_tool(config, repo_id=REPO_ID)
    assert result["ok"] is False, result
    assert result["status"] == "error", result
    assert "count" not in result and "sidecarCount" not in result, result


def test_empty_and_malformed_sidecars_count_truthfully(tmp_path: Path) -> None:
    """L44-C1-08: empty maps count zero; malformed are unreadable, not counted."""

    code = _code_tree(tmp_path)
    memory = _converted_memory(tmp_path)
    _write_card(memory, "pkg/alpha.py", [_current_anchor(code, "pkg/alpha.py")])
    empty = {
        "path": "pkg/empty.py",
        "schema": "ar-onboarding-file/v1",
        "realizes": [],
        "references": {},
    }
    (memory / "onboarding" / "pkg" / "empty.py.json").write_text(
        canonical_text(empty), encoding="utf-8"
    )
    (memory / "onboarding" / "pkg" / "empty.py.md").write_text(
        "# empty\n\nNo references.\n", encoding="utf-8"
    )
    broken = {
        "path": "pkg/broken.py",
        "schema": "ar-onboarding-file/v1",
        "realizes": [],
        "references": {"1": {"targets": [{"kind": "code"}]}},
    }
    (memory / "onboarding" / "pkg" / "broken.py.json").write_text(
        canonical_text(broken), encoding="utf-8"
    )
    (memory / "onboarding" / "pkg" / "broken.py.md").write_text(
        "# broken\n\nSee [1].\n", encoding="utf-8"
    )

    packet = _summarize(code_repository_root=code, context=_context(memory, tmp_path))
    assert packet["status"] == "checked", packet
    assert packet["sidecarCount"] == 1, packet
    assert packet["count"] == 1, packet
    assert packet["states"] == {"current": 1, "stale": 0}, packet
    assert packet["unreadableSidecars"] == ["onboarding/pkg/broken.py.json"], packet
    assert packet["violationCount"] == 2, packet
    assert [finding["rule"] for finding in packet["violationSample"]] == [
        "R22.1-shape",
        "R22.1-shape",
    ], packet
    assert {finding["path"] for finding in packet["violationSample"]} == {
        "onboarding/pkg/broken.py.json"
    }, packet


def test_unbuildable_base_withholds_new_carried_refusals(tmp_path: Path) -> None:
    """L44-C1-09: a failed base records one refusal; carried anchors stay stale."""

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

    def _failed_base(_root: Path, _candidate: Any) -> tuple[Any, str | None]:
        return None, "the base cache is unreachable"

    refused = _summarize(
        code_repository_root=code, context=_context(memory, tmp_path), base=_failed_base
    )
    assert refused["violationCount"] == 1, refused
    (finding,) = refused["violationSample"]
    assert finding["rule"] == CONVERTED_BASE_RULE, finding
    assert refused["states"] == {"current": 0, "stale": 1}, refused
    assert refused["actionableCount"] == 1, refused


def _stage_list(repo: Path) -> str:
    return git(repo, "ls-files", "--stage")


_EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def test_interval_failure_leaves_everything_untouched(
    tmp_path: Path, bound_validation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L44-C1-02/L44-C1-03: a failure stages a private index; entry state stays."""

    def _boom(**kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(baseline, "memory_commit_refusal", _boom)
    config, _code, memory = _adoptable_converted_world(tmp_path, stale=True)
    (memory / "external-staged.txt").write_text("external\n", encoding="utf-8")
    git(memory, "add", "--", "external-staged.txt")
    (memory / "intended.txt").write_text("intended\n", encoding="utf-8")
    git(memory, "add", "-N", "--", "intended.txt")
    (memory / "linked.txt").symlink_to("onboarding/pkg/alpha.py.md")
    before = _fingerprint(memory)
    before_stage = _stage_list(memory)
    assert "intended.txt" in before_stage
    assert _EMPTY_BLOB in before_stage
    assert git(memory, "ls-files", "--stage", "--", "linked.txt") == ""
    branches = MemoryBranches(source_branch="main", work_branch="main")
    for dry in (True, False):
        with pytest.raises(RuntimeError, match="boom"):
            memory_baseline_adopt_tool(
                config,
                repo_id=REPO_ID,
                accept_drift=True,
                branches=branches,
                dry_run=dry,
            )
        assert _fingerprint(memory) == before, f"a failure changed the repository (dry={dry})"
        assert _stage_list(memory) == before_stage, f"a failure changed the index (dry={dry})"
        assert _EMPTY_BLOB in _stage_list(memory), "the entry intent-to-add state is gone"
        assert git(memory, "ls-files", "--stage", "--", "linked.txt") == "", (
            "an owned symlink staging leaked into the worktree index"
        )
        assert (memory / "linked.txt").readlink().as_posix() == "onboarding/pkg/alpha.py.md"
        assert (memory / "external-staged.txt").read_text(encoding="utf-8") == "external\n"


def test_success_brings_real_index_to_adopted_state(tmp_path: Path, bound_validation: Any) -> None:
    """L44-C1-04: after validation passes the real index records the adopted tree."""

    config, _code, memory = _adoptable_converted_world(tmp_path, stale=True)
    adopted = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert adopted["ok"] is True and adopted["state"] == "adopted", adopted
    assert git(memory, "write-tree") == git(memory, "rev-parse", "HEAD^{tree}"), (
        "the worktree index does not record the adopted tree"
    )


def test_malicious_hook_cannot_change_the_recorded_tree(
    tmp_path: Path, bound_validation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L44-C1-04: a hook that rewrites and stages layout bytes is refused.

    R152 governs the adoption: the repository's hook runs as the check on the
    validated tree. The hook's bytes never reach a commit — the adoption is
    refused naming the paths, no commit is made, the branch stays where it
    was, and the worktree and the index hold what they held before.
    """

    seen: list[str] = []
    real_refusal = baseline.memory_commit_refusal

    def _spy(**kwargs: Any) -> Any:
        seen.append(kwargs["candidate_tree"])
        return real_refusal(**kwargs)

    monkeypatch.setattr(baseline, "memory_commit_refusal", _spy)
    config, _code, memory = _adoptable_converted_world(tmp_path, stale=True)
    layout = memory / "knowledge" / "layout.json"
    original = layout.read_bytes()
    before = _fingerprint(memory)
    before_stage = _stage_list(memory)

    def _rev(name: str) -> str | None:
        probe = subprocess.run(
            ["git", "-C", str(memory), "rev-parse", "--verify", name],
            capture_output=True,
            text=True,
            check=False,
        )
        return probe.stdout.strip() or None if probe.returncode == 0 else None

    # A first adoption: no commit and no branch exist yet.
    assert _rev("HEAD") is None
    hook = memory / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f'printf \'{{"invalid": true}}\' > "{layout.as_posix()}"\n'
        f'git -C "{memory.as_posix()}" add -A\n'
        f'touch "{memory.as_posix()}/.hook-ran"\n',
        encoding="utf-8",
    )
    os.chmod(hook, 0o755)
    with pytest.raises(RuntimeError, match=r"knowledge/layout\.json"):
        memory_baseline_adopt_tool(
            config,
            repo_id=REPO_ID,
            accept_drift=True,
            branches=MemoryBranches(source_branch="main", work_branch="main"),
        )
    assert seen, "the staged tree was never validated"
    assert _rev("HEAD") is None, "a commit was made"
    assert _rev("main") is None, "the branch moved"
    assert layout.read_bytes() == original, "the hook's bytes stayed in the worktree"
    assert _stage_list(memory) == before_stage, "the index changed"
    # The worktree holds what it held before, plus the hook's own untracked
    # dropping — the proof the hook ran as the check (no other line appears).
    after_status = subprocess.run(
        ["git", "-C", str(memory), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    assert set(after_status) - {"?? .hook-ran"} == set(before[2].splitlines()), after_status
    assert (memory / ".hook-ran").exists(), "the hook never ran as the check"


def test_passing_hook_adoption_records_the_validated_tree(
    tmp_path: Path, bound_validation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L44-C1-04: with a passing hook the adoption records exactly the validated tree."""

    seen: list[str] = []
    real_refusal = baseline.memory_commit_refusal

    def _spy(**kwargs: Any) -> Any:
        seen.append(kwargs["candidate_tree"])
        return real_refusal(**kwargs)

    monkeypatch.setattr(baseline, "memory_commit_refusal", _spy)
    config, _code, memory = _adoptable_converted_world(tmp_path, stale=True)
    layout = memory / "knowledge" / "layout.json"
    original = layout.read_bytes()
    hook = memory / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        f"printf 'x\\n' >> \"{memory.as_posix()}/.hook-count\"\n"
        f'touch "{memory.as_posix()}/.hook-ran"\n',
        encoding="utf-8",
    )
    os.chmod(hook, 0o755)
    adopted = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert adopted["ok"] is True and adopted["state"] == "adopted", adopted
    assert seen, "the staged tree was never validated"
    assert seen[-1] == git(memory, "rev-parse", "HEAD^{tree}"), (seen, adopted)
    committed_layout = git(memory, "rev-parse", "HEAD:knowledge/layout.json")
    assert committed_layout == git(memory, "rev-parse", f"{seen[-1]}:knowledge/layout.json"), (
        "the commit records more or less than the validated tree"
    )
    assert committed_layout == git(memory, "hash-object", layout.as_posix()), (
        "the committed layout differs from the worktree bytes validation passed"
    )
    assert layout.read_bytes() == original
    assert (memory / ".hook-ran").exists(), "the hook never ran as the check"
    assert (memory / ".hook-count").read_text(encoding="utf-8") == "x\n", (
        "the hook did not run exactly once"
    )


def test_unreadable_history_reports_unavailable_on_converted_trees(
    tmp_path: Path, bound_validation: Any
) -> None:
    """L44-C1-11: a converted tree with unreadable history reports unavailable."""

    config, _code, memory = _adoptable_converted_world(tmp_path, stale=True)
    (memory / "onboarding" / "extra.md").write_text("# extra\n", encoding="utf-8")
    git(memory, "add", "-A")
    git(memory, "commit", "-qm", "second")
    content = git(memory, "rev-parse", "HEAD")
    header, message = git(memory, "cat-file", "-p", content).split("\n\n", 1)
    lines = header.split("\n")
    tree = lines[0]
    remaining = "\n".join(line for line in lines[1:] if not line.startswith("parent "))
    broken_file = tmp_path / "missing-parent.commit"
    broken_file.write_text(
        f"{tree}\nparent {'f' * 40}\n{remaining}\n\n{message}\n", encoding="utf-8"
    )
    broken = git(memory, "hash-object", "-t", "commit", "-w", str(broken_file))
    git(memory, "update-ref", "refs/heads/main", broken, content)
    refs = git(memory, "show-ref")
    unavailable = memory_baseline_status_tool(config, repo_id=REPO_ID)
    assert unavailable["ok"] is False and unavailable["state"] == "unavailable", unavailable
    assert git(memory, "show-ref") == refs
