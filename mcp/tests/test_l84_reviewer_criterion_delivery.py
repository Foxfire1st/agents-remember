"""L84 A3: public starters file criterion-bound intake before the native held opening.

The offered host/catalog are fixture doubles. Capturing an agent-create payload proves
delivery to that boundary, never model consumption, criterion reading or a review verdict.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from agents_remember.application.agent_binding import AgentBinding, read_agent_binding
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli.paseo_role_tools import send_role_message, start_leaf_set, start_role
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path
from agents_remember.models.role_agents import RoleMessageCall, RoleStartCall, RoleStartSetCall
from agents_remember.models.role_launcher import LauncherRole, RoleSelection
from agents_remember.tasks import read_task_doc
from role_first_message_support import FirstMessageMatrixBase
from test_worktree_sync import git


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ReviewerCriterionDeliveryTests(FirstMessageMatrixBase):
    """One actual fixture world; no foreign topology, caller or receipt setup."""

    def _role_receipt(self, role: LauncherRole) -> dict[str, Any]:
        selection = RoleSelection(role=role, **self.refs_of(role))
        receipt = _read_receipt(_receipt_path(self.config, selection))
        assert receipt is not None
        return receipt

    def _filed_intake(self, prompt: str, body: str, catalogs: dict[str, bytes]) -> dict[str, Any]:
        pointer = re.search(r"^Message file: (.+) \(SHA-256 ([0-9a-f]{64})\)$", prompt, re.M)
        self.assertIsNotNone(pointer, "native opening must name its actual filed intake")
        assert pointer is not None
        path, digest = Path(pointer[1]), pointer[2]
        data = path.read_bytes()
        self.assertEqual(_sha(data), digest)
        self.assertEqual(path.parent, self.contract.task_root / "notes" / "messages")
        text = data.decode("utf-8")
        self.assertIn("\n## body\n" + body, text)
        self.assertNotIn(body, prompt, "full intake travels in its file")
        records = re.findall(r"^- (.+) sha256:([0-9a-f]{64}) \(copied as (.+)\)$", text, re.M)
        self.assertEqual({source for source, _, _ in records}, set(catalogs))
        copied = []
        for source, source_hash, copy in records:
            target = Path(copy)
            self.assertEqual(source_hash, _sha(catalogs[source]))
            self.assertEqual(Path(source).read_bytes(), catalogs[source])
            self.assertEqual(target.parent, path.parent)
            self.assertEqual(target.read_bytes(), catalogs[source])
            copied.append(
                {
                    "source": source,
                    "sha256": source_hash,
                    "copy": copy,
                    "content": target.read_text(encoding="utf-8"),
                }
            )
        return {
            "path": str(path),
            "sha256": digest,
            "content": text,
            "pointer": pointer[0],
            "attachments": copied,
        }

    def _start_manager(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, str], AgentBinding]:
        # A genuine public Manager start mints the caller, its native receipt and environment.
        manager_call = RoleStartCall(
            role="manager",
            request_id=uuid.uuid4(),
            sprint_document_ref=self.sprint_ref,
            master_document_ref=self.master_ref,
        )
        manager_answer = start_role(self.config, manager_call, environment={})
        self.assertTrue(manager_answer.get("ok"), manager_answer)
        self.assertEqual(manager_answer["status"], "running")
        manager = self._role_receipt("manager")
        self.assertTrue(manager["toolServer"]["applied"])
        environment = manager["toolServer"]["environment"]
        binding = read_agent_binding(environment)
        assert binding is not None
        self.assertEqual(binding.role, "manager")
        self.assertEqual(binding.agent_id, manager_answer["agentId"])
        self.assertEqual(binding.agent_id, manager["agentId"])
        self.assertEqual(binding.agent_id, manager["execution"]["agentId"])
        self.assertNotIn("replayRequest", manager)
        self.assertEqual(binding.request_id, str(manager_call.request_id))
        self.assertEqual(binding.request_id, manager["requestId"])
        self.assertEqual(binding.report_path, manager["report"]["path"])
        self.assertEqual(binding.sprint_ref, self.sprint_ref)
        self.assertEqual(binding.master_ref, self.master_ref)
        self.assertIsNone(binding.task_ref)
        return manager_answer, manager, environment, binding

    def _held_leaf_set(
        self, binding: AgentBinding, environment: dict[str, str]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # The PUBLIC held starter owns three seats, not a fabricated single held role.
        set_call = RoleStartSetCall(
            request_id=uuid.uuid4(),
            sprint_document_ref=self.sprint_ref,
            master_document_ref=self.master_ref,
            task_document_ref=self.leaf_ref,
        )
        started = start_leaf_set(self.config, set_call, environment=environment)
        self.assertTrue(started.get("ok"), started)
        self.assertEqual(started["status"], "completed")
        self.assertEqual(set(started["seats"]), {"worker", "reviewer", "curator"})
        self.assertEqual(started["seats"]["worker"]["status"], "running")
        for role in ("reviewer", "curator"):
            self.assertEqual(started["seats"][role]["status"], "held")
            self.assertTrue(started["seats"][role]["held"])
            self.assertFalse(started["seats"][role]["opened"])
        reviewer = self._role_receipt("reviewer")
        reviewer_id = started["seats"]["reviewer"]["agentId"]
        self.assertEqual(reviewer["agentId"], reviewer_id)
        self.assertEqual(reviewer["execution"]["agentId"], reviewer_id)
        self.assertEqual(reviewer["replayRequest"]["agent"]["agentId"], reviewer_id)
        self.assertEqual(reviewer["parentAgentId"], binding.agent_id)
        self.assertEqual(reviewer["requestId"], started["seats"]["reviewer"]["requestId"])
        self.assertEqual(
            set(self.runtime.agents),
            {binding.agent_id, *(s["agentId"] for s in started["seats"].values())},
        )
        opening = reviewer["heldOpeningPrompt"]
        self.assertIn("Confirm the assignment before inspection", opening)
        self.assertIn(
            "If the opening names no review mode, ask your starter for its first "
            "`role_message` on `agents-remember-task` and wait. A Reviewer started from the "
            "dashboard has no starter and asks the developer in its own chat instead. "
            "Inspect nothing and infer no mode before the answer arrives.",
            opening,
        )
        self.assertIn(
            "Once intake arrives, require every applicable input below; refuse and report "
            "omissions rather than guessing.",
            opening,
        )
        intake = re.search(
            r"Before inspecting the assigned packet or candidate, follow "
            r"\[the review intake procedure\]\(([^)]+)\) for the mode, handover scope "
            r"and chat boundary\.",
            opening,
        )
        self.assertIsNotNone(intake, "intake home must be named before inspection")
        assert intake is not None
        target, fragment = intake[1].rsplit("#", 1)
        self.assertEqual(fragment, "receive-the-intake-and-hand-over-the-leaf-verdict")
        with shipped_composition_corpus() as (corpus, _):
            self.assertEqual(Path(target).resolve(), (corpus / "operations/review.md").resolve())
        self.assertIn("Require these inputs before candidate inspection:", opening)
        self.assertFalse(
            any(
                command == "agent-create"
                and payload.get("agentId") == reviewer_id
                and isinstance(payload.get("prompt"), str)
                for command, payload in self.runtime.calls
            )
        )
        return started, reviewer

    def test_public_set_delivers_caller_bound_criterion_intake_before_first_inspection(
        self,
    ) -> None:
        manager_answer, manager, environment, binding = self._start_manager()
        started, reviewer = self._held_leaf_set(binding, environment)
        reviewer_id, opening = reviewer["agentId"], reviewer["heldOpeningPrompt"]
        assert self.contract.memory_worktree is not None
        tools = self.contract.memory_worktree / "system" / "tools.md"
        tools.parent.mkdir(parents=True, exist_ok=True)
        tools.write_text(
            "# Synthetic fixture tools\n\nQuestion-only transport exercise; no code "
            "inspection, checks or verdict is requested.\n",
            encoding="utf-8",
        )
        leaf = read_task_doc(self.contract.task_root / f"{self.leaf_slug}.json")
        with shipped_composition_corpus() as (corpus, manifest):
            catalogs = {
                str(corpus / "criteria" / name): (corpus / "criteria" / name).read_bytes()
                for name in ("code-seam.md", "report-verification.md")
            }
            body = (
                "Synthetic question-only intake delivery fixture. No real review is commissioned.\n"
                "Mode: baseline. Question-only is the purpose, not another review mode.\n"
                f"Canonical task: {self.leaf_ref.key}\nPurpose: verify starter-bound criterion "
                "delivery at the first intake, before any inspection.\n"
                "Seam: leaf-hosted transport question about code-seam CS-1; no real "
                "organizational/atomic/master-exit seam or verdict is asserted.\n"
                "Exact native fixture requirement references:\n"
                + json.dumps(leaf.model_dump(mode="json")["requirements"], indent=2)
                + "\nCorpus disposition: fixture data only; no developer corpus approval claimed.\n"
                + f"Fixture code anchor: {git(self.contract.code_worktree, 'rev-parse', 'HEAD')}\n"
                + f"Fixture memory anchor: {git(self.contract.memory_worktree, 'rev-parse', 'HEAD')}\n"
                + "These are measured fixture anchors, not a Worker freeze or a code-PASS claim.\n"
                + "Worker attempt/freeze/passing handover results: none; no candidate review "
                "is requested. Refuse real inspection without its actual required inputs.\n"
                + "Owner-bound catalogs for this question:\n"
                + "\n".join(
                    f"{source} SHA-256 {_sha(content)}" for source, content in catalogs.items()
                )
                + f"\nResolved fixture tools: {tools} SHA-256 {_sha(tools.read_bytes())}\n"
                + "UI: none. Actual model reading/consumption/reasoning/verdict is outside this "
                "fake-host boundary proof.\n"
            )
            observations: list[dict[str, Any]] = []
            original_observer = self.runtime.observer

            def observe(command: str, payload: dict[str, Any]) -> None:
                assert original_observer is not None
                original_observer(command, payload)
                prompt = payload.get("prompt")
                if (
                    command != "agent-create"
                    or payload.get("agentId") != reviewer_id
                    or not isinstance(prompt, str)
                ):
                    return
                self.assertEqual(
                    payload["idempotencyKey"], f"ar-role-launch:{reviewer['requestId']}"
                )
                self.assertTrue(prompt.startswith(opening + "\n\n"))
                filed = self._filed_intake(prompt, body, catalogs)
                self.assertIn(f"recipientAgentId: {reviewer_id}\n", filed["content"])
                self.assertIn(f"agent {binding.agent_id}", filed["content"])
                self.assertIn(f"agent {binding.agent_id}", prompt[len(opening) :])
                observations.append(
                    {
                        "payload": payload,
                        "filedIntake": filed,
                        "boundary": "before fake host agent-create handler",
                    }
                )

            self.runtime.observer = observe
            delivered = send_role_message(
                self.config,
                RoleMessageCall(
                    agent_id=reviewer_id,
                    text="Take this criterion-bound fixture intake.",
                    body=body,
                    attach=list(catalogs),
                    wait=False,
                ),
                environment=environment,
            )
            self.runtime.observer = original_observer
            self.assertTrue(delivered.get("ok"), delivered)
            self.assertEqual(delivered["status"], "accepted")
            self.assertTrue(delivered["openedHeldSeat"])
            self.assertEqual(delivered["recipientAgentId"], reviewer_id)
            self.assertEqual(len(observations), 1)
            opened = self._role_receipt("reviewer")
            self.assertEqual(opened["status"], "running")
            self.assertEqual(opened["agentId"], reviewer_id)
            self.assertEqual(self._role_receipt("curator")["status"], "held")
            self.assertEqual(self.send_boundary_missing[reviewer["requestId"]], [])
            prompt = observations[0]["payload"]["prompt"]
            # The checker rejects dropped pointers and mismatched hashes on copied payloads.
            with self.assertRaises(AssertionError):
                self._filed_intake(prompt.replace("Message file:", "Dropped file:"), body, catalogs)
            digest = observations[0]["filedIntake"]["sha256"]
            with self.assertRaises(AssertionError):
                self._filed_intake(prompt.replace(digest, "0" * 64), body, catalogs)
            # Portable raw witness: focused pytest -rP retains it before fixture teardown.
            print(
                "NATIVE66_DELIVERY_EVIDENCE="
                + json.dumps(
                    {
                        "schema": "l84-native66-delivery-fixture/v1",
                        "managerAnswer": manager_answer,
                        "managerReceipt": manager,
                        "callerEnvironment": environment,
                        "setAnswer": started,
                        "heldReviewerReceipt": reviewer,
                        "openedReviewerReceipt": opened,
                        "messageAnswer": delivered,
                        "body": body,
                        "packagedCorpus": str(corpus),
                        "manifest": manifest,
                        "observations": observations,
                        "sendBoundaryMissing": self.send_boundary_missing[reviewer["requestId"]],
                        "limits": "Native public preparation/filing/opening payload only. Fake host/catalog; "
                        "no model consumption, criterion reading, verdict or approval.",
                    },
                    ensure_ascii=False,
                )
            )
