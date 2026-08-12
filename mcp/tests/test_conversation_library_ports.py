"""Dormant port normalization tests with fake native boundaries (260718-CHATS-L2).

These prove the per-harness resolvers produce the landed normalized grammar (strict
``ConversationItem`` validators included), honest cursor/generation behavior, and exact resume
targets — without touching real harness processes (the installed-runtime suite covers those).
"""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

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
    AppServerSeams,
    CodexConversationLibrary,
)
from agents_remember.serving.conversation.library.cursor import (
    LibraryCursorAuthority,
    mint_signing_key,
)
from agents_remember.serving.conversation.library.errors import (
    CatalogGenerationError,
    LibraryStoreError,
)
from agents_remember.serving.conversation.library.pi import PiConversationLibrary
from agents_remember.serving.conversation.library.scope import canonical_library_scope

CALLER = AuthorizationBinding(principal_id="local-operator:1000", tenant_id="/ws")
CODEX = Harness(id="codex", name="Codex", command="codex", argv=("codex",))


def _caps() -> HistoryCapabilities:
    evidence = CapabilityEvidence(
        runtime_version="0.144.5", fixture_id="test-fixture", observed_at="2026-07-18T00:00:00Z"
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
    """Canned app-server boundary recording every request."""

    def __init__(self, script: Mapping[str, object]) -> None:
        self.script = dict(script)
        self.calls: list[tuple[str, Mapping[str, object]]] = []
        self.stopped = False

    async def start(self, launch: object) -> None:
        self.launch = launch

    async def request(
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
                "userAgent": "Codex Desktop/0.144.5 (Ubuntu; x86_64) Test (agents_remember; 3.0.0)",
                "codexHome": "/home/x/.codex",
                "platformFamily": "unix",
                "platformOs": "linux",
            }
        value = self.script[method]
        if callable(value):
            return value(params)  # type: ignore[return-value]
        if method == "thread/list" and params.get("sourceKinds") not in (
            None,
            ["cli", "vscode", "exec", "appServer"],
        ):
            # The library's additive sub-agent fetch gets an empty page at
            # this fake boundary; the agent-grouping suite owns the non-empty cases.
            return {"data": [], "nextCursor": None}
        return value  # type: ignore[return-value]

    async def notify(self, method: str, params: Mapping[str, object]) -> None:
        self.calls.append((method, params))

    async def messages(self):
        return
        yield

    async def respond(self, request_id: object, result: Mapping[str, object]) -> None:
        del request_id, result

    async def respond_error(self, request_id: object, *, code: int, message: str) -> None:
        del request_id, code, message

    async def stop(self, mode: str) -> None:  # noqa: ARG002 - transport protocol

        self.stopped = True


class _FakeHelperHost:
    def __init__(self, results: Mapping[str, object]) -> None:
        self.results = dict(results)
        self.calls: list[tuple[str, str, Mapping[str, object]]] = []

    async def call(self, harness: str, operation: str, payload: Mapping[str, object]):
        self.calls.append((harness, operation, payload))
        value = self.results[operation]
        if callable(value):
            value = value(payload)
        return value, "9.9.9", "1.2.3"


def _scope(tmp: str, harness: str = "codex"):
    return canonical_library_scope(CALLER, harness, None, workspace_root=Path(tmp))  # type: ignore[arg-type]


def _codex_library(script: Mapping[str, object]) -> CodexConversationLibrary:
    transport = _FakeCodexTransport(script)
    library = CodexConversationLibrary(
        authorization=CALLER,
        cursor_authority=LibraryCursorAuthority(mint_signing_key()),
        capabilities=_capabilities,  # type: ignore[arg-type]
        harness=CODEX,
        seams=AppServerSeams(env=lambda: {}, transport_factory=lambda: transport),
    )
    library._test_transport = transport  # type: ignore[attr-defined]
    return library


THREAD_PAGE = {
    "data": [
        {
            "id": "thr_aaaabbbbcccc",
            "preview": "Fix the tests",
            "name": None,
            "createdAt": 1730831111,
            "updatedAt": 1730839999,
            "status": {"type": "notLoaded"},
        },
        {
            "id": "thr_ddddeeeeffff",
            "preview": "",
            "name": "Named thread",
            "createdAt": 1730830000,
            "updatedAt": 1730831111,
            "status": {"type": "notLoaded"},
        },
    ],
    "nextCursor": "2026-07-17T20:47:03Z",
}

THREAD_READ = {
    "thread": {
        "id": "thr_aaaabbbbcccc",
        "updatedAt": 1730839999,
        "turns": [
            {
                "id": "turn-1",
                "status": "completed",
                "items": [
                    {
                        "type": "userMessage",
                        "id": "u1",
                        "clientId": "req-1",
                        "content": [{"type": "text", "text": "hello codex"}],
                    },
                    {"type": "agentMessage", "id": "a1", "text": "hello **operator**"},
                    {
                        "type": "reasoning",
                        "id": "r1",
                        "summary": ["thinking one", "thinking two"],
                        "content": [],
                    },
                    {
                        "type": "mcpToolCall",
                        "id": "m1",
                        "server": "agents-remember",
                        "tool": "context_packet",
                        "status": "completed",
                        "arguments": {"repo_id": "x"},
                        "result": {"content": [{"type": "text", "text": '{"ok":true}'}]},
                    },
                    {
                        "type": "fileChange",
                        "id": "f1",
                        "changes": [{"path": "/tmp/a.md", "diff": "@@ -1 +1 @@\n-old\n+new"}],
                    },
                    {"type": "webSearch", "id": "w1", "query": "codex"},
                    {"type": "contextCompaction", "id": "c1"},
                ],
            }
        ],
    }
}


class CodexLibraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    async def test_list_maps_rows_keys_and_next_cursor(self) -> None:
        library = _codex_library({"thread/list": THREAD_PAGE})  # type: ignore[arg-type]
        scope = _scope(self.tmp)  # type: ignore[arg-type]
        page = await library.list(scope, cursor=None, limit=25)
        assert page.scope.harness_id == "codex"
        assert len(page.rows) == 2
        first, second = page.rows
        assert first.title == "Fix the tests"
        assert second.title == "Named thread"
        assert first.safe_native_id_suffix == "bbbbcccc"[-6:]
        assert first.last_activity_at is not None and first.last_activity_at.startswith("2024-")
        binding, vendor = library._cursor_authority.verify_conversation_key(first.conversation_key)
        assert vendor == "thr_aaaabbbbcccc"
        assert binding.identity_digest == first.identity_digest
        assert page.next_cursor is not None
        next_binding, position = library._cursor_authority.verify_list_cursor(page.next_cursor)
        assert position == "2026-07-17T20:47:03Z"
        assert next_binding.scope == scope

    async def test_list_generation_mismatch_resets_cursor(self) -> None:
        pages = {"thread/list": THREAD_PAGE}
        library = _codex_library(pages)  # type: ignore[arg-type]
        scope = _scope(self.tmp)  # type: ignore[arg-type]
        page = await library.list(scope, cursor=None, limit=25)
        assert page.next_cursor is not None
        changed = {
            "thread/list": {
                "data": [dict(THREAD_PAGE["data"][0], id="thr_zzzz")],  # type: ignore[index]
                "nextCursor": None,
            }
        }
        library._test_transport.script = changed  # type: ignore[attr-defined]
        with self.assertRaises(CatalogGenerationError):
            await library.list(scope, cursor=page.next_cursor, limit=25)

    async def test_read_normalizes_items_with_ordinals_and_window(self) -> None:
        library = _codex_library({"thread/read": THREAD_READ})  # type: ignore[arg-type]
        scope = _scope(self.tmp)  # type: ignore[arg-type]
        digest = library._cursor_authority.identity_digest(
            "codex", "thr_aaaabbbbcccc", scope.canonical_project_scope
        )
        ref = NativeConversationRef(
            harness_id="codex",
            vendor_conversation_id="thr_aaaabbbbcccc",
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        page = await library.read(ref, before=None, limit=4)
        assert page.total_items == 7
        assert page.has_older is True
        assert [item.global_ordinal for item in page.items] == [4, 5, 6, 7]
        tool, change, unknown, notice = page.items
        assert tool.kind == "tool-call" and tool.blocks[0].type == "tool-input"
        assert tool.blocks[1].type == "tool-output"  # type: ignore[index]
        assert change.blocks[0].type == "diff"
        assert unknown.kind == "unknown-vendor"
        assert notice.kind == "notice" and notice.lane == "system"
        assert page.older_cursor is not None

        older = await library.read(ref, before=page.older_cursor, limit=4)
        assert [item.global_ordinal for item in older.items] == [1, 2, 3]
        user, assistant, reasoning = older.items
        assert user.lane == "unknown-input" and user.source == "native-history"
        assert user.provenance.strength == "native-only" and user.provenance.producer is None
        assert user.correlation is not None and user.correlation.vendor_correlation_id == "req-1"
        assert assistant.role == "assistant" and assistant.blocks[0].type == "markdown"
        assert reasoning.kind == "thinking"
        assert older.has_older is False
        assert older.older_cursor is None

    async def test_shape_skewed_list_payloads_fail_as_store_errors(self) -> None:
        # Generation probe without an array `data`.
        library = _codex_library({"thread/list": {"nextCursor": None}})
        scope = _scope(self.tmp)
        with self.assertRaisesRegex(LibraryStoreError, "shape validation"):
            await library.list(scope, cursor=None, limit=5)

        # A list row missing its id.
        library = _codex_library({"thread/list": {"data": [{"preview": "x"}], "nextCursor": None}})
        with self.assertRaisesRegex(LibraryStoreError, "shape validation"):
            await library.list(scope, cursor=None, limit=5)

        # A non-integer updatedAt timestamp.
        library = _codex_library(
            {
                "thread/list": {
                    "data": [
                        {
                            "id": "thr_1",
                            "preview": "x",
                            "name": None,
                            "createdAt": 1,
                            "updatedAt": "not-a-number",
                            "status": {"type": "notLoaded"},
                        }
                    ],
                    "nextCursor": None,
                }
            }
        )
        with self.assertRaisesRegex(LibraryStoreError, "shape validation"):
            await library.list(scope, cursor=None, limit=5)

        # Review F4: a type-valid but range-absurd timestamp (corrupt store row).
        library = _codex_library(
            {
                "thread/list": {
                    "data": [
                        {
                            "id": "thr_1",
                            "preview": "x",
                            "name": None,
                            "createdAt": 1,
                            "updatedAt": 10**20,
                            "status": {"type": "notLoaded"},
                        }
                    ],
                    "nextCursor": None,
                }
            }
        )
        with self.assertRaisesRegex(LibraryStoreError, "shape validation"):
            await library.list(scope, cursor=None, limit=5)

    async def test_shape_skewed_read_payloads_fail_as_store_errors(self) -> None:
        library = _codex_library({"thread/read": {"thread": {"id": "thr_1"}}})
        scope = _scope(self.tmp)
        digest = library._cursor_authority.identity_digest(
            "codex", "thr_1", scope.canonical_project_scope
        )
        ref = NativeConversationRef(
            harness_id="codex",
            vendor_conversation_id="thr_1",
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        # A thread without a turns array.
        with self.assertRaisesRegex(LibraryStoreError, "shape validation"):
            await library.read(ref, before=None, limit=5)

        # An item without an id.
        library = _codex_library(
            {
                "thread/read": {
                    "thread": {
                        "id": "thr_1",
                        "updatedAt": 1,
                        "turns": [
                            {
                                "id": "turn-1",
                                "status": "completed",
                                "items": [{"type": "userMessage", "content": []}],
                            }
                        ],
                    }
                }
            }
        )
        with self.assertRaisesRegex(LibraryStoreError, "shape validation"):
            await library.read(ref, before=None, limit=5)

    async def test_resolve_mints_exact_resume_target(self) -> None:
        library = _codex_library({"thread/read": THREAD_READ})  # type: ignore[arg-type]
        scope = _scope(self.tmp)  # type: ignore[arg-type]
        digest = library._cursor_authority.identity_digest(
            "codex", "thr_aaaabbbbcccc", scope.canonical_project_scope
        )
        ref = NativeConversationRef(
            harness_id="codex",
            vendor_conversation_id="thr_aaaabbbbcccc",
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        target = await library.resolve_resume_target(ref)
        _binding, vendor, launch = library._cursor_authority.verify_resume_target(target)
        assert vendor == "thr_aaaabbbbcccc"
        assert launch == {"kind": "codex-thread-resume", "threadId": "thr_aaaabbbbcccc"}
        # Existence was proven through a metadata-only read, never a resume mutation.
        methods = [method for method, _ in library._test_transport.calls]  # type: ignore[attr-defined]
        assert methods == ["initialize", "initialized", "thread/read"]
        _method, params = library._test_transport.calls[-1]  # type: ignore[attr-defined]
        assert "includeTurns" not in params


CLAUDE_READ = {
    "signature": "sig-claude",
    "totalItems": 4,
    "hasOlder": False,
    "olderOrdinal": None,
    "items": [
        {
            "ordinal": 1,
            "type": "user",
            "uuid": "uuid-1",
            "parentToolUseId": None,
            "parentAgentId": None,
            "timestamp": "2026-07-14T13:20:37.790Z",
            "role": "user",
            "content": "plain user text",
        },
        {
            "ordinal": 2,
            "type": "assistant",
            "uuid": "uuid-2",
            "parentToolUseId": None,
            "parentAgentId": None,
            "role": "assistant",
            "content": [
                {"type": "text", "text": "answer"},
                {"type": "thinking", "thinking": "ponder"},
                {"type": "tool_use", "id": "toolu_1", "name": "Bash"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png"}},
            ],
        },
        {
            "ordinal": 3,
            "type": "user",
            "uuid": "uuid-3",
            "parentToolUseId": "toolu_1",
            "parentAgentId": None,
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ran ok"}],
        },
        {
            "ordinal": 4,
            "type": "system",
            "uuid": "uuid-4",
            "parentToolUseId": None,
            "parentAgentId": None,
            "role": None,
            "content": None,
        },
    ],
}


class ClaudeLibraryTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_list_rows_and_paging(self) -> None:
        host_results = {
            "list": {
                "signature": "sig-list",
                "rows": [
                    {
                        "sessionId": "sess-abcdef123456",
                        "summary": "sum",
                        "customTitle": "My chat",
                        "lastModified": 1784035249127,
                    }
                ],
                "nextCursor": "25",
            }
        }
        library = self._library(host_results)
        scope = _scope(self.tmp, "claude")  # type: ignore[arg-type]
        page = await library.list(scope, cursor=None, limit=25)
        assert page.rows[0].title == "My chat"
        assert page.rows[0].safe_native_id_suffix == "123456"
        assert page.rows[0].last_activity_at is not None
        assert page.next_cursor is not None
        _binding, position = library._cursor_authority.verify_list_cursor(page.next_cursor)
        assert position == "25"

    async def test_range_absurd_timestamp_fails_as_store_error(self) -> None:
        # Review F4: type-valid, range-absurd lastModified from a corrupt native row.
        library = self._library(
            {
                "list": {
                    "signature": "sig-list",
                    "rows": [{"sessionId": "sess-1", "summary": "s", "lastModified": 10**20}],
                    "nextCursor": None,
                }
            }
        )
        scope = _scope(self.tmp, "claude")
        with self.assertRaisesRegex(LibraryStoreError, "out-of-range timestamp"):
            await library.list(scope, cursor=None, limit=5)

    async def test_read_maps_blocks_roles_and_provenance(self) -> None:
        library = self._library({"read": CLAUDE_READ})
        scope = _scope(self.tmp, "claude")  # type: ignore[arg-type]
        digest = library._cursor_authority.identity_digest(
            "claude", "sess-1", scope.canonical_project_scope
        )
        ref = NativeConversationRef(
            harness_id="claude",
            vendor_conversation_id="sess-1",
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        page = await library.read(ref, before=None, limit=50)
        assert page.total_items == 4
        user, assistant, tool_result, system = page.items
        assert user.role == "user" and user.lane == "unknown-input"
        assert user.created_at == "2026-07-14T13:20:37.790Z"
        assert [block.type for block in assistant.blocks] == [
            "markdown",
            "thinking",
            "tool-input",
            "unknown-vendor",
        ]
        assert assistant.blocks[2].block_id == "toolu_1"
        assert tool_result.role == "tool" and tool_result.kind == "tool-result"
        assert tool_result.correlation is not None
        assert tool_result.correlation.tool_call_id == "toolu_1"
        assert system.role == "system" and system.lane == "system"

    async def test_resolve_mints_argv_resume(self) -> None:
        library = self._library(
            {
                "resolve-resume-target": {
                    "vendorConversationId": "sess-1",
                    "cwd": "/ws",
                    "lastModified": 1,
                }
            }
        )
        scope = _scope(self.tmp, "claude")  # type: ignore[arg-type]
        digest = library._cursor_authority.identity_digest(
            "claude", "sess-1", scope.canonical_project_scope
        )
        ref = NativeConversationRef(
            harness_id="claude",
            vendor_conversation_id="sess-1",
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        target = await library.resolve_resume_target(ref)
        _binding, vendor, launch = library._cursor_authority.verify_resume_target(target)
        assert vendor == "sess-1"
        assert launch == {"kind": "argv", "args": ["--resume", "sess-1"]}


PI_READ = {
    "signature": "sig-pi",
    "totalItems": 4,
    "hasOlder": True,
    "olderOrdinal": 1,
    "items": [
        {
            "ordinal": 1,
            "id": "e1",
            "parentId": None,
            "type": "message",
            "timestamp": "2026-07-15T13:46:11.877Z",
            "message": {"role": "user", "content": "hi pi"},
        },
        {
            "ordinal": 2,
            "id": "e2",
            "parentId": "e1",
            "type": "message",
            "timestamp": "2026-07-15T13:46:12.079Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "toolCall", "id": "tc1", "name": "bash"},
                ],
            },
        },
        {
            "ordinal": 3,
            "id": "e3",
            "parentId": "e2",
            "type": "message",
            "timestamp": "2026-07-15T13:46:13.000Z",
            "message": {
                "role": "toolResult",
                "toolCallId": "tc1",
                "content": [{"type": "text", "text": "done"}],
            },
        },
        {
            "ordinal": 4,
            "id": "e4",
            "parentId": "e3",
            "type": "compaction",
            "timestamp": "2026-07-15T13:47:00.000Z",
            "summary": "compressed",
            "firstKeptEntryId": "e3",
            "tokensBefore": 1000,
            "fromHook": False,
        },
    ],
}


class PiLibraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _library(self, results: Mapping[str, object]) -> PiConversationLibrary:
        return PiConversationLibrary(
            authorization=CALLER,
            cursor_authority=LibraryCursorAuthority(mint_signing_key()),
            capabilities=_capabilities,  # type: ignore[arg-type]
            helper_host=_FakeHelperHost(results),  # type: ignore[arg-type]
        )

    async def test_read_maps_roles_tools_and_notices(self) -> None:
        library = self._library({"read": PI_READ})
        scope = _scope(self.tmp, "pi")  # type: ignore[arg-type]
        digest = library._cursor_authority.identity_digest(
            "pi", "sess-1", scope.canonical_project_scope
        )
        ref = NativeConversationRef(
            harness_id="pi",
            vendor_conversation_id="sess-1",
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        page = await library.read(ref, before=None, limit=10)
        assert page.total_items == 4
        assert page.has_older is True and page.older_cursor is not None
        user, assistant, tool_result, compaction = page.items
        assert user.lane == "unknown-input" and user.provenance.producer is None
        assert [block.type for block in assistant.blocks] == ["markdown", "tool-input"]
        assert assistant.blocks[1].block_id == "tc1"
        assert tool_result.role == "tool" and tool_result.kind == "tool-result"
        assert tool_result.correlation is not None
        assert tool_result.correlation.tool_call_id == "tc1"
        assert compaction.kind == "notice" and compaction.lane == "system"
        assert "compressed" in compaction.blocks[0].text  # type: ignore[attr-defined]

    async def test_resolve_mints_session_file_argv(self) -> None:
        library = self._library(
            {
                "resolve-resume-target": {
                    "vendorConversationId": "sess-1",
                    "sessionFile": "/home/x/.pi/sessions/sess-1.jsonl",
                    "cwd": "/ws",
                }
            }
        )
        scope = _scope(self.tmp, "pi")  # type: ignore[arg-type]
        digest = library._cursor_authority.identity_digest(
            "pi", "sess-1", scope.canonical_project_scope
        )
        ref = NativeConversationRef(
            harness_id="pi",
            vendor_conversation_id="sess-1",
            project_scope=scope.canonical_project_scope,
            identity_digest=digest,
        )
        target = await library.resolve_resume_target(ref)
        _binding, _vendor, launch = library._cursor_authority.verify_resume_target(target)
        assert launch == {
            "kind": "argv",
            "args": ["--session", "/home/x/.pi/sessions/sess-1.jsonl"],
        }


if __name__ == "__main__":
    unittest.main()
