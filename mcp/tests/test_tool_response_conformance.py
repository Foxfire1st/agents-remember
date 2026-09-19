"""Dev-time conformance for MCP tool response contracts, at the firing state.

Production code validates every tool payload against its registered response model in
``agents_remember.mcp.tools.base._tool_payload``. That validation runs **after** the
producer has performed its writes, so a producer key the model forbids costs the caller
the payload that would have told it the work was done: ``lifecycle_finalize_task``
completed its transaction and then answered with a pydantic error, ``session_retire``
terminated the seat and then answered with a pydantic error, and ``task_doc``'s
``read_steps`` -- the one operation the tool publishes as *the* way to read a checklist --
could never return at all. This module moves that guarantee into the suite.

**The rule this module is built on, learned from the 1,174-line conformance sweep that
``d3610903`` deleted: a fixture that sits in a guard's false branch is a false green.**
That sweep captured ``lifecycle_finalize_task`` with ``dry_run=True`` on a leaf contract,
where the atomic-series release bridge returns early and never emits the two keys its own
model forbids, and it captured ``task_doc`` with ``operation="create"`` only, never
``read_steps``. It passed for the whole life of both defects. So every fixture below
**asserts that it reached the emitting branch**: the guard that gates the key is read out
of the payload or out of the source, and a fixture that fell into the early return fails
rather than passing quietly.

Two layers, and they answer different questions:

* ``ToolResponseSurfaceTests`` is **total and structural**. Every registered response
  model has exactly one adapter entry and every adapter entry is registered; the public
  roster is a subset of the registry; the 17 internal registrations are named. This layer
  cannot see a wrong key, but it cannot be sampled either -- a new tool cannot be added
  without appearing here.
* ``ToolResponseFiringStateTests`` is **executed**. Each case drives the real producer,
  through the real adapter, against a hermetic scratch coordination root, and asserts the
  payload validates *and* that the conditional key was actually produced.
"""

from __future__ import annotations

import ast
import asyncio
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest import mock

from agents_remember.application.operator_inbox_tools import operator_inbox_post_tool
from agents_remember.application.structural.outcomes import StructuralOutcome, structural_payload
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.worktree_tools import _worktree_result
from agents_remember.controlplane.operator_inbox_records import (
    InboxAddress,
    InboxMessage,
    InboxPoster,
    InboxRouting,
    create_operator_inbox_entry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.mcp.registration import TOOL_REGISTRARS
from agents_remember.mcp.tools import terminal as terminal_payload_tools
from agents_remember.mcp.tools.base import PUBLIC_TOOLS, _tool_payload
from agents_remember.mcp.tools.operator_inbox import operator_inbox_post_payload
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.models.base import FlexibleResponseModel, StrictResponseModel
from agents_remember.models.lifecycles.finalize import LifecycleFinalizeTaskResponse
from agents_remember.models.operator_inbox import OperatorInboxPostResponse
from agents_remember.models.structural.agent import (
    RenameChildResponse,
    RenameSelfResponse,
    RetireChildResponse,
)
from agents_remember.models.task_doc import TaskDocResponse
from agents_remember.models.terminal import SessionRetireResponse
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.models.worktree import WorktreeOperationControlResponse
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.worktrees.activation import atomic_series_activation_terminal
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_control_errors import (
    LifecycleControlError,
)
from agents_remember.worktrees.modules.finalize import FinalizeArgs, _finalized_result
from agents_remember.worktrees.worktree_contract import WorktreeContract
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError
from test_agent_notifier import _entry, _FakeHost

REPO = "agents-remember"
SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
PACKAGE_ROOT = SRC_ROOT / "agents_remember"
TOOLS_ROOT = PACKAGE_ROOT / "mcp" / "tools"
LIFECYCLE_PACKAGE = PACKAGE_ROOT / "worktrees" / "integration" / "lifecycle"


# --------------------------------------------------------------------------------------
# Shared hermetic world
# --------------------------------------------------------------------------------------


def adapter_tool_ids() -> dict[str, list[str]]:
    """Every tool id a ``_tool_payload("<id>", ...)`` call site names, with its sites.

    Read from the source rather than from a hand-kept list, so this cannot drift from the
    adapter surface it describes. 96 call sites name 89 ids at the merged base: some tools have
    two entry points (``operator_inbox_post_payload`` and
    ``registered_operator_inbox_post_payload``), and the five ``knowledge_*`` builders route
    through the choke point as of ``260918-TSIP-L10`` -- before that they returned a raw dict,
    which is why five registered models had no adapter entry here.
    """

    sites: dict[str, list[str]] = {}
    for path in sorted(TOOLS_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "_tool_payload"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                sites.setdefault(first.value, []).append(f"{path.name}:{node.lineno}")
    return sites


def choke_point_handlers(path: Path) -> tuple[list[ast.FunctionDef | ast.AsyncFunctionDef], int]:
    """The handlers in one tool module, and that module's ``_tool_payload`` call sites.

    A **handler** here is any public module-level function: in ``mcp/tools/`` a public
    function *is* an entry point the transport can call, and every one of them is named
    ``*_payload`` by convention. The one exemption is derived rather than listed -- a
    function the module itself passes *into* ``_tool_payload(...)`` is an argument of the
    choke point (the ``compact_*_payload`` shapers hand their trimmed dict to
    ``_tool_payload``), so it is not a route around it.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"))
    call_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_tool_payload"
    ]
    inputs: set[str] = set()
    for call in call_sites:
        for argument in (*call.args, *(entry.value for entry in call.keywords)):
            for node in ast.walk(argument):
                if isinstance(node, ast.Name):
                    inputs.add(node.id)

    handlers = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
        and node.name not in inputs
    ]
    return handlers, len(call_sites)


def advertised_description(tool_name: str) -> str:
    """The description a caller actually receives for one tool.

    Read from the registered FastMCP surface rather than from the source constant, so this
    asserts the published contract and not the module that happens to define it -- the same
    probe the T50 description pins use.
    """

    server = FastMCP("tsip-l4-description-probe")
    for register_tools in TOOL_REGISTRARS:
        register_tools(server, cast(McpRuntimeConfig, _RegistrationStub()))
    advertised = {
        tool.name: " ".join((tool.description or "").split())
        for tool in asyncio.run(server.list_tools())
    }
    return advertised[tool_name]


class _RegistrationStub:
    """A registration-time config stub: no registrar validates the config while registering."""

    def __getattr__(self, name: str) -> object:
        return _RegistrationStub()


def advertised_tool_names() -> set[str]:
    """Every tool name the product actually registers, read from the registered surface.

    The same probe :func:`advertised_description` uses: a real ``FastMCP`` server, every
    registrar in ``TOOL_REGISTRARS``, and the server's own listing. This is the **derived**
    population the roster assertions compare against, and it is derived from the registration
    modules rather than from ``PUBLIC_TOOLS`` -- so a tool that is registered but missing from
    the roster (or the reverse) is a disagreement this module can see, which a literal count
    could never show. `T94`: the roster is 72 at this master's merged base, and the figure every
    check on this master was measured against before it was 67.
    """

    server = FastMCP("tsip-l10-roster-probe")
    for register_tools in TOOL_REGISTRARS:
        register_tools(server, cast(McpRuntimeConfig, _RegistrationStub()))
    return {tool.name for tool in asyncio.run(server.list_tools())}


# The roster size this master measured on its own merged base (`7879f5b2`): **72**, five more
# than the 67 every figure on this master was written against, because the incoming
# `260915_knowledge-substrate` line added the five `knowledge_*` tools (`T94`). It is a
# *visible* constant rather than an inline literal so that a deliberate change to the surface is
# one edit here -- and it is asserted ALONGSIDE the live-surface equality below, never instead
# of it, so an accidental change fails against the registration modules rather than against a
# number somebody has to remember to update.
ROSTER_SIZE = 72

# The tool-adapter module population and the handler population, same rule: the numbers are
# asserted with the rule (every module's every handler returns `_tool_payload(...)`, and the
# call-site total equals the handler total) so the rule cannot be satisfied by measuring
# nothing, and the derivation below is what makes a silent change fail.
TOOL_MODULE_COUNT = 20
TOOL_HANDLER_COUNT = 96


def literal_keyword_values(path: Path, keyword: str) -> set[str]:
    """Every string literal passed to ``keyword=`` anywhere in one module.

    Used to derive, from the source, the value set a producer can emit -- so a new value
    cannot be added without this module seeing it.
    """

    values: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for entry in node.keywords:
            if entry.arg != keyword:
                continue
            if isinstance(entry.value, ast.Constant) and isinstance(entry.value.value, str):
                values.add(entry.value.value)
    return values


def scratch_coordination_root(prefix: str) -> tuple[Path, McpRuntimeConfig]:
    """A coordination root under ``/tmp`` with one initialized Git repository."""

    scratch = Path(tempfile.mkdtemp(prefix=prefix))
    coord = scratch / "coord"
    coord.mkdir(parents=True)
    repo = coord / "repo"
    repo.mkdir()
    for command in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    config = McpRuntimeConfig(
        config_path=coord / "settings.json",
        coordination_root=coord,
        workspace_root=coord,
        transcript_root=coord / "logs" / "mcp",
        repositories={REPO: RepositoryScope(repo_id=REPO, path=repo)},
    )
    return scratch, config


class ScratchWorldTests(unittest.TestCase):
    """Base for cases that build a coordination root; every root is removed afterwards."""

    def setUp(self) -> None:
        self._roots: list[Path] = []

    def tearDown(self) -> None:
        for root in self._roots:
            shutil.rmtree(root, ignore_errors=True)

    def world(self, prefix: str) -> McpRuntimeConfig:
        scratch, config = scratch_coordination_root(prefix)
        self._roots.append(scratch)
        return config


# --------------------------------------------------------------------------------------
# Layer 1 -- total, structural
# --------------------------------------------------------------------------------------


class ToolResponseSurfaceTests(unittest.TestCase):
    """The registry, the adapter surface and the public roster must agree exactly."""

    def test_registration_and_adapter_surface_agree_in_both_directions(self) -> None:
        """Requirement: every roster tool is registered, and every registration is used.

        Four populations, each derived rather than transcribed: the registry
        (``TOOL_RESPONSE_MODELS``), the adapter entries (the ``_tool_payload`` id literals
        in ``mcp/tools/``), the advertised roster (``PUBLIC_TOOLS``) and the **registered
        surface** the product itself publishes (the live ``FastMCP`` listing). The two
        directions this leaf exists for are the first two assertions: registry minus adapter must be
        empty (a registered model nobody produces is a model nothing validates against) and
        adapter minus registry must be empty (a payload nobody declared). Both were non-empty on the
        merged base for the five ``knowledge_*`` families, whose builders returned a raw dict
        instead of routing through ``_tool_payload``.

        The roster is then compared to the LIVE surface rather than to a count, so a tool that
        is registered but unlisted (or listed but unregistered) fails here; `ROSTER_SIZE` is the
        second, visible edit any deliberate change to that population must make.

        **What was removed from this case, and why (`NF2`).** An earlier form of it ended with
        ``assertEqual(len(registered), len(PUBLIC_TOOLS) + len(registered - set(PUBLIC_TOOLS)))``
        and called it a fourth strengthening. It cannot fail while the ``roster - registered``
        assertion three lines above passes -- ``|registered| = |roster| + |registered - roster|``
        is a partition identity, and that assertion is what makes the second term the exact
        internal set -- so it bought nothing and was deleted rather than dressed up. The failable
        assertion that replaced its *purpose* (a tool silently leaving the advertised population)
        is the live-surface equality above: it fails on a roster entry deleted, on a registrar
        added, and on either side of the roster/registry pair moving alone, and the mutation that
        reds it is recorded in the report's round-2 addendum.
        """

        registered = set(TOOL_RESPONSE_MODELS)
        adapter = adapter_tool_ids()
        self.assertEqual(
            set(),
            registered - set(adapter),
            "registered response models with no adapter entry",
        )
        self.assertEqual(
            set(),
            set(adapter) - registered,
            "adapter entries with no registered response model",
        )
        self.assertEqual(
            set(),
            set(PUBLIC_TOOLS) - registered,
            "roster tools with no registered response model",
        )
        advertised = advertised_tool_names()
        self.assertEqual(
            set(PUBLIC_TOOLS),
            advertised,
            "the advertised roster and the registered surface disagree",
        )
        self.assertEqual(
            len(set(PUBLIC_TOOLS)),
            len(PUBLIC_TOOLS),
            "the roster names a tool twice",
        )
        self.assertEqual(ROSTER_SIZE, len(PUBLIC_TOOLS), "the advertised roster changed size")

    def test_the_seventeen_internal_registrations_are_named_not_implied(self) -> None:
        """The unadvertised registrations are compatibility/internal builders by design.

        They are validated at the same choke point as the roster, so they are defects when
        they drift; they are simply not advertised. Recording the exact set means a tool
        that silently leaves the roster shows up here rather than nowhere.
        """

        internal = set(TOOL_RESPONSE_MODELS) - set(PUBLIC_TOOLS)
        self.assertEqual(
            {
                "attach_terminal_session_to_task",
                "gate_create",
                "gate_decide_internal",
                "gate_list_internal",
                "gate_response_wait",
                "gate_wait",
                "hosted_session_readiness",
                "lifecycle_block",
                "lifecycle_gate_internal",
                "operator_inbox_consume",
                "operator_inbox_poll",
                "operator_inbox_post",
                "operator_inbox_supersede",
                "orchestration_nudge_manager",
                "session_rename",
                "session_retire",
                "spawn_agent_session",
            },
            internal,
        )

    def test_the_models_these_fixtures_validate_are_the_registered_ones(self) -> None:
        """A fixture that validates against a hand-picked class proves nothing about the wire.

        The executed cases below call ``model_validate`` on the concrete classes so the
        assertions can read named fields; this pins each of those classes to the object the
        registry actually validates with, so the two cannot drift apart.
        """

        for tool, model in (
            ("lifecycle_finalize_task", LifecycleFinalizeTaskResponse),
            ("operator_inbox_post", OperatorInboxPostResponse),
            ("retire_child", RetireChildResponse),
            ("rename_child", RenameChildResponse),
            ("rename_self", RenameSelfResponse),
            ("session_retire", SessionRetireResponse),
            ("task_doc", TaskDocResponse),
            ("worktree_operation_control", WorktreeOperationControlResponse),
        ):
            with self.subTest(tool=tool):
                self.assertIs(TOOL_RESPONSE_MODELS[tool], model)

    def test_every_registered_model_is_strict_or_declared_flexible(self) -> None:
        """A strict model forbids extras; a flexible one declares them as its contract.

        The split is derived TWICE and the two derivations must agree: the registry says which
        base class each model inherits (the declaration) and ``model_config['extra']`` says what
        the model actually does (the observation). A model that flips its config without moving
        to the other family, or that moves family without carrying its config, fails here by
        name -- which is what the old ``assertEqual(48, ...)``/``assertEqual(36, ...)`` pair was
        buying, without the two bare counts that the merged line's five new strict
        ``knowledge_*`` models invalidated (`T94`). The observed counts are asserted against the
        declarations, so neither number is transcribed anywhere.
        """

        strict = {
            tool
            for tool, model in TOOL_RESPONSE_MODELS.items()
            if model.model_config.get("extra") == "forbid"
        }
        flexible = {
            tool
            for tool, model in TOOL_RESPONSE_MODELS.items()
            if model.model_config.get("extra") == "allow"
        }
        declared_strict = {
            tool
            for tool, model in TOOL_RESPONSE_MODELS.items()
            if issubclass(model, StrictResponseModel)
        }
        declared_flexible = {
            tool
            for tool, model in TOOL_RESPONSE_MODELS.items()
            if issubclass(model, FlexibleResponseModel)
        }
        self.assertEqual(set(TOOL_RESPONSE_MODELS), strict | flexible)
        self.assertEqual(set(), strict & flexible)
        self.assertEqual(
            declared_strict, strict, "a model declares strict but is not configured so"
        )
        self.assertEqual(
            declared_flexible, flexible, "a model declares flexible but is not configured so"
        )
        self.assertEqual(set(TOOL_RESPONSE_MODELS), declared_strict | declared_flexible)

    def test_every_tool_adapter_module_routes_through_the_choke_point(self) -> None:
        """No tool handler may return a raw dict to the transport, bypassing validation.

        ``_tool_payload`` is the only route from an adapter module to the wire and the only
        place a payload meets its registered response model, so the assertion is about the
        **return** of every handler -- not about the module's text. That distinction is the
        whole point: a substring test cannot tell a module that *calls* the choke point from
        one that *returns a dict past it*, and one appended to ``terminal.py`` as
        ``return {"ok": True, ...}`` left the previous ``assertIn("_tool_payload", source)``
        here green.

        The counts are asserted with the rule so it cannot be satisfied by having no
        handlers at all, and the call-site equality is load-bearing rather than decorative: a
        ``_tool_payload`` call whose result is discarded while a raw dict is returned is
        exactly the bypass shape, and it makes the module's call sites outnumber its
        handlers. 20 modules hold 96 handlers and 96 call sites at the merged base -- the same
        96 the registration census reports, so the two readings cannot drift apart -- and the
        handler/call-site totals are also asserted equal to the id-literal total read by
        :func:`adapter_tool_ids`, which is the derivation that cannot be satisfied by measuring
        nothing. This is the case the five ``knowledge_*`` builders failed before
        ``260918-TSIP-L10``: they returned raw dicts, so the module count was 19.
        """

        modules = [
            path
            for path in sorted(TOOLS_ROOT.glob("*.py"))
            if path.name not in {"__init__.py", "base.py"}
        ]
        self.assertEqual(TOOL_MODULE_COUNT, len(modules))

        handlers = 0
        call_sites_total = 0
        for path in modules:
            functions, call_sites = choke_point_handlers(path)
            self.assertTrue(functions, f"{path.name} defines no handler")
            handlers += len(functions)
            call_sites_total += call_sites
            for function in functions:
                returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
                self.assertTrue(returns, f"{path.name}:{function.name} has no return")
                for node in returns:
                    returned = node.value
                    if not isinstance(returned, ast.Call):
                        self.fail(
                            f"{path.name}:{function.name} returns a raw value at line "
                            f"{node.lineno} instead of _tool_payload(...)"
                        )
                    callee = returned.func
                    self.assertTrue(
                        isinstance(callee, ast.Name) and callee.id == "_tool_payload",
                        f"{path.name}:{function.name} returns {ast.unparse(callee)}(...) at "
                        f"line {node.lineno}; a handler must return _tool_payload(...)",
                    )
            self.assertEqual(
                len(functions),
                call_sites,
                f"{path.name}: {call_sites} _tool_payload call sites for {len(functions)} "
                "handlers -- a call whose result is not returned is a bypass",
            )
        self.assertEqual(TOOL_HANDLER_COUNT, handlers, "the tool-handler population changed")
        # Derived, not transcribed: the walk above and the id-literal census read the same
        # 96 call sites from the same modules by two different routes.
        self.assertEqual(
            sum(len(sites) for sites in adapter_tool_ids().values()),
            call_sites_total,
            "the handler walk and the id-literal census disagree about the call-site population",
        )
        self.assertEqual(TOOL_HANDLER_COUNT, call_sites_total)


# --------------------------------------------------------------------------------------
# Layer 2 -- executed, at the firing state
# --------------------------------------------------------------------------------------


class ToolResponseFiringStateTests(ScratchWorldTests):
    """One case per producer key that a registered model used to forbid."""

    # -- D53: lifecycle_finalize_task / atomicSeriesActivation[Release] -----------------

    def _series_contract(self, coord: McpRuntimeConfig) -> WorktreeContract:
        task_root = coord.coordination_root / "tasks" / REPO / "demo"
        task_root.mkdir(parents=True)
        (coord.coordination_root / "worktrees").mkdir(parents=True, exist_ok=True)
        contract_path = task_root / "series-contract.md"
        contract_path.write_text("scratch series contract\n", encoding="utf-8")
        return WorktreeContract(
            task_id="260918-TSIP-DEMO",
            task_name="demo",
            repo_name=REPO,
            workflow_kind="light-task",
            memory_mode="external",
            coordination_root=coord.coordination_root,
            task_root=task_root,
            contract_path=contract_path,
            task_artifact=task_root / "task.json",
            worktree_group=coord.coordination_root / "worktrees" / "demo",
            code_repo_path=coord.coordination_root / "repo",
            code_source_branch="ar/demo",
            code_work_branch="ar/demo-work",
            code_base_commit="0" * 40,
            code_worktree=coord.coordination_root / "worktrees" / "demo" / "code",
            kind="series",
            leaf_id="260918-TSIP-DEMO-L1",
            lifecycle_id="lc-scratch",
            closeout_status="completed",
            integration_status="completed",
            code_commit="0" * 40,
        )

    def _finalize_payload(self, contract: WorktreeContract, *, dry_run: bool) -> dict:
        """The real terminal builder, wrapped in the production envelope."""

        result = _finalized_result(
            contract,
            FinalizeArgs(contract_path=contract.contract_path, dry_run=dry_run),
            cleanup={},
            updates={},
            projection_effects=[],
        )
        return _worktree_result("lifecycle_finalize_task", result)

    def test_lifecycle_finalize_validates_with_the_series_release_produced(self) -> None:
        """D53: the success arm emits both keys, so both must be declared.

        The firing state is ``contract.kind == "series"`` **and** ``dry_run=False`` **and**
        a release bridge that returns 0 -- in any other state
        ``with_terminal_atomic_series_release`` returns before it writes either key, which
        is exactly why the deleted sweep's ``dry_run=True`` leaf fixture never saw them.
        The first assertion below is that witness: if the keys are absent the fixture fell
        into the early return and the case fails instead of passing vacuously.
        """

        coord = self.world("tsip-l4-finalize-")
        contract = self._series_contract(coord)
        payload = self._finalize_payload(contract, dry_run=False)

        self.assertIn(
            "atomicSeriesActivation", payload, "fixture did not reach the emitting branch"
        )
        self.assertIn(
            "atomicSeriesActivationRelease", payload, "fixture did not reach the emitting branch"
        )
        self.assertEqual("series", contract.kind)
        self.assertFalse(payload["dryRun"])
        response = LifecycleFinalizeTaskResponse.model_validate(payload)
        self.assertEqual("finalized", response.state)
        self.assertEqual({"state": "already-vacant"}, payload["atomicSeriesActivationRelease"])

    def test_lifecycle_finalize_validates_on_the_release_blocked_arm(self) -> None:
        """D53's second site: ``_finalized_result`` spreads the bridge payload whole.

        ``worktrees/modules/finalize.py:161-177`` returns ``activation-release-blocked``
        with ``**activation_release.payload``, so a *failed* release reaches the same model
        carrying ``state == "release-failed"`` and the error triple. The blocked arm is
        reached by making the release itself raise -- the bridge catches it, writes the
        failure triple and returns non-zero, which is the only way that branch fires.
        """

        coord = self.world("tsip-l4-finalize-blocked-")
        contract = self._series_contract(coord)
        with mock.patch.object(
            atomic_series_activation_terminal,
            "release_terminal_atomic_series_selection_if_exact",
            side_effect=OSError("scratch release failure"),
        ):
            payload = self._finalize_payload(contract, dry_run=False)

        self.assertEqual("activation-release-blocked", payload["state"])
        release = payload["atomicSeriesActivationRelease"]
        self.assertEqual("release-failed", release["state"], "the blocked arm did not fire")
        self.assertEqual("OSError", release["errorType"])
        response = LifecycleFinalizeTaskResponse.model_validate(payload)
        self.assertEqual("activation-release-blocked", response.state)
        released = response.atomicSeriesActivationRelease
        self.assertIsNotNone(released)
        assert released is not None  # narrowed above; the model declares it optional
        self.assertEqual("release-failed", released.state)

    def test_the_two_atomic_series_keys_are_declared_together(self) -> None:
        """The class, not the instance: no model may declare one key without the other.

        ``atomicSeriesActivation`` and ``atomicSeriesActivationRelease`` are written by the
        same bridge in the same payload, so a model that declares one and forbids the other
        is the exact asymmetry D53 was. This reads the whole registry rather than naming the
        one model that was wrong, so a half-declaration anywhere fails here.
        """

        strict = {
            tool: model
            for tool, model in TOOL_RESPONSE_MODELS.items()
            if model.model_config.get("extra") == "forbid"
        }
        declared_activation = {
            tool for tool, model in strict.items() if "atomicSeriesActivation" in model.model_fields
        }
        declared_release = {
            tool
            for tool, model in strict.items()
            if "atomicSeriesActivationRelease" in model.model_fields
        }
        self.assertEqual(declared_activation, declared_release)
        self.assertEqual({"lifecycle_finalize_task"}, declared_activation)

        produced = {"atomicSeriesActivation", "atomicSeriesActivationRelease"}
        self.assertEqual(set(), produced - set(LifecycleFinalizeTaskResponse.model_fields))
        self.assertIn(
            "WorktreeCommandResponse",
            (PACKAGE_ROOT / "models" / "worktree.py").read_text(encoding="utf-8"),
        )

    # -- T7: task_doc / steps ----------------------------------------------------------

    def test_task_doc_read_steps_validates_and_returns_the_checklist(self) -> None:
        """T7: ``read_steps`` is advertised in the schema and used to always error.

        The witness is ``"steps" in payload`` with the checklist non-empty: the guard is
        ``_read_steps`` itself, and a fixture that ran any *other* operation would produce a
        payload without the key. The ``get`` control on the same world proves the harness is
        not what failed.
        """

        coord = self.world("tsip-l4-taskdoc-")
        target = TaskDocTarget(repo_id=REPO, task_name="l4-demo", slug="l4_demo")

        def create(**fields: object) -> dict:
            payload: dict[str, object] = {
                "id": "L4",
                "slug": "l4_demo",
                "title": "Conformance demo",
                "kind": "subTask",
                "repo": REPO,
                "type": "fix",
                "createdAt": "2026-01-01T00:00",
            }
            payload.update({str(key): value for key, value in fields.items()})
            return task_doc_tool(
                coord, target, operation="create", edit=TaskDocEdit(fields=payload)
            )

        task_doc_tool(
            coord,
            target,
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "L4M",
                    "slug": "l4_master",
                    "title": "Conformance master",
                    "kind": "master",
                    "repo": REPO,
                    "type": "fix",
                    "createdAt": "2026-01-01T00:00",
                    "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
                }
            ),
        )
        create(
            steps=[
                {
                    "id": "S1",
                    "title": "One",
                    "status": "done",
                    "note": "top note",
                    "substeps": [{"id": "C1", "title": "Child", "status": "pending"}],
                }
            ]
        )

        control = task_doc_payload(coord, target, operation="get")
        self.assertTrue(control["ok"])
        self.assertNotIn("steps", control)

        payload = task_doc_payload(coord, target, operation="read_steps")
        self.assertIn("steps", payload, "fixture did not reach the emitting branch")
        self.assertEqual(["S1"], [step["id"] for step in payload["steps"]])
        self.assertEqual(["C1"], [sub["id"] for sub in payload["steps"][0]["substeps"]])
        response = TaskDocResponse.model_validate(payload)
        self.assertEqual("task_doc.read_steps", response.operation)

    def test_task_doc_description_and_refusal_name_the_same_kind_vocabulary(self) -> None:
        """T43 / T50's class: a description that advertises what the mechanism refuses.

        The published description is the load-bearing surface here because the schema cannot
        carry it -- ``kind`` is a free string in the request -- so a description that still
        advertises ``'light'`` sends a caller at a refusal. Both sides are asserted: the
        refusal names the accepted vocabulary, and the description names the same two words.
        """

        description = advertised_description("task_doc")
        self.assertNotIn("'light'|'subTask'|'master'", description)
        self.assertIn("kind ['subTask'|'master']", description)

        coord = self.world("tsip-l4-kind-")
        target = TaskDocTarget(repo_id=REPO, task_name="l4-kind", slug="l4_kind")

        with self.assertRaises(TaskDocError) as caught:
            task_doc_tool(
                coord,
                target,
                operation="create",
                edit=TaskDocEdit(
                    fields={
                        "id": "L4K",
                        "slug": "l4_kind",
                        "title": "Light refusal",
                        "kind": "light",
                        "repo": REPO,
                        "type": "fix",
                        "createdAt": "2026-01-01T00:00",
                    }
                ),
            )
        message = str(caught.exception)
        self.assertIn("light task documents are no longer supported", message)
        self.assertIn("['subTask', 'master']", message)

    # -- T8: worktree_operation_control / nextTool -------------------------------------

    def test_every_next_tool_its_own_refusals_advertise_is_declared(self) -> None:
        """T8: the narrowed ``nextTool`` Literal must carry what the producers emit.

        The value set is derived from the lifecycle integration package's own source, so a
        fourth successor cannot be added without this case seeing it -- that is the shape
        the defect had: two refusals emitted ``worktree_closeout_preview`` while the
        response model's three-value ``Literal`` forbade it, and the refusal was raised
        *after* the journal mutation, so the caller lost status, detail and next action.
        The refusal payload is assembled exactly as
        ``application/worktree_tools.py:683-696`` assembles it.
        """

        emitted: set[str] = set()
        for path in sorted(LIFECYCLE_PACKAGE.glob("*.py")):
            emitted |= literal_keyword_values(path, "next_tool")
        self.assertEqual(
            {
                "worktree_closeout_preview",
                "worktree_operation_control",
            },
            emitted,
            "the package's literal next_tool vocabulary changed",
        )

        for value in sorted(emitted):
            with self.subTest(next_tool=value):
                error = LifecycleControlError(
                    "closeout-candidate-state-moved",
                    "closeout contract bytes changed after operation admission",
                    expected={"candidateState": "a" * 64},
                    observed={"candidateState": "b" * 64},
                    next_action="retry-closeout-preview",
                    next_tool=value,
                    next_args={"contract_path": "/scratch/series-contract.md"},
                )
                fields = error.response_fields(
                    contract_path="/scratch/series-contract.md",
                    kind="closeout",
                    generation=1,
                )
                self.assertEqual(value, fields["nextTool"], "the guarded branch did not fire")
                refusal: dict[str, object] = {
                    "ok": False,
                    "operation": "worktree_operation_control",
                    "state": "refused",
                    "status": error.status,
                    "detail": error.detail,
                }
                refusal.update(fields)
                payload = _tool_payload("worktree_operation_control", refusal)
                response = WorktreeOperationControlResponse.model_validate(payload)
                self.assertEqual(value, response.nextTool)

    # -- T15: operator_inbox_post / the refusal that could never validate --------------

    def test_operator_inbox_post_sprint_owner_refusal_is_a_typed_payload(self) -> None:
        """T15: the only refusal branch of this tool could not validate under any input.

        It returned ``{ok, operation, status}``: ``status`` was not declared and ``entryId``,
        ``state``, ``messageKind`` and ``deliveryState`` are required. The firing state is a
        ``decision-item`` with a catalog present and no resolvable owner -- a routable
        message on the same world is the control that proves the branch, not the harness, is
        what produced the refusal.
        """

        coord = self.world("tsip-l4-inbox-")
        catalog = TerminalCatalog(terminal_catalog_path(coord.coordination_root))
        catalog.upsert(_entry("worker-1"))
        poster = InboxPoster(created_by="tsip", created_via="cli", sender_agent_id=None)

        decision = InboxMessage(
            ask="a decision is needed",
            response="the sprint owner must rule",
            message_kind="decision-item",
        )
        produced = operator_inbox_post_tool(
            coord, address=InboxAddress(), message=decision, poster=poster
        )
        self.assertEqual("sprint-owner-required", produced["status"], "refusal branch did not fire")
        self.assertFalse(produced["ok"])

        payload = operator_inbox_post_payload(
            coord, address=InboxAddress(), message=decision, poster=poster
        )
        response = OperatorInboxPostResponse.model_validate(payload)
        self.assertEqual("sprint-owner-required", response.status)
        self.assertIsNone(response.entryId)
        self.assertEqual("decision-item", response.messageKind)

    def test_a_queued_operator_inbox_post_still_must_report_its_entry(self) -> None:
        """The conditional requirement's other half, so the refusal cannot hollow it out.

        Making the four fields optional would have let a *successful* post omit the entry it
        queued -- trading a refusal that could never be returned for a success that cannot
        be distinguished from one that queued nothing. The validator is what keeps the
        success contract as strict as it was.
        """

        with self.assertRaises(ValidationError) as caught:
            OperatorInboxPostResponse.model_validate(
                {"ok": True, "operation": "operator_inbox_post", "status": "queued"}
            )
        self.assertIn("must report entryId", str(caught.exception))
        self.assertIn("deliveryState", str(caught.exception))

    # -- T16: session_retire / the stranded-row report ---------------------------------

    def _retirement_world(self, prefix: str) -> McpRuntimeConfig:
        coord = self.world(prefix)
        catalog = TerminalCatalog(terminal_catalog_path(coord.coordination_root))
        catalog.upsert(
            replace(_entry("actor-1"), spawn_role="orchestrator", seat_role="orchestrator")
        )
        catalog.upsert(_entry("target-1"))
        store = OperatorInboxStore(observer_root(coord))
        store.append(
            create_operator_inbox_entry(
                InboxMessage(ask="queued for the seat about to retire", response="pending work"),
                entry_id="stranded-row-1",
                now="2026-09-18T10:00:00+00:00",
                routing=InboxRouting(address=InboxAddress(agent_id="target-1")),
                poster=InboxPoster(created_by="tsip", created_via="cli", sender_agent_id="actor-1"),
            )
        )
        return coord

    def test_session_retire_reports_the_stranded_row_after_the_seat_is_gone(self) -> None:
        """T16: the seat is retired, then the caller got a schema error instead of the report.

        Each arm runs on its own world because retirement is irreversible: on a reused world
        the second call takes ``already-retired`` and carries none of the three keys, which
        is how a naive harness reports a false clean. The witness is that the row really was
        surfaced -- ``strandedRowIds`` non-empty -- and the catalog really did flip.
        """

        coord = self._retirement_world("tsip-l4-retire-")
        payload = terminal_payload_tools.session_retire_payload(
            coord, actor_session_id="actor-1", session_id="target-1", host=_FakeHost()
        )

        self.assertEqual("retired", payload["status"], "the guarded branch did not fire")
        self.assertEqual(["stranded-row-1"], payload["strandedRowIds"], "no row was surfaced")
        self.assertEqual(1, payload["strandedRowCount"])
        response = SessionRetireResponse.model_validate(payload)
        retired = TerminalCatalog(terminal_catalog_path(coord.coordination_root)).get("target-1")
        self.assertIsNotNone(retired)
        assert retired is not None  # created above; retirement terminates it
        self.assertEqual("terminated", retired.status)
        self.assertEqual(["stranded-row-1"], response.strandedRowIds)

        retry = terminal_payload_tools.session_retire_payload(
            coord, actor_session_id="actor-1", session_id="target-1", host=_FakeHost()
        )
        self.assertEqual("already-retired", retry["status"])
        self.assertEqual([], retry.get("strandedRowIds", []))
        SessionRetireResponse.model_validate(retry)

    # -- T4: the shared structural delivery projection ---------------------------------

    def test_the_structural_delivery_projection_is_declared_on_every_consumer(self) -> None:
        """T4: one producer, six consumers, three of which did not declare its keys.

        ``structural_payload`` adds ``deliveryState`` and ``adapterDeliveryState`` whenever
        the outcome carries them. ``retire_child``, ``rename_child`` and ``rename_self``
        reach the producer through call sites that pass no delivery state today, so the keys
        are latent rather than live -- but the producer *can* emit them, and a model that
        forbids a key its own producer can emit is D53's shape. The witness is the guard:
        the outcome below carries both states, and the case fails if the producer drops
        them, which is the only way this could pass vacuously.
        """

        outcome = StructuralOutcome(
            operation="rename_self",
            ok=True,
            status="renamed",
            document=None,
            role="worker",
            detail=None,
            delivery_state="delivered",
            adapter_delivery_state="accepted",
        )
        emitted = structural_payload(outcome)
        self.assertIn("deliveryState", emitted, "fixture did not reach the emitting branch")
        self.assertIn("adapterDeliveryState", emitted, "fixture did not reach the emitting branch")

        for model in (RetireChildResponse, RenameChildResponse, RenameSelfResponse):
            with self.subTest(model=model.__name__):
                declared = set(model.model_fields)
                self.assertEqual(set(), {"deliveryState", "adapterDeliveryState"} - declared)
                response = model.model_validate(
                    {
                        **emitted,
                        "operation": model.model_fields["operation"].default,
                    }
                )
                self.assertEqual("delivered", response.deliveryState)
                self.assertEqual("accepted", response.adapterDeliveryState)
