"""Pure task-document, series-contract and frame-manifest helpers for planned comparisons (MIK-R189 rule 4).

Lives in ``tasks`` (rank 9) so both ``serving`` (rank 17) and ``application`` (rank 21) can read
the master line's contract branches, build the in-memory planned comparison record, and format
frame bindings and revision rows without ``serving`` importing ``application``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.recorded_reads import (
    observed_entries,
    observed_json_files,
    observed_path_exists,
    observed_text,
)
from agents_remember.models.knowledge.review_frame import (
    PLANNED_OWNER,
    FrameBinding,
    FrameManifest,
    coverage_summary,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)

if TYPE_CHECKING:
    from agents_remember.canvas.explainer_grades import Grades
    from agents_remember.canvas.explainer_staleness import GradeState

__all__ = [
    "PLANNED_RECORDED_AT",
    "WHOLE_PAGE_PART",
    "PlannedComparisonTips",
    "agreed_revision_number",
    "compose_explainer_send_message",
    "contract_branch_lines",
    "leaf_document",
    "leaf_document_stem",
    "manifest_claims",
    "manifest_parts",
    "master_is_organizational",
    "planned_comparison",
    "resolve_line_commit",
    "resolve_master_contract",
    "retained_revision_numbers",
    "revision_kind_of",
    "revision_row_of",
    "serialize_frame_binding",
    "serialize_grades_and_categories",
]

PLANNED_RECORDED_AT = "planned-master-tip"
WHOLE_PAGE_PART = "whole-page"


@dataclass(frozen=True, slots=True)
class PlannedComparisonTips:
    """The two tips one planned comparison binds: each side's repository, commit and tree."""

    task_id: str
    leaf_id: str
    code_repository: str
    code_commit: str
    code_tree: str
    memory_repository: str
    memory_commit: str
    memory_tree: str


def planned_comparison(
    tips: PlannedComparisonTips | None = None, /, **fields: str
) -> ReviewTreeComparisonRecord:
    """The in-memory planned comparison: number 0, each base equal to its candidate.

    The call passes one frozen :class:`PlannedComparisonTips`; the keyword form is kept only for the
    composition in ``serving.explainer``, which resolves the tips from Git before the object exists.
    """

    planned = (
        tips
        if tips is not None
        else PlannedComparisonTips(
            task_id=fields["task_id"],
            leaf_id=fields["leaf_id"],
            code_repository=fields["code_repository"],
            code_commit=fields["code_commit"],
            code_tree=fields["code_tree"],
            memory_repository=fields["memory_repository"],
            memory_commit=fields["memory_commit"],
            memory_tree=fields["memory_tree"],
        )
    )
    return ReviewTreeComparisonRecord(
        task_id=planned.task_id,
        leaf_id=planned.leaf_id,
        number=0,
        code_base=ReviewTreeSide(
            repository=planned.code_repository, tree=planned.code_tree, commit=planned.code_commit
        ),
        code_candidate=ReviewTreeSide(
            repository=planned.code_repository, tree=planned.code_tree, commit=planned.code_commit
        ),
        memory_base=ReviewTreeSide(
            repository=planned.memory_repository,
            tree=planned.memory_tree,
            commit=planned.memory_commit,
        ),
        memory_candidate=ReviewTreeSide(
            repository=planned.memory_repository,
            tree=planned.memory_tree,
            commit=planned.memory_commit,
        ),
        recorded_at=PLANNED_RECORDED_AT,
    )


def _master_document(coordination_root: Path, repository_id: str, master: str) -> dict[str, Any]:
    named = coordination_root / "tasks" / repository_id / master / "task.json"
    if not named.is_file():
        return {}
    try:
        payload = json.loads(named.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_master_contract(
    coordination_root: Path, repository_id: str, master: str
) -> Path | None:
    """The master line's series contract: declared ``seriesContractPath`` first, then fallback."""

    folder = coordination_root / "tasks" / repository_id / master
    document = _master_document(coordination_root, repository_id, master)
    declared = document.get("seriesContractPath")
    if isinstance(declared, str) and declared:
        for candidate in (Path(declared), folder / declared):
            if candidate.is_file():
                return candidate
    fallback = folder / "series-contract.md"
    return fallback if fallback.is_file() else None


def master_is_organizational(coordination_root: Path, repository_id: str, master: str) -> bool:
    """Whether the master's task document declares organizational nature rather than atomic.

    The canonical cell is the task document's ``executionNature`` (``tasks/document.py``); a
    nature-less master is atomic by the atomic-sequential default, so legacy documents need no
    migration. The two older spellings are read only for documents that predate the cell.
    """

    document = _master_document(coordination_root, repository_id, master)
    if document.get("executionNature") == "organizational":
        return True
    topology = document.get("executionTopology")
    if isinstance(topology, dict) and topology.get("masterNature") == "organizational":
        return True
    return document.get("workflowKind") == "organizational"


def contract_branch_lines(
    text: str, *, organizational: bool = False
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Each side's candidate branches from the series contract (MIK-R189 rule 4, MIK-R186 rule 4)."""

    sides: dict[str, dict[str, str]] = {"code": {}, "memory": {}}
    seen_side: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("code:"):
            seen_side = "code"
        elif stripped.startswith("memory:"):
            seen_side = "memory"
        elif seen_side is not None:
            for key in ("source_branch", "work_branch"):
                if stripped.startswith(f"{key}:"):
                    value = stripped.split(":", 1)[1].strip()
                    if value:
                        sides[seen_side][key] = value
    order = ("source_branch", "work_branch") if organizational else ("work_branch", "source_branch")

    def lines_for(side: str) -> tuple[str, ...]:
        named = [sides[side][key] for key in order if sides[side].get(key)]
        return tuple(dict.fromkeys(named)) or ("HEAD",)

    return (lines_for("code"), lines_for("memory"))


def resolve_line_commit(repository: Path, lines: tuple[str, ...]) -> str | None:
    """Resolve the first existing branch/ref in ``lines`` to its tip commit SHA."""

    for line in lines:
        try:
            result = run_git(repository, ["rev-parse", "--verify", "--quiet", line])
        except (OSError, RuntimeError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def serialize_frame_binding(binding: FrameBinding | None) -> dict[str, Any] | None:
    if binding is None:
        return None
    dumped = binding.model_dump(mode="json")
    is_planned_owner = binding.comparison_owner == PLANNED_OWNER
    kind = "planned" if is_planned_owner else "recorded"
    label = "planned" if is_planned_owner else f"#{binding.comparison_number}"
    return {
        **dumped,
        "kind": kind,
        "owner": kind,
        "number": binding.comparison_number,
        "comparison": binding.comparison_number,
        "reference": binding.comparison_reference,
        "label": label,
    }


def _find_leaf_document(task_root: Path, leaf_id: str) -> tuple[str, dict[str, Any]] | None:
    # The listing and every document read before the match are inputs of the answer (D570).
    if not observed_path_exists(task_root) or not task_root.is_dir():
        return None
    for path in observed_json_files(task_root):
        try:
            payload = json.loads(observed_text(path))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("id") == leaf_id:
            return path.stem, payload
    return None


def leaf_document(task_root: Path, leaf_id: str) -> dict[str, Any] | None:
    found = _find_leaf_document(task_root, leaf_id)
    return None if found is None else found[1]


def leaf_document_stem(task_root: Path, leaf_id: str) -> str | None:
    found = _find_leaf_document(task_root, leaf_id)
    return None if found is None else found[0]


def agreed_revision_number(task_root: Path, leaf_id: str) -> int | None:
    """The agreed revision's number, through the one canonical agreement resolver.

    The pin is what the validated agreement identifies: the strict ``intentReview.agreedRevision``
    record (revision id, SHA-256, packet versions; MIK-R189 rule 8) resolved to its retained,
    digest-verified revision. A loose key, an unretained revision or a tampered manifest pins
    nothing here -- the same reading the removal pin and the receipts use.
    """

    document = leaf_document(task_root, leaf_id)
    if document is None:
        return None
    from agents_remember.tasks.document import resolve_agreed_revision  # noqa: PLC0415

    resolved = resolve_agreed_revision(task_root, document, leaf_id=leaf_id)
    if resolved is None or not resolved.digest_verified:
        return None
    return resolved.revision


def retained_revision_numbers(frame_dir: Path) -> list[int]:
    revisions_dir = frame_dir / "revisions"
    try:
        children = observed_entries(revisions_dir)  # the listing is an input (D570)
    except OSError:
        return []
    return sorted(
        (
            int(name)
            for name, kind in children
            if (kind == "directory" or (kind == "link" and (revisions_dir / name).is_dir()))
            and not name.startswith(".")
            and name.isdigit()
        ),
        reverse=True,
    )


def revision_kind_of(manifest: FrameManifest) -> str:
    focus = (manifest.focus or "").strip().lower()
    if focus.startswith(("part:", "focus:", "focused:", "section:")) or "focused" in focus:
        return "focused"
    return "global"


def _format_pick(index: int, pick: dict[str, Any]) -> str:
    title = str(pick.get("title") or pick.get("kind") or f"pick {index}")
    line = f"[{index}] {title}"
    ref = pick.get("reference")
    if isinstance(ref, dict) and ref.get("id"):
        line += f" ({ref['id']})"
    elif isinstance(pick.get("text"), str) and pick["text"].strip():
        line += f": {pick['text'].strip()}"
    note = str(pick.get("note") or "").strip()
    if note:
        line += f" — note: {note}"
    return line


def compose_explainer_send_message(
    message: str,
    picks: object,
    stale_part_ids: object,
    stale_grades: object,
) -> str:
    sections: list[str] = []
    if message.strip():
        sections.append(message.strip())
    if isinstance(picks, list):
        sections.extend(
            _format_pick(index, pick)
            for index, pick in enumerate(picks, start=1)
            if isinstance(pick, dict)
        )
    if isinstance(stale_part_ids, list) and stale_part_ids:
        names = [str(item) for item in stale_part_ids if str(item).strip()]
        if names:
            sections.append("Stale parts: " + ", ".join(names))
    if isinstance(stale_grades, list) and stale_grades:
        refs = [str(item) for item in stale_grades if str(item).strip()]
        if refs:
            sections.append("Stale grades: " + ", ".join(refs))
    return "\n".join(sections) if sections else message


def revision_row_of(manifest: FrameManifest, *, agreed: int | None) -> dict[str, Any]:
    number = manifest.revision
    return {
        "revision": number,
        "binding": serialize_frame_binding(manifest.binding),
        "label": (
            "planned"
            if manifest.binding.comparison_owner == PLANNED_OWNER
            else f"#{manifest.binding.comparison_number}"
        ),
        "at": manifest.created_at,
        "kind": revision_kind_of(manifest),
        "focus": manifest.focus,
        "coverage": coverage_summary(manifest.interaction_coverage),
        "agreed": number == agreed,
        "pinned": number == agreed,
    }


def manifest_parts(manifest: FrameManifest) -> list[dict[str, Any]]:
    return [{"id": part.part, "title": part.title} for part in manifest.part_map]


def manifest_claims(manifest: FrameManifest) -> list[dict[str, Any]]:
    holders = {claim: part.part for part in manifest.part_map for claim in part.claims}
    comparison = manifest.binding.comparison_reference
    rows: list[dict[str, Any]] = []
    for claim in manifest.evidence.claims.claims:
        first = claim.bases[0] if claim.bases else None
        row: dict[str, Any] = {
            "id": claim.claim_id,
            "part": holders.get(claim.claim_id, WHOLE_PAGE_PART),
            "basis": claim.classification,
            "text": claim.statement,
        }
        if first is not None and first.reference:
            row["link"] = {
                "block": first.reference,
                "label": claim.subject or first.reference,
                "comparison": comparison,
            }
        rows.append(row)
    return rows


def serialize_grades_and_categories(
    held_grades: Grades, states: Sequence[GradeState]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    categories = [
        {
            "name": cat.name,
            "meaning": getattr(cat, "meaning", None),
            "scale": [{"grade": point.grade, "meaning": point.meaning} for point in cat.scale],
        }
        for cat in held_grades.categories
    ]
    grades = [
        {
            "reference": state.reference,
            "category": state.grade.category,
            "grade": state.grade.grade,
            "reason": state.grade.reason,
            "stale": state.stale,
            "staleReason": state.reason,
            "madeAt": state.grade.made_at,
            "binding": serialize_frame_binding(state.grade.binding),
        }
        for state in states
    ]
    return categories, grades
