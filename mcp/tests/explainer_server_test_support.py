"""The explainer's own tools at the mounted boundary: render and the three grade tools (MIK-R211 v5).

Each case protects one distinct operation of the seat's own server, at the mounted boundary
(``FastMCP.call_tool``), against a real environment: :func:`build_environment` writes the
workspace, its snapshot of two real Git trees, the packet and the grants, and the assertion is on
what came back and what lies on disk.

1. the registration adds exactly the four tools: render, define_category, remove_category, grade;
2. ``define_category`` and ``grade`` write the store under the leaf's lock, which a build shares;
3. a grade at a binding the environment has left is refused, names binding.json and writes nothing;
4. a foreign caller cannot call the grade tools;
5. each answer is held to its own bound, and what passes it is a named file in scratch/;
6. production grades bind real blocks, planned and same-number claims, by exact identity;
7. ``render`` answers its run folder and report file and publishes, rereading the binding under
   the lock; the same page again is ``unchanged``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.review_evidence_packet import (
    comparison_reference,
    planned_reference,
)
from agents_remember.canvas.explainer_inputs import ExplainerInputs  # the canonical owner
from agents_remember.canvas.explainer_paths import GENERATION_SUFFIX
from agents_remember.canvas.explainer_state import PlannedIdentity, agent_id
from agents_remember.canvas.explainer_workspace import (
    EnvironmentInputs,
    EnvironmentRef,
    EnvironmentRun,
    build_environment,
)
from agents_remember.mcp.registration.explainer_tools import (
    register_bound_explainer_tools,
)  # the entry
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord, ReviewTreeSide
from canvas_host_test_support import LEAF, REPO, TASK, config, packet, publish, task
from knowledge_index_test_support import commit_all, init_repository, write_review_tree
from mcp.server.fastmcp import FastMCP

AT = "2026-10-22T09:00:00+00:00"
MESSAGE_ID = "8d1b0f5e-0f4a-4a57-9c2e-4f4b1f2a6c11"
ARCHITECT = "architect-7d1f"
SCALE = [
    {"grade": "sound", "meaning": "the claim holds against the recorded evidence"},
    {"grade": "thin", "meaning": "the claim rests on less than it says"},
]
REFUSED_FOREIGN = "not-this-explainer"


# ---------------------------------------------------------------------------------------------
# One environment: two real repositories, a record, a packet, a built workspace and its seat
# ---------------------------------------------------------------------------------------------


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class World:
    """One leaf's environment, its two repositories and the task's records area."""

    root: Path
    code: Path
    memory: Path
    task_root: Path
    workspace: Path
    seat: str
    record: ReviewTreeComparisonRecord
    base_commit: str
    candidate_commit: str

    @property
    def code_tree(self) -> str:
        return self.record.code_candidate.tree


def _record(
    code: Path, memory: Path, *, number: int
) -> tuple[ReviewTreeComparisonRecord, str, str]:
    base_commit, candidate_commit = (
        _git(code, "rev-parse", "HEAD~1"),
        _git(code, "rev-parse", "HEAD"),
    )
    comparison = ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=number,
        code_base=ReviewTreeSide(
            repository=str(code), tree=_git(code, "rev-parse", "HEAD~1^{tree}"), commit=base_commit
        ),
        code_candidate=ReviewTreeSide(
            repository=str(code),
            tree=_git(code, "rev-parse", "HEAD^{tree}"),
            commit=candidate_commit,
        ),
        memory_base=ReviewTreeSide(
            repository=str(memory), tree=_git(memory, "rev-parse", "HEAD~1^{tree}")
        ),
        memory_candidate=ReviewTreeSide(
            repository=str(memory), tree=_git(memory, "rev-parse", "HEAD^{tree}")
        ),
        recorded_at=AT,
    )
    return comparison, base_commit, candidate_commit


def _world(tmp_path: Path, *, number: int = 7, planned: bool = False) -> World:
    """Build one environment whose snapshot is two real trees, then raise its generation."""

    settings = config(tmp_path)
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    init_repository(code)
    _write(
        code,
        {
            "src/app.py": "def run():\n    return 1\n",
            "README.md": "the fixture repository\n",
        },
    )
    commit_all(code, "the base")
    _write(code, {"src/app.py": "def run():\n    return 2\n", "src/new.py": "NEW = True\n"})
    commit_all(code, "the candidate")
    init_repository(memory)
    write_review_tree(memory)
    commit_all(memory, "the memory base")
    _write(memory, {"notes/added.md": "# added after the base\n"})
    commit_all(memory, "the memory candidate")
    record, base_commit, candidate_commit = _record(code, memory, number=number)
    reference = planned_reference(TASK, LEAF) if planned else comparison_reference(record)
    binding = FrameBinding.from_record(
        record,
        comparison_owner="planned" if planned else REPO,
        comparison_reference=reference,
    )
    identity = (
        PlannedIdentity(
            code_tip=candidate_commit,
            memory_tip=_git(memory, "rev-parse", "HEAD"),
            code_tree=record.code_candidate.tree,
            memory_tree=record.memory_candidate.tree,
        )
        if planned
        else None
    )
    root = task(settings)
    (root / "notes" / "reference" / LEAF).mkdir(parents=True, exist_ok=True)
    (root / "notes" / "reference" / LEAF / "agreed.md").write_text(
        "# the agreed page\n\nthis wording was agreed\n", encoding="utf-8"
    )
    publish(root, record)
    built = build_environment(
        EnvironmentRun(
            ref=EnvironmentRef(settings, REPO, TASK, LEAF),
            inputs=EnvironmentInputs(
                binding=binding,
                packet=packet(record),
                capsule="the capsule for this leaf's explainer\n",
                code_repository=code,
                memory_repository=memory,
                planned=identity,
            ),
            now=AT,
        )
    )
    built.workspace.with_name(built.workspace.name + GENERATION_SUFFIX).write_text("1\n")
    return World(
        root=tmp_path,
        code=code,
        memory=memory,
        task_root=root,
        workspace=built.workspace,
        seat=agent_id(built.workspace, 1),
        record=record,
        base_commit=base_commit,
        candidate_commit=candidate_commit,
    )


def _fingerprints(references: tuple[str, ...]) -> dict[str, str]:
    """A deterministic stand-in for L89's per-block fingerprint (the product hands one in)."""

    return {
        reference: "sha256:" + hashlib.sha256(reference.encode()).hexdigest()
        for reference in references
    }


def _server(world: World, **overrides: Any):
    inputs = {
        "task_root": world.task_root,
        "fingerprints": _fingerprints,
        "now": lambda: AT,
    }
    inputs.update(overrides)
    seat = inputs.pop("seat", world.seat)
    return _server_only(world.workspace, seat=seat, inputs=ExplainerInputs(**inputs))


def _server_only(
    workspace: Path, *, seat: str | None = None, inputs: ExplainerInputs | None = None
) -> FastMCP:
    """The explainer's tools on a bare server: what a launch would register, without a launch."""

    server = FastMCP("review-explainer")
    register_bound_explainer_tools(server, workspace=workspace, seat=seat, inputs=inputs)
    return server


def _content(server: Any, tool: str, arguments: Mapping[str, Any] | None = None) -> str:
    """The text one mounted tool call answered, whichever shape the SDK returns it in."""

    result = asyncio.run(server.call_tool(tool, dict(arguments or {})))
    if isinstance(result, tuple):  # (content blocks, structured body) on a non-object return
        result = result[0]
    first = result[0]
    return first.text if hasattr(first, "text") else str(first)


def _call(server: Any, tool: str, arguments: Mapping[str, Any] | None = None) -> Any:
    """One mounted tool call, decoded: the JSON body the tool answered."""

    return json.loads(_content(server, tool, arguments))
