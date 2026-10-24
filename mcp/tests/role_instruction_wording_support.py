"""The skill trees, the forbidden strings and the file readers the wording cases share.

Split out of ``test_role_instruction_wording.py`` so the two halves of the check read on their
own: ``test_role_instruction_wording.py`` holds the instruction-file cases (the router, the role
files, their operations and the packaged copies), and
``test_role_instruction_wording_handover.py`` holds the handover text built in code and the
descriptions of the role tools. Both read the same canonical tree, the same forbidden list and
the same ``forbidden_in`` matcher, defined once here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = Path("l-01-agent-lifecycles")
CANONICAL = REPOSITORY_ROOT / "skills"
# The copy the capsule compiler reads; a rewrite is not live until the sync has run.
PACKAGED = (
    REPOSITORY_ROOT / "mcp" / "src" / "agents_remember" / "package_data" / "runtime" / "skills"
)

# PNT-R06 item 12, searched case-insensitively.
FORBIDDEN = (
    "orca",
    "native Run",
    "Run/Task/Dispatch",
    "Dispatch worker",
    "worker-start",
    "run-create",
    "worker_done",
    "dispatch_agent",
    "message_parent",
    "message_child",
    # MIK-R113 rule 10: the other removed tools, the seat variables of the earlier way and the
    # removed argument of worktree_attach. No role text tells an agent to use a removed part.
    "rename_child",
    "rename_self",
    "lifecycle_start",
    "lifecycle_resume",
    "lifecycle_turn_end_notification",
    "lifecycle_end",
    "switch_lifecycle",
    "lifecycle_phase",
    "lifecycle_gate",
    "gate_decide",
    "gate_list",
    "AR_SPAWN_ROLE",
    "AR_HOSTED_SESSION_ID",
    "on_unsaved",
)
# The report directory of a role, as a segment of the report and artifact paths in a handover. It
# carried the previous host's name until the rename leaf (PNT-R08); the search below now reads
# the whole text, this segment included.
REPORT_DIRECTORY = "/role-launch/"

TOOL_SERVER = "agents-remember-task"
OTHER_INSTALLATION = "agents-remember"
LAUNCHER_ROLES: tuple[str, ...] = tuple(
    dict.fromkeys(canonical_role(role) for role in get_args(LauncherRole))
)
STARTERS = ("architect", "orchestrator", "manager")
_NAMED_TOOL = re.compile("`(" + "|".join(sorted(map(re.escape, PUBLIC_TOOLS))) + ")`")


def forbidden_in(text: str) -> list[str]:
    """The forbidden strings a text contains, in the packet's spelling."""

    folded = text.casefold()
    return [word for word in FORBIDDEN if word.casefold() in folded]


def instruction_files() -> list[Path]:
    """The router, the launcher roles' files and their operations, relative to a skill tree."""

    manifest = json.loads((CANONICAL / LIFECYCLE / "composition-manifest.json").read_text("utf-8"))
    roles = [manifest["roles"][role]["file"] for role in LAUNCHER_ROLES]
    operations = [
        entry["source"]
        for entry in manifest["operations"].values()
        if set(entry["applies_to_roles"]) & set(LAUNCHER_ROLES)
    ]
    return [LIFECYCLE / name for name in ("SKILL.md", *roles, *sorted(operations))]


def read(relative: Path) -> str:
    return (CANONICAL / relative).read_text(encoding="utf-8")


def role_text(role: str) -> str:
    return " ".join(read(LIFECYCLE / "roles" / f"{role}.md").split())
