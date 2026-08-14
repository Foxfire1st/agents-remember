"""Library cursor/key authority and canonical scope contract tests (260718-CHATS-L2)."""

from __future__ import annotations

import pytest
from agents_remember.models.conversations.cursors import (
    LibraryConversationKey,
    NativeResumeTarget,
)
from agents_remember.models.conversations.identity import (
    AuthorizationBinding,
)
from agents_remember.serving.conversation.library.cursor import (
    LibraryCursorAuthority,
    mint_signing_key,
)
from agents_remember.serving.conversation.library.errors import (
    InvalidLibraryCursorError,
    LibraryScopeError,
)
from agents_remember.serving.conversation.library.scope import (
    canonical_library_scope,
    clamp_limit,
    query_digest,
)

CALLER = AuthorizationBinding(principal_id="local-operator:1000", tenant_id="/ws")
OTHER = AuthorizationBinding(principal_id="local-operator:1001", tenant_id="/ws")


def _scope(tmp_path, harness: str = "codex"):
    return canonical_library_scope(CALLER, harness, None, workspace_root=tmp_path)  # type: ignore[arg-type]


def _authority() -> LibraryCursorAuthority:
    return LibraryCursorAuthority(mint_signing_key())


def test_list_cursor_round_trip_and_tamper_rejection(tmp_path) -> None:
    authority = _authority()
    scope = _scope(tmp_path)
    cursor = authority.mint_list_cursor(scope, catalog_generation=7, native_cursor="native-abc")
    binding, position = authority.verify_list_cursor(cursor)
    assert binding.scope == scope
    assert binding.purpose == "library-list"
    assert binding.catalog_generation == 7
    assert position == "native-abc"

    token = str(cursor)
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    with pytest.raises(InvalidLibraryCursorError):
        authority.verify_list_cursor(type(cursor)(tampered))


def test_read_cursor_round_trip_and_wrong_purpose_rejection(tmp_path) -> None:
    authority = _authority()
    scope = _scope(tmp_path)
    read_cursor = authority.mint_read_cursor(scope, catalog_generation=3, native_cursor=41)
    binding, position = authority.verify_read_cursor(read_cursor)
    assert binding.purpose == "library-read"
    assert position == 41

    with pytest.raises(InvalidLibraryCursorError):
        authority.verify_list_cursor(read_cursor)  # type: ignore[arg-type]

    list_cursor = authority.mint_list_cursor(scope, catalog_generation=3, native_cursor="x")
    with pytest.raises(InvalidLibraryCursorError):
        authority.verify_read_cursor(list_cursor)  # type: ignore[arg-type]


def test_foreign_key_signature_fails_closed(tmp_path) -> None:
    authority = _authority()
    foreign = _authority()
    cursor = authority.mint_list_cursor(_scope(tmp_path), catalog_generation=1, native_cursor="x")
    with pytest.raises(InvalidLibraryCursorError, match="signature"):
        foreign.verify_list_cursor(cursor)


def test_conversation_key_round_trip_and_garbage_rejection(tmp_path) -> None:
    authority = _authority()
    scope = _scope(tmp_path)
    digest = authority.identity_digest("codex", "thread-1", scope.canonical_project_scope)
    key = authority.mint_conversation_key(
        scope, vendor_conversation_id="thread-1", identity_digest=digest, catalog_generation=9
    )
    binding, vendor = authority.verify_conversation_key(key)
    assert vendor == "thread-1"
    assert binding.identity_digest == digest
    assert binding.catalog_generation == 9

    with pytest.raises(InvalidLibraryCursorError):
        authority.verify_conversation_key(LibraryConversationKey("ar-lck1.not-a-token"))


def test_resume_target_round_trip_and_garbage_rejection(tmp_path) -> None:
    authority = _authority()
    scope = _scope(tmp_path)
    digest = authority.identity_digest("pi", "sess-1", scope.canonical_project_scope)
    target = authority.mint_resume_target(
        scope,
        vendor_conversation_id="sess-1",
        identity_digest=digest,
        catalog_generation=2,
        launch={"kind": "argv", "args": ["--session", "/tmp/file.jsonl"]},
    )
    binding, vendor, launch = authority.verify_resume_target(target)
    assert vendor == "sess-1"
    assert launch["kind"] == "argv"
    assert binding.catalog_generation == 2

    with pytest.raises(InvalidLibraryCursorError):
        authority.verify_resume_target(NativeResumeTarget("ar-nrt1.e30="))


def test_identity_digest_is_stable_scope_and_vendor_sensitive() -> None:
    authority = _authority()
    first = authority.identity_digest("codex", "t1", "/ws")
    assert first == authority.identity_digest("codex", "t1", "/ws")
    assert first != authority.identity_digest("codex", "t2", "/ws")
    assert first != authority.identity_digest("codex", "t1", "/other")
    assert first != authority.identity_digest("pi", "t1", "/ws")
    assert first.startswith("sha256:")
    assert _authority().identity_digest("codex", "t1", "/ws") != first


def test_catalog_generation_is_content_derived_and_positive() -> None:
    one = LibraryCursorAuthority.catalog_generation("sig-a")
    assert one == LibraryCursorAuthority.catalog_generation("sig-a")
    assert one != LibraryCursorAuthority.catalog_generation("sig-b")
    assert 1 <= one <= 2**53


def test_query_digest_binds_harness_scope_and_sort() -> None:
    assert query_digest("codex", "/ws") == query_digest("codex", "/ws")
    assert query_digest("codex", "/ws") != query_digest("pi", "/ws")
    assert query_digest("codex", "/ws") != query_digest("codex", "/other")


def test_canonical_scope_defaults_to_root(tmp_path) -> None:
    scope = canonical_library_scope(CALLER, "codex", None, workspace_root=tmp_path)
    assert scope.canonical_project_scope == str(tmp_path.resolve())
    assert scope.authorization == CALLER
    assert scope.harness_id == "codex"


def test_canonical_scope_narrows_inside_root(tmp_path) -> None:
    child = tmp_path / "sub" / "project"
    child.mkdir(parents=True)
    scope = canonical_library_scope(CALLER, "pi", str(child), workspace_root=tmp_path)
    assert scope.canonical_project_scope == str(child.resolve())
    relative = canonical_library_scope(CALLER, "pi", "sub/project", workspace_root=tmp_path)
    assert relative.canonical_project_scope == str(child.resolve())


def test_canonical_scope_rejects_traversal_symlink_and_cross_scope(tmp_path) -> None:
    outside = tmp_path.parent
    with pytest.raises(LibraryScopeError):
        canonical_library_scope(CALLER, "codex", str(outside), workspace_root=tmp_path)
    with pytest.raises(LibraryScopeError):
        canonical_library_scope(CALLER, "codex", "..", workspace_root=tmp_path)
    with pytest.raises(LibraryScopeError):
        canonical_library_scope(CALLER, "codex", "/etc", workspace_root=tmp_path)
    with pytest.raises(LibraryScopeError):
        canonical_library_scope(CALLER, "codex", "missing-dir", workspace_root=tmp_path)
    file_inside = tmp_path / "file.txt"
    file_inside.write_text("x")
    with pytest.raises(LibraryScopeError):
        canonical_library_scope(CALLER, "codex", str(file_inside), workspace_root=tmp_path)

    link = tmp_path / "escape-link"
    link.symlink_to(outside)
    with pytest.raises(LibraryScopeError):
        canonical_library_scope(CALLER, "codex", str(link), workspace_root=tmp_path)


def test_clamp_limit_bounds_and_rejects() -> None:
    assert clamp_limit(None, default=50, maximum=100) == 50
    assert clamp_limit(1, default=50, maximum=100) == 1
    assert clamp_limit(500, default=50, maximum=100) == 100
    with pytest.raises(InvalidLibraryCursorError):
        clamp_limit(0, default=50, maximum=100)
