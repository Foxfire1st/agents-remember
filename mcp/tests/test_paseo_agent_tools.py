"""What a launch gives an agent beside its first message: tool server, binding, note, artifact."""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.application.agent_binding import (
    TOOL_SERVER_NAME,
    AgentBinding,
    read_agent_binding,
)
from agents_remember.application.orca_task_context import OrcaRoleContext
from agents_remember.cli import paseo_launch
from agents_remember.cli.orca_handover_artifacts import (
    MAX_HANDOVER_ARTIFACT_BYTES,
    artifact_line,
    first_message,
    restore_handover_artifact,
    write_handover_artifact,
)
from agents_remember.cli.paseo_launch import (
    RECOVERY_NOTE_LIMIT,
    recovery_note,
    tool_server_definition,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.core import server_info_payload
from agents_remember.models.core import ServingBuildPayload
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_refs import ResolvedTaskDocument

SOURCE = Path(__file__).resolve().parents[1] / "src" / "agents_remember" / "cli"
# The code a launch runs through. It serves every harness alike, so it names none.
LAUNCH_CODE = (
    SOURCE / "paseo_launch.py",
    SOURCE / "paseo_catalog.py",
    SOURCE / "paseo_bridge.py",
    SOURCE / "paseo_bridge.mjs",
    SOURCE / "orca_task_routes.py",
    SOURCE / "orca_task_receipts.py",
    SOURCE / "orca_task_preparation.py",
    SOURCE / "orca_task_liveness.py",
    SOURCE / "orca_handover_artifacts.py",
    SOURCE.parent / "application" / "agent_binding.py",
)
HARNESS_NAMES = ("codex", "claude", "pi", "hermes", "eve", "opencode", "copilot", "omp")
HARNESS_NAME = re.compile(
    rf"(?<![A-Za-z0-9_])(?:{'|'.join(HARNESS_NAMES)})(?![A-Za-z0-9_])", re.IGNORECASE
)
SPRINT = TaskDocumentRef(repository="agents-remember", path="sprint/task.json")
MASTER = TaskDocumentRef(repository="agents-remember", path="master/task.json")
LEAF = TaskDocumentRef(repository="agents-remember", path="master/01_leaf.json")
SHA = "a" * 64


def binding(**references: TaskDocumentRef) -> AgentBinding:
    return AgentBinding(
        agent_id="f3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607",
        role="worker",
        request_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        report_path="/group/task-reports/orca-native/01_LEAF-worker.md",
        **references,
    )


def context(role: str, *references: TaskDocumentRef) -> OrcaRoleContext:
    documents = [
        ResolvedTaskDocument(
            ref=reference,
            path=Path("/coordination/tasks") / reference.key,
            document=TaskDocument.model_validate(
                {
                    "id": "DOCUMENT",
                    "slug": "document",
                    "title": "A document",
                    "kind": "master",
                    "repo": reference.repository,
                    "createdAt": "2026-10-02T00:00:00+00:00",
                }
            ),
        )
        for reference in references
    ]
    sprint, master, task = (*documents, None, None, None)[:3]
    return OrcaRoleContext(role, sprint, master, task, task or master or sprint)  # type: ignore[arg-type]


class ToolServerDefinitionTests(unittest.TestCase):
    def test_the_definition_starts_this_builds_tool_server_with_its_source_and_settings(
        self,
    ) -> None:
        settings = Path("/settings/agents-remember-settings.json")
        bound = binding(sprint_ref=SPRINT, master_ref=MASTER, task_ref=LEAF)
        package = paseo_launch.launching_source_root()
        self.assertEqual(package, SOURCE.parent)
        placements = {
            # A checkout beside its environment: the definition names the source tree.
            "a source tree outside the interpreter": (
                Path("/elsewhere/.venv"),
                {"PYTHONPATH": package.parent.as_posix()},
            ),
            # An installed package is the interpreter's own; nothing has to be named.
            "a package inside the interpreter": (package.parent, {}),
        }
        for label, (prefix, source) in placements.items():
            with self.subTest(label), patch.object(sys, "prefix", prefix.as_posix()):
                self.assertEqual(
                    tool_server_definition(settings, bound),
                    {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": ["-m", "agents_remember.mcp", "--config", settings.as_posix()],
                        "env": {
                            **source,
                            "AR_PASEO_AGENT_ID": bound.agent_id,
                            "AR_ROLE": "worker",
                            "AR_REQUEST_ID": bound.request_id,
                            "AR_REPORT_PATH": bound.report_path,
                            "AR_SPRINT_REF": '{"repository":"agents-remember","path":"sprint/task.json"}',
                            "AR_MASTER_REF": '{"repository":"agents-remember","path":"master/task.json"}',
                            "AR_TASK_REF": '{"repository":"agents-remember","path":"master/01_leaf.json"}',
                        },
                    },
                )
        self.assertEqual(TOOL_SERVER_NAME, "agents-remember-task")
        with patch.object(sys, "executable", ""), self.assertRaisesRegex(ValueError, "interpreter"):
            tool_server_definition(settings, bound)

    def test_the_tool_server_reads_the_binding_it_was_started_with_and_reports_it(self) -> None:
        selections: dict[str, dict[str, TaskDocumentRef]] = {
            "taskless": {},
            "sprint-bound": {"sprint_ref": SPRINT},
            "master-bound": {"sprint_ref": SPRINT, "master_ref": MASTER},
            "leaf-bound": {"sprint_ref": SPRINT, "master_ref": MASTER, "task_ref": LEAF},
        }
        for label, references in selections.items():
            with self.subTest(label):
                bound = binding(**references)
                environment = bound.environment()
                # A reference the selection does not have is not in the environment at all.
                self.assertEqual(len(environment), 4 + len(references))
                self.assertEqual(read_agent_binding(environment), bound)
        self.assertIsNone(read_agent_binding({"AR_ROLE": "worker"}))
        complete = binding(task_ref=LEAF).environment()
        for variable, damaged in (
            ("AR_REQUEST_ID", {**complete, "AR_REQUEST_ID": ""}),
            ("AR_TASK_REF", {**complete, "AR_TASK_REF": '{"repository":"agents-remember"}'}),
            ("AR_TASK_REF", {**complete, "AR_TASK_REF": "master/01_leaf.json"}),
        ):
            with (
                self.subTest("a damaged binding is refused", variable=variable),
                self.assertRaisesRegex(ValueError, variable),
            ):
                read_agent_binding(damaged)

        config = McpRuntimeConfig(
            config_path=Path("/settings/agents-remember-settings.json"),
            coordination_root=Path("/coordination"),
            workspace_root=Path("/projects"),
            transcript_root=Path("/coordination/logs/mcp"),
        )
        build = ServingBuildPayload(version="0", bootedAt="2026-10-02T00:00:00Z")
        bound = binding(sprint_ref=SPRINT, master_ref=MASTER, task_ref=LEAF)
        with patch.dict("os.environ", bound.environment()):
            reported = server_info_payload(config, build)["agentBinding"]
        self.assertEqual(
            reported,
            {
                "agentId": bound.agent_id,
                "role": "worker",
                "requestId": bound.request_id,
                "reportPath": bound.report_path,
                "sprintDocumentRef": {"repository": "agents-remember", "path": "sprint/task.json"},
                "masterDocumentRef": {"repository": "agents-remember", "path": "master/task.json"},
                "taskDocumentRef": {"repository": "agents-remember", "path": "master/01_leaf.json"},
            },
        )
        with patch.dict("os.environ", clear=False) as environment:
            environment.pop("AR_PASEO_AGENT_ID", None)
            self.assertNotIn("agentBinding", server_info_payload(config, build))


class RecoveryNoteTests(unittest.TestCase):
    def test_the_note_names_role_references_and_artifact_within_600_characters(self) -> None:
        artifact = {
            "path": "/group/task-reports/orca-native/01_LEAF-worker.handover.txt",
            "sha256": SHA,
        }
        self.assertEqual(
            recovery_note(context("worker", SPRINT, MASTER, LEAF), artifact),
            "AR role agent: worker. Sprint agents-remember/sprint/task.json. Master "
            "agents-remember/master/task.json. Task agents-remember/master/01_leaf.json. "
            f"Assignment file: {artifact['path']} (SHA-256 {SHA}). Reload that file whenever "
            "your assignment is not in your context.",
        )
        self.assertEqual(
            recovery_note(context("architect"), artifact),
            f"AR role agent: architect. No task reference. Assignment file: {artifact['path']} "
            f"(SHA-256 {SHA}). Reload that file whenever your assignment is not in your context.",
        )
        # The longest role name, three long task references and a long artifact path.
        long_references = [
            TaskDocumentRef(
                repository="agents-remember",
                path=f"261001_paseo-native-host-trial-with-a-long-folder-name/{name}-{'x' * 90}.json",
            )
            for name in ("sprint", "master", "leaf")
        ]
        long_artifact = {
            "path": "/home/developer/coordination/worktrees/agents-remember/"
            + "a-long-enclosure-group-folder-name-" * 3
            + "/task-reports/orca-native/261001-PNT-L5-system-specialist-"
            + f"{uuid.UUID(int=0)}.handover.txt",
            "sha256": SHA,
        }
        note = recovery_note(context("system-specialist", *long_references), long_artifact)
        self.assertGreater(sum(len(reference.key) for reference in long_references), 450)
        self.assertLessEqual(len(note), RECOVERY_NOTE_LIMIT)
        self.assertEqual(RECOVERY_NOTE_LIMIT, 600)
        # The artifact's path and digest are never shortened; the references keep both ends.
        self.assertIn(f"Assignment file: {long_artifact['path']} (SHA-256 {SHA}).", note)
        shown = re.fullmatch(
            r"AR role agent: system-specialist\. Sprint (\S+)\. Master (\S+)\. Task (\S+)\. "
            r"Assignment file: .*",
            note,
        )
        assert shown is not None
        for reference, text in zip(long_references, shown.groups(), strict=True):
            head, tail = text.split("…")
            self.assertLess(len(text), len(reference.key))
            self.assertTrue(reference.key.startswith(head) and reference.key.endswith(tail))
            self.assertGreater(len(tail), len(head))
        with self.assertRaisesRegex(ValueError, "within 600 characters"):
            recovery_note(
                context("worker", SPRINT, MASTER, LEAF), {"path": "/r/" + "p" * 600, "sha256": SHA}
            )


class HandoverArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_the_artifact_is_written_once_beside_the_report_and_named_by_one_line(self) -> None:
        reports = self.root / "task" / "notes" / "reports"
        reports.mkdir(parents=True)
        # A leaf agent reaches its report folder through a link inside its workspace.
        access = self.root / "group" / "task-reports"
        access.parent.mkdir()
        access.symlink_to(reports, target_is_directory=True)
        report = (access / "orca-native" / "01_LEAF-worker-request.md").as_posix()
        content = "Compiled capsule · 役割\n\nAR owner assignment and canonical task handover:\n{}"
        body = content.encode("utf-8")

        reference = write_handover_artifact(report, content)

        stored = reports / "orca-native" / "01_LEAF-worker-request.handover.txt"
        self.assertEqual(
            reference,
            {
                "path": (access / "orca-native" / "01_LEAF-worker-request.handover.txt").as_posix(),
                "canonicalPath": stored.as_posix(),
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
            },
        )
        self.assertEqual(stored.read_bytes(), body)
        written = stored.stat()
        line = (
            f"AR handover artifact: {reference['path']} (SHA-256 {reference['sha256']}). It "
            "holds everything below this line; read that file again whenever your assignment is "
            "no longer in your context."
        )
        self.assertEqual(artifact_line(reference), line)
        self.assertNotIn("\n", line)
        message = first_message(reference, content)
        self.assertEqual(message, f"{line}\n{content}")

        with self.subTest("the same request reuses it"):
            self.assertEqual(write_handover_artifact(report, content), reference)
            restore_handover_artifact(reference, message)
            self.assertEqual(
                (stored.stat().st_ino, stored.stat().st_mtime_ns),
                (written.st_ino, written.st_mtime_ns),
            )
        with self.subTest("different content is refused and nothing changes"):
            with self.assertRaisesRegex(ValueError, "different handover content"):
                write_handover_artifact(report, content + " changed")
            self.assertEqual(stored.read_bytes(), body)
            self.assertEqual([entry.name for entry in stored.parent.iterdir()], [stored.name])
        with self.subTest("a saved message that is not the artifact's is refused"):
            for other in (content, f"{line}\n{content} changed", line):
                with self.assertRaisesRegex(ValueError, "does not match its handover artifact"):
                    restore_handover_artifact(reference, other)
        with self.subTest("a lost artifact is written again from the saved message"):
            stored.unlink()
            restore_handover_artifact(reference, message)
            self.assertEqual(stored.read_bytes(), body)
        with self.subTest("something else at the artifact's path is refused"):
            stored.unlink()
            stored.mkdir()
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                write_handover_artifact(report, content)
        with self.subTest("bounds"):
            large = (self.root / "large.md").as_posix()
            self.assertEqual(write_handover_artifact(large, "x" * 300_000)["bytes"], 300_000)
            with self.assertRaisesRegex(ValueError, "size limit"):
                write_handover_artifact(
                    (self.root / "larger.md").as_posix(), "x" * (MAX_HANDOVER_ARTIFACT_BYTES + 1)
                )
            self.assertFalse((self.root / "larger.handover.txt").exists())


class NoHarnessNameTests(unittest.TestCase):
    def test_the_launch_code_names_no_harness(self) -> None:
        for path in LAUNCH_CODE:
            with self.subTest(path.name):
                text = path.read_text(encoding="utf-8")
                found = sorted({match.group(0) for match in HARNESS_NAME.finditer(text)})
                self.assertEqual(found, [], f"{path.name} names a harness: {found}")
        # The scan sees what it is meant to catch, and passes an identifier that merely contains
        # a harness's name.
        caught: dict[str, list[Any]] = {
            'if provider == "eve":': ["eve"],
            "HARNESSES = ('Codex', 'pi')": ["Codex", "pi"],
            "capsule.codex_delivery.trusted_instructions": [],
            "an api key and several steps": [],
        }
        for text, names in caught.items():
            with self.subTest(text):
                self.assertEqual(HARNESS_NAME.findall(text), names)


if __name__ == "__main__":
    unittest.main()
