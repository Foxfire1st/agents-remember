"""Locked-helper, installed-fixture, port, and router ownership regressions."""

from __future__ import annotations

import json
import re
from pathlib import Path

from agents_remember.serving.conversation import ports
from agents_remember.serving.conversation.active.api import router as active_router
from agents_remember.serving.conversation.control.api import router as control_router
from agents_remember.serving.conversation.library.api import router as library_router
from agents_remember.serving.conversation.models import RuntimeFixtureEvidence
from agents_remember.serving.conversation.router import CONVERSATION_CHILD_ROUTERS, router
from fastapi.routing import APIRoute

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_ROOT = REPO_ROOT / "mcp" / "native_helpers" / "conversation_library"
FIXTURE_ROOT = REPO_ROOT / "mcp" / "tests" / "fixtures" / "conversation_runtime"


def test_exactly_two_conversation_ports_exist() -> None:
    public_ports = {
        name
        for name, value in vars(ports).items()
        if name.endswith("Port") and isinstance(value, type)
    }
    assert public_ports == {"ActiveConversationPort", "ConversationLibraryPort"}
    assert not hasattr(ports, "NativeControlPort")


def test_root_composes_three_owned_child_routers() -> None:
    assert (
        active_router,
        library_router,
        control_router,
    ) == CONVERSATION_CHILD_ROUTERS
    assert active_router.prefix == "/api/terminal/{ar_session_id}/conversation"
    assert library_router.prefix == "/api/harnesses/{harness_id}/conversations"
    assert control_router.prefix == "/api/terminal/{ar_session_id}"
    # L2 landed the library behavior routes inside its owned child; active and control remain
    # behavior-empty shells for their own leaves.
    assert active_router.routes == []
    assert control_router.routes == []
    library_paths = {
        (tuple(sorted(route.methods or ())), route.path)
        for route in library_router.routes
        if isinstance(route, APIRoute)
    }
    assert library_paths == {
        (("GET",), "/api/harnesses/{harness_id}/conversations"),
        (("GET",), "/api/harnesses/{harness_id}/conversations/{conversation_key}"),
        (("POST",), "/api/harnesses/{harness_id}/conversations/{conversation_key}/open"),
        (("POST",), "/api/harnesses/{harness_id}/conversations/{conversation_key}/open-status"),
        (
            ("POST",),
            "/api/harnesses/{harness_id}/conversations/{conversation_key}/open-reconcile",
        ),
    }
    assert tuple(getattr(route, "original_router", None) for route in router.routes) == (
        active_router,
        library_router,
        control_router,
    )


def test_global_registration_has_one_stable_inclusion_seam() -> None:
    harness_api = (
        REPO_ROOT / "mcp" / "src" / "agents_remember" / "serving" / "harness_control_api.py"
    ).read_text(encoding="utf-8")
    app_source = (
        REPO_ROOT / "mcp" / "src" / "agents_remember" / "serving" / "app.py"
    ).read_text(encoding="utf-8")

    # L0 one-time composition binding: the single registration call now carries the
    # immutable ConversationRuntime authority; still exactly one call and no other seam.
    assert harness_api.count("register_conversation_routes(app, conversation_runtime)") == 1
    assert "register_conversation_routes" not in app_source
    assert "include_router" not in app_source


def test_helper_package_and_lock_select_only_the_exact_repository_dependencies() -> None:
    package = json.loads((HELPER_ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((HELPER_ROOT / "package-lock.json").read_text(encoding="utf-8"))

    assert package["private"] is True
    assert package["dependencies"] == {
        "@anthropic-ai/claude-agent-sdk": "0.3.207",
        "@earendil-works/pi-coding-agent": "0.80.7",
    }
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    assert lock["packages"]["node_modules/@anthropic-ai/claude-agent-sdk"]["version"] == "0.3.207"
    assert (
        lock["packages"]["node_modules/@earendil-works/pi-coding-agent"]["version"]
        == "0.80.7"
    )


def test_helper_runtime_source_has_no_incidental_module_resolution() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((HELPER_ROOT / "src").glob("*.ts"))
        if not path.name.endswith(".test.ts")
    )
    forbidden = (
        "NODE_PATH",
        "npm_config_cache",
        "require.resolve",
        "createRequire",
        "opensrc",
        "/node_modules/",
        "npx ",
    )
    assert all(token not in source for token in forbidden)
    assert sorted(path.name for path in (HELPER_ROOT / "src").glob("*.ts")) == [
        "claude.ts",
        "pi.ts",
        "protocol.test.ts",
        "protocol.ts",
    ]


def test_installed_runtime_fixtures_are_allowlisted_evidence_not_enablement() -> None:
    expected = {
        "codex-0.144.5.json": ("codex", "0.144.5", None),
        "claude-2.1.211.json": ("claude", "2.1.211", "0.3.207"),
        "pi-0.80.7.json": ("pi", "0.80.7", "0.80.7"),
    }
    loaded: dict[str, RuntimeFixtureEvidence] = {}
    for filename, version_tuple in expected.items():
        raw = json.loads((FIXTURE_ROOT / filename).read_text(encoding="utf-8"))
        fixture = RuntimeFixtureEvidence.model_validate(raw)
        loaded[filename] = fixture
        assert (fixture.harness_id, fixture.runtime_version, fixture.helper_version) == version_tuple
        assert fixture.enables_capabilities is False
        assert fixture.redaction_policy == "allowlist-v1"
        assert any(observation.result == "observed" for observation in fixture.observations)

    claude = loaded["claude-2.1.211.json"]
    helper_gate = next(
        item for item in claude.observations if item.operation.startswith("locked-helper/")
    )
    assert helper_gate.result == "not-exercised"
    assert "remain unverified" in helper_gate.reason


def test_runtime_fixtures_contain_no_raw_secret_path_or_conversation_material() -> None:
    serialized = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(FIXTURE_ROOT.glob("*.json"))
    )
    forbidden_patterns = (
        re.compile(r"/(?:home|Users)/"),
        re.compile(r"[A-Za-z]:\\\\Users\\\\"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
        re.compile(r"\bBearer\s+", re.IGNORECASE),
        re.compile(r'"(?:prompt|messageText|raw|cwd|sessionFile|vendorConversationId)"\s*:'),
    )
    assert all(pattern.search(serialized) is None for pattern in forbidden_patterns)
