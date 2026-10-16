"""The existing change-set viewer on a recorded review's exact trees (MIK-R86)."""

from pathlib import Path
from typing import Any

from agents_remember.application.review_source_inventory import is_text_path
from agents_remember.application.review_tree_comparison import comparison_records
from agents_remember.errors import AuthorityError
from agents_remember.kernel.git_command import PARSED_DIFF_OPTIONS
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.models.knowledge_files.shapes import require_repository_path
from agents_remember.serving.changeset_reads import change_counters
from agents_remember.serving.changeset_reads import read_git as run_git
from agents_remember.serving.scope import language_for
from agents_remember.worktrees.worktree_contract import WorktreeContract


def recorded_comparison(contract: WorktreeContract, number: int) -> ReviewTreeComparisonRecord:
    for record in comparison_records(contract.task_root, contract.leaf_id or contract.task_name):
        if record.number == number:
            return record
    raise FileNotFoundError(f"leaf {contract.leaf_id} records no tree comparison {number}")


def _range(record: ReviewTreeComparisonRecord, memory: bool) -> tuple[Path, str, str]:
    before = record.memory_base if memory else record.code_base
    after = record.memory_candidate if memory else record.code_candidate
    base = record.converted_base.tree if memory and record.converted_base else before.tree
    return Path(after.repository), base, after.tree


def _files(root: Path, base: str, tip: str, *, memory: bool) -> list[dict[str, Any]]:
    # One diff supplies both status and counts. No rename inference: the review lists two paths.
    args = ["diff", *PARSED_DIFF_OPTIONS, "--raw", "--numstat", "-z", "--no-renames", base, tip]
    if memory:
        args.extend(["--", "knowledge/", "onboarding/"])
    result = run_git(root, args)
    if result.returncode:
        raise FileNotFoundError(
            f"comparison trees {base}..{tip} cannot be read: {result.stderr.strip()}"
        )
    fields = result.stdout.split("\0")
    statuses: dict[str, str] = {}
    index = 0
    while index < len(fields) and fields[index].startswith(":"):
        statuses[fields[index + 1]] = fields[index].rsplit(" ", 1)[1]
        index += 2
    files = []
    for field in fields[index:]:
        if not field:
            continue
        added, removed, path = field.split("\t", 2)
        if not is_text_path(path):
            continue  # The review's inventory carries these byte spellings separately.
        files.append(
            {
                "path": path,
                "status": statuses[path],
                "insertions": None if added == "-" else int(added),
                "deletions": None if removed == "-" else int(removed),
                **({"hasSidecar": False} if not memory else {}),
            }
        )
    return sorted(files, key=lambda file: file["path"])


def _paired_sidecars(record: ReviewTreeComparisonRecord, code: list[dict[str, Any]]) -> set[str]:
    """Pair against the two recorded trees, including an unchanged or removed sidecar."""
    paths = [f"onboarding/{file['path']}.md" for file in code]
    if not paths:
        return set()
    present: set[str] = set()
    for side in (record.memory_base, record.memory_candidate):
        tree = (
            record.converted_base.tree
            if side is record.memory_base and record.converted_base
            else side.tree
        )
        result = run_git(
            Path(side.repository),
            ["--literal-pathspecs", "ls-tree", "-r", "-z", "--name-only", tree, "--", *paths],
        )
        if result.returncode:
            raise FileNotFoundError(
                f"comparison memory tree {tree} cannot be read: {result.stderr.strip()}"
            )
        present.update(result.stdout.split("\0"))
    return present


def comparison_changeset(contract: WorktreeContract, number: int) -> dict[str, Any]:
    record = recorded_comparison(contract, number)
    code = _files(*_range(record, False), memory=False)
    memory = _files(*_range(record, True), memory=True)
    memory_paths = _paired_sidecars(record, code)
    for file in code:
        file["hasSidecar"] = f"onboarding/{file['path']}.md" in memory_paths
    _, code_base, code_tip = _range(record, False)
    _, memory_base, memory_tip = _range(record, True)
    return {
        "scope": contract.leaf_id,
        "mode": "comparison",
        "comparison": number,
        "trees": {
            "codeBase": code_base,
            "codeTip": code_tip,
            "memoryBase": memory_base,
            "memoryTip": memory_tip,
        },
        "code": code,
        "memory": memory,
        "counters": {"code": change_counters(code), "memory": change_counters(memory)},
    }


def comparison_file_diff(
    contract: WorktreeContract, number: int, kind: str, path: str
) -> dict[str, Any]:
    record = recorded_comparison(contract, number)
    memory = kind == "memory"
    root, base, tip = _range(record, memory)
    # Historical Git addresses must not follow today's filesystem aliases (L86-CODE-R1-F01).
    try:
        rel = require_repository_path(path)
    except ValueError as error:
        raise AuthorityError(str(error)) from error
    if memory and not rel.startswith(("knowledge/", "onboarding/")):
        raise AuthorityError("comparison memory files must be under knowledge/ or onboarding/")
    sides = []
    for tree in (base, tip):
        # cat-file distinguishes a missing path (add/delete) from a lost tree without inventing
        # content. The tree check is needed only after a failed blob read.
        result = run_git(root, ["show", f"{tree}:{rel}"])
        if result.returncode:
            exists = run_git(root, ["cat-file", "-t", tree])
            if exists.returncode or exists.stdout.strip() != "tree":
                raise FileNotFoundError(
                    f"Git can no longer produce comparison tree {tree} in {root}"
                )
            sides.append(None)
        else:
            sides.append(
                {
                    "content": result.stdout.encode("utf-8", "surrogateescape").decode(
                        "utf-8", "replace"
                    )
                }
            )
    return {
        "scope": contract.leaf_id,
        "kind": "memory" if memory else "code",
        "path": rel,
        "language": language_for(Path(rel)),
        "before": sides[0],
        "after": sides[1],
    }
