"""The tool-call log cases: records, raised calls, terminating routes and settings.

Split text-identical out of test_tool_answer_table.py under MIK-R196 rule 10."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agents_remember.application.tool_answer_log import (
    ENCLOSURE_LOGS_DIRNAME,
    TOOL_LOG_BASENAME_PREFIX,
    ToolCaller,
    ToolCallRecord,
    append_jsonl_line,
    ensure_enclosure_logs_dir,
    prune_old_tool_logs,
    record_tool_call,
    reset_log_budgets,
    resolve_log_roots,
    shorten_long_arguments,
    tool_call_context,
)
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.cli import tool_log_stats
from agents_remember.kernel.primitives.runtime_config import (
    TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT,
    TOOL_LOG_KEEP_DAYS_DEFAULT,
    TOOL_LOG_MAX_BYTES_DEFAULT,
    ConfigError,
    parse_tool_log_settings,
)
from agents_remember.models.tools.tool_answer import strip_envelope
from tool_answer_table_support import _server_info_case, _stub_config
from tool_entry_point_world import EntryPointWorld


class ToolAnswerLogTests(unittest.TestCase):
    def test_strip_envelope_removes_exactly_the_four(self) -> None:
        stripped = strip_envelope(
            {
                "ok": True,
                "tokens": 5,
                "tokenizer": "tiktoken:o200k_base",
                "tokenCountExact": True,
                "operation": "ping",
                "code": "refused",
                "error": "one sentence.",
                "state": "s",
            }
        )
        self.assertEqual(
            stripped,
            {"ok": True, "code": "refused", "error": "one sentence.", "state": "s"},
        )

    def _record(
        self,
        coord: Path,
        *,
        ok: bool = True,
        code: str | None = None,
        arguments: dict[str, Any] | None = None,
    ):
        reset_log_budgets()
        dest = resolve_log_roots(_stub_config(coord))
        self.assertIsNone(dest.enclosure_dir)
        self.assertIsNotNone(dest.coord_log_dir)
        assert dest.coord_log_dir is not None
        return record_tool_call(
            ToolCallRecord(
                tool_name="ping",
                operation="ping",
                arguments=arguments or {},
                caller=ToolCaller(agent_id="a1", role="worker", task="t"),
                ok=ok,
                code=code,
                duration_ms=1.5,
                answer_tokens=42,
                whole_result={"ok": ok, "detail": [1, 2]},
                result_bytes=18,
                guard_cut=False,
                needs_detail=True,
                enclosure_dir=dest.enclosure_dir,
                coord_log_dir=dest.coord_log_dir,
            )
        )

    def _read_lines(self, coord: Path) -> list[dict[str, Any]]:
        log_dir = coord / "logs" / "mcp"
        files = sorted(log_dir.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
        self.assertEqual(len(files), 1)
        return [
            json.loads(line)
            for line in files[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_success_line_holds_the_rule_3_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            outcome = self._record(coord, arguments={"repo_id": "agents-remember"})
            self.assertIsNone(outcome.log_error)
            self.assertIsNotNone(outcome.log)
            (line,) = self._read_lines(coord)
            self.assertEqual(line["tool"], "ping")
            self.assertEqual(line["operation"], "ping")
            self.assertEqual(line["caller"]["agentId"], "a1")
            self.assertEqual(line["caller"]["role"], "worker")
            self.assertTrue(line["ok"])
            self.assertEqual(line["answerTokens"], 42)
            self.assertEqual(line["resultBytes"], 18)
            self.assertFalse(line["guardCut"])
            self.assertEqual(line["arguments"], {"repo_id": "agents-remember"})
            self.assertEqual(line["dropped"], 0)
            self.assertTrue(line["detail"].endswith(".json"))
            self.assertIsNotNone(outcome.log)
            assert outcome.log is not None
            stored = json.loads(Path(outcome.log).read_text(encoding="utf-8"))
            self.assertEqual(stored["detail"], [1, 2])

    def test_refused_and_raised_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            self._record(coord, ok=False, code="start-blocked")
            lines = self._read_lines(coord)
            self.assertFalse(lines[-1]["ok"])
            self.assertEqual(lines[-1]["code"], "start-blocked")
            outcome = record_tool_call(
                ToolCallRecord(
                    tool_name="task_doc",
                    operation="task_doc",
                    arguments={},
                    caller=ToolCaller(),
                    ok=False,
                    code="raised",
                    duration_ms=0.5,
                    answer_tokens=0,
                    whole_result={"ok": False, "code": "raised", "error": "boom."},
                    result_bytes=0,
                    guard_cut=False,
                    needs_detail=True,
                    enclosure_dir=None,
                    coord_log_dir=coord / "logs" / "mcp",
                )
            )
            self.assertIsNone(outcome.log_error)
            lines = self._read_lines(coord)
            self.assertEqual(lines[-1]["code"], "raised")

    def test_long_argument_replaced_by_length_and_digest(self) -> None:
        shortened = shorten_long_arguments({"small": "x", "big": "y" * 500})
        self.assertEqual(shortened["small"], "x")
        self.assertEqual(len(shortened["big"]), 2)
        self.assertEqual(shortened["big"]["length"], 500)
        self.assertEqual(
            shortened["big"]["sha256"], hashlib.sha256(("y" * 500).encode()).hexdigest()
        )

    def test_no_credential_and_no_env_value_in_a_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_SECRET_VALUE"] = "s3cr3t-env-value-xyz"
            try:
                coord = Path(tmp)
                self._record(
                    coord,
                    arguments={
                        "apiKey": "sk-live-123",
                        "password": "hunter2",
                        "note": "s3cr3t-env-value-xyz",
                        "plain": "fine",
                    },
                )
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_SECRET_VALUE"]
        self.assertEqual(line["arguments"]["apiKey"], "<omitted>")
        self.assertEqual(line["arguments"]["password"], "<omitted>")
        self.assertEqual(line["arguments"]["note"], "<env>")
        self.assertEqual(line["arguments"]["plain"], "fine")

    def test_embedded_env_secret_and_credential_spellings_are_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_SECRET_VALUE"] = "s3cr3t-env-value-xyz"
            try:
                coord = Path(tmp)
                self._record(
                    coord,
                    arguments={
                        "query": "find s3cr3t-env-value-xyz in the docs",
                        "nested": {"deep": ["pair s3cr3t-env-value-xyz here"]},
                        "token": "tok-abc-123",
                        "Authorization": "Bearer xyz",
                        "auth": "shh",
                        "tokens": 5,
                        "tokenizer": "o200k_base",
                        "plain": "fine",
                    },
                )
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_SECRET_VALUE"]
        self.assertEqual(line["arguments"]["query"], "find <env> in the docs")
        self.assertEqual(line["arguments"]["nested"], {"deep": ["pair <env> here"]})
        self.assertEqual(line["arguments"]["token"], "<omitted>")
        self.assertEqual(line["arguments"]["Authorization"], "<omitted>")
        self.assertEqual(line["arguments"]["auth"], "<omitted>")
        self.assertEqual(line["arguments"]["tokens"], 5)
        self.assertEqual(line["arguments"]["tokenizer"], "o200k_base")
        self.assertEqual(line["arguments"]["plain"], "fine")
        text = json.dumps(line)
        self.assertNotIn("s3cr3t-env-value-xyz", text)
        self.assertNotIn("tok-abc-123", text)
        self.assertNotIn("Bearer xyz", text)

    def test_short_credential_variable_value_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_SHORT_CREDENTIAL"] = "S4719x"
            try:
                coord = Path(tmp)
                self._record(
                    coord,
                    arguments={
                        "query": "find S4719x here",
                        "nested": {"text": "nested S4719x value"},
                    },
                )
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_SHORT_CREDENTIAL"]
        self.assertEqual(line["arguments"]["query"], "find <env> here")
        self.assertEqual(line["arguments"]["nested"], {"text": "nested <env> value"})
        self.assertNotIn("S4719x", json.dumps(line))

    def test_ordinary_env_value_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_NOTE"] = "ordinary-note-value"
            try:
                coord = Path(tmp)
                self._record(coord, arguments={"note": "holds ordinary-note-value inside"})
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_NOTE"]
        self.assertEqual(line["arguments"]["note"], "holds ordinary-note-value inside")

    def test_home_folder_path_stays_whole(self) -> None:
        """Ordinary home-folder text is preserved whole inside its length domain (rule 3)."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_DATADIR"] = tmp
            try:
                coord = Path(tmp)
                short_path = "group/series-contract.md"
                self._record(coord, arguments={"contract_path": short_path, "repo_id": "r"})
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_DATADIR"]
        self.assertEqual(line["arguments"]["contract_path"], short_path)

    def test_home_folder_path_follows_the_length_rule(self) -> None:
        """The real tmp-rooted path is whole at or under 200 chars, else length+digest (rule 3)."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_DATADIR"] = tmp
            try:
                coord = Path(tmp)
                contract_path = (coord / "group" / "series-contract.md").as_posix()
                self._record(coord, arguments={"contract_path": contract_path, "repo_id": "r"})
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_DATADIR"]
        if len(contract_path) > 200:
            self.assertEqual(
                line["arguments"]["contract_path"],
                {
                    "length": len(contract_path),
                    "sha256": hashlib.sha256(contract_path.encode("utf-8")).hexdigest(),
                },
            )
        else:
            self.assertEqual(line["arguments"]["contract_path"], contract_path)

    def test_overlong_path_shortens_to_length_and_digest(self) -> None:
        """A value over 200 chars is replaced by its length and digest (rule 3)."""
        long_path = "home/" + "d" * 250 + "/series-contract.md"
        assert len(long_path) > 200
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            self._record(coord, arguments={"contract_path": long_path, "repo_id": "r"})
            (line,) = self._read_lines(coord)
        self.assertEqual(
            line["arguments"]["contract_path"],
            {
                "length": len(long_path),
                "sha256": hashlib.sha256(long_path.encode("utf-8")).hexdigest(),
            },
        )

    def test_slash_bearing_env_credential_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_SECRET_SLASH"] = "reviewSynthetic/SlashCredential4719"
            try:
                coord = Path(tmp)
                self._record(
                    coord,
                    arguments={
                        "query": "prefix reviewSynthetic/SlashCredential4719 suffix",
                        "nested": {"text": "prefix reviewSynthetic/SlashCredential4719"},
                    },
                )
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_SECRET_SLASH"]
        self.assertEqual(line["arguments"]["query"], "prefix <env> suffix")
        self.assertEqual(line["arguments"]["nested"], {"text": "prefix <env>"})
        self.assertNotIn("reviewSynthetic/SlashCredential4719", json.dumps(line))

    def test_path_looking_credential_values_are_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_SECRET_SLASH"] = "reviewSynthetic/SlashCredential4719"
            try:
                coord = Path(tmp)
                self._record(
                    coord,
                    arguments={
                        "query": "reviewSynthetic/SlashCredential4719/report",
                        "nested": {"text": "/tmp/reviewSynthetic/SlashCredential4719/report"},
                        "safe": "report without the value",
                    },
                )
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_SECRET_SLASH"]
        self.assertEqual(line["arguments"]["query"], "<env>/report")
        self.assertEqual(line["arguments"]["nested"], {"text": "/tmp/<env>/report"})
        self.assertEqual(line["arguments"]["safe"], "report without the value")
        self.assertNotIn("reviewSynthetic/SlashCredential4719", json.dumps(line))
        # A diagnostic holding no protected value passes through untouched.
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            self._record(coord, arguments={"repo_id": "r", "note": "plain diagnostic"})
            (plain,) = self._read_lines(coord)
        self.assertEqual(plain["arguments"]["note"], "plain diagnostic")

    def test_prune_touches_only_old_tool_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            old_log = folder / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01.jsonl"
            old_log.write_text("{}\n", encoding="utf-8")
            old_detail = folder / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01"
            old_detail.mkdir()
            fresh_log = folder / f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl"
            fresh_log.write_text("{}\n", encoding="utf-8")
            other = folder / "other.jsonl"
            other.write_text("{}\n", encoding="utf-8")
            old = 946684800.0
            os.utime(old_log, (old, old))
            os.utime(old_detail, (old, old))
            removed = prune_old_tool_logs(folder, 14, today="2026-10-08")
            self.assertEqual(removed, 1)
            self.assertFalse(old_log.exists())
            self.assertFalse(old_detail.exists())
            self.assertTrue(fresh_log.exists())
            self.assertTrue(other.exists())

    def test_day_change_and_keep_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            log_dir = coord / "logs" / "mcp"
            log_dir.mkdir(parents=True)
            stale_log = log_dir / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01.jsonl"
            stale_log.write_text("{}\n", encoding="utf-8")
            stale_detail = log_dir / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01"
            stale_detail.mkdir()
            (stale_detail / "x.json").write_text("{}", encoding="utf-8")
            old = 946684800.0  # 2000-01-01
            os.utime(stale_log, (old, old))
            os.utime(stale_detail / "x.json", (old, old))
            os.utime(stale_detail, (old, old))
            keep = log_dir / "keep.txt"
            keep.write_text("x", encoding="utf-8")
            self._record(coord)
            self.assertFalse(stale_log.exists())
            self.assertFalse(stale_detail.exists())
            self.assertTrue(keep.exists())

    def test_enclosure_keeps_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            enclosure = Path(tmp) / "enclosure" / ENCLOSURE_LOGS_DIRNAME
            made = ensure_enclosure_logs_dir(Path(tmp) / "enclosure")
            self.assertIsNotNone(made)
            assert made is not None
            self.assertTrue(made.is_dir())
            stale = enclosure / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01.jsonl"
            stale.write_text("{}\n", encoding="utf-8")
            outcome = record_tool_call(
                ToolCallRecord(
                    tool_name="ping",
                    operation="ping",
                    arguments={},
                    caller=ToolCaller(),
                    ok=True,
                    code=None,
                    duration_ms=0.1,
                    answer_tokens=1,
                    whole_result={"ok": True},
                    result_bytes=2,
                    guard_cut=False,
                    needs_detail=True,
                    enclosure_dir=enclosure,
                    coord_log_dir=None,
                    keep_days=1,
                )
            )
            self.assertIsNone(outcome.log_error)
            self.assertTrue(stale.exists())

    def test_failing_log_write_does_not_fail_the_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("file, not a dir", encoding="utf-8")
            outcome = record_tool_call(
                ToolCallRecord(
                    tool_name="ping",
                    operation="ping",
                    arguments={},
                    caller=ToolCaller(),
                    ok=True,
                    code=None,
                    duration_ms=0.1,
                    answer_tokens=1,
                    whole_result={"ok": True},
                    result_bytes=2,
                    guard_cut=False,
                    needs_detail=True,
                    enclosure_dir=None,
                    coord_log_dir=blocker / "logs",
                )
            )
            self.assertIsNotNone(outcome.log_error)
            self.assertIn("not recorded", outcome.log_error or "")
            answer = complete_tool_response("server_info", _server_info_case())
            self.assertTrue(answer["ok"])

    def test_no_coordination_root_writes_no_log_and_does_not_fail(self) -> None:
        dest = resolve_log_roots(None)
        self.assertIsNone(dest.enclosure_dir)
        self.assertIsNone(dest.coord_log_dir)

    def test_custom_absolute_transcript_root_holds_line_detail_and_location(
        self,
    ) -> None:
        # Rule 3, E3: a transcriptRoot set to another absolute folder holds
        # the line, the detail file and the location in the answer.
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            custom = coord / "elsewhere" / "tool-logs"
            config = _stub_config(coord, transcript_root=custom)
            dest = resolve_log_roots(config)
            self.assertEqual(dest.coord_log_dir, custom)
            with tool_call_context("server_info", {}, config=config):
                answer = complete_tool_response(
                    "server_info",
                    _server_info_case(),
                )
            self.assertTrue(answer["log"].startswith(custom.as_posix()))
            detail = Path(answer["log"])
            self.assertTrue(detail.is_file())
            self.assertEqual(detail.parent.parent, custom)
            lines = [
                json.loads(line)
                for file in sorted(custom.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["detail"], detail.name)
            self.assertFalse((coord / "logs").exists())

    def test_append_is_one_whole_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.jsonl"
            self.assertIsNone(append_jsonl_line(path, {"a": 1}))
            self.assertIsNone(append_jsonl_line(path, {"b": 2}))
            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines(),
                ["", '{"a": 1}', '{"b": 2}'],
            )


class ToolRaisedCallLoggingTests(unittest.TestCase):
    """Raised calls log through the production entry and still raise (rule 3)."""

    def _today_lines(self, world: EntryPointWorld) -> list[dict[str, Any]]:
        files = sorted((world.coord / "logs" / "mcp").glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
        self.assertEqual(len(files), 1)
        return [
            json.loads(line)
            for line in files[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_unknown_tool_refusal_is_logged(self) -> None:
        world = EntryPointWorld()
        try:
            status, _ = world.call("no_such_tool_xyz", {})
            self.assertTrue(status.startswith("RAISED"), status)
            (line,) = self._today_lines(world)
            self.assertEqual(line["tool"], "no_such_tool_xyz")
            self.assertFalse(line["ok"])
            self.assertEqual(line["code"], "raised")
            self.assertTrue(line["detail"].endswith(".json"))
        finally:
            world.close()

    def test_strict_argument_refusal_is_logged(self) -> None:
        world = EntryPointWorld()
        try:
            status, _ = world.call("task_doc", {"repo_id": 123, "operation": "get"})
            self.assertTrue(status.startswith("RAISED"), status)
            (line,) = self._today_lines(world)
            self.assertEqual(line["tool"], "task_doc")
            self.assertFalse(line["ok"])
            self.assertEqual(line["code"], "raised")
            self.assertTrue(line["detail"].endswith(".json"))
        finally:
            world.close()


class ToolTerminatingRouteTests(unittest.TestCase):
    """Cleanup, abandon and finalize never log into an enclosure (rule 3)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        cls.world.build()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.close()

    def _resolve(self, tool: str) -> Any:
        with mock.patch(
            "agents_remember.application.tool_answer_log._bound_enclosure_contract",
            return_value=self.world.contract,
        ):
            return resolve_log_roots(_stub_config(self.world.coord), tool)

    def test_terminating_operations_log_to_the_root(self) -> None:
        for tool in ("worktree_cleanup", "worktree_abandon", "lifecycle_finalize_task"):
            dest = self._resolve(tool)
            self.assertIsNone(dest.enclosure_dir, tool)
            self.assertIsNotNone(dest.coord_log_dir, tool)

    def test_ordinary_tool_uses_the_live_enclosure(self) -> None:
        dest = self._resolve("ping")
        self.assertIsNotNone(dest.enclosure_dir)
        self.assertIsNotNone(dest.coord_log_dir)
        self.assertTrue(Path(str(dest.enclosure_dir)).is_dir())

    def test_removed_group_falls_back_to_the_root(self) -> None:
        with mock.patch(
            "agents_remember.application.tool_answer_log._bound_enclosure_contract",
            return_value=self.world.coord / "no-such-contract.md",
        ):
            dest = resolve_log_roots(_stub_config(self.world.coord), "ping")
        self.assertIsNone(dest.enclosure_dir)
        self.assertIsNotNone(dest.coord_log_dir)


class ToolLogSettingsTests(unittest.TestCase):
    def test_defaults_and_parsing(self) -> None:
        self.assertEqual(parse_tool_log_settings(None).keep_days, TOOL_LOG_KEEP_DAYS_DEFAULT)
        self.assertEqual(TOOL_LOG_KEEP_DAYS_DEFAULT, 14)
        self.assertEqual(parse_tool_log_settings({}).keep_days, 14)
        self.assertEqual(parse_tool_log_settings({"keepDays": 30}).keep_days, 30)
        self.assertEqual(parse_tool_log_settings({}).max_bytes, TOOL_LOG_MAX_BYTES_DEFAULT)
        self.assertEqual(TOOL_LOG_MAX_BYTES_DEFAULT, 268435456)
        self.assertEqual(
            parse_tool_log_settings({}).enclosure_max_bytes,
            TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT,
        )
        self.assertEqual(TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT, 67108864)
        parsed = parse_tool_log_settings(
            {"keepDays": 30, "maxBytes": 1024, "enclosureMaxBytes": 2048}
        )
        self.assertEqual(
            (parsed.keep_days, parsed.max_bytes, parsed.enclosure_max_bytes),
            (30, 1024, 2048),
        )

    def test_bad_values_refuse(self) -> None:
        for raw in (
            "x",
            [],
            {"keepDays": 0},
            {"keepDays": -3},
            {"keepDays": True},
            {"keepDays": "14"},
            {"maxBytes": 0},
            {"maxBytes": -1},
            {"maxBytes": True},
            {"maxBytes": "1024"},
            {"enclosureMaxBytes": 0},
            {"enclosureMaxBytes": False},
            {"enclosureMaxBytes": "2048"},
            {"bogus": 1},
        ):
            with self.assertRaises(ConfigError, msg=repr(raw)):
                parse_tool_log_settings(raw)


class ToolLogStatsCommandTests(unittest.TestCase):
    def _write_log(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        lines = [
            {
                "tool": "ping",
                "operation": "ping",
                "answerTokens": 10,
                "guardCut": False,
            },
            {
                "tool": "ping",
                "operation": "ping",
                "answerTokens": 30,
                "guardCut": False,
            },
            {"tool": "ping", "operation": "ping", "answerTokens": 20, "guardCut": True},
            {
                "tool": "task_doc",
                "operation": "get",
                "answerTokens": 100,
                "guardCut": False,
            },
        ]
        (folder / f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl").write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
        )

    def test_collect_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._write_log(folder)
            rows = tool_log_stats.collect_rows(folder)
            by_tool = {(row["tool"], row["operation"]): row for row in rows}
            ping = by_tool[("ping", "ping")]
            self.assertEqual(ping["calls"], 3)
            self.assertEqual(ping["median"], 20)
            self.assertEqual(ping["largest"], 30)
            self.assertEqual(ping["sum"], 60)
            self.assertEqual(ping["guardCuts"], 1)
            self.assertEqual(by_tool[("task_doc", "get")]["calls"], 1)

    def test_day_span_and_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._write_log(folder)
            self.assertEqual(
                tool_log_stats.run(
                    argparse.Namespace(log_folder=folder, since=None, until=None, json=True)
                ),
                0,
            )
            self.assertEqual(
                tool_log_stats.run(
                    argparse.Namespace(log_folder=folder, since="2026-10-09", until=None, json=True)
                ),
                0,
            )
            self.assertEqual(
                tool_log_stats.run(
                    argparse.Namespace(
                        log_folder=Path(tmp) / "missing",
                        since=None,
                        until=None,
                        json=False,
                    )
                ),
                2,
            )
            self.assertEqual(
                tool_log_stats.run(
                    argparse.Namespace(log_folder=folder, since="not-a-day", until=None, json=False)
                ),
                2,
            )
