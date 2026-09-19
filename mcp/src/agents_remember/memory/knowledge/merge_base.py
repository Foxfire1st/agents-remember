"""Resolve which dataset is the common base, and prove it when Git ancestry is the authority.

A merge is defined by its base. Two sides that disagree about where they started have no common
baseline to diff against, and the failure mode this module exists to prevent is the quiet one:
picking *some* commit -- an arbitrary ``git merge-base`` result, or ``HEAD`` as a stand-in -- and
producing a delta against a base neither side descends from.

So the base claim is a closed union and neither member is a guess:

* :class:`SuppliedGitBase` -- the caller resolved the base itself and names the exact commit and
  tree. No ancestry command runs; the recorded fact is that this was a caller decision.
* :class:`ResolvedGitBase` -- the caller claims one commit is the unique common base and asks for
  the evidence. The claim is verified against the repository: the commit must be an ancestor of both
  sides, and the history must have exactly one common base, which must be the claimed commit.

Zero common bases, several common bases, or a single common base that is not the claimed commit all
refuse. The dataset side of resolution is the same closed contract the rest of the package uses:
each input is read-only, its logical identity is re-read and compared with the identity the caller
admitted, and its declared structure is checked against the supported schema generation -- all of it
*before* any session, lock or destination resource exists.

Nothing here reads a Git object to decide what a dataset *is*. The Git commands answer one question
about commit ancestry, and the datasets are identified by their own logical digest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import apsw

from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge import logical
from agents_remember.memory.knowledge.merge_schema import (
    require_supported_structure,
    selected_generation,
)
from agents_remember.memory.knowledge.refusals import RefusalFacts, refusal
from agents_remember.models.knowledge.merge import (
    MergeBaseRequest,
    MergeBaseResolution,
    MergeInput,
    ResolvedGitBase,
)
from agents_remember.models.knowledge.result import (
    KnowledgeOperation,
    KnowledgeRefusal,
)

# The two operations this module's refusals belong to.
RESOLVE_OPERATION: KnowledgeOperation = "resolve_merge_base"
MERGE_OPERATION: KnowledgeOperation = "merge_knowledge_datasets"

# The command the ancestry evidence is read through. Each entry is one question and one answer, so
# the adjudication below reads as a sequence of facts rather than as argv construction.
_GIT_IS_REPOSITORY = ("rev-parse", "--is-inside-work-tree")


@dataclass(frozen=True)
class BaseResolution:
    """The outcome of one base resolution: the proven resolution, or the refusal that replaced it."""

    state: str
    resolution: MergeBaseResolution | None = None
    refusal: KnowledgeRefusal | None = None

    @property
    def resolved(self) -> bool:
        """Whether a resolution was established."""

        return self.state == "resolved"


def resolve_merge_base(request: MergeBaseRequest) -> BaseResolution:
    """Prove the three input identities, their structure and the Git base claim.

    The order is deliberate: the datasets are identified and structurally checked first, because a
    Git question about a commit is not evidence about a file, and a caller that handed over a
    dataset this schema cannot read should hear that rather than a merge-base verdict.
    """

    materialized = _materialize_inputs(request)
    if isinstance(materialized, KnowledgeRefusal):
        return BaseResolution(state="refused", refusal=materialized)
    selected = selected_generation(
        {role: item.database_path for role, item in materialized.items()}, RESOLVE_OPERATION
    )
    if isinstance(selected, KnowledgeRefusal):
        return BaseResolution(state="refused", refusal=selected)
    for role, item in materialized.items():
        structure_refusal = require_supported_structure(
            item.database_path, RESOLVE_OPERATION, role=role, generation=selected
        )
        if structure_refusal is not None:
            return BaseResolution(state="refused", refusal=structure_refusal)
    git_refusal, base_commit = _adjudicate_git_base(request)
    if git_refusal is not None:
        return BaseResolution(state="refused", refusal=git_refusal)
    resolution = MergeBaseResolution(
        repository_id=request.repository.repository_id,
        base_reference=materialized["base"].reference,
        base_identity=materialized["base"].expected_identity,
        left_identity=materialized["left"].expected_identity,
        right_identity=materialized["right"].expected_identity,
        git_base_commit=base_commit,
        uniqueness_checked=isinstance(request.git_base, ResolvedGitBase),
    )
    return BaseResolution(state="resolved", resolution=resolution)


def _materialize_inputs(
    request: MergeBaseRequest,
) -> dict[str, MergeInput] | KnowledgeRefusal:
    """Read each input's identity one at a time and require the admitted one.

    Inputs are materialized **sequentially and without any lock**: the resource lock belongs to the
    destination publication, and taking one here to read an immutable file would hold a lock this
    operation does not need while it still has three more inputs to read.
    """

    materialized: dict[str, MergeInput] = {}
    for item in request.inputs:
        path = Path(item.database_path)
        if not path.is_file():
            return _unavailable(f"the {item.role} dataset is not present", path)
        try:
            observed = logical.dataset_identity(path)
        except (apsw.Error, OSError, ValueError) as error:
            return _unavailable(
                f"the {item.role} dataset could not be read as this schema generation: {error}",
                path,
            )
        if observed != item.expected_identity:
            return refusal(
                "stale_precondition",
                RESOLVE_OPERATION,
                f"the {item.role} dataset is not the identity the request admitted for it",
                facts=RefusalFacts(
                    record_id=str(path),
                    expected=item.expected_identity.logical_digest,
                    observed=observed.logical_digest,
                ),
                next_action=(
                    "Re-read the dataset's logical identity and resolve the base again against "
                    "the identity that is actually there. No input was modified."
                ),
            )
        materialized[item.role] = item
    return materialized


def _adjudicate_git_base(
    request: MergeBaseRequest,
) -> tuple[KnowledgeRefusal | None, str | None]:
    """Return the Git-base refusal and the exact admitted commit, if the caller asked for one."""

    claim = request.git_base
    if not isinstance(claim, ResolvedGitBase):
        return None, None
    root = Path(claim.repository_root)
    if not root.is_absolute():
        return _git_refusal(
            "common_base_unavailable",
            "the repository root must be an absolute path; a relative root would be resolved "
            "against whatever process happened to run the command",
            root,
        ), None
    repository = run_git(root, list(_GIT_IS_REPOSITORY))
    if repository.returncode or repository.stdout.strip() != "true":
        return _git_refusal(
            "common_base_unavailable",
            "the named repository root is not a Git working tree, so no ancestry evidence exists",
            root,
        ), None
    ancestry = _ancestry_refusal(root, claim)
    if ancestry is not None:
        return ancestry, None
    uniqueness = _uniqueness_refusal(root, claim)
    if uniqueness is not None:
        return uniqueness, None
    return None, claim.base_commit_id


def _ancestry_refusal(root: Path, claim: ResolvedGitBase) -> KnowledgeRefusal | None:
    """Refuse a claimed base that is not an ancestor of both sides."""

    for role, commit in (
        ("left", claim.left_commit_id),
        ("right", claim.right_commit_id),
    ):
        if not _is_ancestor(root, claim.base_commit_id, commit):
            return _git_refusal(
                "common_base_mismatch",
                f"the claimed base commit is not an ancestor of the {role} commit",
                root,
                expected=claim.base_commit_id,
                observed=commit,
            )
    return None


def _uniqueness_refusal(root: Path, claim: ResolvedGitBase) -> KnowledgeRefusal | None:
    """Refuse a history with zero common bases, several, or one the request did not claim."""

    bases = _common_bases(root, claim.left_commit_id, claim.right_commit_id)
    if not bases:
        return _git_refusal(
            "common_base_unavailable",
            "the two sides have no common base commit",
            root,
        )
    if len(bases) > 1:
        return _git_refusal(
            "common_base_ambiguous",
            f"the two sides have {len(bases)} common base commits, so none of them is the base",
            root,
            observed=", ".join(bases),
        )
    if bases[0] != claim.base_commit_id:
        return _git_refusal(
            "common_base_mismatch",
            "the unique common base commit is not the commit the request claimed",
            root,
            expected=claim.base_commit_id,
            observed=bases[0],
        )
    return None


def _is_ancestor(root: Path, base_commit: str, commit: str) -> bool:
    """Whether ``base_commit`` is an ancestor of ``commit``, as Git reports it."""

    result = run_git(root, ["merge-base", "--is-ancestor", base_commit, commit])
    return result.returncode == 0


def _common_bases(root: Path, left_commit: str, right_commit: str) -> list[str]:
    """Return every common base commit of the two sides, in Git's own order."""

    result = run_git(root, ["merge-base", "--all", left_commit, right_commit])
    if result.returncode:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _git_refusal(
    code: str,
    detail: str,
    root: Path,
    *,
    expected: str | None = None,
    observed: str | None = None,
) -> KnowledgeRefusal:
    """Build one Git-base refusal with the repository it was decided in."""

    return refusal(
        code,  # type: ignore[arg-type]
        RESOLVE_OPERATION,
        detail,
        facts=RefusalFacts(record_id=str(root), expected=expected, observed=observed),
        next_action=(
            "Resolve the exact common base outside this operation and supply it explicitly, then "
            "merge again. No arbitrary merge-base result and no HEAD substitute is used here."
        ),
    )


def _unavailable(detail: str, path: Path) -> KnowledgeRefusal:
    """Build the refusal for an input that is not there or could not be read."""

    return refusal(
        "selected_input_unavailable",
        RESOLVE_OPERATION,
        detail,
        facts=RefusalFacts(record_id=str(path)),
        next_action=(
            "Supply the exact selected input again. Nothing here recovers a missing dataset from "
            "HEAD, a branch name or a Markdown source."
        ),
    )
