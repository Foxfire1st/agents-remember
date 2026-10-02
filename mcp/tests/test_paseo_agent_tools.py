"""What a launch gives an agent beside its first message: tool server, binding, note, artifact."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.application.agent_binding import (
    TOOL_SERVER_NAME,
    AgentBinding,
    read_agent_binding,
)
from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.cli import (
    paseo_launch,
    role_handover_artifacts,
    role_launch_preparation,
    role_launch_receipts,
)
from agents_remember.cli.paseo_launch import (
    RECOVERY_NOTE_LIMIT,
    recovery_note,
    tool_server_definition,
)
from agents_remember.cli.role_handover_artifacts import (
    MAX_HANDOVER_ARTIFACT_BYTES,
    artifact_line,
    first_message,
    restore_handover_artifact,
    write_handover_artifact,
)
from agents_remember.cli.role_launch_preparation import RoleHandoverRequest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.core import server_info_payload
from agents_remember.models.core import ServingBuildPayload
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from test_paseo_launch import PaseoLaunchTestCase

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "agents_remember"
SPRINT = TaskDocumentRef(repository="agents-remember", path="sprint/task.json")
MASTER = TaskDocumentRef(repository="agents-remember", path="master/task.json")
LEAF = TaskDocumentRef(repository="agents-remember", path="master/01_leaf.json")
SHA = "a" * 64
# The references each role class carries.
ROLE_REFERENCES: dict[str, tuple[TaskDocumentRef, ...]] = {
    "architect": (),
    "system-specialist": (),
    "orchestrator": (SPRINT,),
    "manager": (SPRINT, MASTER),
    "worker": (SPRINT, MASTER, LEAF),
    "reviewer": (SPRINT, MASTER, LEAF),
    "curator": (SPRINT, MASTER, LEAF),
}
BINDING_VARIABLES = (
    "AR_PASEO_AGENT_ID",
    "AR_ROLE",
    "AR_REQUEST_ID",
    "AR_REPORT_PATH",
    "AR_SPRINT_REF",
    "AR_MASTER_REF",
    "AR_TASK_REF",
)
SEAT_VARIABLES = ("AR_SPAWN_ROLE", "AR_HOSTED_SESSION_ID")


def binding(role: str = "worker", *references: TaskDocumentRef) -> AgentBinding:
    sprint, master, task = (*references, None, None, None)[:3]
    return AgentBinding(
        agent_id="f3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607",
        role=role,
        request_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        report_path="/group/task-reports/role-launch/01_LEAF-worker.md",
        sprint_ref=sprint,
        master_ref=master,
        task_ref=task,
    )


def context(role: str, *references: TaskDocumentRef) -> RoleLaunchContext:
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
    return RoleLaunchContext(role, sprint, master, task, task or master or sprint)  # type: ignore[arg-type]


def reference_json(reference: TaskDocumentRef) -> str:
    return f'{{"repository":"{reference.repository}","path":"{reference.path}"}}'


class ToolServerDefinitionTests(unittest.TestCase):
    def test_the_definition_starts_this_builds_tool_server_with_its_source_and_settings(
        self,
    ) -> None:
        settings = Path("/settings/agents-remember-settings.json")
        bound = binding("worker", SPRINT, MASTER, LEAF)
        package = paseo_launch.launching_source_root()
        self.assertEqual(package, PACKAGE)
        placements = {
            # A checkout beside its environment: the definition names the source tree.
            "a source tree outside the interpreter": (
                Path("/elsewhere/.venv"),
                package.parent.as_posix(),
            ),
            # An installed package is the interpreter's own: the variable is set and empty, so
            # that no inherited value names another source tree.
            "a package inside the interpreter": (package.parent, ""),
        }
        # The four variables that say where a process of this build may write travel with the
        # definition when the launching process has them; none is given a value of its own.
        surroundings: dict[str, dict[str, str]] = {
            "the launching process has them": {
                "GIT_OPTIONAL_LOCKS": "0",
                "PYTHONPYCACHEPREFIX": "/cache/pycache",
                "TMUX_TMPDIR": "/run/tmux",
                "AR_DAGGER_AUTHORITY_ROOT": "/state/dagger-authority",
                "PATH": "/usr/bin",
            },
            "it has some of them": {
                "GIT_OPTIONAL_LOCKS": "0",
                "PYTHONPYCACHEPREFIX": "",
                "AR_DAGGER_AUTHORITY_ROOT": "/state/dagger-authority",
            },
            "it has none": {},
        }
        for label, (prefix, source) in placements.items():
            for surrounding, process in surroundings.items():
                with (
                    self.subTest(label, surrounding=surrounding),
                    patch.object(sys, "prefix", prefix.as_posix()),
                    patch.dict(os.environ, process, clear=True),
                ):
                    kept = {
                        name: value for name, value in process.items() if name != "PATH" and value
                    }
                    self.assertEqual(
                        tool_server_definition(settings, bound),
                        {
                            "type": "stdio",
                            "command": sys.executable,
                            # -P: the server's working directory is not a place to load from.
                            "args": [
                                "-P",
                                "-m",
                                "agents_remember.mcp",
                                "--config",
                                settings.as_posix(),
                            ],
                            "env": {
                                "PYTHONPATH": source,
                                **kept,
                                "AR_SPAWN_ROLE": "",
                                "AR_HOSTED_SESSION_ID": "",
                                "AR_PASEO_AGENT_ID": bound.agent_id,
                                "AR_ROLE": "worker",
                                "AR_REQUEST_ID": bound.request_id,
                                "AR_REPORT_PATH": bound.report_path,
                                "AR_SPRINT_REF": reference_json(SPRINT),
                                "AR_MASTER_REF": reference_json(MASTER),
                                "AR_TASK_REF": reference_json(LEAF),
                            },
                        },
                    )
        self.assertEqual(TOOL_SERVER_NAME, "agents-remember-task")
        with patch.object(sys, "executable", ""), self.assertRaisesRegex(ValueError, "interpreter"):
            tool_server_definition(settings, bound)

    def test_the_tool_server_reads_the_binding_it_was_started_with_and_reports_it(self) -> None:
        for role, references in ROLE_REFERENCES.items():
            with self.subTest(role):
                bound = binding(role, *references)
                environment = bound.environment()
                # Every variable is always there; a reference the role does not have is empty.
                self.assertEqual(sorted(environment), sorted((*BINDING_VARIABLES, *SEAT_VARIABLES)))
                self.assertEqual(
                    [environment[name] for name in BINDING_VARIABLES[4:]],
                    [*map(reference_json, references), "", "", ""][:3],
                )
                self.assertEqual([environment[name] for name in SEAT_VARIABLES], ["", ""])
                self.assertEqual(read_agent_binding(environment), bound)
        self.assertIsNone(read_agent_binding({"AR_ROLE": "worker"}))

        complete = binding("worker", SPRINT, MASTER, LEAF).environment()
        absent = "The agent binding of this tool server has no "
        damaged: dict[str, tuple[dict[str, str], str]] = {
            "no role": ({"AR_ROLE": ""}, absent + "AR_ROLE."),
            "no report path": ({"AR_REPORT_PATH": ""}, absent + "AR_REPORT_PATH."),
            "no request id": ({"AR_REQUEST_ID": ""}, absent + "AR_REQUEST_ID."),
            "a role that is no AR role": ({"AR_ROLE": "root"}, "names no AR role in AR_ROLE"),
            "an agent id that was not minted": (
                {"AR_PASEO_AGENT_ID": "agent-7"},
                "no minted id in AR_PASEO_AGENT_ID",
            ),
            "a request id in another spelling": (
                {"AR_REQUEST_ID": complete["AR_REQUEST_ID"].upper()},
                "no minted id in AR_REQUEST_ID",
            ),
            "a relative report path": (
                {"AR_REPORT_PATH": "task-reports/report.md"},
                "no absolute path in AR_REPORT_PATH",
            ),
            "a reference without its path": (
                {"AR_TASK_REF": '{"repository":"agents-remember"}'},
                "no readable AR_TASK_REF",
            ),
            "a reference that is not JSON": (
                {"AR_TASK_REF": "master/01_leaf.json"},
                "no readable AR_TASK_REF",
            ),
            "a leaf role without its references": (
                {"AR_SPRINT_REF": "", "AR_MASTER_REF": "", "AR_TASK_REF": ""},
                "the task references of a worker",
            ),
            "a leaf role without its master": (
                {"AR_MASTER_REF": ""},
                "the task references of a worker",
            ),
            "a taskless role with a leaf": (
                {"AR_ROLE": "architect", "AR_SPRINT_REF": "", "AR_MASTER_REF": ""},
                "the task references of a architect",
            ),
            "a sprint-bound role with a master": (
                {"AR_ROLE": "orchestrator", "AR_TASK_REF": ""},
                "the task references of a orchestrator",
            ),
            "a master-bound role without its master": (
                {"AR_ROLE": "manager", "AR_MASTER_REF": "", "AR_TASK_REF": ""},
                "the task references of a manager",
            ),
        }
        for label, (change, reason) in damaged.items():
            with (
                self.subTest("a binding no launch makes is refused", case=label),
                self.assertRaisesRegex(ValueError, re.escape(reason)),
            ):
                read_agent_binding({**complete, **change})

        with self.subTest("what a harness passes on does not reach the binding"):
            # A harness that starts the tool server with its own environment underneath.
            inherited = {
                "PATH": "/usr/bin",
                "PYTHONPATH": "/another/ar/checkout/mcp/src",
                "AR_SPAWN_ROLE": "worker",
                "AR_HOSTED_SESSION_ID": "seat-7",
                "AR_MASTER_REF": reference_json(MASTER),
                "AR_TASK_REF": reference_json(LEAF),
            }
            bound = binding("orchestrator", SPRINT)
            definition = tool_server_definition(Path("/settings/ar.json"), bound)
            started = {**inherited, **definition["env"]}
            self.assertEqual(read_agent_binding(started), bound)
            self.assertEqual([started[name] for name in SEAT_VARIABLES], ["", ""])
            self.assertNotEqual(started["PYTHONPATH"], inherited["PYTHONPATH"])
            with patch.object(sys, "prefix", PACKAGE.parent.as_posix()):
                installed = tool_server_definition(Path("/settings/ar.json"), bound)
            self.assertEqual({**inherited, **installed["env"]}["PYTHONPATH"], "")
            # Had the launch set only what the selection has, the stray references would be read.
            partial = {name: value for name, value in definition["env"].items() if value}
            with self.assertRaisesRegex(ValueError, "the task references of a orchestrator"):
                read_agent_binding({**inherited, **partial})

        config = McpRuntimeConfig(
            config_path=Path("/settings/agents-remember-settings.json"),
            coordination_root=Path("/coordination"),
            workspace_root=Path("/projects"),
            transcript_root=Path("/coordination/logs/mcp"),
        )
        build = ServingBuildPayload(version="0", bootedAt="2026-10-02T00:00:00Z")
        bound = binding("worker", SPRINT, MASTER, LEAF)
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


def artifact_with_path_of(length: int) -> dict[str, str]:
    """An artifact reference whose path has exactly ``length`` characters."""

    stem = "/group/task-reports/role-launch/"
    return {"path": stem + "p" * (length - len(stem) - 13) + ".handover.txt", "sha256": SHA}


class RecoveryNoteTests(unittest.TestCase):
    def test_the_note_names_role_references_and_artifact_within_600_characters(self) -> None:
        artifact = {
            "path": "/group/task-reports/role-launch/01_LEAF-worker.handover.txt",
            "sha256": SHA,
        }
        tail = (
            f"Assignment file: {artifact['path']} (SHA-256 {SHA}). Reload that file whenever "
            "your assignment is not in your context."
        )
        self.assertEqual(
            recovery_note(context("worker", SPRINT, MASTER, LEAF), artifact),
            "AR role agent: worker. Sprint agents-remember/sprint/task.json. Master "
            "agents-remember/master/task.json. Task agents-remember/master/01_leaf.json. " + tail,
        )
        self.assertEqual(
            recovery_note(context("architect"), artifact),
            "AR role agent: architect. No task reference. " + tail,
        )
        self.assertEqual(RECOVERY_NOTE_LIMIT, 600)

    def test_a_note_that_would_not_fit_shortens_the_less_specific_references_first(self) -> None:
        # The longest note of the live task tree: references of 56, 64 and 123 characters and an
        # artifact path of 218, for which the unshortened note has 670.
        references = [
            TaskDocumentRef(repository="agents-remember", path=path)
            for path in (
                "260713_improved-agentic-system/task.json",
                "260921_complete-code-and-intent-review/task.json",
                "260921_complete-code-and-intent-review/"
                "28_curated-foundation-from-code-external-sources-and-onboarding.json",
            )
        ]
        path = (
            "/home/firefox/projects/ar-coordination/worktrees/agents-remember/"
            "28_curated-foundation-from-code-external-60ff47641f-ar/task-reports/role-launch/"
            "260921-ICR-L28-reviewer-00000000-0000-0000-0000-000000000000.handover.txt"
        )
        sizes = ([len(reference.key) for reference in references], len(path))
        self.assertEqual(sizes, ([56, 64, 123], 218))
        reviewer = context("reviewer", *references)
        leaf = references[2].key
        note = recovery_note(reviewer, {"path": path, "sha256": SHA})
        # Path, digest and the leaf reference are whole. Sprint and master end at their task
        # folder, whose document they name, and keep its leading id and the start of its slug.
        self.assertEqual(
            note,
            "AR role agent: reviewer. Sprint agents-remember/260713_i…. Master "
            f"agents-remember/260921_c…. Task {leaf}. Assignment file: {path} (SHA-256 {SHA}). "
            "Reload that file whenever your assignment is not in your context.",
        )
        self.assertEqual(len(note), 600)

        whole = len(recovery_note(reviewer, artifact_with_path_of(100))) - 100
        # Characters over the limit -> what the note then reads for sprint and master.
        steps = {
            # The document name of the sprint goes first, as a whole.
            1: "Sprint agents-remember/260713_improved-agentic-system. Master "
            "agents-remember/260921_complete-code-and-intent-review/task.json.",
            # The master's name stays while it fits, to the character: ten are needed for it.
            10: "Sprint agents-remember/260713_improved-agentic-system. Master "
            "agents-remember/260921_complete-code-and-intent-review/task.json.",
            # Then the master's goes; both folders are still whole.
            11: "Sprint agents-remember/260713_improved-agentic-system. Master "
            "agents-remember/260921_complete-code-and-intent-review.",
            20: "Sprint agents-remember/260713_improved-agentic-system. Master "
            "agents-remember/260921_complete-code-and-intent-review.",
            # Only then are the folders' slugs cut, the longer one first.
            21: "Sprint agents-remember/260713_improved-agentic-system. Master "
            "agents-remember/260921_complete-code-and-intent-revi….",
            40: "Sprint agents-remember/260713_improved-agentic…. Master "
            "agents-remember/260921_complete-code-an….",
            # The shortest form of both: repository and the folder's leading id.
            74: "Sprint agents-remember/260713…. Master agents-remember/260921….",
        }
        for over, shown in steps.items():
            with self.subTest("the less specific references give way first", over=over):
                note = recovery_note(reviewer, artifact_with_path_of(600 - whole + over))
                self.assertIn(f". {shown} Task {leaf}. Assignment file: ", note)
                self.assertLessEqual(len(note), 600)
        with self.subTest("the leaf is cut only when the others are at their shortest"):
            note = recovery_note(reviewer, artifact_with_path_of(600 - whole + 76))
            self.assertIn(
                ". Sprint agents-remember/260713…. Master agents-remember/260921…. Task "
                f"{leaf[:-3]}…. Assignment file: ",
                note,
            )
            self.assertEqual(len(note), 600)
        with self.subTest("room a short reference does not use goes to the others"):
            longer = TaskDocumentRef(
                repository="agents-remember",
                path="260921_complete-code-and-intent-review-round-2/task.json",
            )
            mixed = context("reviewer", SPRINT, longer, references[2])
            whole = len(recovery_note(mixed, artifact_with_path_of(100))) - 100
            note = recovery_note(mixed, artifact_with_path_of(600 - whole + 24))
            self.assertIn(
                ". Sprint agents-remember/sprint. Master "
                f"agents-remember/260921_complete-code-and-intent-review-ro…. Task {leaf}. ",
                note,
            )
            self.assertEqual(len(note), 600)
        with self.subTest("a part without a slug is whole or at its shortest, never cut inside"):
            plain = context(
                "worker",
                *(
                    TaskDocumentRef(repository="sandbox-app", path=path)
                    for path in (
                        "sbx-sprint/task.json",
                        "sbx-text-helpers/task.json",
                        "sbx-text-helpers/01_slugify.json",
                    )
                ),
            )
            whole = len(recovery_note(plain, artifact_with_path_of(100))) - 100
            forms = {
                11: "Sprint sandbox-app/sbx-sprint. Master sandbox-app/sbx-text-helpers. "
                "Task sandbox-app/sbx-text-helpers/01_slugify.json.",
                21: "Sprint sandbox-app/sbx-sprint. Master sandbox-app/sbx-text-hel…. "
                "Task sandbox-app/sbx-text-helpers/01_slugify.json.",
                24: "Sprint sandbox-app/sbx-sprint. Master sandbox-app/sbx-text-hel…. "
                "Task sandbox-app/sbx-text-hel…/01_slugify.json.",
            }
            for over, shown in forms.items():
                note = recovery_note(plain, artifact_with_path_of(600 - whole + over))
                self.assertIn(f". {shown} Assignment file: ", note)
        with self.subTest("a single reference is the most specific one and keeps its document"):
            long_sprint = TaskDocumentRef(
                repository="agents-remember", path=f"260713_{'improved-' * 30}system/task.json"
            )
            note = recovery_note(
                context("orchestrator", long_sprint), {"path": path, "sha256": SHA}
            )
            self.assertRegex(
                note, r"\. Sprint agents-remember/260713_(improved-)+i[a-z]*…/task…\. Assignment"
            )
            self.assertEqual(len(note), 600)

    def test_a_document_name_is_given_only_with_every_other_part_whole(self) -> None:
        # A folder without a slug is whole or at its leading id; the document of a reference
        # that may end at its folder is named only when that folder is whole.
        reference = "sandbox-app/mdlon-cqjkn-bjwsvcod-stawizwh/task.json"
        at_folder = "sandbox-app/mdlon-cqjkn-bjwsvcod-stawizwh"
        shortest = "sandbox-app/mdlon-cqjkn-…"
        forms = {
            len(reference): reference,
            len(reference) - 1: at_folder,
            len(at_folder): at_folder,
            # Room for the document's name beside the cut folder, but not for the folder.
            len(at_folder) - 1: shortest,
            len(shortest) + len("/task.json"): shortest,
            len(shortest): shortest,
            0: shortest,
        }
        for width, shown in forms.items():
            with self.subTest(width=width):
                self.assertEqual(
                    paseo_launch._shortened(reference, width, ends_at_folder=True), shown
                )
        # The most specific reference always names its document, at the least by its start.
        self.assertEqual(
            paseo_launch._shortened(reference, 0, ends_at_folder=False), f"{shortest}/task…"
        )

    def test_the_limit_and_the_shortest_form_are_exact(self) -> None:
        taskless = context("architect")
        full = context("worker", SPRINT, MASTER, LEAF)
        whole = len(recovery_note(taskless, artifact_with_path_of(100))) - 100
        with self.subTest("a taskless note of 600 characters is sent, one of 601 is refused"):
            self.assertEqual(len(recovery_note(taskless, artifact_with_path_of(600 - whole))), 600)
            with self.assertRaisesRegex(ValueError, "within 600 characters"):
                recovery_note(taskless, artifact_with_path_of(601 - whole))
        whole = len(recovery_note(full, artifact_with_path_of(100))) - 100
        with self.subTest("references are whole at 600 and shortened from 601"):
            at_limit = recovery_note(full, artifact_with_path_of(600 - whole))
            self.assertEqual((len(at_limit), "…" in at_limit), (600, False))
            self.assertIn(" Sprint agents-remember/sprint/task.json. ", at_limit)
            # A part without a slug is never cut inside: the sprint's document name goes whole.
            over = recovery_note(full, artifact_with_path_of(601 - whole))
            self.assertIn(
                " Sprint agents-remember/sprint. Master agents-remember/master/task.json. "
                "Task agents-remember/master/01_leaf.json. ",
                over,
            )
            self.assertEqual(len(over), 591)
        with self.subTest("the shortest form keeps repository and leading ids; beyond it, refusal"):
            # 'sprint' and 'master' have no slug to be cut; the leaf keeps its number.
            shortest = (
                " Sprint agents-remember/sprint. Master agents-remember/master. "
                "Task agents-remember/master/01…. "
            )
            saved = sum(len(reference.key) for reference in (SPRINT, MASTER, LEAF)) - 70
            self.assertEqual(saved, 29)
            note = recovery_note(full, artifact_with_path_of(600 - whole + saved))
            self.assertIn(shortest, note)
            self.assertEqual(len(note), 600)
            with self.assertRaisesRegex(ValueError, "within 600 characters"):
                recovery_note(full, artifact_with_path_of(601 - whole + saved))


class HandoverArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def linked_report_folder(self) -> tuple[Path, Path]:
        """A task's report folder, and the link through which a leaf agent reaches it."""

        reports = self.root / "task" / "notes" / "reports"
        reports.mkdir(parents=True)
        access = self.root / "group" / "task-reports"
        access.parent.mkdir()
        access.symlink_to(reports, target_is_directory=True)
        return reports, access

    def test_the_artifact_is_written_once_beside_the_report_and_named_by_one_line(self) -> None:
        reports, access = self.linked_report_folder()
        report = (access / "role-launch" / "01_LEAF-worker-request.md").as_posix()
        content = "Compiled capsule · 役割\n\nAR owner assignment and canonical task handover:\n{}"
        body = content.encode("utf-8")

        reference = write_handover_artifact(report, content)

        stored = reports / "role-launch" / "01_LEAF-worker-request.handover.txt"
        self.assertEqual(
            reference,
            {
                "path": (access / "role-launch" / "01_LEAF-worker-request.handover.txt").as_posix(),
                "canonicalPath": stored.as_posix(),
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
            },
        )
        self.assertEqual(stored.read_bytes(), body)
        written = stored.stat()
        # Nobody can write the file once it exists.
        self.assertEqual(stat.S_IMODE(written.st_mode), 0o444)
        line = (
            f"AR handover artifact: {reference['path']} (SHA-256 {reference['sha256']}). It "
            "holds everything below this line; read that file again whenever your assignment is "
            "no longer in your context."
        )
        self.assertEqual(artifact_line(reference), line)
        self.assertNotIn("\n", line)
        message = first_message(reference, content)
        self.assertEqual(message, f"{line}\n{content}")

        receipt = self.root / "worker-receipt.json"
        with self.subTest("the same request reuses it"):
            self.assertEqual(write_handover_artifact(report, content), reference)
            restore_handover_artifact(reference, message, receipt=receipt)
            self.assertEqual(
                (stored.stat().st_ino, stored.stat().st_mtime_ns),
                (written.st_ino, written.st_mtime_ns),
            )
        with self.subTest("different content is refused and nothing changes"):
            # The refusal names the file and says what to do.
            refusal = (
                f"already has different handover content in {re.escape(stored.as_posix())}\\. "
                "That file is never changed; start the role again under a new request id\\."
            )
            with self.assertRaisesRegex(ValueError, refusal):
                write_handover_artifact(report, content + " changed")
            self.assertEqual(stored.read_bytes(), body)
            self.assertEqual([entry.name for entry in stored.parent.iterdir()], [stored.name])
        with self.subTest("a saved message that is not the artifact's is refused"):
            mismatches = {
                "no artifact line": content,
                "another content": f"{line}\n{content} changed",
                "the line alone": line,
                # The content alone would pass the digest; the line has to be the artifact's.
                "another first line": f"AR handover artifact: elsewhere.\n{content}",
            }
            for label, saved in mismatches.items():
                with (
                    self.subTest(label),
                    self.assertRaisesRegex(ValueError, "does not match its handover artifact"),
                ):
                    restore_handover_artifact(reference, saved, receipt=receipt)
        with self.subTest("a path that no longer leads to the recorded file is named as such"):
            # A reference whose recorded file is not where its own path leads: nothing is
            # written there, and the refusal names both places.
            elsewhere = self.root / "elsewhere.handover.txt"
            refusal = (
                f"The handover artifact of this request is recorded at {elsewhere}, but the path "
                f"the agent was given, {reference['path']}, now leads to {stored}. Put back what "
                "that path ran through (for a leaf role, the report-access link of its "
                "enclosure) and retry."
            )
            with self.assertRaisesRegex(ValueError, re.escape(refusal)):
                restore_handover_artifact(
                    {**reference, "canonicalPath": elsewhere.as_posix()}, message, receipt=receipt
                )
            self.assertFalse(elsewhere.exists())
        with self.subTest("a retry over a changed artifact says how the retry gets through"):
            stored.unlink()
            stored.write_text("another assignment", encoding="utf-8")
            refusal = (
                f"The handover artifact {stored} no longer holds the first message saved for "
                "this request. Delete that file and retry; the retry writes it again from the "
                "saved message."
            )
            with self.assertRaisesRegex(ValueError, re.escape(refusal)):
                restore_handover_artifact(reference, message, receipt=receipt)
            self.assertEqual(stored.read_text(encoding="utf-8"), "another assignment")
        with self.subTest("a lost artifact is written again from the saved message"):
            stored.unlink()
            restore_handover_artifact(reference, message, receipt=receipt)
            self.assertEqual(stored.read_bytes(), body)

    def test_a_retry_names_a_damaged_reference_and_a_path_that_leads_to_itself(self) -> None:
        _reports, access = self.linked_report_folder()
        report = (access / "role-launch" / "01_LEAF-worker-request.md").as_posix()
        content = "Compiled capsule and handover."
        reference = write_handover_artifact(report, content)
        message = first_message(reference, content)
        stored = Path(reference["canonicalPath"])
        body = stored.read_bytes()
        receipt = self.root / "worker-receipt.json"
        elsewhere = self.root / "elsewhere.handover.txt"
        with self.subTest("a reference that lacks a part is refused by the receipt's name"):
            damaged: dict[str, tuple[dict[str, Any], str]] = {
                "no path": ({k: v for k, v in reference.items() if k != "path"}, "path"),
                "no digest": ({k: v for k, v in reference.items() if k != "sha256"}, "sha256"),
                "no recorded place": (
                    {k: v for k, v in reference.items() if k != "canonicalPath"},
                    "canonicalPath",
                ),
                "a path that is no text": ({**reference, "path": None}, "path"),
                "nothing at all": ({}, "path, canonicalPath, sha256"),
            }
            for label, (broken, named) in damaged.items():
                refusal = (
                    f"The receipt {receipt} records the handover artifact of this request "
                    f"without its {named}, so the saved first message cannot be checked "
                    "against its file and nothing was sent. Put the reference back as the "
                    "launch wrote it and retry."
                )
                with self.subTest(label), self.assertRaises(ValueError) as raised:
                    restore_handover_artifact(broken, message, receipt=receipt)
                self.assertEqual(str(raised.exception), refusal)
            self.assertEqual(stored.read_bytes(), body)
        with self.subTest("a path that leads nowhere else is not said to lead to itself"):
            # No link stands between the given path and its file, so the path leads to itself.
            plain = write_handover_artifact((self.root / "plain" / "report.md").as_posix(), content)
            self.assertEqual(plain["path"], plain["canonicalPath"])
            refusal = (
                f"The handover artifact of this request is recorded at {elsewhere}, but the path "
                f"the agent was given, {plain['path']}, no longer leads to that file. Put back "
                "what that path ran through (for a leaf role, the report-access link of its "
                "enclosure) and retry."
            )
            with self.assertRaises(ValueError) as raised:
                restore_handover_artifact(
                    {**plain, "canonicalPath": elsewhere.as_posix()},
                    first_message(plain, content),
                    receipt=receipt,
                )
            self.assertEqual(str(raised.exception), refusal)

    def test_the_artifact_is_a_regular_file_of_bounded_size(self) -> None:
        reports, access = self.linked_report_folder()
        (reports / "role-launch").mkdir()
        content = "Compiled capsule and handover."
        with self.subTest("something else at the artifact's path is refused"):
            (reports / "role-launch" / "01_LEAF-worker-request.handover.txt").mkdir()
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                write_handover_artifact(
                    (access / "role-launch" / "01_LEAF-worker-request.md").as_posix(), content
                )
        with self.subTest("a link at the artifact's own name is not followed"):
            outside = self.root / "outside.txt"
            linked = (access / "role-launch" / "02_LEAF-worker-request.md").as_posix()
            (reports / "role-launch" / "02_LEAF-worker-request.handover.txt").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                write_handover_artifact(linked, content)
            self.assertFalse(outside.exists())
        with self.subTest("bounds"):
            large = (self.root / "large.md").as_posix()
            self.assertEqual(write_handover_artifact(large, "x" * 300_000)["bytes"], 300_000)
            with self.assertRaisesRegex(ValueError, "size limit"):
                write_handover_artifact(
                    (self.root / "larger.md").as_posix(), "x" * (MAX_HANDOVER_ARTIFACT_BYTES + 1)
                )
            self.assertFalse((self.root / "larger.handover.txt").exists())
            # The limit counts bytes: fewer characters than the limit, more bytes.
            wide = "é" * (MAX_HANDOVER_ARTIFACT_BYTES // 2 + 1)
            self.assertLess(len(wide), MAX_HANDOVER_ARTIFACT_BYTES)
            with self.assertRaisesRegex(ValueError, "size limit"):
                write_handover_artifact((self.root / "wide.md").as_posix(), wide)

    def test_of_simultaneous_writers_one_wins_and_different_content_is_refused(self) -> None:
        writers = 16
        real_mkstemp = tempfile.mkstemp

        def all_at_once(report: str, contents: list[str]) -> list[Any]:
            # Every writer has found no artifact before any of them puts one in place.
            together = threading.Barrier(writers)

            def mkstemp(*args: Any, **kwargs: Any) -> Any:
                together.wait(timeout=30)
                return real_mkstemp(*args, **kwargs)

            def write(content: str) -> Any:
                try:
                    return write_handover_artifact(report, content)
                except ValueError as error:
                    return error

            with (
                patch.object(role_handover_artifacts.tempfile, "mkstemp", mkstemp),
                ThreadPoolExecutor(max_workers=writers) as pool,
            ):
                return list(pool.map(write, contents))

        with self.subTest("different content: exactly one writer wins, the others are refused"):
            report = (self.root / "different" / "report.md").as_posix()
            contents = [f"compilation {number}" for number in range(writers)]
            outcomes = all_at_once(report, contents)
            won = [outcome for outcome in outcomes if isinstance(outcome, dict)]
            refused = [outcome for outcome in outcomes if isinstance(outcome, ValueError)]
            self.assertEqual((len(won), len(refused)), (1, writers - 1))
            self.assertTrue(all("different handover content" in str(error) for error in refused))
            stored = Path(won[0]["canonicalPath"])
            self.assertEqual(stored.read_text(encoding="utf-8"), contents[outcomes.index(won[0])])
            self.assertEqual([entry.name for entry in stored.parent.iterdir()], [stored.name])
        with self.subTest("the same content: every writer is told the same artifact"):
            report = (self.root / "same" / "report.md").as_posix()
            outcomes = all_at_once(report, ["one compilation"] * writers)
            self.assertEqual(outcomes, [outcomes[0]] * writers)
            self.assertIsInstance(outcomes[0], dict)
            self.assertEqual(os.stat(outcomes[0]["canonicalPath"]).st_nlink, 1)


class HandoverArtifactOnTheRouteTests(PaseoLaunchTestCase):
    def test_the_handover_artifact_is_written_once_and_a_repeat_reuses_or_refuses_it(self) -> None:
        with self.subTest("a retry sends the saved message and finds its artifact unchanged"):
            request = self.request("worker")
            self.runtime.fail("agent-create", "paseo_bridge_timeout")
            self.assertEqual(self.dispatch(request)[1]["status"], "unknown")
            artifact = Path(self.artifact(request)["path"])
            written = artifact.stat()
            self.assertEqual(self.dispatch(request)[1]["status"], "running")
            first, second = (call[1] for call in self.runtime.calls if call[0] == "agent-create")
            self.assertEqual(second, first)
            self.assertEqual(
                (artifact.stat().st_ino, artifact.stat().st_mtime_ns),
                (written.st_ino, written.st_mtime_ns),
            )
        with self.subTest("a retry writes a lost artifact again from the saved message"):
            request = self.request("manager")
            self.runtime.fail("agent-create", "paseo_daemon_unreachable")
            self.dispatch(request)
            artifact = Path(self.artifact(request)["path"])
            artifact.unlink()
            self.assertEqual(self.dispatch(request)[1]["status"], "running")
            self.assertEqual(artifact.read_text(encoding="utf-8"), self.prompt)
        for label, content, launches in (
            ("the same content is reused", self.prompt, True),
            ("different content is refused before a receipt", "an earlier compilation", False),
        ):
            with self.subTest("an artifact the request already has", case=label):
                # The process ended after the artifact was written and before the receipt was.
                request = self.request("architect")
                artifact = Path(self.artifact(request)["path"])
                artifact.write_text(content, encoding="utf-8")
                written = artifact.stat()
                self.runtime.calls.clear()
                if launches:
                    self.assertEqual(self.dispatch(request)[1]["status"], "running")
                else:
                    error = self.refused(request)
                    self.assertEqual(error.status_code, 409)
                    self.assertEqual(
                        str(error.detail),
                        f"This role request already has different handover content in {artifact}. "
                        "That file is never changed; start the role again under a new request id.",
                    )
                    self.assertEqual(self.runtime.launch_calls(), [])
                    self.assertFalse(self.receipt_path(request).exists())
                self.assertEqual(artifact.read_text(encoding="utf-8"), content)
                self.assertEqual(artifact.stat().st_ino, written.st_ino)

    def test_a_retry_over_a_changed_or_lost_artifact_says_and_does_what_gets_it_through(
        self,
    ) -> None:
        # A task-bound and a taskless role: the retry's check is the same for both.
        for role in ("orchestrator", "architect"):
            with self.subTest("a retry refuses an artifact whose content was changed", role=role):
                request = self.request(role)
                self.runtime.fail("agent-create", "paseo_daemon_unreachable")
                self.dispatch(request)
                artifact = Path(self.artifact(request)["path"])
                artifact.unlink()
                artifact.write_text("another assignment", encoding="utf-8")
                saved = self.receipt_path(request).read_bytes()
                calls = len(self.runtime.calls)
                error = self.refused(request)
                self.assertEqual(error.status_code, 409)
                # A new request id would be refused for a selection with an open execution;
                # the refusal names what lets this retry through.
                self.assertEqual(
                    str(error.detail),
                    f"The handover artifact {artifact} no longer holds the first message saved "
                    "for this request. Delete that file and retry; the retry writes it again "
                    "from the saved message.",
                )
                self.assertEqual(len(self.runtime.calls), calls)
                self.assertEqual(self.receipt_path(request).read_bytes(), saved)
                self.assertEqual(artifact.read_text(encoding="utf-8"), "another assignment")
                # And that is what lets it through.
                artifact.unlink()
                self.assertEqual(self.dispatch(request)[1]["status"], "running")
                self.assertEqual(artifact.read_text(encoding="utf-8"), self.prompt)
        with self.subTest("a taskless retry writes a lost artifact again"):
            request = self.request("system-specialist")
            self.runtime.fail("agent-create", "paseo_bridge_timeout")
            self.dispatch(request)
            artifact = Path(self.artifact(request)["path"])
            artifact.unlink()
            self.assertEqual(self.dispatch(request)[1]["status"], "running")
            self.assertEqual(artifact.read_text(encoding="utf-8"), self.prompt)

    def compile_for_the_enclosure(self) -> None:
        """Compile as the real compilation does for a leaf role: the report lies behind the link
        that the leaf's enclosure holds to its task's report folder."""

        def compiled(handover: RoleHandoverRequest) -> dict[str, Any]:
            access = Path(handover.workspace["taskReportAccessRoot"])
            report = access / "role-launch" / f"01_LEAF-worker-{handover.request_id}.md"
            return {
                **self.compile_handover(handover),
                "taskReportPath": report.as_posix(),
                "canonicalTaskReportPath": report.resolve().as_posix(),
            }

        self.replace(role_launch_preparation, "_compile_handover", compiled)

    def unresolved_leaf_launch(self) -> tuple[Any, dict[str, Any], Path, Path]:
        """A worker launch that stays unresolved: its request, receipt, link and stored artifact."""

        self.compile_for_the_enclosure()
        request = self.request("worker")
        self.runtime.fail("agent-create", "paseo_daemon_unreachable")
        self.assertEqual(self.dispatch(request)[1]["status"], "unknown")
        saved = self.receipt(request)
        link = self.enclosures.group / "task-reports"
        return request, saved, link, Path(saved["handoverArtifact"]["canonicalPath"])

    def refused_with_nothing_sent(self, request: Any) -> str:
        calls = len(self.runtime.calls)
        error = self.refused(request)
        self.assertEqual(error.status_code, 409)
        self.assertEqual(len(self.runtime.calls), calls)
        return str(error.detail)

    def test_a_leaf_retry_puts_a_missing_report_access_link_back(self) -> None:
        request, saved, link, stored = self.unresolved_leaf_launch()
        reference = saved["handoverArtifact"]
        written = stored.stat()
        recorded = (
            f"The handover artifact of this request is recorded at {stored}, but the path the "
            f"agent was given, {reference['path']},"
        )
        put_back = (
            "Put back what that path ran through (for a leaf role, the report-access link of "
            "its enclosure) and retry."
        )
        with self.subTest("something else at the link's name is refused, and named"):
            link.unlink()
            link.mkdir()
            # A folder is at the link's name, so the given path leads to itself.
            self.assertEqual(
                self.refused_with_nothing_sent(request),
                f"{recorded} no longer leads to that file. {put_back}",
            )
            self.assertEqual(list(link.iterdir()), [])
            link.rmdir()
        with self.subTest("a link that leads nowhere is not replaced and nothing is made there"):
            nowhere = self.root / "removed-report-folder"
            link.symlink_to(nowhere)
            self.assertEqual(
                self.refused_with_nothing_sent(request),
                f"{recorded} now leads to {nowhere / 'role-launch' / stored.name}. {put_back}",
            )
            self.assertEqual(os.readlink(link), nowhere.as_posix())
            self.assertFalse(nowhere.exists())
            link.unlink()
        with self.subTest("a missing link is bound again, as the first launch bound it"):
            self.assertFalse(link.exists())
            self.assertEqual(self.dispatch(request)[1]["status"], "running")
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), saved["workspace"]["taskReportRoot"])
            self.assertEqual(link.resolve(), stored.parent.parent)
            self.assertEqual(Path(reference["path"]).read_text(encoding="utf-8"), self.prompt)
            self.assertEqual(
                (stored.stat().st_ino, stored.stat().st_mtime_ns),
                (written.st_ino, written.st_mtime_ns),
            )
            created = self.runtime.launch_calls()[-1][1]
            self.assertEqual(created["prompt"], saved["replayRequest"]["agent"]["prompt"])

    def test_a_leaf_retry_binds_no_link_for_a_receipt_that_disagrees_with_itself(self) -> None:
        request, saved, link, stored = self.unresolved_leaf_launch()
        reference = saved["handoverArtifact"]
        workspace = saved["workspace"]
        path = self.receipt_path(request)
        link.unlink()
        foreign = self.root / "a-folder-of-the-caller-s-choosing"
        foreign.mkdir()
        elsewhere = self.root / "a-place-of-the-caller-s-choosing"
        renamed = self.enclosures.group / "reports"
        # A retry binds only where the receipt agrees with itself: the link's name and folder,
        # the artifact's path under the link, its recorded place under the link's target.
        disagreeing: dict[str, dict[str, Any]] = {
            "another target for the link": {
                "workspace": {**workspace, "taskReportRoot": foreign.as_posix()}
            },
            "another place for the link": {
                "workspace": {**workspace, "taskReportAccessRoot": (elsewhere / "link").as_posix()}
            },
            "another name for the link": {
                "workspace": {**workspace, "taskReportAccessRoot": renamed.as_posix()}
            },
            "another name for the link, and the artifact's path through it": {
                "workspace": {**workspace, "taskReportAccessRoot": renamed.as_posix()},
                "handoverArtifact": {
                    **reference,
                    "path": (renamed / "role-launch" / stored.name).as_posix(),
                },
            },
            "the link in another folder than the workspace": {
                "workspace": {**workspace, "path": elsewhere.as_posix()}
            },
            "an artifact path that leaves the link again": {
                "handoverArtifact": {
                    **reference,
                    "path": f"{link.as_posix()}/../task-reports/role-launch/{stored.name}",
                }
            },
        }
        for label, changed in disagreeing.items():
            with self.subTest(label):
                role_launch_receipts._write_receipt(path, {**saved, **changed})
                self.refused_with_nothing_sent(request)
                # No link was made, at the enclosure or anywhere the receipt names.
                self.assertFalse(link.is_symlink() or link.exists())
                self.assertFalse(elsewhere.exists() or renamed.exists())
                self.assertEqual(list(foreign.iterdir()), [])
        with self.subTest("an artifact reference that lacks a part is refused by the receipt"):
            damaged = {key: value for key, value in reference.items() if key != "canonicalPath"}
            role_launch_receipts._write_receipt(path, {**saved, "handoverArtifact": damaged})
            self.assertIn(
                f"The receipt {path} records the handover artifact of this request without its "
                "canonicalPath,",
                self.refused_with_nothing_sent(request),
            )
            self.assertFalse(link.is_symlink() or link.exists())
        with self.subTest("the receipt as the launch wrote it gets the retry through"):
            role_launch_receipts._write_receipt(path, saved)
            self.assertEqual(self.dispatch(request)[1]["status"], "running")
            self.assertEqual(os.readlink(link), workspace["taskReportRoot"])

    def test_a_leaf_agent_is_given_its_paths_through_the_report_access_link(self) -> None:
        self.compile_for_the_enclosure()
        request = self.request("worker")

        self.assertEqual(self.dispatch(request)[1]["status"], "running")

        name = f"01_LEAF-worker-{request.request_id}"
        link = self.enclosures.group / "task-reports" / "role-launch"
        canonical = self.leaf.path.parent / "notes" / "reports" / "role-launch"
        self.assertNotEqual(link, canonical)
        self.assertEqual(link.resolve(), canonical)
        created = self.runtime.launch_calls()[-1][1]
        digest = hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()
        # The agent is told the paths it can reach from inside its enclosure, everywhere.
        reachable = (link / f"{name}.handover.txt").as_posix()
        environment = created["mcpServers"]["agents-remember-task"]["env"]
        self.assertEqual(environment["AR_REPORT_PATH"], (link / f"{name}.md").as_posix())
        self.assertIn(
            f" Assignment file: {reachable} (SHA-256 {digest}). ", created["systemPrompt"]
        )
        self.assertTrue(
            created["prompt"].startswith(f"AR handover artifact: {reachable} (SHA-256 {digest}). ")
        )
        self.assertEqual(
            self.receipt(request)["handoverArtifact"],
            {
                "path": reachable,
                "canonicalPath": (canonical / f"{name}.handover.txt").as_posix(),
                "sha256": digest,
                "bytes": len(self.prompt.encode("utf-8")),
            },
        )
        stored = canonical / f"{name}.handover.txt"
        self.assertEqual(stored.read_text(encoding="utf-8"), self.prompt)
        self.assertEqual(stat.S_IMODE(stored.stat().st_mode), 0o444)


if __name__ == "__main__":
    unittest.main()
