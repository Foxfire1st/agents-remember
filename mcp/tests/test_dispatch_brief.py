from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.controlplane.operator_inbox_records import create_operator_inbox_entry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.mcp.tools.operator_inbox import operator_inbox_post_payload
from agents_remember.observer import observer_root
from agents_remember.serving.dispatch_brief import (
    DispatchBriefGate,
    fulfill_briefed_expectation,
)
from agents_remember.serving.hosted_readiness import (
    HostedReadinessHost,
    HostedReadinessResult,
    hosted_session_readiness,
)
from agents_remember.serving.inbox_delivery import deliver_inbox_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_paste import (
    DispatchPastePolicy,
    PasteResult,
    TerminalPaster,
)


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _target(root: Path, session_id: str = "worker-1") -> TerminalCatalogEntry:
    log_path = root / f"{session_id}.jsonl"
    log_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"cwd": str(root.resolve())},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return TerminalCatalogEntry(
        id=session_id,
        label="Worker",
        kind="harness",
        harness="codex",
        lifecycle_id="LC-worker",
        cwd=root,
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-07-12T10:00:00+00:00",
        last_attached_at="2026-07-12T10:00:00+00:00",
        status="running",
        leaf_key="repo/master/leaf-1",
        seat_role="worker",
        spawn_role="worker",
        prompt_keywords=("ultracode",),
        session_log_path=log_path,
    )


class _LogPaster:
    def __init__(
        self,
        log_path: Path,
        *,
        accept: bool,
        harness: str = "codex",
        cwd: Path | None = None,
    ) -> None:
        self.log_path = log_path
        self.accept = accept
        self.harness = harness
        self.cwd = cwd
        self.calls: list[tuple[str, str, DispatchPastePolicy | None]] = []

    def paste(
        self,
        tmux_name: str,
        text: str,
        *,
        submit: bool = False,
        accepted=None,
        dispatch_policy: DispatchPastePolicy | None = None,
        **_kwargs: object,
    ) -> PasteResult:
        self.calls.append((tmux_name, text, dispatch_policy))
        if not self.accept:
            return PasteResult(
                delivered=True,
                submitted=False,
                capture="\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} draft",
            )
        with self.log_path.open("a", encoding="utf-8") as handle:
            if self.harness == "claude":
                handle.write(
                    json.dumps(
                        {
                            "type": "user",
                            "cwd": str(self.cwd),
                            "message": {"role": "user", "content": text},
                        }
                    )
                    + "\n"
                )
            else:
                handle.write(
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": text}],
                            },
                        }
                    )
                    + "\n"
                )
        confirmed = bool(accepted()) if accepted is not None else False
        return PasteResult(delivered=True, submitted=submit and confirmed)


def _ready(entry: TerminalCatalogEntry):
    def check(
        _catalog: TerminalCatalog,
        _host: HostedReadinessHost,
        session_id: str,
    ) -> HostedReadinessResult:
        assert session_id == entry.id
        return HostedReadinessResult("ready", entry.id, entry=entry)

    return check


def _gate(entry: TerminalCatalogEntry, *, input_ready: bool = True) -> DispatchBriefGate:
    return DispatchBriefGate(
        input_ready=lambda tmux_name: input_ready and tmux_name == entry.tmux_name,
        readiness=_ready(entry),
    )


def _seed_claude_command(target: TerminalCatalogEntry) -> None:
    assert target.session_log_path is not None
    target.session_log_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "type": "user",
                    "cwd": str(target.cwd.resolve()),
                    "promptId": "command-1",
                    "message": {
                        "role": "user",
                        "content": (
                            "<command-name>/effort</command-name>\n"
                            "<command-args>ultracode</command-args>"
                        ),
                    },
                },
                {
                    "type": "user",
                    "cwd": str(target.cwd.resolve()),
                    "promptId": "command-1",
                    "message": {
                        "role": "user",
                        "content": (
                            "<local-command-stdout>Set effort level to ultracode"
                            "</local-command-stdout>"
                        ),
                    },
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )


def _post(
    root: Path,
    *,
    entry: TerminalCatalogEntry,
    catalog: TerminalCatalog,
    paster: _LogPaster,
    gate: DispatchBriefGate | None = None,
) -> dict:
    return operator_inbox_post_payload(
        _config(root),
        lifecycle_id=None,
        agent_id=entry.id,
        ask="Implement the leaf",
        response="Use the exact task contract.",
        created_by="manager-1",
        created_via="cli",
        sender_agent_id="manager-1",
        sender_role="manager",
        recipient_role="worker",
        message_kind="dispatch-brief",
        deliver_to_hosted=True,
        terminal_catalog=catalog,
        terminal_host=TerminalHost(tmux_probe=lambda name: name == entry.tmux_name),
        terminal_paster=paster,  # type: ignore[arg-type]
        dispatch_readiness=_ready(entry),
        dispatch_gate=gate or _gate(entry),
    )


def test_ready_dispatch_creates_one_exact_row_and_starts_clocks(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = TerminalCatalog(tmp_path / "logs" / "dashboard" / "terminal-sessions.json")
    target = _target(tmp_path)
    catalog.upsert(target)
    paster = _LogPaster(target.session_log_path, accept=True)  # type: ignore[arg-type]

    posted = _post(tmp_path, entry=target, catalog=catalog, paster=paster)

    assert posted["deliveryState"] == "delivered"
    assert posted["deliveryDetail"] == "harness-log-confirmed"
    assert posted["agentId"] == target.id
    current = OperatorInboxStore(observer_root(config)).current()
    assert list(current) == [posted["entryId"]]
    durable = current[posted["entryId"]]
    assert durable.messageKind == "dispatch-brief"
    assert durable.leafKey == "repo/master/leaf-1"
    assert paster.calls[0][0] == target.tmux_name
    assert paster.calls[0][1].count("ultracode") == 1
    assert paster.calls[0][2] == DispatchPastePolicy(
        attempt="initial",
        visible_marker=f"entry: {durable.id}",
        harness="codex",
    )

    expectations = ExpectationRowStore(observer_root(config)).current()
    by_kind = {row.kind: row for row in expectations.values()}
    assert set(by_kind) == {"ack-by", "briefed-by", "turn-report-by"}
    assert by_kind["briefed-by"].state == "met"
    assert by_kind["turn-report-by"].state == "pending"
    assert by_kind["ack-by"].state == "pending"
    assert by_kind["briefed-by"].sourceId == durable.id
    assert by_kind["turn-report-by"].sourceId == durable.id
    assert by_kind["briefed-by"].createdAt == durable.createdAt
    assert by_kind["turn-report-by"].createdAt == durable.createdAt


def test_dispatch_retro_proves_spawn_phase_commands_before_briefed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = TerminalCatalog(tmp_path / "logs" / "dashboard" / "terminal-sessions.json")
    target = replace(
        _target(tmp_path),
        harness="claude",
        session_commands=("/effort ultracode",),
    )
    _seed_claude_command(target)
    catalog.upsert(target)
    assert target.session_log_path is not None
    paster = _LogPaster(
        target.session_log_path,
        accept=True,
        harness="claude",
        cwd=target.cwd.resolve(),
    )

    posted = _post(tmp_path, entry=target, catalog=catalog, paster=paster)

    assert posted["deliveryState"] == "delivered"
    assert posted["deliveryDetail"] == "harness-log-confirmed"
    assert len(paster.calls) == 1
    briefed = next(
        row
        for row in ExpectationRowStore(observer_root(config)).current().values()
        if row.kind == "briefed-by"
    )
    assert briefed.state == "met"


def test_unproven_spawn_phase_command_keeps_brief_pending(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = TerminalCatalog(tmp_path / "logs" / "dashboard" / "terminal-sessions.json")
    target = replace(
        _target(tmp_path),
        harness="claude",
        session_commands=("/effort ultracode",),
    )
    assert target.session_log_path is not None
    target.session_log_path.write_text("", encoding="utf-8")
    catalog.upsert(target)
    paster = _LogPaster(
        target.session_log_path,
        accept=True,
        harness="claude",
        cwd=target.cwd.resolve(),
    )

    posted = _post(tmp_path, entry=target, catalog=catalog, paster=paster)

    assert posted["deliveryState"] == "unconfirmed"
    assert "launch session commands unconfirmed" in posted["deliveryDetail"]
    assert [call[1] for call in paster.calls][-1] == "/effort ultracode"
    briefed = ExpectationRowStore(observer_root(config)).find_by_source(
        posted["entryId"], kind="briefed-by"
    )
    assert briefed is not None and briefed.state == "pending"


def test_non_claude_launch_command_transport_does_not_deadlock_brief(tmp_path: Path) -> None:
    catalog = TerminalCatalog(tmp_path / "logs" / "dashboard" / "terminal-sessions.json")
    target = replace(_target(tmp_path), session_commands=("/mode focused",))
    catalog.upsert(target)
    assert target.session_log_path is not None
    paster = _LogPaster(target.session_log_path, accept=True)

    posted = _post(tmp_path, entry=target, catalog=catalog, paster=paster)

    assert posted["deliveryState"] == "delivered"
    assert posted["deliveryDetail"] == "harness-log-confirmed"
    assert len(paster.calls) == 1


def test_not_ready_refuses_before_creating_row_or_pasting(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = TerminalCatalog(tmp_path / "terminal-sessions.json")
    target = _target(tmp_path)
    catalog.upsert(target)
    paster = _LogPaster(target.session_log_path, accept=True)  # type: ignore[arg-type]

    def not_ready(
        _catalog: TerminalCatalog,
        _host: HostedReadinessHost,
        session_id: str,
    ) -> HostedReadinessResult:
        return HostedReadinessResult("not-ready", session_id, entry=target, detail="booting")

    with pytest.raises(ValueError, match="observed not-ready"):
        operator_inbox_post_payload(
            config,
            lifecycle_id=None,
            agent_id=target.id,
            ask="Brief",
            response="Work",
            created_by="manager-1",
            created_via="cli",
            message_kind="dispatch-brief",
            terminal_catalog=catalog,
            terminal_host=TerminalHost(tmux_probe=lambda _name: True),
            terminal_paster=paster,  # type: ignore[arg-type]
            dispatch_readiness=not_ready,
        )

    assert OperatorInboxStore(observer_root(config)).current() == {}
    assert paster.calls == []
    assert ExpectationRowStore(observer_root(config)).current() == {}


@pytest.mark.parametrize(
    ("agent_id", "deliver_to_hosted"),
    [(None, True), ("worker-1", False)],
)
def test_dispatch_requires_exact_agent_id_and_hosted_delivery(
    tmp_path: Path,
    agent_id: str | None,
    deliver_to_hosted: bool,
) -> None:
    with pytest.raises(ValueError, match="exact agent_id"):
        operator_inbox_post_payload(
            _config(tmp_path),
            lifecycle_id="LC-worker",
            agent_id=agent_id,
            ask="Brief",
            response="Work",
            created_by="manager-1",
            created_via="cli",
            message_kind="dispatch-brief",
            deliver_to_hosted=deliver_to_hosted,
        )


def test_copy_mode_failure_after_readiness_keeps_same_row_pending(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = TerminalCatalog(tmp_path / "logs" / "dashboard" / "terminal-sessions.json")
    target = _target(tmp_path)
    catalog.upsert(target)
    paster = _LogPaster(target.session_log_path, accept=True)  # type: ignore[arg-type]

    posted = _post(
        tmp_path,
        entry=target,
        catalog=catalog,
        paster=paster,
        gate=_gate(target, input_ready=False),
    )

    assert posted["deliveryState"] == "unconfirmed"
    assert "copy mode" in posted["deliveryDetail"]
    assert paster.calls == []
    current = OperatorInboxStore(observer_root(config)).current()
    assert list(current) == [posted["entryId"]]
    briefed = ExpectationRowStore(observer_root(config)).find_by_source(
        posted["entryId"], kind="briefed-by"
    )
    assert briefed is not None and briefed.state == "pending"


def test_immediate_readiness_recheck_failure_keeps_same_row_pending(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = TerminalCatalog(tmp_path / "logs" / "dashboard" / "terminal-sessions.json")
    target = _target(tmp_path)
    catalog.upsert(target)
    paster = _LogPaster(target.session_log_path, accept=True)  # type: ignore[arg-type]

    def not_ready(
        _catalog: TerminalCatalog,
        _host: HostedReadinessHost,
        session_id: str,
    ) -> HostedReadinessResult:
        return HostedReadinessResult("not-ready", session_id, entry=target, detail="copy-mode")

    posted = _post(
        tmp_path,
        entry=target,
        catalog=catalog,
        paster=paster,
        gate=DispatchBriefGate(input_ready=lambda _tmux_name: True, readiness=not_ready),
    )

    assert posted["deliveryState"] == "unconfirmed"
    assert "not-ready" in posted["deliveryDetail"]
    assert paster.calls == []
    current = OperatorInboxStore(observer_root(config)).current()
    assert list(current) == [posted["entryId"]]
    briefed = ExpectationRowStore(observer_root(config)).find_by_source(
        posted["entryId"], kind="briefed-by"
    )
    assert briefed is not None and briefed.state == "pending"


def test_dispatch_gate_checks_identity_before_copy_mode_cancellation(tmp_path: Path) -> None:
    catalog = TerminalCatalog(tmp_path / "terminal-sessions.json")
    target = _target(tmp_path)
    replacement = replace(
        target,
        tmux_name="ar-replacement",
        created_at="2026-07-12T11:00:00+00:00",
    )
    cancelled: list[str] = []

    def replaced(
        _catalog: TerminalCatalog,
        _host: HostedReadinessHost,
        session_id: str,
    ) -> HostedReadinessResult:
        return HostedReadinessResult("ready", session_id, entry=replacement)

    detail = DispatchBriefGate(
        input_ready=lambda tmux_name: not cancelled.append(tmux_name),
        readiness=replaced,
    ).check(catalog, TerminalHost(tmux_probe=lambda _name: True), target)

    assert detail == "dispatch target identity changed before copy-mode cancellation; no input sent"
    assert cancelled == []


def test_dispatch_gate_rechecks_same_identity_after_copy_mode_cancellation(tmp_path: Path) -> None:
    catalog = TerminalCatalog(tmp_path / "terminal-sessions.json")
    target = _target(tmp_path)
    replacement = replace(
        target,
        tmux_name="ar-replacement",
        created_at="2026-07-12T11:00:00+00:00",
    )
    observations = iter((target, replacement))
    cancelled: list[str] = []

    def changing(
        _catalog: TerminalCatalog,
        _host: HostedReadinessHost,
        session_id: str,
    ) -> HostedReadinessResult:
        return HostedReadinessResult("ready", session_id, entry=next(observations))

    detail = DispatchBriefGate(
        input_ready=lambda tmux_name: not cancelled.append(tmux_name),
        readiness=changing,
    ).check(catalog, TerminalHost(tmux_probe=lambda _name: True), target)

    assert detail == "dispatch target identity changed after copy-mode cancellation; no input sent"
    assert cancelled == [target.tmux_name]


def test_failed_delivery_retries_original_row_with_recovery_policy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    catalog = TerminalCatalog(tmp_path / "logs" / "dashboard" / "terminal-sessions.json")
    target = _target(tmp_path)
    catalog.upsert(target)
    failed_paster = _LogPaster(target.session_log_path, accept=False)  # type: ignore[arg-type]
    posted = _post(tmp_path, entry=target, catalog=catalog, paster=failed_paster)
    assert posted["deliveryState"] == "unconfirmed"

    store = OperatorInboxStore(observer_root(config))
    original = store.current()[posted["entryId"]]
    retry_paster = _LogPaster(target.session_log_path, accept=True)  # type: ignore[arg-type]
    delivered = deliver_inbox_entry(
        store=store,
        catalog=catalog,
        host=TerminalHost(tmux_probe=lambda _name: True),
        paster=retry_paster,  # type: ignore[arg-type]
        entry=original,
        dispatch_gate=_gate(target),
    )
    fulfill_briefed_expectation(ExpectationRowStore(observer_root(config)), delivered)

    assert delivered.id == original.id
    assert delivered.deliveryState == "delivered"
    assert retry_paster.calls[0][2] == DispatchPastePolicy(
        attempt="recovery",
        visible_marker=f"entry: {original.id}",
        harness="codex",
    )
    assert list(store.current()) == [original.id]
    briefed = ExpectationRowStore(observer_root(config)).find_by_source(
        original.id, kind="briefed-by"
    )
    assert briefed is None
    current_expectations = ExpectationRowStore(observer_root(config)).current()
    assert (
        next(row for row in current_expectations.values() if row.kind == "briefed-by").state
        == "met"
    )


def test_production_recovery_gate_submits_own_visible_draft_without_repaste(
    tmp_path: Path,
) -> None:
    catalog = TerminalCatalog(tmp_path / "terminal-sessions.json")
    target = _target(tmp_path)
    catalog.upsert(target)
    store = OperatorInboxStore(tmp_path / "observer")
    entry = create_operator_inbox_entry(
        entry_id="dispatch-1",
        now="2026-07-12T10:05:00+00:00",
        lifecycle_id=target.lifecycle_id,
        agent_id=target.id,
        ask="Brief",
        response="Work",
        created_by="manager",
        created_via="cli",
        message_kind="dispatch-brief",
    )
    store.append(entry)
    entry = store.record_delivery(
        entry.id,
        now="2026-07-12T10:06:00+00:00",
        delivery_state="unconfirmed",
        delivered_to_session=target.id,
        delivery_detail="draft visible after suppressed Enter",
    )
    capture = (
        "[Pasted Content 200 chars]\n"
        "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} entry: dispatch-1"
    )
    loaded: list[str] = []
    keys: list[str] = []

    def send_key(_tmux_name: str, key: str) -> bool:
        keys.append(key)
        assert target.session_log_path is not None
        with target.session_log_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "entry: dispatch-1"}],
                        },
                    }
                )
                + "\n"
            )
        return True

    paster = TerminalPaster(
        load_buffer=lambda _name, text: not loaded.append(text),
        paste_buffer=lambda _tmux, _buffer: True,
        send_key=send_key,
        capture_pane=lambda _tmux: capture,
        sleep=lambda _seconds: None,
    )

    def observe(
        observed_catalog: TerminalCatalog,
        host: HostedReadinessHost,
        session_id: str,
    ) -> HostedReadinessResult:
        return hosted_session_readiness(
            observed_catalog,
            host,
            session_id=session_id,
            pane_capturer=lambda _tmux: capture,
            pane_mode_probe=lambda _tmux: False,
        )

    delivered = deliver_inbox_entry(
        store=store,
        catalog=catalog,
        host=TerminalHost(tmux_probe=lambda _name: True),
        paster=paster,
        entry=entry,
        dispatch_gate=DispatchBriefGate(
            input_ready=lambda _tmux: True,
            readiness=observe,
        ),
    )

    assert delivered.deliveryState == "delivered"
    assert delivered.deliveryDetail == "harness-log-confirmed"
    assert loaded == []
    assert keys == ["Enter"]


def test_exact_agent_target_never_falls_back_to_matching_lifecycle(tmp_path: Path) -> None:
    catalog = TerminalCatalog(tmp_path / "terminal-sessions.json")
    target = _target(tmp_path, "worker-1")
    catalog.upsert(target)
    store = OperatorInboxStore(tmp_path / "observer")
    entry = create_operator_inbox_entry(
        entry_id="dispatch-1",
        now="2026-07-12T10:05:00+00:00",
        lifecycle_id=target.lifecycle_id,
        agent_id="missing-session",
        ask="Brief",
        response="Work",
        created_by="manager",
        created_via="cli",
        message_kind="dispatch-brief",
    )
    store.append(entry)
    paster = _LogPaster(target.session_log_path, accept=True)  # type: ignore[arg-type]
    delivered = deliver_inbox_entry(
        store=store,
        catalog=catalog,
        host=TerminalHost(tmux_probe=lambda _name: True),
        paster=paster,  # type: ignore[arg-type]
        entry=entry,
        dispatch_gate=_gate(target),
    )

    assert delivered.deliveryState == "no-hosted-session"
    assert delivered.deliveredToSession is None
    assert paster.calls == []


@pytest.mark.parametrize(
    "relative",
    (
        Path("l-01-agent-lifecycles/SKILL.md"),
        Path("l-01-agent-lifecycles/roles/architect.md"),
        Path("l-01-agent-lifecycles/roles/orchestrator.md"),
        Path("l-01-agent-lifecycles/roles/manager.md"),
    ),
)
def test_dispatch_instructions_encode_three_states_and_are_synced(relative: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    canonical = (repo_root / "skills" / relative).read_text(encoding="utf-8")
    packaged = (
        repo_root
        / "mcp"
        / "src"
        / "agents_remember"
        / "package_data"
        / "runtime"
        / "skills"
        / relative
    ).read_text(encoding="utf-8")

    normalized = " ".join(canonical.split())
    for phrase in (
        "spawned-unbriefed",
        "hosted_session_readiness",
        "dispatch-brief",
        "deliveryState=delivered",
        "deliveryDetail=harness-log-confirmed",
        "not active work",
    ):
        assert phrase in normalized
    assert packaged == canonical
