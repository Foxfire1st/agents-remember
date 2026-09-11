"""Disposable process and tmux ownership for the ambient-role scenario."""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.serving.daemon import daemon_dir
from agents_remember.serving.daemon import stop as stop_daemon
from agents_remember.serving.retire import SeatClosure, retire_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from fixture import E2EFixture


def teardown(fixture: E2EFixture, catalog: TerminalCatalog) -> dict[str, object]:
    """Attempt every cleanup leg and return secondary evidence without masking the primary run."""

    now = datetime.now(UTC).isoformat()
    errors: list[dict[str, str]] = []
    try:
        tmux_environment = _isolated_tmux_environment(fixture)
    except RuntimeError as exc:
        tmux_environment = None
        errors.append(_cleanup_error("tmux-isolation", exc))
    host = TerminalHost() if tmux_environment is not None else None
    errors.extend(_teardown_catalog_entries(catalog, host, now=now))
    errors.extend(_stop_dashboard(fixture))
    if tmux_environment is not None:
        errors.extend(_kill_tmux_server(tmux_environment))
    return {"status": "clean" if not errors else "cleanup-failed", "errors": errors}


def _teardown_catalog_entries(
    catalog: TerminalCatalog,
    host: TerminalHost | None,
    *,
    now: str,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    try:
        entries = catalog.list(include_terminated=True)
    except Exception as exc:
        entries = []
        errors.append(_cleanup_error("catalog-list", exc))
    for entry in entries:
        if host is None:
            errors.append(
                {
                    "phase": "terminal-cleanup-refused",
                    "error": "isolated tmux ownership could not be proven",
                    "session": entry.id,
                }
            )
            continue
        if entry.status == "terminated":
            try:
                host.terminate(entry.id, tmux_name=entry.tmux_name)
            except Exception as exc:
                errors.append(_cleanup_error("terminate-retired", exc, session=entry.id))
            continue
        try:
            retire_entry(
                catalog,
                host,
                entry,
                SeatClosure(
                    at=now,
                    reason="ARSPAWN E2E teardown",
                    edge="e2e-teardown",
                ),
            )
        except Exception as exc:
            errors.append(_cleanup_error("retire-active", exc, session=entry.id))
    return errors


def _stop_dashboard(fixture: E2EFixture) -> list[dict[str, str]]:
    try:
        config = load_config(fixture.authority_path)
        stop_daemon(daemon_dir(config))
    except Exception as exc:
        return [_cleanup_error("stop-dashboard", exc)]
    return []


def _kill_tmux_server(environment: dict[str, str]) -> list[dict[str, str]]:
    try:
        tmux = subprocess.run(
            ["tmux", "kill-server"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
    except OSError as exc:
        return [_cleanup_error("kill-tmux-server", exc)]
    if tmux.returncode not in {0, 1}:
        return [
            {
                "phase": "kill-tmux-server",
                "error": f"tmux exited {tmux.returncode}",
            }
        ]
    return []


def _cleanup_error(
    phase: str,
    error: Exception,
    *,
    session: str | None = None,
) -> dict[str, str]:
    evidence = {"phase": phase, "error": f"{type(error).__name__}: {error}"}
    if session is not None:
        evidence["session"] = session
    return evidence


def prepare_tmux_server(fixture: E2EFixture) -> None:
    """Start an isolated tmux server without changing role-pane exit semantics."""

    socket_root = fixture.root / "tmux-runtime"
    socket_root.mkdir(mode=0o700)
    os.environ["TMUX_TMPDIR"] = socket_root.as_posix()
    os.environ.pop("TMUX", None)
    env = _isolated_tmux_environment(fixture)
    env.update(
        {
            "CODEX_HOME": fixture.codex_home.as_posix(),
            "OPENAI_API_KEY": "arspawn-e2e-non-secret",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    subprocess.run(
        ["tmux", "kill-server"],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    anchor = f"arspawn-e2e-anchor-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", anchor, "--", "sleep", "600"],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    subprocess.run(
        ["tmux", "set-option", "-s", "exit-empty", "off"],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    subprocess.run(
        ["tmux", "kill-session", "-t", anchor],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _isolated_tmux_environment(fixture: E2EFixture) -> dict[str, str]:
    """Return the exact fixture-owned tmux environment or refuse destructive commands."""

    expected = (fixture.root / "tmux-runtime").resolve()
    configured = os.environ.get("TMUX_TMPDIR", "").strip()
    if not configured or Path(configured).resolve() != expected:
        raise RuntimeError(f"fixture tmux isolation is not active: expected TMUX_TMPDIR={expected}")
    if os.environ.get("TMUX", "").strip():
        raise RuntimeError("fixture tmux isolation refuses an inherited TMUX server address")
    return dict(os.environ)
