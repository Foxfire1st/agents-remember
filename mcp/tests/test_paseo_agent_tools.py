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
from agents_remember.application.orca_task_context import OrcaRoleContext
from agents_remember.cli import orca_handover_artifacts, orca_task_preparation, paseo_launch
from agents_remember.cli.orca_handover_artifacts import (
    MAX_HANDOVER_ARTIFACT_BYTES,
    artifact_line,
    first_message,
    restore_handover_artifact,
    write_handover_artifact,
)
from agents_remember.cli.orca_task_preparation import OrcaHandoverRequest
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
from test_paseo_launch import PaseoLaunchTestCase

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "agents_remember"
# The code a launch runs through: every module of the seam, found by name, so that a new one is
# scanned without anyone listing it. It serves every harness alike, so it names none.
LAUNCH_CODE_PATTERNS = (
    "cli/paseo_*.py",
    "cli/orca_*.py",
    "cli/leaf_enclosure_start.py",
    "cli/paseo_bridge.mjs",
    "application/agent_binding.py",
    "application/orca_task_context.py",
)
HARNESS_NAMES = ("codex", "claude", "pi", "hermes", "eve", "opencode", "copilot", "omp")
# A word ends at anything that is not a letter, at an underscore and at a change of case, so a
# harness's name is found inside an identifier too.
WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")
# The one identifier of the capsule compiler that carries a harness's name and selects nothing.
INHERITED_IDENTIFIER = "codex_delivery"
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


def harness_names_in(text: str) -> list[str]:
    scanned = text.replace(INHERITED_IDENTIFIER, "")
    return [word for word in WORD.findall(scanned) if word.lower() in HARNESS_NAMES]


def binding(role: str = "worker", *references: TaskDocumentRef) -> AgentBinding:
    sprint, master, task = (*references, None, None, None)[:3]
    return AgentBinding(
        agent_id="f3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607",
        role=role,
        request_id="0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d",
        report_path="/group/task-reports/orca-native/01_LEAF-worker.md",
        sprint_ref=sprint,
        master_ref=master,
        task_ref=task,
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

    stem = "/group/task-reports/orca-native/"
    return {"path": stem + "p" * (length - len(stem) - 13) + ".handover.txt", "sha256": SHA}


class RecoveryNoteTests(unittest.TestCase):
    def test_the_note_names_role_references_and_artifact_within_600_characters(self) -> None:
        artifact = {
            "path": "/group/task-reports/orca-native/01_LEAF-worker.handover.txt",
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
        # Names as the live task tree has them: references of 56, 72 and 123 characters and an
        # artifact path of 224, for which the unshortened note has 684.
        references = [
            TaskDocumentRef(repository="agents-remember", path=path)
            for path in (
                "260713_improved-agentic-system/task.json",
                "260921_complete-code-and-intent-review-round-2/task.json",
                "260921_complete-code-and-intent-review/"
                "28_curated-foundation-from-code-external-sources-and-onboarding.json",
            )
        ]
        path = (
            "/home/firefox/projects/ar-coordination/worktrees/agents-remember/"
            "28_curated-foundation-from-code-external-60ff47641f-ar/task-reports/orca-native/"
            "260921-ICR-L28-reviewer-00000000-0000-0000-0000-000000000000-fix-2.handover.txt"
        )
        sizes = ([len(reference.key) for reference in references], len(path))
        self.assertEqual(sizes, ([56, 72, 123], 224))
        tail = (
            f" Assignment file: {path} (SHA-256 {SHA}). Reload that file whenever your "
            "assignment is not in your context."
        )
        note = recovery_note(context("reviewer", *references), {"path": path, "sha256": SHA})
        # Path and digest are whole. Sprint and master keep the repository and the leading id of
        # folder and file; the leaf, cut last, loses only the end of its slug.
        self.assertEqual(
            note,
            "AR role agent: reviewer. Sprint agents-remember/260713…/task…. Master "
            "agents-remember/260921…/task…. Task agents-remember/"
            "260921_complete-code-and-intent-review/"
            "28_curated-foundation-from-code-external-sources-and-…." + tail,
        )
        self.assertEqual(len(note), 600)

        with self.subTest("the most specific reference stays whole while the others can be named"):
            note = recovery_note(
                context("reviewer", *references),
                {"path": path[:-60] + ".handover.txt", "sha256": SHA},
            )
            self.assertIn(
                " Sprint agents-remember/260713_improved-ag…/task.json. Master "
                "agents-remember/260921_complete-cod…/task.json. Task "
                f"{references[2].key}. Assignment file: ",
                note,
            )
            self.assertEqual(len(note), 600)
        with self.subTest("room a short reference does not use goes to the others"):
            mixed = context("reviewer", SPRINT, *references[1:])
            whole = len(recovery_note(mixed, artifact_with_path_of(100))) - 100
            note = recovery_note(mixed, artifact_with_path_of(600 - whole + 24))
            self.assertIn(
                " Sprint agents-remember/sprint/task.json. Master "
                "agents-remember/260921_complete-code-…/task.json. Task "
                f"{references[2].key}. Assignment file: ",
                note,
            )
            self.assertEqual(len(note), 600)
        with self.subTest("a single reference is the most specific one"):
            long_sprint = TaskDocumentRef(
                repository="agents-remember", path=f"260713_{'improved-' * 30}system/task.json"
            )
            note = recovery_note(
                context("orchestrator", long_sprint), {"path": path, "sha256": SHA}
            )
            self.assertRegex(
                note,
                r"\. Sprint agents-remember/260713_(improved-)+i[a-z]*…/task\.json\. Assignment",
            )
            self.assertEqual(len(note), 600)

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
            over = recovery_note(full, artifact_with_path_of(601 - whole))
            self.assertEqual(len(over), 600)
            self.assertIn(
                " Sprint agents-remember/sprint/task.js…. Master agents-remember/master/task.json. "
                "Task agents-remember/master/01_leaf.json. ",
                over,
            )
        with self.subTest("the shortest form keeps repository and leading ids; beyond it, refusal"):
            # 'sprint' and 'master' have no id to be cut down to; the documents do.
            shortest = (
                " Sprint agents-remember/sprint/task…. Master agents-remember/master/task…. "
                "Task agents-remember/master/01…. "
            )
            saved = sum(len(reference.key) for reference in (SPRINT, MASTER, LEAF)) - 82
            self.assertEqual(saved, 17)
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

        with self.subTest("the same request reuses it"):
            self.assertEqual(write_handover_artifact(report, content), reference)
            restore_handover_artifact(reference, message)
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
            elsewhere = self.root / "elsewhere.handover.txt"
            mismatches = {
                "no artifact line": (reference, content),
                "another content": (reference, f"{line}\n{content} changed"),
                "the line alone": (reference, line),
                # The content alone would pass the digest; the line has to be the artifact's.
                "another first line": (reference, f"AR handover artifact: elsewhere.\n{content}"),
                # A reference whose resolved path is not where its own path leads.
                "another target": ({**reference, "canonicalPath": elsewhere.as_posix()}, message),
            }
            for label, (given, saved) in mismatches.items():
                with (
                    self.subTest(label),
                    self.assertRaisesRegex(ValueError, "does not match its handover artifact"),
                ):
                    restore_handover_artifact(given, saved)
            self.assertFalse(elsewhere.exists())
        with self.subTest("a lost artifact is written again from the saved message"):
            stored.unlink()
            restore_handover_artifact(reference, message)
            self.assertEqual(stored.read_bytes(), body)

    def test_the_artifact_is_a_regular_file_of_bounded_size(self) -> None:
        reports, access = self.linked_report_folder()
        (reports / "orca-native").mkdir()
        content = "Compiled capsule and handover."
        with self.subTest("something else at the artifact's path is refused"):
            (reports / "orca-native" / "01_LEAF-worker-request.handover.txt").mkdir()
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                write_handover_artifact(
                    (access / "orca-native" / "01_LEAF-worker-request.md").as_posix(), content
                )
        with self.subTest("a link at the artifact's own name is not followed"):
            outside = self.root / "outside.txt"
            linked = (access / "orca-native" / "02_LEAF-worker-request.md").as_posix()
            (reports / "orca-native" / "02_LEAF-worker-request.handover.txt").symlink_to(outside)
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
                patch.object(orca_handover_artifacts.tempfile, "mkstemp", mkstemp),
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
        with self.subTest("a retry refuses an artifact whose content was changed"):
            request = self.request("orchestrator")
            self.runtime.fail("agent-create", "paseo_daemon_unreachable")
            self.dispatch(request)
            artifact = Path(self.artifact(request)["path"])
            artifact.unlink()
            artifact.write_text("another assignment", encoding="utf-8")
            saved = self.receipt_path(request).read_bytes()
            calls = len(self.runtime.calls)
            error = self.refused(request)
            self.assertEqual(error.status_code, 409)
            self.assertIn(f"different handover content in {artifact}", str(error.detail))
            self.assertIn("under a new request id", str(error.detail))
            self.assertEqual(len(self.runtime.calls), calls)
            self.assertEqual(self.receipt_path(request).read_bytes(), saved)
            self.assertEqual(artifact.read_text(encoding="utf-8"), "another assignment")
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

    def test_a_leaf_agent_is_given_its_paths_through_the_report_access_link(self) -> None:
        def compiled_for_the_enclosure(handover: OrcaHandoverRequest) -> dict[str, Any]:
            # As the real compilation does for a leaf role: the report lies behind the link that
            # the leaf's enclosure holds to its task's report folder.
            access = Path(handover.workspace["taskReportAccessRoot"])
            report = access / "orca-native" / f"01_LEAF-worker-{handover.request_id}.md"
            return {
                **self.compile_handover(handover),
                "taskReportPath": report.as_posix(),
                "canonicalTaskReportPath": report.resolve().as_posix(),
            }

        self.replace(orca_task_preparation, "_compile_handover", compiled_for_the_enclosure)
        request = self.request("worker")

        self.assertEqual(self.dispatch(request)[1]["status"], "running")

        name = f"01_LEAF-worker-{request.request_id}"
        link = self.enclosures.group / "task-reports" / "orca-native"
        canonical = self.leaf.path.parent / "notes" / "reports" / "orca-native"
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


class NoHarnessNameTests(unittest.TestCase):
    def test_the_launch_code_names_no_harness(self) -> None:
        scanned = sorted(
            {path for pattern in LAUNCH_CODE_PATTERNS for path in PACKAGE.glob(pattern)}
        )
        # The pattern finds the seam's modules, the ones of this leaf among them.
        for expected in (
            "paseo_launch.py",
            "paseo_catalog.py",
            "paseo_bridge.mjs",
            "orca_task_routes.py",
            "orca_task_receipts.py",
            "orca_task_preparation.py",
            "orca_handover_artifacts.py",
            "leaf_enclosure_start.py",
            "agent_binding.py",
        ):
            self.assertIn(expected, [path.name for path in scanned])
        for path in scanned:
            with self.subTest(path.name):
                found = sorted(set(harness_names_in(path.read_text(encoding="utf-8"))))
                self.assertEqual(found, [], f"{path.name} names a harness: {found}")
        # The scan sees what it is meant to catch, inside an identifier too, and passes the one
        # inherited identifier and words that merely contain a harness's letters.
        caught: dict[str, list[str]] = {
            'if provider == "eve":': ["eve"],
            "HARNESSES = ('Codex', 'pi')": ["Codex", "pi"],
            'WITHOUT_TOOL_SERVERS = ("eve", "hermes")': ["eve", "hermes"],
            "if is_eve_provider(provider):": ["eve"],
            "EVE_ID = provider": ["EVE"],
            "isClaudeProvider(entry)": ["Claude"],
            "capsule.codex_delivery.trusted_instructions": [],
            "an api key, several steps, an event and every option": [],
        }
        for text, names in caught.items():
            with self.subTest(text):
                self.assertEqual(harness_names_in(text), names)


if __name__ == "__main__":
    unittest.main()
