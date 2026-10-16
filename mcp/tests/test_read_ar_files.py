"""``read_ar_files`` names where a thing is and does not carry it (MIK-R109 rule 1).

For each requested path (as strings, up to twenty) the tool answers whether
the file exists in the caller's code checkout, the governing card as a
location in the caller's memory worktree (or that it has none), the card's
record counts by state on the code the caller sees, and — only for records
whose state asks the caller to act — the text ID, kind, state and card
heading of each. Overviews are listed once per call as locations, nearest
first. No source text, card text or overview text is returned; removed
parameters and object-form entries are refused with ``argument-removed``;
the same call on the same state gives the same answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
# The canonical retrieval carrier that documents this route: the one file a reader follows to
# discover it, and therefore the file a carrier-parity case has to measure.
CARRIER = Path(__file__).resolve().parents[2] / "skills/c-04-retrieval-strategy-router/SKILL.md"
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.published_intent import (
    PublishedIntentSelection,
    published_intent_block,
    read_published_intent,
    resolve_published_intent,
)
from agents_remember.application.read_files import read_ar_files_tool
from agents_remember.kernel.coordination_context.models import (
    CoordinationContext,
    CrossRepoSettings,
    StorageSettings,
)
from agents_remember.kernel.primitives.runtime_config import (
    load_config,
)
from agents_remember.mcp.registration import TOOL_REGISTRARS
from agents_remember.mcp.tools import read_ar_files_payload
from agents_remember.models.knowledge_files import (
    derived_realization_id,
    derived_record_id,
)
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from knowledge_index_test_support import write_review_tree
from knowledge_writer_test_support import CODE_FILE, build_world, canonical, commit_all
from mcp.server.fastmcp import FastMCP
from read_scope_test_support import (
    BATCH_PATH,
    build_read_scope_fixture,
)
from test_config import settings_payload
from tool_entry_point_world import EntryPointWorld

REPO = "agents-remember"

ARGUMENT_REMOVED = "read the file with your own tools; this tool names the cards"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _make_config(root: Path, code_root: Path):
    """A real ``McpRuntimeConfig`` whose coordination root is ``root`` and whose
    one repo points at ``code_root`` -- so ``observer_root`` and the reset marker
    resolve under ``root/logs/observer`` and the repo path confines correctly."""
    settings = settings_payload(root)
    settings["workspaceRoot"] = str(code_root.parent)
    settings["repositories"] = {REPO: {}}
    path = root / ".codex" / "mcp" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings), encoding="utf-8")
    return load_config(path)


def _build_context(
    code_root: Path,
    onboarding_root: Path,
    *,
    coordination_root: Path,
    storage_mode: str,
    repo_name: str = REPO,
) -> CoordinationContext:
    """A minimal context with the fields the application layer reads, isolating storage."""
    storage = StorageSettings(mode=storage_mode, default=storage_mode)
    return CoordinationContext(
        topology="external",
        code_repository_name=repo_name,
        code_repository_root=code_root,
        coordination_root=coordination_root,
        memory_root=onboarding_root.parent,
        onboarding_root=onboarding_root,
        settings_path=onboarding_root / "settings.md",
        path_settings_path=None,
        task_root=coordination_root,
        temp_root=coordination_root / "temp",
        docs_root=coordination_root / "docs",
        system_root=onboarding_root.parent / "system",
        sources_path=onboarding_root.parent / "system" / "sources.md",
        tools_path=onboarding_root.parent / "system" / "tools.md",
        storage=storage,
        path_rules=storage.path_rules,
        cross_repo=CrossRepoSettings(),
        memory_mode="external",
    )


def _write_sidecar(onboarding_root: Path, source_rel: str, body: str) -> None:
    path = onboarding_root / f"{source_rel}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_rel}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| path | `{source_rel}` |",
                "| doc_type | `file-level-onboarding` |",
                "| lastUpdated | 2026-06-01T00:00 |",
                "",
                body,
                "",
                "## Update History",
                "- 2026-06-01 seeded",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_overview(onboarding_root: Path, route: str, text: str) -> None:
    rel = "overview.md" if route == "" else f"{route}/overview.md"
    path = onboarding_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_route_index(onboarding_root: Path, route: str, *, covered: list[str]) -> None:
    rel = "overview.index.json" if route == "" else f"{route}/overview.index.json"
    scope = "**" if route == "" else f"{route}/**"
    path = onboarding_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "agents-remember-route-index",
                "route": route,
                "sourceScope": [scope],
                "coveredFiles": covered,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# request shape: strings only, one to twenty, no removed parameters
# --------------------------------------------------------------------------


class RequestShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.code = self._dir / "workspace" / REPO
        (self.code / "pkg").mkdir(parents=True)
        (self.code / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
        self.onb = self._dir / "memory" / "onboarding"
        self.onb.mkdir(parents=True)
        self.config = _make_config(self._dir, self.code)

    def tearDown(self) -> None:
        pass

    def _read(self, files, *, storage_mode: str = "disabled"):
        ctx = _build_context(
            self.code,
            self.onb,
            coordination_root=self.config.coordination_root,
            storage_mode=storage_mode,
        )
        return read_ar_files_tool(self.config, repo_id=REPO, files=files, _context=ctx)

    def test_object_entry_is_refused_with_argument_removed(self) -> None:
        result = self._read([{"path": "pkg/mod.py", "source": "full"}])
        self.assertEqual(result["state"], "refused")
        self.assertEqual(result["refusalCode"], "argument-removed")
        self.assertEqual(result["refusalDetail"], ARGUMENT_REMOVED)

    def test_range_entry_is_refused_with_argument_removed(self) -> None:
        result = self._read([{"path": "pkg/mod.py", "source": {"startLine": 1, "endLine": 2}}])
        self.assertEqual(result["state"], "refused")
        self.assertEqual(result["refusalCode"], "argument-removed")

    def test_onboarding_suppressed_entry_is_refused_with_argument_removed(self) -> None:
        result = self._read([{"path": "pkg/mod.py", "onboarding": False}])
        self.assertEqual(result["state"], "refused")
        self.assertEqual(result["refusalCode"], "argument-removed")

    def test_refresh_is_refused_with_argument_removed(self) -> None:
        payload = read_ar_files_payload(self.config, REPO, ["pkg/mod.py"], refresh=True)
        self.assertEqual(payload["state"], "refused")
        self.assertEqual(payload["refusalCode"], "argument-removed")
        self.assertEqual(payload["refusalDetail"], ARGUMENT_REMOVED)

    def test_empty_list_is_refused_before_anything_is_read(self) -> None:
        result = self._read([])
        self.assertEqual(result["state"], "refused")
        self.assertEqual(result["refusalCode"], "empty_paths")

    def test_twenty_paths_are_accepted_and_twenty_one_refused(self) -> None:
        twenty = [f"pkg/mod{i}.py" for i in range(20)]
        for rel in twenty:
            (self.code / rel).write_text("x = 1\n", encoding="utf-8")
        result = self._read(twenty)
        self.assertEqual(result["state"], "named")
        self.assertEqual(len(result["files"]), 20)
        refused = self._read([*twenty, "pkg/extra.py"])
        self.assertEqual(refused["state"], "refused")
        self.assertEqual(refused["refusalCode"], "too_many_paths")

    def test_the_same_call_twice_gives_the_same_answer(self) -> None:
        # No per-session state: nothing is kept about what was sent.
        first = self._read(["pkg/mod.py", "pkg/missing.py"])
        second = self._read(["pkg/mod.py", "pkg/missing.py"])
        self.assertEqual(first, second)

    def test_path_confinement_rejects_escape(self) -> None:
        # Confinement fails closed at the row boundary: the outside path
        # earns its row, and nothing outside is read.
        result = self._read(["pkg/mod.py", "../secret.txt"])
        self.assertEqual(result["state"], "named")
        self.assertTrue(result["files"][0]["exists"])
        row = result["files"][1]
        self.assertEqual(row["refusalCode"], "outside-repository")
        self.assertIn("refusalDetail", row)
        self.assertNotIn("card", row)

    def test_escape_first_and_last_keep_valid_rows(self) -> None:
        for files in (
            ["../secret.txt", "pkg/mod.py"],
            ["pkg/mod.py", "/abs/secret.txt"],
        ):
            with self.subTest(files=files):
                result = self._read(files)
                self.assertEqual(result["state"], "named")
                kinds = {row.get("refusalCode", "named") for row in result["files"]}
                self.assertEqual(kinds, {"named", "outside-repository"}, result)
                named = next(row for row in result["files"] if "refusalCode" not in row)
                self.assertTrue(named["exists"])

    def test_symlink_file_escape_rejected(self) -> None:
        outside = self._dir / "outside_secret.txt"
        outside.write_text("secret\n", encoding="utf-8")
        link = self.code / "leak.py"
        link.symlink_to(outside)
        result = self._read(["pkg/mod.py", "leak.py"])
        self.assertEqual(result["state"], "named")
        self.assertTrue(result["files"][0]["exists"])
        self.assertEqual(result["files"][1]["refusalCode"], "outside-repository")

    def test_symlink_dir_escape_rejected(self) -> None:
        outside_dir = self._dir / "outside_dir"
        outside_dir.mkdir()
        (outside_dir / "secret.py").write_text("secret = 1\n", encoding="utf-8")
        link = self.code / "linkdir"
        link.symlink_to(outside_dir, target_is_directory=True)
        result = self._read(["linkdir/secret.py"])
        self.assertEqual(result["state"], "named")
        (row,) = result["files"]
        self.assertEqual(row["refusalCode"], "outside-repository")


# --------------------------------------------------------------------------
# naming on a converted tree: locations, counts, no text
# --------------------------------------------------------------------------


class NamingTests(unittest.TestCase):
    """One path and twenty hold no source, card or overview text; locations open."""

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        world = build_world(self._dir / "converted")
        self.code = self._dir / "workspace" / REPO
        self.code.parent.mkdir()
        shutil.move(str(world.code), str(self.code))
        self.memory = world.memory
        self.config = _make_config(self._dir, self.code)

    def tearDown(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def _context(self, storage_mode: str = "repo-sidecar"):
        return _build_context(
            self.code,
            self.memory / "onboarding",
            coordination_root=self.config.coordination_root,
            storage_mode=storage_mode,
        )

    def _read(self, files):
        return read_ar_files_tool(
            self.config,
            repo_id=REPO,
            files=files,
            working_folder=self._dir,
            _context=self._context(),
        )

    def test_one_path_names_without_carrying(self) -> None:
        result = self._read([CODE_FILE])
        self.assertEqual(result["state"], "named")
        row = result["files"][0]
        self.assertTrue(row["exists"])
        self.assertTrue(row["card"].endswith(f"onboarding/{CODE_FILE}.md"))
        # The location opens as written from the caller's working folder.
        self.assertTrue((self._dir / row["card"]).is_file())
        self.assertEqual(
            set(row["recordCounts"]), {"stale", "unverifiable", "unrealized", "current"}
        )
        # No text of any kind travels: the recorded statement stays out.
        dumped = json.dumps(result)
        self.assertNotIn("a landing is one recorded pair", dumped)
        self.assertNotIn("Land both halves", dumped)

    def test_twenty_paths_hold_no_text_and_name_the_chain_once(self) -> None:
        files = [CODE_FILE, *[f"pkg/extra{i}.py" for i in range(19)]]
        for rel in files[1:]:
            (self.code / rel).write_text("x = 1\n", encoding="utf-8")
        result = self._read(files)
        self.assertEqual(len(result["files"]), 20)
        dumped = json.dumps(result)
        self.assertNotIn("a landing is one recorded pair", dumped)
        # Overview locations are named once for the call, not per file.
        self.assertEqual(len(result["overviews"]), len(set(result["overviews"])))
        for location in result["overviews"]:
            self.assertTrue((self._dir / location).is_file(), location)

    def test_a_path_that_is_not_in_the_repository_says_so(self) -> None:
        result = self._read([CODE_FILE, "pkg/ghost.py"])
        self.assertTrue(result["files"][0]["exists"])
        ghost = result["files"][1]
        self.assertFalse(ghost["exists"])
        self.assertNotIn("card", ghost)

    def test_a_path_with_no_card_has_none(self) -> None:
        (self.code / "pkg" / "nocard.py").write_text("x = 1\n", encoding="utf-8")
        result = self._read(["pkg/nocard.py"])
        row = result["files"][0]
        self.assertTrue(row["exists"])
        self.assertIsNone(row["card"])

    def test_memory_states_travel_with_the_answer(self) -> None:
        result = self._read([CODE_FILE])
        self.assertEqual(result["memoryState"], "converted")
        self.assertEqual(result["codeState"], "head")
        self.assertTrue(result["codeTree"])


# --------------------------------------------------------------------------
# a stale record is named singly, with its heading in the card
# --------------------------------------------------------------------------


class StaleNamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        world = build_world(self._dir / "converted")
        self.code = self._dir / "workspace" / REPO
        self.code.parent.mkdir()
        shutil.move(str(world.code), str(self.code))
        self.memory = world.memory
        # Converted grammar: sections citing numbered references, resolved
        # through the card's sidecar. The stale record's own anchor lives
        # under "Landing pair"; another file's reference lives under "Tests".
        card = self.memory / "onboarding" / f"{CODE_FILE}.md"
        sidecar_path = card.with_suffix(".json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        anchor = sidecar["realizes"][0]["anchor"]
        sidecar["references"] = {
            "1": {"targets": [{"kind": "code", "anchor": dict(anchor)}]},
            "2": {
                "targets": [
                    {
                        "kind": "test",
                        "anchor": {
                            "path": "tests/test_landing.py",
                            "locator": {"kind": "file"},
                            "blob": "0" * 40,
                            "content": "sha256:" + "0" * 64,
                        },
                    }
                ]
            },
        }
        sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        card.write_text(
            "\n".join(
                [
                    "# Human title unlike the path",
                    "",
                    "## Landing pair",
                    "",
                    "- landing holds [1]",
                    "",
                    "## Tests",
                    "",
                    "- tested elsewhere [2]",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.config = _make_config(self._dir, self.code)

    def tearDown(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def _read(self, files):
        ctx = _build_context(
            self.code,
            self.memory / "onboarding",
            coordination_root=self.config.coordination_root,
            storage_mode="repo-sidecar",
        )
        return read_ar_files_tool(
            self.config,
            repo_id=REPO,
            files=files,
            working_folder=self._dir,
            _context=ctx,
        )

    def _break_anchor(self) -> None:
        from knowledge_writer_test_support import git as _git  # noqa: PLC0415

        path = self.code / CODE_FILE
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "return (code, memory)", "return (memory, code)"
            ),
            encoding="utf-8",
        )
        _git(self.code, "add", "-A")
        _git(self.code, "commit", "-q", "-m", "break the recorded anchor")

    def test_a_broken_anchor_is_named_with_id_kind_state_and_heading(self) -> None:
        self._break_anchor()
        result = self._read([CODE_FILE])
        row = result["files"][0]
        self.assertEqual(row["recordCounts"]["stale"], 1)
        self.assertEqual(row["cardState"], "stale")
        (named,) = [one for one in row["actionNeeded"] if one["state"] == "stale"]
        self.assertTrue(named["id"].startswith("INV-"))
        self.assertEqual(named["kind"], "invariant")
        self.assertEqual(named["heading"], "Landing pair")

    def test_a_current_card_names_no_record(self) -> None:
        result = self._read([CODE_FILE])
        row = result["files"][0]
        self.assertEqual(row["actionNeeded"], [])
        self.assertEqual(row["cardState"], "current")


# --------------------------------------------------------------------------
# two records in one source file resolve to their own card sections
# --------------------------------------------------------------------------


class DistinctSectionHeadingTests(unittest.TestCase):
    """MIK-R109 round-1 F07: headings come from the converted grammar.

    Two stale records anchor the same source file at two recorded blobs;
    the card cites each blob under its own section through the sidecar's
    numbered references. The registered naming read must resolve each
    record to its own section -- not to the first path-matching section,
    and not to the card title (which unlike the source path is asserted
    distinct from both sections).
    """

    _TITLE = "Human title unlike the path"
    _FIRST = "First hold"
    _SECOND = "Second hold"

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        world = build_world(self._dir / "converted")
        self.code = self._dir / "workspace" / REPO
        self.code.parent.mkdir()
        shutil.move(str(world.code), str(self.code))
        # The registered payload resolves memory from the configured
        # coordination root, so the fixture memory moves to the default
        # external root rather than staying beside the world.
        self.memory = self._dir / "ar-coordination" / "memory-repos" / f"ar-{REPO}"
        self.memory.parent.mkdir(parents=True)
        shutil.move(str(world.memory), str(self.memory))
        locator = {"kind": "symbol", "name": "land_pair"}
        source = self.code / CODE_FILE
        # The edits land inside the anchored symbol: appending trailing
        # comments would leave the symbol's content identical (and the
        # records current), so each version rewrites the function body.
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "return (code, memory)", "return (memory, code)"
            ),
            encoding="utf-8",
        )
        commit_all(self.code, "second version")
        anchor_v2 = CodeSnapshot.capture(self.code).resolve(CODE_FILE, locator).to_document()
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "return (memory, code)", "return (code, code)"
            ),
            encoding="utf-8",
        )
        commit_all(self.code, "current version")
        second_legacy = "second-landing-section"
        second_invariant = derived_record_id("invariant", second_legacy)
        (
            self.memory / "knowledge" / "invariants" / f"{second_invariant}-second-landing.json"
        ).write_text(
            canonical(
                {
                    "schema": "ar-invariant/v1",
                    "id": second_invariant,
                    "revision": 1,
                    "status": "accepted",
                    "statement": "The second hold lands as one pair.",
                    "applicability": "Every landing.",
                    "conditions": [],
                    "exclusions": [],
                    "supersedes": [],
                    "admission": "legacy-unassessed",
                    "origin": {
                        "task": "260101-OLD",
                        "leaf": "260101-OLD-L1",
                        "legacyId": second_legacy,
                    },
                }
            ),
            encoding="utf-8",
        )
        family_path = next((self.memory / "knowledge" / "families").glob("*.json"))
        family = json.loads(family_path.read_text(encoding="utf-8"))
        family["members"] = [*family["members"], second_invariant]
        family_path.write_text(canonical(family), encoding="utf-8")
        card = self.memory / "onboarding" / f"{CODE_FILE}.md"
        sidecar_path = card.with_suffix(".json")
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        anchor_v1 = sidecar["realizes"][0]["anchor"]
        sidecar["realizes"].append(
            {
                "id": derived_realization_id(second_legacy, CODE_FILE, locator),
                "invariant": second_invariant,
                "anchor": anchor_v2,
                "role": "primary-authority",
                "rationale": "The second hold lands both halves.",
            }
        )
        sidecar["references"] = {
            "1": {"targets": [{"kind": "code", "anchor": dict(anchor_v1)}]},
            "2": {"targets": [{"kind": "code", "anchor": dict(anchor_v2)}]},
        }
        sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        card.write_text(
            "\n".join(
                [
                    f"# {self._TITLE}",
                    "",
                    f"## {self._FIRST}",
                    "",
                    "- first holds [1]",
                    "",
                    f"## {self._SECOND}",
                    "",
                    "- second holds [2]",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        commit_all(self.memory, "two records under two sections")
        self.config = _make_config(self._dir, self.code)

    def tearDown(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def test_two_records_in_one_file_resolve_to_their_own_sections(self) -> None:
        payload = read_ar_files_payload(self.config, REPO, [CODE_FILE])
        TOOL_RESPONSE_MODELS["read_ar_files"].model_validate(payload)
        self.assertEqual(payload["state"], "named", payload)
        (row,) = payload["files"]
        self.assertTrue(row["exists"], payload)
        self.assertEqual(row["recordCounts"].get("stale"), 2, payload)
        headings = {one["id"]: one["heading"] for one in row["actionNeeded"]}
        self.assertEqual(len(headings), 2, payload)
        self.assertEqual(set(headings.values()), {self._FIRST, self._SECOND}, payload)
        self.assertNotIn(self._TITLE, headings.values())
        self.assertNotEqual(self._TITLE, CODE_FILE)


# --------------------------------------------------------------------------
# overview locations: once per call, nearest first, openable
# --------------------------------------------------------------------------


class OverviewLocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.code = self._dir / "workspace" / REPO
        (self.code / "pkg" / "sub").mkdir(parents=True)
        (self.code / "pkg" / "sub" / "mod.py").write_text("a = 1\n", encoding="utf-8")
        self.onb = self._dir / "memory" / "onboarding"
        self.onb.mkdir(parents=True)
        _write_overview(self.onb, "", "# Repo overview\nroot text\n")
        _write_overview(self.onb, "pkg", "# pkg overview\npkg text\n")
        _write_overview(self.onb, "pkg/sub", "# sub overview\nsub text\n")
        _write_sidecar(self.onb, "pkg/sub/mod.py", "Mod body.")
        _write_route_index(self.onb, "pkg/sub", covered=["pkg/sub/mod.py"])
        self.config = _make_config(self._dir, self.code)

    def tearDown(self) -> None:
        pass

    def _read(self, files):
        ctx = _build_context(
            self.code,
            self.onb,
            coordination_root=self.config.coordination_root,
            storage_mode="repo-sidecar",
        )
        return read_ar_files_tool(
            self.config,
            repo_id=REPO,
            files=files,
            working_folder=self._dir,
            _context=ctx,
        )

    def test_chain_is_listed_once_nearest_first(self) -> None:
        result = self._read(["pkg/sub/mod.py"])
        self.assertEqual(
            result["overviews"],
            [
                "memory/onboarding/pkg/sub/overview.md",
                "memory/onboarding/pkg/overview.md",
                "memory/onboarding/overview.md",
            ],
        )

    def test_locations_open_as_written_and_carry_no_text(self) -> None:
        result = self._read(["pkg/sub/mod.py"])
        for location in result["overviews"]:
            self.assertTrue((self._dir / location).is_file(), location)
        dumped = json.dumps(result)
        for text in ("root text", "pkg text", "sub text", "Mod body."):
            self.assertNotIn(text, dumped)

    def test_twenty_paths_sharing_one_chain_name_it_once(self) -> None:
        # Packet boundary: twenty paths under one chain name the chain once,
        # nearest first, each overview a single time.
        files = []
        for index in range(20):
            rel = f"pkg/sub/extra{index}.py"
            (self.code / rel).write_text("a = 1\n", encoding="utf-8")
            files.append(rel)
        result = self._read(files)
        self.assertEqual(
            result["overviews"],
            [
                "memory/onboarding/pkg/sub/overview.md",
                "memory/onboarding/pkg/overview.md",
                "memory/onboarding/overview.md",
            ],
        )


# --------------------------------------------------------------------------
# memory states: missing or unconverted names no card
# --------------------------------------------------------------------------


class MemoryStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.code = self._dir / "workspace" / REPO
        (self.code / "pkg").mkdir(parents=True)
        (self.code / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
        self.onb = self._dir / "memory" / "onboarding"
        self.onb.mkdir(parents=True)
        _write_sidecar(self.onb, "pkg/mod.py", "Mod body.")
        _write_route_index(self.onb, "pkg", covered=["pkg/mod.py"])
        self.config = _make_config(self._dir, self.code)

    def tearDown(self) -> None:
        pass

    def _read(self, onboarding_root: Path):
        ctx = _build_context(
            self.code,
            onboarding_root,
            coordination_root=self.config.coordination_root,
            storage_mode="repo-sidecar",
        )
        return read_ar_files_tool(
            self.config,
            repo_id=REPO,
            files=["pkg/mod.py"],
            working_folder=self._dir,
            _context=ctx,
        )

    def test_missing_memory_names_no_card(self) -> None:
        shutil.rmtree(self._dir / "memory")
        result = self._read(self._dir / "memory" / "onboarding")
        self.assertEqual(result["memoryState"], "missing")
        self.assertTrue(result["files"][0]["exists"])
        self.assertIsNone(result["files"][0]["card"])

    def test_unconverted_memory_names_no_card(self) -> None:
        result = self._read(self.onb)
        self.assertEqual(result["memoryState"], "unconverted")
        self.assertTrue(result["files"][0]["exists"])
        self.assertIsNone(result["files"][0]["card"])


# --------------------------------------------------------------------------
# mounted route: the registered model validates the new shape
# --------------------------------------------------------------------------


class MountedContractTests(unittest.TestCase):
    """The mounted route resolves memory from the real coordination tree."""

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        self.fixture = build_read_scope_fixture(self._dir / "read-scope")
        self.code = self._dir / "read-scope" / REPO
        shutil.move(str(self.fixture.git_root), str(self.code))
        self.memory = self._dir / "ar-coordination" / "memory-repos" / f"ar-{REPO}"
        (self.memory / "system").mkdir(parents=True)
        (self.memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
        (self.memory / "onboarding").mkdir(parents=True)
        self.config = _make_config(self._dir, self.code)

    def tearDown(self) -> None:
        pass

    def test_named_answer_validates_against_the_registered_model(self) -> None:
        card = self.memory / "onboarding" / f"{BATCH_PATH}.md"
        card.parent.mkdir(parents=True, exist_ok=True)
        card.write_text("# batch\n\nLegacy card.\n", encoding="utf-8")
        payload = read_ar_files_payload(self.config, REPO, [BATCH_PATH])
        TOOL_RESPONSE_MODELS["read_ar_files"].model_validate(payload)
        self.assertEqual(payload["state"], "named")
        self.assertEqual(payload["memoryState"], "unconverted")
        self.assertTrue(payload["files"][0]["exists"])
        self.assertIsNone(payload["files"][0].get("card"))
        dumped = json.dumps(payload)
        self.assertNotIn("Legacy card.", dumped)

    def test_refused_answer_validates_against_the_registered_model(self) -> None:
        payload = read_ar_files_payload(self.config, REPO, [{"path": BATCH_PATH}])
        TOOL_RESPONSE_MODELS["read_ar_files"].model_validate(payload)
        self.assertEqual(payload["state"], "refused")
        self.assertEqual(payload["refusalCode"], "argument-removed")


# --------------------------------------------------------------------------
# published intent: the direct block keeps its contract (the tool no longer carries it)
# --------------------------------------------------------------------------


class PublishedIntentDirectTests(unittest.TestCase):
    """The selective block itself is unchanged; only its ride on read_ar_files is gone."""

    def setUp(self) -> None:
        self._dir = Path(tempfile.mkdtemp())
        world = build_world(self._dir / "converted")
        self.code = self._dir / "workspace" / REPO
        self.code.parent.mkdir()
        shutil.move(str(world.code), str(self.code))
        self.memory = world.memory
        self.config = _make_config(self._dir, self.code)

    def tearDown(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def _context(self):
        return _build_context(
            self.code,
            self.memory / "onboarding",
            coordination_root=self.config.coordination_root,
            storage_mode="repo-sidecar",
        )

    def _selection(self) -> PublishedIntentSelection:
        selected = resolve_published_intent(self._context())
        assert isinstance(selected, PublishedIntentSelection), selected
        return selected

    def test_a_seed_that_is_not_typed_is_refused_rather_than_raising(self) -> None:
        entry = read_published_intent(self._selection(), [cast(Any, object())])["seeds"][0]
        self.assertEqual(entry["state"], "refused")
        self.assertEqual(entry["refusalCode"], "invalid_payload")

    def test_a_path_with_no_recorded_anchor_is_a_named_absence(self) -> None:
        entry = published_intent_block(self._context(), ["unassigned/never-recorded.py"])["seeds"][
            0
        ]
        self.assertEqual(entry["refusalCode"], "registration_absent")
        self.assertEqual(entry["state"], "refused")
        self.assertNotIn("rows", entry)

    def test_a_path_that_cannot_be_a_seed_is_refused(self) -> None:
        entry = published_intent_block(self._context(), [":(exclude)pkg/a.py"])["seeds"][0]
        self.assertEqual(entry["state"], "refused")
        self.assertEqual(entry["refusalCode"], "invalid_payload")

    def test_an_unconverted_root_cannot_open_a_legacy_database(self) -> None:
        marker = self.memory / "knowledge/layout.json"
        marker.unlink()
        frozen = self.memory / "knowledge.sqlite"
        frozen.write_bytes(b"SQLite format 3\x00 frozen legacy bytes")
        block = published_intent_block(self._context(), [CODE_FILE])
        self.assertEqual(block["state"], "unusable")
        self.assertEqual(block["refusalCode"], "legacy-format")
        self.assertEqual(block["seeds"], [])


# --------------------------------------------------------------------------
# carrier parity: the skill names the answer's own keys
# --------------------------------------------------------------------------


class CarrierParityTests(unittest.TestCase):
    def test_the_carrier_uses_the_field_spellings_the_answer_returns(self) -> None:
        """The carrier names the answer's own keys, not a guess.

        A carrier that spells a field the answer never carries is a reader's
        dead end, and no behavioural case can catch it: the code is right and
        the instruction is wrong.
        """

        carrier = CARRIER.read_text(encoding="utf-8")
        for spelling in (
            "`card`",
            "`recordCounts`",
            "`actionNeeded`",
            "`overviews`",
            "`codeState`",
            "`memoryState`",
        ):
            self.assertIn(spelling, carrier)
        for retired in (
            "published_intent",
            "repository_overview",
            "route_overviews",
            "served_earlier",
        ):
            self.assertNotIn(retired, carrier)


# --------------------------------------------------------------------------
# registered naming on a converted world memory
# --------------------------------------------------------------------------


class RegisteredNamingTests(unittest.TestCase):
    """Registered ``read_ar_files`` calls on a converted world memory.

    Boundary refusal, nearest-first overviews, row-level confinement,
    stale-card absence and partial-index qualification through the
    registered handler -- not injected contexts.
    """

    def setUp(self) -> None:
        self.world = EntryPointWorld()
        self.addCleanup(self.world.close)
        self.world.build()
        code = self.world.code
        (code / "pkg" / "sub").mkdir(parents=True, exist_ok=True)
        for index in range(10):
            (code / f"pkg/mod{index}.py").write_text("x = 1\n", encoding="utf-8")
            (code / f"pkg/sub/deep{index}.py").write_text("x = 1\n", encoding="utf-8")
        (code / "pkg/stale.py").write_text("x = 1\n", encoding="utf-8")
        (code / "pkg/ok.py").write_text("x = 1\n", encoding="utf-8")
        memory = self.world.memory
        write_review_tree(memory)
        onb = memory / "onboarding"
        _write_overview(onb, "", "# Repo overview\nroot text\n")
        _write_overview(onb, "pkg", "# pkg overview\npkg text\n")
        _write_overview(onb, "pkg/sub", "# sub overview\nsub text\n")
        _write_route_index(
            onb,
            "pkg",
            covered=[f"pkg/mod{index}.py" for index in range(10)]
            + [f"pkg/sub/deep{index}.py" for index in range(10)]
            + ["pkg/stale.py", "pkg/ok.py"],
        )

    def _call(self, files, **arguments) -> dict[str, Any]:
        outcome, out = self.world.call(
            "read_ar_files", {"repo_id": "repo-a", "files": files, **arguments}
        )
        self.assertEqual(outcome, "RETURNED", out)
        self.assertIsInstance(out, dict)
        assert isinstance(out, dict)
        return out

    def test_omitted_refresh_names(self) -> None:
        out = self._call(["pkg/mod0.py"])
        self.assertEqual(out["state"], "named", out)
        self.assertTrue(out["files"][0]["exists"], out)

    def test_explicit_true_false_and_null_refresh_are_refused(self) -> None:
        for refresh in (True, False, None):
            with self.subTest(refresh=refresh):
                out = self._call(["pkg/mod0.py"], refresh=refresh)
                self.assertEqual(out["state"], "refused", out)
                self.assertEqual(out["refusalCode"], "argument-removed", out)
                self.assertEqual(
                    out["refusalDetail"],
                    "read the file with your own tools; this tool names the cards",
                    out,
                )

    def test_object_entries_are_refused(self) -> None:
        out = self._call([{"path": "pkg/mod0.py", "source": "full"}])
        self.assertEqual(out["state"], "refused", out)
        self.assertEqual(out["refusalCode"], "argument-removed", out)
        self.assertEqual(
            out["refusalDetail"],
            "read the file with your own tools; this tool names the cards",
            out,
        )

    def test_source_and_onboarding_entries_are_refused(self) -> None:
        for entry in (
            {"path": "pkg/mod0.py", "onboarding": "full"},
            {"path": "pkg/mod0.py", "source": "full", "onboarding": "full"},
        ):
            with self.subTest(entry=entry):
                out = self._call([entry])
                self.assertEqual(out["state"], "refused", out)
                self.assertEqual(out["refusalCode"], "argument-removed", out)
                self.assertEqual(
                    out["refusalDetail"],
                    "read the file with your own tools; this tool names the cards",
                    out,
                )

    def test_removed_refresh_is_absent_from_the_advertised_schema(self) -> None:
        server: Any = FastMCP("removed-argument-schema")
        for register_tools in TOOL_REGISTRARS:
            register_tools(server, None)  # type: ignore[arg-type]
        schemas = {tool.name: tool.inputSchema or {} for tool in asyncio.run(server.list_tools())}
        properties = schemas["read_ar_files"].get("properties", {})
        self.assertNotIn("refresh", properties, schemas["read_ar_files"])
        self.assertIn("files", properties, schemas["read_ar_files"])

    def test_twenty_paths_name_shared_chains_nearest_first(self) -> None:
        files = [f"pkg/mod{index}.py" for index in range(10)] + [
            f"pkg/sub/deep{index}.py" for index in range(10)
        ]
        out = self._call(files)
        self.assertEqual(out["state"], "named", out)
        self.assertEqual(len(out["files"]), 20, out)
        overviews = out["overviews"]
        self.assertEqual(len(overviews), len(set(overviews)), out)
        ranks = {location: rank for rank, location in enumerate(overviews)}
        sub = next(path for path in overviews if path.endswith("pkg/sub/overview.md"))
        pkg = next(
            path for path in overviews if path.endswith("pkg/overview.md") and "pkg/sub" not in path
        )
        root = next(
            path for path in overviews if path.endswith("overview.md") and "pkg" not in path
        )
        self.assertLess(ranks[sub], ranks[pkg], out)
        self.assertLess(ranks[pkg], ranks[root], out)

    def test_mixed_batch_reports_bad_rows_and_answers_good_ones(self) -> None:
        out = self._call(["../outside.py", "pkg/mod0.py", "/abs.py"])
        self.assertEqual(out["state"], "named", out)
        self.assertEqual(len(out["files"]), 3, out)
        self.assertEqual(out["files"][0]["refusalCode"], "outside-repository", out)
        self.assertTrue(out["files"][1]["exists"], out)
        self.assertNotIn("refusalCode", out["files"][1], out)
        self.assertEqual(out["files"][2]["refusalCode"], "outside-repository", out)

    def test_stale_covered_card_reports_absence(self) -> None:
        out = self._call(["pkg/stale.py", "pkg/mod0.py"])
        self.assertEqual(out["state"], "named", out)
        stale, _sibling = out["files"]
        self.assertTrue(stale["exists"], out)
        self.assertIsNone(stale.get("card"), out)
        self.assertIn("statesProblem", stale, out)

    def test_partial_index_qualifies_counts(self) -> None:
        (self.world.memory / "knowledge" / "broken.json").write_text("{not json", encoding="utf-8")
        (self.world.memory / "onboarding" / "pkg" / "ok.py.md").write_text(
            "# ok card\n", encoding="utf-8"
        )
        out = self._call(["pkg/ok.py"])
        self.assertEqual(out["state"], "named", out)
        (row,) = out["files"]
        self.assertEqual(row.get("indexComplete"), False, out)
        problem = row.get("statesProblem", "")
        self.assertIn("partial", problem, out)
        # Repair evidence: the skipped file with its reason, not a bare
        # pointer at the check tool.
        self.assertIn("broken.json", problem, out)
        self.assertIn("knowledge_integrity_check", problem, out)


# --------------------------------------------------------------------------
# registered repair evidence: malformed records name (path, reason)
# --------------------------------------------------------------------------


class RegisteredRepairEvidenceTests(unittest.TestCase):
    """Skipped index files name their path and reason through registered reads.

    A converted tree with a malformed existing record carries the repair
    evidence (path, reason, integrity-check route) alongside the surviving
    counts; the complete-tree control shows normality with no qualifier.
    """

    def setUp(self) -> None:
        self.world = EntryPointWorld()
        self.addCleanup(self.world.close)
        self.world.build()
        code, memory = self.world.code, self.world.memory
        (code / "pkg").mkdir(parents=True, exist_ok=True)
        (code / "pkg/mod.py").write_text("x = 1\n", encoding="utf-8")
        (memory / "knowledge" / "invariants").mkdir(parents=True, exist_ok=True)
        (memory / "knowledge/layout.json").write_text(
            json.dumps({"schema": "ar-memory-layout/v2", "conversion": "1"}),
            encoding="utf-8",
        )
        self.inv_id = derived_record_id("invariant", "repair-evidence")
        self.inv_rel = f"knowledge/invariants/{self.inv_id}-mod.json"
        (memory / self.inv_rel).write_text(
            canonical(
                {
                    "schema": "ar-invariant/v1",
                    "id": self.inv_id,
                    "revision": 1,
                    "status": "accepted",
                    "statement": "Mod lands as one pair.",
                    "applicability": "Every mod.",
                    "conditions": [],
                    "exclusions": [],
                    "supersedes": [],
                    "admission": "legacy-unassessed",
                    "origin": {
                        "task": "260928-MIK",
                        "leaf": "260928-MIK-L109",
                        "legacyId": "legacy-repair-evidence",
                    },
                }
            ),
            encoding="utf-8",
        )
        self.fam_id = derived_record_id("family", "repair-evidence-family")
        (memory / "knowledge/families").mkdir(parents=True, exist_ok=True)
        (memory / "knowledge/families" / f"{self.fam_id}-mod.json").write_text(
            canonical(
                {
                    "schema": "ar-family/v1",
                    "id": self.fam_id,
                    "revision": 1,
                    "status": "accepted",
                    "title": "Repair evidence family",
                    "guarantee": "Mod holds together.",
                    "members": [self.inv_id],
                    "routes": ["pkg"],
                    "admission": "legacy-unassessed",
                    "origin": {
                        "task": "260928-MIK",
                        "leaf": "260928-MIK-L109",
                        "legacyId": "legacy-repair-evidence-family",
                    },
                }
            ),
            encoding="utf-8",
        )
        onb = memory / "onboarding"
        _write_sidecar(onb, "pkg/mod.py", "Mod body.")
        blob = subprocess.run(
            ["git", "-C", str(code), "hash-object", "pkg/mod.py"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        digest = "sha256:" + hashlib.sha256((code / "pkg/mod.py").read_bytes()).hexdigest()
        self.rlz_id = "RLZ-500000"
        (onb / "pkg/mod.py.json").write_text(
            json.dumps(
                {
                    "schema": "ar-onboarding-file/v1",
                    "path": "pkg/mod.py",
                    "references": {},
                    "realizes": [
                        {
                            "id": self.rlz_id,
                            "invariant": self.inv_id,
                            "anchor": {
                                "locator": {"kind": "file"},
                                "blob": blob,
                                "content": digest,
                            },
                            "role": "primary-authority",
                            "rationale": "Mod enforces the invariant.",
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        _write_route_index(onb, "pkg", covered=["pkg/mod.py"])
        for root in (code, memory):
            for name, value in (
                ("user.email", "repair@example.invalid"),
                ("user.name", "repair"),
            ):
                subprocess.run(
                    ["git", "-C", str(root), "config", name, value],
                    check=True,
                    capture_output=True,
                )
        commit_all(code, "one governed file")
        commit_all(memory, "one governing card")

    def _call(self, files) -> dict[str, Any]:
        outcome, out = self.world.call("read_ar_files", {"repo_id": "repo-a", "files": list(files)})
        self.assertEqual(outcome, "RETURNED", out)
        self.assertIsInstance(out, dict)
        assert out is not None
        return out

    def test_complete_tree_names_counts_with_no_qualifier(self) -> None:
        out = self._call(["pkg/mod.py"])
        self.assertEqual(out["state"], "named", out)
        (row,) = out["files"]
        self.assertTrue(row["exists"], out)
        self.assertNotIn("knowledge index is partial", row.get("statesProblem", ""), out)
        # The live link is measured, not fallback: one current realization
        # counted, a current card, and no qualifier beside the counts (a
        # complete index carries no statesProblem/indexComplete keys).
        self.assertGreaterEqual(row["recordCounts"].get("current", 0), 1, out)
        self.assertEqual(row.get("cardState"), "current", out)
        self.assertEqual(row.get("statesProblem", ""), "", out)
        self.assertNotIn("indexComplete", row, out)

    def test_converted_malformed_record_names_repair_evidence(self) -> None:
        # Malform the existing indexed record (not an extra invalid file,
        # not a missing layout): the surviving counts stay beside the
        # repair evidence for the skipped file.
        (self.world.memory / self.inv_rel).write_text("{not json", encoding="utf-8")
        out = self._call(["pkg/mod.py"])
        self.assertEqual(out["state"], "named", out)
        (row,) = out["files"]
        self.assertTrue(row["exists"], out)
        problem = row.get("statesProblem", "")
        self.assertIn("knowledge index is partial", problem, out)
        self.assertIn(f"{self.inv_id}-mod.json", problem, out)
        self.assertIn("knowledge_integrity_check", problem, out)
        # The surviving zero is qualified, not normal: the linked
        # invariant is unreadable, so no current realization is counted
        # and the index is incomplete -- unlike the control's measured
        # nonzero counts with no qualifier.
        self.assertEqual(row["recordCounts"].get("current", 0), 0, out)
        self.assertFalse(row.get("indexComplete"), out)


# --------------------------------------------------------------------------
# registered card lifecycle: readability before emission, sibling by sibling
# --------------------------------------------------------------------------


class RegisteredCardLifecycleTests(unittest.TestCase):
    """Card create/delete/replace through the registered handler.

    Readability is checked before emission: an unreadable card names no
    location and no states, the actual surface is reported, and the valid
    sibling's row is asserted state-by-state in every phase, including the
    boundary replacement.
    """

    def setUp(self) -> None:
        self.world = EntryPointWorld()
        self.addCleanup(self.world.close)
        self.world.build()
        code, memory = self.world.code, self.world.memory
        (code / "pkg").mkdir(parents=True, exist_ok=True)
        (code / "pkg/keep.py").write_text("x = 1\n", encoding="utf-8")
        (code / "pkg/drop.py").write_text("x = 2\n", encoding="utf-8")
        onb = memory / "onboarding"
        (memory / "knowledge").mkdir(parents=True, exist_ok=True)
        (memory / "knowledge/layout.json").write_text(
            json.dumps({"schema": "ar-memory-layout/v2", "conversion": "1"}),
            encoding="utf-8",
        )
        _write_sidecar(onb, "pkg/keep.py", "Keep body.")
        _write_sidecar(onb, "pkg/drop.py", "Drop body.")
        _write_route_index(onb, "pkg", covered=["pkg/keep.py", "pkg/drop.py"])
        for root in (code, memory):
            for name, value in (
                ("user.email", "lifecycle@example.invalid"),
                ("user.name", "lifecycle"),
            ):
                subprocess.run(
                    ["git", "-C", str(root), "config", name, value],
                    check=True,
                    capture_output=True,
                )
        commit_all(code, "two governed files")
        commit_all(memory, "two governing cards")

    def _call(self, files) -> dict[str, Any]:
        outcome, out = self.world.call("read_ar_files", {"repo_id": "repo-a", "files": list(files)})
        self.assertEqual(outcome, "RETURNED", out)
        self.assertIsInstance(out, dict)
        assert out is not None
        return out

    def _rows(self, out: dict[str, Any]):
        rows = {str(row["path"]): row for row in out["files"]}
        self.assertEqual(set(rows), {"pkg/keep.py", "pkg/drop.py"}, out)
        return rows

    def _assert_valid_sibling(self, row, out) -> None:
        self.assertTrue(row["exists"], out)
        self.assertTrue(str(row["card"]).endswith("pkg/keep.py.md"), out)
        self.assertEqual(
            row.get("recordCounts"),
            {"current": 0, "stale": 0, "unverifiable": 0, "unrealized": 0},
            out,
        )
        self.assertIsNone(row.get("cardState"), out)
        self.assertEqual(row.get("actionNeeded"), [], out)
        self.assertIn("registration_absent", row.get("statesProblem", ""), out)

    def test_card_lifecycle_keeps_the_valid_sibling_state_by_state(self) -> None:
        files = ["pkg/keep.py", "pkg/drop.py"]
        rows = self._rows(self._call(files))
        self._assert_valid_sibling(rows["pkg/keep.py"], rows)
        self.assertTrue(str(rows["pkg/drop.py"]["card"]).endswith("pkg/drop.py.md"), rows)
        # Deleting the card names the absence and keeps the sibling.
        (self.world.memory / "onboarding" / "pkg/drop.py.md").unlink()
        rows = self._rows(self._call(files))
        self._assert_valid_sibling(rows["pkg/keep.py"], rows)
        dropped = rows["pkg/drop.py"]
        self.assertTrue(dropped["exists"], rows)
        self.assertIsNone(dropped.get("card"), rows)
        self.assertIn("no readable file", dropped.get("statesProblem", ""), rows)
        # Replacing the card at its path with unreadable bytes names the
        # actual surface instead of an unopenable location; the sibling
        # still aligns state-by-state.
        (self.world.memory / "onboarding" / "pkg/drop.py.md").write_bytes(b"\xff\xfe\x00not-utf8")
        rows = self._rows(self._call(files))
        self._assert_valid_sibling(rows["pkg/keep.py"], rows)
        dropped = rows["pkg/drop.py"]
        self.assertTrue(dropped["exists"], rows)
        self.assertIsNone(dropped.get("card"), rows)
        self.assertIn("could not be read", dropped.get("statesProblem", ""), rows)


# --------------------------------------------------------------------------
# registered distinct sections: two records, one file, own anchors
# --------------------------------------------------------------------------


class RegisteredDistinctSectionTests(unittest.TestCase):
    """Two stale records anchor one file at two recorded blobs (registered).

    Mirrors DistinctSectionHeadingTests through the registered handler:
    the card cites each blob under its own section through the sidecar's
    numbered references, with trailing context lines after each bullet so
    only a multiline reference match resolves them. Each record must
    resolve to its own section -- not to the first path-matching section,
    and not to the card title -- with no title fallback flagged.
    """

    _TITLE = "Human title unlike the path"
    _FIRST = "First hold"
    _SECOND = "Second hold"

    def setUp(self) -> None:
        self.world = EntryPointWorld()
        self.addCleanup(self.world.close)
        self.world.build()
        code, memory = self.world.code, self.world.memory
        rel = "pkg/hold.py"
        (code / "pkg").mkdir(parents=True, exist_ok=True)
        anchors = []
        for version in (1, 2):
            (code / rel).write_text(f"def hold():\n    return {version}\n", encoding="utf-8")
            blob = subprocess.run(
                ["git", "-C", str(code), "hash-object", rel],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            digest = "sha256:" + hashlib.sha256((code / rel).read_bytes()).hexdigest()
            anchors.append({"locator": {"kind": "file"}, "blob": blob, "content": digest})
        (code / rel).write_text("def hold():\n    return 3\n", encoding="utf-8")
        onb = memory / "onboarding"
        (memory / "knowledge").mkdir(parents=True, exist_ok=True)
        (memory / "knowledge/layout.json").write_text(
            json.dumps({"schema": "ar-memory-layout/v2", "conversion": "1"}),
            encoding="utf-8",
        )
        _write_overview(onb, "pkg", "# pkg overview\npkg text\n")
        invariants = []
        for index in range(2):
            inv = derived_record_id("invariant", f"reg-mirror-{index}")
            invariants.append(inv)
            inv_path = memory / "knowledge" / "invariants" / f"{inv}-hold-{index}.json"
            inv_path.parent.mkdir(parents=True, exist_ok=True)
            inv_path.write_text(
                canonical(
                    {
                        "schema": "ar-invariant/v1",
                        "id": inv,
                        "revision": 1,
                        "status": "accepted",
                        "statement": f"Hold {index} lands as one pair.",
                        "applicability": f"Every hold {index}.",
                        "conditions": [],
                        "exclusions": [],
                        "supersedes": [],
                        "admission": "legacy-unassessed",
                        "origin": {
                            "task": "260928-MIK",
                            "leaf": "260928-MIK-L109",
                            "legacyId": f"legacy-reg-mirror-{index}",
                        },
                    }
                ),
                encoding="utf-8",
            )
        (onb / f"{rel}.md").write_text(
            "\n".join(
                [
                    f"# {self._TITLE}",
                    "",
                    f"## {self._FIRST}",
                    "",
                    "- first holds [1]",
                    "Trailing context after the first bullet.",
                    "",
                    f"## {self._SECOND}",
                    "",
                    "- second holds [2]",
                    "Trailing context after the second bullet.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (onb / f"{rel}.json").write_text(
            json.dumps(
                {
                    "schema": "ar-onboarding-file/v1",
                    "path": rel,
                    "references": {
                        "1": {"targets": [{"kind": "code", "anchor": dict(anchors[0])}]},
                        "2": {"targets": [{"kind": "code", "anchor": dict(anchors[1])}]},
                    },
                    "realizes": [
                        {
                            "id": f"RLZ-{200 + index:06d}",
                            "invariant": inv,
                            "anchor": anchors[index],
                            "role": "primary-authority",
                            "rationale": f"Hold {index} lands both halves.",
                        }
                        for index, inv in enumerate(invariants)
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        _write_route_index(onb, "pkg", covered=[rel])
        for root in (code, memory):
            for name, value in (
                ("user.email", "sections@example.invalid"),
                ("user.name", "sections"),
            ):
                subprocess.run(
                    ["git", "-C", str(root), "config", name, value],
                    check=True,
                    capture_output=True,
                )
        commit_all(code, "current version")
        commit_all(memory, "two records under two sections")

    def test_two_records_resolve_to_their_own_sections_registered(self) -> None:
        outcome, out = self.world.call(
            "read_ar_files", {"repo_id": "repo-a", "files": ["pkg/hold.py"]}
        )
        self.assertEqual(outcome, "RETURNED", out)
        assert out is not None
        TOOL_RESPONSE_MODELS["read_ar_files"].model_validate(out)
        self.assertEqual(out["state"], "named", out)
        (row,) = out["files"]
        self.assertTrue(row["exists"], out)
        self.assertEqual(row["recordCounts"].get("stale"), 2, out)
        headings = {one["id"]: one["heading"] for one in row["actionNeeded"]}
        self.assertEqual(len(headings), 2, out)
        self.assertEqual(set(headings.values()), {self._FIRST, self._SECOND}, out)
        self.assertNotIn(self._TITLE, headings.values())
        for one in row["actionNeeded"]:
            self.assertFalse(one["titleFallback"], one)

    def test_unscored_sections_report_title_fallback_registered(self) -> None:
        onb = self.world.memory / "onboarding"
        (onb / "pkg/hold.py.md").write_text(
            "\n".join(
                [
                    f"# {self._TITLE}",
                    "",
                    "## Unrelated hold",
                    "",
                    "- nothing cites a reference here",
                    "Trailing context.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        sidecar_path = onb / "pkg/hold.py.json"
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["references"] = {}
        sidecar_path.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
        outcome, out = self.world.call(
            "read_ar_files", {"repo_id": "repo-a", "files": ["pkg/hold.py"]}
        )
        self.assertEqual(outcome, "RETURNED", out)
        assert out is not None
        (row,) = out["files"]
        self.assertEqual(row["recordCounts"].get("stale"), 2, out)
        self.assertEqual(len(row["actionNeeded"]), 2, out)
        for one in row["actionNeeded"]:
            self.assertEqual(one["heading"], self._TITLE, one)
            self.assertTrue(one["titleFallback"], one)


# --------------------------------------------------------------------------
# registered same-blob sections: content-exact scoring
# --------------------------------------------------------------------------


class RegisteredSameBlobSectionTests(unittest.TestCase):
    """Two stale records share one recorded blob under distinct sections (registered).

    Same file version, distinct recorded contents: the sidecar cites each
    content under its own section. Each record must resolve to the section
    citing its own content -- a record-to-heading mapping, not a set of
    values -- even though both sections match the shared blob. The
    earlier section must not win for the later section's record.
    """

    _TITLE = "Human title unlike the path"
    _FIRST = "First symbol"
    _SECOND = "Second symbol"

    def setUp(self) -> None:
        self.world = EntryPointWorld()
        self.addCleanup(self.world.close)
        self.world.build()
        code, memory = self.world.code, self.world.memory
        rel = "pkg/two.py"
        (code / "pkg").mkdir(parents=True, exist_ok=True)
        (code / rel).write_text("def two():\n    return 1\n", encoding="utf-8")
        blob = subprocess.run(
            ["git", "-C", str(code), "hash-object", rel],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        first_content = "sha256:" + "1" * 64
        second_content = "sha256:" + "2" * 64
        onb = memory / "onboarding"
        (memory / "knowledge").mkdir(parents=True, exist_ok=True)
        (memory / "knowledge/layout.json").write_text(
            json.dumps({"schema": "ar-memory-layout/v2", "conversion": "1"}),
            encoding="utf-8",
        )
        (memory / "knowledge/invariants").mkdir(parents=True, exist_ok=True)
        self.inv_ids = []
        for index, name in enumerate(("first", "second")):
            inv = derived_record_id("invariant", f"reg-same-blob-{index}")
            self.inv_ids.append(inv)
            (memory / "knowledge/invariants" / f"{inv}-hold-{index}.json").write_text(
                canonical(
                    {
                        "schema": "ar-invariant/v1",
                        "id": inv,
                        "revision": 1,
                        "status": "accepted",
                        "statement": f"Hold {name} lands as one pair.",
                        "applicability": f"Every hold {name}.",
                        "conditions": [],
                        "exclusions": [],
                        "supersedes": [],
                        "admission": "legacy-unassessed",
                        "origin": {
                            "task": "260928-MIK",
                            "leaf": "260928-MIK-L109",
                            "legacyId": f"legacy-reg-same-blob-{index}",
                        },
                    }
                ),
                encoding="utf-8",
            )
        (onb / "pkg").mkdir(parents=True, exist_ok=True)
        (onb / f"{rel}.md").write_text(
            "\n".join(
                [
                    f"# {self._TITLE}",
                    "",
                    f"## {self._FIRST}",
                    "",
                    "- first holds [1]",
                    "Trailing context after the first bullet.",
                    "",
                    f"## {self._SECOND}",
                    "",
                    "- second holds [2]",
                    "Trailing context after the second bullet.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (onb / f"{rel}.json").write_text(
            json.dumps(
                {
                    "schema": "ar-onboarding-file/v1",
                    "path": rel,
                    "references": {
                        "1": {
                            "targets": [
                                {
                                    "kind": "code",
                                    "anchor": {
                                        "locator": {"kind": "file"},
                                        "blob": blob,
                                        "content": first_content,
                                    },
                                }
                            ]
                        },
                        "2": {
                            "targets": [
                                {
                                    "kind": "code",
                                    "anchor": {
                                        "locator": {"kind": "file"},
                                        "blob": blob,
                                        "content": second_content,
                                    },
                                }
                            ]
                        },
                    },
                    "realizes": [
                        {
                            "id": "RLZ-400000",
                            "invariant": self.inv_ids[0],
                            "anchor": {
                                "locator": {"kind": "file"},
                                "blob": blob,
                                "content": first_content,
                            },
                            "role": "primary-authority",
                            "rationale": "First holds both halves.",
                        },
                        {
                            "id": "RLZ-400001",
                            "invariant": self.inv_ids[1],
                            "anchor": {
                                "locator": {"kind": "file"},
                                "blob": blob,
                                "content": second_content,
                            },
                            "role": "primary-authority",
                            "rationale": "Second holds both halves.",
                        },
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        _write_route_index(onb, "pkg", covered=[rel])
        for root in (code, memory):
            for name, value in (
                ("user.email", "sections@example.invalid"),
                ("user.name", "sections"),
            ):
                subprocess.run(
                    ["git", "-C", str(root), "config", name, value],
                    check=True,
                    capture_output=True,
                )
        (code / rel).write_text("def two():\n    return 2\n", encoding="utf-8")
        commit_all(code, "newer version stales the records")
        commit_all(memory, "two records under two sections")

    def test_same_blob_records_resolve_to_their_own_content_sections(self) -> None:
        outcome, out = self.world.call(
            "read_ar_files", {"repo_id": "repo-a", "files": ["pkg/two.py"]}
        )
        self.assertEqual(outcome, "RETURNED", out)
        assert out is not None
        TOOL_RESPONSE_MODELS["read_ar_files"].model_validate(out)
        self.assertEqual(out["state"], "named", out)
        (row,) = out["files"]
        self.assertTrue(row["exists"], out)
        self.assertEqual(row["recordCounts"].get("stale"), 2, out)
        headings = {one["id"]: one["heading"] for one in row["actionNeeded"]}
        self.assertEqual(
            headings,
            {self.inv_ids[0]: self._FIRST, self.inv_ids[1]: self._SECOND},
            out,
        )
        for one in row["actionNeeded"]:
            self.assertFalse(one["titleFallback"], one)


# --------------------------------------------------------------------------
# test plumbing
# --------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
