"""Every role, on every harness and in every kind of start, gets a sane first message (MIK-R193).

A role's first message is read again from cache on every turn of its life, so it carries the
role's instructions and a projected hand-over only, names the file beside it that holds the
complete hand-over, and says nothing twice. This module builds the real first message through the
product's own start path for every allowed combination and checks rules 1, 3, 5, 6 and 7.

The combinations all come from the code rather than from a list here:

* role: every key of ``ROLE_START_OPERATIONS``;
* harness: every id of ``HARNESS_IDS`` and every id the settings fixture adds;
* parentless start: the selections of ``PARENTLESS_START_SELECTIONS``;
* parented start: the pairs of ``MAY_START``;
* seat set and held seat opening: for ``LEAF_ROLES``;
* successor: for every role outside ``TASKLESS_ROLES``;
* step-up: for every role, with a ``stepUp`` row for it in the fixture's settings;
* restore of a saved launch: for every role.

The built combinations are counted in each test and asserted against the code's own tables; a
failure names the role, the harness, the kind and the broken check.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from _user_settings_guard import user_settings_path_for_write
from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.role_capsules import launch as role_capsules_launch
from agents_remember.application.role_launch_context import (
    LEAF_ROLES,
    PARENTLESS_START_SELECTIONS,
    TASKLESS_ROLES,
)
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.cli import (
    paseo_catalog,
    paseo_launch,
    paseo_status,
    role_held_messages,
    role_launch_routes,
    role_launch_workspace,
)
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.paseo_role_tools import MAY_START, start_leaf_set
from agents_remember.cli.role_brief_message import HANDOVER_HEADING, SHARED_BLOCK_HEADING
from agents_remember.cli.role_handover_file import planned_handover_file
from agents_remember.cli.role_handover_projection import (
    PROJECTION_FIELDS,
    PROJECTION_FILE_FIELD,
    project_handover,
    projection_text,
    projection_tokens,
)
from agents_remember.cli.role_held_messages import HeldMessage
from agents_remember.cli.role_launch_preparation import (
    ROLE_START_OPERATIONS,
    SPLIT_WORK_DIRECTION,
)
from agents_remember.kernel.harnesses import HARNESS_IDS
from agents_remember.kernel.primitives import checkout_coordination
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.models.role_agents import RoleStartSetCall, SeatLaunchOverride
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import LauncherRole, RoleDispatchRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from agents_remember.worktrees.task_document_copies import prepare_task_document_copies
from paseo_launch_test_support import prepare_host
from test_paseo_launch import FakeRuntime
from test_worktree_support import FixtureNames, open_external_contract_fixture

# The harness list of the test's settings fixture: the built-in ids and the id it adds under
# ``orchestration.harnesses``.
FIXTURE_HARNESSES: tuple[str, ...] = (*HARNESS_IDS, "hermes")
# The settings' launch choice of every role, and the step-up row beside it. The step-up names a
# stronger model of the same harness, as a user's settings would.
ROLE_MODEL = "m-1"
STEP_UP_MODEL = "step-up-model"
# The budget registry this repository ships; its ``handover`` section is rule 5's record.
BUDGETS_PATH = (
    Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles/capsule-budgets.json"
)

# Rule 5's fixed root: a projection is counted with the shape a real coordination root has, so
# the count does not move with the test's temporary folder.
FIXED_ROOT = "/home/user/projects/ar-coordination"
# Below that root the fixture's repository, task, leaf and packet names are as long as this
# master's own (rule 5), so the measured projection has the shape a real launch carries.
FIXTURE_REPO = "agents-remember"
FIXTURE_TASK = "260928_maintained-invariant-knowledge"
FIXTURE_MASTER = "260713_improved-agentic-system"
# The rule's own example of a below-root name (worktrees/agents-remember/260928-mik-l89-ar/260928-mik-l89).
FIXTURE_LEAF = "260928-mik-l89"
FIXTURE_SPRINT = "260713_improved-agentic-system"
PACKET = "MIK-R{}03d-v1-a-roles-first-message-holds-what-it-needs-at-once.md"


def _packet_name(number: int) -> str:
    """One requirement packet's file name, at the length the real packets have."""

    return f"MIK-R{number:03d}-v1-a-roles-first-message-holds-what-it-needs-at-once.md"


HARNESS_CATALOG: dict[str, Any] = {
    "runtime": {"serverId": "srv_configured", "version": PASEO_VERSION},
    "providers": [
        {
            "id": harness,
            "label": harness.title(),
            "modes": [],
            "models": [
                {
                    "id": ROLE_MODEL,
                    "label": "Model one",
                    "efforts": [{"id": "low", "label": "Low"}],
                },
                {
                    "id": STEP_UP_MODEL,
                    "label": "Model two",
                    "efforts": [{"id": "low", "label": "Low"}],
                },
            ],
        }
        for harness in FIXTURE_HARNESSES
    ],
}


class _CatalogRuntime(FakeRuntime):
    """The fake host, offering one provider for every harness the fixture names."""

    def _catalog(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return HARNESS_CATALOG

    def _role_profiles(self, _payload: dict[str, Any]) -> dict[str, Any]:
        """The host knows no role profile: the settings' own choices apply."""

        return {
            "serverId": "srv_configured",
            "profiles": [],
            "readAt": "2026-10-21T10:00:00.000Z",
        }


class _Recorded:
    """The hand-over objects the launch handed to its file writer, by request id."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}

    def record(self, report_path: str, handover: dict[str, Any]):
        self.objects[str(handover["requestId"])] = handover
        return planned_handover_file(report_path, handover)

    def of(self, receipt: dict[str, Any]) -> dict[str, Any]:
        return self.objects[receipt["requestId"]]


def _projection_of(message: str) -> dict[str, Any]:
    """The hand-over projection a message carries, or an empty object when it carries none."""

    _head, heading, projection_part = message.rpartition(f"{HANDOVER_HEADING}\n")
    if not heading:
        return {}
    decoded, _end = json.JSONDecoder().raw_decode(projection_part)
    return decoded


def _message_key(message: str) -> str:
    """A message's identity for the send-boundary record."""

    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def _common_prefix(left: str, right: str) -> int:
    """How many characters two strings share from their start."""

    limit = min(len(left), len(right))
    at = 0
    while at < limit and left[at] == right[at]:
        at += 1
    return at


def _operation_of(role: str) -> str:
    """The capsule operation a started role runs, from the code's own table."""

    if role in ROLE_START_OPERATIONS:
        return ROLE_START_OPERATIONS[cast(LauncherRole, canonical_role(role))]
    return ROLE_START_OPERATIONS["investigator"]


def _passages(text: str) -> dict[tuple[str, ...], int]:
    """Every window of 40 words of ``text``, with how often it occurs."""

    words = text.split()
    windows: dict[tuple[str, ...], int] = {}
    for start in range(len(words) - 39):
        window = tuple(words[start : start + 40])
        windows[window] = windows.get(window, 0) + 1
    return windows


def _code_names(text: str) -> set[str]:
    """The code-font names of a compiled capsule or the brief's opening."""

    names: set[str] = set()
    for span in re.findall(r"`([^`\n]+)`", text):
        for part in span.split():
            candidate = part.strip(".,;:'\"()[]")
            if candidate:
                names.add(candidate)
    return names


def _lookup(container: dict[str, Any], path: str) -> tuple[bool, Any]:
    """Whether a dotted path with optional ``[index]`` resolves, and what it holds."""

    current: Any = container
    for part in re.split(r"\.(?![^\[]*\])", path):
        indexed = re.fullmatch(r"([A-Za-z0-9_]+)\[(\d+)\]", part)
        key = indexed[1] if indexed else part
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
        if indexed:
            position = int(indexed[2])
            if not isinstance(current, list) or position >= len(current):
                return False, None
            current = current[position]
    return True, current


def _absolute_paths(text: str) -> list[str]:
    """Every absolute path a message states in running text: one component or more."""

    found = re.findall(r"(?<![\w/.])/[\w.@+~-]+(?:/[\w.@+~-]+)*", text)
    return [path.rstrip(".") for path in found if path not in {"/", "//"}]


def _string_values(value: Any) -> Iterator[str]:
    """Every string a JSON value holds, however deeply it is nested."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)


def _quoted_spans(text: str) -> list[str]:
    """Every backticked or double-quoted span of a message, which may hold a path with spaces."""

    return [*re.findall(r"`([^`\n]+)`", text), *re.findall(r'"([^"\n]+)"', text)]


def _emitted_paths(message: str, projection: dict[str, Any]) -> set[str]:
    """Every absolute path the message puts before its reader, however it is spelled.

    A path is read whole wherever the message delimits it — a backticked or quoted span is taken
    as the path it holds, so one containing a space is checked as written, not cut at the space.
    The structured values are read as they stand for the same reason, and running text is read for
    the paths no delimiter carries.
    """

    paths = set(_absolute_paths(message))
    for span in _quoted_spans(message):
        if span.startswith("/"):
            paths.add(span.rstrip(".,;:"))
    paths.update(
        text for text in _string_values(projection) if text.startswith("/") and "\n" not in text
    )
    return paths


def _missing_paths(message: str, projection: dict[str, Any]) -> list[str]:
    """Every absolute path the message emits that is not there, but the ones it writes."""

    written = [
        projection.get("taskReportPath"),
        projection.get("canonicalTaskReportPath"),
        (projection.get("workspace") or {}).get("taskReportRoot"),
        (projection.get("workspace") or {}).get("taskReportAccessRoot"),
    ]
    # A successor line names the earlier report paths it takes over: the predecessor's, not this
    # launch's.
    successor = message.partition("Successor start:")[2]
    earlier = set(_emitted_paths(successor.split("Take over from what is written there.")[0], {}))
    missing = []
    for path in sorted(_emitted_paths(message, projection)):
        if path in earlier:
            continue
        if any(folder and (path == folder or path.startswith(f"{folder}/")) for folder in written):
            continue
        if not Path(path).exists():
            missing.append(path)
    return missing


def _unreachable(opening: str, projection: dict[str, Any], handover: dict[str, Any]) -> list[str]:
    """The code-font names of the opening that stand in neither the projection nor the file.

    A name counts only when it starts at a field the hand-over object itself has (or at the
    projection's own file field): every other backticked name is a tool's answer field or a tool
    name, not a hand-over value.
    """

    roots = set(handover) | {PROJECTION_FILE_FIELD}
    missing = []
    for name in sorted(_code_names(opening)):
        head = name.split(".", 1)[0].split("[", 1)[0]
        if head not in roots:
            continue
        if _lookup(projection, name)[0]:
            continue
        if not _lookup(handover, name)[0]:
            missing.append(name)
    return missing


def _long_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if len(value) > 200 else []
    if isinstance(value, dict):
        return [text for item in value.values() for text in _long_strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _long_strings(item)]
    return []


def _may_repeat(text: str) -> bool:
    """Absolute paths and task-document references may stand in two places of a projection."""

    return text.startswith("/") or ("/" in text and text.endswith(".json"))


class FirstMessageMatrixTests(unittest.TestCase):
    """MIK-R193 rules 1, 3, 5, 6 and 7: the real first message of every allowed start."""

    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        with patch.dict(checkout_coordination._declared, {"mode": "test"}):
            self.contract = open_external_contract_fixture(
                self.root,
                names=FixtureNames(
                    repo_name=FIXTURE_REPO,
                    task_name=FIXTURE_TASK,
                    master_name=FIXTURE_TASK,
                    leaf_name=FIXTURE_LEAF,
                ),
            )
        self.repo_id = self.contract.repo_name
        self.leaf_slug = self.contract.leaf_id.lower().replace("_", "-")
        self.sprint_ref = TaskDocumentRef(
            repository=self.repo_id, path=f"{FIXTURE_SPRINT}/task.json"
        )
        self.master_ref = TaskDocumentRef(
            repository=self.repo_id, path=f"{self.contract.task_root.name}/task.json"
        )
        self.leaf_ref = TaskDocumentRef(
            repository=self.repo_id,
            path=f"{self.contract.task_root.name}/{self.leaf_slug}.json",
        )
        self._write_task_documents()
        # Publish every requirement copy before the first launch, so each projection names all
        # twelve packets and the measured size does not move with the order of the starts.
        prepare_task_document_copies(
            self.contract.coordination_root, self.contract.worktree_group, self.leaf_ref
        )
        self._write_settings()
        with patch.dict(checkout_coordination._declared, {"mode": "test"}):
            self.config = load_config(self.settings_path)
        self.recorded = _Recorded()
        self.runtime = _CatalogRuntime()
        # Rule 7's path check runs at the send boundary: on the exact message the host is handed,
        # inside the create call, not on a copy after the start returns.
        self.send_boundary_missing: dict[str, list[str]] = {}
        # Every launch the host was actually given, whether or not its message was clean: a send
        # that happened must have a snapshot, and a seat still held has not had one yet.
        self.sent_launches: set[str] = set()
        self.runtime.observer = self._check_paths_at_send
        for module in (paseo_catalog, paseo_launch, paseo_status):
            self._patch(module, "bridge_call", new=self.runtime)
        self._patch(
            role_launch_workspace,
            "_start_leaf_enclosure",
            new=self._leaf_enclosure,
        )
        patcher = patch(
            "agents_remember.cli.role_brief_message.planned_handover_file",
            self.recorded.record,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def _check_paths_at_send(self, command: str, payload: dict[str, Any]) -> None:
        """Rule 7's path check on the message the host is given, at the moment it is given."""

        if command != "agent-create":
            return
        message = payload.get("prompt")
        if not isinstance(message, str) or not message:
            return
        _head, heading, projection_part = message.rpartition(f"{HANDOVER_HEADING}\n")
        projection: dict[str, Any] = {}
        if heading:
            decoded, _end = json.JSONDecoder().raw_decode(projection_part)
            projection = decoded
        # The record is keyed by the launch's request id, which every projection of that launch
        # carries, so a held delivery's snapshot — the saved opening plus the waiting work the host
        # is actually given — is the one a later check of the opening will find. A message that
        # carries no projection has no launch, and falls back to its own bytes.
        request_id = projection.get("requestId")
        key = request_id if isinstance(request_id, str) and request_id else _message_key(message)
        self.sent_launches.add(key)
        self.send_boundary_missing[key] = _missing_paths(message, projection)

    def _patch(self, target: Any, name: str, *, new: Any) -> None:
        patcher = (
            patch.object(target, name, new)
            if not isinstance(target, str)
            else patch(target, new=new)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # The fixture's world.

    def _write_task_documents(self) -> None:
        write_task_doc(
            self.contract.coordination_root / "tasks" / self.repo_id / FIXTURE_SPRINT,
            TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "task",
                    "title": "First message sprint",
                    "kind": "master",
                    "repo": self.repo_id,
                    "createdAt": "2026-10-21T10:00:00+00:00",
                    "orchestrates": [self.contract.task_root.name],
                    # The sprint's own landing line: a step-up records a decision on it.
                    "integrationBranch": "main",
                }
            ),
        )
        write_task_doc(
            self.contract.task_root,
            TaskDocument.model_validate(
                {
                    "id": self.contract.task_id,
                    "slug": "task",
                    "title": self.contract.task_name,
                    "kind": "master",
                    "repo": self.repo_id,
                    "createdAt": "2026-10-21T10:01:00+00:00",
                    "subTasks": [
                        {
                            "number": self.contract.leaf_id,
                            "name": "First message",
                            "file": f"{self.leaf_slug}.md",
                            "status": "inProgress",
                        }
                    ],
                }
            ),
        )
        requirements = self.contract.task_root / "requirements"
        requirements.mkdir(parents=True, exist_ok=True)
        for number in range(1, 13):
            (requirements / _packet_name(number)).write_text(
                f"# MIK-R{number:02d} @ v1\n\nA requirement of the first message.\n",
                encoding="utf-8",
            )
        write_task_doc(
            self.contract.task_root,
            TaskDocument.model_validate(
                {
                    "id": self.contract.leaf_id,
                    "slug": self.leaf_slug,
                    "title": "First message",
                    "kind": "subTask",
                    "status": "inProgress",
                    "repo": self.repo_id,
                    "createdAt": "2026-10-21T10:02:00+00:00",
                    "master": "task.json",
                    # Twelve requirement packets: more than any leaf of this master names, so the
                    # projection measured here is the largest one this role can have (rule 5).
                    "requirements": [
                        f"- [MIK-R{number:03d}@v1](requirements/{_packet_name(number)}) — a "
                        "requirement of the first message."
                        for number in range(1, 13)
                    ],
                    "enclosures": [
                        {
                            "leafId": self.contract.leaf_id,
                            "enclosurePath": self.contract.contract_path.as_posix(),
                        }
                    ],
                }
            ),
        )

    def _write_settings(self) -> None:
        self.settings_path = self.root / "settings" / "ar.json"
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(
                {
                    "coordinationRoot": self.contract.coordination_root.as_posix(),
                    "workspaceRoot": self.root.as_posix(),
                    "repositories": {self.repo_id: {}},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        # The host this installation launches on, then the user's own settings: a launch choice
        # and a step-up row for every role of ROLE_START_OPERATIONS, and the harness id the
        # product does not ship. Both live in the one settings file a launch reads.
        prepare_host(self.root, configured=True)
        user_settings = user_settings_path_for_write()
        current = json.loads(user_settings.read_text(encoding="utf-8"))
        current["orchestration"] = {
            "harnesses": {"hermes": {"command": "hermes"}},
            "roles": {
                role: {
                    "harness": HARNESS_IDS[1],
                    "model": ROLE_MODEL,
                    "stepUp": {"model": STEP_UP_MODEL},
                }
                for role in ROLE_START_OPERATIONS
            },
        }
        user_settings.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _leaf_enclosure(self, _config: Any, _leaf: Any, *, parent_task: str = ""):
        """The enclosure the fixture's contract already opened for this leaf."""

        del parent_task
        return self.contract.contract_path, {
            "enclosurePreparation": "found",
            "worktree_group": self.contract.worktree_group,
            "code_worktree": self.contract.code_worktree,
            "memory_worktree": self.contract.memory_worktree,
        }

    def refs_of(self, role: str) -> dict[str, Any]:
        """The task references a role's own selection carries, from the code's table."""

        required, accepted, forbidden = PARENTLESS_START_SELECTIONS[canonical_role(role)]
        provided = {
            "sprintDocumentRef": self.sprint_ref,
            "masterDocumentRef": self.master_ref,
            "taskDocumentRef": self.leaf_ref,
        }
        return {
            key: reference
            for (key, reference), wanted in zip(
                provided.items(), (required, accepted, forbidden), strict=True
            )
            if wanted is not False
        }

    def request(self, role: str, harness: str, **extra: Any) -> RoleDispatchRequest:
        return RoleDispatchRequest.model_validate(
            {
                "role": role,
                "requestId": uuid.uuid4(),
                **self.refs_of(role),
                "agentOverride": {"agentId": harness},
                **extra,
            }
        )

    def starter(self, role: str) -> StartingAgent:
        return StartingAgent(
            str(uuid.uuid4()),
            role,
            f"{role} · first message",
            sprint_ref=self.sprint_ref,
            master_ref=self.master_ref,
            task_ref=self.leaf_ref if role in LEAF_ROLES else None,
        )

    def receipt_path(self, request: RoleDispatchRequest) -> Path:
        return role_launch_routes._receipt_path(
            self.config,
            request,
            request.request_id if request.role in TASKLESS_ROLES else None,
        )

    def receipt(self, request: RoleDispatchRequest) -> dict[str, Any]:
        return json.loads(self.receipt_path(request).read_text(encoding="utf-8"))

    def sent_prompt(self, request: RoleDispatchRequest) -> str:
        """The first message the host was given for this request (MIK-R193 rule 1)."""

        key = f"ar-role-launch:{request.request_id}"
        for command, payload in self.runtime.calls:
            if command == "agent-create" and payload.get("idempotencyKey") == key:
                return payload["prompt"]
        raise AssertionError(f"no first message was sent for {request.request_id}")

    def release(self, receipt: dict[str, Any]) -> None:
        """Free the seat for the next combination: one seat holds one agent at a time."""

        execution = receipt.get("execution") or {}
        agent = execution.get("agentId")
        if isinstance(agent, str) and agent in self.runtime.agents:
            self.runtime.agents[agent]["archivedAt"] = "2026-10-21T10:30:00.000Z"

    def start(self, role: str, harness: str, **extra: Any) -> tuple[Any, dict[str, Any]]:
        request = self.request(role, harness, **extra)
        role_launch_routes._start_execution(self.config, request)
        receipt = self.receipt(request)
        return request, receipt

    # The checks of rules 1, 3, 5, 6 and 7.

    def check_message(  # noqa: PLR0913 - one check list, one call per world
        self,
        message: str,
        receipt: dict[str, Any],
        *,
        role: str,
        harness: str,
        kind: str,
        expect_snapshot: bool = True,
    ) -> None:
        operation = _operation_of(role)
        where = f"{role}/{operation}, {harness}, {kind}"
        line, separator, body = message.partition("\n")
        self.assertTrue(separator, f"{where}: the message has no artifact line")
        self.assertIn(
            receipt["handoverArtifact"]["path"], line, f"{where}: the line omits the hand-over text"
        )
        self.assertIn(
            receipt["handoverFile"]["path"], line, f"{where}: the line omits the complete hand-over"
        )
        self.assertIn(
            receipt["handoverArtifact"]["sha256"], line, f"{where}: the line omits the text's SHA"
        )
        self.assertIn(
            receipt["handoverFile"]["sha256"],
            line,
            f"{where}: the line omits the complete hand-over's SHA",
        )
        handover = self.recorded.of(receipt)
        stored = receipt["handoverFile"]["content"]
        self.assertEqual(
            json.dumps(handover, ensure_ascii=False, indent=1) + "\n",
            stored,
            f"{where}: the file does not hold the hand-over object's own rendering",
        )
        self.assertEqual(
            json.loads(stored),
            handover,
            f"{where}: the file does not hold the hand-over object",
        )
        self.assertEqual(
            Path(receipt["handoverFile"]["path"]).read_text(encoding="utf-8"),
            stored,
            f"{where}: the written complete hand-over differs from the saved text",
        )
        self.assertEqual(
            Path(receipt["handoverArtifact"]["path"]).read_text(encoding="utf-8"),
            body,
            f"{where}: the hand-over text differs from what follows the artifact line",
        )
        # Rule 11: the one constant stands once, inside the opening, before the capsule.
        self.assertEqual(
            message.count(SPLIT_WORK_DIRECTION),
            1,
            f"{where}: the split-work direction does not stand exactly once",
        )
        at = message.index(SPLIT_WORK_DIRECTION)
        tools = message.index("AR tools, before your first tool call:")
        first_read = message.index(
            "Each contentDigest records the document snapshot used for this launch."
        )
        self.assertLess(
            message.index("AR ROLE BRIEF:"),
            at,
            f"{where}: the direction stands before the opening it belongs to",
        )
        self.assertLess(at, tools, f"{where}: the direction stands after the AR-tools paragraph")
        self.assertLess(tools, first_read, f"{where}: the first-read paragraph moved")
        # Rule 1: at most one block, and no second heading for it. Absence of the block when no
        # source provides one is proven by the plain launch in the shared-block test below.
        self.assertLessEqual(
            message.count(f"## {SHARED_BLOCK_HEADING}"),
            1,
            f"{where}: the block's heading stands more than once",
        )
        head, heading, projection_text_part = body.rpartition(
            "AR owner assignment and canonical task handover:\n"
        )
        self.assertTrue(heading, f"{where}: the message has no hand-over heading")
        # The projection is one compact object exactly as the message carries it; a successor or
        # step-up line may follow it.
        projection, end = json.JSONDecoder().raw_decode(projection_text_part)
        expected = project_handover(
            handover,
            handover_file={
                "path": receipt["handoverFile"]["path"],
                "sha256": receipt["handoverFile"]["sha256"],
            },
            bound_document=self._bound_document(handover),
        )
        self.assertEqual(
            projection_text_part[:end],
            projection_text(expected),
            f"{where}: the message's projection is not the object's compact rendering",
        )
        self.assertEqual(projection, expected, f"{where}: the projection is not the object's")
        self.check_projected_values(projection, handover, receipt, where)
        self.assertLessEqual(set(projection), set(PROJECTION_FIELDS) | {PROJECTION_FILE_FIELD})
        budgets = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))["handover"]
        key = f"{role}/{operation}"
        entry = budgets.get(key) or budgets["system-specialist"]
        measured = projection_tokens(
            projection_text(projection).replace(self.root.as_posix(), FIXED_ROOT)
        )
        self.assertLessEqual(
            measured,
            entry["budget_tokens"],
            f"{where}: the projection is {measured} tokens, over its "
            f"{entry['budget_tokens']} budget",
        )
        self.check_nothing_twice(message, projection, where)
        self.check_reachable(head, projection, handover, where)
        self.check_paths(message, projection, where, expect_snapshot=expect_snapshot)

    def _bound_document(self, handover: dict[str, Any]) -> dict[str, Any] | None:
        selection = handover["selection"]
        return (
            selection.get("taskDocumentRef")
            or selection.get("masterDocumentRef")
            or selection.get("sprintDocumentRef")
        )

    def check_nothing_twice(self, message: str, projection: dict[str, Any], where: str) -> None:
        repeated = sorted(
            {" ".join(window) for window, count in _passages(message).items() if count > 1}
        )
        self.assertEqual(repeated, [], f"{where}: a passage of 40 words appears twice")
        seen: dict[str, str] = {}
        for key, value in projection.items():
            for text in _long_strings(value):
                if _may_repeat(text):
                    continue
                self.assertNotIn(
                    text, seen, f"{where}: {key} repeats a long value of {seen.get(text)}"
                )
                seen[text] = key
        frozen = [json.dumps(value, sort_keys=True) for value in projection.values()]
        self.assertEqual(
            len(frozen),
            len(set(frozen)),
            f"{where}: two top-level fields of the projection hold the same value",
        )

    def check_projected_values(
        self,
        projection: dict[str, Any],
        handover: dict[str, Any],
        receipt: dict[str, Any],
        where: str,
    ) -> None:
        """Every projected field is the hand-over object's own value, without the projector."""

        for key, value in projection.items():
            if key == PROJECTION_FILE_FIELD:
                self.assertEqual(
                    value,
                    {
                        "path": receipt["handoverFile"]["path"],
                        "sha256": receipt["handoverFile"]["sha256"],
                    },
                    f"{where}: the file reference is not the receipt's",
                )
                continue
            self.assertIn(key, handover, f"{where}: {key} is no field of the hand-over object")
            if key == "documents":
                self.assertEqual(
                    len(value), 1, f"{where}: the projection names {len(value)} documents"
                )
                self.assertIn(
                    value[0],
                    handover["documents"],
                    f"{where}: the projected document is not one of the object's",
                )
                continue
            if isinstance(value, dict):
                for nested, nested_value in value.items():
                    found, stored = _lookup(handover, f"{key}.{nested}")
                    self.assertTrue(
                        found, f"{where}: {key}.{nested} is not a field of the hand-over object"
                    )
                    self.assertEqual(
                        json.loads(json.dumps(nested_value)),
                        json.loads(json.dumps(stored)),
                        f"{where}: {key}.{nested} is not the object's value",
                    )
                continue
            self.assertEqual(value, handover[key], f"{where}: {key} is not the object's value")

    def check_reachable(
        self,
        opening: str,
        projection: dict[str, Any],
        handover: dict[str, Any],
        where: str,
    ) -> None:
        self.assertEqual(
            _unreachable(opening, projection, handover),
            [],
            f"{where}: the brief names a value that is in neither the projection nor the file",
        )
        for name in sorted(_code_names(opening)):
            head = name.split(".", 1)[0].split("[", 1)[0]
            if head not in set(handover) | {PROJECTION_FILE_FIELD}:
                continue
            if _lookup(projection, name)[0]:
                continue
            self.assertIn(
                "named by `handoverFile`",
                opening,
                f"{where}: the message does not say where {name!r} stands",
            )

    def check_paths(
        self,
        message: str,
        projection: dict[str, Any],
        where: str,
        *,
        expect_snapshot: bool = True,
    ) -> None:
        """Every absolute path the message emits is there, but the ones the role writes.

        ``expect_snapshot`` is false only for a seat whose start was held and whose delivery this
        world has not performed: nothing was sent, so there is no send to verify yet. Every launch
        that reached the host must carry a snapshot, and a missing one is a failure rather than a
        clean read.
        """

        self.assertEqual(
            _missing_paths(message, projection),
            [],
            f"{where}: the message names a path that is not there",
        )
        # The same check ran on the message the host was handed, at the moment it was handed, and
        # the snapshot it recorded is the one for this launch. A launch with no snapshot is a
        # failed check, not a clean one: the send itself is the thing being verified, and a held
        # delivery's snapshot is keyed by the same request id as the opening that is checked here.
        key = projection.get("requestId")
        if not (isinstance(key, str) and key):
            key = _message_key(message)
        if expect_snapshot or key in self.sent_launches:
            self.assertIn(
                key,
                self.send_boundary_missing,
                f"{where}: no send-boundary snapshot exists for this launch, so its send was "
                "never checked",
            )
        if key in self.send_boundary_missing:
            self.assertEqual(
                self.send_boundary_missing[key],
                [],
                f"{where}: the send-boundary check found a path that is not there",
            )

    # One test per kind of start.

    def test_the_shared_block_comes_from_the_compilation_on_both_start_paths(self) -> None:
        """Rule 1 and amendment f23cfd6a: the block is read from the compiler's outcome.

        The task-bound path (a leaf role) and the taskless path (the architect) are both built
        with a source that fills the field, and with none. Nothing is patched after the compile,
        so a start that drops the block fails here instead of passing past the defect.
        """

        block = "Every role reads the charter, the invariants and the route index first."
        heading = f"## {SHARED_BLOCK_HEADING}\n\n{block}\n\n"
        for role, title, kind in (
            ("worker", "# Worker", "task-bound"),
            ("architect", "# Architect", "taskless"),
        ):
            for harness in FIXTURE_HARNESSES[:1]:
                where = f"{role}/{kind}/{harness}"
                plain_request = self.request(role, harness)
                role_launch_routes._start_execution(self.config, plain_request)
                plain = self.sent_prompt(plain_request)
                self.release(self.receipt(plain_request))
                self.assertNotIn(SHARED_BLOCK_HEADING, plain, f"{where}: no source, yet a block")

                blocked_request = self.request(role, harness)
                with self.shared_block(block):
                    role_launch_routes._start_execution(self.config, blocked_request)
                blocked = self.sent_prompt(blocked_request)
                self.check_message(
                    blocked,
                    self.receipt(blocked_request),
                    role=role,
                    harness=harness,
                    kind=kind,
                )
                self.release(self.receipt(blocked_request))
                self.assertEqual(blocked.count(block), 1, f"{where}: the block does not stand once")
                capsule_at = plain.index(title)
                self.assertEqual(
                    blocked.index(heading),
                    capsule_at,
                    f"{where}: the block does not stand where the capsule starts",
                )
                self.assertEqual(
                    plain.index(title),
                    blocked.index(title) - len(heading),
                    f"{where}: the capsule moved rather than being preceded by the block",
                )

                # An empty string is no block: same capsule position, no heading.
                empty_request = self.request(role, harness)
                with self.shared_block(""):
                    role_launch_routes._start_execution(self.config, empty_request)
                empty = self.sent_prompt(empty_request)
                self.release(self.receipt(empty_request))
                self.assertNotIn(SHARED_BLOCK_HEADING, empty, f"{where}: an empty block wrote one")
                self.assertEqual(
                    empty.index(title),
                    plain.index(title),
                    f"{where}: an empty block moved the capsule",
                )

    @contextmanager
    def shared_block(self, text: str) -> Iterator[None]:
        """Fill the block at the compiler, as a later source (L62) will fill it."""

        real_task = role_capsules_launch.compile_task_capsule
        real_admitted = role_capsules_launch.compile_admitted_capsule

        def carrying_task(*args: Any, **kwargs: Any) -> Any:
            outcome = cast(Any, real_task)(*args, **kwargs)
            if outcome.compilation is None:
                return outcome
            return replace(outcome, compilation=replace(outcome.compilation, shared_block=text))

        def carrying_admitted(*args: Any, **kwargs: Any) -> Any:
            outcome = cast(Any, real_admitted)(*args, **kwargs)
            return replace(outcome, shared_block=text)

        with (
            patch.object(role_capsules_launch, "compile_task_capsule", carrying_task),
            patch.object(role_capsules_launch, "compile_admitted_capsule", carrying_admitted),
        ):
            yield

    def test_the_system_specialist_alias_carries_the_projection(self) -> None:
        """The alias role starts for a taskless seat, and its projection is the investigator's."""

        built = 0
        for harness in FIXTURE_HARNESSES:
            with self.subTest(harness=harness):
                request = RoleDispatchRequest.model_validate(
                    {
                        "role": "system-specialist",
                        "requestId": uuid.uuid4(),
                        "sprintDocumentRef": self.sprint_ref,
                        "agentOverride": {"agentId": harness},
                    }
                )
                role_launch_routes._start_execution(self.config, request)
                receipt = self.receipt(request)
                self.check_message(
                    self.sent_prompt(request),
                    receipt,
                    role="system-specialist",
                    harness=harness,
                    kind="parentless",
                )
                self.release(receipt)
                built += 1
        self.assertEqual(built, len(FIXTURE_HARNESSES))

    def test_parentless_starts_carry_the_projection(self) -> None:
        built = 0
        for role in ROLE_START_OPERATIONS:
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    request, receipt = self.start(role, harness)
                    self.check_message(
                        self.sent_prompt(request),
                        receipt,
                        role=role,
                        harness=harness,
                        kind="parentless",
                    )
                    self.release(receipt)
                    built += 1
        self.assertEqual(built, len(ROLE_START_OPERATIONS) * len(FIXTURE_HARNESSES))

    def test_parented_starts_carry_the_projection(self) -> None:
        built = 0
        for caller, targets in MAY_START.items():
            for role in targets:
                for harness in FIXTURE_HARNESSES:
                    with self.subTest(caller=caller, role=role, harness=harness):
                        request = self.request(role, harness)
                        role_launch_routes._start_execution(
                            self.config, request, self.starter(caller)
                        )
                        self.check_message(
                            self.sent_prompt(request),
                            self.receipt(request),
                            role=role,
                            harness=harness,
                            kind=(
                                "parented leaf seat"
                                if role in LEAF_ROLES
                                else "parented above leaf"
                            ),
                        )
                        self.release(self.receipt(request))
                        built += 1
        self.assertGreater(built, 0)

    def test_a_held_seat_opens_with_a_sane_first_message(self) -> None:
        built = 0
        for role in sorted(LEAF_ROLES):
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    request = self.request(role, harness)
                    role_launch_routes._start_execution(self.config, request, held=True)
                    receipt = self.receipt(request)
                    self.assertIs(receipt.get("heldSeat"), True)
                    # A held seat's opening is not sent by its start; the delivery is what
                    # carries it, and that test owns the snapshot.
                    self.check_message(
                        receipt["heldOpeningPrompt"],
                        receipt,
                        role=role,
                        harness=harness,
                        kind="held seat opening",
                        expect_snapshot=False,
                    )
                    self.release(receipt)
                    built += 1
        self.assertEqual(built, len(LEAF_ROLES) * len(FIXTURE_HARNESSES))

    def test_a_started_set_gives_each_seat_a_sane_first_message(self) -> None:
        built = 0
        manager = AgentBinding(
            agent_id=str(uuid.uuid4()),
            role="manager",
            request_id=str(uuid.uuid4()),
            report_path="/reports/manager.md",
            sprint_ref=self.sprint_ref,
            master_ref=self.master_ref,
        )
        for harness in FIXTURE_HARNESSES:
            with self.subTest(harness=harness):
                call = RoleStartSetCall(
                    request_id=uuid.uuid4(),
                    sprint_document_ref=self.sprint_ref,
                    master_document_ref=self.master_ref,
                    task_document_ref=self.leaf_ref,
                    worker=SeatLaunchOverride(agent=harness),
                    reviewer=SeatLaunchOverride(agent=harness),
                    curator=SeatLaunchOverride(agent=harness),
                )
                answer = start_leaf_set(self.config, call, environment=manager.environment())
                self.assertTrue(answer["ok"], answer)
                for role in sorted(LEAF_ROLES):
                    request = RoleDispatchRequest.model_validate(
                        {
                            "role": role,
                            "requestId": role_launch_routes.seat_request_id(call.request_id, role),
                            **self.refs_of(role),
                            "agentOverride": {"agentId": harness},
                        }
                    )
                    receipt = self.receipt(request)
                    held = receipt.get("heldSeat") is True
                    message = receipt["heldOpeningPrompt"] if held else self.sent_prompt(request)
                    # A held seat has not been sent to, so it has no send to verify here; its
                    # delivery is the check that owns its snapshot.
                    self.check_message(
                        message,
                        receipt,
                        role=role,
                        harness=harness,
                        kind="seat set",
                        expect_snapshot=not held,
                    )
                    self.release(receipt)
                    built += 1
        self.assertGreater(built, 0)

    def test_a_successor_names_the_seat_it_takes_over(self) -> None:
        built = 0
        for role in [name for name in ROLE_START_OPERATIONS if name not in TASKLESS_ROLES]:
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    _first_request, first = self.start(role, harness)
                    self.runtime.agents[first["execution"]["agentId"]]["archivedAt"] = (
                        "2026-10-21T10:30:00.000Z"
                    )
                    request, receipt = self.start(role, harness)
                    message = self.sent_prompt(request)
                    self.assertIn("Successor start:", message)
                    self.check_message(
                        message, receipt, role=role, harness=harness, kind="successor"
                    )
                    self.release(receipt)
                    built += 1
        self.assertGreater(built, 0)

    def test_a_step_up_names_the_material_it_takes_over(self) -> None:
        built = 0
        for role in ROLE_START_OPERATIONS:
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    request, receipt = self.start(
                        role, harness, stepUp="The last round needs a stronger model"
                    )
                    message = self.sent_prompt(request)
                    self.assertIn("Step-up:", message)
                    self.check_message(message, receipt, role=role, harness=harness, kind="step-up")
                    self.release(receipt)
                    built += 1
        self.assertGreater(built, 0)

    def test_a_restored_launch_sends_the_saved_message_and_files(self) -> None:
        built = 0
        for role in ROLE_START_OPERATIONS:
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    request = self.request(role, harness)
                    self.runtime.fail("agent-create", "paseo_daemon_unreachable")
                    role_launch_routes._start_execution(self.config, request)
                    saved = self.receipt(request)["replayRequest"]["agent"]["prompt"]
                    for name in ("handoverArtifact", "handoverFile"):
                        Path(self.receipt(request)[name]["canonicalPath"]).unlink(missing_ok=True)
                    role_launch_routes._start_execution(self.config, request)
                    resent = [
                        call[1]["prompt"]
                        for call in self.runtime.calls
                        if call[0] == "agent-create"
                    ][-1]
                    self.assertEqual(resent, saved)
                    self.check_message(
                        saved,
                        self.receipt(request),
                        role=role,
                        harness=harness,
                        kind="restore",
                    )
                    self.release(self.receipt(request))
                    built += 1
        self.assertGreater(built, 0)

    def test_the_reachability_check_refuses_a_name_that_is_nowhere(self) -> None:
        """Rule 7's oracle answers for a file-only root, an index and a name that is nowhere.

        A check that only recognised projected roots would pass a brief naming a field nothing
        holds; these controls fail that check and pass the two the rule allows.
        """

        request, receipt = self.start("worker", FIXTURE_HARNESSES[0])
        handover = self.recorded.of(receipt)
        message = self.sent_prompt(request)
        _head, _heading, projection_part = message.rpartition(
            "AR owner assignment and canonical task handover:\n"
        )
        projection, _end = json.JSONDecoder().raw_decode(projection_part)
        self.release(receipt)
        # A name that no field of the hand-over object starts: not a hand-over reference at all.
        self.assertEqual(_unreachable("`role_start` and `stop`", projection, handover), [])
        # A field of the file the projection leaves out: reachable through the file.
        self.assertEqual(_unreachable("`contextPacket`", projection, handover), [])
        # An indexed path through the projected documents list: reachable in the projection.
        self.assertEqual(_unreachable("`documents[0].taskDocReadArgs`", projection, handover), [])
        # A file-only root with a part the object does not have, and a projected root likewise.
        for name in ("capsule.missing", "contextPacket.missing", "workspace.missing"):
            self.assertEqual(
                _unreachable(f"see `{name}`", projection, handover),
                [name],
                f"{name}: the reachability check did not report a name that is nowhere",
            )

    def test_the_path_check_reads_a_whitespace_path_whole_and_runs_at_send(self) -> None:
        """B07: a path holding a space is checked as written, and the check runs at the send.

        The negative control the Reviewer reproduced: a message says to read a backticked path
        whose *prefix* exists on disk and whose complete text does not. A check that stops at the
        space accepts it; this one reports the whole path. The second control shows the same check
        running on the message handed to the host, not on a copy read back afterwards.
        """

        folder = self.root / "whitespace"
        folder.mkdir(parents=True, exist_ok=True)
        prefix = folder / "report"
        prefix.write_text("a real file at the prefix only\n", encoding="utf-8")
        complete = f"{prefix} long name.md"
        self.assertTrue(Path(str(prefix)).exists())
        self.assertFalse(Path(complete).exists())
        message = f"Read `{complete}` before you answer.\n"
        self.assertEqual(
            _missing_paths(message, {}),
            [complete],
            "the path check cut a whitespace path at the space and accepted its prefix",
        )
        # The same message, handed to the host: the check records it at the send boundary.
        self._check_paths_at_send("agent-create", {"prompt": message})
        self.assertEqual(
            self.send_boundary_missing.get(_message_key(message)),
            [complete],
            "the path check did not run on the message the host was handed",
        )
        # And a launch with no snapshot at all is refused rather than read as clean.
        with self.assertRaises(AssertionError):
            self.check_paths("no such launch\n", {"requestId": "no-such-request"}, "control")

        # A held delivery is sent as the saved opening plus the waiting work. The check of the
        # opening must find the snapshot of that actual send — so a required path missing from
        # the work, or from the opening, is a failure of the delivery and not a clean read.
        request = self.request("worker", FIXTURE_HARNESSES[0])
        role_launch_routes._start_execution(self.config, request, held=True)
        receipt = self.receipt(request)
        opening = receipt["heldOpeningPrompt"]
        missing_task = str(self.root / "no-such-task-document.json")
        sent = f"{opening}\nReport your role, operation and first read.\nRead `{missing_task}` before you answer.\n"
        self._check_paths_at_send("agent-create", {"prompt": sent})
        projection = _projection_of(opening)
        self.assertIn(projection["requestId"], self.send_boundary_missing)
        with self.assertRaises(AssertionError):
            self.check_paths(opening, projection, "held delivery control")
        # The clean delivery of the same opening passes, which keeps the control negative.
        self._check_paths_at_send(
            "agent-create",
            {"prompt": f"{opening}\nReport your role, operation and first read.\nGo.\n"},
        )
        self.check_paths(opening, projection, "held delivery positive control")
        self.release(self.receipt(request))

    def test_a_held_seat_opens_through_the_delivery_path(self) -> None:
        """Rule 7's held-opening cells drive the real opener, not a stored string.

        For every leaf role and harness the seat is started held, one message is taken into its
        store, and the delivery path opens it. What the host receives is the saved opening plus
        the waiting work, so the opening itself is what rules 1, 3, 5, 6 and 7 are checked on,
        and rule 6's duplicate check covers that opening alone, as it does for any first message.
        """

        built = 0
        for role in sorted(LEAF_ROLES):
            for harness in FIXTURE_HARNESSES:
                with self.subTest(role=role, harness=harness):
                    where = f"{role}, {harness}, held delivery"
                    request = self.request(role, harness)
                    role_launch_routes._start_execution(self.config, request, held=True)
                    receipt_path = self.receipt_path(request)
                    receipt = self.receipt(request)
                    self.assertIs(receipt.get("heldSeat"), True)
                    role_held_messages.hold_message(
                        receipt["workspace"]["path"],
                        receipt["agentId"],
                        HeldMessage(
                            sender_line="From manager · fixture · agent sender-fixture",
                            sender_agent_id="sender-fixture",
                            text="Report your role, operation and first read.",
                            reason="not-running",
                        ),
                    )
                    outcome = role_held_messages.drain_held(self.config, receipt_path, receipt)
                    self.assertEqual(outcome["outcome"], "opened", f"{where}: {outcome}")
                    # The held start creates the seat without a message; the open is the
                    # create that carries one.
                    opened = [
                        call[1]["prompt"]
                        for call in self.runtime.calls
                        if call[0] == "agent-create" and "prompt" in call[1]
                    ][-1]
                    opening = receipt["heldOpeningPrompt"]
                    self.assertTrue(
                        opened.startswith(opening),
                        f"{where}: the delivered turn does not begin with the saved opening",
                    )
                    self.assertEqual(
                        opened.count("Report your role, operation and first read."),
                        1,
                        f"{where}: the waiting work does not stand once in the delivered turn",
                    )
                    self.check_message(
                        opening,
                        self.receipt(request),
                        role=role,
                        harness=harness,
                        kind="held delivery",
                    )
                    self.release(self.receipt(request))
                    built += 1
        self.assertEqual(built, len(LEAF_ROLES) * len(FIXTURE_HARNESSES))


if __name__ == "__main__":
    unittest.main()
