from __future__ import annotations

from pathlib import Path

from agents_remember.serving.harness_logs import CommandEvidence
from agents_remember.serving.injector import (
    DeliveryRow,
    deliver,
    envelope_text,
    verify_or_reissue_command,
)
from agents_remember.serving.terminal_paste import PasteResult


class _Log:
    def __init__(self, *, bound: bool = True) -> None:
        self.bound_path = Path("/tmp/session.jsonl") if bound else None
        self.messages: set[str] = set()
        self.commands: dict[str, CommandEvidence] = {}

    def message_present(self, entry_id: str) -> bool:
        return entry_id in self.messages

    def command_evidence(self, command: str) -> CommandEvidence:
        return self.commands.get(command, CommandEvidence())


class _Paster:
    def __init__(self, log: _Log, *, fail: bool = False, capture: str = "") -> None:
        self.log = log
        self.fail = fail
        self.capture = capture
        self.calls: list[str] = []

    def paste(
        self, _tmux: str, text: str, *, submit: bool = False, accepted=None, **_kwargs: object
    ) -> PasteResult:
        self.calls.append(text)
        if self.fail:
            return PasteResult(delivered=True, submitted=False, capture=self.capture)
        if submit and accepted is not None:
            if "id=" in text:
                entry_id = text.split("id=", 1)[1].split("]", 1)[0]
                self.log.messages.add(entry_id)
                if self.log.bound_path is None:
                    self.log.bound_path = Path("/tmp/session.jsonl")
            elif text.startswith("/"):
                self.log.commands[text] = CommandEvidence(
                    recorded=True, succeeded=True, output="ok"
                )
            return PasteResult(delivered=True, submitted=bool(accepted()))
        return PasteResult(delivered=True, submitted=False)


def test_envelope_guarantees_kind_entry_id_and_ack() -> None:
    text = envelope_text(
        DeliveryRow(kind="nudge", entry_id="E1", text="continue", ack_instruction="reply")
    )
    assert text.startswith("[Agents Remember delivery:nudge id=E1]")
    assert "ack: reply" in text


def test_message_is_acked_from_bound_log_and_returns_provenance() -> None:
    log = _Log()
    result = deliver(
        DeliveryRow(kind="brief", entry_id="E1", text="work"),
        tmux_name="ar-1",
        paster=_Paster(log),  # type: ignore[arg-type]
        harness="codex",
        session_log=log,  # type: ignore[arg-type]
    )
    assert result.outcome == "acked"
    assert result.bound_entry_id == "E1"
    assert result.session_log_path == Path("/tmp/session.jsonl")


def test_submitted_message_without_log_entry_fails_with_diagnostic_capture() -> None:
    log = _Log()
    result = deliver(
        DeliveryRow(kind="brief", entry_id="E1", text="work"),
        tmux_name="ar-1",
        paster=_Paster(log, fail=True, capture="idle pane"),  # type: ignore[arg-type]
        harness="codex",
        session_log=log,  # type: ignore[arg-type]
    )
    assert result.outcome == "failed"
    assert result.verification == ("user-message-entry",)
    assert result.capture == "idle pane"


def test_failure_capture_may_receive_modal_diagnostic_label() -> None:
    log = _Log()
    result = deliver(
        DeliveryRow(kind="message", entry_id="E1", text="work"),
        tmux_name="ar-1",
        paster=_Paster(log, fail=True, capture="Approaching rate limits — switch model?"),  # type: ignore[arg-type]
        harness="codex",
        session_log=log,  # type: ignore[arg-type]
    )
    assert result.outcome == "blocked"
    assert result.reason == "codex-quota-limit"


def test_unbound_spawn_command_is_deferred_not_acked() -> None:
    log = _Log(bound=False)
    result = deliver(
        DeliveryRow(
            kind="session-command",
            entry_id="E1",
            text="/effort ultracode",
            envelope=False,
        ),
        tmux_name="ar-1",
        paster=_Paster(log),  # type: ignore[arg-type]
        harness="claude",
        session_log=log,  # type: ignore[arg-type]
    )
    assert result.outcome == "landed-unacked"
    assert not result.submitted


def test_successful_retroactive_command_is_not_reissued() -> None:
    log = _Log()
    log.commands["/effort ultracode"] = CommandEvidence(
        recorded=True,
        succeeded=True,
        output="Set effort level to ultracode",
    )
    paster = _Paster(log)
    result = verify_or_reissue_command(
        DeliveryRow(
            kind="session-command",
            entry_id="E1",
            text="/effort ultracode",
            envelope=False,
        ),
        tmux_name="ar-1",
        paster=paster,  # type: ignore[arg-type]
        harness="claude",
        session_log=log,  # type: ignore[arg-type]
    )
    assert result.outcome == "acked"
    assert paster.calls == []


def test_errored_command_reissues_only_that_command() -> None:
    log = _Log()
    log.commands["/effort ultracode"] = CommandEvidence(
        recorded=True,
        errored=True,
        output="Invalid argument",
    )
    paster = _Paster(log)
    row = DeliveryRow(
        kind="session-command",
        entry_id="E1",
        text="/effort ultracode",
        envelope=False,
    )
    result = verify_or_reissue_command(
        row,
        tmux_name="ar-1",
        paster=paster,  # type: ignore[arg-type]
        harness="claude",
        session_log=log,  # type: ignore[arg-type]
    )
    assert result.outcome == "acked"
    assert paster.calls == ["/effort ultracode"]
