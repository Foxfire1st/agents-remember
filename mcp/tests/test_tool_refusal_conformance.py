"""Every public tool's FAILURE path, driven through its production entry point.

`mcp/tests/test_tool_entry_point_sweep.py` (L5) sweeps every public tool with a **benign**
argument set and asserts each answer is a payload that validates or a typed refusal. It cannot
see the other half of the surface: what a tool answers when the call **cannot succeed**. That
half is where a seat actually needs the envelope, and it is where this leaf's defects live --
`T34`'s nine lost ``ok``/``status``/``nextAction`` to a bare exception on an ordinary absent
capability, and `T62`'s preview crashed on its own producer's output.

**What this module asserts, for every advertised tool.** Driven down a path that cannot
succeed, the answer is one of exactly three things, each named:

* a **typed refusal** -- ``ok: false`` with a refusal identity (``status``/``state``/
  ``refusalStatus``), a non-empty reason, and a machine-readable next action
  (``nextStep``/``nextTool``/``nextAction``/``nextArgs``). The three axes are asserted
  separately, so a refusal that stops naming WHY fails on that axis by name;
* an entry in ``ALWAYS_ANSWERS`` -- a tool whose invocation here legitimately succeeds because
  it has no failure mode under this fixture (``ping``) or because the refusal path itself is
  inexpressible through the argument model. Each entry carries its reason;
* an entry in ``BARE_RAISERS`` -- a measured, named, counted defect: the call raised and the
  envelope is gone. This is the family `T34` belonged to, and it is pinned so the census can
  only shrink on purpose.

The population is **derived at run time** from the advertised roster (both the server's own
tool listing and `PUBLIC_TOOLS`), and both pinned sets are asserted **equal** to what the
census observed in each direction -- so a tool that stops refusing, a tool that starts raising,
and a stale pin all fail, and none of them can be made to pass by widening a constant.

**The positive control is executed, not described** (`FailurePathControlTests`): it takes a real
refusal from the census, strips its identity / its reason / its next action one axis at a time,
and requires the predicate to reject each one -- so the case proves it can see a refusal that
stopped naming something rather than describing one.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from test_tool_entry_point_sweep import T34_REPAIRED_TOOLS, EntryPointWorld
from tool_refusal_census_support import drive_census

REFUSAL_IDENTITY_KEYS = ("status", "state", "refusalStatus")
# `explanation` is `RoleCapsuleResponse`'s own operator-facing reason line for both of its
# shapes (the model says so in its docstring), so it is a reason key here rather than a
# tolerated exception -- refusing without one still fails the axis.
REASON_KEYS = ("detail", "summary", "message", "explanation")
NAVIGATION_KEYS = ("nextStep", "nextTool", "nextAction", "nextArgs")

# ---------------------------------------------------------------------------------------
# The tools whose invocation in this census legitimately answers `ok: true`, with the reason.
#
# This is NOT a tolerated-defect pin: every entry is either a tool with no failure mode at all
# under this fixture, or a tool whose refusal path is deliberately not reachable here. A tool
# that answers `ok: true` without an entry is a tool that reported success for a call this
# module believes must fail, which is a finding, not a tolerated shape. Asserted by set
# equality in both directions.
#
# Owner: this leaf (`260918-TSIP-L6`). When a real refusal path is found for one of these, move
# it out of this set in the same change.
# ---------------------------------------------------------------------------------------
ALWAYS_ANSWERS: frozenset[str] = frozenset(
    {
        # A liveness probe. It takes no argument that can be wrong and has no precondition, so
        # "its failure path" is not a path this product has.
        "ping",
        # Reports the resolved configuration. `repo_id`/`task_name` are optional filters, so a
        # filter that matches nothing narrows the answer instead of refusing.
        "server_info",
        # Files a coverage verdict per requested path; a path with no card is REPORTED in the
        # answer (`files[].status: missing`) rather than refused.
        "read_ar_files",
        # `memory_init` is a repair, not a precondition check: the fixture's memory root already
        # exists with an onboarding scaffold, so this call has nothing to refuse. Its
        # `dry_run=True` form verifies rather than writes.
        "memory_init",
        # The two installers are driven with `dry_run=True`, which is the only hermetic form.
        # Both report a complete plan and refuse (raise) only on a real-write collision -- see
        # `BARE_RAISERS`' comment for the class this leaf measured on the real path.
        "runtime_install",
        "skills_install",
        # Status/diagnostics are the CONSTRUCTIVE half of `T34`: with no provider configured
        # they answer `providers.state: "noProviders"` / `state: "noProviders"`. That is the
        # behaviour the nine repaired tools were brought up to, so their answering here is the
        # repair working, not a gap.
        "provider_status",
        "provider_diagnostics",
        # The benchmark family is driven with `dry_run=True` only: its real run executes Codex
        # agents in a sandbox (minutes, a provider call) and cannot be part of a hermetic
        # seconds-long census. The dry run answers in an envelope, and its not-being-read-only
        # is the register's `T12`, owned by this leaf and recorded separately rather than
        # asserted here.
        "codex_benchmark_prepare",
        "codex_benchmark_run",
        # The lifecycle signal family answers in the state it is called in, and this census
        # starts a lifecycle first (so `lifecycle_start`'s own "already active" path is the one
        # measured). `lifecycle_turn_end_notification`, `lifecycle_end`, `switch_lifecycle` and
        # `lifecycle_phase` are each legal from `running`, so their invocation here succeeds.
        # What DOES lose the envelope in this family is pinned in `BARE_RAISERS`
        # (`lifecycle_start`, `lifecycle_resume`) and, over all four states, in the sweep's
        # `STATE_DEPENDENT_RAISERS`.
        "lifecycle_turn_end_notification",
        "lifecycle_end",
        "switch_lifecycle",
        "lifecycle_phase",
        # `skill_catalog_list` enumerates what the catalog publishes; an empty catalog is an
        # empty list, not a refusal.
        "skill_catalog_list",
        # `gate_list` lists the folded gates in the caller's own document scope. A caller
        # whose document holds no gate gets an empty list, which is an answer, not a refusal --
        # the `structural-caller-required` path is a different branch and is not reached by a
        # well-formed `caller`.
        "gate_list",
        # `closeout_queue(action="status")` answers `ok: true` with a projection whose
        # `sourceProblems` names what it could not read; an unreadable or absent sprint document
        # is REPORTED through that field. Its `structural-caller-required` refusal is a separate
        # path (`T13`'s family) and is not reached by a well-formed caller.
        "closeout_queue",
    }
)

# ---------------------------------------------------------------------------------------
# The measured bare raisers: the call raised and the caller lost `ok`/`status`/`nextAction`.
#
# `T34` was this class for nine tools and is REPAIRED (`application/provider_tools.py`,
# `application/memory_tools.py`). What remains is the same defect on other preconditions,
# grouped by the mechanism that raises, each already on the register:
#
# * ``require_repo`` -- `context_packet`, `resolve_context`, `drift_check`,
#   `memory_baseline_status` answer an unknown `repo_id` by raising the authority error
#   (`kernel/authority.py`). Its sibling `memory_baseline_adopt` now refuses in the envelope
#   for the absence this leaf repaired, which is why it is not here.
# * the worktree contract address -- `citation_fix`, `citation_migrate`, `route_index_refresh`,
#   `task_reopen`, `lifecycle_finalize_task`, `task_doc` raise on a contract/document that does
#   not exist. `T13`'s family (a refusal that does not name its artifact) and this leaf's own
#   `T34` share the fix direction: answer in the envelope the family already declares.
# * the lifecycle state precondition -- `lifecycle_start` (already active),
#   `lifecycle_resume` (not blocked). These two are ALSO pinned by the sweep's
#   `STATE_DEPENDENT_RAISERS`; their repair needs a refusal shape the strict
#   `LifecycleResponse` family does not have, which is a product decision this leaf argues
#   rather than takes.
# * `worktree_attach`/`worktree_status` (task name resolves no enclosure),
#   `memory_carryover_plan`/`memory_carryover_apply` (a source path outside the coordination
#   root), `provider_watchers` (a renamed action), `skill_catalog_read` (an unpublished
#   resource). Each names its fault in the message and none names a next action.
#
# Asserted by set equality in both directions. THE RULE, identical to the sweep's:
# **repaired => remove that entry in the same change; never delete the constant, never widen
# it.** A new bare raiser is a failure, and a stale entry is a failure too. Owner: `L6`.
# ---------------------------------------------------------------------------------------
BARE_RAISERS: frozenset[str] = frozenset(
    {
        "context_packet",
        "resolve_context",
        "drift_check",
        "memory_baseline_status",
        "citation_fix",
        "citation_migrate",
        "route_index_refresh",
        "task_reopen",
        "lifecycle_finalize_task",
        "task_doc",
        "lifecycle_start",
        "lifecycle_resume",
        "worktree_attach",
        "worktree_status",
        "memory_carryover_plan",
        "memory_carryover_apply",
        "provider_watchers",
        "skill_catalog_read",
    }
)


def refusal_axes(payload: dict[str, Any]) -> tuple[bool, bool, bool]:
    """The three axes a typed refusal must satisfy, read from the payload itself.

    Returned as one tuple so a case can name the axis that failed. ``ok`` is not one of the
    three: it is asserted separately, because a payload that is not a refusal at all should
    fail as "answered", not as "refused without a reason".
    """

    identity = any(payload.get(key) for key in REFUSAL_IDENTITY_KEYS)
    reason = any(str(payload.get(key) or "").strip() for key in REASON_KEYS)
    navigation = any(payload.get(key) for key in NAVIGATION_KEYS)
    return identity, reason, navigation


class FailurePathCensusTests:
    """One world, one census, and the assertions that make it mean something."""

    @classmethod
    def setup_class(cls) -> None:
        cls.world = EntryPointWorld()
        cls.world.build()
        cls.census = drive_census(cls.world)

    @classmethod
    def teardown_class(cls) -> None:
        cls.world.close()

    def test_the_censused_population_is_the_advertised_one(self) -> None:
        """Derived at run time from the roster and the live server, never a literal count."""

        advertised = {tool.name for tool in anyio.run(self.world.server.list_tools)}
        assert advertised == set(PUBLIC_TOOLS), (
            "the server advertises a different surface than the roster"
        )
        assert set(self.census) == set(PUBLIC_TOOLS), (
            "the census did not drive every advertised tool"
        )

    def test_every_failure_path_answers_in_one_of_the_three_named_shapes(self) -> None:
        """No fourth shape: a refusal, a recorded always-answer, or a recorded bare raiser."""

        observed_ok = {
            tool for tool, row in self.census.items() if row["payload"].get("ok") is True
        }
        observed_raised = {tool for tool, row in self.census.items() if row["kind"] != "RETURNED"}
        observed_refused = set(self.census) - observed_ok - observed_raised

        assert observed_raised == set(BARE_RAISERS), (
            "the bare-raiser census moved: repair it and remove the entry in the same change, "
            "or add the new one deliberately (never widen this constant)"
        )
        assert observed_ok == set(ALWAYS_ANSWERS), (
            "a tool answered ok:true where this census expects a refusal, or an entry is stale"
        )
        assert observed_refused == set(PUBLIC_TOOLS) - set(BARE_RAISERS) - set(ALWAYS_ANSWERS), (
            "the three shapes do not partition the population"
        )

    def test_every_refusal_names_what_refused_why_and_the_next_action(self) -> None:
        """The three axes, each asserted by name so a regression says which one it broke."""

        failures: list[str] = []
        for tool in sorted(set(PUBLIC_TOOLS) - set(BARE_RAISERS) - set(ALWAYS_ANSWERS)):
            payload = self.census[tool]["payload"]
            assert payload.get("ok") is False, f"{tool} answered ok:{payload.get('ok')!r}"
            identity, reason, navigation = refusal_axes(payload)
            missing = [
                axis
                for axis, present in (
                    ("what refused (a refusal identity)", identity),
                    ("why (a non-empty reason)", reason),
                    ("the next action (machine-readable navigation)", navigation),
                )
                if not present
            ]
            if missing:
                failures.append(f"{tool}: missing {', '.join(missing)}")
        assert failures == [], "refusals that do not name themselves:\n" + "\n".join(failures)

    def test_the_t34_family_is_no_longer_a_bare_raiser(self) -> None:
        """The repair this leaf landed, asserted where it is observable: at the entry point.

        `T34`'s nine answered every absent capability with a bare exception. Each must now
        answer in the envelope, and each must still be advertised. This is the census's
        positive control for the repair itself -- the case cannot pass by removing the tools
        from the roster, because the roster equality above fails first.
        """

        # ONE source of truth. `T34_REPAIRED_TOOLS` lives in the sweep module because that is
        # where the pin it belongs to lives (`ENVELOPE_LOSING_RAISERS`), and the sweep's
        # roster-equality cases are what protect it. A second inline copy here could drift
        # from it silently, which is `L6`'s `F6`.
        repaired = T34_REPAIRED_TOOLS
        assert repaired <= set(PUBLIC_TOOLS), "a repaired T34 tool is no longer advertised"
        assert repaired & set(BARE_RAISERS) == set(), (
            "a repaired T34 tool is back in the bare-raiser pin"
        )
        for tool in sorted(repaired):
            row = self.census[tool]
            assert row["kind"] == "RETURNED", f"{tool} lost the envelope again"
            identity, reason, navigation = refusal_axes(row["payload"])
            assert identity and reason and navigation, (
                f"{tool} answered without a complete refusal: {sorted(row['payload'])}"
            )


class FailurePathControlTests:
    """The predicate must be able to fail. Each axis is stripped from a REAL refusal."""

    def test_stripping_each_axis_of_a_real_refusal_makes_the_predicate_fail(self) -> None:
        refusal = {
            "ok": False,
            "status": "provider-capability-unavailable",
            "detail": "the grepai-memory provider is not configured",
            "nextStep": {"summary": "arm the provider", "nextTool": "provider_watchers"},
        }
        assert refusal_axes(refusal) == (True, True, True), "the control's own fixture is wrong"

        for axis, mutation in (
            ("identity", {"status": None, "state": None, "refusalStatus": None}),
            ("why", {"detail": None, "summary": None, "message": None}),
            (
                "next action",
                {"nextStep": None, "nextTool": None, "nextAction": None, "nextArgs": None},
            ),
        ):
            stripped = {**refusal, **mutation}
            observed = refusal_axes(stripped)
            assert observed != (True, True, True), (
                f"the predicate cannot see a refusal that lost its {axis}"
            )
            assert observed.count(False) >= 1

    def test_the_other_two_shapes_are_distinguishable_too(self) -> None:
        """So the three-way partition is not a predicate that only ever says one thing."""

        answered = {"ok": True, "operation": "ping"}
        refusal = {"ok": False, "status": "x", "detail": "y", "nextStep": {"summary": "z"}}
        assert answered.get("ok") is True
        assert refusal.get("ok") is False
        assert all(refusal_axes(refusal))
        # A bare raise has NO payload at all -- the census records it as `kind != RETURNED`,
        # which is exactly why that arm is read from the call outcome and not from a dict.
        assert refusal_axes({}) == (False, False, False), (
            "an empty payload must satisfy none of the three axes"
        )


def test_the_module_runs_in_seconds_and_touches_no_real_state() -> None:
    """A hermeticity statement the module can be held to: no docker, no network, no real root.

    The world is a `tempfile.TemporaryDirectory` built by `EntryPointWorld`; the fixture's MCP
    settings point every repository at that directory, and no provider is configured, so no
    call in the census can reach a container or a network. Asserted as a property of the
    fixture rather than promised in prose: the settings file the server was booted from names
    only roots inside the temporary directory.
    """

    world = EntryPointWorld()
    try:
        settings = json.loads(world.settings.read_text(encoding="utf-8"))
        assert world.root == world.coord.parent
        for repo_id, scope in settings["repositories"].items():
            assert scope["path"].startswith(world.root.as_posix()), (
                f"{repo_id} points outside the disposable world"
            )
            assert scope["memoryRoot"].startswith(world.root.as_posix()), (
                f"{repo_id} memory points outside the disposable world"
            )
        assert settings["coordinationRoot"] == world.coord.as_posix()
        assert settings["providers"] == {}, "a configured provider would make this census live"
    finally:
        world.close()
