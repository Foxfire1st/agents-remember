"""The controls of the entry-point sweep: the instrument must be able to say no.

Moved unchanged out of ``test_tool_entry_point_sweep.py`` so that module stays under the
repository's file-size rail (MIK-R26 rule 7, N1); the sweep's world, its pins and its probe cases
stay there. Three classes:

* ``EntryPointCoverageTests``: the swept population is derived from the advertised roster, so a new
  tool cannot arrive unswept;
* ``ChokePointControlTests``: a payload its model forbids is refused by the choke point, and the
  sweep's classifier marks it -- if the choke point ever stops validating, this goes red rather
  than the whole sweep quietly passing on payloads nothing checked;
* ``EntryPointCensusControlTests``: the hermeticity census sees a write, a rewrite and a delete.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

import anyio
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.mcp.registration import core as core_registration
from agents_remember.mcp.tools.base import _tool_payload
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.models.tools.tool_response import finalize_tool_response
from pydantic import ValidationError
from tool_entry_point_world import REPO, EntryPointWorld, _ToolCallTimeout, classify

from mcp import types


class EntryPointCoverageTests(unittest.TestCase):
    """The swept population is derived, so a new tool cannot arrive unswept."""

    def test_the_swept_population_is_the_advertised_one(self) -> None:
        world = EntryPointWorld()
        self.addCleanup(world.close)
        arguments = world.benign()
        self.assertEqual(set(), set(PUBLIC_TOOLS) - set(arguments), "roster tools with no case")
        self.assertEqual(set(), set(arguments) - set(PUBLIC_TOOLS), "cases for no tool")
        self.assertEqual(
            set(),
            set(PUBLIC_TOOLS) - set(TOOL_RESPONSE_MODELS),
            "advertised tools with no registered response model",
        )
        # Both sides are derived from the live server, so the sweep covers what is advertised
        # rather than what this module believes is advertised.
        advertised = {tool.name for tool in anyio.run(world.server.list_tools)}
        self.assertEqual(set(PUBLIC_TOOLS), advertised)


class ChokePointControlTests(unittest.TestCase):
    """The control the sweep depends on: a bad payload must be caught, not described."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        cls.addClassCleanup(cls.world.close)
        world = cls.world
        declare_test_process()
        cls.good = (world.call("ping", {})[1]) or {}

    def test_a_tool_that_never_answers_fails_by_name_and_closes_its_handler(self) -> None:
        handler_closed: list[bool] = []

        async def no_response(request):
            try:
                await anyio.sleep_forever()
            finally:
                handler_closed.append(True)

        with (
            mock.patch.dict(
                self.world.server._mcp_server.request_handlers,
                {types.CallToolRequest: no_response},
            ),
            mock.patch("tool_entry_point_world.TOOL_RESPONSE_WAIT_SECONDS", 3),
            self.assertRaisesRegex(
                _ToolCallTimeout, "'ping' tool response did not finish within 3 seconds"
            ),
        ):
            self.world.call("ping", {})
        self.assertEqual(handler_closed, [True])
        self.assertEqual("payload", classify("ping", self.world.call("ping", {}))[0])

    def test_a_payload_its_model_forbids_is_refused_by_the_choke_point(self) -> None:
        self.assertTrue(self.good, "the control needs a real payload to corrupt")
        self.assertEqual("payload", classify("ping", ("RETURNED", self.good))[0])
        corrupted = {**self.good, "undeclaredControlKey": 1}
        with self.assertRaises(ValidationError):
            finalize_tool_response("ping", corrupted)
        self.assertEqual(
            "unvalidatable",
            classify("ping", ("RETURNED", corrupted))[0],
            "the choke point validated but the sweep's classifier did not notice",
        )

    def test_a_raising_entry_point_is_classified_as_losing_the_envelope(self) -> None:
        self.assertEqual("error", classify("ping", ("RAISED ToolError", None))[0])
        self.assertEqual("unvalidatable", classify("ping", ("RETURNED", None))[0])
        bare = classify("ping", ("RETURNED", {**self.good, "ok": False}))[0]
        self.assertNotEqual("refusal", bare, "a bare ok:false must not pass as a typed refusal")
        self.assertEqual("bare-not-ok", bare)

    def test_a_producer_key_its_model_forbids_arrives_as_boundary_validation(self) -> None:
        """A `T7`-class break, driven through the entry point, lands in its own named arm.

        The arm is not decoration: because the choke point validates before returning, this is
        the shape such a break actually takes for a caller, so the sweep asserts THIS arm empty
        by name rather than letting the break appear as an anonymous raiser.
        """

        # Patch where the handler LOOKS IT UP: the registrar imports the builder by name, so
        # patching the defining module would leave the registered handler untouched and the
        # case would pass on an uncorrupted payload.
        original = core_registration.ping_payload

        def through_the_choke_point() -> dict[str, Any]:
            return _tool_payload("ping", {**original(), "undeclaredControlKey": 1})

        with mock.patch.object(core_registration, "ping_payload", through_the_choke_point):
            kind, payload = self.world.call("ping", {})
        arm, detail = classify("ping", (kind, payload))
        self.assertEqual("boundary-validation", arm, detail)
        self.assertIn("PingResponse", detail, "the detail must name the model that refused")
        self.assertEqual(
            "payload", classify("ping", self.world.call("ping", {}))[0], "the patch leaked"
        )

    def test_a_handler_that_bypasses_the_choke_point_arrives_as_unvalidatable(self) -> None:
        """The `unvalidatable` arm IS reachable at the entry point — by the bypass shape.

        ``mcp/tools/base.py::_tool_payload`` is the only route from a handler to the wire and
        the only place a payload meets its model, so a handler that returns a raw dict past it
        hands the consumer a payload nothing validated. That is the shape
        ``test_tool_response_conformance``'s structural case guards against statically; this
        case measures what it costs the caller, and is why the sweep asserts the arm empty.
        """

        original = core_registration.ping_payload

        def bypassing() -> dict[str, Any]:
            return {**original(), "undeclaredControlKey": 1}

        with mock.patch.object(core_registration, "ping_payload", bypassing):
            kind, payload = self.world.call("ping", {})
        arm, detail = classify("ping", (kind, payload))
        self.assertEqual("unvalidatable", arm, detail)
        self.assertIn("undeclaredControlKey", detail)

    def test_an_invalid_argument_lands_in_its_own_arm_and_names_the_callers_side(self) -> None:
        """The two validation refusals share a message shape; only the model separates them.

        ``citation_fix`` requires a ``contract_path``, so calling it without one is refused by
        the generated ``citation_fixArguments`` input model. That must NOT be reported as the
        tool's own response model refusing its payload — the fix exists so the diagnosis stays
        right the first time a benign argument stops being accepted.
        """

        kind, payload = self.world.call("citation_fix", {"repo_id": REPO})
        arm, detail = classify("citation_fix", (kind, payload))
        self.assertEqual("argument-validation", arm, detail)
        self.assertIn("citation_fixArguments", detail, "the detail must name the input model")
        self.assertNotIn(
            TOOL_RESPONSE_MODELS["citation_fix"].__name__,
            detail,
            "an argument refusal reported as the response model refusing its payload",
        )


class EntryPointCensusControlTests(unittest.TestCase):
    """The hermeticity check must be able to see a write, a rewrite and a delete."""

    def setUp(self) -> None:
        self.world = EntryPointWorld()
        self.addCleanup(self.world.close)

    def test_the_census_notices_a_write_a_rewrite_and_a_delete(self) -> None:
        for name, mutate in (
            ("write into the memory repository", self._write_memory),
            ("rewrite the code repository's README", self._rewrite_code_readme),
            ("delete the code repository's README", self._delete_code_readme),
        ):
            repo = self.world.memory if "memory" in name else self.world.code
            before = self.world.repository_state(repo)
            mutate()
            after = self.world.repository_state(repo)
            self.assertNotEqual(
                before["census"],
                after["census"],
                f"the census did not notice a mutation that did {name}",
            )

    def _write_memory(self) -> None:
        (self.world.memory / "system" / "undeclared-write.md").write_text(
            "written by the census control\n", encoding="utf-8"
        )

    def _rewrite_code_readme(self) -> None:
        (self.world.code / "README.md").write_text("rewritten\n", encoding="utf-8")

    def _delete_code_readme(self) -> None:
        (self.world.code / "README.md").unlink()

    def test_the_coordination_boundary_notices_an_undeclared_add_rewrite_and_delete(self) -> None:
        """The coordination-root half of the hermeticity case, exercised where it is blind.

        Three mutations the repository censuses cannot see: a file added at the coordination
        root outside every write zone, a pre-existing file there rewritten, and a file deleted.
        The predicate is the one the case asserts, so this control proves the case rather than a
        second implementation of it.
        """

        world = self.world
        # An anchor at the coordination root itself: outside every declared write zone, and not
        # inside either repository, so only this boundary can see it. `coord/system/` would be
        # inside a zone (it holds the installed scaffold) and would prove nothing.
        anchor = world.coord / "outside-zone-anchor.md"
        anchor.write_text("anchor\n", encoding="utf-8")
        for name, mutate in (
            ("add a file outside every write zone", self._add_outside_zones),
            ("rewrite a file outside every write zone", self._rewrite_outside_zones),
            ("delete a file outside every write zone", self._delete_outside_zones),
        ):
            before = world.coordination_census()
            mutate()
            after = world.coordination_census()
            boundary = world.coordination_boundary(before, after)
            noticed = (
                boundary["added_outside_zones"]
                or boundary["rewritten_outside_zones"]
                or boundary["removed"]
            )
            self.assertTrue(noticed, f"the boundary did not notice a mutation that did {name}")

    def _add_outside_zones(self) -> None:
        (self.world.coord / "undeclared.md").write_text("added\n", encoding="utf-8")

    def _rewrite_outside_zones(self) -> None:
        (self.world.coord / "outside-zone-anchor.md").write_text("rewritten\n", encoding="utf-8")

    def _delete_outside_zones(self) -> None:
        (self.world.coord / "outside-zone-anchor.md").unlink()
