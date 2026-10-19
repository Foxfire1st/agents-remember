from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel import filesystem
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.onboarding_doc import (
    markdown_table_cells,
)
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.models.knowledge_files.documents import dependent_document_paths
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.modules.git import (
    require_git,
    worktree_candidate_tree,
)
from agents_remember.worktrees.modules.models import (
    EntityFingerprintRefreshPlan,
    EntityFingerprintRequiredItem,
    EntityFingerprintRow,
    OnboardingRefreshPlan,
)
from agents_remember.worktrees.modules.onboarding_trace import (
    INCOMPLETE_CODE,
    OnboardingTraceResult,
    OnboardingTraceSides,
    onboarding_trace_result,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

ENTITY_FINGERPRINT_ALGORITHM = "git-blob-set-v1"
ENTITIES_CATALOG_NAME = "entities.md"
ENTITY_EVIDENCE_CHECK = "entity-evidence"
ENTITY_EVIDENCE_ABSENT = "entity-evidence-absent"
_EVIDENCE_CLEARING = "re-point the row's evidence list to files the candidate holds"
_FINGERPRINT_CLEARING = (
    "run the c-05-create-or-update-onboarding-files skill, then rerun the closeout"
)


def entities_catalog_path(context) -> Path:
    """The repository entity catalog this context's closeout refreshes."""

    return context.onboarding_root / ENTITIES_CATALOG_NAME


def _tree_holds(repository: Path, tree: str, source_path: str) -> bool:
    """Whether ``tree`` of ``repository`` holds ``source_path`` (MIK-R178 rule 1).

    The check reads the tree by its id (``<tree>:<path>``), never ``HEAD``, so the candidate tree
    is asked whatever the worktree or a later commit holds. A path Git cannot resolve is absent;
    ``run_git`` itself reports a repository it cannot read, and the caller names that failure.
    """

    result = run_git(repository, ["rev-parse", "--verify", "--quiet", f"{tree}:{source_path}"])
    return result.returncode == 0 and bool(result.stdout.strip())


def _tree_paths(repository: Path, tree: str) -> frozenset[str]:
    """Every path ``tree`` holds, read once, so a whole catalog is checked in one Git call.

    An unreadable tree raises by name: it is reported as the step it belongs to, with the tree and
    the repository, never as a silent "everything is absent".
    """

    result = run_git(repository, ["ls-tree", "-r", "-z", "--name-only", tree])
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git ls-tree {tree} failed"
        raise RuntimeError(
            "the closeout's memory refresh cannot read the code candidate tree: step "
            f"entity-fingerprint evidence check, tree {tree} in {repository.as_posix()} ({detail})"
        )
    return frozenset(name for name in result.stdout.split("\0") if name)


def sidecar_onboarding_path(onboarding_root: Path, source_path: str) -> Path:
    return onboarding_root / f"{source_path}.md"


def onboarding_refresh_plan_for_context(
    context,
    changed_paths: list[str],
    *,
    working_paths: list[str] | None = None,
    dependent_documents: frozenset[str] | None = None,
) -> OnboardingRefreshPlan:
    """Plan sidecar refreshes for changed sources, split by responsibility tier.

    ``working_paths`` names the paths changed in the working tree; paths outside
    it arrived via commits (merges, pre-committed slices). Working paths without
    onboarding block closeout (``missing``/``unsupported``); committed-range
    paths without onboarding are collected as ``unonboarded`` and never block,
    so transported history cannot force whole-repository onboarding. ``None``
    keeps the strict legacy semantics: every changed path is treated as working.
    """
    if dependent_documents is None:
        memory_root = context.onboarding_root.parent
        folder = filesystem.extended_path(memory_root / "knowledge/dependents")
        dependent_documents = dependent_document_paths(
            {
                path.relative_to(
                    filesystem.extended_path(memory_root)
                ).as_posix(): path.read_bytes()
                for path in folder.glob("*.json")
            }
        )
    working = set(changed_paths if working_paths is None else working_paths)
    required: list[dict[str, str]] = []
    missing: list[str] = []
    unsupported: list[str] = []
    unonboarded: list[str] = []
    for source_path in changed_paths:
        if source_path in dependent_documents:
            continue
        storage = resolver.resolve_storage_for_source(
            source_path, context.storage, context.code_repository_name
        )
        if storage == "disabled":
            continue
        if not resolver.is_sidecar_storage(storage):
            (unsupported if source_path in working else unonboarded).append(source_path)
            continue
        onboarding_path = sidecar_onboarding_path(context.onboarding_root, source_path)
        if not filesystem.exists(onboarding_path):
            (missing if source_path in working else unonboarded).append(source_path)
            continue
        required.append(
            {
                "source_path": source_path,
                "onboarding_file": onboarding_path.as_posix(),
            }
        )
    return {
        "required": required,
        "missing": missing,
        "unsupported": unsupported,
        "unonboarded": unonboarded,
    }


def normalized_table_cell(cell: str) -> str:
    return re.sub(r"[^a-z0-9]", "", cell.lower())


def refresh_route_indexes_for_context(context) -> dict[str, Any]:
    result = build_route_indexes(
        code_root=context.code_repository_root,
        onboarding_root=context.onboarding_root,
        repository=context.code_repository_name,
        storage=context.storage,
        dry_run=False,
    )
    return result.to_dict()


def route_index_refresh_plan_for_context(context) -> dict[str, Any]:
    result = build_route_indexes(
        code_root=context.code_repository_root,
        onboarding_root=context.onboarding_root,
        repository=context.code_repository_name,
        storage=context.storage,
        dry_run=True,
    )
    return result.to_dict()


def _fingerprint_table_header(lines: list[str]) -> tuple[dict[str, int], int] | None:
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = markdown_table_cells(line)
        normalized = {normalized_table_cell(cell): position for position, cell in enumerate(cells)}
        required = {"entity", "algorithm", "fingerprint", "evidencepaths"}
        if required.issubset(normalized):
            return {key: normalized[key] for key in required}, index + 2
    return None


def _fingerprint_row(line: str, index: int, header: dict[str, int]) -> EntityFingerprintRow | None:
    if not line.lstrip().startswith("|"):
        return None
    cells = markdown_table_cells(line)
    if len(cells) <= max(header.values()):
        return None
    algorithm = cells[header["algorithm"]].strip("`")
    evidence_cell = cells[header["evidencepaths"]]
    return {
        "line_index": index,
        "entity": cells[header["entity"]].strip("`"),
        "algorithm": algorithm,
        "fingerprint": cells[header["fingerprint"]].strip("`"),
        "evidence_paths": re.findall(r"`([^`]+)`", evidence_cell),
    }


def parse_entity_fingerprint_rows(catalog_path: Path) -> list[EntityFingerprintRow]:
    if not filesystem.exists(catalog_path):
        return []
    return parse_entity_fingerprint_text(filesystem.read_text(catalog_path, encoding="utf-8"))


def parse_entity_fingerprint_text(text: str) -> list[EntityFingerprintRow]:
    """The catalog's fingerprint rows, read from text that need not be a file (MIK-R178 rule 3)."""

    lines = text.splitlines()
    table = _fingerprint_table_header(lines)
    if table is None:
        return []
    header, start_index = table

    rows: list[EntityFingerprintRow] = []
    for index in range(start_index, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            break
        row = _fingerprint_row(line, index, header)
        if row is not None:
            rows.append(row)
    return rows


def _row_is_edited(
    entity: str, evidence_paths: list[str], base_evidence: Mapping[str, Sequence[str]] | None
) -> bool:
    """Whether the row is new, or its evidence list differs from its row at the memory base."""

    if base_evidence is None:
        return False
    base = base_evidence.get(entity)
    return base is None or sorted(base) != sorted(evidence_paths)


def entity_fingerprint_refresh_plan_for_context(
    context,
    changed_paths: list[str],
    *,
    base_evidence: Mapping[str, Sequence[str]] | None = None,
) -> EntityFingerprintRefreshPlan:
    """The rows the closeout recomputes, and the rows it cannot recompute (MIK-R178 rule 3).

    A row is in scope when its evidence touches a changed path, when its evidence list differs
    from the same row at the leaf's memory base (``base_evidence``), or when the base holds no
    such row. ``None`` keeps the changed-path rule alone, for a caller with no base to compare
    against. A row in scope whose algorithm is not :data:`ENTITY_FINGERPRINT_ALGORITHM` is
    ``unsupported`` rather than planned.
    """

    changed = set(changed_paths)
    catalog_path = entities_catalog_path(context)
    required: list[EntityFingerprintRequiredItem] = []
    unsupported: list[dict[str, str]] = []
    for row in parse_entity_fingerprint_rows(catalog_path):
        entity = str(row["entity"])
        evidence_paths = list(row["evidence_paths"])
        affected_paths = sorted(changed.intersection(evidence_paths))
        if not affected_paths and not _row_is_edited(entity, evidence_paths, base_evidence):
            continue
        if row["algorithm"] != ENTITY_FINGERPRINT_ALGORITHM:
            unsupported.append({"entity": entity, "algorithm": str(row["algorithm"])})
            continue
        required.append(
            {
                "entity": entity,
                "onboarding_file": catalog_path.as_posix(),
                "evidence_paths": evidence_paths,
                "affected_paths": affected_paths,
            }
        )
    return {
        "required": required,
        "unsupported": unsupported,
    }


def entity_fingerprint_plan_problems(
    context, plan: EntityFingerprintRefreshPlan, *, code_tree: str
) -> list[str]:
    """Every entity-row problem the closeout can know before the code commit (MIK-R178 rules 1-2).

    One line per entity row, naming the catalog file, the row and every remedy that applies.
    **Every row of the catalog is evidence-checked**, planned or not: a row whose evidence the
    code candidate does not hold is a fault whether the refresh would recompute it or not, so a
    leaf that deletes the only file a row cites is refused here rather than closing out with
    broken evidence (L176-R1-F01). A row is also checked for the defects that stop a recompute --
    an unsupported algorithm, a missing fingerprint value -- and those are reported **beside** its
    absent paths, never instead of them (L176-R1-F03).
    """

    catalog_path = entities_catalog_path(context)
    tree_paths = _tree_paths(context.code_repository_root, code_tree)
    planned = {str(item["entity"]) for item in plan["required"]}
    unsupported = {str(item["entity"]): str(item["algorithm"]) for item in plan["unsupported"]}
    lines: list[str] = []
    for row in parse_entity_fingerprint_rows(catalog_path):
        entity = str(row["entity"])
        details: list[str] = []
        if entity in unsupported:
            details.append(
                f"uses the unsupported algorithm {unsupported[entity]!r}; set the row to "
                f"{ENTITY_FINGERPRINT_ALGORITHM} and {_FINGERPRINT_CLEARING}"
            )
        if entity in planned and not str(row["fingerprint"]):
            details.append(f"has no fingerprint value; {_FINGERPRINT_CLEARING}")
        absent = sorted(
            source_path for source_path in row["evidence_paths"] if source_path not in tree_paths
        )
        if absent:
            details.append(
                f"cites evidence paths the code candidate does not hold: {', '.join(absent)}; "
                f"{_EVIDENCE_CLEARING}"
            )
        if details:
            lines.append(f"{catalog_path.as_posix()} row {entity!r} " + "; ".join(details))
    return lines


def entity_fingerprint_plan_refusal(
    context, plan: EntityFingerprintRefreshPlan, *, code_tree: str
) -> str | None:
    """The entity half of the closeout's candidate verdict, as one message (MIK-R178).

    ``plan`` is the refresh the closeout will run and ``code_tree`` the accepted code candidate
    tree, read by its id, so the answer depends on the code candidate and the memory worktree and
    on nothing the closeout writes later. ``None`` when every row's evidence holds and the refresh
    would commit every planned row.
    """

    lines = entity_fingerprint_plan_problems(context, plan, code_tree=code_tree)
    if not lines:
        return None
    header = (
        "external-memory closeout refuses this candidate before the code commit: the memory "
        f"refresh cannot run from the code candidate tree {code_tree}"
    )
    return "\n".join([header, *(f"- {line}" for line in lines)])


def route_index_refresh_problems(context) -> list[str]:
    """The route-index refresh's deterministic refusals, asked from the two trees (MIK-R178 rule 1).

    The refresh renders every route's cache from the code candidate and the memory worktree. An
    input it cannot read -- a stale cache held as non-UTF-8 bytes, a route overview it cannot
    render -- refuses the closeout *after* the code commit when nothing asks earlier
    (L176-R1-F02). This asks the refresh's own planner, the same function, so the class cannot
    drift from what the refresh does, and names the step, the offending path and the error rather
    than a bare codec message.
    """

    try:
        route_index_refresh_plan_for_context(context)
    except (OSError, UnicodeError, RuntimeError) as error:
        return [
            "the route-index refresh cannot read its inputs: step route-index plan, "
            f"{_unreadable_route_index(context.onboarding_root)}, code root "
            f"{context.code_repository_root.as_posix()}, memory root "
            f"{context.onboarding_root.as_posix()} ({type(error).__name__}: {error})"
        ]
    return []


def _unreadable_route_index(onboarding_root: Path) -> str:
    """The route cache the refresh cannot read, or a description of the inputs it was given."""

    for path in sorted(onboarding_root.rglob("*.index.json")):
        if not path.is_file():
            continue
        try:
            path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            return f"{path.as_posix()} ({type(error).__name__})"
    return "the route-index inputs"


def entity_evidence_at_base(onboarding_root: Path, commit: str) -> dict[str, list[str]] | None:
    """Each entity row's evidence list at the leaf's memory base (MIK-R178 rule 3).

    ``None`` when the caller names no base commit: no row is compared with a base, and only the
    changed-path rule applies. ``{}`` when the base holds no catalog at all: every row of the
    leaf's catalog is new there. A commit Git cannot read refuses by name, so an unreadable input
    never reads as a new catalog.
    """

    if not commit:
        return None
    repository = onboarding_root.parent
    try:
        relative = (onboarding_root / ENTITIES_CATALOG_NAME).relative_to(repository).as_posix()
    except ValueError:
        return None
    if not _tree_holds(repository, commit, relative):
        return {}
    result = run_git(repository, ["show", f"{commit}:{relative}"])
    if result.returncode != 0:
        raise RuntimeError(
            "the closeout's memory refresh cannot read the entity catalog at the leaf's memory "
            f"base: step entity-fingerprint base read, {relative} at commit {commit} "
            f"({result.stderr.strip() or 'git show failed'})"
        )
    return {
        str(row["entity"]): list(row["evidence_paths"])
        for row in parse_entity_fingerprint_text(result.stdout)
    }


def compute_git_blob_set_fingerprint(
    repo_root: Path, evidence_paths: list[str], *, tree: str | None = None
) -> str:
    """``git-blob-set-v1`` over ``evidence_paths`` at ``tree`` (``HEAD`` when no tree is named).

    The closeout passes the code commit's tree, so the value is over the tree the leaf commits and
    not whatever ``HEAD`` holds when the refresh runs (MIK-R178 rule 3).
    """

    reference = "HEAD" if tree is None else tree
    lines: list[str] = []
    for source_path in sorted(evidence_paths):
        blob_hash = require_git(repo_root, ["rev-parse", f"{reference}:{source_path}"])
        lines.append(f"{source_path}\0{blob_hash}\n")
    digest = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _commit_tree(repository: Path, commit: str) -> str:
    """The tree of ``commit``, or a refusal naming the step, the commit and the repository."""

    try:
        return require_git(repository, ["rev-parse", "--verify", f"{commit}^{{tree}}"])
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "the closeout's memory refresh cannot read the code commit's tree: step "
            f"entity-fingerprint refresh, commit {commit} in {repository.as_posix()} "
            f"({type(error).__name__}: {error})"
        ) from error


def _row_fingerprint(
    repository: Path,
    item: EntityFingerprintRequiredItem,
    *,
    code_tree: str,
    catalog_path: Path,
) -> str:
    """One row's value at the code commit's tree, or a refusal naming step, paths and tree."""

    evidence_paths = list(item["evidence_paths"])
    try:
        return compute_git_blob_set_fingerprint(repository, evidence_paths, tree=code_tree)
    except (RuntimeError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "the closeout's memory refresh failed while computing a git-blob-set-v1 value: step "
            f"entity-fingerprint refresh, {catalog_path.as_posix()} row {item['entity']!r}, "
            f"evidence paths {', '.join(sorted(evidence_paths))} at code tree {code_tree} "
            f"({type(error).__name__}: {error})"
        ) from error


def refresh_entity_fingerprints_for_context(
    context, changed_paths: list[str], *, code_commit: str, memory_base_commit: str = ""
) -> list[dict[str, object]]:
    """Recompute the entity fingerprints this leaf changed, over the code commit's own tree.

    MIK-R178 rule 3: a row is recomputed when its evidence touches a changed path, when its
    evidence list differs from the same row at the leaf's memory base (``memory_base_commit``),
    and when the row is new. The value is ``git-blob-set-v1`` over the row's evidence set at the
    tree of ``code_commit``. Rule 2: a row the candidate cannot support refuses here by name,
    naming the step, the path and the tree it read, rather than with a bare Git message.
    """

    catalog_path = entities_catalog_path(context)
    base_evidence = entity_evidence_at_base(context.onboarding_root, memory_base_commit)
    plan = entity_fingerprint_refresh_plan_for_context(
        context, changed_paths, base_evidence=base_evidence
    )
    code_tree = _commit_tree(context.code_repository_root, code_commit)
    refusal = entity_fingerprint_plan_refusal(context, plan, code_tree=code_tree)
    if refusal is not None:
        raise RuntimeError(refusal)
    required = list(plan["required"])
    if not required:
        return []

    lines = filesystem.read_text(catalog_path, encoding="utf-8").splitlines()
    refreshed: list[dict[str, object]] = []
    rows_by_entity = {
        str(row["entity"]): row for row in parse_entity_fingerprint_rows(catalog_path)
    }
    for item in required:
        entity = str(item["entity"])
        row = rows_by_entity[entity]
        fingerprint = _row_fingerprint(
            context.code_repository_root, item, code_tree=code_tree, catalog_path=catalog_path
        )
        line_index = int(row["line_index"])
        old_fingerprint = str(row["fingerprint"])
        if not old_fingerprint:
            raise RuntimeError(
                f"external-memory closeout requires entity fingerprint values before memory "
                f"commit; {catalog_path.as_posix()} row {entity!r} is missing a fingerprint. "
                "Run the c-05-create-or-update-onboarding-files skill, then rerun closeout."
            )
        lines[line_index] = lines[line_index].replace(old_fingerprint, fingerprint, 1)
        refreshed.append(
            {
                "entity": entity,
                "onboarding_file": catalog_path.as_posix(),
                "fingerprint": fingerprint,
                "affected_paths": item["affected_paths"],
            }
        )
    filesystem.write_text(catalog_path, "\n".join(lines) + "\n", encoding="utf-8")
    return refreshed


def entity_evidence_paths(context) -> list[str]:
    """Every evidence path the entity catalog names, in one sorted list (MIK-R178 rule 4)."""

    paths = {
        source_path
        for row in parse_entity_fingerprint_rows(entities_catalog_path(context))
        for source_path in row["evidence_paths"]
    }
    return sorted(paths)


def entity_evidence_findings(
    context, evidence_blobs: Mapping[str, str | None]
) -> list[dict[str, str]]:
    """Entity rows citing a path the code candidate does not hold (MIK-R178 rule 4).

    ``evidence_blobs`` maps every evidence path of the catalog (:func:`entity_evidence_paths`) to
    its blob at the leaf's code candidate, and a path the candidate lacks to ``None``. Each
    finding names the catalog file, the entity row and the path, and is actionable: the curator
    re-points the row's evidence list. A row with no evidence path reports nothing.
    """

    catalog_path = entities_catalog_path(context)
    findings: list[dict[str, str]] = []
    for row in parse_entity_fingerprint_rows(catalog_path):
        entity = str(row["entity"])
        for source_path in row["evidence_paths"]:
            if evidence_blobs.get(source_path) is not None:
                continue
            findings.append(
                {
                    "check": ENTITY_EVIDENCE_CHECK,
                    "code": ENTITY_EVIDENCE_ABSENT,
                    "path": catalog_path.as_posix(),
                    "message": (
                        f"entity row {entity!r} cites evidence path {source_path!r} the code "
                        f"candidate does not hold; {_EVIDENCE_CLEARING}"
                    ),
                }
            )
    return findings


def entity_evidence_findings_for_candidate(context, code_worktree: Path) -> list[dict[str, str]]:
    """MIK-R178 rule 4 over ``code_worktree``'s own candidate tree: never raises.

    The curator's memory-quality check reads the leaf's code candidate -- the same capture the
    closeout commits, uncommitted changes included -- so a row naming a path the leaf deletes or
    renames is reported while the curator can still re-point it. A candidate Git cannot read is one
    finding naming that input, so the check never lapses into silence.
    """

    paths = entity_evidence_paths(context)
    if not paths:
        return []
    try:
        blobs = _candidate_blobs(code_worktree, paths)
    except Exception as error:  # an unreadable candidate is one finding, never a silent pass
        return [
            {
                "check": ENTITY_EVIDENCE_CHECK,
                "code": INCOMPLETE_CODE,
                "path": "",
                "message": (
                    f"the entity evidence check cannot read the code candidate's blobs: {error}"
                ),
            }
        ]
    return entity_evidence_findings(context, blobs)


def _candidate_blobs(code_worktree: Path, paths: Sequence[str]) -> dict[str, str | None]:
    """Each path's blob at the worktree's candidate tree, ``None`` for a path it does not hold."""

    with TemporaryDirectory(prefix="ar-entity-evidence-") as scratch:
        tree = worktree_candidate_tree(code_worktree, Path(scratch) / "index")
    blobs: dict[str, str | None] = {}
    for source_path in paths:
        result = run_git(
            code_worktree, ["rev-parse", "--verify", "--quiet", f"{tree}:{source_path}"]
        )
        blobs[source_path] = result.stdout.strip() if result.returncode == 0 else None
    return blobs


def onboarding_refresh_plan(
    contract: WorktreeContract,
    changed_paths: list[str],
    *,
    working_paths: list[str] | None = None,
) -> OnboardingRefreshPlan:
    from agents_remember.application.knowledge_worklist.leaf_completion import (  # noqa: PLC0415
        leaf_onboarding_trace_sides,
    )

    sides = leaf_onboarding_trace_sides(contract)
    documents = None
    if sides is not None:
        if sides.incomplete is not None:
            raise RuntimeError(
                f"the onboarding refresh cannot read its memory sides: {sides.incomplete}"
            )
        documents = dependent_document_paths(sides.base) | dependent_document_paths(sides.candidate)
    return onboarding_refresh_plan_for_context(
        contract_context(contract),
        changed_paths,
        working_paths=working_paths,
        dependent_documents=documents,
    )


def entity_fingerprint_refresh_plan(
    contract: WorktreeContract, changed_paths: list[str]
) -> EntityFingerprintRefreshPlan:
    """The closeout's own plan, compared with the catalog at the leaf's memory base."""

    context = contract_context(contract)
    return entity_fingerprint_refresh_plan_for_context(
        context,
        changed_paths,
        base_evidence=entity_evidence_at_base(context.onboarding_root, contract.memory_base_commit),
    )


def _require_onboarded_sources(
    context,
    changed_paths: list[str],
    working_paths: list[str] | None,
    *,
    dependent_documents: frozenset[str],
) -> OnboardingRefreshPlan:
    """Today's missing/unsupported sidecar refusal, shared by both gates unchanged."""
    plan = onboarding_refresh_plan_for_context(
        context,
        changed_paths,
        working_paths=working_paths,
        dependent_documents=dependent_documents,
    )
    details: list[str] = []
    if plan["missing"]:
        details.append(f"missing sidecar onboarding for: {', '.join(plan['missing'])}")
    if plan["unsupported"]:
        details.append(f"unsupported onboarding storage for: {', '.join(plan['unsupported'])}")
    if details:
        raise RuntimeError(
            "external-memory closeout requires current onboarding for changed source files before memory commit; "
            + "; ".join(details)
            + ". Run the c-05-create-or-update-onboarding-files skill, then rerun closeout."
        )
    return plan


@dataclass(frozen=True)
class OnboardingTraceInputs:
    """What both gate entries read about a change, beside the memory sides (MIK-R53, MIK-R177).

    ``code_blobs`` maps every changed source path to its blob at the code candidate (``None`` for a
    deleted file). ``identical_changes`` is the leaf's worklist run's ``identicalChanges``: the gate
    groups identical copies exactly as ``worklist_onboarding`` does, so an item a row answers in the
    worklist is the item the gate raises. It carries no default, so no caller judges ungrouped
    items where the worklist grouped identical copies. ``working_paths`` names the changed paths
    that are uncommitted, which the missing-sidecar refusal reads; ``None`` keeps its strict legacy
    meaning, every changed path treated as working.
    """

    code_blobs: Mapping[str, str | None]
    identical_changes: Mapping[str, Sequence[str]]
    working_paths: list[str] | None = None


class IdenticalChangesUnreadable(ValueError):
    """A worklist run's ``identicalChanges`` is not a group map (MIK-R177)."""


def identical_changes_from_run(document: Mapping[str, Any]) -> dict[str, list[str]]:
    """The run's ``identicalChanges`` as ordered source lists, or a named refusal (MIK-R177).

    The one reader both gate entries use. A complete run's groups are
    ``{representative source path: [source path, ...]}``; an entry that is not a list of non-empty
    source paths is a malformed grouping input and is refused by name. Nothing is dropped, coerced
    or replaced by a singleton, so a malformed group can never shorten itself into an ungrouped
    item. A genuine empty mapping stays empty, and each group keeps the run's order.
    """

    groups = document.get("identicalChanges")
    if not isinstance(groups, Mapping):
        raise IdenticalChangesUnreadable("the run names no identicalChanges mapping")
    parsed: dict[str, list[str]] = {}
    for representative, members in groups.items():
        if not isinstance(representative, str) or not representative:
            raise IdenticalChangesUnreadable(
                f"an identicalChanges key is not a source path: {representative!r}"
            )
        if not isinstance(members, (list, tuple)):
            raise IdenticalChangesUnreadable(
                f"identicalChanges[{representative!r}] is not a list of source paths: {members!r}"
            )
        if not all(isinstance(one, str) and one for one in members):
            raise IdenticalChangesUnreadable(
                f"identicalChanges[{representative!r}] holds a member that is not a source path: "
                f"{list(members)!r}"
            )
        if not members:
            raise IdenticalChangesUnreadable(
                f"identicalChanges[{representative!r}] names no member"
            )
        if representative not in members:
            raise IdenticalChangesUnreadable(
                f"identicalChanges[{representative!r}] does not name its representative among its "
                f"members: {list(members)!r}"
            )
        parsed[representative] = list(members)
    return parsed


def onboarding_trace_gate_for_context(
    context,
    changed_paths: list[str],
    sides: OnboardingTraceSides,
    inputs: OnboardingTraceInputs,
) -> tuple[OnboardingTraceResult, list[dict[str, str]]]:
    """MIK-R30 on a converted tree, for the curator's memory-quality run: never raises.

    Returns the gate's result and its repair findings: one per missing trace (rule 6), one per
    unreadable input, and one for changed sources without onboarding (today's refusal, unchanged).
    ``inputs`` carries the candidate's blobs and the worklist's identical-copy groups (MIK-R177).
    """
    result = onboarding_trace_result(
        context,
        changed_paths,
        sides,
        code_blobs=inputs.code_blobs,
        source_groups=inputs.identical_changes,
    )
    findings = result.repair_findings()
    try:
        documents = dependent_document_paths(sides.base) | dependent_document_paths(sides.candidate)
        _require_onboarded_sources(
            context,
            changed_paths,
            inputs.working_paths,
            dependent_documents=documents,
        )
    except RuntimeError as error:
        findings.insert(
            0,
            {
                "check": "memory-refresh-attestations",
                "code": "memory-refresh-attestation-failed",
                "path": "",
                "message": f"sidecar onboarding: {error}",
            },
        )
    return result, findings
