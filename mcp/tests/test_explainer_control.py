"""Focused tests for ``explainer_control`` and the master-to-architect record.

MIK-R186 rule 6 gives the architect Update and Retire over a leaf's explainer; MIK-R189 rule 7 adds
``claim``, the record's second writer, and the Manager start's writer. Every case here runs on a
temporary coordination root with a fake host and no network: the seat service is composed for real
(the composition root's own function) and the two calls it makes are recorded, so what is under
test is the caller check, the record, and the report move.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
if str(MCP_SRC) not in sys.path:
    sys.path.append(str(MCP_SRC))

from agents_remember.application.master_architect_record import (
    CLAIM,
    MANAGER_START,
    master_architect_path,
    read_record,
    read_record_architect,
    write_record,
)
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.canvas.explainer_paths import leaf_workspace
from agents_remember.canvas.explainer_state import ExplainerBinding, PlannedIdentity
from agents_remember.cli.paseo_bridge import SEND_OUTCOME_UNKNOWN, PaseoBridgeFailure
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.role_held_messages import MAX_HELD_PER_RECIPIENT
from agents_remember.cli.role_launch_receipts import (
    EXECUTIONS_DIRECTORY,
    RECEIPT_SCHEMA,
    _receipt_path,
)
from agents_remember.cli.role_launch_routes import remember_master_architect
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp.tools import explainer_control as control
from agents_remember.mcp.tools.explainer_control import (
    NOT_AN_ARCHITECT,
    NOT_THIS_MASTERS_ARCHITECT,
    UNKNOWN_ACTION,
    ExplainerControlCall,
    explainer_control_payload,
)
from agents_remember.models.knowledge.review_frame import FrameBinding, planned_reference
from agents_remember.models.role_launcher import RoleDispatchRequest, RoleSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.planned_comparison import (
    PlannedComparisonTips,
    contract_branch_lines,
    planned_comparison,
    resolve_line_commit,
)
from test_task_intent_review import (
    LEAF_ID as fixture_leaf,
)
from test_task_intent_review import (
    MASTER_NAME as fixture_master,
)
from test_task_intent_review import (
    REPO as fixture_repo,
)
from test_task_intent_review import (
    _fixture as intent_fixture,
)

REPO = "agents-remember"
MASTER = "260928-maintained-invariant-knowledge"
LEAF = "260928-MIK-L183"
ARCHITECT = str(uuid.uuid4())
OTHER_ARCHITECT = str(uuid.uuid4())
ORCHESTRATOR = str(uuid.uuid4())
EXPLAINER = str(uuid.uuid4())
HELD_STORE_SCHEMA = "ar-held-messages/v1"


class _FakeHost:
    """The seat service's host, recording what it is asked and reaching nothing."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.sent: list[tuple[str, str]] = []
        self.archived: list[str] = []
        self.asked: list[str] = []

    def create_seat(self, **details: Any) -> None:
        self.created.append(details)

    def send(self, agent_id: str, text: str) -> None:
        self.sent.append((agent_id, text))

    def archive(self, agent_id: str) -> None:
        self.archived.append(agent_id)

    def seat_state(self, agent_id: str) -> str:
        self.asked.append(agent_id)
        return "live"

    def cancel(self, agent_id: str, *, request_id: str) -> dict[str, Any] | None:
        del agent_id, request_id
        return {
            "status": "cancelled",
            "turnStopped": True,
            "permissionClosed": True,
            "requestUncertain": False,
        }

    def in_turn(self, agent_id: str) -> bool:
        self.asked.append(agent_id)
        return False


class _World(unittest.TestCase):
    """A temporary coordination root, a fake host, and the two bindings these cases use."""

    def setUp(self) -> None:
        self._scratch = tempfile.TemporaryDirectory(prefix="ar-explainer-control-")
        self.root = Path(self._scratch.name)
        coordination = self.root / "coord"
        coordination.mkdir(parents=True)
        repository = self.root / "repo"
        repository.mkdir()
        memory = self.root / "memory"
        memory.mkdir()
        self.config = McpRuntimeConfig(
            config_path=coordination / "settings.json",
            coordination_root=coordination,
            workspace_root=self.root / "workspace",
            transcript_root=coordination / "logs" / "mcp",
            repositories={REPO: RepositoryScope(repo_id=REPO, path=repository, memory_root=memory)},
        )
        self.host = _FakeHost()

    def tearDown(self) -> None:
        self._scratch.cleanup()

    # -- the caller identities an MCP tool sees ------------------------------------------

    def environment(
        self,
        role: str,
        agent_id: str,
        *,
        sprint: TaskDocumentRef | None = None,
        master: TaskDocumentRef | None = None,
    ) -> dict[str, str]:
        """One hosted seat's environment, exactly as a launch writes it."""

        references = {"sprint": sprint, "master": master, "task": None}
        values = {
            "AR_PASEO_AGENT_ID": agent_id,
            "AR_ROLE": role,
            "AR_REQUEST_ID": str(uuid.uuid4()),
            "AR_REPORT_PATH": f"/tmp/ar-report-{agent_id}.md",
        }
        for name, reference in references.items():
            values[f"AR_{name.upper()}_REF"] = (
                "" if reference is None else json.dumps(reference.model_dump(mode="json"))
            )
        return values

    def architect_environment(self, agent_id: str) -> dict[str, str]:
        return self.environment("architect", agent_id)

    def manager_environment(self, agent_id: str) -> dict[str, str]:
        sprint = TaskDocumentRef(repository=REPO, path=f"{MASTER}/sprint.json")
        master_ref = TaskDocumentRef(repository=REPO, path=f"{MASTER}/master.json")
        return self.environment("manager", agent_id, sprint=sprint, master=master_ref)

    # -- running one call ---------------------------------------------------------------

    def run_call(
        self,
        call: ExplainerControlCall,
        *,
        environment: dict[str, str] | None = None,
        send: Any = None,
    ) -> dict[str, Any]:
        with (
            mock.patch.object(control, "_explainer_host", lambda config: self.host),
            tool_call_context("explainer_control", {"action": call.action}, config=self.config),
        ):
            return explainer_control_payload(self.config, call, environment=environment, send=send)

    # -- records and held stores --------------------------------------------------------

    def record_file(self) -> Path:
        return master_architect_path(self.config.coordination_root, REPO, MASTER)

    def write_receipt(self, path: Path, **fields: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": RECEIPT_SCHEMA, **fields}), encoding="utf-8")

    def held_store(self, folder: Path, agent_id: str) -> Path:
        return folder / ".ar-product" / "held-messages" / f"{agent_id}.json"

    def write_held_store(self, folder: Path, agent_id: str, rows: list[dict[str, Any]]) -> Path:
        path = self.held_store(folder, agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema": HELD_STORE_SCHEMA,
                    "agentId": agent_id,
                    "messages": rows,
                    "updatedAt": "2026-10-22T09:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        return path

    def held_rows(self, path: Path) -> list[dict[str, Any]]:
        return json.loads(path.read_text(encoding="utf-8"))["messages"]


class ClaimTests(_World):
    """MIK-R189 rule 7 step 3: who may claim, what it writes, and what it moves."""

    def test_an_architect_claim_writes_the_record(self) -> None:
        answer = self.run_call(
            ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
            environment=self.architect_environment(ARCHITECT),
        )

        self.assertTrue(answer["ok"])
        self.assertEqual(answer["state"], "claimed")
        self.assertEqual(answer["architectAgentId"], ARCHITECT)
        self.assertEqual(answer["record"]["act"], CLAIM)
        record = read_record(self.record_file())
        assert record is not None
        self.assertEqual(record["architectAgentId"], ARCHITECT)
        self.assertEqual(record["act"], CLAIM)
        self.assertTrue(record["at"])

    def test_a_non_architect_claim_is_refused_and_writes_nothing(self) -> None:
        answer = self.run_call(
            ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
            environment=self.manager_environment(str(uuid.uuid4())),
        )

        self.assertFalse(answer["ok"])
        self.assertEqual(answer["state"], "refused")
        self.assertEqual(answer["reason"], NOT_AN_ARCHITECT)
        self.assertEqual(answer["code"], NOT_AN_ARCHITECT)
        self.assertFalse(self.record_file().exists())

    def test_a_claim_moves_one_held_report_to_the_claiming_seat(self) -> None:
        # The record names the previous seat, whose store still holds this master's report.
        write_record(
            self.record_file(),
            architect_agent_id=OTHER_ARCHITECT,
            act=MANAGER_START,
            at="2026-10-22T08:00:00+00:00",
        )
        previous_folder = self.root / "workspace" / "previous-architect"
        previous_folder.mkdir(parents=True)
        self.write_receipt(
            self.config.coordination_root
            / "notes"
            / "reports"
            / EXECUTIONS_DIRECTORY
            / "architect"
            / "sessions"
            / f"{uuid.uuid4()}.json",
            agentId=OTHER_ARCHITECT,
            role="architect",
            workspace={"path": str(previous_folder)},
        )
        row = {
            "id": str(uuid.uuid4()),
            "senderLine": f"From explainer · {MASTER} · agent {EXPLAINER}",
            "senderAgentId": EXPLAINER,
            "text": f"From explainer · {MASTER} · agent {EXPLAINER}\n\nthe report",
            "takenAt": "2026-10-22T09:00:00+00:00",
            "reason": "recipient-busy",
        }
        store = self.write_held_store(previous_folder, OTHER_ARCHITECT, [row])
        sent: list[tuple[str, str, str]] = []

        answer = self.run_call(
            ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
            environment=self.architect_environment(ARCHITECT),
            send=lambda agent_id, text, message_id: (
                sent.append((agent_id, text, message_id)) or True
            ),
        )

        self.assertEqual(read_record_architect(self.record_file()), ARCHITECT)
        self.assertEqual(self.held_rows(store), [])
        self.assertEqual(sent, [(ARCHITECT, row["text"], row["id"])])
        self.assertEqual(answer["claim"]["found"], 1)
        self.assertEqual(answer["claim"]["withdrawn"], [row["id"]])
        self.assertEqual(answer["claim"]["sent"], 1)
        self.assertEqual(answer["claim"]["held"], 0)
        log_path = (
            self.config.coordination_root
            / "notes"
            / "reports"
            / "developer-messages"
            / REPO
            / MASTER
            / "reports.jsonl"
        )
        logged = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(logged[-1]["reportId"], row["id"])
        self.assertEqual(logged[-1]["state"], "reported")
        self.assertEqual(logged[-1]["architect"], ARCHITECT)

    def _setup_claim_pair(self) -> tuple[Path, Path, Path]:
        write_record(
            self.record_file(),
            architect_agent_id=OTHER_ARCHITECT,
            act=MANAGER_START,
            at="2026-10-22T08:00:00+00:00",
        )
        previous_folder = self.root / "workspace" / "previous-architect"
        claimer_folder = self.root / "workspace" / "claimer-architect"
        previous_folder.mkdir(parents=True, exist_ok=True)
        claimer_folder.mkdir(parents=True, exist_ok=True)
        for aid, folder in ((OTHER_ARCHITECT, previous_folder), (ARCHITECT, claimer_folder)):
            self.write_receipt(
                self.config.coordination_root
                / "notes"
                / "reports"
                / EXECUTIONS_DIRECTORY
                / "architect"
                / "sessions"
                / f"{aid}.json",
                agentId=aid,
                role="architect",
                workspace={"path": str(folder)},
            )
        log_dir = (
            self.config.coordination_root
            / "notes"
            / "reports"
            / "developer-messages"
            / REPO
            / MASTER
        )
        log_dir.mkdir(parents=True, exist_ok=True)
        return previous_folder, claimer_folder, log_dir / "reports.jsonl"

    @staticmethod
    def _make_report_row(report_id: str, file_path: Path | None = None) -> dict[str, Any]:
        footer = (
            f"\n\nMessage file: {file_path.as_posix()} (SHA-256 {'a' * 64})" if file_path else ""
        )
        return {
            "id": report_id,
            "senderLine": f"From explainer · {MASTER} · agent {EXPLAINER}",
            "senderAgentId": EXPLAINER,
            "text": f"From explainer · {MASTER} · agent {EXPLAINER}\n\nthe report{footer}",
            "takenAt": "2026-10-22T09:00:00+00:00",
            "reason": "recipient-busy",
        }

    def test_claim_respects_provider_limit_timeout_and_full_or_unavailable_destination(
        self,
    ) -> None:
        previous_folder, claimer_folder, log_path = self._setup_claim_pair()
        limited_id, already_unknown_id = str(uuid.uuid4()), str(uuid.uuid4())
        log_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "messageId": "m-1",
                            "reportId": limited_id,
                            "sha256": "a" * 64,
                            "state": "held",
                            "architect": OTHER_ARCHITECT,
                            "at": "2026-10-22T09:00:00+00:00",
                        }
                    ),
                    json.dumps(
                        {
                            "messageId": "m-2",
                            "reportId": already_unknown_id,
                            "sha256": "b" * 64,
                            "state": "sent, outcome unknown",
                            "architect": OTHER_ARCHITECT,
                            "at": "2026-10-22T09:00:00+00:00",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.write_held_store(
            previous_folder,
            OTHER_ARCHITECT,
            [self._make_report_row(limited_id), self._make_report_row(already_unknown_id)],
        )
        sender_calls: list[str] = []
        with mock.patch.object(
            control,
            "provider_limit_hold",
            return_value={
                "heldReason": "provider-limit",
                "limitedUntil": "2026-10-22T12:00:00+00:00",
            },
            create=True,
        ):
            limited_ans = self.run_call(
                ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
                environment=self.architect_environment(ARCHITECT),
                send=lambda _a, _t, mid: sender_calls.append(mid) or True,
            )
        self.assertEqual(sender_calls, [])
        self.assertEqual(limited_ans["claim"]["found"], 1)
        self.assertEqual(limited_ans["claim"]["held"], 1)
        self.assertEqual(limited_ans["claim"]["sent"], 0)
        claimer_rows = self.held_rows(self.held_store(claimer_folder, ARCHITECT))
        self.assertEqual(len(claimer_rows), 1)
        self.assertEqual(claimer_rows[0]["id"], limited_id)
        self.assertEqual(claimer_rows[0]["reason"], "provider-limit")

        # 2) Timeout / SEND_OUTCOME_UNKNOWN records unknown, is not held or tray-copied, and retry
        #    never resends it.
        timeout_id = str(uuid.uuid4())
        write_record(
            self.record_file(),
            architect_agent_id=OTHER_ARCHITECT,
            act=MANAGER_START,
            at="2026-10-22T09:30:00+00:00",
        )
        log_path.write_text(
            json.dumps(
                {
                    "messageId": "m-3",
                    "reportId": timeout_id,
                    "sha256": "c" * 64,
                    "state": "held",
                    "architect": OTHER_ARCHITECT,
                    "at": "2026-10-22T09:30:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.write_held_store(previous_folder, OTHER_ARCHITECT, [self._make_report_row(timeout_id)])
        self.write_held_store(claimer_folder, ARCHITECT, [])

        def timeout_sender(_a: str, _t: str, _mid: str) -> bool:
            raise PaseoBridgeFailure(SEND_OUTCOME_UNKNOWN, "bridge timed out")

        timeout_ans = self.run_call(
            ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
            environment=self.architect_environment(ARCHITECT),
            send=timeout_sender,
        )
        self.assertEqual(timeout_ans["claim"]["sent"], 0)
        self.assertEqual(timeout_ans["claim"]["held"], 0)
        self.assertEqual(timeout_ans["claim"]["unknown"], 1)
        self.assertEqual(self.held_rows(self.held_store(claimer_folder, ARCHITECT)), [])
        last_line = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last_line["state"], "sent, outcome unknown")
        self.write_held_store(previous_folder, OTHER_ARCHITECT, [self._make_report_row(timeout_id)])
        retry_calls: list[str] = []
        self.run_call(
            ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
            environment=self.architect_environment(ARCHITECT),
            send=lambda _a, _t, mid: retry_calls.append(mid) or True,
        )
        self.assertEqual(retry_calls, [])

        # 3) Full destination held store falls back to tray (`not reported`) without falsely
        #    incrementing `held`.
        full_id = str(uuid.uuid4())
        report_file = self.root / "report-for-tray.md"
        report_file.write_text("report body\n", encoding="utf-8")
        write_record(
            self.record_file(),
            architect_agent_id=OTHER_ARCHITECT,
            act=MANAGER_START,
            at="2026-10-22T10:00:00+00:00",
        )
        log_path.write_text(
            json.dumps(
                {
                    "messageId": "m-4",
                    "reportId": full_id,
                    "sha256": "d" * 64,
                    "state": "held",
                    "file": report_file.as_posix(),
                    "architect": OTHER_ARCHITECT,
                    "at": "2026-10-22T10:00:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.write_held_store(
            previous_folder, OTHER_ARCHITECT, [self._make_report_row(full_id, report_file)]
        )
        self.write_held_store(
            claimer_folder,
            ARCHITECT,
            [self._make_report_row(str(uuid.uuid4())) for _ in range(MAX_HELD_PER_RECIPIENT)],
        )
        full_ans = self.run_call(
            ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
            environment=self.architect_environment(ARCHITECT),
            send=lambda _a, _t, _mid: False,
        )
        self.assertEqual(full_ans["claim"]["sent"], 0)
        self.assertEqual(full_ans["claim"]["held"], 0)
        self.assertEqual(full_ans["claim"]["notReported"], 1)
        self.assertTrue((self.root / "report-for-tray.undelivered.md").is_file())
        last_full_line = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last_full_line["state"], "not reported")

    def _setup_claim_without_claimer_workspace(self) -> tuple[Path, Path]:
        """The previous seat holds reports; the claiming seat has no launch receipt at all."""
        write_record(
            self.record_file(),
            architect_agent_id=OTHER_ARCHITECT,
            act=MANAGER_START,
            at="2026-10-22T08:00:00+00:00",
        )
        previous_folder = self.root / "workspace" / "previous-architect"
        previous_folder.mkdir(parents=True, exist_ok=True)
        self.write_receipt(
            self.config.coordination_root
            / "notes"
            / "reports"
            / EXECUTIONS_DIRECTORY
            / "architect"
            / "sessions"
            / f"{OTHER_ARCHITECT}.json",
            agentId=OTHER_ARCHITECT,
            role="architect",
            workspace={"path": str(previous_folder)},
        )
        log_path = (
            self.config.coordination_root
            / "notes"
            / "reports"
            / "developer-messages"
            / REPO
            / MASTER
            / "reports.jsonl"
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return previous_folder, log_path

    def test_claim_with_an_unavailable_destination_falls_back_to_the_tray(self) -> None:
        # The claiming seat has no launch receipt and no workspace: its destination cannot take a
        # held report, so the withdrawn report goes back to the tray as `not reported`, with the
        # reports.jsonl row naming the transition, and nothing is re-held anywhere.
        previous_folder, log_path = self._setup_claim_without_claimer_workspace()
        unavailable_id = str(uuid.uuid4())
        report_file = self.root / "report-unavailable.md"
        report_file.write_text("report body\n", encoding="utf-8")
        log_path.write_text(
            json.dumps(
                {
                    "messageId": "m-5",
                    "reportId": unavailable_id,
                    "sha256": "e" * 64,
                    "state": "held",
                    "file": report_file.as_posix(),
                    "architect": OTHER_ARCHITECT,
                    "at": "2026-10-22T11:00:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.write_held_store(
            previous_folder, OTHER_ARCHITECT, [self._make_report_row(unavailable_id, report_file)]
        )
        answer = self.run_call(
            ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
            environment=self.architect_environment(ARCHITECT),
            send=lambda _a, _t, _mid: False,
        )
        self.assertEqual(answer["claim"]["sent"], 0)
        self.assertEqual(answer["claim"]["held"], 0)
        self.assertEqual(answer["claim"]["notReported"], 1)
        self.assertTrue((self.root / "report-unavailable.undelivered.md").is_file())
        last_line = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last_line["reportId"], unavailable_id)
        self.assertEqual(last_line["state"], "not reported")
        self.assertIn("destination-unavailable", last_line.get("refusal") or "")

    def test_claim_restores_custody_when_no_report_file_exists(self) -> None:
        # With no report file on disk and no claiming workspace, the withdrawn row is restored to
        # the previous seat's store and logs `held`, so a retry can still move the report.
        previous_folder, log_path = self._setup_claim_without_claimer_workspace()
        orphan_id = str(uuid.uuid4())
        self.write_held_store(previous_folder, OTHER_ARCHITECT, [self._make_report_row(orphan_id)])
        answer = self.run_call(
            ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
            environment=self.architect_environment(ARCHITECT),
            send=lambda _a, _t, _mid: False,
        )
        self.assertEqual(answer["claim"]["sent"], 0)
        self.assertEqual(answer["claim"]["held"], 0)
        self.assertEqual(answer["claim"]["notReported"], 1)
        restored = self.held_rows(self.held_store(previous_folder, OTHER_ARCHITECT))
        self.assertEqual([row["id"] for row in restored], [orphan_id])
        last_line = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last_line["reportId"], orphan_id)
        self.assertEqual(last_line["state"], "held")
        self.assertEqual(last_line["architect"], OTHER_ARCHITECT)


class SeatActionTests(_World):
    """MIK-R186 rule 6: only the seat the record names may update or retire."""

    def setUp(self) -> None:
        super().setUp()
        self.seat_call: Any = None

    def named(self, agent_id: str) -> None:
        self.run_call(
            ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
            environment=self.architect_environment(agent_id),
        )

    def update(self, agent_id: str) -> dict[str, Any]:
        answer = mock.Mock()
        answer.current = False
        answer.as_record.return_value = {"current": False, "previousNumber": 2}
        with mock.patch.object(control, "update_seat", return_value=answer) as seat:
            payload = self.run_call(
                ExplainerControlCall(action="update", repo_id=REPO, task_name=MASTER, leaf_id=LEAF),
                environment=self.architect_environment(agent_id),
            )
        self.seat_call = seat.call_args
        return payload

    def retire(self, agent_id: str) -> dict[str, Any]:
        answer = mock.Mock()
        answer.as_record.return_value = {"receipt": {"id": "r-1"}, "workspaceRemoved": True}
        with mock.patch.object(control, "retire_seat", return_value=answer) as seat:
            payload = self.run_call(
                ExplainerControlCall(
                    action="retire",
                    repo_id=REPO,
                    task_name=MASTER,
                    leaf_id=LEAF,
                    receipt_id="r-1",
                ),
                environment=self.architect_environment(agent_id),
            )
        self.seat_call = seat.call_args
        return payload

    def test_update_by_the_named_seat_is_accepted(self) -> None:
        self.named(ARCHITECT)

        answer = self.update(ARCHITECT)

        self.assertTrue(answer["ok"])
        self.assertEqual(answer["state"], "updated")
        self.assertEqual(answer["architectAgentId"], ARCHITECT)
        self.assertEqual(answer["update"], {"current": False, "previousNumber": 2})
        deps, scope = self.seat_call.args
        self.assertIs(deps.host, self.host)
        self.assertEqual((scope.repository_id, scope.task, scope.leaf_id), (REPO, MASTER, LEAF))

    def test_retire_by_the_named_seat_is_accepted_with_its_receipt(self) -> None:
        self.named(ARCHITECT)

        answer = self.retire(ARCHITECT)

        self.assertTrue(answer["ok"])
        self.assertEqual(answer["state"], "retired")
        self.assertEqual(answer["retire"]["workspaceRemoved"], True)
        self.assertEqual(self.seat_call.kwargs["receipt_id"], "r-1")
        self.assertEqual(self.seat_call.args[1].leaf_id, LEAF)

    def test_update_by_another_seat_is_refused_and_names_claim(self) -> None:
        self.named(ARCHITECT)

        answer = self.update(OTHER_ARCHITECT)

        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], NOT_THIS_MASTERS_ARCHITECT)
        self.assertIn("claim", answer["detail"])
        self.assertIn(ARCHITECT, answer["detail"])

    def test_retire_by_another_seat_is_refused_and_names_claim(self) -> None:
        self.named(ARCHITECT)

        answer = self.retire(OTHER_ARCHITECT)

        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], NOT_THIS_MASTERS_ARCHITECT)
        self.assertIn("claim", answer["detail"])

    def test_update_with_no_record_is_refused_and_names_claim(self) -> None:
        answer = self.update(ARCHITECT)

        self.assertFalse(answer["ok"])
        self.assertEqual(answer["state"], "refused")
        self.assertEqual(answer["reason"], NOT_THIS_MASTERS_ARCHITECT)
        self.assertIn("claim", answer["detail"])

    def test_retire_with_no_record_is_refused(self) -> None:
        answer = self.retire(ARCHITECT)

        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], NOT_THIS_MASTERS_ARCHITECT)

    def test_an_update_without_a_leaf_is_refused_by_name(self) -> None:
        self.named(ARCHITECT)

        answer = self.run_call(
            ExplainerControlCall(action="update", repo_id=REPO, task_name=MASTER),
            environment=self.architect_environment(ARCHITECT),
        )

        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], "leaf-required")

    def test_a_retire_without_its_receipt_id_is_refused_by_name(self) -> None:
        self.named(ARCHITECT)

        answer = self.run_call(
            ExplainerControlCall(action="retire", repo_id=REPO, task_name=MASTER, leaf_id=LEAF),
            environment=self.architect_environment(ARCHITECT),
        )

        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], "receipt-id-required")

    def test_unknown_or_empty_action_is_refused_before_any_lookup_or_retire(self) -> None:
        self.named(ARCHITECT)
        record_before = self.record_file().read_bytes()
        for bad_action in ("retir", "unknown", ""):
            with (
                mock.patch.object(control, "retire_seat") as retire_mock,
                mock.patch.object(control, "update_seat") as update_mock,
            ):
                answer = self.run_call(
                    ExplainerControlCall(
                        action=bad_action,
                        repo_id=REPO,
                        task_name=MASTER,
                        leaf_id=LEAF,
                        receipt_id="r-1",
                    ),
                    environment=self.architect_environment(ARCHITECT),
                )
            self.assertFalse(answer["ok"])
            self.assertEqual(answer["state"], "refused")
            self.assertEqual(answer["reason"], UNKNOWN_ACTION)
            self.assertEqual(answer["code"], UNKNOWN_ACTION)
            retire_mock.assert_not_called()
            update_mock.assert_not_called()
            self.assertEqual(self.record_file().read_bytes(), record_before)

    def test_the_named_seat_reaches_the_real_seat_service(self) -> None:
        """The accepted caller is answered by the seat service itself, composed as the page is.

        No workspace exists for this leaf, so the service's own refusal comes back -- and the fake
        host was asked about the seat it names, which is what proves the composition is the real
        one and not a second wiring of it.
        """

        self.named(ARCHITECT)
        (self.config.coordination_root / "tasks" / REPO / MASTER).mkdir(parents=True)

        answer = self.run_call(
            ExplainerControlCall(action="update", repo_id=REPO, task_name=MASTER, leaf_id=LEAF),
            environment=self.architect_environment(ARCHITECT),
        )

        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], "leaf-unavailable")
        self.assertTrue(self.host.asked)


class RealCompositionTests(_World):
    """S03: both seat actions reach the real seat service through the registered tool.

    The leaf, its planning document and its series contract are real; only the host adapter is a
    fake, because no Paseo host runs in a unit process. Update runs the whole planned comparison
    (facts, binding read, contract resolution at the master tips, packet preparation) and Retire
    runs its receipt, archive and removal steps -- each without a TypeError or a 500.
    """

    def _planned_environment(self) -> tuple[Any, Any, dict[str, str], Path]:
        scratch = tempfile.TemporaryDirectory(prefix="ar-explainer-planned-")
        self.addCleanup(scratch.cleanup)
        fixture = intent_fixture(Path(scratch.name))
        contract_text = fixture.series.contract_path.read_text(encoding="utf-8")
        code_lines, memory_lines = contract_branch_lines(contract_text, organizational=False)
        code_commit = resolve_line_commit(fixture.code, code_lines)
        memory_commit = resolve_line_commit(fixture.memory, memory_lines)
        assert code_commit is not None and memory_commit is not None
        code_tree = run_git(fixture.code, ["rev-parse", f"{code_commit}^{{tree}}"]).stdout.strip()
        memory_tree = run_git(
            fixture.memory, ["rev-parse", f"{memory_commit}^{{tree}}"]
        ).stdout.strip()
        record = planned_comparison(
            PlannedComparisonTips(
                task_id=fixture_master,
                leaf_id=fixture_leaf,
                code_repository=str(fixture.code),
                code_commit=code_commit,
                code_tree=code_tree,
                memory_repository=str(fixture.memory),
                memory_commit=memory_commit,
                memory_tree=memory_tree,
            )
        )
        binding = FrameBinding.from_record(
            record,
            comparison_owner="planned",
            comparison_reference=planned_reference(fixture_master, fixture_leaf),
        )
        planned = PlannedIdentity(
            code_tip=code_commit,
            memory_tip=memory_commit,
            code_tree=code_tree,
            memory_tree=memory_tree,
        )
        workspace = leaf_workspace(fixture.config, fixture_repo, fixture_master, fixture_leaf)
        workspace.mkdir(parents=True)
        (workspace / "binding.json").write_text(
            json.dumps(
                ExplainerBinding(
                    binding=binding,
                    planned=planned,
                    snapshot="snapshot-0",
                    prepared_at="2026-10-24T00:00:00+00:00",
                    packet_digest="sha256:" + "0" * 64,
                    record_digest=None,
                ).model_dump(by_alias=True),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        write_record(
            master_architect_path(fixture.config.coordination_root, fixture_repo, fixture_master),
            architect_agent_id=ARCHITECT,
            act=MANAGER_START,
            at="2026-10-24T00:00:00+00:00",
        )
        return fixture, self.host, self.architect_environment(ARCHITECT), workspace

    def test_update_and_retire_reach_the_real_seat_service(self) -> None:
        fixture, host, environment, workspace = self._planned_environment()

        def run(action: str, **fields: str) -> dict[str, Any]:
            with (
                mock.patch.object(control, "_explainer_host", lambda config: host),
                tool_call_context("explainer_control", {"action": action}, config=fixture.config),
            ):
                return explainer_control_payload(
                    fixture.config,
                    ExplainerControlCall(
                        action=action, repo_id="repo-a", task_name="intent-master", **fields
                    ),
                    environment=environment,
                )

        update = run("update", leaf_id="TEST-INTENT-LEAF")
        self.assertTrue(update["ok"], json.dumps(update, default=str))
        # The real planned comparison was resolved and equal to the binding, so the update is
        # ``current`` and wrote nothing -- the service's own answer, not a stub's.
        self.assertEqual(update["state"], "current")
        self.assertTrue(update["update"]["current"])
        self.assertTrue((workspace / "binding.json").is_file())

        retire = run("retire", leaf_id="TEST-INTENT-LEAF", receipt_id="r-1")
        self.assertTrue(retire["ok"], json.dumps(retire, default=str))
        self.assertEqual(retire["state"], "retired")
        self.assertEqual(retire["retire"]["receipt"]["state"], "finished")
        self.assertTrue(retire["retire"]["workspaceRemoved"])
        self.assertFalse((workspace / "binding.json").exists())
        self.assertEqual(host.archived, [retire["retire"]["receipt"]["tree"]["agent_id"]])


class ManagerStartWriterTests(_World):
    """MIK-R189 rule 7 step 3: what a Manager start records, and what it must never record."""

    def setUp(self) -> None:
        super().setUp()
        self.sprint = TaskDocumentRef(repository=REPO, path=f"{MASTER}/sprint.json")
        self.master_ref = TaskDocumentRef(repository=REPO, path=f"{MASTER}/master.json")
        self.request = RoleDispatchRequest(
            role="manager",
            requestId=uuid.uuid4(),
            sprintDocumentRef=self.sprint,
            masterDocumentRef=self.master_ref,
        )

    def test_a_hosted_architect_starter_is_recorded(self) -> None:
        starter = StartingAgent(ARCHITECT, "architect", MASTER, sprint_ref=self.sprint)

        record = remember_master_architect(self.config, self.request, starter)

        assert record is not None
        self.assertEqual(record["architectAgentId"], ARCHITECT)
        self.assertEqual(record["act"], MANAGER_START)
        self.assertEqual(read_record_architect(self.record_file()), ARCHITECT)

    def test_an_orchestrator_starter_records_its_architect_parent(self) -> None:
        self.write_receipt(
            _receipt_path(
                self.config,
                RoleSelection(role="orchestrator", sprintDocumentRef=self.sprint),
            ),
            agentId=ORCHESTRATOR,
            role="orchestrator",
            parentAgentId=ARCHITECT,
        )
        self.write_receipt(
            self.config.coordination_root
            / "notes"
            / "reports"
            / EXECUTIONS_DIRECTORY
            / "architect"
            / "sessions"
            / f"{uuid.uuid4()}.json",
            agentId=ARCHITECT,
            role="architect",
        )
        starter = StartingAgent(ORCHESTRATOR, "orchestrator", MASTER, sprint_ref=self.sprint)

        record = remember_master_architect(self.config, self.request, starter)

        assert record is not None
        self.assertEqual(record["architectAgentId"], ARCHITECT)
        self.assertNotEqual(record["architectAgentId"], ORCHESTRATOR)

    def test_an_orchestrator_without_a_recorded_architect_parent_writes_nothing(self) -> None:
        self.write_receipt(
            _receipt_path(
                self.config,
                RoleSelection(role="orchestrator", sprintDocumentRef=self.sprint),
            ),
            agentId=ORCHESTRATOR,
            role="orchestrator",
        )
        starter = StartingAgent(ORCHESTRATOR, "orchestrator", MASTER, sprint_ref=self.sprint)

        self.assertIsNone(remember_master_architect(self.config, self.request, starter))
        self.assertFalse(self.record_file().exists())

    def test_any_other_starter_writes_nothing(self) -> None:
        for role in ("manager", "reviewer", "investigator"):
            with self.subTest(role=role):
                starter = StartingAgent(str(uuid.uuid4()), role, MASTER, sprint_ref=self.sprint)
                self.assertIsNone(remember_master_architect(self.config, self.request, starter))
                self.assertFalse(self.record_file().exists())

    def test_the_launcher_writes_nothing(self) -> None:
        self.assertIsNone(remember_master_architect(self.config, self.request, None))
        self.assertFalse(self.record_file().exists())


if __name__ == "__main__":
    unittest.main()


class ReceiptBoundaryTests(_World):
    """S06: the registered MCP tool refuses a path receipt id and writes nothing outside."""

    def named(self, agent_id: str) -> None:
        self.run_call(
            ExplainerControlCall(action="claim", repo_id=REPO, task_name=MASTER),
            environment=self.architect_environment(agent_id),
        )

    def test_a_path_receipt_id_is_refused_at_the_tool_boundary_and_leaves_outside_bytes(
        self,
    ) -> None:
        self.named(ARCHITECT)
        outside = self.root.parent / "outside-inventory.json"
        outside.write_text("keep me", encoding="utf-8")

        def inventory() -> list[str]:
            # The harness's own tool log under logs/ is not a product effect.
            return sorted(
                str(path.relative_to(self.root))
                for path in self.root.rglob("*")
                if "logs" not in path.relative_to(self.root).parts
            )

        before = inventory()

        with mock.patch.object(control, "retire_seat") as retire_mock:
            answer = self.run_call(
                ExplainerControlCall(
                    action="retire",
                    repo_id=REPO,
                    task_name=MASTER,
                    leaf_id=LEAF,
                    receipt_id="../../outside-inventory.json",
                ),
                environment=self.architect_environment(ARCHITECT),
            )

        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], "receipt-id-required")
        retire_mock.assert_not_called()
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep me")
        self.assertEqual(inventory(), before)
