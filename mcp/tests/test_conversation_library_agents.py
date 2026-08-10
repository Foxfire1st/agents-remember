"""Library sub-agent grouping tests with fake native boundaries.

Codex: sub-agent threads list through the PROBED camelCase ``sourceKinds`` vocabulary
(``subAgent`` et al. — proven against the vendored codex main ``ThreadSourceKind`` enum and a
live 0.145.0 app-server probe, 2026-07-26) and group client-side under their parent's row via
``parentThreadId`` (the vendor ``parentThreadId`` filter itself is experimental-gated on
0.145.0). Claude: ``subagents/agent-*.jsonl`` + ``.meta.json`` children group under their
parent session row with meta-bound identity. Unproven shapes stay visibly unavailable through
``agents_note`` — never silently absent, never guessed.
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from agents_remember.errors import CodexAppServerRpcError
from agents_remember.kernel.harnesses import Harness
from agents_remember.models.conversations.capabilities import (
    CapabilityEvidence,
    FeatureCapability,
    HistoryCapabilities,
)
from agents_remember.models.conversations.identity import (
    AuthorizationBinding,
    NativeConversationRef,
)
from agents_remember.serving.codex_app_server_protocol import JsonObject
from agents_remember.serving.conversation.library.claude import ClaudeConversationLibrary
from agents_remember.serving.conversation.library.codex import (
    _AGENT_SOURCE_KINDS,
    AppServerSeams,
    CodexConversationLibrary,
)
from agents_remember.serving.conversation.library.cursor import (
    LibraryCursorAuthority,
    mint_signing_key,
)
from agents_remember.serving.conversation.library.errors import (
    LibraryStoreError,
)
from agents_remember.serving.conversation.library.scope import canonical_library_scope

CALLER = AuthorizationBinding(principal_id="local-operator:1000", tenant_id="/ws")
CODEX = Harness(id="codex", name="Codex", command="codex", argv=("codex",))


def _caps() -> HistoryCapabilities:
    evidence = CapabilityEvidence(
        runtime_version="0.145.0", fixture_id="test-fixture", observed_at="2026-07-26T00:00:00Z"
    )
    feature = FeatureCapability(
        state="supported",
        reason="test-supported",
        evidence_tier="runtime-fixture",
        evidence=evidence,
    )
    return HistoryCapabilities(
        list=feature,
        read=feature,
        resume=feature,
        completeness=feature,
        tool_completeness=feature,
    )


async def _capabilities(_harness: str) -> HistoryCapabilities:
    return _caps()


class _FakeCodexTransport:
    """Canned app-server boundary recording every request, dispatching thread/list by kinds."""

    def __init__(
        self,
        *,
        top_page: JsonObject,
        agent_page: JsonObject | BaseException,
        read_page: JsonObject | None = None,
    ) -> None:
        self.top_page = top_page
        self.agent_page = agent_page
        self.read_page = read_page
        self.calls: list[tuple[str, Mapping[str, object]]] = []
        self.stopped = False

    async def start(self, launch: object) -> None:
        self.launch = launch

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_agents.py:90).
    async def request(  # pragma: no cover
        self,
        method: str,
        params: Mapping[str, object],
        *,
        before_write: object = None,
    ) -> JsonObject:
        del before_write
        self.calls.append((method, params))
        if method == "initialize":
            return {
                "userAgent": "agents_remember/0.145.0 (Ubuntu; x86_64) Test (agents_remember; 3.0.0)",
                "codexHome": "/home/x/.codex",
                "platformFamily": "unix",
                "platformOs": "linux",
            }
        if method == "thread/list":
            kinds = params.get("sourceKinds")
            if kinds == list(_AGENT_SOURCE_KINDS):
                if isinstance(self.agent_page, BaseException):
                    raise self.agent_page
                return self.agent_page
            return self.top_page
        if method == "thread/read":
            assert self.read_page is not None
            return self.read_page
        raise AssertionError(f"unexpected method {method}")

    async def notify(self, method: str, params: Mapping[str, object]) -> None:
        self.calls.append((method, params))

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_agents.py:121).
    async def messages(self):  # pragma: no cover
        return
        yield

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_agents.py:125).
    async def respond(
        self, request_id: object, result: Mapping[str, object]
    ) -> None:  # pragma: no cover
        del request_id, result

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_agents.py:128).
    async def respond_error(
        self, request_id: object, *, code: int, message: str
    ) -> None:  # pragma: no cover
        del request_id, code, message

    async def stop(self, mode: str) -> None:  # noqa: ARG002 - transport protocol

        self.stopped = True


class _FakeHelperHost:
    def __init__(self, results: Mapping[str, object]) -> None:
        self.results = dict(results)
        self.calls: list[tuple[str, str, Mapping[str, object]]] = []

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_conversation_library_agents.py:141).
    async def call(
        self, harness: str, operation: str, payload: Mapping[str, object]
    ):  # pragma: no cover
        self.calls.append((harness, operation, payload))
        value = self.results[operation]
        if callable(value):
            value = value(payload)
        return value, "9.9.9", "1.2.3"


def _scope(tmp: str, harness: str = "codex"):
    return canonical_library_scope(CALLER, harness, None, workspace_root=Path(tmp))  # type: ignore[arg-type]


TOP_PAGE = {
    "data": [
        {
            "id": "thr_parent000001",
            "preview": "Parent thread",
            "name": None,
            "createdAt": 1730831111,
            "updatedAt": 1730839999,
            "status": {"type": "notLoaded"},
            "source": "cli",
            "parentThreadId": None,
        },
        {
            "id": "thr_parent000002",
            "preview": "Other thread",
            "name": None,
            "createdAt": 1730830000,
            "updatedAt": 1730831111,
            "status": {"type": "notLoaded"},
            "source": "cli",
            "parentThreadId": None,
        },
    ],
    "nextCursor": None,
}

AGENT_PAGE = {
    "data": [
        {
            "id": "thr_agent00000001",
            "preview": "Review the diff",
            "name": None,
            "createdAt": 1730835000,
            "updatedAt": 1730836000,
            "status": {"type": "notLoaded"},
            "parentThreadId": "thr_parent000001",
            "agentNickname": "Leibniz",
            "agentRole": "reviewer",
            "source": {
                "subAgent": {
                    "thread_spawn": {
                        "parent_thread_id": "thr_parent000001",
                        "depth": 1,
                        "agent_path": "/root/reviewer",
                        "agent_nickname": "Leibniz",
                        "agent_role": "reviewer",
                    }
                }
            },
        },
        {
            "id": "thr_agent00000002",
            "preview": "Explore the repo",
            "name": None,
            "createdAt": 1730834000,
            "updatedAt": 1730834500,
            "status": {"type": "notLoaded"},
            "parentThreadId": "thr_parent000001",
            "agentNickname": None,
            "agentRole": None,
            "source": {
                "subAgent": {"thread_spawn": {"parent_thread_id": "thr_parent000001", "depth": 1}}
            },
        },
        {
            # Orphan on this page: its parent pages elsewhere, so it groups there.
            "id": "thr_agent00000003",
            "preview": "Off-page child",
            "name": None,
            "createdAt": 1730833000,
            "updatedAt": 1730833500,
            "status": {"type": "notLoaded"},
            "parentThreadId": "thr_parent999999",
            "agentNickname": None,
            "agentRole": None,
            "source": "unknown",
        },
    ],
    "nextCursor": None,
}

AGENT_THREAD_READ: JsonObject = {
    "thread": {
        "id": "thr_agent00000001",
        "updatedAt": 1730836000,
        "turns": [
            {
                "id": "turn-1",
                "status": "completed",
                "items": [
                    {
                        "type": "userMessage",
                        "id": "u1",
                        "content": [{"type": "text", "text": "review this"}],
                    },
                    {"type": "agentMessage", "id": "a1", "text": "looks good"},
                ],
            }
        ],
    }
}


class CodexLibraryAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _library(
        self,
        *,
        top_page: JsonObject = TOP_PAGE,
        agent_page: JsonObject | BaseException = AGENT_PAGE,
        read_page: JsonObject | None = None,
    ) -> CodexConversationLibrary:
        transport = _FakeCodexTransport(
            top_page=top_page, agent_page=agent_page, read_page=read_page
        )
        library = CodexConversationLibrary(
            authorization=CALLER,
            cursor_authority=LibraryCursorAuthority(mint_signing_key()),
            capabilities=_capabilities,  # type: ignore[arg-type]
            harness=CODEX,
            seams=AppServerSeams(env=lambda: {}, transport_factory=lambda: transport),
        )
        library._test_transport = transport  # type: ignore[attr-defined]
        return library

    async def test_agents_group_under_parent_with_probed_source_kinds(self) -> None:
        library = self._library()
        scope = _scope(self.tmp)
        page = await library.list(scope, cursor=None, limit=25)
        assert page.agents_note is None
        assert len(page.rows) == 2
        parent, other = page.rows
        assert other.agents == ()
        assert len(parent.agents) == 2

        named, fallback = parent.agents
        # Identity is evidence-bound: nickname/role/agent_path from the wire.
        assert named.title == "Leibniz"
        assert named.nickname == "Leibniz"
        assert named.role == "reviewer"
        assert named.agent_path == "/root/reviewer"
        assert named.safe_native_id_suffix == "000001"
        assert named.last_activity_at is not None
        # No identity evidence on the wire: the honest fallback, never a fabricated name.
        assert fallback.title == "agent thr_agen"
        assert fallback.nickname is None and fallback.role is None

        # The agent key mints the agent's own native identity, so opening it reads the
        # agent thread — the key round-trips to the agent thread id, not the parent's.
        binding, vendor = library._cursor_authority.verify_conversation_key(named.conversation_key)
        assert vendor == "thr_agent00000001"
        assert binding.identity_digest == named.identity_digest

        # The probed vocabulary is pinned: the agent fetch used exactly the camelCase kinds
        # proven against the installed 0.145.0 app-server, and the top-level fetch did not.
        agent_calls = self._agent_source_kind_calls(library)
        assert len(agent_calls) == 1
        assert _AGENT_SOURCE_KINDS == (
            "subAgent",
            "subAgentReview",
            "subAgentCompact",
            "subAgentThreadSpawn",
            "subAgentOther",
        )

    def _agent_source_kind_calls(self, library: CodexConversationLibrary) -> list[object]:
        """The probed ``thread/list`` calls that carried the pinned source-kind vocabulary."""
        return [
            params
            for method, params in library._test_transport.calls  # type: ignore[attr-defined]
            if method == "thread/list" and params.get("sourceKinds") == list(_AGENT_SOURCE_KINDS)
        ]

    async def test_agent_conversation_reads_native_agent_thread(self) -> None:
        library = self._library(read_page=AGENT_THREAD_READ)
        scope = _scope(self.tmp)
        digest = library._cursor_authority.identity_digest(
            "codex", "thr_agent00000001", scope.canonical_project_scope
        )
        ref = NativeConversationRef(
            harness_id="codex",
            vendor_conversation_id="thr_agent00000001",
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        page = await library.read(ref, before=None, limit=10)
        assert page.total_items == 2
        user, assistant = page.items
        assert user.role == "user"
        assert assistant.role == "assistant"
        method, params = library._test_transport.calls[-1]  # type: ignore[attr-defined]
        assert method == "thread/read"
        assert params["threadId"] == "thr_agent00000001"

    async def test_unproven_agent_kinds_degrade_to_exact_note(self) -> None:
        refusal = CodexAppServerRpcError(
            "thread/list",
            -32600,
            "Invalid request: unknown variant `subAgent`, expected one of `cli`, `vscode`",
        )
        library = self._library(agent_page=refusal)
        scope = _scope(self.tmp)
        page = await library.list(scope, cursor=None, limit=25)
        # Top-level listing still works; the unavailable agents are visible with the reason.
        assert len(page.rows) == 2
        assert all(row.agents == () for row in page.rows)
        assert page.agents_note is not None
        assert "unknown variant `subAgent`" in page.agents_note

    async def test_truncated_agent_listing_is_visible(self) -> None:
        truncated = dict(AGENT_PAGE, nextCursor="more-agents")
        library = self._library(agent_page=truncated)  # type: ignore[arg-type]
        scope = _scope(self.tmp)
        page = await library.list(scope, cursor=None, limit=25)
        assert page.agents_note is not None and "truncated" in page.agents_note
        assert len(page.rows[0].agents) == 2  # What was proven still groups.

    async def test_nested_depth2_agents_are_named_not_silently_absent(self) -> None:
        """An agent row whose parent is itself
        an agent thread can never group under a visible top-level row — the page says so."""

        nested_page: JsonObject = {
            "data": [
                *AGENT_PAGE["data"],  # type: ignore[index]
                {
                    "id": "thr_agent00000004",
                    "preview": "Nested child",
                    "name": None,
                    "createdAt": 1730832000,
                    "updatedAt": 1730832500,
                    "status": {"type": "notLoaded"},
                    "parentThreadId": "thr_agent00000001",
                    "agentNickname": None,
                    "agentRole": None,
                    "source": "unknown",
                },
            ],
            "nextCursor": None,
        }
        library = self._library(agent_page=nested_page)
        scope = _scope(self.tmp)
        page = await library.list(scope, cursor=None, limit=25)
        assert page.agents_note is not None
        assert "1 nested sub-agent" in page.agents_note
        parent, _other = page.rows
        # The nested row grouped nowhere; the depth-1 children and the off-page orphan
        # (its parent pages elsewhere) never trip the note.
        assert len(parent.agents) == 2

    async def test_ungroupable_agent_row_fails_closed(self) -> None:
        skewed = {
            "data": [dict(AGENT_PAGE["data"][0], parentThreadId=None)],  # type: ignore[index]
            "nextCursor": None,
        }
        library = self._library(agent_page=skewed)  # type: ignore[arg-type]
        scope = _scope(self.tmp)
        with self.assertRaisesRegex(LibraryStoreError, "shape validation"):
            await library.list(scope, cursor=None, limit=25)


CLAUDE_AGENT_LIST = {
    "signature": "sig-claude-agents",
    "agentsEnumerated": True,
    "rows": [
        {
            "sessionId": "sess-abcdef123456",
            "summary": "parent chat",
            "lastModified": 1784035249127,
            "agents": [
                {
                    "agentId": "ae798f6d07aa5c82a",
                    "agentType": "Explore",
                    "description": "bg-probe",
                    "toolUseId": "toolu_01RaMjyDo95XSVNyA58GpGe6",
                    "spawnDepth": 1,
                    "lastModified": 1784035248000,
                },
                {
                    "agentId": "bf000000000000001",
                    "lastModified": 1784035247000,
                },
            ],
        }
    ],
    "nextCursor": None,
}

CLAUDE_AGENT_READ = {
    "signature": "sig-claude-agent-read",
    "totalItems": 2,
    "hasOlder": False,
    "olderOrdinal": None,
    "items": [
        {
            "ordinal": 1,
            "type": "user",
            "uuid": "agent-uuid-1",
            "parentToolUseId": None,
            "parentAgentId": "ae798f6d07aa5c82a",
            "timestamp": "2026-07-26T07:15:59.582Z",
            "role": "user",
            "content": "read probe.txt",
        },
        {
            "ordinal": 2,
            "type": "assistant",
            "uuid": "agent-uuid-2",
            "parentToolUseId": None,
            "parentAgentId": "ae798f6d07aa5c82a",
            "role": "assistant",
            "content": [{"type": "text", "text": "contents verbatim"}],
        },
    ],
}


class ClaudeLibraryAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _library(self, results: Mapping[str, object]) -> ClaudeConversationLibrary:
        return ClaudeConversationLibrary(
            authorization=CALLER,
            cursor_authority=LibraryCursorAuthority(mint_signing_key()),
            capabilities=_capabilities,  # type: ignore[arg-type]
            helper_host=_FakeHelperHost(results),  # type: ignore[arg-type]
        )

    async def test_agents_group_under_parent_session_with_meta_identity(self) -> None:
        library = self._library({"list": CLAUDE_AGENT_LIST})
        scope = _scope(self.tmp, "claude")  # type: ignore[arg-type]
        page = await library.list(scope, cursor=None, limit=25)
        assert page.agents_note is None
        assert len(page.rows) == 1
        (parent,) = page.rows
        assert len(parent.agents) == 2
        named, fallback = parent.agents
        # Identity comes from the .meta.json evidence only.
        assert named.title == "bg-probe"
        assert named.role == "Explore"
        assert named.join_key == "toolu_01RaMjyDo95XSVNyA58GpGe6"
        assert named.last_activity_at is not None
        assert fallback.title == "agent bf000000"
        assert fallback.role is None and fallback.join_key is None
        # The agent key round-trips to the composite <sessionId>/<agentId> vendor id.
        _binding, vendor = library._cursor_authority.verify_conversation_key(named.conversation_key)
        assert vendor == "sess-abcdef123456/ae798f6d07aa5c82a"

    async def test_helper_without_agent_evidence_is_visibly_unavailable(self) -> None:
        library = self._library(
            {
                "list": {
                    "signature": "sig-list",
                    "rows": [
                        {"sessionId": "sess-1", "summary": "s", "lastModified": 1784035249127}
                    ],
                    "nextCursor": None,
                }
            }
        )
        scope = _scope(self.tmp, "claude")
        page = await library.list(scope, cursor=None, limit=25)
        assert len(page.rows) == 1
        assert page.rows[0].agents == ()
        assert page.agents_note is not None and "no sub-agent evidence" in page.agents_note

    async def test_nested_spawn_depth_agents_are_named_not_silently_absent(self) -> None:
        """A spawnDepth>1 row's real parent is
        another sub-agent — it stays listed AND the page names the grouping limit."""

        library = self._library(
            {
                "list": {
                    "signature": "sig-claude-nested",
                    "agentsEnumerated": True,
                    "rows": [
                        {
                            "sessionId": "sess-abcdef123456",
                            "summary": "parent chat",
                            "lastModified": 1784035249127,
                            "agents": [
                                {
                                    "agentId": "ae798f6d07aa5c82a",
                                    "spawnDepth": 1,
                                    "lastModified": 1784035248000,
                                },
                                {
                                    "agentId": "cf000000000000002",
                                    "spawnDepth": 2,
                                    "lastModified": 1784035247000,
                                },
                            ],
                        }
                    ],
                    "nextCursor": None,
                }
            }
        )
        scope = _scope(self.tmp, "claude")  # type: ignore[arg-type]
        page = await library.list(scope, cursor=None, limit=25)
        assert len(page.rows[0].agents) == 2
        assert page.agents_note is not None
        assert "1 nested sub-agent" in page.agents_note

    async def test_empty_catalog_from_old_helper_is_visibly_unavailable(self) -> None:
        """Over ZERO rows there is no
        row-level ``agents`` key to inspect — the missing response marker degrades to
        the honest note instead of looking like an empty agent catalog."""

        library = self._library({"list": {"signature": "sig-list", "rows": [], "nextCursor": None}})
        scope = _scope(self.tmp, "claude")
        page = await library.list(scope, cursor=None, limit=25)
        assert page.rows == ()
        assert page.agents_note is not None and "no sub-agent evidence" in page.agents_note

    async def test_empty_catalog_with_enumeration_marker_stays_quiet(self) -> None:
        library = self._library(
            {
                "list": {
                    "signature": "sig-list",
                    "agentsEnumerated": True,
                    "rows": [],
                    "nextCursor": None,
                }
            }
        )
        scope = _scope(self.tmp, "claude")
        page = await library.list(scope, cursor=None, limit=25)
        assert page.agents_note is None

    async def test_agent_read_routes_through_helper_with_agent_id(self) -> None:
        library = self._library({"read": CLAUDE_AGENT_READ})
        scope = _scope(self.tmp, "claude")  # type: ignore[arg-type]
        vendor_id = "sess-abcdef123456/ae798f6d07aa5c82a"
        digest = library._cursor_authority.identity_digest(
            "claude", vendor_id, scope.canonical_project_scope
        )
        ref = NativeConversationRef(
            harness_id="claude",
            vendor_conversation_id=vendor_id,
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        page = await library.read(ref, before=None, limit=10)
        assert page.total_items == 2
        user, assistant = page.items
        assert user.role == "user" and user.lane == "unknown-input"
        assert assistant.role == "assistant"
        _harness, operation, payload = library._helper.calls[-1]  # type: ignore[attr-defined]
        assert operation == "read"
        assert payload["vendorConversationId"] == "sess-abcdef123456"
        assert payload["agentId"] == "ae798f6d07aa5c82a"

    async def test_agent_resume_target_fails_closed_with_exact_reason(self) -> None:
        library = self._library({})
        scope = _scope(self.tmp, "claude")
        vendor_id = "sess-abcdef123456/ae798f6d07aa5c82a"
        digest = library._cursor_authority.identity_digest(
            "claude", vendor_id, scope.canonical_project_scope
        )
        ref = NativeConversationRef(
            harness_id="claude",
            vendor_conversation_id=vendor_id,
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        with self.assertRaisesRegex(LibraryStoreError, "no native resume target"):
            await library.resolve_resume_target(ref)
        assert library._helper.calls == []  # type: ignore[attr-defined]

    async def test_agent_row_without_id_fails_closed(self) -> None:
        library = self._library(
            {
                "list": {
                    "signature": "sig-list",
                    "rows": [
                        {
                            "sessionId": "sess-1",
                            "summary": "s",
                            "lastModified": 1,
                            "agents": [{"description": "no id"}],
                        }
                    ],
                    "nextCursor": None,
                }
            }
        )
        scope = _scope(self.tmp, "claude")
        with self.assertRaises(LibraryStoreError):
            await library.list(scope, cursor=None, limit=25)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
