"""The generated session-start hooks, run as processes (MIK-R113 rule 6, last bullet).

A hook gives its text to a chat that the product did not start and says nothing to a role agent.
It tells the two apart by the host's own record of its agent: the host gives every harness process
``PASEO_HOME`` and ``PASEO_AGENT_ID``, and the product labels each agent it launches ``ar.role``.
Whenever the hook cannot tell, it speaks; it never fails a session start. Each case below runs
the committed, generated hook file of a harness against a made-up host home.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
HOOKS = {
    harness: REPOSITORY_ROOT / folder / "hooks" / "agents-remember-session-start.py"
    for harness, folder in (
        ("claude", ".claude"),
        ("codex", ".codex"),
        ("cursor", ".cursor"),
        ("vscode", ".github-vscode"),
    )
}
AGENT_ID = "0b6f2a54-8c1d-4e7a-9f30-5d2c7e1a4b96"
OTHER_AGENT_ID = "7d3e9c10-2b4a-4f6e-8a51-c09e3f7b2d48"
WORKING_FOLDER = "home-user-projects"
ROLE_SENTENCE = "An agent whose first message is the hand-over of a role is not meant by this"


def _record(home: Path, agent_id: str, content: str | bytes) -> Path:
    path = home / "agents" / WORKING_FOLDER / f"{agent_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def _labelled(role: str | None) -> str:
    labels = {"ar.request-id": "made-up"} if role is None else {"ar.role": role}
    return json.dumps(
        {"id": AGENT_ID, "provider": "codex", "title": "SECRET TITLE", "labels": labels}
    )


def _run(hook: Path, variables: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """One start of the hook, with only the named variables beside what a process needs."""
    environment = {"PATH": os.environ.get("PATH", ""), "PWD": REPOSITORY_ROOT.as_posix()}
    return subprocess.run(
        [sys.executable, "-I", hook.as_posix()],
        cwd=REPOSITORY_ROOT,
        env={**environment, **variables},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _text(completed: subprocess.CompletedProcess[str]) -> str:
    """The text a speaking hook hands its harness, whichever envelope the harness reads."""
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    inner = payload.get("hookSpecificOutput", payload)
    return str(inner.get("additionalContext", inner.get("additional_context")))


def _host(home: Path, agent_id: str = AGENT_ID) -> dict[str, str]:
    return {"PASEO_HOME": home.as_posix(), "PASEO_AGENT_ID": agent_id}


@pytest.mark.parametrize("harness", sorted(HOOKS))
def test_a_role_agent_of_the_host_gets_no_text(harness: str, tmp_path: Path) -> None:
    _record(tmp_path, AGENT_ID, _labelled("manager"))

    completed = _run(HOOKS[harness], _host(tmp_path))

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""


@pytest.mark.parametrize("harness", sorted(HOOKS))
@pytest.mark.parametrize(
    "case",
    [
        "label-absent",
        "no-record",
        "unreadable-record",
        "record-of-another-shape",
        "no-host-names",
        "id-of-another-form",
        "names-of-the-tool-server-alone",
    ],
)
def test_the_hook_speaks_whenever_it_cannot_tell(harness: str, case: str, tmp_path: Path) -> None:
    hook = HOOKS[harness]
    variables = _host(tmp_path)
    if case == "label-absent":
        _record(tmp_path, AGENT_ID, _labelled(None))
    elif case == "no-record":
        # The role agent's record is another agent's: this session's agent has none.
        _record(tmp_path, OTHER_AGENT_ID, _labelled("manager"))
    elif case == "unreadable-record":
        _record(tmp_path, AGENT_ID, b"\xff\xfe{ not a record")
    elif case == "record-of-another-shape":
        _record(tmp_path, AGENT_ID, json.dumps(["ar.role", {"labels": "ar.role"}]))
    elif case == "no-host-names":
        _record(tmp_path, AGENT_ID, _labelled("manager"))
        variables = {}
    elif case == "id-of-another-form":
        # A pattern in place of an id would find the labelled record of any agent.
        _record(tmp_path, AGENT_ID, _labelled("manager"))
        variables = _host(tmp_path, "*")
    else:
        # The binding of a launch goes to the agent's tool server; the hook does not read it.
        variables = {"AR_PASEO_AGENT_ID": AGENT_ID, "AR_ROLE": "manager", "AR_REQUEST_ID": "r-1"}

    text = _text(_run(hook, variables))

    assert text == hook.with_suffix(".md").read_text(encoding="utf-8")
    assert "role_start" in text
    assert ROLE_SENTENCE in text
    assert "SECRET TITLE" not in text


def test_an_id_that_would_leave_the_host_home_builds_no_path(tmp_path: Path) -> None:
    home = tmp_path / "host"
    outside = tmp_path / "agents-elsewhere" / WORKING_FOLDER / f"{AGENT_ID}.json"
    outside.parent.mkdir(parents=True)
    outside.write_text(_labelled("manager"), encoding="utf-8")
    (home / "agents" / WORKING_FOLDER).mkdir(parents=True)

    completed = _run(
        HOOKS["claude"], _host(home, f"../../../agents-elsewhere/{WORKING_FOLDER}/{AGENT_ID}")
    )

    assert ROLE_SENTENCE in _text(completed)
