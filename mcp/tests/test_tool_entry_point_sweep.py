"""Every public tool's production entry point, swept in one hermetic world.

This module is the durable form of `260918-TSIP-L5`'s trace: it drives **every** tool the
server advertises through the same adapter a consumer's call takes — a real in-memory MCP
client session against a server built by ``create_server`` — and asserts that each one
answers in the product's own vocabulary.

**The arms this sweep publishes, and where each one is reachable:**

======================== =============================================================
``payload``              the tool answered and its payload validates.
``refusal``              ``ok: false`` with a machine-readable refusal identity or a next
                         action, so the caller keeps ``ok``/``status``.
``bare-not-ok``          ``ok: false`` with neither — pinned below.
``unvalidatable``        a payload that does not validate **where the classifier sees it**.
                         Reachable through the entry point by exactly one shape: a handler
                         that returns a raw dict PAST the choke point, which is the only
                         place a payload meets its model. Both controls below drive it.
``boundary-validation``  a raise in which the tool's OWN registered response model refused the
                         payload its producer built — the shape a ``T7``-class break takes when
                         the producer emits the key *through* ``_tool_payload``. Asserted empty
                         by name, so such a break is diagnosed instead of appearing anonymous.
``argument-validation``  a raise in which some other model refused, in practice the generated
                         ``<tool>Arguments`` input model: the CALLER's error. It shares a
                         message shape with the arm above and is separated by model identity,
                         never by wording. Asserted empty by name too.
``error``                any other raise: the envelope is gone. Pinned twice, below.
======================== =============================================================

`T34` (owner `L6`, registered on master `260918_tool-surface-and-process-integrity`) is the
first pin: an ordinary absent-capability condition escapes as a bare Python exception, so the
caller loses ``ok``, ``status`` and every recovery key. `L5`'s round-1 trace measured them;
this module keeps measuring them, so the set can only change on purpose. The second pin is the
same defect on the lifecycle family's *state* precondition, which this sweep reaches a typed
branch for by arranging the state its tools document — so it would otherwise read green over
those five.

**The rule for both pins:** when `L6` repairs one of these tools, delete that entry **in the
same change** that lands the repair — never delete a constant, and never widen one to make a
new failure pass. A tenth raiser is a failure: each pin is asserted *equal* to what was
observed, in both directions.

The positive control is a case, not a comment (``ChokePointControlTests``, in
``test_tool_entry_point_controls.py``): it drives
``models/tools/tool_response.py::finalize_tool_response`` with a payload its model forbids and
requires it to refuse, and requires the sweep's own classifier to mark that payload
unvalidatable. If the choke point ever stops validating, that case goes red rather than the
whole sweep quietly passing on payloads nothing checked.
"""

from __future__ import annotations

import unittest
from typing import Any

from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from tool_entry_point_world import (
    EXPECTED_LIFECYCLE_STATE_POPULATION,
    KNOWN_MEMORY_SCAFFOLD_ADDITIONS,
    LIFECYCLE_STATE_POPULATION,
    NAVIGATION_KEYS,
    REFUSAL_IDENTITY_KEYS,
    UNMARKED_NOT_OK,
    EntryPointWorld,
    _git,
    refusal_marks,
)

# ---------------------------------------------------------------------------------------
# T34: the tools that lose the whole envelope. Owner L6 -- REPAIRED, and the pin is now
# empty on purpose rather than deleted.
#
# `260918-TSIP-L6` repaired all nine at `application/provider_tools.py` and
# `application/memory_tools.py`: each now answers with the standard refusal envelope
# (`ok:false` plus `state`/`status`/`detail`/`nextAction`) instead of raising, which is what
# its own siblings `provider_status` (`providers.state: "noProviders"`) and
# `memory_baseline_status` already did under the identical absence.
#
# This constant was NOT deleted when the repair landed, because deleting it would delete the
# only place that says what the sweep is allowed to observe. It is empty, and the rule stands:
# an entry may only be added here deliberately, in the change that introduces the raiser this
# list would then pin. Reintroducing one means editing BOTH this constant and
# `test_the_pinned_raisers_are_exactly_the_ones_that_lose_the_envelope`, which asserts this set
# equals the sweep's observed error arm in both directions.
# ---------------------------------------------------------------------------------------
ENVELOPE_LOSING_RAISERS: frozenset[str] = frozenset()

# What the repaired nine must answer with now. Derived from the roster rather than hard-coded,
# so the case below cannot rot: these are the tools T34 named, and every one of them must still
# be advertised and must still answer in the envelope.
T34_REPAIRED_TOOLS: frozenset[str] = frozenset(
    {
        "memory_baseline_adopt",
        "grepai_search",
        "grepai_trace",
        "cgc_symbol_search",
        "cgc_callers",
        "cgc_callees",
        "cgc_dependencies",
        "cgc_complexity",
        "cgc_visualize",
    }
)

# ---------------------------------------------------------------------------------------
# The second pin, same defect, different precondition: the lifecycle family's *state*.
#
# Each of these five tools documents one ambient state and answers in an envelope in that
# state (the sweep arranges it, and reaches a payload or a refusal for all five). Called in
# any other state it raises, so the caller loses `ok`/`status`/`nextStep` exactly as T34's
# nine do. Measured as a four-state matrix (none / running / blocked / awaiting-developer);
# the map below is that matrix, tool -> the states in which the envelope is lost. Owner L6,
# with the sibling census's rows 13/15/16/17. Same rule: repair one, remove its entry in the
# same change; the matrix case asserts this map equals what it observes, in both directions.
# ---------------------------------------------------------------------------------------
STATE_DEPENDENT_RAISERS: dict[str, tuple[str, ...]] = {
    "lifecycle_start": ("running", "blocked", "awaiting-developer"),
    "lifecycle_resume": ("none", "running", "awaiting-developer"),
    "lifecycle_turn_end_notification": ("none", "blocked", "awaiting-developer"),
    "lifecycle_end": ("none",),
    "lifecycle_phase": ("none",),
}


class EntryPointProbeTests(unittest.TestCase):
    """One sweep, one world, and the assertions that make it mean something."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        cls.addClassCleanup(cls.world.close)
        cls.world.build()
        cls.before_contract = cls.world.contract.read_bytes()
        cls.before_worktrees = _git(cls.world.code, "worktree", "list")
        cls.before_code = cls.world.repository_state(cls.world.code)
        cls.before_memory = cls.world.repository_state(cls.world.memory)
        cls.before_coordination = cls.world.coordination_census()
        cls.results = cls.world.sweep()

    def test_every_public_tool_answers_in_the_products_own_vocabulary(self) -> None:
        broken = {
            tool: row["detail"]
            for tool, row in self.results.items()
            if row["arm"] == "unvalidatable"
        }
        self.assertEqual({}, broken, "payloads that failed their own response model")
        # Not "no exceptions": a producer key its model forbids arrives HERE at the entry
        # point, because the choke point validates before returning. Asserted by name so a
        # T7-class break is diagnosed as what it is instead of as a raiser.
        self.assertEqual(
            {},
            {
                tool: row["detail"]
                for tool, row in self.results.items()
                if row["arm"] == "boundary-validation"
            },
            "a tool's own response model refused its payload at the boundary",
        )
        errors = {tool for tool, row in self.results.items() if row["arm"] == "error"}
        self.assertEqual(
            set(ENVELOPE_LOSING_RAISERS),
            errors,
            "tools that raise instead of answering (T34 pins exactly these)",
        )
        self.assertEqual(
            set(UNMARKED_NOT_OK),
            {tool for tool, row in self.results.items() if row["arm"] == "bare-not-ok"},
            "a payload reporting ok:false with no refuse code and no next action appeared or "
            "disappeared: update UNMARKED_NOT_OK in the same change",
        )
        self.assertEqual(
            len(PUBLIC_TOOLS), len(self.results), "the sweep did not invoke every public tool"
        )

    def test_the_pinned_raisers_are_exactly_the_ones_that_lose_the_envelope(self) -> None:
        """The pin is empty because `T34` is repaired -- and the pin still guards the surface.

        Deleting the constant when the repair landed would have deleted the only place that
        records what the sweep may observe. Keeping it empty keeps the guard: this case asserts
        the pin equals the observed error arm in BOTH directions, so a reintroduced raiser fails
        here by name, and an entry added to the pin without a raiser fails here too.
        """

        observed = {tool for tool, row in self.results.items() if row["arm"] == "error"}
        self.assertEqual(
            set(ENVELOPE_LOSING_RAISERS),
            observed,
            "a raiser appeared or disappeared: update ENVELOPE_LOSING_RAISERS in the same "
            "change that repairs or introduces it (T34, owner L6)",
        )
        self.assertEqual(
            set(),
            observed,
            "an envelope-losing raiser is on the swept surface again; T34's repair (L6) has "
            "regressed, or a new tool arrived without a refusal envelope",
        )

    def test_the_t34_family_answers_with_a_named_refusal_instead_of_raising(self) -> None:
        """`T34`'s positive control: the nine repaired tools answer, and the answer is typed.

        Before the repair each of these nine raised `ToolError` with ``envelope_keys=[]``, so
        the caller lost ``ok``/``status``/``nextAction``. This case is the durable form of that
        finding: every one of the nine is still advertised, still answers, and the answer names
        **what refused** (a refusal identity), **why** (a non-empty detail) and **the next
        action**.

        Its control is not a comment. The same assertions are run against an `ok: false` payload
        with the refusal keys stripped, and that control must fail them -- so the case proves it
        can see a refusal that stopped naming anything rather than describing one.
        """

        def assert_named_refusal(tool: str, payload: dict[str, Any]) -> None:
            self.assertEqual(False, payload.get("ok"), f"{tool} did not refuse")
            identity = refusal_marks(payload)
            self.assertTrue(
                [key for key in REFUSAL_IDENTITY_KEYS if key in identity],
                f"{tool} refused without a refusal identity: {sorted(payload)}",
            )
            self.assertTrue(
                str(payload.get("detail") or "").strip(),
                f"{tool} refused without saying why (no detail)",
            )
            self.assertTrue(
                [key for key in NAVIGATION_KEYS if key in identity],
                f"{tool} refused without a machine-readable next action: {sorted(payload)}",
            )

        missing = T34_REPAIRED_TOOLS - set(PUBLIC_TOOLS)
        self.assertEqual(set(), missing, "a repaired T34 tool is no longer advertised")
        self.assertEqual(
            set(),
            T34_REPAIRED_TOOLS - set(self.results),
            "the sweep did not invoke a repaired T34 tool",
        )
        for tool in sorted(T34_REPAIRED_TOOLS):
            row = self.results[tool]
            self.assertNotEqual("error", row["arm"], f"{tool} lost the envelope again")
            assert_named_refusal(tool, row["payload"])

        # The control: the same predicate, on the shape a bare raise would have produced.
        with self.assertRaises(AssertionError):
            assert_named_refusal("grepai_search", {"ok": False, "operation": "grepai_search"})

    def test_the_state_dependent_raisers_lose_the_envelope_when_unprepared(self) -> None:
        """The second pin is measured, not declared: the four-state matrix is re-derived."""

        allowed = set(LIFECYCLE_STATE_POPULATION)
        self.assertEqual(
            EXPECTED_LIFECYCLE_STATE_POPULATION,
            allowed,
            "the derivation rule no longer produces the population it is meant to: a weakened "
            "rule would otherwise stop measuring the members that do not raise",
        )
        self.assertLessEqual(
            set(STATE_DEPENDENT_RAISERS), allowed, "a pinned tool outside the measured population"
        )
        self.assertLess(
            len(STATE_DEPENDENT_RAISERS),
            len(allowed),
            "the population collapsed onto the pin, so the matrix measures nothing the pin does "
            "not already assert",
        )
        observed = self.world.matrix()
        pinned = {tool: list(states) for tool, states in STATE_DEPENDENT_RAISERS.items()}
        self.assertEqual(
            pinned,
            observed,
            "the lifecycle state matrix moved: update STATE_DEPENDENT_RAISERS in the same "
            "change (T34's family, owner L6)",
        )
        self.assertTrue(
            any(states for states in observed.values()),
            "a matrix with no state-dependent raiser would make this pin vacuous",
        )

    def test_every_refusal_carries_a_machine_readable_identity(self) -> None:
        """Marks are read from the PAYLOAD, so a mislabelled arm cannot satisfy this case."""

        unmarked: dict[str, str] = {}
        marked = 0
        for tool, row in sorted(self.results.items()):
            payload = row["payload"]
            if not isinstance(payload, dict) or payload.get("ok") is not False:
                continue
            marks = refusal_marks(payload)
            if marks:
                marked += 1
                continue
            unmarked[tool] = f"ok:false with none of {(*REFUSAL_IDENTITY_KEYS, *NAVIGATION_KEYS)}"
        self.assertTrue(marked, "a sweep with no marked refusal proves nothing")
        self.assertEqual(
            set(UNMARKED_NOT_OK),
            set(unmarked),
            "a refusal lost or gained its machine-readable identity: update UNMARKED_NOT_OK in "
            "the same change that causes it",
        )

    def test_the_resume_case_asserts_the_transition_it_arranged(self) -> None:
        """``prepare`` claims the state it arranged; this asserts it, and the move after."""

        resume = self.results["lifecycle_resume"]
        self.assertEqual(
            "blocked",
            resume["prepared_state"],
            "the resume case did not arrange the state lifecycle_resume documents",
        )
        self.assertEqual(
            "running",
            resume["state_after"],
            "lifecycle_resume did not leave the ambient in the state it reports",
        )

    def test_the_sweep_reads_the_worktree_instead_of_performing_the_operations(self) -> None:
        """The bound is a boundary, not "wrote nothing" — and here is exactly what it covers.

        Asserted, per repository: HEAD, the Git status, and every file OUTSIDE ``.git`` by
        digest. ``.git`` is excluded and deliberately so: opening a linked worktree rewrites its
        administrative files (measured, the memory worktree's ``.git/worktrees/…/index``), which
        is Git doing its job, not the product writing where it should not. Asserted for the
        coordination root: nothing is removed, and nothing is added or rewritten outside the
        declared write zones. Not asserted: anything inside ``.git``.
        """

        self._assert_repositories_untouched()
        self._assert_coordination_root_respected()

    def _assert_repositories_untouched(self) -> None:

        self.assertTrue(self.world.contract.exists(), "the enclosure contract is gone")
        self.assertEqual(
            self.before_contract,
            self.world.contract.read_bytes(),
            "the sweep rewrote the enclosure contract",
        )
        self.assertEqual(
            self.before_worktrees,
            _git(self.world.code, "worktree", "list"),
            "the sweep moved a worktree",
        )
        self.assertEqual(
            "not-started",
            self._closeout_status(),
            "the sweep started the closeout it is only supposed to probe",
        )
        after_code = self.world.repository_state(self.world.code)
        self.assertEqual(self.before_code["head"], after_code["head"], "the code HEAD moved")
        self.assertEqual(self.before_code["status"], after_code["status"], "the code repo is dirty")
        self.assertEqual(
            self.before_code["census"],
            after_code["census"],
            "the sweep wrote to, rewrote or deleted a file in the code repository",
        )
        after_memory = self.world.repository_state(self.world.memory)
        self.assertEqual(self.before_memory["head"], after_memory["head"], "the memory HEAD moved")
        added = set(after_memory["census"]) - set(self.before_memory["census"])
        removed = set(self.before_memory["census"]) - set(after_memory["census"])
        rewritten = {
            path
            for path in set(self.before_memory["census"]) & set(after_memory["census"])
            if self.before_memory["census"][path] != after_memory["census"][path]
        }
        self.assertEqual(set(), removed, "the sweep deleted a file from the memory repository")
        self.assertEqual(set(), rewritten, "the sweep rewrote a file in the memory repository")
        self.assertEqual(
            set(KNOWN_MEMORY_SCAFFOLD_ADDITIONS),
            added,
            "the memory repository gained a file the sweep is not declared to add",
        )
        # The memory status is compared too, in the one way the declared additions allow: every
        # TRACKED line must be identical, and the untracked lines must be exactly the additions.
        self.assertEqual(
            [
                line
                for line in self.before_memory["status"].splitlines()
                if not line.startswith("??")
            ],
            [line for line in after_memory["status"].splitlines() if not line.startswith("??")],
            "the sweep changed the memory repository's tracked status",
        )
        self.assertEqual(
            sorted(f"?? {path}" for path in KNOWN_MEMORY_SCAFFOLD_ADDITIONS),
            sorted(line for line in after_memory["status"].splitlines() if line.startswith("??")),
            "the memory repository reports untracked files the sweep is not declared to add",
        )

    def _assert_coordination_root_respected(self) -> None:
        """Nothing under the coordination root disappears, and nothing moves outside a zone."""

        boundary = self.world.coordination_boundary(
            self.before_coordination, self.world.coordination_census()
        )
        self.assertEqual(
            [], boundary["removed"], "the sweep deleted a file from its own coordination root"
        )
        self.assertEqual(
            [],
            boundary["added_outside_zones"],
            "the sweep added a file outside the declared coordination write zones",
        )
        self.assertEqual(
            [],
            boundary["rewritten_outside_zones"],
            "the sweep rewrote a file outside the declared coordination write zones",
        )

    def _closeout_status(self) -> str:
        lines = self.world.contract.read_text(encoding="utf-8").splitlines()
        start = lines.index("closeout:")
        for line in lines[start + 1 :]:
            if line.startswith("  status:"):
                return line.split(":", 1)[1].strip()
            if line and not line.startswith(" "):
                break
        return ""
