"""The internal command that starts a leaf enclosure in a process of its own, and its caller."""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.application.worktree_tool_requests import StartExecution, TaskIdentity
from agents_remember.cli import leaf_enclosure_start
from agents_remember.cli.__main__ import build_parser, main
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
    RepositoryScope,
)

IDENTITY = TaskIdentity(
    repo_id="sandbox-app",
    task_name="sbx-text-helpers",
    worktree_name="01-slugify-0123456789",
    leaf_id="SBX-M1-L1",
    parent_task="sbx-sprint",
)


def backend_config(root: Path, *, repository: bool = True) -> McpRuntimeConfig:
    scope = RepositoryScope(
        repo_id="sandbox-app", path=root / "projects" / "sandbox-app", memory_root=root / "memory"
    )
    return McpRuntimeConfig(
        config_path=root / "settings" / "no-such-settings.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
        repositories={"sandbox-app": scope} if repository else {},
    )


def request_of(config: McpRuntimeConfig) -> dict[str, Any]:
    """The request the backend that holds ``config`` sends for ``IDENTITY``."""

    return {
        "repoId": "sandbox-app",
        "taskName": "sbx-text-helpers",
        "worktreeName": "01-slugify-0123456789",
        "leafId": "SBX-M1-L1",
        "parentTask": "sbx-sprint",
        "coordinationRoot": config.coordination_root.as_posix(),
        "codeRoot": (config.workspace_root / "sandbox-app").as_posix(),
        "memoryRoot": (config.workspace_root.parent / "memory").as_posix(),
    }


class LeafEnclosureStartCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.loaded = backend_config(self.root)

    def run_command(self, request: Any, start: Any) -> tuple[int, str, list[str]]:
        """Run the command in this process with the settings and the worktree owner replaced."""

        order: list[str] = []

        def load(path: str) -> McpRuntimeConfig:
            order.append(f"settings loaded from {path}")
            return self.loaded

        def start_tool(config: Any, identity: TaskIdentity, **options: Any) -> dict[str, Any]:
            order.append("worktree start")
            self.assertEqual((config, identity), (self.loaded, IDENTITY))
            self.assertEqual(options, {"execution": StartExecution(skip_provider_setup=True)})
            print("a line the worktree start prints")
            if isinstance(start, BaseException):
                raise start
            return start

        output = io.StringIO()
        with (
            patch.object(
                leaf_enclosure_start,
                "declare_process_role",
                side_effect=lambda role: order.append(f"role {role}"),
            ),
            patch.object(leaf_enclosure_start, "bind_worktree_services"),
            patch.object(leaf_enclosure_start, "load_config", side_effect=load),
            patch.object(leaf_enclosure_start, "worktree_start_tool", side_effect=start_tool),
            patch.object(leaf_enclosure_start.sys, "stdin", io.StringIO(json.dumps(request))),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            status = main([leaf_enclosure_start.COMMAND, "--config", "/sandbox/settings.json"])
        return status, output.getvalue(), order

    def test_the_command_runs_the_worktree_start_and_answers_with_one_json_object(self) -> None:
        request = request_of(self.loaded)
        status, output, order = self.run_command(request, {"ok": True, "state": "started"})
        self.assertEqual((status, json.loads(output)), (0, {"ok": True}))
        # The tool server's role is declared before the settings file is loaded.
        self.assertEqual(
            order,
            ["role mcp", "settings loaded from /sandbox/settings.json", "worktree start"],
        )
        refusals: dict[str, tuple[Any, Any, dict[str, str]]] = {
            "the worktree start refuses": (
                request,
                {"ok": False, "state": "refused", "summary": "the base branch is missing"},
                {"code": "refused", "message": "the base branch is missing"},
            ),
            "the worktree start raises": (
                request,
                ConfigError("unknown repository sandbox-app"),
                {"code": "ConfigError", "message": "unknown repository sandbox-app"},
            ),
            "the request is incomplete": (
                {**request, "leafId": ""},
                {"ok": True},
                {
                    "code": "ValueError",
                    "message": "the request must be a JSON object with repoId, taskName, "
                    "worktreeName, leafId, parentTask as text.",
                },
            ),
        }
        for label, (sent, start, error) in refusals.items():
            with self.subTest(label):
                status, output, _order = self.run_command(sent, start)
                self.assertEqual((status, json.loads(output)), (1, {"ok": False, "error": error}))

    def test_the_command_starts_nothing_when_its_settings_name_other_roots(self) -> None:
        asked = request_of(self.loaded)
        elsewhere = (self.root / "another-sandbox").as_posix()
        differing: dict[str, tuple[dict[str, Any], str]] = {
            "another coordination root": (
                {**asked, "coordinationRoot": elsewhere},
                f"coordinationRoot: {asked['coordinationRoot']} instead of {elsewhere}",
            ),
            "another code root": ({**asked, "codeRoot": elsewhere}, "codeRoot: "),
            "another memory root": ({**asked, "memoryRoot": elsewhere}, "memoryRoot: "),
            "a memory root the backend does not have": (
                {**asked, "memoryRoot": None},
                "memoryRoot: ",
            ),
            "no roots in the request": (
                {key: asked[key] for key in leaf_enclosure_start._IDENTITY_FIELDS},
                "coordinationRoot: ",
            ),
        }
        for label, (sent, named) in differing.items():
            with self.subTest(label):
                status, output, order = self.run_command(sent, AssertionError("must not start"))
                reply = json.loads(output)
                self.assertEqual((status, reply["ok"]), (1, False))
                self.assertEqual(reply["error"]["code"], "leaf_enclosure_roots_differ")
                self.assertIn(named, reply["error"]["message"])
                self.assertIn("nothing was started", reply["error"]["message"])
                self.assertNotIn("worktree start", order)
        with self.subTest("the settings no longer name the repository"):
            self.loaded = backend_config(self.root, repository=False)
            status, output, order = self.run_command(asked, AssertionError("must not start"))
            self.assertEqual(json.loads(output)["error"]["code"], "leaf_enclosure_roots_differ")
            self.assertNotIn("worktree start", order)

    def test_the_command_is_in_no_help_and_no_public_sub_command(self) -> None:
        self.assertNotIn(leaf_enclosure_start.COMMAND, build_parser().format_help())
        with (
            contextlib.redirect_stderr(io.StringIO()) as refused,
            self.assertRaises(SystemExit),
        ):
            build_parser().parse_args([leaf_enclosure_start.COMMAND, "--config", "/settings"])
        self.assertIn("invalid choice", refused.getvalue())
        # It is the internal command only as the first argument; anywhere else it is an argument
        # of the public command before it.
        with (
            patch.object(leaf_enclosure_start, "main") as internal,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            main(["paseo", "status", leaf_enclosure_start.COMMAND, "--config", "/settings"])
        internal.assert_not_called()
        with patch.object(leaf_enclosure_start, "main", return_value=7) as internal:
            self.assertEqual(main([leaf_enclosure_start.COMMAND, "--config", "/settings"]), 7)
        internal.assert_called_once_with(["--config", "/settings"])


class LeafEnclosureChildProcessTests(unittest.TestCase):
    """Real child processes: the build's own command line, and stand-ins for a stuck start."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = backend_config(self.root)

    def stand_in(self, body: str) -> Any:
        """Run ``body`` as the child instead of this build's interpreter."""

        script = self.root / "child"
        script.write_text(f"#!/bin/sh\ncd {self.root}\n{body}\n", encoding="utf-8")
        script.chmod(0o755)
        return patch.object(leaf_enclosure_start.sys, "executable", script.as_posix())

    def gone(self, name: str) -> bool:
        """Whether the process whose id the stand-in wrote to ``name`` has ended."""

        pid = int((self.root / name).read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split(") ")[1][0]
            except OSError:
                return True
            if state == "Z":
                return True
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        return False

    def test_the_real_child_answers_through_the_build_s_own_command_line(self) -> None:
        started: list[tuple[list[str], dict[str, Any]]] = []
        real_popen = subprocess.Popen

        def popen(argv: list[str], **options: Any) -> Any:
            started.append((argv, options))
            return real_popen(argv, **options)

        # The backend's working directory can be a folder agents write into. A module lying there
        # under the name of one the command imports is not loaded in its place.
        folder = self.root / "a-folder-agents-write-into"
        folder.mkdir()
        (folder / "json.py").write_text("raise SystemExit('the decoy was imported')\n", "utf-8")
        with (
            patch.object(leaf_enclosure_start.subprocess, "Popen", popen),
            contextlib.chdir(folder),
        ):
            outcome = leaf_enclosure_start.start_leaf_enclosure_in_child(self.config, IDENTITY)
            unknown = leaf_enclosure_start.start_leaf_enclosure_in_child(
                backend_config(self.root, repository=False), IDENTITY
            )

        self.assertIs(outcome["ok"], False)
        self.assertEqual(outcome["state"], "ConfigError")
        self.assertIn("MCP settings file does not exist", outcome["summary"])
        self.assertIn(self.config.config_path.as_posix(), outcome["summary"])
        self.assertEqual([entry.name for entry in self.root.iterdir()], [folder.name])
        self.assertEqual([entry.name for entry in folder.iterdir()], ["json.py"])
        ((argv, options),) = started
        self.assertEqual(
            argv[1:],
            [
                "-P",
                "-m",
                "agents_remember.cli",
                "start-leaf-enclosure",
                "--config",
                self.config.config_path.as_posix(),
            ],
        )
        # A process group of its own inside the backend's session; the backend's own environment
        # and working directory; no roots on the command line.
        self.assertEqual(options["process_group"], 0)
        self.assertFalse({"env", "cwd", "start_new_session"} & set(options))
        # A backend that does not know the repository starts no process at all.
        self.assertEqual(unknown["state"], "leaf_enclosure_repository_unknown")

    def test_a_start_cut_off_at_the_limit_leaves_no_process_of_its_group_behind(self) -> None:
        # The child and what it started both ignore the polite signal; only the forceful one ends
        # them. The child records its own session and group.
        stuck = (
            "trap '' TERM\n(trap '' TERM; exec sleep 300) &\necho $! > started-by-the-child.pid\n"
            "echo $$ > child.pid\nps -o sid=,pgid= -p $$ > child.ids\n"
            "echo 'git fetch: still receiving objects' >&2\nwait"
        )
        answered: dict[str, Any] = {}
        caller = threading.Thread(
            target=lambda: answered.update(
                leaf_enclosure_start.start_leaf_enclosure_in_child(self.config, IDENTITY)
            ),
            daemon=True,
        )
        with self.stand_in(stuck), patch.object(leaf_enclosure_start, "START_TIMEOUT_SECONDS", 1):
            caller.start()
            caller.join(10)
            in_time = not caller.is_alive()
            if not in_time:
                # Only the forceful signal ends these processes; without it the caller waits on.
                for name in ("child.pid", "started-by-the-child.pid"):
                    os.kill(int((self.root / name).read_text(encoding="utf-8")), signal.SIGKILL)
                caller.join(10)

        self.assertTrue(in_time, "the cut-off did not end processes that ignore the polite signal")
        outcome = answered
        self.assertEqual(outcome["state"], "leaf_enclosure_start_timeout")
        self.assertIn(
            "was cut off after 1 seconds, together with its process group", outcome["summary"]
        )
        self.assertIn("may have left a half-made enclosure", outcome["summary"])
        self.assertIn("repaired or abandoned with AR's worktree tools", outcome["summary"])
        self.assertNotIn("later Start completes", outcome["summary"])
        self.assertIn("The child process said: git fetch: still receiving", outcome["summary"])
        for name in ("child.pid", "started-by-the-child.pid"):
            self.assertTrue(self.gone(name), f"{name}: the process survived the cut-off")
        # The child was in the caller's session and led a group of its own.
        session, group = (self.root / "child.ids").read_text(encoding="utf-8").split()
        child = (self.root / "child.pid").read_text(encoding="utf-8").strip()
        self.assertEqual(int(session), os.getsid(0))
        self.assertEqual(group, child)
        self.assertNotEqual(int(group), os.getpgid(0))

    def test_the_reply_of_a_child_that_ended_is_not_held_back_by_what_it_left_running(self) -> None:
        lingering = "sleep 30 &\necho $! > left-running.pid\nprintf '{\"ok\": true}'\nexit 0"
        with (
            self.stand_in(lingering),
            patch.object(leaf_enclosure_start, "START_TIMEOUT_SECONDS", 8),
        ):
            began = time.monotonic()
            outcome = leaf_enclosure_start.start_leaf_enclosure_in_child(self.config, IDENTITY)

        self.assertEqual(outcome, {"ok": True})
        self.assertLess(time.monotonic() - began, 4)
        os.kill(int((self.root / "left-running.pid").read_text(encoding="utf-8")), signal.SIGKILL)

    def test_a_child_that_ends_without_reading_its_request_is_answered_from_its_reply(
        self,
    ) -> None:
        # More than a pipe holds, so the request cannot be written to a child that reads nothing.
        identity = TaskIdentity(
            repo_id="sandbox-app",
            task_name="sbx-text-helpers",
            worktree_name="w" * 300_000,
            leaf_id="SBX-M1-L1",
            parent_task="sbx-sprint",
        )
        early = (
            'printf \'{"ok": false, "error": {"code": "ended-early", "message": "read nothing"}}\'\n'
            "echo 'the settings file is gone' >&2\nexit 1"
        )
        with self.stand_in(early):
            outcome = leaf_enclosure_start.start_leaf_enclosure_in_child(self.config, identity)

        self.assertEqual(outcome["state"], "ended-early")
        self.assertIn("(ended-early): read nothing", outcome["summary"])
        self.assertIn("The child process said: the settings file is gone", outcome["summary"])

    def test_a_terminal_read_below_the_child_fails_at_once_instead_of_stopping_it(self) -> None:
        # The backend was started from a terminal: the child is a background process group of
        # that terminal's session, and something below it asks the terminal for a credential.
        child = (
            "import subprocess, sys\n"
            "from unittest.mock import patch\n"
            "from agents_remember.cli import leaf_enclosure_start as command\n"
            "def load(path):\n"
            "    asked = subprocess.run(['sh', '-c', 'read line < /dev/tty'], check=False)\n"
            "    raise RuntimeError(f'the terminal read ended with status {asked.returncode}')\n"
            "with (\n"
            "    patch.object(command, 'declare_process_role'),\n"
            "    patch.object(command, 'bind_worktree_services'),\n"
            "    patch.object(command, 'load_config', load),\n"
            "):\n"
            "    raise SystemExit(command.main(['--config', '/sandbox/settings.json']))\n"
        )
        backend = (
            "import fcntl, json, os, subprocess, sys, termios, time\n"
            "from agents_remember.cli import leaf_enclosure_start as command\n"
            "_leader, terminal = os.openpty()\n"
            "fcntl.ioctl(terminal, termios.TIOCSCTTY, 0)\n"
            "command.START_TIMEOUT_SECONDS = 120\n"
            "began = time.monotonic()\n"
            "try:\n"
            "    done = command._run_child([sys.executable, '-c', sys.argv[1]], json.loads(sys.argv[2]))\n"
            "    answer = {'status': done.returncode, 'reply': json.loads(done.stdout)}\n"
            "except subprocess.TimeoutExpired:\n"
            "    answer = {'cutOff': True}\n"
            "print(json.dumps({**answer, 'seconds': time.monotonic() - began}))\n"
        )
        request = json.dumps(request_of(self.config))
        # The child's limit lies far above the time a fresh interpreter needs to start on a
        # loaded machine (21 seconds were measured at a load average of 45 with eight copies of
        # this module at once): a child that is stopped by its terminal read runs into the
        # limit, one whose read fails answers long before it.
        # A session of its own, so that the pseudo-terminal becomes its controlling terminal.
        completed = subprocess.run(
            [sys.executable, "-c", backend, child, request],
            capture_output=True,
            text=True,
            check=False,
            start_new_session=True,
            timeout=300,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        answer = json.loads(completed.stdout)
        self.assertNotIn("cutOff", answer, "the child was stopped by its terminal read")
        self.assertEqual(answer["status"], 1)
        self.assertEqual(answer["reply"]["error"]["code"], "RuntimeError")
        self.assertRegex(
            answer["reply"]["error"]["message"], r"^the terminal read ended with status [1-9]"
        )
        self.assertLess(answer["seconds"], 60)

    def test_output_that_is_not_utf_8_is_a_refusal_with_the_child_s_last_words(self) -> None:
        garbled = "printf '\\377\\376{'\nprintf 'caf\\351: out of memory' >&2\nexit 1"
        with self.stand_in(garbled):
            outcome = leaf_enclosure_start.start_leaf_enclosure_in_child(self.config, IDENTITY)

        self.assertEqual(outcome["state"], "leaf_enclosure_start_unreadable")
        self.assertIn("status 1 and no readable reply", outcome["summary"])
        self.assertIn("The child process said: caf", outcome["summary"])
        self.assertIn(": out of memory", outcome["summary"])

    def test_a_refusal_keeps_to_800_characters_and_ends_with_the_child_s_last_words(self) -> None:
        long_message = json.dumps(
            {"ok": False, "error": {"code": "refused", "message": "m" * 3000}}
        )
        long_winded = f"printf '%s' '{long_message}'\nprintf '%2000s' 'the last words' >&2\nexit 1"
        with self.stand_in(long_winded):
            outcome = leaf_enclosure_start.start_leaf_enclosure_in_child(self.config, IDENTITY)

        self.assertEqual(outcome["state"], "refused")
        self.assertEqual(len(outcome["summary"]), 800)
        self.assertTrue(outcome["summary"].endswith("the last words"))
        self.assertIn("(refused): mmmm", outcome["summary"])


if __name__ == "__main__":
    unittest.main()
