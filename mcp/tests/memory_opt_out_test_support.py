"""MIK-R50 opt-out refusal worlds, shared once by the split refusal modules.

``test_memory_opt_out_refused_start.py`` (rules 1-2) and
``test_memory_opt_out_refused_routes.py`` (rules 3-4) both build scratch
coordinations with ``_world`` and assert refusals with ``_assert_opt_out``;
every definition here lives exactly once, imported by both.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.task_paths import series_contract_path
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.worktree_contract import (
    contract_publication_text,
    default_contract,
    default_series_contract,
    load_contract,
    write_contract,
)
from test_worktree_support import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    git,
    init_repo,
)

declare_test_process()

STATUS = "memory-opt-out-refused"
REPO = "repo-a"

_REMOVED_REASON = "a task in it can be neither closed out, synced, integrated nor landed"


@dataclass(frozen=True)
class _World:
    root: Path
    code: Path
    coord: Path
    memory: Path
    base: str


def _world(root: Path, *, memory: str | None = "plain") -> _World:
    """One scratch coordination with a code repo and the requested memory shape.

    ``memory`` is ``"converted"`` (the layout marker committed on a branch),
    ``"plain"`` (a real repository with no converted knowledge), ``"broken"``
    (a ``.git`` entry Git cannot answer), a plain directory (``"dir"``), or
    ``None`` (no memory root at all).
    """

    code = root / REPO
    base = init_repo(code, "main")
    (code / "file.txt").write_text("code\n", encoding="utf-8")
    git(code, "add", ".")
    git(code, "commit", "-m", "code base")
    coord = root / "ar-coordination"
    coord.mkdir(parents=True, exist_ok=True)
    memory_root = coord / "memory-repos" / f"ar-{REPO}"
    if memory == "converted":
        init_repo(memory_root, "main")
        marker = memory_root / "knowledge" / "layout.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}\n", encoding="utf-8")
        git(memory_root, "add", ".")
        git(memory_root, "commit", "-m", "converted knowledge")
    elif memory == "plain":
        init_repo(memory_root, "main")
        git(memory_root, "commit", "--allow-empty", "-m", "plain memory")
    elif memory == "broken":
        memory_root.mkdir(parents=True, exist_ok=True)
        (memory_root / ".git").write_text("gitdir: /nonexistent/gitdir\n", encoding="utf-8")
    elif memory == "dir":
        memory_root.mkdir(parents=True, exist_ok=True)
    else:
        assert memory is None
        memory_root = coord / "memory-repos" / f"ar-{REPO}"
    return _World(root=root, code=code, coord=coord, memory=memory_root, base=base)


def _assert_opt_out(payload: dict[str, object], *, operation: str) -> None:
    assert payload["state"] == STATUS, payload
    assert payload["status"] == STATUS, payload
    assert payload["requested"] == "disabled", payload
    assert payload["supported"] == ["external"], payload
    assert "memory root inside the code repository" not in str(payload["summary"])
    assert "memory-mode-unsupported" not in str(payload["summary"])
    assert operation in str(payload["summary"])


def _disabled_leaf_file(world: _World, *, cell: str = "disabled") -> Path:
    """One legacy disabled leaf contract on disk, with no live worktrees or branches."""

    coord = world.coord
    code = world.code
    contract = default_contract(
        ContractTask(
            name="legacy-leaf",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(worktree_name="legacy-leaf", leaf_id="L50"),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="super",
            work_branch="ar/legacy-leaf",
            base_commit="a" * 40,
        ),
        memory=RepoBranchPlan(
            repo_path=coord / "memory-repos" / f"ar-{REPO}",
            source_branch="super",
            work_branch="ar/legacy-leaf",
            base_commit="b" * 40,
        ),
    )
    text = contract_publication_text(contract.contract_path, contract)
    assert "memory_mode: external" in text
    lines: list[str] = []
    in_memory = False
    for line in text.splitlines():
        if line == "memory:":
            in_memory = True
        elif line and not line.startswith(" ") and not line.startswith("---"):
            in_memory = False
        if cell != "disabled" and in_memory and line.strip() in {"mode: external"}:
            lines.append("  mode: bogus")
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = text.replace("memory_mode: external", f"memory_mode: {cell}", 1)
    if cell != "disabled":
        # An unknown cell degrades to disabled only where the section it governs
        # names no worktree and no ledger; otherwise it would read external.
        # Only the memory section's pins are stripped: the code section must
        # keep its own worktree and ledger so the file still loads.
        kept = []
        in_memory_section = False
        for line in text.splitlines():
            if line == "memory:":
                in_memory_section = True
            elif line and not line.startswith(" ") and not line.startswith("---"):
                in_memory_section = False
            if in_memory_section and (
                line.strip() in {"worktree:", "ledger:"}
                or line.startswith("  worktree: ")
                or line.startswith("  ledger: ")
                or line.startswith("  repo_path: ")
                or line.startswith("  source_branch: ")
                or line.startswith("  work_branch: ")
                or line.startswith("  base_commit: ")
            ):
                continue
            kept.append(line)
        text = "\n".join(kept)
    contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract.contract_path.write_text(text, encoding="utf-8")
    read_back = load_contract(contract.contract_path)
    assert read_back.memory_mode == "disabled"
    return contract.contract_path


def _live_disabled_leaf(root: Path) -> tuple[_World, Path]:
    """A genuine live legacy leaf: worktrees, disabled leaf and series, one locator.

    The leaf carries real code and memory worktrees descending from its parent
    series branch; both the leaf and its parent series carry the removed mode
    (or the series edge stops matching the leaf's edgeless sides). The locator
    records the final legacy bytes. Shared once: the attach route test and the
    no-mode start attach test (L50-CODE-R1-F01) build this same object.
    """

    root.mkdir(parents=True, exist_ok=True)
    world = _world(root, memory="plain")
    coord = world.coord
    code = world.code
    memory = world.memory
    assert isinstance(memory, Path)
    git(code, "branch", "super", "main")
    git(memory, "branch", "super", "main")
    task_root = coord / "tasks" / REPO / "legacy-leaf"
    write_task_doc(
        coord / "tasks" / REPO / "legacy-sprint",
        TaskDocument.model_validate(
            {
                "id": "legacy-sprint",
                "slug": "task",
                "title": "Sprint",
                "kind": "master",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "orchestrates": ["legacy-leaf"],
                "integrationBranch": "super",
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "legacy-master",
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "executionNature": "atomic",
                "subTasks": [
                    {"number": "L50", "name": "Leaf", "file": "l50.md", "status": "inProgress"}
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "L50",
                "slug": "l50",
                "title": "Leaf",
                "kind": "subTask",
                "status": "inProgress",
                "repo": REPO,
                "createdAt": "2026-10-08T00:00",
                "master": "task.md",
            }
        ),
    )
    contract = default_contract(
        ContractTask(
            name="legacy-leaf",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
            # The atomic leaf files under its parent series; the lineage and
            # the branch authority both resolve this path. Filed below.
            parent_contract_path=series_contract_path(task_root),
        ),
        leaf=LeafIdentity(
            worktree_name="legacy-leaf",
            leaf_id="L50",
            lifecycle_id="l50-live-legacy-lifecycle",
        ),
        code=RepoBranchPlan(
            repo_path=code,
            # An atomic leaf's source is its parent series branch, which the
            # master-to-leaf lineage edge compares by name.
            source_branch="ar/legacy-leaf",
            work_branch="ar/legacy-leaf-wt",
            base_commit=git(code, "rev-parse", "super"),
        ),
        memory=RepoBranchPlan(
            repo_path=memory,
            source_branch="super",
            work_branch="ar/legacy-leaf-wt",
            base_commit=git(memory, "rev-parse", "super"),
        ),
    )
    assert contract.memory_worktree is not None
    # The leaf worktrees descend from the parent series branch (filed below),
    # which the master-to-leaf lineage edge requires; the base commit is
    # unchanged because the series branch points at super.
    git(code, "branch", "ar/legacy-leaf", "super")
    git(memory, "branch", "ar/legacy-leaf", "super")
    git(
        code,
        "worktree",
        "add",
        "-b",
        contract.code_work_branch,
        str(contract.code_worktree),
        "ar/legacy-leaf",
    )
    git(
        memory,
        "worktree",
        "add",
        "-b",
        contract.memory_work_branch,
        str(contract.memory_worktree),
        "ar/legacy-leaf",
    )
    write_contract(contract.contract_path, contract)
    text = contract.contract_path.read_text(encoding="utf-8")
    contract.contract_path.write_text(
        text.replace("memory_mode: external", "memory_mode: disabled", 1), encoding="utf-8"
    )
    # An atomic leaf attaches under its exact parent series contract: file the
    # series the branch authority derives its surfaces from. The series keeps
    # the canonical branch name while the leaf works on its own branch.
    # (Branches created above so the leaf worktrees can descend from them.)
    series = default_series_contract(
        ContractTask(
            name="legacy-leaf",
            repo_name=REPO,
            coordination_root=coord,
            workflow_kind="light-task",
            memory_mode="external",
        ),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="super",
            work_branch="ar/legacy-leaf",
            base_commit=git(code, "rev-parse", "super"),
        ),
        memory=RepoBranchPlan(
            repo_path=memory,
            source_branch="super",
            work_branch="ar/legacy-leaf",
            base_commit=git(memory, "rev-parse", "super"),
        ),
    )
    write_contract(series.contract_path, series)
    # The legacy parent carries the removed mode too, or its memory edge
    # stops matching the disabled leaf's edgeless sides.
    series_text = series.contract_path.read_text(encoding="utf-8")
    series.contract_path.write_text(
        series_text.replace("memory_mode: external", "memory_mode: disabled", 1),
        encoding="utf-8",
    )
    # The locator must record the final legacy bytes: publishing before the
    # mode rewrite leaves a predecessor that cannot authorize this enclosure.
    publish_new_lifecycle_operation_location(
        load_contract(contract.contract_path),
        contract_text=contract.contract_path.read_text(encoding="utf-8"),
    )
    assert load_contract(contract.contract_path).memory_mode == "disabled"
    assert load_contract(series.contract_path).memory_mode == "disabled"
    return world, contract.contract_path
