from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents_remember.serving.harness_logs import HarnessSessionLog


def _write(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")


def test_claude_log_binding_and_command_evidence(tmp_path: Path) -> None:
    cwd = (tmp_path / "workspace").resolve()
    cwd.mkdir()
    project = tmp_path / "claude" / str(cwd).replace("/", "-")
    path = project / "session.jsonl"
    prompt = "p1"
    _write(
        path,
        [
            {"type": "user", "cwd": str(cwd), "promptId": prompt, "message": {"role": "user", "content": "<command-name>/effort</command-name>\n<command-args>ultracode</command-args>"}},
            {"type": "user", "cwd": str(cwd), "promptId": prompt, "message": {"role": "user", "content": "<local-command-stdout>Set effort level to ultracode</local-command-stdout>"}},
            {"type": "user", "cwd": str(cwd), "message": {"role": "user", "content": "[Agents Remember delivery:brief id=E1]\n\nwork"}},
        ],
    )
    log = HarnessSessionLog(
        harness="claude",
        cwd=cwd,
        started_at=datetime.now(UTC) - timedelta(seconds=5),
        claude_root=tmp_path / "claude",
        codex_root=tmp_path / "codex",
    )
    assert log.message_present("E1")
    assert log.bound_path == path.resolve()
    evidence = log.command_evidence("/effort ultracode")
    assert evidence.recorded
    assert evidence.succeeded
    assert not evidence.errored


def test_claude_errored_command_is_not_success(tmp_path: Path) -> None:
    cwd = (tmp_path / "workspace").resolve()
    cwd.mkdir()
    path = tmp_path / "claude" / str(cwd).replace("/", "-") / "session.jsonl"
    _write(
        path,
        [
            {"type": "user", "cwd": str(cwd), "message": {"role": "user", "content": "<command-name>/effort</command-name>\n<command-args>ultracode</command-args>"}},
            {"type": "user", "cwd": str(cwd), "message": {"role": "user", "content": "<local-command-stdout>Invalid argument: ultracode. Valid options are: low, high</local-command-stdout>"}},
        ],
    )
    log = HarnessSessionLog(
        harness="claude",
        cwd=cwd,
        started_at=datetime.now(UTC) - timedelta(seconds=5),
        bound_path=path,
        claude_root=tmp_path / "claude",
        codex_root=tmp_path / "codex",
    )
    evidence = log.command_evidence("/effort ultracode")
    assert evidence.recorded
    assert evidence.errored
    assert not evidence.succeeded


def test_codex_message_binds_only_matching_cwd(tmp_path: Path) -> None:
    cwd = (tmp_path / "workspace").resolve()
    cwd.mkdir()
    started_at = datetime.now(UTC) - timedelta(seconds=5)
    path = tmp_path / "codex" / started_at.strftime("%Y/%m/%d") / "rollout.jsonl"
    _write(
        path,
        [
            {"type": "session_meta", "payload": {"cwd": str(cwd)}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "[Agents Remember delivery:brief id=E2]\n\nwork"}]}},
        ],
    )
    log = HarnessSessionLog(
        harness="codex",
        cwd=cwd,
        started_at=started_at,
        claude_root=tmp_path / "claude",
        codex_root=tmp_path / "codex",
    )
    assert log.message_present("E2")
    assert log.bound_path == path.resolve()

    wrong = HarnessSessionLog(
        harness="codex",
        cwd=tmp_path / "other",
        started_at=started_at,
        claude_root=tmp_path / "claude",
        codex_root=tmp_path / "codex",
    )
    assert not wrong.message_present("E2")


def test_partial_trailing_jsonl_record_is_ignored_until_complete(tmp_path: Path) -> None:
    cwd = (tmp_path / "workspace").resolve()
    cwd.mkdir()
    path = tmp_path / "codex" / "rollout.jsonl"
    _write(path, [{"type": "session_meta", "payload": {"cwd": str(cwd)}}])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"response_item"')
    log = HarnessSessionLog(
        harness="codex",
        cwd=cwd,
        started_at=datetime.now(UTC) - timedelta(seconds=5),
        claude_root=tmp_path / "claude",
        codex_root=tmp_path / "codex",
    )
    assert not log.message_present("E3")
    _write(
        path,
        [
            {"type": "session_meta", "payload": {"cwd": str(cwd)}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "id=E3"}]}},
        ],
    )
    assert log.message_present("E3")
