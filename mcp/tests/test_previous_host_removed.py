"""PNT-R08: the build names its previous host nowhere, and serves and reads nothing of it.

The previous host of this line was Orca. Four things are shown here, in the one test module that
may write that name:

- item 5: no tracked file names it, in its path or its content, outside the allow-list below;
- item 2: a settings file that still carries the ``orcaRuntime`` block loads as if the block
  were absent;
- item 7: the launcher routes are registered under their new paths only, and no route of the
  dashboard application matches a former path;
- the packet's recovery rule: receipts and reports the previous line left on disk are neither
  read nor changed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

from agents_remember.cli import role_launch_receipts, role_launch_routes
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.role_launcher import RoleLauncherOptionsRequest
from agents_remember.serving.app import create_app
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.static import dashboard_static_dir
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.routing import Match, Mount
from test_paseo_launch import ROLE_REFS, PaseoLaunchTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = "mcp/tests/test_previous_host_removed.py"
NAME = "orca"

SRC = "mcp/src/agents_remember/"
# Identifiers in which the four letters belong to two unrelated words, each with the files it
# stands in. Nothing else of a listed file is allowed.
ALLOWED_IDENTIFIERS: dict[str, tuple[str, ...]] = {
    "_CuratorCandidateInputs": (
        SRC + "application/memory_quality/controller.py",
        "mcp/tests/test_memory_quality_runs.py",
    ),
    "DoorCandidateEvidence": (SRC + "worktrees/integration/closeout/door_evidence.py",),
    "successorCandidateCodeTree": (
        SRC + "certification/lifecycle_admission.py",
        SRC + "certification/lifecycle_models.py",
    ),
    "priorCatalog": (
        SRC + "certification/lifecycle_admission.py",
        "mcp/tests/test_closeout_certification_recovery.py",
    ),
    "priorCatalogDigest": (
        SRC + "certification/lifecycle_admission.py",
        SRC + "certification/lifecycle_models.py",
    ),
    "remoteConnectorCarry": ("dashboard/src/panels/engine-room/remote.styles.ts",),
}
# The tests that must write the name, allowed by file name: this module (items 2, 5 and 7 and the
# receipts of the previous line) and the wording test of PNT-R06, whose list of forbidden
# strings begins with it.
ALLOWED_FILES: dict[str, str] = {
    THIS_FILE: "the search itself, and the cases for the leftover settings block, the former "
    "routes and the previous line's receipts",
    "mcp/tests/test_role_instruction_wording.py": "PNT-R06 item 12: the forbidden strings of "
    "the role instructions",
}

_TOKEN = re.compile(rb"[A-Za-z0-9_]*orca[A-Za-z0-9_]*", re.IGNORECASE)
# A word ends at anything that is not a letter, at an underscore and at a change of case.
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")


def words_of(identifier: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(identifier)]


def named_in(path: str, content: bytes) -> list[str]:
    """Where a file names the previous host outside the allow-list: its path, then its lines."""

    if path in ALLOWED_FILES:
        return []
    found = [f"{path}: the path itself"] if NAME in path.casefold() else []
    for match in _TOKEN.finditer(content):
        token = match.group().decode("latin-1")
        if path in ALLOWED_IDENTIFIERS.get(token, ()):
            continue
        line = content.count(b"\n", 0, match.start()) + 1
        found.append(f"{path}:{line}: {token}")
    return found


def git(root: Path, *arguments: str) -> str:
    """Run Git on the repository at ``root`` and on no other, whatever the caller's environment."""

    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("GIT_") or name == "GIT_EXEC_PATH"
    }
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(
        ["git", *arguments], cwd=root, env=environment, capture_output=True, text=True, check=True
    ).stdout


def tracked_files(root: Path) -> list[str]:
    return [path for path in git(root, "ls-files", "-z").split("\0") if path]


def content_of(root: Path, path: str) -> bytes | None:
    """A tracked file's bytes; a link's target text; nothing for a file the tree has lost."""

    file = root / path
    if file.is_symlink():
        return str(file.readlink()).encode("utf-8", "surrogateescape")
    return file.read_bytes() if file.is_file() else None


class NoTrackedFileNamesThePreviousHostTests(unittest.TestCase):
    def test_no_tracked_file_names_the_previous_host_outside_the_allow_list(self) -> None:
        files = tracked_files(REPO_ROOT)
        self.assertGreater(len(files), 1500, "the search read too few files to mean anything")
        self.assertIn(THIS_FILE, files)
        contents = {path: content_of(REPO_ROOT, path) for path in files}

        found = [
            hit
            for path, content in contents.items()
            if content is not None
            for hit in named_in(path, content)
        ]

        self.assertEqual(found, [])
        # No entry outlives its use: each listed identifier stands in each of its files, and
        # each file allowed by name still names the host.
        for identifier, paths in ALLOWED_IDENTIFIERS.items():
            for path in paths:
                with self.subTest(identifier=identifier, path=path):
                    content = contents.get(path)
                    self.assertIsNotNone(content, "the allow-list names a file that is gone")
                    assert content is not None
                    tokens = {match.group().decode("latin-1") for match in _TOKEN.finditer(content)}
                    self.assertIn(identifier, tokens)
        for path in ALLOWED_FILES:
            with self.subTest(path=path):
                self.assertIn(NAME.encode(), (contents.get(path) or b"").lower())

    def test_the_allow_list_holds_unrelated_words_and_the_tests_that_must_name_the_host(
        self,
    ) -> None:
        for identifier, paths in ALLOWED_IDENTIFIERS.items():
            with self.subTest(identifier=identifier):
                self.assertIn(NAME, identifier.casefold())
                # The four letters run across a word boundary: no word of the identifier holds
                # them, so the identifier cannot be one that is named for the host.
                self.assertEqual([word for word in words_of(identifier) if NAME in word], [])
                self.assertGreater(len(paths), 0)
                self.assertEqual(set(paths) & set(ALLOWED_FILES), set())
        self.assertEqual(
            sorted(ALLOWED_FILES), [THIS_FILE, "mcp/tests/test_role_instruction_wording.py"]
        )
        for path, reason in ALLOWED_FILES.items():
            self.assertRegex(path, r"^mcp/tests/test_\w+\.py$")
            self.assertGreater(len(reason), 40, f"{path} is allowed without a reason")

    def test_the_search_finds_each_kind_of_name_and_passes_only_what_is_listed(self) -> None:
        module = SRC + "cli/zz_launch.py"
        caught: dict[str, tuple[str, bytes, list[str]]] = {
            "a class named for the host": (
                module,
                b"from zz import OrcaRuntimeFailure\n",
                [f"{module}:1: OrcaRuntimeFailure"],
            ),
            "a module path": (
                SRC + "cli/orca_runtime.py",
                b"import json\n",
                [f"{SRC}cli/orca_runtime.py: the path itself"],
            ),
            "a component file in another case": (
                "dashboard/src/cockpit/OrcaChats.tsx",
                b"export {};\n",
                ["dashboard/src/cockpit/OrcaChats.tsx: the path itself"],
            ),
            "a route path": (
                "dashboard/src/cockpit/zz.ts",
                b'\n\nfetch("/api/orca/frame");\n',
                ["dashboard/src/cockpit/zz.ts:3: orca"],
            ),
            "an environment variable": (
                module,
                b'os.environ.get("ORCA_PAIRING_CODE")\n',
                [f"{module}:1: ORCA_PAIRING_CODE"],
            ),
            "the settings block": (
                "docs/reference/settings-json.md",
                b'{\n  "orcaRuntime": {}\n}\n',
                ["docs/reference/settings-json.md:2: orcaRuntime"],
            ),
            "a schema string and a folder": (
                module,
                b'SCHEMA = "ar-orca-native-execution/v1"\nFOLDER = "orca-native"\n',
                [f"{module}:1: orca", f"{module}:2: orca"],
            ),
            "prose in an instruction file": (
                "skills/l-01-agent-lifecycles/SKILL.md",
                b"Orca owns execution.\n",
                ["skills/l-01-agent-lifecycles/SKILL.md:1: Orca"],
            ),
            "a file without text": (
                "dashboard/public/zz.bin",
                b"\x00\xffoRcA\x00",
                ["dashboard/public/zz.bin:1: oRcA"],
            ),
            # An unrelated word is passed in the files it is listed for and nowhere else.
            "a listed identifier in another file": (
                module,
                b"class DoorCandidateEvidence: ...\n",
                [f"{module}:1: DoorCandidateEvidence"],
            ),
            "an unrelated word that nobody listed": (
                module,
                b"def errorCallback(): ...\ncolorCache = {}\n",
                [f"{module}:1: errorCallback", f"{module}:2: colorCache"],
            ),
            "a listed identifier as part of a longer one": (
                SRC + "certification/lifecycle_models.py",
                b"priorCatalogDigest: str\npriorCatalogOrcaDigest: str\n",
                [f"{SRC}certification/lifecycle_models.py:2: priorCatalogOrcaDigest"],
            ),
        }
        for label, (path, content, expected) in caught.items():
            with self.subTest(label):
                self.assertEqual(named_in(path, content), expected)
        passed = {
            "a listed identifier in its file": (
                SRC + "worktrees/integration/closeout/door_evidence.py",
                b"class DoorCandidateEvidence: ...\n",
            ),
            "a file allowed by name": (THIS_FILE, b'NAME = "orca"\n'),
            "the new names": (module, b'fetch("/api/role-launch/frame")  # RoleChats\n'),
        }
        for label, (path, content) in passed.items():
            with self.subTest(label):
                self.assertEqual(named_in(path, content), [])
        # A name of the host is not an unrelated word, whatever it is joined to.
        for identifier in ("OrcaChats", "orca_runtime", "ORCA_PAIRING_CODE", "nativeOrca", "orcas"):
            with self.subTest(identifier=identifier):
                self.assertNotEqual([word for word in words_of(identifier) if NAME in word], [])

    def test_the_tree_search_reads_tracked_files_links_included(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git(root, "init", "-q")
            files = {
                "src/clean.py": b"import json\n",
                "src/named.py": b"ORCA = 1\n",
                "src/Orca/clean.py": b"import json\n",
                "untracked.py": b"ORCA = 2\n",
            }
            for path, content in files.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(content)
            (root / "link").symlink_to("somewhere/orca-native")
            tracked = ["src/clean.py", "src/named.py", "src/Orca/clean.py", "link"]
            git(root, "add", "--", *tracked)

            listed = tracked_files(root)
            found = [
                hit
                for path in listed
                if (content := content_of(root, path)) is not None
                for hit in named_in(path, content)
            ]

        self.assertEqual(sorted(listed), sorted(tracked))
        self.assertEqual(
            sorted(found),
            ["link:1: orca", "src/Orca/clean.py: the path itself", "src/named.py:1: ORCA"],
        )


def settings_document(root: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "coordinationRoot": (root / "coordination").as_posix(),
        "workspaceRoot": (root / "projects").as_posix(),
        "repositories": {},
        "paseoRuntime": {
            "installPrefix": (root / "paseo" / "prefix").as_posix(),
            "home": (root / "paseo" / "home").as_posix(),
            "listen": "127.0.0.1:6862",
            "version": "0.11.0-beta.2",
            "providers": {},
            "embed": [],
        },
    }


class LeftoverSettingsBlockTests(unittest.TestCase):
    def test_a_settings_file_with_the_former_block_loads_as_if_the_block_were_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "settings" / "mcp.json"
            path.parent.mkdir()
            document = settings_document(root)
            path.write_text(json.dumps(document), encoding="utf-8")
            without = load_config(path)
            self.assertIsNotNone(without.paseo_runtime)
            leftovers: dict[str, Any] = {
                "the block as the previous line documented it": {
                    "runtimeRoot": "/opt/previous-host/source",
                    "userDataPath": "/home/dev/.config/previous-host",
                },
                # What that line's parser refused is not judged either: nothing parses the block.
                "a block with a relative path and an unknown key": {
                    "runtimeRoot": "relative/source",
                    "pairingCode": "not-read",
                },
                "a block that is no object": "/opt/previous-host",
                "an empty block": {},
            }
            for label, block in leftovers.items():
                with self.subTest(label):
                    path.write_text(
                        json.dumps({**document, "orcaRuntime": block}), encoding="utf-8"
                    )
                    self.assertEqual(load_config(path), without)
        # The loaded configuration has no place for the block.
        fields = {name.casefold() for name in McpRuntimeConfig.__dataclass_fields__}
        self.assertEqual([name for name in fields if NAME in name], [])


FORMER_ROUTES = (
    "/api/orca/frame",
    "/api/orca/launcher/options",
    "/api/orca/dispatch",
    "/api/orca/result",
)
ROUTES = {
    "/api/role-launch/frame": {"GET"},
    "/api/role-launch/options": {"POST"},
    "/api/role-launch/dispatch": {"POST"},
    "/api/role-launch/result": {"POST"},
}
METHODS = ("GET", "POST", "PUT", "DELETE")


class FormerRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.config = McpRuntimeConfig(
            config_path=root / "settings" / "mcp.json",
            coordination_root=root / "coordination",
            workspace_root=root / "projects",
            transcript_root=root / "coordination" / "logs" / "mcp",
        )

    def test_the_launcher_registers_its_four_routes_and_answers_404_on_the_former_paths(
        self,
    ) -> None:
        app = FastAPI()
        before = len(app.routes)
        role_launch_routes.register_role_launch_routes(app, self.config)

        registered = {route.path: set(route.methods) for route in app.routes[before:]}  # type: ignore[attr-defined]
        self.assertEqual(registered, ROUTES)
        client = TestClient(app)
        for path in FORMER_ROUTES:
            for method in METHODS:
                with self.subTest(path=path, method=method):
                    self.assertEqual(client.request(method, path, json={}).status_code, 404)
        # The new paths are served: without a configured runtime the frame route says so, and
        # the three others judge the request body they were sent.
        frame = client.get("/api/role-launch/frame")
        self.assertEqual((frame.status_code, frame.json()["reason"]), (200, "not-configured"))
        for path in ("options", "dispatch", "result"):
            with self.subTest(path=path):
                self.assertEqual(client.post(f"/api/role-launch/{path}", json={}).status_code, 422)

    def test_no_route_of_the_dashboard_application_matches_a_former_path(self) -> None:
        app = create_app(
            self.config,
            cadence=ProjectionCadence(interval=100),
            collaborators=serving_collaborators(self.config),
        )
        served = [route for route in app.routes if not isinstance(route, Mount)]
        self.assertGreater(len(served), 20)
        paths = {getattr(route, "path", "") for route in served}
        self.assertLessEqual(set(ROUTES), paths)
        self.assertEqual([path for path in paths if NAME in path.casefold()], [])
        client = TestClient(app)
        bundle = dashboard_static_dir() is not None
        for path in FORMER_ROUTES:
            for method in METHODS:
                with self.subTest(path=path, method=method):
                    scope = {"type": "http", "path": path, "method": method, "root_path": ""}
                    matching = [
                        route for route in served if route.matches(scope)[0] is not Match.NONE
                    ]
                    self.assertEqual(matching, [])
                    # Only the static surface, mounted at the root after every API route, is
                    # left to answer: not found for a read when a bundle is built (the build's
                    # own notice of a missing bundle otherwise), and its method refusal for
                    # anything but a read. No handler of the launcher runs.
                    status = client.request(method, path, json={}).status_code
                    expected = (404 if bundle else 503) if method == "GET" else 405
                    self.assertEqual(status, expected)


class PreviousLineReceiptTests(PaseoLaunchTestCase):
    """What the line this build was copied from left on disk is neither read nor changed."""

    def previous_line_address(self, own: Path) -> Path:
        """The same address in that line's folder: its layout below the folder was the same."""

        parts = [
            "orca-native-executions" if part == role_launch_receipts.EXECUTIONS_DIRECTORY else part
            for part in own.parts
        ]
        self.assertNotEqual(parts, list(own.parts))
        return Path(*parts)

    def test_receipts_and_reports_of_the_previous_line_are_ignored_and_left_as_they_are(
        self,
    ) -> None:
        architect, worker = self.request("architect"), self.request("worker")
        reports = self.leaf.path.parent / "notes" / "reports"
        folder = self.previous_line_address(
            self.config.coordination_root
            / "notes"
            / "reports"
            / role_launch_receipts.EXECUTIONS_DIRECTORY
        )
        # An open execution of that line at each address this line would read in its own folder.
        open_execution = {
            "schema": "ar-orca-native-execution/v1",
            "status": "running",
            "execution": {"kind": "terminal", "handle": "term_1"},
        }
        receipts = {
            self.previous_line_address(self.receipt_path(architect)): "architect",
            self.previous_line_address(self.receipt_path(worker)): "worker",
            folder / "architect-legacy.json": "architect",
            folder / "history" / f"{uuid.uuid4()}.json": "architect",
            folder / "message-bindings" / f"{architect.request_id}.json": "architect",
        }
        planted: dict[Path, bytes] = {}
        for path, role in receipts.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            body = {**open_execution, "role": role, "requestId": str(uuid.uuid4())}
            path.write_text(json.dumps(body), encoding="utf-8")
            planted[path] = path.read_bytes()
        report = reports / "orca-native" / f"01_LEAF-worker-{uuid.uuid4()}.md"
        report.parent.mkdir(parents=True)
        report.write_text("A report of the previous line.\n", encoding="utf-8")
        planted[report] = report.read_bytes()

        def previous_line_files() -> set[Path]:
            return {
                path
                for path in self.root.rglob("*")
                if path.is_file() and NAME in path.relative_to(self.root).as_posix()
            }

        self.assertEqual(previous_line_files(), set(planted))
        # The launcher reads no execution from them: none is listed and none is open.
        for request in (architect, worker):
            options = role_launch_routes._role_launch_options_endpoint(
                self.config,
                RoleLauncherOptionsRequest.model_validate(
                    {"role": request.role, **ROLE_REFS[request.role]}
                ),
            )
            answer = json.loads(bytes(options.body))
            self.assertEqual(answer.get("executions", []), [])
            self.assertIsNone(answer.get("execution"))
        # A start on each selection is a first start, although that line's receipts say open.
        for request in (architect, worker):
            status, started = self.dispatch(request)
            self.assertEqual((status, started["status"]), (200, "running"))
            self.assertEqual(self.receipt(request)["schema"], role_launch_receipts.RECEIPT_SCHEMA)
            self.assertEqual(self.refresh(request)["status"], "running")
        self.assertEqual(role_launch_receipts.RECEIPT_SCHEMA, "ar-role-execution/v1")
        # Nothing of that line was changed, moved or added to.
        self.assertEqual(previous_line_files(), set(planted))
        self.assertEqual({path: path.read_bytes() for path in planted}, planted)

    def test_a_receipt_under_the_former_schema_name_at_this_lines_address_is_refused(self) -> None:
        """What this line wrote before the rename is no receipt of the build any more.

        Such a file can only be in a folder this line used before the schema was renamed. It is
        refused by name on every read and on a start, and it is left as it is: no code of the
        build knows the former schema name, so nothing converts or deletes the file.
        """

        worker = self.request("worker")
        path = self.receipt_path(worker)
        path.parent.mkdir(parents=True)
        former = {"schema": "ar-orca-native-execution/v1", "status": "completed", "role": "worker"}
        path.write_text(json.dumps({**former, "requestId": str(uuid.uuid4())}), encoding="utf-8")
        before = path.read_bytes()

        refusal = "The role execution receipt is malformed; reconcile it before launching."
        with self.assertRaises(HTTPException) as read:
            role_launch_routes._role_launch_options_endpoint(
                self.config,
                RoleLauncherOptionsRequest.model_validate(
                    {"role": "worker", **ROLE_REFS["worker"]}
                ),
            )
        self.assertEqual((read.exception.status_code, read.exception.detail), (409, refusal))
        started = self.refused(worker)
        self.assertEqual((started.status_code, started.detail), (409, refusal))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.runtime.agents, {})


if __name__ == "__main__":
    unittest.main()
