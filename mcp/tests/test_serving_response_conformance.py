"""Conformance tests for the declared HTTP response contract (``serving.response_contract``).

The sibling of ``test_served_state_conformance.py``, widened from one route to all 61. That
suite proved ``/api/state``'s assembled body validates against ``ServedWorkspaceProjection``;
this one does the same job for every other route, against the model that route now declares.

**Why the declaration cannot be the gate, and this file has to be.** FastAPI applies
``response_model`` only to values it serializes itself -- a handler that returns a ``Response``
instance is handed back untouched and never reaches ``serialize_response``. 57 of the 61
handlers do exactly that and two more are async-generator SSE routes, so on 59 of them the
decorator buys an OpenAPI schema and validates nothing at runtime. A suite that only asserted
"every route declares a model" would have gone green the moment the decorators landed and
would have caught no drift on those 59 routes ever.

So the tests below **drive the real routes and validate what actually came back**:

* :class:`ServingRouteInventoryTests` -- no HTTP route may lack a declaration, and no
  registration form may escape the walk. The websocket is exempt *by route class*, not by a
  path skip-list: an ``APIWebSocketRoute`` has no ``response_model`` attribute at all, and the
  test asserts that, so the exemption cannot quietly widen to cover a future undeclared HTTP
  route. The walk happens inside a started app, because the lifespan can register routes.
* :class:`RouteWalkerTests` -- each registration form FastAPI accepts, registered and then
  served against a throwaway app, so the inventory's "these are all the routes" clause is
  itself under test rather than assumed.
* :class:`ValidatedRouteHazardTests` -- the two routes FastAPI genuinely validates, where a
  drifted payload is a runtime 500 and not a red test, so their producer's key set is asserted
  against the model's directly.
* :class:`ServingResponseConformanceTests` -- one real request per route through the real app,
  each answer validated against the model declared *for the status that actually came back*
  (``responses[status]["model"]`` when there is one, ``response_model`` otherwise). Every model
  is ``extra="forbid"``, so an undeclared key fails -- and validation is alias-strict, so a
  body that arrived in field-name form fails too (see :func:`validate_wire`).
* :class:`ConversationSuccessConformanceTests` -- real 200/202 bodies off a real control
  bridge for the conversation surface, whose success shapes no refusal path can reach, plus
  the library/open bodies, which need a conversation key this app's own authority will sign.
* :class:`ConversationCompositionRefusalTests` -- the one control refusal ``create_app``
  cannot produce, because it always composes a complete runtime.
* :class:`StreamContractTests` -- the branches a body-shaped model cannot express: the bare
  ``304``, the SSE frames off the generators, and both SSE routes over a real socket.
* :class:`DeclaredSurfaceCoverageTests` -- the score. Every declared ``(method, path, status)``
  is either driven above or listed in :data:`UNDRIVEN_DECLARATIONS` with a reason, asserted
  exactly, so an undriven declaration is a counted number rather than a discovery.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast, get_args
from unittest import mock

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import httpx
import uvicorn
from _control_plane import OPERATOR, FakeControlAdapter, drive_activity, make_harness
from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.mcp.config import McpRuntimeConfig, RepositoryScope
from agents_remember.observer.projection import LifecycleProjection
from agents_remember.serving.app import ServingCollaborators, create_app, stream_events
from agents_remember.serving.conversation.control.api import router as control_router
from agents_remember.serving.conversation.library.api import _OPEN_STATUS_BY_OUTCOME
from agents_remember.serving.conversation.library.factories import library_shared
from agents_remember.serving.conversation.library.scope import canonical_library_scope
from agents_remember.serving.conversation.models import OpenConversationOperation
from agents_remember.serving.delta import DeltaEvent
from agents_remember.serving.events import stream_raw_events
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.harness_control_models import (
    ReconciliationResult,
    SubmissionAuthorityDescriptor,
    SubmissionLookup,
    SubmissionReceipt,
    SubmissionStatus,
    SubmissionStatusBatch,
    WithdrawalResult,
)
from agents_remember.serving.projector import ProjectionCadence, Projector
from agents_remember.serving.response_contract import TerminalCatalogEntryWire
from agents_remember.serving.served_state import SERVED_TAIL_FIELDS
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    TerminalCatalogEntry,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_pty import TerminalSession
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.worktree_contract import WorktreeContract, write_contract
from pydantic import TypeAdapter, ValidationError

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


# --- route index -----------------------------------------------------------------------------


class WalkedRoute(NamedTuple):
    """One dispatchable route and the full path it answers on, prefixes already applied."""

    path: str
    route: Any


def walk_routes(routes: Any, prefix: str = "") -> Iterator[WalkedRoute]:
    """Every route the app can dispatch, through every registration form it accepts.

    Four forms reach ``app.routes``, and a walker that models only one of them is a hole in
    every assertion built on top of it:

    * ``include_router`` -- FastAPI keeps the included ``APIRouter`` behind one opaque
      ``_IncludedRouter`` and resolves it at dispatch time. The 25 conversation routes live
      inside one, so a test reading ``app.routes`` alone would see 36 of the 61 and would have
      declared victory over a surface it never looked at. ``include_context.prefix`` is the
      prefix that router was mounted under and the inner ``route.path`` does **not** carry it:
      reporting the bare path would key the index -- and the websocket path assertion -- on a
      path the app does not serve.
    * ``app.mount`` -- a starlette ``Mount`` whose ``.routes`` is the mounted app's own route
      table. Not hypothetical: ``serving/static.py`` mounts the dashboard bundle at ``/``, so a
      sub-application is an established registration form in the very module this inventory
      claims to cover.
    * ``app.router.add_route`` / ``app.add_api_route`` -- plain entries in ``app.routes``. The
      walker yields them; :class:`ServingRouteInventoryTests` is what refuses to ignore a kind
      it does not model.
    * anything registered from inside the lifespan, which is why the inventory walks a
      **started** app rather than the freshly constructed one.
    """

    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            # An ``_IncludedRouter`` dispatches nothing itself -- it is a prefix plus children.
            yield from walk_routes(inner.routes, prefix + route.include_context.prefix)
            continue
        path = prefix + getattr(route, "path", "")
        yield WalkedRoute(path, route)
        if isinstance(route, Mount):
            yield from walk_routes(route.routes, path)


def route_index(app: FastAPI) -> dict[tuple[str, str], APIRoute]:
    """Every HTTP route keyed by ``(METHOD, path)``."""

    index: dict[tuple[str, str], APIRoute] = {}
    for walked in walk_routes(app.routes):
        if isinstance(walked.route, APIRoute):
            for method in walked.route.methods or ():
                index[(method, walked.path)] = walked.route
    return index


def declared_model(route: APIRoute, status: int) -> Any:
    """The model this route declares for exactly this status.

    A ``responses`` entry wins when there is one -- that is the whole point of declaring the
    refusal shapes separately from the success shape.
    """

    entry = route.responses.get(status)
    if entry is not None and "model" in entry:
        return entry["model"]
    return route.response_model


def declared_statuses(route: APIRoute) -> set[int]:
    """Every status this route declares a body for: the success one plus its ``responses``."""

    statuses = {int(status) for status in route.responses}
    if route.response_model is not None:
        statuses.add(route.status_code or 200)
    return statuses


def declared_pairs(app: FastAPI) -> set[tuple[str, str, int]]:
    """The whole declared contract as ``(method, path, status)`` triples.

    This is the denominator :class:`DeclaredSurfaceCoverageTests` measures the driven table
    against. A declaration nothing drives enforces nothing, so the size of this set minus the
    size of what the suite drives is the honest score of this file.
    """

    return {
        (method, walked.path, status)
        for walked in walk_routes(app.routes)
        if isinstance(walked.route, APIRoute)
        for method in walked.route.methods or ()
        for status in declared_statuses(walked.route)
    }


def validate_wire(model: Any, body: Any) -> Any:
    """Validate a body against its declaration **in alias form only**.

    This is the axis a plain ``TypeAdapter(...).validate_python(body)`` cannot see. Both
    ``WireResponse`` and ``conversation/models.WireModel`` set ``populate_by_name=True``, so
    validation accepts ``identity_digest`` exactly as happily as ``identityDigest``: flip one
    handler's ``model_dump(by_alias=True)`` to ``by_alias=False`` and every key on that route
    goes snake_case -- a total break for the cockpit, which reads the camelCase names -- while
    every model still validates and the suite still reports all green.

    ``by_name=False`` closes it. Only the alias is accepted, so a key that arrived in
    field-name form is an undeclared key against ``extra="forbid"`` and fails here. The two
    neighbouring axes were never the blind ones: a *third* name fails as an extra key either
    way, and a single-word field has no alias to diverge from.

    Models with no alias generator at all (``HttpDetailRefusal``, and the non-wire models the
    unions reach) are unaffected -- with no alias to prefer, pydantic still matches the field
    name, which for them IS the wire name.
    """

    return TypeAdapter(model).validate_python(body, by_alias=True, by_name=False)


def field_name_form(value: Any) -> Any:
    """The same body with every camelCase key rewritten to the field name it aliases.

    Used to *prove* :func:`validate_wire` is load-bearing rather than decorative: the rewritten
    body still validates the old way and must not validate this way.
    """

    if isinstance(value, dict):
        return {
            re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower(): field_name_form(inner)
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [field_name_form(item) for item in value]
    return value


# --- what the table actually drove ------------------------------------------------------------
#
# Every ``_check`` in this file records the ``(method, path, status)`` it drove here, and
# :class:`DeclaredSurfaceCoverageTests` measures that against :func:`declared_pairs`. Before
# this existed the driving tests kept a ``self.checked`` set that nothing ever read: 88 of the
# 286 declared pairs were driven, the other 198 enforced nothing, and no test said so. Seven
# declared models could be made mathematically unsatisfiable -- a required ``str`` retyped to
# ``int`` -- and the suite stayed green.

DRIVEN: set[tuple[str, str, int]] = set()
"""Filled as the driving tests run; read by the coverage test."""

COMPLETED: set[str] = set()
"""``Class.test_method`` for every driving test that has finished in this process."""


@contextlib.asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """The app on a real uvicorn, as a loopback client.

    ``TestClient`` cannot drive an SSE route: the stream never ends, so a read from inside the
    portal thread cannot be closed from outside it. A real socket can be, which is what lets
    ``/api/stream`` and ``/api/events`` be conformance-checked as the *routes* they are and
    not only as the generators behind them.
    """

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", access_log=False)
    )
    task = asyncio.create_task(server.serve())
    while not server.started:  # pragma: no branch - one spin in practice
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")
    try:
        yield client
    finally:
        await client.aclose()
        server.should_exit = True
        server.force_exit = True
        await task


async def first_sse_frame(response: httpx.Response) -> tuple[str, Any]:
    """The first complete ``event:``/``data:`` frame off a live event stream."""

    event = ""
    async for line in response.aiter_lines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            return event, json.loads(line.removeprefix("data:").strip())
    raise AssertionError("the stream closed before it framed anything")


# --- fixtures --------------------------------------------------------------------------------


def _config(tmp: Path, **repos: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
        repositories={
            name: RepositoryScope(repo_id=name, path=path) for name, path in repos.items()
        },
    )


class _LivePaneHost:
    """A terminal host whose panes are all alive, so the liveness sweep keeps its rows.

    Without it ``GET /api/terminal/sessions`` sweeps every seeded row away and the one route
    FastAPI genuinely validates would be conformance-tested against an empty list. The
    signatures mirror ``TerminalHost``'s exactly -- an argument this double ignores is still
    an argument production passes, so it stays named.
    """

    def __init__(self) -> None:
        self.terminated: list[str] = []

    def get(self, session_id: str) -> TerminalSession | None:  # noqa: ARG002
        return None

    def has_session(self, tmux_name: str) -> bool:  # noqa: ARG002
        return True

    def open(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        raise AssertionError("the conformance fixture never spawns a real pane")

    def ensure(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        raise AssertionError("the conformance fixture never spawns a real pane")

    def terminate(self, session_id: str, *, tmux_name: str | None = None) -> None:  # noqa: ARG002
        self.terminated.append(session_id)

    def shutdown(self) -> None:
        return None


def _entry(session: str, tmp: Path, **overrides: Any) -> TerminalCatalogEntry:
    fields: dict[str, Any] = {
        "id": session,
        "label": "Worker",
        "kind": "harness",
        "harness": "claude",
        "lifecycle_id": "L1",
        "cwd": tmp,
        "tmux_name": f"ar-{session}",
        "command": ("claude",),
        "created_at": "2026-06-14T10:00:00+00:00",
        "last_attached_at": "2026-06-14T10:05:00+00:00",
        "status": "running",
    }
    fields.update(overrides)
    return TerminalCatalogEntry(**fields)


def _make_repo(root: Path) -> None:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "README.md").write_text("# repo\n", encoding="utf-8")
    onboarding = root / "ar-memory" / "onboarding" / "pkg"
    onboarding.mkdir(parents=True, exist_ok=True)
    (onboarding / "mod.py.md").write_text(
        "# mod.py\n\n| key | value |\n| --- | --- |\n"
        "| lastVerifiedCommitHash | abc1234 |\n| lastVerifiedCommitDate | 2026-06-01 |\n",
        encoding="utf-8",
    )
    (root / "ar-memory" / "onboarding" / "overview.md").write_text("# overview\n", encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repo),
            "GIT_AUTHOR_NAME": "T",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "T",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


def _seed_changeset(tmp: Path, code: Path) -> None:
    """A real git enclosure so the change-set routes answer with real diffs, not refusals."""

    _git(code, "init", "-q", "-b", "main")
    (code / "f.py").write_text("one\n", encoding="utf-8")
    _git(code, "add", "-A")
    _git(code, "commit", "-qm", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=code, check=True, capture_output=True, text=True
    ).stdout.strip()
    (code / "f.py").write_text("one\ntwo\n", encoding="utf-8")
    contract_path = tmp / "tasks" / "R" / "t" / "enclosures" / "leaf-1" / "series-contract.md"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    write_contract(
        contract_path,
        WorktreeContract(
            task_id="T",
            task_name="t",
            repo_name="R",
            workflow_kind="light-task",
            memory_mode="disabled",
            coordination_root=tmp,
            task_root=tmp / "tasks" / "R" / "t",
            contract_path=contract_path,
            task_artifact=tmp / "tasks" / "R" / "t" / "task.md",
            worktree_group=contract_path.parent,
            code_repo_path=code,
            code_source_branch="main",
            code_work_branch="work",
            code_base_commit=base,
            code_worktree=code,
            kind="leaf",
            leaf_id="leaf-1",
            parent_task_name="t",
        ),
    )


def _seed_task_doc(tmp: Path) -> None:
    """A real master + leaf pair, so a leaf REF resolves and the document route has a body."""

    task_root = tmp / "tasks" / "R" / "t"
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "T",
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "repo": "R",
                "createdAt": "2026-07-31T10:00",
                "subTasks": [
                    {
                        "number": "leaf-1",
                        "name": "Leaf",
                        "file": "leaf-1.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "leaf-1",
                "slug": "leaf-1",
                "title": "The leaf under test",
                "kind": "subTask",
                "repo": "R",
                "createdAt": "2026-07-31T10:01",
                "master": "task.md",
                "objective": "Give the conformance suite a real document to serve.",
                "requirements": ["the declared contract is exercised against a real body"],
            }
        ),
    )


def _seed_notes(tmp: Path) -> None:
    notes = tmp / "tasks" / "R" / "t" / "notes"
    (notes / "reports").mkdir(parents=True, exist_ok=True)
    (notes / "design.md").write_text("# design\n", encoding="utf-8")
    (notes / "reports" / "worker.md").write_text("# report\n", encoding="utf-8")


# --- inventory --------------------------------------------------------------------------------


class ServingRouteInventoryTests(unittest.TestCase):
    """Nothing on the HTTP surface may be undeclared, and the one exemption is structural."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        # The walk happens inside a STARTED app. ``add_api_route`` is legal from the lifespan,
        # and a walk in ``setUp`` alone runs before startup: such a route would serve real
        # traffic while every assertion below passed without ever having seen it.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(TestClient(self.app))
        self.walked = list(walk_routes(self.app.routes))
        self.http = [w for w in self.walked if isinstance(w.route, APIRoute)]

    def test_every_http_route_declares_a_response_model(self) -> None:
        undeclared = [
            f"{sorted(walked.route.methods or ())} {walked.path}"
            for walked in self.http
            if walked.route.response_model is None
        ]
        self.assertEqual(undeclared, [])

    def test_the_websocket_is_exempt_because_it_structurally_cannot_declare_one(self) -> None:
        # The exemption is not "we skipped this path". An ``APIWebSocketRoute`` has no
        # ``response_model`` slot at all, because a websocket has no response body -- it
        # upgrades and then frames bytes both ways. Asserting the ABSENCE of the attribute is
        # what stops this from becoming a skip-list that would swallow the next undeclared
        # HTTP route.
        sockets = [w for w in self.walked if isinstance(w.route, APIWebSocketRoute)]
        self.assertEqual([w.path for w in sockets], ["/api/terminal/{session}"])
        for socket in sockets:
            self.assertFalse(hasattr(socket.route, "response_model"))
            self.assertNotIsInstance(socket.route, APIRoute)

    def test_the_declared_surface_is_the_whole_surface(self) -> None:
        # 62 route decorators: 61 HTTP + 1 websocket. Pinned so a new route cannot be added
        # without this suite being told to exercise it.
        sockets = [w for w in self.walked if isinstance(w.route, APIWebSocketRoute)]
        self.assertEqual(len(self.http), 61)
        self.assertEqual(len(sockets), 1)

    def test_no_registration_form_escapes_the_walker(self) -> None:
        # The counting assertions above are only as wide as the kinds the walker models. A
        # route registered as a plain starlette ``Route`` -- ``app.router.add_route(...)`` --
        # is neither an ``APIRoute`` nor an ``APIWebSocketRoute``, so it would serve HTTP 200
        # JSON while every ``isinstance(..., APIRoute)`` filter above stepped straight over it.
        # Refusing a kind this file does not model is what makes those filters total.
        #
        # FastAPI's own documentation routes are plain ``Route``s registered by ``FastAPI``
        # itself, so they are excluded by the URLs the app reports for them -- derived from the
        # app, never a hard-coded path list.
        generated = {
            self.app.openapi_url,
            self.app.docs_url,
            self.app.redoc_url,
            self.app.swagger_ui_oauth2_redirect_url,
        }
        unmodelled = [
            f"{type(walked.route).__name__} {walked.path}"
            for walked in self.walked
            if not isinstance(walked.route, (APIRoute, APIWebSocketRoute, Mount))
            and walked.path not in generated
        ]
        self.assertEqual(unmodelled, [])

    def test_the_mounted_surface_is_pinned(self) -> None:
        # A ``Mount`` is the one form whose interior this file cannot always enumerate: a bare
        # ASGI callable has no ``routes`` to read. The walker descends when there is something
        # to descend into (a mounted ``FastAPI`` app is walked like any other), and pinning the
        # mount list is what makes the remaining case a deliberate act rather than a blind spot
        # -- ``serving/static.py`` already mounts at ``/``, so this form is in live use here.
        mounts = sorted(walked.path for walked in self.walked if isinstance(walked.route, Mount))
        self.assertEqual(mounts, [""])

    def test_every_declared_refusal_status_names_a_model(self) -> None:
        # A ``responses`` entry that documents a status without a model would let a refusal
        # shape drift unchecked while still looking declared.
        #
        # Carrying a ``content`` key does not excuse it. This check used to skip any entry with
        # one, which meant a future ``responses={409: {"content": {...}}}`` -- a refusal status
        # naming a media type and no shape -- passed silently, and ``declared_model`` would
        # then fall back to the route's SUCCESS model for that status. The one legitimate
        # modelless-with-content entry is the SSE media type on the route's own success status,
        # where ``response_model`` really is the shape of a frame's ``data``, so that is the
        # exemption spelled out, and nothing wider.
        bare = [
            f"{walked.path} {status}"
            for walked in self.http
            for status, entry in walked.route.responses.items()
            if "model" not in entry
            and status != 304
            and not (
                list(entry.get("content", {})) == ["text/event-stream"]
                and status == (walked.route.status_code or 200)
                and walked.route.response_model is not None
            )
        ]
        self.assertEqual(bare, [])

    def test_a_modelless_responses_entry_is_a_304_or_a_declared_sse_media_type(self) -> None:
        # Exactly which entries the check above lets through, named. Two kinds earn it and
        # nothing else does:
        #
        #   * ``/api/state``'s 304 -- a conditional GET answers with no body at all, so there
        #     is nothing to model;
        #   * the three SSE entries, which exist to name ``text/event-stream`` as the media
        #     type. Each route still declares the model of one frame's ``data``, which is what
        #     the ``response_model`` assertion below re-checks -- so these are declared, not
        #     exempt.
        modelless = sorted(
            (walked.path, status)
            for walked in self.http
            for status, entry in walked.route.responses.items()
            if "model" not in entry
        )
        self.assertEqual(
            modelless,
            [
                ("/api/events", 200),
                ("/api/state", 304),
                ("/api/stream", 200),
                ("/api/terminal/{ar_session_id}/conversation/events", 200),
            ],
        )
        for walked in self.http:
            for status, entry in walked.route.responses.items():
                if "model" in entry or status == 304:
                    continue
                self.assertEqual(list(entry["content"]), ["text/event-stream"], walked.path)
                self.assertIsNotNone(walked.route.response_model, walked.path)


class ValidatedRouteHazardTests(unittest.TestCase):
    """The two routes FastAPI validates for real, and the one place that is a hazard.

    ``GET /api/terminal/sessions`` and ``GET /api/harnesses`` return a bare ``dict``, so unlike
    the other 59 they are validated at runtime -- and a payload that gains a key, loses a
    required one or changes a type is answered as **HTTP 500**, not passed through as it was
    before these routes declared a model. On ``/api/harnesses`` that is three required keys
    written by one function. On ``/api/terminal/sessions`` it is a 52-key body assembled by
    hand from a 36-optional-field dataclass that is actively grown, and a leaf that adds a
    field to ``to_json`` and forgets ``TerminalCatalogEntryWire`` takes the cockpit's session
    list down rather than degrading it.

    So the producer's key set is asserted against the model's directly. This fires when the
    field is added -- earlier than the runtime 500, and earlier than a conformance run, which
    only sees the fields its fixture happens to populate.
    """

    def _emitted_keys(self) -> set[str]:
        """Every key ``TerminalCatalogEntry.to_json`` can write, read off its own source.

        An AST scan and not a fully-populated instance, because ``to_json`` is *conditional*:
        every optional key goes through ``_present_fields`` and is absent when ``None``, so no
        single constructed entry proves the full key set. The scan reads exactly the two forms
        that method uses -- string keys of a dict literal, and ``data["x"] = ...``.
        """

        source = (MCP_SRC / "agents_remember" / "serving" / "terminal_catalog.py").read_text()
        entry = next(
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.ClassDef) and node.name == "TerminalCatalogEntry"
        )
        to_json = next(
            node
            for node in entry.body
            if isinstance(node, ast.FunctionDef) and node.name == "to_json"
        )
        keys: set[str] = set()
        for node in ast.walk(to_json):
            if isinstance(node, ast.Dict):
                keys.update(
                    key.value
                    for key in node.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "data"
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)
                    ):
                        keys.add(target.slice.value)
        return keys

    def test_the_catalog_wire_model_covers_every_key_to_json_emits(self) -> None:
        declared = {
            field.alias or name for name, field in TerminalCatalogEntryWire.model_fields.items()
        }
        emitted = self._emitted_keys()
        # Set EQUALITY in both directions. A key the producer writes and the model does not
        # declare is the 500; a key the model declares and the producer cannot write is a
        # contract that describes a body nothing emits.
        self.assertEqual(sorted(emitted - declared), [])
        self.assertEqual(sorted(declared - emitted), [])
        # Pinned, because the scan reading zero keys would satisfy the equality above.
        self.assertEqual(len(emitted), 52)


class RouteWalkerTests(unittest.TestCase):
    """Every registration form that can serve a body must be visible to the walker.

    The inventory above is an argument of the form "these are all the routes, and all of them
    declare a model". Its first clause is a claim about :func:`walk_routes`, and a registration
    form the walker steps over makes the whole argument vacuous for that route -- it serves
    real traffic and no assertion in this file has an opinion about it. So each form gets
    driven here against a throwaway app: registered, then *served*, then found by the walker at
    the path it actually answered on.
    """

    def _walk(self, app: FastAPI) -> dict[str, Any]:
        with TestClient(app):
            return {
                walked.path: walked.route
                for walked in walk_routes(app.routes)
                if isinstance(walked.route, (APIRoute, Route))
            }

    def test_an_included_router_is_reported_at_its_prefixed_path(self) -> None:
        # The latent one: ``include_router(prefix=...)`` leaves the inner ``route.path``
        # unprefixed, so a walker that reported it would key the route index -- and the
        # websocket path assertion -- on a path the app does not serve.
        inner = APIRouter()

        @inner.get("/leaf", response_model=None)
        def leaf() -> JSONResponse:
            return JSONResponse({"ok": True})

        outer = APIRouter()
        outer.include_router(inner, prefix="/nested")
        app = FastAPI()
        app.include_router(outer, prefix="/api/pre")
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/pre/nested/leaf").status_code, 200)
        self.assertIn("/api/pre/nested/leaf", self._walk(app))

    def test_a_mounted_sub_application_is_walked(self) -> None:
        sub = FastAPI()

        @sub.get("/thing", response_model=None)
        def thing() -> JSONResponse:
            return JSONResponse({"ok": True})

        app = FastAPI()
        app.mount("/api/plugin", sub)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/plugin/thing").status_code, 200)
        self.assertIn("/api/plugin/thing", self._walk(app))

    def test_a_plain_starlette_route_is_walked_and_is_not_an_api_route(self) -> None:
        # ``add_route`` serves HTTP 200 JSON while carrying no ``response_model`` slot at all,
        # so the walker must surface it and the inventory must refuse the kind. Being visible
        # is not the same as being declared, and this route is the proof of the difference.
        async def plain(_request: Any) -> JSONResponse:
            return JSONResponse({"ok": True})

        app = FastAPI()
        app.router.add_route("/api/plain", plain, methods=["GET"])
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/plain").status_code, 200)
        walked = self._walk(app)
        self.assertIn("/api/plain", walked)
        self.assertNotIsInstance(walked["/api/plain"], APIRoute)

    def test_a_route_registered_inside_the_lifespan_is_walked_after_startup(self) -> None:
        @contextlib.asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            app.add_api_route("/api/late", late, methods=["GET"], response_model=None)
            yield

        def late() -> JSONResponse:
            return JSONResponse({"ok": True})

        app = FastAPI(lifespan=lifespan)
        self.assertNotIn("/api/late", {walked.path for walked in walk_routes(app.routes)})
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/late").status_code, 200)
            started = {walked.path for walked in walk_routes(app.routes)}
        self.assertIn("/api/late", started)


# --- the driven conformance table --------------------------------------------------------------


class ServingResponseConformanceTests(unittest.TestCase):
    """One real request per route; the answer must validate against what the route declares."""

    maxDiff = None

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        settings = agentic_settings_path(self.tmp)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            json.dumps({"orchestration": {"supervisor": {"enabled": False}}}), encoding="utf-8"
        )
        self.code = self.tmp / "ws" / "R"
        self.code.mkdir(parents=True, exist_ok=True)
        _make_repo(self.code)
        _seed_changeset(self.tmp, self.code)
        _seed_notes(self.tmp)
        _seed_task_doc(self.tmp)
        # A SECOND repo with no memory root at all. ``FileScope.onboarding_root`` is ``None``
        # only when ``resolve_coordination_context`` raises ``MissingMemoryError``, and that is
        # the sole input that reaches ``OnboardingPartnerNone`` -- the fifth of the five shapes
        # ``GET /api/files/onboarding`` declares, and one no repo carrying ``ar-memory/`` can
        # ever produce.
        self.bare = self.tmp / "ws" / "N"
        (self.bare / "pkg").mkdir(parents=True, exist_ok=True)
        (self.bare / "pkg" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.config = _config(self.tmp, R=self.code, N=self.bare)
        self.catalog = TerminalCatalog(terminal_catalog_path(self.tmp))
        # ``live`` carries a control endpoint: without one every control route answers 409
        # ``unsupported`` and the success half of that family would go unexercised.
        self.catalog.upsert(
            _entry(
                "live",
                self.tmp,
                control_endpoint=self.tmp / "control.sock",
                control_state="ready",
                control_protocol="ar-harness-control/v1",
            )
        )
        self.catalog.upsert(_entry("landed", self.tmp, status="landed"))
        self.catalog.upsert(_entry("plain", self.tmp, kind="terminal", harness=None))
        # A live harness seat with NO control endpoint -- the legacy shape, and the only input
        # that reaches ``/paste``'s 409 ``unsupported`` leg.
        self.catalog.upsert(_entry("legacy", self.tmp))
        self.host = _LivePaneHost()
        self.app = create_app(
            self.config,
            cadence=ProjectionCadence(interval=100),
            collaborators=ServingCollaborators(
                terminal_host=cast(Any, self.host), terminal_catalog=self.catalog
            ),
        )
        self.routes = route_index(self.app)

    def _client(self, *, peer: tuple[str, int] = ("127.0.0.1", 50000)) -> TestClient:
        """A LOOPBACK peer: conversation authorization is loopback-only by design, so the
        default ``testclient`` host would turn every conversation route into the same 403 and
        the refusal surface would never be exercised past its first gate.

        ``peer`` exists so the 403 leg can be driven deliberately, from a host that is not
        loopback -- see ``test_the_conversation_authorization_refusal_conforms``."""

        return TestClient(self.app, client=peer)

    def _check(
        self,
        client: TestClient,
        method: str,
        path: str,
        *,
        status: int,
        route: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Drive one route for real, then validate its body against the declared model."""

        key = (method, route or path)
        self.assertIn(key, self.routes, f"no such registered route: {key}")
        DRIVEN.add((method, route or path, status))
        response = client.request(method, path, **kwargs)
        self.assertEqual(response.status_code, status, f"{method} {path}: {response.text}")
        model = declared_model(self.routes[key], status)
        self.assertIsNotNone(model, f"{method} {path} declares nothing for {status}")
        body = response.json()
        try:
            validate_wire(model, body)
        except Exception as exc:  # pragma: no cover - only on a real contract breach
            raise AssertionError(
                f"{method} {path} -> {status} violates its contract: {exc}\n{body}"
            ) from exc
        return body

    def tearDown(self) -> None:
        COMPLETED.add(f"{type(self).__name__}.{self._testMethodName}")

    # -- the shapes a declaration named and nothing drove ---------------------------------

    def test_the_fifth_onboarding_shape_conforms(self) -> None:
        # ``GET /api/files/onboarding`` declares a five-member union.
        # ``OnboardingPartnerNone`` is the member no repo with a memory root can produce, so
        # it needs the second, memory-less repo -- without it this declaration was
        # unfalsifiable and the model could have been made unsatisfiable unnoticed.
        with self._client() as client:
            body = self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=200,
                params={"repo": "N", "path": "pkg/mod.py.md", "direction": "reverse"},
            )
        self.assertEqual(body["kind"], "none")

    def test_the_harness_paste_legs_conform(self) -> None:
        # ``/paste`` fans out on ``entry.kind``: a plain pane answers ``TerminalPaneDelivery``
        # (driven below), a protocol harness answers ``TerminalHarnessDelivery``, and the two
        # 409 refusals are ``TerminalHarnessRefusal``. Only the pane leg was ever driven, so
        # both harness models sat in a declared union that nothing could falsify.
        with self._client() as client:
            harness = self._check(
                client,
                "POST",
                "/api/terminal/live/paste",
                status=200,
                route="/api/terminal/{session}/paste",
                json={"text": "hello", "submit": True},
            )
            # No bridge is listening on the seeded endpoint, so the control client certifies
            # that nothing was written and the route answers its ``unconfirmed`` leg -- still
            # a real ``TerminalHarnessDelivery``, and the only one reachable without a bridge.
            self.assertEqual(harness["status"], "unconfirmed")
            draft = self._check(
                client,
                "POST",
                "/api/terminal/live/paste",
                status=409,
                route="/api/terminal/{session}/paste",
                json={"text": "hello"},
            )
            self.assertEqual(draft["status"], "draft-not-submitted")
            legacy = self._check(
                client,
                "POST",
                "/api/terminal/legacy/paste",
                status=409,
                route="/api/terminal/{session}/paste",
                json={"text": "hello", "submit": True},
            )
        self.assertEqual(legacy["status"], "unsupported")

    def test_the_terminal_open_legs_conform(self) -> None:
        # ``POST /api/terminal/{session}`` declared ``TerminalOpened`` and
        # ``TerminalLaunchConflict`` and the suite only ever drove its 400.
        with self._client() as client:
            opened = self._check(
                client,
                "POST",
                "/api/terminal/plain",
                status=200,
                route="/api/terminal/{session}",
                json={"kind": "terminal"},
            )
            self.assertEqual(opened["status"], "running")
            # The same request against a seat that is already running a *harness* is the
            # launch-selection conflict: same id, different launch identity.
            conflict = self._check(
                client,
                "POST",
                "/api/terminal/live",
                status=409,
                route="/api/terminal/{session}",
                json={"kind": "terminal"},
            )
        self.assertEqual(conflict["status"], "launch-selection-conflict")

    def test_the_conversation_authorization_refusal_conforms(self) -> None:
        # ``CONTROL_RESPONSES[403]`` is declared on all 17 conversation-control routes and no
        # test could reach it, because every client this suite builds is deliberately a
        # loopback peer. Authorization is loopback-ONLY, so a non-loopback peer is the input
        # -- and it refuses at the first gate, before any session lookup.
        with self._client(peer=("10.0.0.5", 5000)) as client:
            body = self._check(
                client,
                "GET",
                "/api/terminal/live/conversation",
                status=403,
                route="/api/terminal/{ar_session_id}/conversation",
                params={"expectedBridgeEpoch": "epoch-1"},
            )
        self.assertEqual(body["status"], "authorization-failed")

    def test_the_terminal_control_refusal_legs_conform(self) -> None:
        # Both members of ``SESSION_CONTROL_RESPONSES``' refusal surface, on every route that
        # shares the table: the 404 for a seat that is not there, and the 409
        # ``UnsupportedSeatRefusal`` for a live harness seat with no control endpoint. Only the
        # success half was driven, under a patched bridge -- which is the half a patched bridge
        # is *able* to reach, and exactly why the other half went unexercised.
        payloads = [
            ("POST", "/set-model", {"model": "opus"}),
            ("POST", "/set-effort", {"effort": "high"}),
            ("POST", "/submission-status", {"expectedBridgeEpoch": "e", "requestIds": ["r1"]}),
            ("POST", "/withdraw", {"expectedBridgeEpoch": "e", "requestId": "r1"}),
            ("POST", "/submit", {"requestId": "r1", "text": "go", "expectedBridgeEpoch": "e"}),
            ("POST", "/reconcile", {"requestId": "r1", "expectedBridgeEpoch": "e"}),
            (
                "POST",
                "/interaction-response",
                {"interactionId": "q1", "expectedBridgeEpoch": "e", "response": "allow"},
            ),
        ]
        with self._client() as client:
            for session, status in (("ghost", 404), ("legacy", 409)):
                self._check(
                    client,
                    "GET",
                    f"/api/terminal/{session}/submission-authority",
                    status=status,
                    route="/api/terminal/{session}/submission-authority",
                )
                self._check(
                    client,
                    "GET",
                    f"/api/terminal/{session}/capabilities",
                    status=status,
                    route="/api/terminal/{session}/capabilities",
                )
                for method, suffix, payload in payloads:
                    self._check(
                        client,
                        method,
                        f"/api/terminal/{session}{suffix}",
                        status=status,
                        route=f"/api/terminal/{{session}}{suffix}",
                        json=payload,
                    )

    def test_the_remaining_terminal_refusal_legs_conform(self) -> None:
        with self._client() as client:
            # ``/image`` 413: the cap is enforced on the read, not only on Content-Length.
            self._check(
                client,
                "POST",
                "/api/terminal/live/image",
                status=413,
                route="/api/terminal/{session}/image",
                files={"file": ("big.png", b"\x89PNG" + b"0" * (5 * 1024 * 1024), "image/png")},
            )
            # ``/attach-leaf`` 400: a hand-opened harness seat carries no spawn role, so
            # there is no seat role to bind the leaf to and the request supplied none.
            refused = self._check(
                client,
                "POST",
                "/api/terminal/legacy/attach-leaf",
                status=400,
                route="/api/terminal/{session}/attach-leaf",
                json={"leafKey": "R/t/leaf-1"},
            )
            self.assertEqual(refused["status"], "role-required")
            # ``/attach-leaf`` 409: the same leaf and role, already held by another live seat.
            self._check(
                client,
                "POST",
                "/api/terminal/live/attach-leaf",
                status=200,
                route="/api/terminal/{session}/attach-leaf",
                json={"leafKey": "R/t/leaf-1", "role": "worker"},
            )
            taken = self._check(
                client,
                "POST",
                "/api/terminal/legacy/attach-leaf",
                status=409,
                route="/api/terminal/{session}/attach-leaf",
                json={"leafKey": "R/t/leaf-1", "role": "worker"},
            )
        self.assertEqual(taken["status"], "leaf-taken")

    def test_the_scoped_read_refusal_legs_conform(self) -> None:
        # The files / notes / change-set family shares one two-status refusal idiom, and half
        # of it was declared on routes no test ever refused.
        with self._client() as client:
            self._check(
                client,
                "GET",
                "/api/files/read",
                status=400,
                params={"repo": "R", "path": "../escape"},
            )
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=400,
                params={"repo": "R", "path": "../escape"},
            )
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=404,
                params={"repo": "ghost", "path": "x.py"},
            )
            self._check(client, "GET", "/api/notes/list", status=404, params={"repo": "ghost"})
            self._check(
                client,
                "GET",
                "/api/notes/list",
                status=400,
                params={"repo": "R", "master": "../escape"},
            )
            self._check(
                client,
                "GET",
                "/api/notes/read",
                status=400,
                params={"repo": "R", "master": "t", "path": "../escape"},
            )
            self._check(
                client,
                "GET",
                "/api/changeset/task",
                status=404,
                params={"repo": "R", "master": "t", "leaf": "ghost", "mode": "working"},
            )
            self._check(
                client,
                "GET",
                "/api/changeset/file-diff",
                status=404,
                params={
                    "repo": "ghost",
                    "master": "t",
                    "leaf": "leaf-1",
                    "mode": "working",
                    "kind": "code",
                    "path": "f.py",
                },
            )
            self._check(
                client,
                "GET",
                "/api/changeset/file-diff",
                status=400,
                params={
                    "repo": "R",
                    "master": "t",
                    "leaf": "leaf-1",
                    "mode": "working",
                    "kind": "code",
                    "path": "../escape",
                },
            )
            self._check(
                client,
                "POST",
                "/api/operator-inbox",
                status=400,
                json={"ask": "Continue?", "response": "Yes"},
            )

    # -- the wire is camelCase, and that is a thing a model alone cannot pin --------------

    def test_a_field_name_body_fails_the_declared_contract(self) -> None:
        """The camelCase wire is what this whole contract exists to hold.

        Nothing about a ``response_model`` pins it. ``populate_by_name=True`` -- set by both
        ``WireResponse`` and ``WireModel`` -- makes validation accept the field name as
        readily as the alias, so a handler that dumped ``by_alias=False`` would send the
        cockpit ``identity_digest`` where it reads ``identityDigest``, break every consumer,
        and validate cleanly against its own declaration.

        This drives a real route, rewrites its body into field-name form, and shows the two
        halves of that: the old check passes it, and :func:`validate_wire` does not.
        """

        with self._client() as client:
            body = self._check(client, "GET", "/api/terminal/sessions", status=200)
        model = declared_model(self.routes[("GET", "/api/terminal/sessions")], 200)
        renamed = field_name_form(body)
        # The route really does answer in alias form -- otherwise the rest proves nothing.
        self.assertNotEqual(renamed, body)
        self.assertIn("tmuxName", body["sessions"][0])
        self.assertIn("tmux_name", renamed["sessions"][0])
        # The blindness, demonstrated: the plain validation this suite used to do accepts it.
        TypeAdapter(model).validate_python(renamed)
        # And the check the suite does now does not.
        with self.assertRaises(ValidationError):
            validate_wire(model, renamed)

    def test_the_conversation_wire_is_pinned_to_camel_case_too(self) -> None:
        """The same axis on the surface that dumps ``by_alias=True`` by hand.

        The 25 conversation routes serialize with an explicit
        ``model_dump(mode="json", by_alias=True)``; flipping any one of those flags is a
        one-character change with no compiler and no model to stop it. Driving a refusal body
        is enough to pin the idiom -- ``_error``/``_envelope`` write the same camel keys the
        success dumps do.
        """

        with self._client() as client:
            body = self._check(
                client,
                "POST",
                "/api/terminal/ghost/conversation/interrupt",
                status=404,
                route="/api/terminal/{ar_session_id}/conversation/interrupt",
                params={"expectedBridgeEpoch": "epoch-1"},
                json={"turnId": "t1", "requestId": "r1"},
            )
        self.assertEqual(sorted(body), ["detail", "status"])

    # -- the read surface ----------------------------------------------------------------

    def test_projection_and_document_routes_conform(self) -> None:
        with self._client() as client:
            self._check(client, "GET", "/api/state", status=200)
            self._check(
                client,
                "GET",
                "/api/task-document",
                status=200,
                params={"path": "R/t/leaf-1.json"},
            )
            self._check(client, "GET", "/api/task-document", status=404, params={"path": "no.json"})

    def test_files_routes_conform(self) -> None:
        with self._client() as client:
            self._check(client, "GET", "/api/files/repos", status=200)
            self._check(client, "GET", "/api/files/list", status=200, params={"repo": "R"})
            self._check(
                client,
                "GET",
                "/api/files/read",
                status=200,
                params={"repo": "R", "path": "pkg/mod.py"},
            )
            # forward pairing: a paired file, and one with no sidecar
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=200,
                params={"repo": "R", "path": "pkg/mod.py"},
            )
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=200,
                params={"repo": "R", "path": "README.md"},
            )
            # reverse pairing: a sidecar, and a partnerless overview
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=200,
                params={"repo": "R", "path": "pkg/mod.py.md", "direction": "reverse"},
            )
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=200,
                params={"repo": "R", "path": "overview.md", "direction": "reverse"},
            )
            # and the refusal idiom the whole family shares
            self._check(client, "GET", "/api/files/list", status=404, params={"repo": "ghost"})
            self._check(
                client,
                "GET",
                "/api/files/read",
                status=404,
                params={"repo": "R", "path": "nope.py"},
            )
            self._check(
                client,
                "GET",
                "/api/files/list",
                status=400,
                params={"repo": "R", "path": "../escape"},
            )

    def test_notes_routes_conform(self) -> None:
        with self._client() as client:
            self._check(
                client, "GET", "/api/notes/list", status=200, params={"repo": "R", "master": "t"}
            )
            self._check(
                client,
                "GET",
                "/api/notes/read",
                status=200,
                params={"repo": "R", "master": "t", "path": "design.md"},
            )
            self._check(
                client,
                "GET",
                "/api/notes/read",
                status=404,
                params={"repo": "R", "master": "t", "path": "ghost.md"},
            )

    def test_changeset_routes_conform(self) -> None:
        leaf = {"repo": "R", "master": "t", "leaf": "leaf-1", "mode": "working"}
        with self._client() as client:
            self._check(client, "GET", "/api/changeset/task", status=200, params=leaf)
            self._check(
                client,
                "GET",
                "/api/changeset/file-diff",
                status=200,
                params={**leaf, "kind": "code", "path": "f.py"},
            )
            self._check(
                client,
                "GET",
                "/api/changeset/master",
                status=200,
                params={"repo": "R", "master": "t"},
            )
            self._check(
                client,
                "GET",
                "/api/changeset/task",
                status=400,
                params={"repo": "R", "leaf": "leaf-1"},
            )

    # -- the write surface ---------------------------------------------------------------

    def test_action_and_inbox_routes_conform(self) -> None:
        with self._client() as client:
            self._check(
                client,
                "POST",
                "/api/actions/dismiss",
                status=202,
                route="/api/actions/{action}",
                json={"actor": "developer", "itemId": "item-1", "kind": "actionable-drift"},
            )
            # A gate verb against a lifecycle with no open gate: the 409 the router mints
            # after the evaluator has already accepted the request.
            self._check(
                client,
                "POST",
                "/api/actions/approve",
                status=409,
                route="/api/actions/{action}",
                json={"actor": "developer", "target": "ghost"},
            )
            self._check(
                client,
                "POST",
                "/api/actions/approve",
                status=400,
                route="/api/actions/{action}",
                json={"actor": "developer"},
            )
            # A non-gate verb whose target is not in the projection at all.
            self._check(
                client,
                "POST",
                "/api/actions/pause",
                status=404,
                route="/api/actions/{action}",
                json={"actor": "developer", "target": "ghost"},
            )
            posted = self._check(
                client,
                "POST",
                "/api/operator-inbox",
                status=200,
                json={"lifecycleId": "L1", "ask": "Continue?", "response": "Yes"},
            )
            self._check(
                client,
                "POST",
                f"/api/operator-inbox/{posted['entryId']}/dismiss",
                status=200,
                route="/api/operator-inbox/{entry_id}/dismiss",
            )
            self._check(
                client,
                "POST",
                "/api/operator-inbox/ghost/dismiss",
                status=404,
                route="/api/operator-inbox/{entry_id}/dismiss",
            )

    def test_terminal_catalog_routes_conform(self) -> None:
        # The two routes FastAPI itself validates. ``sessions`` must carry a row with the
        # conditional key set actually populated, or the one live-enforced model on the whole
        # surface would be exercised against an empty list.
        with self._client() as client:
            sessions = self._check(client, "GET", "/api/terminal/sessions", status=200)
            self._check(client, "GET", "/api/harnesses", status=200)
        rows = {row["id"]: row for row in sessions["sessions"]}
        self.assertIn("live", rows)
        self.assertEqual(rows["live"]["harness"], "claude")
        # The conditional half really is conditional: an unset field is an ABSENT key, never a
        # null. This is what ``response_model_exclude_unset`` preserves.
        self.assertEqual(rows["live"]["controlProtocol"], "ar-harness-control/v1")
        self.assertNotIn("retiredAt", rows["live"])
        self.assertNotIn("launchArgs", rows["live"])
        self.assertNotIn("spawnRole", rows["live"])

    def test_terminal_control_routes_conform(self) -> None:
        with self._client() as client:
            self._check(
                client,
                "POST",
                "/api/terminal/landed-cleanup",
                status=200,
                json={"sessionIds": ["landed", "ghost"]},
            )
            self._check(
                client,
                "POST",
                "/api/terminal/live/attach-leaf",
                status=200,
                route="/api/terminal/{session}/attach-leaf",
                json={"leafKey": "R/t/leaf-1", "role": "worker"},
            )
            self._check(
                client,
                "POST",
                "/api/terminal/ghost/attach-leaf",
                status=404,
                route="/api/terminal/{session}/attach-leaf",
                json={"leafKey": "R/t/leaf-1", "role": "worker"},
            )
            # A plain pane: the paster's tmux call fails in this fixture, which is still a real
            # ``TerminalPaneDelivery`` body -- ``delivered: false`` with the capture attached.
            self._check(
                client,
                "POST",
                "/api/terminal/plain/paste",
                status=200,
                route="/api/terminal/{session}/paste",
                json={"text": "hello", "submit": False},
            )
            self._check(
                client,
                "POST",
                "/api/terminal/ghost/paste",
                status=404,
                route="/api/terminal/{session}/paste",
                json={"text": "hello"},
            )
            self._check(
                client,
                "POST",
                "/api/terminal/live/rename",
                status=200,
                route="/api/terminal/{session}/rename",
                json={"label": "Renamed"},
            )
            self._check(
                client,
                "POST",
                "/api/terminal/ghost/rename",
                status=404,
                route="/api/terminal/{session}/rename",
                json={"label": "x"},
            )
            self._check(
                client,
                "POST",
                "/api/terminal/live/retire",
                status=403,
                route="/api/terminal/{session}/retire",
                json={"actorSession": "live", "reason": "self"},
            )
            self._check(
                client,
                "POST",
                "/api/terminal/live/retire",
                status=404,
                route="/api/terminal/{session}/retire",
                json={"actorSession": "ghost", "reason": "x"},
            )
            self._check(
                client,
                "POST",
                "/api/terminal/plain/terminate",
                status=200,
                route="/api/terminal/{session}/terminate",
            )
            self._check(
                client,
                "POST",
                "/api/terminal/ghost/terminate",
                status=404,
                route="/api/terminal/{session}/terminate",
            )
            self._check(
                client,
                "POST",
                "/api/terminal/live/image",
                status=200,
                route="/api/terminal/{session}/image",
                files={"file": ("dot.png", _PNG, "image/png")},
            )
            self._check(
                client,
                "POST",
                "/api/terminal/live/image",
                status=400,
                route="/api/terminal/{session}/image",
                files={"file": ("dot.txt", b"nope", "text/plain")},
            )
            self._check(
                client,
                "POST",
                "/api/terminal/ghost/image",
                status=404,
                route="/api/terminal/{session}/image",
                files={"file": ("dot.png", _PNG, "image/png")},
            )
            # The opener refuses an unknown kind before it ever reaches the host.
            self._check(
                client,
                "POST",
                "/api/terminal/fresh",
                status=400,
                route="/api/terminal/{session}",
                json={"kind": "nonsense", "label": "x"},
            )

    # -- the harness control surface -----------------------------------------------------

    def test_harness_control_routes_conform(self) -> None:
        # The bridge is the one thing doubled: every route below still resolves its seat
        # through the real catalog + liveness path and answers through the real serializers.
        authority = SubmissionAuthorityDescriptor(bridge_epoch="epoch-1")
        patches = {
            "read_control_capabilities": CapabilitySnapshot((), None, None),
            "set_control_model": SetResult(
                ok=True, acceptance="echo-verified", requested_value="opus"
            ),
            "set_control_effort": SetResult(
                ok=True, acceptance="immediate", requested_value="high"
            ),
            "read_submission_authority": authority,
            "read_submission_status": SubmissionStatusBatch(
                bridge_epoch="epoch-1",
                submissions=(
                    SubmissionLookup(
                        request_id="r1",
                        outcome="found",
                        submission=SubmissionStatus(
                            request_id="r1",
                            state="delivered",
                            submitted_at="2026-06-14T10:00:00+00:00",
                            updated_at="2026-06-14T10:00:01+00:00",
                            accepted_at=None,
                            withdrawable=False,
                        ),
                    ),
                ),
            ),
            "withdraw_control_submission": WithdrawalResult(
                request_id="r1", outcome="not-withdrawable", state="delivered"
            ),
            "submit_control_prompt": SubmissionReceipt(
                request_id="r1",
                acceptance="queued",
                submitted_at="2026-06-14T10:00:00+00:00",
                bridge_epoch="epoch-1",
            ),
            "reconcile_control_prompt": ReconciliationResult(
                request_id="r1",
                state="accepted",
                reconciled_at="2026-06-14T10:00:02+00:00",
                bridge_epoch="epoch-1",
            ),
        }
        stack = [
            mock.patch(f"agents_remember.serving.harness_control_api.{name}", return_value=value)
            for name, value in patches.items()
        ]
        for patch in stack:
            patch.start()
            self.addCleanup(patch.stop)
        with self._client() as client:
            base = "/api/terminal/live"
            self._check(
                client,
                "GET",
                f"{base}/capabilities",
                status=200,
                route="/api/terminal/{session}/capabilities",
            )
            self._check(
                client,
                "POST",
                f"{base}/set-model",
                status=200,
                route="/api/terminal/{session}/set-model",
                json={"model": "opus"},
            )
            self._check(
                client,
                "POST",
                f"{base}/set-effort",
                status=200,
                route="/api/terminal/{session}/set-effort",
                json={"effort": "high"},
            )
            self._check(
                client,
                "GET",
                f"{base}/submission-authority",
                status=200,
                route="/api/terminal/{session}/submission-authority",
            )
            self._check(
                client,
                "POST",
                f"{base}/submission-status",
                status=200,
                route="/api/terminal/{session}/submission-status",
                json={"expectedBridgeEpoch": "epoch-1", "requestIds": ["r1"]},
            )
            self._check(
                client,
                "POST",
                f"{base}/withdraw",
                status=200,
                route="/api/terminal/{session}/withdraw",
                json={"expectedBridgeEpoch": "epoch-1", "requestId": "r1"},
            )
            self._check(
                client,
                "POST",
                f"{base}/submit",
                status=200,
                route="/api/terminal/{session}/submit",
                json={"requestId": "r1", "text": "go", "expectedBridgeEpoch": "epoch-1"},
            )
            self._check(
                client,
                "POST",
                f"{base}/reconcile",
                status=200,
                route="/api/terminal/{session}/reconcile",
                json={"requestId": "r1", "expectedBridgeEpoch": "epoch-1"},
            )
            self._check(
                client,
                "POST",
                f"{base}/interaction-response",
                status=409,
                route="/api/terminal/{session}/interaction-response",
                json={
                    "interactionId": "q1",
                    "expectedBridgeEpoch": "stale",
                    "response": "allow",
                },
            )
            self._check(
                client,
                "GET",
                "/api/terminal/ghost/capabilities",
                status=404,
                route="/api/terminal/{session}/capabilities",
            )
            self._check(
                client,
                "GET",
                "/api/harnesses/nope/capabilities",
                status=404,
                route="/api/harnesses/{harness}/capabilities",
            )

    # -- the conversation surface (refusal legs; success legs live in the class below) ----

    def test_conversation_routes_conform(self) -> None:
        epoch = {"expectedBridgeEpoch": "epoch-1"}
        turn = {"turnId": "t1", "requestId": "r1"}
        withdraw = {"operationRef": "o1", "withdrawRequestId": "w1"}
        ghost = "/api/terminal/ghost"
        calls: list[tuple[str, str, str, int, dict[str, Any]]] = [
            (
                "GET",
                f"{ghost}/conversation",
                "/api/terminal/{ar_session_id}/conversation",
                404,
                {"params": epoch},
            ),
            (
                "POST",
                f"{ghost}/conversation/agents/a1/history",
                "/api/terminal/{ar_session_id}/conversation/agents/{agent_id}/history",
                404,
                {"params": epoch},
            ),
            (
                "GET",
                f"{ghost}/conversation/events",
                "/api/terminal/{ar_session_id}/conversation/events",
                400,
                {"params": {**epoch, "after": "not-a-cursor"}},
            ),
            (
                "POST",
                f"{ghost}/conversation/interrupt",
                "/api/terminal/{ar_session_id}/conversation/interrupt",
                404,
                {"params": epoch, "json": turn},
            ),
            (
                "POST",
                f"{ghost}/conversation/interrupt-status",
                "/api/terminal/{ar_session_id}/conversation/interrupt-status",
                404,
                {"params": epoch, "json": turn},
            ),
            (
                "POST",
                f"{ghost}/conversation/interrupt-reconcile",
                "/api/terminal/{ar_session_id}/conversation/interrupt-reconcile",
                404,
                {"params": epoch, "json": turn},
            ),
            (
                "GET",
                f"{ghost}/operation-queue",
                "/api/terminal/{ar_session_id}/operation-queue",
                404,
                {"params": epoch},
            ),
            (
                "POST",
                f"{ghost}/operation-queue/withdraw",
                "/api/terminal/{ar_session_id}/operation-queue/withdraw",
                404,
                {"params": epoch, "json": {**withdraw, "withdrawalRef": "wr1"}},
            ),
            (
                "POST",
                f"{ghost}/operation-queue/withdraw-status",
                "/api/terminal/{ar_session_id}/operation-queue/withdraw-status",
                404,
                {"params": epoch, "json": withdraw},
            ),
            (
                "POST",
                f"{ghost}/operation-queue/withdraw-reconcile",
                "/api/terminal/{ar_session_id}/operation-queue/withdraw-reconcile",
                404,
                {"params": epoch, "json": withdraw},
            ),
            (
                "GET",
                f"{ghost}/operation-queue/pending-withdrawal-recoveries",
                "/api/terminal/{ar_session_id}/operation-queue/pending-withdrawal-recoveries",
                404,
                {"params": epoch},
            ),
            (
                "POST",
                f"{ghost}/operation-queue/withdraw-recovery",
                "/api/terminal/{ar_session_id}/operation-queue/withdraw-recovery",
                404,
                {"params": epoch, "json": {"recoveryRef": "rc1"}},
            ),
            (
                "POST",
                f"{ghost}/operation-queue/withdraw-recovery-ack",
                "/api/terminal/{ar_session_id}/operation-queue/withdraw-recovery-ack",
                404,
                {
                    "params": epoch,
                    "json": {"recoveryRef": "rc1", "disposition": "keep-current-draft"},
                },
            ),
            (
                "POST",
                f"{ghost}/conversation/attachments",
                "/api/terminal/{ar_session_id}/conversation/attachments",
                404,
                {
                    "params": epoch,
                    "data": {"requestId": "a1"},
                    "files": [("assets", ("dot.png", _PNG, "image/png"))],
                },
            ),
            (
                "POST",
                f"{ghost}/conversation/attachments/rebind",
                "/api/terminal/{ar_session_id}/conversation/attachments/rebind",
                404,
                {"params": epoch, "json": {"recoveryAssetRef": "ar1", "requestId": "r1"}},
            ),
            (
                "GET",
                f"{ghost}/conversation/attachments/r1/status",
                "/api/terminal/{ar_session_id}/conversation/attachments/{request_id}/status",
                404,
                {"params": epoch},
            ),
            (
                "POST",
                f"{ghost}/conversation/attachments/r1/reconcile",
                "/api/terminal/{ar_session_id}/conversation/attachments/{request_id}/reconcile",
                404,
                {"params": epoch},
            ),
            (
                "POST",
                f"{ghost}/conversation/submit",
                "/api/terminal/{ar_session_id}/conversation/submit",
                404,
                {
                    "json": {
                        "expectedBridgeEpoch": "epoch-1",
                        "requestId": "s1",
                        "disposition": "next",
                        "draftRevision": 1,
                        "content": [{"type": "text", "text": "hi"}],
                    }
                },
            ),
            (
                "GET",
                f"{ghost}/conversation/policy",
                "/api/terminal/{ar_session_id}/conversation/policy",
                404,
                {"params": epoch},
            ),
            (
                "GET",
                f"{ghost}/conversation/telemetry",
                "/api/terminal/{ar_session_id}/conversation/telemetry",
                404,
                {"params": epoch},
            ),
            (
                "GET",
                "/api/harnesses/nope/conversations",
                "/api/harnesses/{harness_id}/conversations",
                404,
                {},
            ),
            (
                "GET",
                "/api/harnesses/nope/conversations/k",
                "/api/harnesses/{harness_id}/conversations/{conversation_key}",
                404,
                {},
            ),
            (
                "POST",
                "/api/harnesses/nope/conversations/k/open",
                "/api/harnesses/{harness_id}/conversations/{conversation_key}/open",
                404,
                {"json": {"requestId": "o1", "expectedIdentityDigest": "d"}},
            ),
            (
                "POST",
                "/api/harnesses/nope/conversations/k/open-status",
                "/api/harnesses/{harness_id}/conversations/{conversation_key}/open-status",
                404,
                {"json": {"requestId": "o1"}},
            ),
            (
                "POST",
                "/api/harnesses/nope/conversations/k/open-reconcile",
                "/api/harnesses/{harness_id}/conversations/{conversation_key}/open-reconcile",
                404,
                {"json": {"requestId": "o1"}},
            ),
        ]
        with self._client() as client:
            for method, path, route, status, kwargs in calls:
                self._check(client, method, path, status=status, route=route, **kwargs)


# --- the conversation success shapes -----------------------------------------------------------


class ConversationSuccessConformanceTests(unittest.IsolatedAsyncioTestCase):
    """Real 200/202 bodies off a real control bridge, validated against the declared models.

    The refusal legs above prove the ``responses`` tables; only a live bridge can prove the
    success models, which are exactly the models these handlers dump. Real uvicorn (not
    ``TestClient``) because the bridge must live on this test's own event loop.
    """

    async def asyncSetUp(self) -> None:
        self.adapter = FakeControlAdapter(harness="codex")
        self.harness = make_harness(self, self.adapter, "ar-conf", harness="codex")
        await self.harness.start()
        self.addAsyncCleanup(self.harness.stop)
        self.epoch = self.harness.epoch
        for module in ("control", "active"):
            patch = mock.patch(
                f"agents_remember.serving.conversation.{module}.api"
                ".resolve_conversation_authorization",
                lambda request: OPERATOR,
            )
            patch.start()
            self.addCleanup(patch.stop)
        self.server = uvicorn.Server(
            uvicorn.Config(
                self.harness.app, host="127.0.0.1", port=0, log_level="warning", access_log=False
            )
        )
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.02)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")
        self.routes = route_index(self.harness.app)

    async def asyncTearDown(self) -> None:
        COMPLETED.add(f"{type(self).__name__}.{self._testMethodName}")
        await self.client.aclose()
        self.server.should_exit = True
        await self.task

    async def _check(
        self, method: str, path: str, *, route: str, status: int, **kwargs: Any
    ) -> Any:
        DRIVEN.add((method, route, status))
        response = await self.client.request(method, path, **kwargs)
        self.assertEqual(response.status_code, status, f"{method} {path}: {response.text}")
        model = declared_model(self.routes[(method, route)], status)
        body = response.json()
        try:
            validate_wire(model, body)
        except Exception as exc:  # pragma: no cover - only on a real contract breach
            raise AssertionError(f"{method} {path} -> {status}: {exc}\n{body}") from exc
        return body

    async def test_the_conversation_success_bodies_conform(self) -> None:
        session = "ar-conf"
        base = f"/api/terminal/{session}"
        params = {"expectedBridgeEpoch": self.epoch}
        await self._check(
            "GET",
            base + "/conversation",
            route="/api/terminal/{ar_session_id}/conversation",
            status=200,
            params=params,
        )
        await self._check(
            "POST",
            base + "/conversation/agents/agent-1/history",
            route="/api/terminal/{ar_session_id}/conversation/agents/{agent_id}/history",
            status=200,
            params=params,
        )
        await self._check(
            "GET",
            base + "/conversation/policy",
            route="/api/terminal/{ar_session_id}/conversation/policy",
            status=200,
            params=params,
        )
        await self._check(
            "GET",
            base + "/conversation/telemetry",
            route="/api/terminal/{ar_session_id}/conversation/telemetry",
            status=200,
            params=params,
        )
        await self._check(
            "GET",
            base + "/operation-queue",
            route="/api/terminal/{ar_session_id}/operation-queue",
            status=200,
            params=params,
        )
        await self._check(
            "GET",
            base + "/operation-queue/pending-withdrawal-recoveries",
            route="/api/terminal/{ar_session_id}/operation-queue/pending-withdrawal-recoveries",
            status=200,
            params=params,
        )
        # The staging answer and the submit answer are the two bodies assembled at the route,
        # so they are the two the conversation surface had no model for at all.
        await self._check(
            "POST",
            base + "/conversation/attachments",
            route="/api/terminal/{ar_session_id}/conversation/attachments",
            status=200,
            params=params,
            data={"requestId": "att-1", "metadata": json.dumps([{"kind": "image"}])},
            files=[("assets", ("dot.png", _PNG, "image/png"))],
        )
        await self._check(
            "GET",
            base + "/conversation/attachments/att-1/status",
            route="/api/terminal/{ar_session_id}/conversation/attachments/{request_id}/status",
            status=200,
            params=params,
        )
        await self._check(
            "POST",
            base + "/conversation/submit",
            route="/api/terminal/{ar_session_id}/conversation/submit",
            status=200,
            json={
                "expectedBridgeEpoch": self.epoch,
                "requestId": "sub-1",
                "disposition": "next",
                "draftRevision": 1,
                "content": [{"type": "text", "text": "generate"}],
            },
        )
        # An interrupt REJECTED by the harness is still the operation's own body, on 422 --
        # ``interrupt_http_status`` picks the status off the acknowledgement. That is the leg
        # the shared refusal table got wrong, so it is the leg worth driving.
        await drive_activity(self.harness, "running")
        await self._check(
            "POST",
            base + "/conversation/interrupt",
            route="/api/terminal/{ar_session_id}/conversation/interrupt",
            status=422,
            params=params,
            json={"turnId": "turn-1", "requestId": "int-1"},
        )
        await self._check(
            "POST",
            base + "/conversation/interrupt-status",
            route="/api/terminal/{ar_session_id}/conversation/interrupt-status",
            status=422,
            params=params,
            json={"turnId": "turn-1", "requestId": "int-1"},
        )

    def _conversation_key(self, vendor: str = "thread-1") -> tuple[str, str]:
        """A conversation key this app's OWN cursor authority will re-authorize.

        The signing key is minted per runtime, so a key has to come from the app under test;
        there is no fixture value that works. Without one the whole library surface answers
        404 ``unknown-harness`` at the first gate, which is exactly why the open trio's own
        body shapes had never been driven.
        """

        runtime = self.harness.runtime
        shared = library_shared(runtime)
        authorization = runtime.authorization.resolve(client_host="127.0.0.1")
        scope = canonical_library_scope(
            authorization, "codex", None, workspace_root=runtime.scope.workspace_root
        )
        digest = shared.cursor_authority.identity_digest(
            "codex", vendor, scope.canonical_project_scope
        )
        key = shared.cursor_authority.mint_conversation_key(
            scope,
            vendor_conversation_id=vendor,
            identity_digest=digest,
            catalog_generation=1,
        )
        return key.root, digest

    async def test_the_library_and_open_bodies_conform(self) -> None:
        """The five library routes, past the 404 that was the only leg anything drove.

        This is the surface the merged ``responses`` table got wrong. The open trio answer
        409/422/503 with TWO unrelated families -- the operation's own outcome, and
        ``_error_response``'s typed refusals -- and a dict merge kept only one of them. Both
        are driven here, on the same status, so a table that declares one and not the other
        fails.
        """

        base = "/api/harnesses/codex/conversations"
        listing = "/api/harnesses/{harness_id}/conversations"
        one = listing + "/{conversation_key}"
        # ``LIBRARY_RESPONSES[422]``: the capability gate, and the only library refusal that is
        # not the shared ``{status, detail}`` envelope. This fixture's harness registry is
        # empty, so the history gate reports the harness unavailable.
        refusal = await self._check("GET", base, route=listing, status=422)
        self.assertEqual(refusal["status"], "capability-unavailable")
        self.assertEqual(refusal["capabilityState"], "unavailable")
        key, digest = self._conversation_key()
        await self._check("GET", f"{base}/{key}", route=one, status=422)

        # The open trio's own body, on a status the shared table also claims. The resume gate
        # refuses on this fixture, so the operation settles ``unsupported`` -- an OUTCOME, with
        # a full ``OpenConversationOperation`` on the wire, not a refusal envelope.
        opened = await self._check(
            "POST",
            f"{base}/{key}/open",
            route=one + "/open",
            status=422,
            json={"requestId": "o1", "expectedIdentityDigest": digest},
        )
        self.assertEqual(opened["outcome"], "unsupported")
        for suffix in ("open-status", "open-reconcile"):
            replayed = await self._check(
                "POST",
                f"{base}/{key}/{suffix}",
                route=f"{one}/{suffix}",
                status=422,
                json={"requestId": "o1"},
            )
            self.assertEqual(replayed["outcome"], "unsupported")

        # ...and the OTHER family on the open trio's statuses: a typed refusal, same route,
        # same 409 the outcome table also claims. Under a table that merged instead of
        # unioning, this body had no declared model at all.
        stale = await self._check(
            "POST",
            f"{base}/{key}/open",
            route=one + "/open",
            status=409,
            json={"requestId": "o2", "expectedIdentityDigest": "sha256:not-the-row"},
        )
        self.assertEqual(stale["status"], "stale-identity")
        scoped = await self._check(
            "POST",
            f"{base}/{key}/open",
            route=one + "/open",
            status=403,
            json={"requestId": "o3", "expectedIdentityDigest": digest, "cwd": "/"},
        )
        self.assertEqual(scoped["status"], "scope-denied")
        malformed = await self._check(
            "POST",
            f"{base}/{key}/open",
            route=one + "/open",
            status=400,
            json={"requestId": "o4"},
        )
        self.assertEqual(malformed["status"], "invalid-cursor")


class ConversationCompositionRefusalTests(unittest.TestCase):
    """``CONTROL_RESPONSES[503]``: declared on all 17 control routes, driven on none.

    ``create_app`` always composes a complete ``ConversationRuntime``, so the composition
    refusal is unreachable through it -- which is precisely why this status sat declared and
    unexercised on seventeen routes. It is not unreachable in general: the routers are
    independently mountable (``register_conversation_routes`` is a separate call), and a router
    mounted without its runtime is the state the refusal exists for. Driving the real route
    function through the real declaration is what makes the declaration falsifiable.
    """

    def test_a_control_route_without_its_runtime_answers_its_declared_503(self) -> None:
        app = FastAPI()
        app.include_router(control_router)
        routes = route_index(app)
        route = "/api/terminal/{ar_session_id}/operation-queue"
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            response = client.get(
                "/api/terminal/s1/operation-queue", params={"expectedBridgeEpoch": "e"}
            )
        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()
        self.assertEqual(body["status"], "composition-unavailable")
        validate_wire(declared_model(routes[("GET", route)], 503), body)
        DRIVEN.add(("GET", route, 503))

    def tearDown(self) -> None:
        COMPLETED.add(f"{type(self).__name__}.{self._testMethodName}")


# --- the branches no body-shaped model can express ---------------------------------------------


class StreamContractTests(unittest.IsolatedAsyncioTestCase):
    """The 304 with no body, and the SSE frames whose ``data`` the declaration names.

    The generator legs below drive the real generators rather than the HTTP route, for the same
    reason ``test_served_state_conformance.py`` does: an SSE route never ends, so a
    ``TestClient`` stream cannot be closed from inside the portal thread. The generator IS the
    route body -- ``api_stream``/``api_events`` do nothing but yield from it -- and driving it
    directly is the only way to reach the second frame, which is where the snapshot/delta
    asymmetry lives.

    That is a good reason to drive the generator and was never a good reason to drive ONLY the
    generator: it leaves the route itself -- its status, its media type, its serializer -- with
    no test at all. ``test_the_sse_routes_answer_over_http`` closes that over a real socket,
    where a stream can be cancelled from the outside.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        settings = agentic_settings_path(self.tmp)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            json.dumps({"orchestration": {"supervisor": {"enabled": False}}}), encoding="utf-8"
        )
        self.config = _config(self.tmp)
        self.app = create_app(self.config, cadence=ProjectionCadence(interval=100))
        self.routes = route_index(self.app)

    def tearDown(self) -> None:
        COMPLETED.add(f"{type(self).__name__}.{self._testMethodName}")

    async def test_the_sse_routes_answer_over_http_with_their_declared_first_frame(self) -> None:
        # Both SSE routes, driven as routes: 200, ``text/event-stream``, and a first frame
        # whose ``data`` validates against what the route declares. Neither had ever been
        # requested over HTTP by any test in this repository.
        async with serve(self.app) as client:
            for path, expected in (("/api/stream", "snapshot"), ("/api/events", "ready")):
                async with client.stream("GET", path, timeout=10) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(
                        response.headers["content-type"].startswith("text/event-stream"),
                        response.headers["content-type"],
                    )
                    event, data = await asyncio.wait_for(first_sse_frame(response), timeout=10)
                self.assertEqual(event, expected)
                route = self.routes[("GET", path)]
                validate_wire(route.response_model, data)
                DRIVEN.add(("GET", path, 200))

    def test_the_304_branch_declares_a_body_less_response(self) -> None:
        # ``/api/state`` cannot become a model-returning handler because of this branch, so the
        # contract has to say the 304 carries no model -- and the route has to keep proving it.
        entry = self.routes[("GET", "/api/state")].responses[304]
        self.assertNotIn("model", entry)
        with TestClient(self.app) as client:
            first = client.get("/api/state")
            cached = client.get("/api/state", headers={"If-None-Match": first.headers["etag"]})
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.content, b"")
        DRIVEN.add(("GET", "/api/state", 304))

    async def test_the_state_stream_snapshot_and_delta_split_the_declaration(self) -> None:
        route = self.routes[("GET", "/api/stream")]
        self.assertEqual(list(route.responses[200]["content"]), ["text/event-stream"])
        projector = Projector(self.config, cadence=ProjectionCadence(interval=100))
        await projector.prime()
        stream = stream_events(projector)
        try:
            snapshot = await asyncio.wait_for(stream.__anext__(), timeout=5)
            self.assertEqual(snapshot.event, "snapshot")
            # The declared model is what a ``snapshot`` frame's data is.
            validate_wire(route.response_model, snapshot.data)
            pending = asyncio.create_task(stream.__anext__())
            await asyncio.sleep(0.02)
            projector._broadcast(
                (
                    1,
                    DeltaEvent(
                        "lifecycle",
                        LifecycleProjection(
                            id="L1",
                            state="running",
                            phase="build",
                            fleeting=False,
                            startedAt="2026-06-14T10:00:00Z",
                            lastEventTs="2026-06-14T10:00:00Z",
                        ),
                    ),
                )
            )
            delta = await asyncio.wait_for(pending, timeout=5)
        finally:
            await stream.aclose()
        # The asymmetry the declaration rests on: a delta is one bare projection node, so it
        # is NOT the declared whole-state body and must not carry the serve-time tail.
        self.assertEqual(delta.event, "lifecycle")
        assert isinstance(delta.data, dict)
        self.assertEqual(set(delta.data) & set(SERVED_TAIL_FIELDS), set())
        LifecycleProjection.model_validate(delta.data)

    async def test_the_raw_river_ready_marker_validates_against_its_declaration(self) -> None:
        # The river's ``event`` frames are verbatim observer records, so the marker is the one
        # frame this route mints -- and therefore the only one it can honestly declare.
        route = self.routes[("GET", "/api/events")]
        self.assertEqual(list(route.responses[200]["content"]), ["text/event-stream"])
        stream = stream_raw_events(self.config, interval=100)
        try:
            frame = await asyncio.wait_for(stream.__anext__(), timeout=5)
        finally:
            await stream.aclose()
        self.assertEqual(frame.event, "ready")
        validate_wire(route.response_model, frame.data)


# --- the score ---------------------------------------------------------------------------------


UNDRIVEN_DECLARATIONS: dict[tuple[str, str], frozenset[int]] = {
    # --- one shared table, seventeen routes -------------------------------------------------
    # ``CONTROL_RESPONSES`` / ``CONVERSATION_RESPONSES`` declare six statuses on every
    # conversation route. Reaching each of them means driving a REAL control bridge into each
    # typed failure -- a stale epoch, a rejected operation, a dead socket mid-write -- and the
    # bridge fixture models the harness edge, not those failures. What is driven instead is the
    # 404 (no such seat) on every one of them, the 403 on one, and every success shape off the
    # live bridge; the rest of each row is the same ``StatusRefusal`` shape reached by a
    # different exception, through one mapper (``_map_typed_error``) that IS driven.
    ("GET", "/api/terminal/{ar_session_id}/conversation"): frozenset({400, 409, 422, 503}),
    ("POST", "/api/terminal/{ar_session_id}/conversation/agents/{agent_id}/history"): frozenset(
        {400, 403, 409, 422, 503}
    ),
    ("POST", "/api/terminal/{ar_session_id}/conversation/attachments"): frozenset(
        {400, 403, 409, 422, 503}
    ),
    ("POST", "/api/terminal/{ar_session_id}/conversation/attachments/rebind"): frozenset(
        {200, 400, 403, 409, 422, 503}
    ),
    (
        "POST",
        "/api/terminal/{ar_session_id}/conversation/attachments/{request_id}/reconcile",
    ): frozenset({200, 400, 403, 409, 422, 503}),
    (
        "GET",
        "/api/terminal/{ar_session_id}/conversation/attachments/{request_id}/status",
    ): frozenset({400, 403, 409, 422, 503}),
    # The conversation SSE route: 200 is a stream, and unlike ``/api/stream`` it needs a live
    # bridge AND a live uvicorn at once, which no fixture here stands up together.
    ("GET", "/api/terminal/{ar_session_id}/conversation/events"): frozenset(
        {200, 403, 404, 409, 422, 503}
    ),
    ("POST", "/api/terminal/{ar_session_id}/conversation/interrupt"): frozenset(
        {200, 202, 400, 403, 409, 503}
    ),
    ("POST", "/api/terminal/{ar_session_id}/conversation/interrupt-reconcile"): frozenset(
        {200, 202, 400, 403, 409, 422, 503}
    ),
    ("POST", "/api/terminal/{ar_session_id}/conversation/interrupt-status"): frozenset(
        {200, 202, 400, 403, 409, 503}
    ),
    ("GET", "/api/terminal/{ar_session_id}/conversation/policy"): frozenset(
        {400, 403, 409, 422, 503}
    ),
    ("POST", "/api/terminal/{ar_session_id}/conversation/submit"): frozenset(
        {202, 400, 403, 409, 422, 503}
    ),
    ("GET", "/api/terminal/{ar_session_id}/conversation/telemetry"): frozenset(
        {400, 403, 409, 422, 503}
    ),
    ("GET", "/api/terminal/{ar_session_id}/operation-queue"): frozenset({400, 403, 409, 422}),
    (
        "GET",
        "/api/terminal/{ar_session_id}/operation-queue/pending-withdrawal-recoveries",
    ): frozenset({400, 403, 409, 422, 503}),
    ("POST", "/api/terminal/{ar_session_id}/operation-queue/withdraw"): frozenset(
        {200, 202, 400, 403, 409, 422, 503}
    ),
    ("POST", "/api/terminal/{ar_session_id}/operation-queue/withdraw-reconcile"): frozenset(
        {200, 400, 403, 409, 422, 503}
    ),
    ("POST", "/api/terminal/{ar_session_id}/operation-queue/withdraw-recovery"): frozenset(
        {200, 400, 403, 409, 422, 503}
    ),
    ("POST", "/api/terminal/{ar_session_id}/operation-queue/withdraw-recovery-ack"): frozenset(
        {200, 400, 403, 409, 422, 503}
    ),
    ("POST", "/api/terminal/{ar_session_id}/operation-queue/withdraw-status"): frozenset(
        {200, 400, 403, 409, 422, 503}
    ),
    # --- the library surface ----------------------------------------------------------------
    # 200 on list/read needs a harness whose native history store is installed and probeable,
    # which is a real vendor binary. The capability-gate 422 IS driven, and so is every open
    # outcome the gate can settle without one.
    ("GET", "/api/harnesses/{harness_id}/conversations"): frozenset({200, 400, 403, 409, 503}),
    ("GET", "/api/harnesses/{harness_id}/conversations/{conversation_key}"): frozenset(
        {200, 400, 403, 409, 503}
    ),
    # 201 ``opened`` / 202 ``pending`` / 503 ``launch-failed`` need a harness that actually
    # resumes; the 422 ``unsupported`` outcome and the typed refusals on the same statuses are
    # driven, which is what pins the two families the merged table used to collapse.
    ("POST", "/api/harnesses/{harness_id}/conversations/{conversation_key}/open"): frozenset(
        {201, 202, 503}
    ),
    (
        "POST",
        "/api/harnesses/{harness_id}/conversations/{conversation_key}/open-status",
    ): frozenset({201, 202, 400, 403, 409, 503}),
    (
        "POST",
        "/api/harnesses/{harness_id}/conversations/{conversation_key}/open-reconcile",
    ): frozenset({201, 202, 400, 403, 409, 503}),
    # --- the projection surface -------------------------------------------------------------
    # 503 is "the projection has not primed yet", a startup race the fixtures deliberately do
    # not have -- ``ProjectionCadence`` primes before the client is handed over.
    ("POST", "/api/actions/{action}"): frozenset({503}),
    ("GET", "/api/state"): frozenset({503}),
    ("GET", "/api/task-document"): frozenset({503}),
    # --- the harness control surface ---------------------------------------------------------
    # 503 is the control bridge refusing or unreachable mid-call. The seat resolution legs (404
    # unknown seat, 409 unsupported seat) and every success leg are driven; this last one needs
    # a bridge that accepts a connection and then fails, which the doubles do not model.
    ("GET", "/api/harnesses/{harness}/capabilities"): frozenset({200, 503}),
    ("GET", "/api/terminal/{session}/capabilities"): frozenset({503}),
    ("GET", "/api/terminal/{session}/submission-authority"): frozenset({503}),
    ("POST", "/api/terminal/{session}/set-model"): frozenset({503}),
    ("POST", "/api/terminal/{session}/set-effort"): frozenset({503}),
    ("POST", "/api/terminal/{session}/submission-status"): frozenset({503}),
    ("POST", "/api/terminal/{session}/withdraw"): frozenset({503}),
    ("POST", "/api/terminal/{session}/submit"): frozenset({503}),
    ("POST", "/api/terminal/{session}/reconcile"): frozenset({503}),
    ("POST", "/api/terminal/{session}/interaction-response"): frozenset({200, 503}),
    # 200 needs an actor seat that holds retire authority over another seat, which is a seat
    # role fixture rather than a request; the 403 and both 404s are driven.
    ("POST", "/api/terminal/{session}/retire"): frozenset({200}),
}
"""Every declared ``(method, path, status)`` this suite does not drive, and why.

This is a ledger, not a suppression list: :class:`DeclaredSurfaceCoverageTests` asserts it
EXACTLY, so a declaration that stops being driven has to be added here by hand, and a leg that
becomes drivable has to be removed. The number it carries is the honest score of this file.
"""

_DRIVING_CLASSES: tuple[type[unittest.TestCase], ...] = (
    ServingResponseConformanceTests,
    ConversationSuccessConformanceTests,
    ConversationCompositionRefusalTests,
    StreamContractTests,
)


def _grouped(pairs: set[tuple[str, str, int]]) -> dict[tuple[str, str], frozenset[int]]:
    grouped: dict[tuple[str, str], set[int]] = {}
    for method, path, status in pairs:
        grouped.setdefault((method, path), set()).add(status)
    return {key: frozenset(value) for key, value in grouped.items()}


def _driven_pairs() -> set[tuple[str, str, int]]:
    """What the driving tests actually drove, this process.

    ``DRIVEN`` is filled as they run, which makes it free on a whole-module run and wrong on a
    partial one -- and a coverage number computed from a partial run is the same silent
    absence this whole class exists to end. So when a driver has not run, it is run here.
    """

    expected = {
        f"{cls.__name__}.{name}"
        for cls in _DRIVING_CLASSES
        for name in unittest.TestLoader().getTestCaseNames(cls)
    }
    if not expected <= COMPLETED:
        suite = unittest.TestSuite()
        loader = unittest.TestLoader()
        for cls in _DRIVING_CLASSES:
            suite.addTests(loader.loadTestsFromTestCase(cls))
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        if not result.wasSuccessful():  # pragma: no cover - the drivers report their own
            raise AssertionError(
                "the conformance drivers failed while measuring coverage; run the module"
            )
    return set(DRIVEN)


class DeclaredSurfaceCoverageTests(unittest.TestCase):
    """The conformance table must account for the whole declared surface.

    A declaration nothing drives enforces nothing. That was not a hypothetical: the driving
    tests kept a ``self.checked`` set that no assertion ever read, 88 of 286 declared
    ``(method, path, status)`` pairs were driven, and seven declared models could be made
    mathematically unsatisfiable -- a required field retyped to a type no body could ever
    carry -- without one test going red.

    The gap is smaller now and it is not zero, because some legs need a real vendor harness or
    a bridge that fails mid-write. What changed is that the remainder is *counted*: it is
    listed in :data:`UNDRIVEN_DECLARATIONS` with a reason, asserted exactly, and a declaration
    that quietly stops being exercised fails here instead of going unnoticed.
    """

    maxDiff = None

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        self.app = create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100))

    def test_the_conformance_table_accounts_for_every_declared_pair(self) -> None:
        declared = declared_pairs(self.app)
        driven = _driven_pairs()
        # Nothing may be driven that is not declared: a request answering on a status the route
        # never declared would otherwise be validated against the success model by fallback.
        self.assertEqual(sorted(driven - declared), [])
        self.assertEqual(_grouped(declared - driven), UNDRIVEN_DECLARATIONS)
        # The headline, pinned so neither side can move without a decision: 286 declared pairs,
        # 133 driven against a real body, 153 declared-and-undriven with a reason each.
        self.assertEqual(len(declared), 286)
        self.assertEqual(len(driven), 133)
        self.assertEqual(len(declared) - len(driven), 153)

    def test_every_route_has_at_least_one_driven_status(self) -> None:
        # The weaker claim, but the one that has to hold without exception: a route no request
        # ever reaches is a route whose declaration is pure decoration. Every one of the 61 is
        # driven on at least one status, which is what makes the ledger above a list of
        # unexercised *legs* rather than of unexercised routes.
        driven = {(method, path) for method, path, _ in _driven_pairs()}
        never = sorted(
            f"{method} {path}"
            for method, path, _ in declared_pairs(self.app)
            if (method, path) not in driven
        )
        self.assertEqual(never, [])

    def test_the_open_status_map_is_total_over_the_declared_outcomes(self) -> None:
        # ``_open_call`` indexes ``_OPEN_STATUS_BY_OUTCOME`` directly, so this equality is what
        # makes that safe -- and it is also what removed an undeclared 500: the old ``.get(...,
        # 500)`` answered an unmapped outcome with a full operation body on a status no
        # ``responses`` table names and no test could reach.
        self.assertEqual(
            set(_OPEN_STATUS_BY_OUTCOME),
            set(get_args(OpenConversationOperation.model_fields["outcome"].annotation)),
        )


if __name__ == "__main__":
    unittest.main()
