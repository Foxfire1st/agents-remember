"""The AR workflow as one ordered path through the tools, walked in a disposable world.

`260918-TSIP-L8`: the workflow statement this leaf owns. A trace that lives in the task root is
not a landing, so the ordered path is re-derived here, by a test run, at every revision:

* the operations run **in order** through the registered entry points -- authoring, `worktree_start`,
  the work, closeout preview and apply, integration preview and apply, cleanup preview, finalize --
  and reach the terminal `cleanup-completed` contract;
* every **guidance block** a response carries is checked against **the caller's own contract**,
  never against whatever lifecycle the process happens to hold (`T54`: in one round every response
  of a leaf named a concurrent session's enclosure);
* every **recommended preview step** is actually **executed** and required to answer (`T62`: the
  preview every closeout doctrine tells a seat to take first raised an internal `RuntimeError`
  whenever a drift snapshot existed for the task's branch).

**Why a walk and not a table.** The properties above are asserted over *populations derived at run
time*: the hops come from executing the chain, the guidance channels come from each response the
chain produced, and the arguments a recommendation supplies are read from the product's own
guidance state machine over the contract on disk. The only literal sequences here are (a)
`WORKFLOW_OPERATIONS`, which *is* the workflow statement this leaf owns and is asserted against
what the walk observed, and (b) value fixtures for arguments a recommendation leaves to the
caller. Nothing is keyed by tool name except the two pins, and each pin is asserted **equal in both
directions** to what the run derived.

**Every response's operational triple is called, not just the one the walk happens to follow**
(`260918-TSIP-L8` fix round 2). The first revision of this module measured `worktree_status`'s
recommendation through the phase state machine only, so the *same* operation being recommended
uncallably by the `worktree_start` response's own `nextTool`/`nextArgs` was invisible to it: the
mutation that repaired that channel left the module green. A recommendation channel a case does not
call is a channel the case cannot see, so the walk now probes **both** producers of the same
recommendation -- each hop's own triple, and the `worktree_start` retry-provider-setup response,
driven in its own disposable world because the start guard needs the session's lifecycle handed back
first -- and the callability pin is asserted over the union.

**Two guidance channels, two authorities** (measured, and the reason the address check has two
arms). A worktree response can carry:

* the **top-level triple** ``nextAction``/``nextTool``/``nextArgs``, declared on
  ``models/worktree.py::WorktreeCommandResponse`` and produced from the response's **own**
  contract by ``worktrees/modules/guidance.py``. It is the *operational* channel: it names the next
  operation of this task, and being producer-derived it cannot name another task.
* ``nextStep``, the *lifecycle* overlay computed at the choke point from the **process-global**
  ambient lifecycle (``application/next_step.py::next_step_for`` reads ``LifecycleState.enclosure``)
  and then filtered by ``application/tool_response.py::bound_next_step``, which withholds any hint
  that does not name the response's own place. It is what carries the turn-end/notify moments.

So guidance is authoritative for navigation only where the address binding holds, and the two
channels must be read as two: a case that inspects only ``nextStep`` reports "withheld" where the
seat in fact received the same operational move top-level.

The module is hermetic: one `TemporaryDirectory`, two real Git repositories, real task documents,
a real server built by ``create_server`` and a real in-memory MCP client session per call. No
Docker, no network, no writes outside the temporary tree.
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.worktrees.modules.guidance import lifecycle_guidance
from workflow_chain_guidance_test_support import (
    EXPECTED_PHASES,
    EXPECTED_STATES,
    GUIDANCE_STEPS_MISSING_REQUIRED_ARGS,
    STALE_BASE_RAISERS,
    WITHHELD_GUIDANCE_HOPS,
    WORKFLOW_OPERATIONS,
    WorkflowChainObservation,
    _names_same_place,
    classify_call,
    missing_required_args,
)


class WorkflowChainTests(unittest.TestCase):
    """The ordered chain, its guidance, and its previews, over one shared disposable world."""

    observation: WorkflowChainObservation

    @classmethod
    def setUpClass(cls) -> None:
        cls.observation = WorkflowChainObservation()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.observation.close()

    # -- 1. the order -------------------------------------------------------------------------

    def test_the_chain_runs_in_the_declared_order_through_the_registered_entry_points(self) -> None:
        """The workflow statement, asserted against what the run actually did.

        The population is the walk's own hops, so an operation that silently stops happening
        shortens this tuple and fails here rather than passing quietly.
        """

        observed = tuple(hop.tool for hop in self.observation.hops)
        self.assertEqual(observed, WORKFLOW_OPERATIONS)
        for hop in self.observation.hops:
            self.assertIn(hop.tool, PUBLIC_TOOLS, f"{hop.tool} is not on the advertised roster")
            self.assertEqual(
                classify_call(hop.tool, hop.kind, hop.payload),
                "answered",
                f"{hop.tool} lost its envelope: {str(hop.payload.get('detail'))[:200]}",
            )

    def test_the_chain_reaches_every_declared_state_and_the_terminal_contract(self) -> None:
        """Each operation's own vocabulary, in order, and the terminal cleanup cell."""

        seen = [(hop.tool, hop.state) for hop in self.observation.hops]
        for tool, state in EXPECTED_STATES:
            self.assertIn((tool, state), seen, f"{tool} never reached {state!r}: {seen}")
        self.assertEqual(self.observation.terminal_cleanup, "completed")
        # The phase progression comes from the product's state machine over the real contract file,
        # never from a literal in this module.
        phases = [hop.phase for hop in self.observation.hops]
        for phase in EXPECTED_PHASES:
            self.assertIn(phase, phases, f"the walk never reported phase {phase!r}: {phases}")
        self.assertEqual(phases[0], "worktree-started", phases)
        self.assertEqual(phases[-1], "cleanup-completed", phases)

    def test_each_hop_was_the_move_the_previous_response_named(self) -> None:
        """Order derived from the guidance, not merely asserted beside it.

        Where a response carries an operational `nextTool` that is a forward move of the chain, the
        next hop taken must be exactly that tool. This is what makes the statement a *chain*: the
        product's own guidance, not this module's literal, decides what comes next.
        """

        hops = self.observation.hops
        forward = 0
        holds = 0
        for index, previous in enumerate(hops):
            named = previous.top_next_tool
            if named is None:
                continue
            if named == previous.tool:
                # The phase has not advanced, so the guidance points at the same operation again
                # ("keep working here"). That is a hold, not a move, and it is counted as one.
                holds += 1
                continue
            following = hops[index + 1] if index + 1 < len(hops) else None
            if following is not None and following.tool == named:
                forward += 1
                continue
            # A named move may be preceded by a step the workflow itself interleaves (the cleanup
            # preview between integration and finalize), but it may never be skipped or replaced.
            self.assertIn(
                named,
                [hop.tool for hop in hops[index + 1 :]],
                f"{previous.tool} named {named}, and the walk never took it",
            )
        self.assertGreaterEqual(
            forward, 4, "the operational channel named the following hop too rarely to be a chain"
        )
        self.assertGreaterEqual(holds, 1, "no phase ever held the walk at one operation")

    # -- 2. the guidance resolves to the caller's own task ------------------------------------

    def test_every_guidance_channel_names_the_callers_own_task(self) -> None:
        """`T54`: guidance must name the response's own place, on every channel it uses."""

        own = self.observation.world.contract.resolve()
        checked = 0
        for hop in self.observation.hops:
            for channel, args in hop.channels:
                for key in ("contract_path", "enclosure_path", "contractPath", "enclosurePath"):
                    path = args.get(key)
                    if not path:
                        continue
                    checked += 1
                    self.assertTrue(
                        _names_same_place(str(path), own),
                        f"{hop.tool}'s {channel} guidance named {path}, not this task's {own}",
                    )
        self.assertGreaterEqual(
            checked, len(self.observation.hops), "no guidance channel carried a path at all"
        )

    def test_a_concurrent_siblings_guidance_never_reaches_this_task(self) -> None:
        """A sibling's addressed contract yields its own hint and cannot redirect this response.

        First prove the sibling contract produced guidance, then check every channel of this
        task's response stays bound to its own enclosure and next move.
        """

        hop = self.observation.sibling_hop
        assert hop is not None
        sibling = self.observation.sibling_contract
        assert sibling is not None

        self.assertIsNotNone(hop.raw_next, "the addressed contract produced no landing hint")
        for channel, args in hop.channels:
            for key in ("contract_path", "enclosure_path", "contractPath", "enclosurePath"):
                path = args.get(key)
                if path:
                    self.assertFalse(
                        _names_same_place(str(path), sibling),
                        f"{hop.tool}'s {channel} guidance named the sibling enclosure {path}",
                    )
        if hop.top_next_tool is not None:
            self.assertIn(hop.top_next_tool, PUBLIC_TOOLS)

    # -- 3. the recommended steps are usable --------------------------------------------------

    def test_every_recommended_step_is_callable_with_the_args_its_guidance_supplies(self) -> None:
        """The derived population of recommendations, with `T60`/`T97`'s lesson applied to it.

        Each hop was first called with exactly what its own guidance supplied (plus the arguments
        that guidance declares as required), and **every response's own operational triple was then
        called verbatim** (`WorkflowChainObservation._probe_operational_triples`). Both are
        recommendations a seat could follow, so the pin is asserted EQUAL to the union: a
        recommendation that stops being callable on *either* channel is a failure, and the day one
        is deliberately tolerated, its entry has to be named in the same change.
        """

        derived = self.observation.callability_population()
        self.assertEqual(
            derived,
            GUIDANCE_STEPS_MISSING_REQUIRED_ARGS,
            "a recommended step's guidance no longer supplies arguments its tool requires, on the "
            "channel the walk followed or on a response's own operational triple; repair the "
            "guidance, or name the entry in GUIDANCE_STEPS_MISSING_REQUIRED_ARGS in the same "
            "change that introduces it",
        )

    def test_every_response_that_published_an_operational_triple_was_called_with_it(self) -> None:
        """The population of the case above, asserted so that it cannot silently shrink.

        A pin is only as wide as the population it is asserted over, so this case states the
        population: every hop carrying a `nextTool`/`nextArgs` triple has exactly one probe, each
        probe called the tool and the arguments its own response published, and the second producer
        of the same recommendation (`worktree_start`'s retry-provider-setup answer) was driven too
        rather than cited. An arm that stops probing a channel reds here.
        """

        probes = self.observation.operational_triples
        expected = {
            f"hop {index} {hop.tool}"
            for index, hop in enumerate(self.observation.hops)
            if hop.top_next_tool is not None
        }
        observed = {call.source for call in probes if call.source.startswith("hop ")}
        self.assertEqual(observed, expected, "a hop's operational triple was never called")
        self.assertIn(
            "worktree_start retry-provider-setup",
            {call.source for call in probes},
            "the retry-provider-setup response's own triple was not driven",
        )
        self.assertEqual(
            self.observation.retry_provider_setup_state,
            "blocked",
            "the retry-provider-setup response did not reach the arm that recommends worktree_status",
        )
        # Every probe must have called exactly what its response published: same shape as the
        # walk's own as-given call, one per triple, in walk order.
        by_source = {call.source: call for call in probes}
        for index, hop in enumerate(self.observation.hops):
            tool = hop.top_next_tool
            if tool is None:
                continue
            call = by_source[f"hop {index} {hop.tool}"]
            self.assertEqual(call.tool, tool)
            self.assertEqual(call.args, hop.top_next_args)
            # A probe that lost its envelope would classify as `raised`; that is a different
            # finding from an unusable recommendation, and neither belongs here.
            self.assertNotEqual(
                call.arm, "raised", f"{call.source} lost its envelope when called as published"
            )
        self.assertNotEqual(by_source["worktree_start retry-provider-setup"].arm, "raised")
        # And the negative fact, stated rather than left implicit: the operations that publish no
        # operational triple at all are the two terminal `lifecycle_finalize_task` calls, which are
        # exactly the ones the lifecycle overlay is authoritative for.
        self.assertEqual(
            {hop.tool for hop in self.observation.hops if hop.top_next_tool is None},
            {"lifecycle_finalize_task"},
        )

    def test_the_callability_arm_can_actually_fail(self) -> None:
        """The control for the case above: the classifier must see an unusable recommendation.

        `worktree_closeout_apply` requires `contract_path` and `intent_note`; called with neither,
        its own argument model must refuse, and the refusal must be attributed to that model -- not
        to the tool, and not to a generic failure. Without this, an "answered" classification could
        be satisfied by a classifier that never refuses anything.
        """

        kind, payload = self.observation.world.call("worktree_closeout_apply", {})
        self.assertEqual(
            classify_call("worktree_closeout_apply", kind, payload), "argument-refused"
        )
        self.assertEqual(
            missing_required_args(kind, payload), frozenset({"contract_path", "intent_note"})
        )
        # The same arm one field short: the recorded `intent_note` shape, and the PLURAL/ singular
        # message forms must classify alike or the population above would miss exactly the case it
        # was written for.
        partial = {"contract_path": self.observation.world.contract.as_posix()}
        kind, payload = self.observation.world.call("worktree_closeout_apply", partial)
        self.assertEqual(
            classify_call("worktree_closeout_apply", kind, payload), "argument-refused"
        )
        self.assertEqual(missing_required_args(kind, payload), frozenset({"intent_note"}))
        # And the other arm is separated by model identity: a call that loses the envelope
        # entirely is not an unusable recommendation.
        self.assertEqual(
            classify_call("worktree_closeout_apply", "RAISED ToolError", {"detail": "boom"}),
            "raised",
        )

    def test_both_previews_keep_their_argument_addressed_approval_stop(self) -> None:
        previews = [
            hop
            for hop in self.observation.hops
            if hop.tool == "worktree_closeout_preview"
            or (hop.tool == "worktree_integrate" and hop.state == "would-integrate")
        ]
        self.assertEqual(len(previews), 2)
        for hop in previews:
            with self.subTest(tool=hop.tool):
                self.assertIsNotNone(hop.emitted_next)
                assert hop.emitted_next is not None
                self.assertIsNone(hop.emitted_next.nextArgs)
                self.assertIn("stop", hop.emitted_next.summary.lower())

    def test_the_guidance_that_could_not_be_validated_is_withheld_and_counted(self) -> None:
        """`T54`/`T82`: the withhold is real, counted, and explained in both directions.

        A response that declares no camelCase address cannot vouch for guidance derived from the
        process-global lifecycle, so `bound_next_step` withholds it. That is pinned by tool and
        count so it cannot grow unnoticed, and so repairing the producers is a visible edit here
        rather than a silent improvement. Asserted EQUAL in both directions.
        """

        counts: dict[str, int] = {}
        for hop in self.observation.hops:
            if hop.withheld:
                counts[hop.tool] = counts.get(hop.tool, 0) + 1
        self.assertEqual(counts, WITHHELD_GUIDANCE_HOPS)
        # Not vacuous: at least one withheld hop must still publish the same move top-level, which
        # is why the withhold costs this chain nothing today.
        carried = [
            hop.tool
            for hop in self.observation.hops
            if hop.withheld and hop.top_next_tool is not None
        ]
        self.assertTrue(carried, "every withheld hop also lost the top-level operational channel")

    # -- 4. the previews are usable -----------------------------------------------------------

    def test_every_preview_step_answered_and_the_drift_snapshot_was_actually_read(self) -> None:
        """`T62`'s trigger, inside the workflow, on this base.

        The drift snapshot is planted at the producer's own path before the cleanup and finalize
        previews, and the case asserts it was present -- otherwise a green would only mean the
        previews never met the shape that used to crash them. Every preview must have answered, and
        the snapshot must survive a preview untouched.
        """

        assert self.observation.drift_snapshot is not None
        self.assertTrue(
            self.observation.drift_snapshot_present_at_preview,
            "the drift snapshot was not present when the previews ran; the arm tested nothing",
        )
        previews = [
            hop
            for hop in self.observation.hops
            if hop.tool.endswith("_preview")
            or (hop.tool == "worktree_cleanup" and hop.as_given_args.get("dry_run"))
            or (
                hop.tool in ("lifecycle_finalize_task", "worktree_integrate")
                and hop.as_given_args.get("dry_run")
            )
        ]
        self.assertGreaterEqual(len(previews), 4, [hop.tool for hop in previews])
        for hop in previews:
            self.assertEqual(classify_call(hop.tool, hop.kind, hop.payload), "answered", hop.tool)
            self.assertTrue(
                str(hop.state or "").startswith("would-") or hop.state == "closed",
                f"{hop.tool} preview answered {hop.state!r}",
            )
        self.assertTrue(
            self.observation.drift_snapshot_preserved_by_previews,
            "a preview removed the drift snapshot it only planned to remove",
        )
        # And the real finalize did reclaim it, so the preservation above is not an artefact of
        # nothing ever touching the file.
        self.assertFalse(
            self.observation.drift_snapshot.exists(),
            "the real finalize left the drift snapshot behind",
        )

    def test_a_stale_base_closes_the_envelope_by_raising_and_is_counted(self) -> None:
        """`T87` measured, and `T34`'s class found on it.

        The register's order -- sync the leaf onto the current tip, then close out -- is asserted by
        driving the state rather than describing it: the master's source branch advances after the
        leaf started, `worktree_sync` reports `would-sync`, and the closing operations are called in
        exactly that state. The preview's refusal escapes as a raise, so the caller loses `ok`,
        `status` and the machine-readable remedy (`worktree_sync` appears only in the prose). The
        pin is asserted EQUAL in both directions: repair it and the entry goes; a second raiser is a
        failure.
        """

        calls = self.observation.stale_base_calls
        self.assertEqual(
            [hop.tool for hop in calls], ["worktree_closeout_preview", "worktree_sync"]
        )
        self.assertEqual(
            self.observation.stale_sync_state,
            "would-sync",
            "the arrangement did not leave the leaf behind its source branch; the arm is vacuous",
        )
        raisers = frozenset(
            hop.tool
            for hop in calls
            if classify_call(hop.tool, hop.kind, hop.payload) != "answered"
        )
        self.assertEqual(raisers, STALE_BASE_RAISERS)
        detail = str(calls[0].payload.get("detail", ""))
        self.assertIn("source-lineage-stale", detail)
        self.assertIn("worktree_sync", detail)

    # -- 5. the declared-requirement channel --------------------------------------------------

    def test_the_declared_requirement_channel_is_real_and_its_one_needed_case_is_unreachable(
        self,
    ) -> None:
        """`T109`: the overlay can declare what it leaves to the caller, and where that channel is.

        `NextStep.nextRequiredArgs` is the channel a recommendation uses to say "this call needs
        this argument", and `worktree_closeout_apply`'s `intent_note` is exactly the requirement the
        *operational* triple cannot carry. Three measured facts, in order:

        1. where the channel is reachable, the product uses it -- `cleanup-pending` declares
           `contract_path` for `lifecycle_finalize_task`, read from the contract on disk at the one
           walk moment that phase is live;
        2. the phase that declares `intent_note` (`closeout-pending`) is **unreachable from the
           tools' own writers**: the real apply leaves `approved_for_commit` and
           `closeout_status: completed` in the *same* contract write
           (`worktrees/modules/closeout.py::_amended_closeout_contract`), and the phase machine
           tests `closeout_status == "completed"` first, so no tool-sequenced state ever reads it;
        3. the declaration is nonetheless real, and deleting it would be invisible unless it is
           asserted where it is defined -- the contract state the phase is defined over is built
           here from the *real* post-apply contract with only that cell rewritten, which is the
           phase's own input and the reason `T109` is a finding rather than a repair.

        The declared requirement being absent from the operational triple is
        `GUIDANCE_STEPS_MISSING_REQUIRED_ARGS`'s remaining entry: the two are the same defect seen
        from its two sides.

        **Population, stated rather than implied.** The phase machine declares requirements in four
        places (`guidance.py`: `carryover-pending`, `cleanup-pending`, the published-authority
        `integration-pending`, and `closeout-pending`). This case pins two of them -- the one the
        walk reaches (`cleanup-pending`) and the one it cannot (`closeout-pending`). The other two
        are **not** asserted here: `carryover-pending` needs an integration that landed without the
        exact memory mapping, and the published-authority `integration-pending` needs a published
        curator-coherence digest, neither of which this fixture builds. Deleting either of those two
        declarations leaves this module green, which is a stated limit of this instrument rather
        than a claim it makes (`notes/reports/tsip-instruments/l8-fix2-03-mutations.py`, the `A5c`
        mutation, records the measurement).
        """

        # 1. reachable, and used: the phase declares `contract_path`, and the very next hop of the
        #    walk is the operation that phase named, carrying the declared requirement.
        hops = self.observation.hops
        cleanup_pending = self.observation.cleanup_pending_guidance
        self.assertEqual(cleanup_pending.get("phase"), "cleanup-pending", cleanup_pending)
        self.assertEqual(cleanup_pending.get("nextTool"), "lifecycle_finalize_task")
        self.assertEqual(cleanup_pending.get("nextRequiredArgs"), ["contract_path"])
        finalize = next(hop for hop in hops if hop.tool == "lifecycle_finalize_task")
        for name in cleanup_pending["nextRequiredArgs"]:
            self.assertIn(
                name, finalize.as_given_args, f"the walk's finalize hop ignored the declared {name}"
            )

        # 2. the declaring phase's input is never produced by a tool writer.
        after_closeout = self.observation.contract_after_closeout
        assert after_closeout is not None
        self.assertTrue(after_closeout.approved_for_commit)
        self.assertEqual(after_closeout.closeout_status, "completed")
        self.assertEqual(lifecycle_guidance(after_closeout).get("phase"), "integration-pending")

        # 3. the declaration, asserted on the state that defines it.
        declaring = replace(after_closeout, closeout_status="not-started")
        declaring_guidance = lifecycle_guidance(declaring)
        self.assertEqual(declaring_guidance.get("phase"), "closeout-pending", declaring_guidance)
        self.assertEqual(declaring_guidance.get("nextTool"), "worktree_closeout_apply")
        self.assertEqual(declaring_guidance.get("nextRequiredArgs"), ["intent_note"])
        self.assertNotIn("intent_note", declaring_guidance.get("nextArgs") or {})
        # The walk never reached that phase, and the operational triple that recommends the same
        # tool is the one the pin already carries -- the two are the same defect from its two sides.
        self.assertEqual(
            [hop.tool for hop in hops if hop.phase == "closeout-pending"],
            [],
            "a hop followed the closeout-pending phase; T109's unreachability claim is now wrong",
        )
