"""Tool-description bounds (MIK-R112 rule 7, amended in place under D126).

Every registered tool holds to 600 characters, except a tool whose row in the table of
MIK-R108 rule 6 is an interim row of L109, L110 or L111: that tool keeps its description
until its leaf lands, held to the length it has when this leaf lands
(``INTERIM_DESCRIPTION_LENGTHS``). Every rewritten description ends by naming the one
place with the longer explanation, and the test checks that place exists. Argument schema
descriptions, where they exist, hold to 120 characters. A leaf that adds a tool adds its
description under the same bound in the same commit.
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.install import provider_watchers as install_provider_watchers
from agents_remember.install import runtime as install_runtime
from agents_remember.mcp.registration import TOOL_REGISTRARS
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_descriptions import (
    INTERIM_DESCRIPTION_OWNERS,
    RETAINED_DESCRIPTION_LENGTHS,
    TOOL_ARGUMENT_DESCRIPTION_MAX_CHARS,
    TOOL_DESCRIPTION_MAX_CHARS,
)
from mcp.server.fastmcp import FastMCP

# The length each interim-owned description has when this leaf lands. Recorded at
# landing from the live server; the test fails on any growth before the owning leaf
# rewrites it.
INTERIM_DESCRIPTION_LENGTHS: dict[str, int] = {
    "memory_quality_check": 1865,
    "citation_fix": 848,
    "route_index_refresh": 244,
    "memory_init": 701,
    "memory_baseline_status": 111,
    "memory_baseline_adopt": 278,
    "memory_carryover_plan": 172,
    "memory_carryover_apply": 233,
    "knowledge_integrity_check": 895,
    "curator_coherence": 1467,
    "worktree_start": 1853,
    "worktree_attach": 430,
    "worktree_status": 608,
    "worktree_sync": 1574,
    "worktree_pause": 828,
    "direct_landing": 1562,
    "worktree_closeout_preview": 296,
    "worktree_closeout_apply": 888,
    "worktree_integrate": 597,
    "worktree_checkpoint_landing": 1251,
    "worktree_record_landing": 620,
    "worktree_operation_control": 445,
    "worktree_cleanup": 507,
    "worktree_abandon": 578,
    "role_start": 1990,
    "role_message": 2523,
    "task_doc": 11974,
    "worktree_enclosure_adopt": 285,
    "lifecycle_finalize_task": 1174,
    "closeout_queue": 571,
    "task_reopen": 798,
}

_POINTER_RE = re.compile(r"(docs/[^\s]+\.md|skill://[^\s]+|skill [a-z0-9][a-z0-9-]*)\.?$")

# The shipped longer-explanation file (MIK-R112 rule 7): canonical in the checkout,
# copied byte-for-byte into the packaged runtime tree, and installed to
# <coordinationRoot>/docs/ so the short descriptions name one place in both layouts.
TOOL_DETAILS_NAME = "tool-details.md"
TOOL_DETAILS_CHECKOUT = REPO_ROOT / "docs" / TOOL_DETAILS_NAME
TOOL_DETAILS_PACKAGED = (
    MCP_SRC / "agents_remember" / "package_data" / "runtime" / "docs" / TOOL_DETAILS_NAME
)


def _live_descriptions() -> dict[str, str]:
    server = FastMCP("description-bounds-probe")
    for register_tools in TOOL_REGISTRARS:
        register_tools(server, None)  # type: ignore[arg-type]
    return {
        tool.name: " ".join((tool.description or "").split())
        for tool in asyncio.run(server.list_tools())
    }


def _live_tool_schemas() -> dict[str, dict[str, Any]]:
    server = FastMCP("argument-bounds-probe")
    for register_tools in TOOL_REGISTRARS:
        register_tools(server, None)  # type: ignore[arg-type]
    return {tool.name: tool.inputSchema or {} for tool in asyncio.run(server.list_tools())}


def _check_node_description(schema: dict[str, Any], prefix: str, overlong: list[str]) -> None:
    description = schema.get("description", "")
    if len(description) > TOOL_ARGUMENT_DESCRIPTION_MAX_CHARS:
        overlong.append(f"{prefix} ({len(description)} characters)")


def _collect_child_arguments(
    tool: str,
    children: list[tuple[str, dict[str, Any]]],
    overlong: list[str],
) -> None:
    for prefix, child in children:
        _collect_overlong_arguments(tool, child, prefix, overlong)


def _collect_overlong_arguments(
    tool: str, schema: dict[str, Any], prefix: str, overlong: list[str]
) -> None:
    # Parent ruling 0a408a8a: EVERY description in a published input schema is
    # bounded -- arguments, fields of models, models themselves, and anything
    # reachable through composition/items containers. A model that an argument
    # takes is published under $defs with its own description; the agent
    # carries all of it, so its description is checked here, not skipped.
    # The current node itself is checked on entry, so descriptions sitting
    # directly on a composition branch, items or additionalProperties node are
    # inspected; child collectors below only recurse and never re-check.
    _check_node_description(schema, prefix, overlong)
    _collect_child_arguments(
        tool,
        [
            (f"{prefix}.{name}", prop)
            for name, prop in (schema.get("properties") or {}).items()
            if isinstance(prop, dict)
        ]
        + [
            (f"{prefix}.$defs.{def_name}", definition)
            for def_name, definition in (schema.get("$defs") or {}).items()
            if isinstance(definition, dict)
        ],
        overlong,
    )
    for keyword in ("allOf", "anyOf", "oneOf"):
        branches = schema.get(keyword) or []
        if isinstance(branches, list):
            _collect_child_arguments(
                tool,
                [
                    (f"{prefix}.{keyword}[{index}]", branch)
                    for index, branch in enumerate(branches)
                    if isinstance(branch, dict)
                ],
                overlong,
            )
    for keyword in ("items", "additionalProperties"):
        nested = schema.get(keyword)
        if isinstance(nested, dict):
            _collect_overlong_arguments(tool, nested, f"{prefix}.{keyword}", overlong)


def _pointer_target(place: str) -> Path | None:
    if place.startswith("docs/"):
        return REPO_ROOT / place.partition("#")[0]
    if place.startswith("skill://"):
        return None
    if place.startswith("skill "):
        return REPO_ROOT / "skills" / place[len("skill ") :]
    return None


class ToolDescriptionBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.described = _live_descriptions()

    def test_tool_descriptions_hold_bounds_and_name_where_the_rest_is(self) -> None:
        self.assertEqual(set(self.described), set(PUBLIC_TOOLS))
        for name, text in sorted(self.described.items()):
            with self.subTest(tool=name):
                self.assertTrue(text.strip(), f"{name} has no description")
                if name in INTERIM_DESCRIPTION_OWNERS:
                    self.assertIn(
                        name,
                        INTERIM_DESCRIPTION_LENGTHS,
                        f"{name} is interim-owned but has no recorded landing length",
                    )
                    self.assertLessEqual(
                        len(text),
                        INTERIM_DESCRIPTION_LENGTHS[name],
                        f"{name} grew past its landing length "
                        f"({INTERIM_DESCRIPTION_LENGTHS[name]}); only "
                        f"{INTERIM_DESCRIPTION_OWNERS[name]} may rewrite it",
                    )
                    continue
                if name in RETAINED_DESCRIPTION_LENGTHS:
                    self.assertLessEqual(
                        len(text),
                        RETAINED_DESCRIPTION_LENGTHS[name],
                        f"{name} grew past its retained length "
                        f"({RETAINED_DESCRIPTION_LENGTHS[name]})",
                    )
                    continue
                self.assertLessEqual(
                    len(text),
                    TOOL_DESCRIPTION_MAX_CHARS,
                    f"{name} exceeds {TOOL_DESCRIPTION_MAX_CHARS} characters",
                )
                match = _POINTER_RE.search(text)
                self.assertIsNotNone(
                    match,
                    f"{name} does not end by naming where the longer explanation is",
                )
                assert match is not None
                target = _pointer_target(match.group(1))
                if target is not None:
                    self.assertTrue(
                        target.exists(), f"{name} points at a place that does not exist"
                    )

    def test_argument_schema_descriptions_hold_their_bound(self) -> None:
        # The live input schemas, not the adapter source: the bound governs
        # what the agent reads, and the adapters declare arguments through
        # @server.tool() (a Call decorator), which a source walk for a bare
        # ``tool`` attribute never visits.
        schemas = _live_tool_schemas()
        self.assertEqual(set(schemas), set(PUBLIC_TOOLS))
        overlong: list[str] = []
        for name in sorted(schemas):
            _collect_overlong_arguments(name, schemas[name], name, overlong)
        self.assertEqual(overlong, [])

    def test_model_description_bound_holds_with_positive_control(self) -> None:
        # Parent ruling 0a408a8a: the bound governs every description in a
        # published input schema, including each model definition. The 121
        # control must fail; the 120 boundary must pass. Pure shape cases --
        # no server, no world.
        overlong: list[str] = []
        _collect_overlong_arguments(
            "control",
            {"$defs": {"Model": {"description": "x" * 121, "properties": {}}}},
            "control",
            overlong,
        )
        self.assertEqual(len(overlong), 1)
        self.assertTrue(overlong[0].startswith("control.$defs.Model (121 characters)"))
        fitting: list[str] = []
        _collect_overlong_arguments(
            "control",
            {"$defs": {"Model": {"description": "x" * 120, "properties": {}}}},
            "control",
            fitting,
        )
        self.assertEqual(fitting, [])

    def test_owned_descriptions_cover_every_moved_detail(self) -> None:
        # MIK-R112 F08 moved-material inventory: every argument, operation and
        # refusal the two owned short descriptions omit lives in the shipped
        # longer explanation. The keyed phrases below are the omitted details.
        details = TOOL_DETAILS_CHECKOUT.read_text(encoding="utf-8")
        required = (
            "unusedProviderKeys",
            "install_provider_deps=true",
            "no longer",
            "install/start/watch",
            "providers-remove",
            "dry_run",
            "reportPath",
            "role-capsules",
            "AR_EXPERIMENT",
            "refused by name",
            "withheld",
            "rollback",
            "wouldInstallHost",
            "configured: false",
            "restartRequired",
            "never count as enabled",
            "providerless",
            "cache-reuse flag",
            "changes nothing observable",
        )
        for phrase in required:
            self.assertIn(
                phrase,
                details,
                f"moved detail missing from {TOOL_DETAILS_NAME}: {phrase}",
            )


class ShippedToolDetailsTests(unittest.TestCase):
    def test_packaged_copy_matches_the_checkout_canonical(self) -> None:
        self.assertTrue(TOOL_DETAILS_PACKAGED.exists(), "packaged docs copy is missing")
        self.assertEqual(
            TOOL_DETAILS_PACKAGED.read_bytes(),
            TOOL_DETAILS_CHECKOUT.read_bytes(),
            "packaged docs/tool-details.md drifted from the checkout canonical",
        )

    def test_disposable_install_reaches_the_named_place(self) -> None:
        # The pointer names docs/tool-details.md in both layouts: the checkout
        # file (checked by the bounds test above) and the installed
        # <coordinationRoot>/docs/ copy. This installs the real packaged tree
        # into a disposable coordination root and reads the named place back.
        def watcher_result(action: str, **kwargs: object) -> dict:
            return {
                "provider": "watchers",
                "action": action,
                "ok": True,
                "partial": False,
                "recoveryActions": [],
            }

        with tempfile.TemporaryDirectory() as tmp_dir:
            coordination_root = Path(tmp_dir) / "ar-coordination"
            with patch.object(
                install_provider_watchers.lifecycle,
                "watchers_run",
                side_effect=lambda args, action: watcher_result(action),
            ):
                install_runtime.install_runtime(
                    MCP_SRC / "agents_remember" / "package_data",
                    coordination_root,
                    dry_run=False,
                    provider_deps=install_runtime.ProviderDependencyInstall(
                        settings={}, timeout=1800, enabled=False
                    ),
                )
            installed = coordination_root / "docs" / TOOL_DETAILS_NAME
            self.assertTrue(installed.exists(), "installed docs/tool-details.md is missing")
            self.assertEqual(
                installed.read_bytes(),
                TOOL_DETAILS_CHECKOUT.read_bytes(),
                "installed docs/tool-details.md differs from the checkout canonical",
            )


if __name__ == "__main__":
    unittest.main()
