"""Tests for the CGC watcher entrypoint guard Docker asset.

The guard runs inside the runner image where `redis` is installed as a cgc
dependency; on the host a stub module is injected before import so the asset
can be loaded and its self-heal logic tested directly.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

GUARD_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agents_remember"
    / "package_data"
    / "runtime"
    / "providers"
    / "docker"
    / "codegraphcontext"
    / "watch_guard.py"
)


class FakeRedisError(Exception):
    pass


class FakeBusyLoadingError(FakeRedisError):
    pass


class FakeResponseError(FakeRedisError):
    pass


def _fake_redis_module() -> mock.MagicMock:
    module = mock.MagicMock(name="redis")
    module.exceptions.RedisError = FakeRedisError
    module.exceptions.BusyLoadingError = FakeBusyLoadingError
    module.exceptions.ResponseError = FakeResponseError
    return module


def load_guard() -> types.ModuleType:
    fake_redis = _fake_redis_module()
    with mock.patch.dict(sys.modules, {"redis": fake_redis, "redis.exceptions": fake_redis.exceptions}):
        spec = importlib.util.spec_from_file_location("cgc_watch_guard", GUARD_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class WatchGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.guard = load_guard()

    def test_wait_for_ready_returns_client_on_pong(self) -> None:
        client = mock.Mock()
        client.ping.return_value = True
        with mock.patch.object(self.guard, "_connection", return_value=client):
            self.assertIs(self.guard.wait_for_ready(5), client)

    def test_wait_for_ready_waits_through_loading_then_succeeds(self) -> None:
        loading = mock.Mock()
        loading.ping.side_effect = FakeBusyLoadingError("loading the dataset")
        ready = mock.Mock()
        ready.ping.return_value = True
        with (
            mock.patch.object(self.guard, "_connection", side_effect=[loading, ready]),
            mock.patch.object(self.guard.time, "sleep"),
        ):
            self.assertIs(self.guard.wait_for_ready(60), ready)

    def test_wait_for_ready_gives_up_after_deadline(self) -> None:
        with mock.patch.object(self.guard, "_connection") as connection:
            connection.return_value.ping.side_effect = FakeRedisError("nope")
            self.assertIsNone(self.guard.wait_for_ready(0))

    def test_indexed_file_count_parses_count_reply(self) -> None:
        client = mock.Mock()
        client.execute_command.return_value = [[b"count(f)"], [[10472]], [b"stats"]]
        self.assertEqual(self.guard.indexed_file_count(client, "cgc_repo"), 10472)
        client.execute_command.assert_called_once_with(
            "GRAPH.RO_QUERY", "cgc_repo", "MATCH (f:File) RETURN count(f)"
        )

    def test_indexed_file_count_returns_none_for_missing_graph(self) -> None:
        client = mock.Mock()
        client.execute_command.side_effect = FakeResponseError(
            "Invalid graph operation on empty key"
        )
        self.assertIsNone(self.guard.indexed_file_count(client, "cgc_repo"))

    def test_indexed_file_count_reraises_other_response_errors(self) -> None:
        client = mock.Mock()
        client.execute_command.side_effect = FakeResponseError("unknown command")
        with self.assertRaises(FakeResponseError):
            self.guard.indexed_file_count(client, "cgc_repo")

    def test_indexed_file_count_returns_none_for_unparseable_reply(self) -> None:
        client = mock.Mock()
        client.execute_command.return_value = []
        self.assertIsNone(self.guard.indexed_file_count(client, "cgc_repo"))

    def test_clear_poisoned_graph_deletes_empty_graph(self) -> None:
        client = mock.Mock()
        client.execute_command.return_value = [[b"count(f)"], [[0]], [b"stats"]]
        self.guard.clear_poisoned_graph(client, "cgc_repo", 1)
        client.execute_command.assert_called_with("GRAPH.DELETE", "cgc_repo")

    def test_clear_poisoned_graph_keeps_indexed_graph(self) -> None:
        client = mock.Mock()
        client.execute_command.return_value = [[b"count(f)"], [[42]], [b"stats"]]
        self.guard.clear_poisoned_graph(client, "cgc_repo", 1)
        delete_calls = [
            call for call in client.execute_command.call_args_list if call.args[0] == "GRAPH.DELETE"
        ]
        self.assertEqual(delete_calls, [])

    def test_clear_poisoned_graph_skips_absent_graph(self) -> None:
        client = mock.Mock()
        client.execute_command.side_effect = FakeResponseError(
            "Invalid graph operation on empty key"
        )
        self.guard.clear_poisoned_graph(client, "cgc_repo", 1)
        delete_calls = [
            call for call in client.execute_command.call_args_list if call.args[0] == "GRAPH.DELETE"
        ]
        self.assertEqual(delete_calls, [])

    def test_main_checks_graph_then_execs_cgc(self) -> None:
        client = mock.Mock()
        client.execute_command.return_value = [[b"count(f)"], [[7]], [b"stats"]]
        env = {"FALKORDB_GRAPH_NAME": "cgc_repo"}
        with (
            mock.patch.object(self.guard.os, "environ", env),
            mock.patch.object(self.guard, "wait_for_ready", return_value=client),
            mock.patch.object(self.guard.os, "execvp") as execvp,
            mock.patch.object(self.guard.sys, "argv", ["cgc-watch-guard.py", "watch", "/repo"]),
        ):
            self.guard.main()
        execvp.assert_called_once_with("cgc", ["cgc", "watch", "/repo"])

    def test_main_execs_cgc_even_when_backend_never_ready(self) -> None:
        env = {"FALKORDB_GRAPH_NAME": "cgc_repo", "CGC_GUARD_WAIT_SECONDS": "0"}
        with (
            mock.patch.object(self.guard.os, "environ", env),
            mock.patch.object(self.guard, "wait_for_ready", return_value=None),
            mock.patch.object(self.guard.os, "execvp") as execvp,
            mock.patch.object(self.guard.sys, "argv", ["cgc-watch-guard.py", "watch", "/repo"]),
        ):
            self.guard.main()
        execvp.assert_called_once_with("cgc", ["cgc", "watch", "/repo"])

    def test_main_skips_graph_check_without_graph_name(self) -> None:
        with (
            mock.patch.object(self.guard.os, "environ", {}),
            mock.patch.object(self.guard, "wait_for_ready") as wait,
            mock.patch.object(self.guard.os, "execvp") as execvp,
            mock.patch.object(self.guard.sys, "argv", ["cgc-watch-guard.py", "watch", "/repo"]),
        ):
            self.guard.main()
        wait.assert_not_called()
        execvp.assert_called_once_with("cgc", ["cgc", "watch", "/repo"])


if __name__ == "__main__":
    unittest.main()
