"""Every public tool's FAILURE path, driven through its production entry point.

`mcp/tests/test_tool_entry_point_sweep.py` (L5) sweeps every public tool with a **benign**
argument set and asserts each answer is a payload that validates or a typed refusal. It cannot
see the other half of the surface: what a tool answers when the call **cannot succeed**. That
half is where a seat actually needs the envelope, and it is where this leaf's defects live --
`T34`'s nine lost ``ok``/``status``/``nextAction`` to a bare exception on an ordinary absent
capability, and `T62`'s preview crashed on its own producer's output.

**What this module asserts, for every advertised tool.** Driven down a path that cannot
succeed, the answer is one of exactly four things, each named:

* a **typed refusal** -- ``ok: false`` with a refusal identity (``status``/``state``/
  ``refusalStatus``), a non-empty reason, and a machine-readable next action
  (``nextStep``/``nextTool``/``nextAction``/``nextArgs``). The three axes are asserted
  separately, so a refusal that stops naming WHY fails on that axis by name;
# * a **stateful refusal** -- ``ok: true`` with ``state: "refused"`` and the refusal carried in the
#   surface's own ``refusalCode``/``refusalDetail`` pair. This is the mounted ``knowledge_*``
#   surface, whose models declare it: *"A refusal is a state, not a partial success"*
#   (``models/tools/knowledge_responses.py``), and -- since MIK-R109 rule 1 -- ``read_ar_files``,
#   whose removed parameters and object-form entries refuse with ``argument-removed``. It is a
#   **fourth shape that had to be named**, not a
#   tolerated entry in ``ALWAYS_ANSWERS``: the merged line's five tools arrived after this census
  was written, and a tool that refuses is not a tool that succeeded. Pinned by name in
  ``STATEFUL_REFUSALS`` under the same both-directions rule as the other two, with its own case
  asserting the code and the detail, so the shape cannot become a place to hide a silent success;
* an entry in ``ALWAYS_ANSWERS`` -- a tool whose invocation here legitimately succeeds because
  it has no failure mode under this fixture (``server_info``) or because the refusal path itself is
  inexpressible through the argument model. Each entry carries its reason;
* an entry in ``BARE_RAISERS`` -- a measured, named, counted defect: the call raised and the
  envelope is gone. This is the family `T34` belonged to, and it is pinned so the census can
  only shrink on purpose.

The population is **derived at run time** from the advertised roster (both the server's own
tool listing and `PUBLIC_TOOLS`), and all three pinned sets are asserted **equal** to what the
census observed in each direction -- so a tool that stops refusing, a tool that starts raising,
and a stale pin all fail, and none of them can be made to pass by widening a constant.

**The positive control is executed, not described** (`FailurePathControlTests`): it takes a real
refusal from the census, strips its identity / its reason / its next action one axis at a time,
and requires the predicate to reject each one -- so the case proves it can see a refusal that
stopped naming something rather than describing one.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import anyio
import pytest
from agents_remember.application.worktree_tool_requests import TaskIdentity
from agents_remember.application.worktree_tools import worktree_start_tool
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.models.worktree import WorktreeStartResponse
from agents_remember.observer import (
    AmbientLifecycle,
    EventStore,
    install_ambient,
    observer_root,
)
from agents_remember.observer.ambient import reset_ambient
from pydantic import ValidationError
from test_tool_entry_point_sweep import T34_REPAIRED_TOOLS
from tool_entry_point_world import EntryPointWorld
from tool_refusal_census_support import drive_census

REFUSAL_IDENTITY_KEYS = ("status", "state", "refusalStatus")
# `explanation` is `RoleCapsuleResponse`'s own operator-facing reason line for both of its
# shapes (the model says so in its docstring), so it is a reason key here rather than a
# tolerated exception -- refusing without one still fails the axis.
REASON_KEYS = ("detail", "summary", "message", "explanation")
# MIK-R108: the lifecycle next-step hint rides the answer as ``next``
# ({summary, tool, args}) instead of ``nextStep``; it still counts as the
# machine-readable next action a refusal names.
NAVIGATION_KEYS = ("next", "nextStep", "nextTool", "nextAction", "nextArgs")

# ---------------------------------------------------------------------------------------
# The fourth shape: a refusal reported as a STATE of a successful call.
#
# The mounted `knowledge_*` surface declares this and only this. Its response models say so in
# their own words -- "A refusal is a state, not a partial success. `state` is `view`/`result` or
# `refused`, and the refusal fields name the offending input" -- and carry the refusal in a
# `refusalCode`/`refusalDetail` pair rather than in `ok:false` plus a top-level navigation key.
#
# It arrived with the merged `260915_knowledge-substrate` line, after this census was written,
# and it is neither of the two answers the census already had: the call did NOT raise, and it did
# NOT succeed. Putting these five in `ALWAYS_ANSWERS` would have been the widening this module's
# own docstring forbids ("a tool that reported success for a call this module believes must
# fail, which is a finding, not a tolerated shape"), so the shape is NAMED here instead, with
# the same both-directions set equality as every other arm, and its own case below asserts the
# code and the detail are present. Owner: `260918-TSIP-L10`, which reconciled the two lines at
# the merge; the shape itself is `models/tools/knowledge_responses.py`'s and is not changed here.
#
# THE RULE, identical to the other pins: a tool that starts answering this way is a failure
# unless it is added here deliberately, in the change that introduces it; an entry that stops
# answering this way is a failure too.
# ---------------------------------------------------------------------------------------
STATEFUL_REFUSALS: frozenset[str] = frozenset(
    {
        "knowledge_read",
        "knowledge_diff",
        "knowledge_integrity_check",
        # MIK-R109 rule 1: removed parameters and object-form file entries are
        # refused with `argument-removed` rather than read. The census drives
        # the object form, so that case is this tool's refusal proof.
        "read_ar_files",
    }
)

# The state value the shape is spelled with. Named rather than inlined so the two readers of the
# shape -- the partition below and the axis case -- cannot disagree about what they are reading.
STATEFUL_REFUSAL_STATE = "refused"

# The mounted knowledge tool whose description names WHERE knowledge is written. No mounted tool
# writes (`knowledge_change` left the registered set with the canonical database, MIK-R26), so
# the read's description carries the route. Both shipped CLI entry points are required by name,
# because one writer sits behind both: a caller told about only `knowledge-ingest` cannot reach
# the route that exists for a repository with no leaf, and a sentence naming one of two is the
# incomplete-about-the-store class rather than a stylistic slip (`D55`).
WRITE_ROUTE_NAMING_SURFACES: frozenset[str] = frozenset({"knowledge_read"})


def is_stateful_refusal(payload: dict[str, Any]) -> bool:
    """Whether one answer is a refusal carried as a state of a successful call."""

    return payload.get("ok") is True and payload.get("state") == STATEFUL_REFUSAL_STATE


def registered_response_model(tool: str) -> Any:
    """The registry entry for one tool, refusing BY NAME when a pin names an unregistered id.

    `NF4`: the stateful axis validated ``TOOL_RESPONSE_MODELS[tool]`` directly, so a pin naming a
    tool the registry does not carry surfaced as a bare ``KeyError: '<name>'`` -- an instrument
    fault wearing a crash's clothes, which is `T19`'s family one level down. The lookup now says
    what is wrong and what to do about it, and it is the only place this module indexes the
    registry, so a second caller cannot reintroduce the bare form.
    """

    try:
        return TOOL_RESPONSE_MODELS[tool]
    except KeyError:
        raise AssertionError(
            f"stateful pin names an unregistered tool: {tool!r} is in STATEFUL_REFUSALS but not "
            "in TOOL_RESPONSE_MODELS -- register it or remove it from the pin"
        ) from None


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
        # Reports the resolved configuration. `repo_id`/`task_name` are optional filters, so a
        # filter that matches nothing narrows the answer instead of refusing.
        "server_info",
        # `memory_init` is a repair, not a precondition check: the fixture's memory root already
        # exists with an onboarding scaffold, so this call has nothing to refuse. Its
        # `dry_run=True` form verifies rather than writes.
        "memory_init",
        # `runtime_install` is driven with `dry_run=True`, which is the only hermetic form.
        # It reports a complete plan and refuses (raises) only on a real-write collision.
        "runtime_install",
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
# removed by MIK-R112 with its tools,
# `application/memory_tools.py`). What remains is the same defect on other preconditions,
# grouped by the mechanism that raises, each already on the register:
#
# * ``require_repo`` -- `context_packet`,
#   `memory_baseline_status` answer an unknown `repo_id` by raising the authority error
#   (`kernel/authority.py`). Its sibling `memory_baseline_adopt` now refuses in the envelope
#   for the absence this leaf repaired, which is why it is not here.
# * the worktree contract address -- `citation_fix`, `route_index_refresh`,
#   `task_reopen`, `lifecycle_finalize_task`, `task_doc` raise on a contract/document that does
#   not exist. `T13`'s family (a refusal that does not name its artifact) and this leaf's own
#   `T34` share the fix direction: answer in the envelope the family already declares.
# * `worktree_attach`/`worktree_status` (task name resolves no enclosure),
#   `memory_carryover_plan`/`memory_carryover_apply` (a source path outside the coordination
#   root). Each names its fault in the message and none names a next action.
#
# MIK-R112 removed the tools that raised on other preconditions (`drift_check`,
# `citation_migrate`, `lifecycle_start`, `lifecycle_resume`, `provider_watchers`,
# `skill_catalog_read`); their entries leave with them in the same change.
#
# `worktree_start` was pinned here for L112-CODE-R1-F12 (C1) and left with the
# Parent-bound repair: the refusal no longer names a tool and validates, so the
# entry leaves in the same change per the rule above.
#
# Asserted by set equality in both directions. THE RULE, identical to the sweep's:
# **repaired => remove that entry in the same change; never delete the constant, never widen
# it.** A new bare raiser is a failure, and a stale entry is a failure too. Owner: `L6`.
# ---------------------------------------------------------------------------------------
BARE_RAISERS: frozenset[str] = frozenset(
    {
        "context_packet",
        "memory_baseline_status",
        "citation_fix",
        "route_index_refresh",
        "task_reopen",
        "lifecycle_finalize_task",
        "task_doc",
        "worktree_attach",
        "worktree_status",
        # MIK-R82's adoption tool: a contract outside the coordination root is
        # refused by name at the boundary, which raises (measured 2026-10-11).
        # Owned by the worktree-tool leaves (L111 reshapes these answers).
        "worktree_enclosure_adopt",
        "memory_carryover_plan",
        "memory_carryover_apply",
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


# The oracle's path rule (L108, T54, refined by Parent ruling 20ef3396): the
# path half means a response handing the caller an artifact that is THERE. A
# path the same response reports as missing/absent is the caller's argument
# given back -- no declaration, no navigation key, no tool; the producer's
# human next action stays as stated. Availability is decided from the
# response's own fields only: an explicit missing report in `observed.state`.
# No tool-name list, no filesystem guesses. A row whose fields cannot say
# (a declared path with no missing report and no navigation) fails LOUDLY by
# name -- the unknown returns to the Owner, never a silent pass.
_ORACLE_PATH_KEYS = (
    "contractPath",
    "contract_path",
    "enclosurePath",
    "enclosure_path",
    "memoryRoot",
    "memory_root",
    "docPath",
    "renderedPath",
)

# Explicit missing-report vocabulary in `observed.state` (case-insensitive).
_MISSING_STATES = ("missing", "absent")


def _declares_artifact_path(payload: dict[str, Any]) -> bool:
    """Whether one delivered answer declares an artifact path of its own place."""
    return any(
        isinstance(payload.get(key), str) and str(payload.get(key)).strip()
        for key in _ORACLE_PATH_KEYS
    )


def _addresses_a_tool(payload: dict[str, Any]) -> bool:
    """Whether one delivered answer machine-addresses a next tool.

    Only keys that name a tool (or its args) count; prose guidance (summary,
    detail, nextAction sentences, summary-only next hints) names no tool and
    never counts, per the F12 ruling that the guidance names no tool.
    """
    for key in ("nextTool", "nextArgs"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, dict) and len(value) > 0:
            return True
    next_hint = payload.get("next")
    if isinstance(next_hint, dict) and (next_hint.get("tool") or next_hint.get("args")):
        return True
    step = payload.get("nextStep")
    return isinstance(step, dict) and bool(step.get("nextTool") or step.get("nextArgs"))


def _reports_path_missing(payload: dict[str, Any]) -> bool:
    """Whether the response itself reports its path as missing/absent.

    Response-owned availability fact: only an explicit `observed.state` in the
    missing vocabulary counts. Anything else -- status strings, prose detail --
    does not decide; a declared path without this report is treated as an
    available artifact (and then must carry navigation, loudly if not).
    """
    observed = payload.get("observed")
    if isinstance(observed, dict):
        state = observed.get("state")
        if isinstance(state, str) and state.strip().lower() in _MISSING_STATES:
            return True
    return False


def _oracle_failure(tool: str, payload: dict[str, Any]) -> str | None:
    """The refined oracle verdict for one delivered refusal, or None when it holds.

    Available artifact (declared path, no missing report) requires a
    tool-addressing navigation key. Missing-path echo (declared path the
    response reports missing) carries no navigation key and no tool. No path
    declares none. Producers are never touched by this verdict.
    """
    declares = _declares_artifact_path(payload)
    addressed = _addresses_a_tool(payload)
    if declares and not _reports_path_missing(payload):
        if not addressed:
            return (
                f"{tool}: available artifact declared but no tool addressed "
                "(oracle rule: a navigation key where the response hands over "
                "an artifact that is there)"
            )
        return None
    if addressed:
        if declares:
            return (
                f"{tool}: missing-path echo addresses a tool "
                "(oracle rule: a path the response reports missing is no declaration)"
            )
        return (
            f"{tool}: no path declared but a tool is addressed "
            "(oracle rule: none where the response declares none)"
        )
    return None


class OracleRuleUnitTests:
    """Pure response-shape cases for the refined oracle (Parent ruling 20ef3396).

    No world, no census, no setup: each case feeds one plain payload dict to
    the exact oracle helpers. Running this class alone must never drive the
    39-row census (that drive belongs to FailurePathCensusTests.setup_class
    and to the separately admitted v3 diagnostic only).
    """

    def test_available_path_with_navigation_passes(self) -> None:
        """Parent case 1: a response handing over an artifact that is there carries navigation."""
        payload = {
            "ok": False,
            "state": "refused",
            "summary": "the enclosure is busy",
            "contractPath": "/coord/tasks/repo-a/m/enclosures/held/series-contract.md",
            "nextStep": {
                "summary": "Retry after the holder releases it.",
                "nextTool": "worktree_status",
            },
        }
        assert _declares_artifact_path(payload) is True
        assert _reports_path_missing(payload) is False
        assert _oracle_failure("case", payload) is None

    def test_missing_path_echo_without_navigation_passes(self) -> None:
        """Parent case 2: an echo the response reports missing carries no tool; prose stays."""
        payload = {
            "ok": False,
            "state": "refused",
            "summary": "the canonical enclosure locator is missing",
            "contractPath": "/coord/tasks/repo-a/m/enclosures/absent/series-contract.md",
            "observed": {"state": "missing"},
            "nextAction": "developer-decision",
        }
        assert _declares_artifact_path(payload) is True
        assert _reports_path_missing(payload) is True
        assert _oracle_failure("case", payload) is None

    def test_available_path_without_navigation_fails(self) -> None:
        """Parent case 3: a declared available artifact with no navigation fails loudly by name."""
        payload = {
            "ok": False,
            "state": "refused",
            "summary": "the enclosure is busy",
            "contractPath": "/coord/tasks/repo-a/m/enclosures/held/series-contract.md",
        }
        failure = _oracle_failure("case", payload)
        assert failure is not None
        assert failure.startswith("case: available artifact declared but no tool addressed")


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

    def test_every_failure_path_answers_in_one_of_the_four_named_shapes(self) -> None:
        """No fifth shape: a typed refusal, a stateful refusal, an always-answer, or a raiser.

        The four arms are read from the payload and the call outcome -- never from a pin -- so
        each assertion below compares an observation to a pin in both directions. (Renamed from
        ``..._three_named_shapes`` by `260918-TSIP-L10`, in the change that added the fourth:
        the name states the claim, and the claim changed.)
        """

        observed_raised = {tool for tool, row in self.census.items() if row["kind"] != "RETURNED"}
        returned = set(self.census) - observed_raised
        observed_stateful = {
            tool for tool in returned if is_stateful_refusal(self.census[tool]["payload"])
        }
        observed_ok = {
            tool
            for tool in returned - observed_stateful
            if self.census[tool]["payload"].get("ok") is True
        }
        observed_refused = returned - observed_stateful - observed_ok

        assert observed_raised == set(BARE_RAISERS), (
            "the bare-raiser census moved: repair it and remove the entry in the same change, "
            "or add the new one deliberately (never widen this constant)"
        )
        assert observed_stateful == set(STATEFUL_REFUSALS), (
            "a tool's answer moved into (or out of) the ok:true/state:refused shape this census "
            "pins by name: add or remove the entry in the same change that moves it"
        )
        assert observed_ok == set(ALWAYS_ANSWERS), (
            "a tool answered ok:true where this census expects a refusal, or an entry is stale"
        )
        assert observed_refused == (
            set(PUBLIC_TOOLS) - set(BARE_RAISERS) - set(ALWAYS_ANSWERS) - set(STATEFUL_REFUSALS)
        ), "the four shapes do not partition the population"

        # `NF3`: the stateful arm's own axes are asserted HERE as well as in their own case, so a
        # skip marker on that case cannot remove the protection -- the partition would still hold
        # while a refusal stopped naming itself. One implementation, two callers.
        self._assert_stateful_refusals()

    def test_every_refusal_names_what_refused_why_and_the_next_action(self) -> None:
        """Identity, reason, and the refined oracle, each asserted by name.

        The oracle (L108, T54, Parent ruling 20ef3396): an available artifact
        (declared path, no missing report) requires tool-addressing navigation;
        a missing-path echo carries none and no tool; no path declares none.
        Availability comes from the response's own fields only, with no tool
        list. Every refused row is asserted, none skipped.
        """

        failures: list[str] = []
        population = (
            set(PUBLIC_TOOLS) - set(BARE_RAISERS) - set(ALWAYS_ANSWERS) - set(STATEFUL_REFUSALS)
        )
        for tool in sorted(population):
            payload = self.census[tool]["payload"]
            assert payload.get("ok") is False, f"{tool} answered ok:{payload.get('ok')!r}"
            identity, reason, _navigation = refusal_axes(payload)
            missing = [
                axis
                for axis, present in (
                    ("what refused (a refusal identity)", identity),
                    ("why (a non-empty reason)", reason),
                )
                if not present
            ]
            if missing:
                failures.append(f"{tool}: missing {', '.join(missing)}")
            failure = _oracle_failure(tool, payload)
            if failure is not None:
                failures.append(failure)
        assert failures == [], "refusals that do not name themselves:\n" + "\n".join(failures)

    def test_no_path_cases_carry_no_tool_address(self) -> None:
        """The two named no-path cases and their reason (L108, T54).

        `direct_landing` (absent contract) and `dispatch_agent` (absent task
        document) declare no artifact path — a taskDocumentRef addresses a
        missing document, which is what both refusals refuse — so the T54 guard
        withholds the ambient hint and neither carries a tool-addressing key.
        """
        for tool in ("direct_landing", "dispatch_agent"):
            payload = self.census[tool]["payload"]
            assert payload.get("ok") is False, f"{tool} answered ok:{payload.get('ok')!r}"
            assert not _declares_artifact_path(payload), (
                f"{tool} left the (L108, T54) no-path half: it declares an artifact path"
            )
            assert not _addresses_a_tool(payload), (
                f"{tool} left the (L108, T54) no-path half: it addresses a tool"
            )

    def test_every_stateful_refusal_names_its_code_its_detail_and_its_model(
        self,
    ) -> None:
        """The fourth shape's own axes, so it cannot become a place to hide a silent success.

        `ok: true` alone would be indistinguishable from the always-answers, so the shape is
        held to the two things its models declare: a shipped ``refusalCode`` naming WHAT was
        refused and a non-empty ``refusalDetail`` naming why, on a payload that validates
        against the tool's own registered response model. A member that loses either field, or
        stops validating, fails here by name rather than sliding into `ALWAYS_ANSWERS`.

        The assertions themselves live in :meth:`_assert_stateful_refusals`, which the partition
        case calls too (`NF3`): a protection that only one case executes is a protection a skip
        marker can remove, and the axis is exactly the kind of case that gets marked skip while
        somebody "temporarily" debugs something.
        """

        self._assert_stateful_refusals()

    def _assert_stateful_refusals(self) -> None:
        """The stateful arm's three axes, callable from more than one case on purpose (`NF3`)."""

        assert set(STATEFUL_REFUSALS) <= set(PUBLIC_TOOLS), (
            "a stateful-refusal pin names a tool that is not advertised"
        )
        failures: list[str] = []
        for tool in sorted(STATEFUL_REFUSALS):
            row = self.census[tool]
            payload = row["payload"]
            if row["kind"] != "RETURNED":
                failures.append(f"{tool}: raised; the envelope is gone")
                continue
            if payload.get("state") != STATEFUL_REFUSAL_STATE:
                failures.append(f"{tool}: answered state:{payload.get('state')!r}")
                continue
            code = payload.get("refusalCode")
            detail = payload.get("refusalDetail")
            if not (isinstance(code, str) and code.strip()):
                failures.append(f"{tool}: refused as a state without a refusalCode")
                continue
            if not (isinstance(detail, str) and detail.strip()):
                failures.append(f"{tool}: refused as a state without a refusalDetail")
                continue
            # MIK-R108: the model contract belongs to the whole result the
            # function computed (read back from the answer's detail file);
            # the answer holds what the caller needs next, without the
            # four envelope fields.
            whole = row.get("whole") or {}
            if not whole:
                failures.append(f"{tool}: no whole result behind the answer's log")
                continue
            for field in ("tokens", "tokenizer", "tokenCountExact", "operation"):
                if field in payload:
                    failures.append(f"{tool}: answer holds envelope field {field!r}")
            model = registered_response_model(tool)
            try:
                model.model_validate(whole)
            except ValidationError as invalid:
                failures.append(
                    f"{tool}: {model.__name__} refused its own whole result: "
                    f"{' | '.join(str(invalid).splitlines()[:2])}"
                )
        assert failures == [], "stateful refusals that do not name themselves:\n" + "\n".join(
            failures
        )

    def test_the_write_plane_is_named_by_both_its_shipped_entry_points(self) -> None:
        """One writer, two shipped CLI routes, and both named where a caller reads them.

        The surface checked is the tool *description* the model reads before it calls anything.
        A description that names one route leaves the other undiscoverable, so the check is by
        name, and it fails on either alone.
        """

        advertised = {tool.name: tool for tool in anyio.run(self.world.server.list_tools)}
        for tool in sorted(WRITE_ROUTE_NAMING_SURFACES):
            description = advertised[tool].description or ""
            for entry_point in (
                "agents-remember knowledge-ingest",
                "agents-remember knowledge-bootstrap",
            ):
                assert entry_point in description, (
                    f"the advertised description of {tool} does not name {entry_point!r}, so a "
                    f"model reaching for the write plane cannot find that route: {description}"
                )

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
    """MIK-R112 F12 as repaired under the Parent-bound ruling: refusal without a tool.

    The lifecycle-switch-required refusal names no tool (no nextTool /
    nextOperation / nextArgs at top level or in nextStep, whose summary carries
    the same sentence) and validates against the narrowed vocabulary. The old
    removed-tool guidance stays as a negative control: it still fails the same
    model. No switch tool, model member, or backend is resurrected here, and
    the emitter returns before any filesystem work, so the cases write nothing.
    """

    def _bound_config(self, coord: Path) -> McpRuntimeConfig:
        return McpRuntimeConfig(
            config_path=coord / "settings.json",
            coordination_root=coord,
            workspace_root=coord,
            transcript_root=coord / "logs",
            repositories={"repo-a": RepositoryScope(repo_id="repo-a", path=coord)},
        )

    def test_switch_refusal_names_no_tool_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            reset_ambient()
            amb = AmbientLifecycle(EventStore(observer_root(self._bound_config(coord))))
            install_ambient(amb)
            amb.start(fleeting=False)
            try:
                refusal = worktree_start_tool(
                    self._bound_config(coord),
                    TaskIdentity(repo_id="repo-a", task_name="m", worktree_name="w"),
                )
            finally:
                reset_ambient()
            assert refusal["ok"] is False
            assert "nextTool" not in refusal
            assert "nextOperation" not in refusal
            assert "nextArgs" not in refusal
            assert set(refusal["nextStep"]) == {"summary"}
            assert "new session" in refusal["nextStep"]["summary"]
            assert "new session" in refusal["summary"]
            WorktreeStartResponse.model_validate(refusal)

    def test_old_removed_tool_guidance_stays_a_negative_control(self) -> None:
        old_guidance = {
            "ok": False,
            "state": "lifecycle-switch-required",
            "summary": "start a fresh fleeting lifecycle with switch_lifecycle",
            "nextOperation": "switch_task_lifecycle",
            "nextTool": "switch_lifecycle",
            "nextArgs": {},
            "nextStep": {
                "summary": "Switch to a fresh fleeting lifecycle, then retry worktree_start.",
                "nextOperation": "switch_task_lifecycle",
                "nextTool": "switch_lifecycle",
                "nextArgs": {},
            },
        }
        with pytest.raises(ValidationError, match="switch_lifecycle"):
            WorktreeStartResponse.model_validate(old_guidance)

    def test_stripping_each_axis_of_a_real_refusal_makes_the_predicate_fail(
        self,
    ) -> None:
        """The predicate must be able to fail. Each axis is stripped from a REAL refusal."""
        refusal = {
            "ok": False,
            "status": "provider-capability-unavailable",
            "detail": "the grepai-memory provider is not configured",
            "nextStep": {
                "summary": "arm the provider",
                "nextTool": "provider_watchers",
            },
        }
        assert refusal_axes(refusal) == (True, True, True), "the control's own fixture is wrong"

        for axis, mutation in (
            ("identity", {"status": None, "state": None, "refusalStatus": None}),
            ("why", {"detail": None, "summary": None, "message": None}),
            (
                "next action",
                {
                    "nextStep": None,
                    "nextTool": None,
                    "nextAction": None,
                    "nextArgs": None,
                },
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
        refusal = {
            "ok": False,
            "status": "x",
            "detail": "y",
            "nextStep": {"summary": "z"},
        }
        assert answered.get("ok") is True
        assert refusal.get("ok") is False
        assert all(refusal_axes(refusal))
        # A bare raise has NO payload at all -- the census records it as `kind != RETURNED`,
        # which is exactly why that arm is read from the call outcome and not from a dict.
        assert refusal_axes({}) == (False, False, False), (
            "an empty payload must satisfy none of the three axes"
        )


def test_an_unregistered_pin_is_refused_by_name_not_by_a_key_error() -> None:
    """`NF4`: the lookup's contract, asserted rather than described.

    The state this guards is unreachable while the registration agreement holds -- every advertised
    tool is registered, and the pin is a subset of the roster -- which is exactly why the guard is
    worth pinning: the failure it converts is an *instrument* fault (a pin naming something the
    registry does not carry) and it must read as one sentence naming the pin, not as a bare
    ``KeyError`` from a comprehension. The case drives the helper directly with a name the registry
    does not carry, so it measures the helper and not a tree state.
    """

    with pytest.raises(AssertionError, match="stateful pin names an unregistered tool"):
        registered_response_model("tsip_l10_unregistered_probe")


def test_no_case_in_this_module_is_removed_by_a_skip_marker(
    request: pytest.FixtureRequest,
) -> None:
    """`T99`/`NF3`: a protection removed by a skip marker is invisible in a green count.

    A skip is the one edit that makes a case stop protecting anything while every other reading
    stays green: `7 passed, 1 skipped, exit 0` is what the added stateful axis case reported when
    it was deliberately marked skipped in review. No population check in this repository counts
    skips, so this module pins its own: every case it collects must be free of a ``skip`` or
    ``skipif`` marker, and a marker added to any of them -- the axis case included -- reddens
    here by node id.

    **The limit, stated rather than implied.** This case reads the collected items' markers, so
    it catches the way a skip is added in practice (and the way the review added one). A case
    that calls ``pytest.skip()`` inside its body is not visible to it; catching that needs a
    session-level reporter, which is a shared-bootstrap change rather than this module's. The
    second half of the same protection is structural and does not depend on this case at all:
    the stateful arm's assertions are executed by the partition case as well
    (:meth:`FailurePathCensusTests._assert_stateful_refusals`), so marking the axis case skip
    leaves the axes asserted anyway -- `NF3`'s point is that the protection, not the case, is
    what must survive.
    """

    this_file = Path(__file__).resolve()
    removed = sorted(
        item.nodeid
        for item in request.session.items
        if (parent := item.getparent(pytest.Module)) is not None
        and parent.path == this_file
        and (
            item.get_closest_marker("skip") is not None
            or item.get_closest_marker("skipif") is not None
        )
    )
    assert removed == [], (
        f"cases in this module are removed by a skip marker: {removed} -- a skipped protection is "
        "a protection that did not run; delete the marker or take the case out of the module"
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
