from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import pytest
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.mcp.tools.hosted_readiness import hosted_session_readiness_payload
from agents_remember.serving.hosted_readiness import HostedReadinessResult, hosted_session_readiness
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry


def _entry(
    session_id: str,
    *,
    status: str = "running",
    created_at: str = "2026-07-12T10:00:00+00:00",
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=session_id,
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at=created_at,
        last_attached_at=created_at,
        status=status,  # type: ignore[arg-type]
    )


class _Host:
    def __init__(self, answers: list[bool] | None = None) -> None:
        self.answers = list(answers or [])
        self.calls: list[str] = []

    def has_session(self, tmux_name: str) -> bool:
        self.calls.append(tmux_name)
        return self.answers.pop(0) if self.answers else True


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


@pytest.fixture
def catalog(tmp_path: Path) -> TerminalCatalog:
    return TerminalCatalog(tmp_path / "terminal-sessions.json")


def test_modern_codex_composer_is_ready_for_exact_session(catalog: TerminalCatalog) -> None:
    catalog.upsert(_entry("target"))
    captures: list[str] = []

    def capture(tmux_name: str) -> str:
        captures.append(tmux_name)
        return (
            "header\n\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase\n\n"
            "  gpt-5.6-sol xhigh fast · ~/Projects"
        )

    result = hosted_session_readiness(
        catalog,
        _Host(),
        session_id="target",
        pane_capturer=capture,
        pane_mode_probe=lambda _name: False,
    )

    assert result.status == "ready"
    assert captures == ["ar-target"]


def test_variable_boot_delay_returns_as_soon_as_ready(catalog: TerminalCatalog) -> None:
    catalog.upsert(_entry("target"))
    panes = iter(
        [
            "starting",
            "still starting",
            "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase",
        ]
    )
    clock = _Clock()
    result = hosted_session_readiness(
        catalog,
        _Host(),
        session_id="target",
        wait_seconds=1.0,
        pane_capturer=lambda _name: next(panes),
        pane_mode_probe=lambda _name: False,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_interval=0.1,
    )

    assert result.status == "ready"
    assert clock.sleeps == [0.1, 0.1]


@pytest.mark.parametrize(
    "pane",
    (
        "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} previous submitted user prompt\n"
        "■ You've hit your usage limit.",
        "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} unrelated existing draft",
    ),
)
def test_codex_history_or_existing_draft_is_not_ready(catalog: TerminalCatalog, pane: str) -> None:
    catalog.upsert(_entry("target"))

    result = hosted_session_readiness(
        catalog,
        _Host(),
        session_id="target",
        pane_capturer=lambda _name: pane,
        pane_mode_probe=lambda _name: False,
    )

    assert result.status == "not-ready"
    assert result.detail == "harness composer is not ready"


def test_zero_bound_readiness_does_not_wait_for_held_writer_batch(
    catalog: TerminalCatalog,
) -> None:
    catalog.upsert(_entry("target"))
    reader = TerminalCatalog(catalog.path)
    finished = threading.Event()
    results: list[HostedReadinessResult] = []

    def read() -> None:
        results.append(
            hosted_session_readiness(
                reader,
                _Host(),
                session_id="target",
                wait_seconds=0,
                pane_capturer=lambda _name: (
                    "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase"
                ),
                pane_mode_probe=lambda _name: False,
            )
        )
        finished.set()

    with catalog.batch():
        catalog.record_turn_state("target", "working", changed_at="2026-07-12T10:05:00+00:00")
        thread = threading.Thread(target=read)
        thread.start()
        assert finished.wait(timeout=0.2)
        thread.join(timeout=0.2)

    assert results[0].status == "ready"


def test_not_ready_wait_is_bounded_and_never_sends_input(catalog: TerminalCatalog) -> None:
    catalog.upsert(_entry("target"))
    clock = _Clock()
    captures: list[str] = []
    result = hosted_session_readiness(
        catalog,
        _Host(),
        session_id="target",
        wait_seconds=0.25,
        pane_capturer=lambda name: captures.append(name) or "booting",
        pane_mode_probe=lambda _name: False,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        poll_interval=0.1,
    )

    assert result.status == "not-ready"
    assert clock.value == pytest.approx(0.25)
    assert len(captures) == 4


@pytest.mark.parametrize("status", ["exited", "landed"])
def test_nonterminal_nonrunning_rows_are_not_ready(catalog: TerminalCatalog, status: str) -> None:
    catalog.upsert(_entry("target", status=status))
    result = hosted_session_readiness(catalog, _Host(), session_id="target")
    assert result.status == "not-ready"
    assert result.detail == f"catalog status is {status}"


def test_only_terminated_status_is_terminal(catalog: TerminalCatalog) -> None:
    catalog.upsert(_entry("target", status="terminated"))
    result = hosted_session_readiness(catalog, _Host(), session_id="target")
    assert result.status == "terminated"


def test_transient_tmux_absence_is_rechecked(catalog: TerminalCatalog) -> None:
    catalog.upsert(_entry("target"))
    clock = _Clock()
    result = hosted_session_readiness(
        catalog,
        _Host([False, True, True]),
        session_id="target",
        wait_seconds=0.5,
        pane_capturer=lambda _name: (
            "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase"
        ),
        pane_mode_probe=lambda _name: False,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result.status == "ready"


def test_copy_mode_is_concrete_not_ready_state(catalog: TerminalCatalog) -> None:
    catalog.upsert(_entry("target"))
    result = hosted_session_readiness(
        catalog,
        _Host(),
        session_id="target",
        pane_capturer=lambda _name: (
            "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase"
        ),
        pane_mode_probe=lambda _name: True,
    )
    assert result.status == "not-ready"
    assert result.detail == "pane is in copy mode"


def test_parallel_session_prompt_cannot_satisfy_target(catalog: TerminalCatalog) -> None:
    catalog.upsert(_entry("target"))
    catalog.upsert(_entry("other"))
    captured: list[str] = []

    def capture(tmux_name: str) -> str:
        captured.append(tmux_name)
        return (
            "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase"
            if tmux_name == "ar-other"
            else "booting"
        )

    result = hosted_session_readiness(
        catalog,
        _Host(),
        session_id="target",
        pane_capturer=capture,
        pane_mode_probe=lambda _name: False,
    )
    assert result.status == "not-ready"
    assert captured == ["ar-target"]


def test_catalog_disappearance_during_observation_is_unknown(catalog: TerminalCatalog) -> None:
    catalog.upsert(_entry("target"))

    def capture(_tmux_name: str) -> str:
        catalog.mark_terminated("target", "2020-01-01T00:00:00+00:00")
        catalog.compact(
            now=datetime.fromisoformat("2026-07-12T10:00:00+00:00"),
            retain_seconds=0,
        )
        return "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase"

    result = hosted_session_readiness(
        catalog,
        _Host(),
        session_id="target",
        pane_capturer=capture,
        pane_mode_probe=lambda _name: False,
    )
    assert result.status == "unknown-session"


def test_replaced_catalog_identity_is_not_the_spawned_session(catalog: TerminalCatalog) -> None:
    catalog.upsert(_entry("target"))

    def capture(_tmux_name: str) -> str:
        catalog.upsert(_entry("target", created_at="2026-07-12T11:00:00+00:00"))
        return "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} Explain this codebase"

    result = hosted_session_readiness(
        catalog,
        _Host(),
        session_id="target",
        pane_capturer=capture,
        pane_mode_probe=lambda _name: False,
    )
    assert result.status == "unknown-session"
    assert "identity changed" in (result.detail or "")


def test_public_wait_has_finite_ceiling(tmp_path: Path) -> None:
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs" / "mcp",
    )
    with pytest.raises(ValueError, match="between 0 and 60"):
        hosted_session_readiness_payload(
            config,
            session_id="target",
            wait_seconds=60.1,
        )
