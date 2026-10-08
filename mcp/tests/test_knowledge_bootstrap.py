"""The taskless knowledge bootstrap: real authority, and knowledge written only as files.

``knowledge-ingest`` needs a leaf enclosure contract, and a repository whose foundation knowledge is
being written has no leaf. ``knowledge-bootstrap`` resolves the second **real** admission instead:
the repository entry of the MCP settings document and the memory layer the ordinary read route
resolves. Every case below is one user operation on a real coordination world -- a real code
checkout, a real external memory repository, a real MCP settings document -- driven through the
shipped command line.

The canonical knowledge database is retired (MIK-R26), so the bootstrap has one writer, the curator
file writer, and it writes a converted memory root as a wave. What these cases measure:

* **authority is the settings document**: a repository it does not declare is refused by name;
* **an unconverted memory root is refused as legacy format**, naming the crossing sync and the
  conversion command, and nothing is written -- no staging candidate, no dataset, no file;
* **the database surface is gone**: the command takes no destination, ``--status`` or
  ``--discard-staging`` argument, and a caller that still names one is refused by the parser;
* **a converted root is written as the wave ``--wave`` names**, through the real admission;
* **the memory initializer names the knowledge location** and reports that nothing is recorded there.

(The file writer's own behaviour -- records, entries, rows, validation -- is measured in
``test_knowledge_writer.py``; this module owns the admission and the route.)
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_bootstrap_admission import (
    AdmittedKnowledgeBootstrap,
    admit_bootstrap_context,
)
from agents_remember.application.memory_tools import memory_init_tool
from agents_remember.cli.__main__ import main
from agents_remember.cli.knowledge_bootstrap import add_arguments
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.knowledge_files.canonical import canonical_text

pytestmark = pytest.mark.evidence_unit

REPO_ID = "bootstrap-repo"
AUTHORIZATION = "authorization:test-bootstrap"
CODE_FILE = "pkg/module.py"
CODE_SYMBOL = "resolve_budget"
MEMORY_CARD = "onboarding/pkg/module.py.md"

_CODE_TEXT = (
    '"""A governed module."""\n\n\ndef resolve_budget(attempt: int) -> int:\n    return attempt\n'
)


@dataclass(frozen=True)
class World:
    """One real coordination world: a code checkout, an external memory repo, and the settings."""

    root: Path
    code_root: Path
    coordination_root: Path
    memory_root: Path
    settings_path: Path

    def admitted(self) -> AdmittedKnowledgeBootstrap:
        """The bootstrap context this world admits, or the fixture defect that stopped it."""

        admitted = admit_bootstrap_context(load_config(self.settings_path), REPO_ID)
        assert isinstance(admitted, AdmittedKnowledgeBootstrap), admitted
        return admitted


def _git(root: Path, args: Sequence[str]) -> str:
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


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _commit(root: Path, message: str) -> str:
    _git(root, ["config", "user.email", "fixture@example.invalid"])
    _git(root, ["config", "user.name", "bootstrap fixture"])
    _git(root, ["add", "-A"])
    _git(root, ["commit", "-q", "-m", message])
    return _git(root, ["rev-parse", "HEAD"])


def world(root: Path, *, converted: bool = False) -> World:
    """A code repository at ``workspace_root / repo_id``, its external memory repo, and settings.

    The layout is the one the resolver and the settings reader already agree on: the code checkout
    where the workspace search finds it, the memory repository at
    ``<coordination_root>/memory-repos/ar-<repo_id>``, and an MCP settings document outside the
    coordination root declaring both. No leaf, no worktree group and no contract exists anywhere in
    it. ``converted`` adds the layout marker and the card's sidecar, which is what a converted
    memory tree holds.
    """

    coordination_root = root / "coordination"
    code_root = root / REPO_ID
    memory_root = coordination_root / "memory-repos" / f"ar-{REPO_ID}"
    _write(code_root, {CODE_FILE: _CODE_TEXT})
    memory = {MEMORY_CARD: f"# {CODE_FILE}\n", "onboarding/overview.md": "# root\n"}
    if converted:
        memory["knowledge/layout.json"] = canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        )
        memory[MEMORY_CARD.removesuffix(".md") + ".json"] = canonical_text(
            {"schema": "ar-onboarding-file/v1", "path": CODE_FILE, "references": {}, "realizes": []}
        )
    _write(memory_root, memory)
    _git(code_root, ["init", "-q", "--initial-branch=main"])
    _git(memory_root, ["init", "-q", "--initial-branch=main"])
    _commit(code_root, "the code tree")
    _commit(memory_root, "the memory tree")
    settings_path = root / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coordination_root.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO_ID: {}},
            }
        ),
        encoding="utf-8",
    )
    return World(
        root=root,
        code_root=code_root,
        coordination_root=coordination_root,
        memory_root=memory_root,
        settings_path=settings_path,
    )


def entry(entry_id: str) -> dict[str, Any]:
    """One hand-off entry in the file writer's shape: an invariant realized at the code symbol."""

    return {
        "id": entry_id,
        "statement": f"The obligation {entry_id} records: an attempt resolves to its budget.",
        "kind": "clause",
        "target": [
            {
                "path": CODE_FILE,
                "locator": {"kind": "symbol", "value": CODE_SYMBOL},
                "rationale": f"{CODE_SYMBOL} is where this obligation is carried.",
            }
        ],
        "found_at": [],
        "disposition": "satisfied",
        "disposition_source": None,
        "evidence": None,
        "authority": {"task_document": "bootstrap_case"},
        "resolution": None,
        "validated_at": None,
        "record_action": None,
        "supersedes": None,
        "scope": {"applicability": "Every attempt.", "conditions": [], "exclusions": []},
        "admission": {
            "criteria": ["prevents_costly_mistake"],
            "justification": "A budget read anywhere else lets an attempt run without one.",
        },
    }


def hand_off(root: Path, name: str, document: object) -> Path:
    path = root / f"{name}.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def argv(world_one: World, listed: Path, *extra: str, repo: str = REPO_ID) -> list[str]:
    return [
        "knowledge-bootstrap",
        "--repo",
        repo,
        "--config",
        str(world_one.settings_path),
        "--list",
        str(listed),
        "--authorization-ref",
        AUTHORIZATION,
        *extra,
    ]


def tree(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` outside ``.git``, by path."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def test_a_repository_the_settings_do_not_declare_is_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Authority is the settings document, not the caller: an undeclared repository has no context."""

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", {"entries": [entry("B-one")]})

    code = main(argv(one, listed, "--wave", "wave-1", "--commit", "--json", repo="ghost-repo"))
    report = json.loads(capsys.readouterr().out)

    assert code == 2, report
    assert report["code"] == "repository_not_allowed", report
    assert REPO_ID in report["detail"], report


def test_an_unconverted_memory_root_is_refused_as_legacy_format_and_nothing_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The database bootstrap is retired: an unconverted root has no writer, and the refusal says
    how the root converts.

    Catches a route that still stages a candidate or publishes a dataset for unconverted memory.
    """

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", {"entries": [entry("B-one")]})
    before = tree(one.coordination_root)

    code = main(argv(one, listed, "--wave", "wave-1", "--commit"))
    printed = capsys.readouterr().out

    assert code == 2, printed
    assert f"knowledge-bootstrap refuses the memory root {one.memory_root.as_posix()}" in printed
    assert "legacy format" in printed and "MIK-R26" in printed
    assert "worktree_sync" in printed and "agents-remember knowledge-convert" in printed
    assert "holds no converted memory yet" in printed
    assert tree(one.coordination_root) == before
    assert not list(one.root.rglob("*.sqlite"))


def test_the_command_takes_no_database_argument(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No argument names a dataset, a staging candidate or a destination, and the parser refuses a
    caller that still passes one rather than ignoring it."""

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = {action.dest for action in parser._actions}
    assert options == {
        "help",
        "repo",
        "hand_off_list",
        "authorization_ref",
        "commit",
        "config",
        "wave",
        "as_json",
    }

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", {"entries": [entry("B-one")]})
    for retired in ("--status", "--discard-staging"):
        with pytest.raises(SystemExit) as exited:
            main(argv(one, listed, retired))
        assert exited.value.code == 2
        assert retired in capsys.readouterr().err


def test_a_converted_memory_root_is_written_as_the_named_wave(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real admission reaches the file writer: a planning run writes nothing, and the commit
    word writes the record, the sidecar entry and the wave's history file into the memory root."""

    one = world(tmp_path / "world", converted=True)
    row = {
        "subject": "handoff:B-one",
        "disposition": "extended",
        "reason": "The module gained its recorded obligation.",
        "covers": [{"handoff": "B-one"}],
    }
    listed = hand_off(one.root, "first", {"entries": [entry("B-one")], "history": [row]})
    before = tree(one.memory_root)

    assert main(argv(one, listed, "--json")) == 2  # a wave must be named
    assert "--wave names it" in capsys.readouterr().out
    planned_code = main(argv(one, listed, "--wave", "wave-1", "--json"))
    planned = json.loads(capsys.readouterr().out)
    assert (planned_code, planned["state"]) == (0, "planned"), planned
    assert tree(one.memory_root) == before

    code = main(argv(one, listed, "--wave", "wave-1", "--commit", "--json"))
    report = json.loads(capsys.readouterr().out)

    assert (code, report["state"]) == (0, "written"), report
    assert report["authorization"] == AUTHORIZATION
    after = tree(one.memory_root)
    (record,) = [path for path in after if path.startswith("knowledge/invariants/")]
    origin = json.loads(after[record])["origin"]
    assert origin["wave"] == "wave-1" and origin["task"] == f"knowledge-bootstrap:{REPO_ID}"
    history = json.loads(after["knowledge/history/wave-1.json"])
    assert history["wave"] == "wave-1" and len(history["rows"]) == 1
    sidecar = json.loads(after[MEMORY_CARD.removesuffix(".md") + ".json"])
    assert [one["locator"] for one in (item["anchor"] for item in sidecar["realizes"])] == [
        {"kind": "symbol", "name": CODE_SYMBOL}
    ]
    assert not [path for path in after if path.endswith(".sqlite")]


def test_memory_init_reports_empty_recorded_and_partial_text_knowledge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The public reporter observes content and completeness, independently of index existence."""
    one = world(tmp_path / "world", converted=True)
    config = load_config(one.settings_path)

    def reported():
        return memory_init_tool(config, repo_id=REPO_ID, dry_run=True, initial_branch="main")[
            "knowledge"
        ]

    empty = reported()
    assert empty["state"] == "not-recorded", empty
    assert (
        Path(empty["datasetPath"]).parent == one.coordination_root / "runtime" / "knowledge-index"
    )
    assert "knowledge-bootstrap" in empty["nextAction"]
    row = {
        "subject": "handoff:B-one",
        "disposition": "extended",
        "reason": "The module gained its recorded obligation.",
        "covers": [{"handoff": "B-one"}],
    }
    listed = hand_off(one.root, "first", {"entries": [entry("B-one")], "history": [row]})
    assert main(argv(one, listed, "--wave", "wave-1", "--commit", "--json")) == 0
    capsys.readouterr()
    complete = reported()
    assert complete["state"] == "recorded", complete
    (record,) = (one.memory_root / "knowledge/invariants").glob("*.json")
    record.write_text("{broken knowledge record")
    partial = reported()
    assert partial["state"] == "unusable", partial
    assert "partial" in partial["detail"] and record.name in partial["detail"]
    legacy = world(tmp_path / "legacy")
    refused = memory_init_tool(
        load_config(legacy.settings_path), repo_id=REPO_ID, dry_run=True, initial_branch="main"
    )["knowledge"]
    assert refused["code"] == "legacy-format", refused
    assert "worktree_sync" in refused["nextAction"] and "knowledge-convert" in refused["nextAction"]


def test_the_bootstrap_skill_describes_the_states_memory_init_reports(tmp_path: Path) -> None:
    """L26-R1-F05: the skill's paragraph about ``memory_init`` names the states the reporter
    really returns, in the canonical file and in every generated copy.

    Catches the earlier text that said a converted tree always reports ``not-recorded`` and
    measures no record population, which contradicted the reporter's ``recorded`` and ``unusable``.
    """

    repository = Path(__file__).resolve().parents[2]
    canonical = (repository / "skills/c-14-knowledge-bootstrap/SKILL.md").read_text("utf-8")
    start = canonical.index("For an admitted converted memory tree")
    paragraph = " ".join(canonical[start : canonical.index("###", start)].split())
    reported = memory_init_tool(
        load_config(world(tmp_path / "legacy").settings_path),
        repo_id=REPO_ID,
        dry_run=True,
        initial_branch="main",
    )["knowledge"]
    for word in ("not-recorded", "recorded", "unusable", "context-not-admitted", reported["code"]):
        assert word in paragraph, word
    assert "measures no record population" not in paragraph
    assert "complete index contains authored records" in paragraph.replace("the ", "")
    roots = (
        "mcp/src/agents_remember/package_data/runtime/skills",
        ".claude/skills",
        ".codex/skills",
        ".cursor/skills",
        ".github-vscode/skills",
        ".hermes/skills",
        ".openclaw/workspace/skills",
        ".pi/skills",
        ".agents/skills",
    )
    copies = [repository / root / "c-14-knowledge-bootstrap/SKILL.md" for root in roots]
    assert {path.read_text("utf-8") for path in copies} == {canonical}
