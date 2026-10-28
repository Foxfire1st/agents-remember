"""How the explainer's kept tools check the binding they act at (MIK-R186 rule 3, MIK-R211 v5).

The explainer reads with its harness's own tools, so this module serves no read surface. What stays is
what the four kept tools need: the environment's binding (``binding.json``), the caller's seat, and the
check that a call names the binding the environment holds -- a call made at another binding is refused
``binding-moved``, naming the binding's label, its digest and the ``binding.json`` the agent can read.
No answer carries the binding itself (MIK-R211 v5 rule 11).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_answers import (  # re-exported: same objects
    answered,
    refusal,
)
from agents_remember.canvas.explainer_binding import require_current_seat
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    ExplainerRefused,
    ExplainerStateError,
    binding_digest,
    read_binding,
    same_binding,
)
from agents_remember.models.knowledge.review_frame import FrameBinding

__all__ = [
    "answered",
    "binding_claim",
    "object_name",
    "refusal",
    "relative_parts",
    "require_binding",
    "require_claim",
    "require_seat",
    "tree_path",
]

_DRIVE = re.compile(r"^[A-Za-z]:")


# MIK-R204 @ v1 rule 1: the answer boundary lives in a light module now. The names below are the *same
# objects* imported from there, so an existing caller's import and a declaration owner's import cannot
# diverge; `tests/test_explainer_answers.py` asserts that identity rather than the spelling.


def binding_claim(binding: ExplainerBinding) -> str:
    """The full comparison-identity token of one binding, including planned tips (MIK-R186 rule 3)."""

    frame = binding.binding
    if binding.planned is not None:
        return (
            f"planned@{binding.planned.code_tip}:{binding.planned.memory_tip}:"
            f"{binding.planned.code_tree}:{binding.planned.memory_tree}"
        )
    return (
        f"{frame.comparison_owner}#{frame.comparison_number}@"
        f"{frame.code_base_tree}:{frame.code_candidate_tree}:"
        f"{frame.compared_memory_base_tree}:{frame.memory_candidate_tree}"
    )


def require_binding(workspace: Path) -> ExplainerBinding:
    """The environment's binding, or a named refusal: nothing is read without one."""

    try:
        binding = read_binding(workspace)
    except ExplainerStateError as error:
        raise ExplainerRefused("binding-unreadable", str(error)) from error
    if binding is None:
        raise ExplainerRefused(
            "explainer-missing",
            f"the leaf's environment at {workspace} has no binding.json; only a build writes one",
        )
    return binding


def require_seat(workspace: Path, seat: str | None) -> str:
    """The caller's identity, read at construction, must be this leaf's explainer (rule 5).

    The comparison itself lives in :func:`agents_remember.canvas.explainer_binding.require_current_seat`,
    so the seat a read is gated on at dispatch and the seat a grade or a report re-checks under the
    lock are one decision, not two spellings of it.
    """

    return require_current_seat(workspace, seat)


def _exact_claims(binding: ExplainerBinding) -> set[str]:
    """All exact full-identity claim spellings that uniquely identify ``binding``."""

    frame = binding.binding
    claims = {binding_digest(binding), binding_claim(binding)}
    if binding.planned is not None:
        tips = f"{binding.planned.code_tip}:{binding.planned.memory_tip}"
        full = f"{tips}:{binding.planned.code_tree}:{binding.planned.memory_tree}"
        claims.update(
            {
                f"planned@{tips}",
                f"planned:{tips}",
                f"planned:{full}",
                f"{frame.comparison_reference}@{tips}",
                f"{frame.comparison_reference}@{full}",
            }
        )
    else:
        trees = (
            f"{frame.code_base_tree}:{frame.code_candidate_tree}:"
            f"{frame.compared_memory_base_tree}:{frame.memory_candidate_tree}"
        )
        claims.add(f"{frame.comparison_reference}@{trees}")
    return claims


def _matches_model_claim(given: dict[str, Any], binding: ExplainerBinding) -> bool | None:
    frame = binding.binding
    try:
        parsed_env = ExplainerBinding.model_validate(given)
        return same_binding(parsed_env.binding, frame) and parsed_env.planned == binding.planned
    except ValueError:
        pass
    try:
        parsed_frame = FrameBinding.model_validate(given)
    except ValueError:
        return None
    if not same_binding(parsed_frame, frame):
        return False
    if binding.planned is None:
        return True
    return (
        parsed_frame.code_candidate_commit == binding.planned.code_tip
        and parsed_frame.memory_candidate_commit == binding.planned.memory_tip
    )


def _matches_face_claim(given: dict[str, Any], binding: ExplainerBinding) -> bool:
    frame = binding.binding
    base_matches = (
        given.get("owner") == frame.comparison_owner
        and given.get("number") == frame.comparison_number
        and given.get("reference") == frame.comparison_reference
        and given.get("codeCandidateTree") == frame.code_candidate_tree
        and given.get("memoryCandidateTree") == frame.memory_candidate_tree
    )
    if not base_matches:
        return False
    if binding.planned is None:
        return "planned" not in given
    planned = given.get("planned")
    return (
        isinstance(planned, dict)
        and planned.get("codeTip") == binding.planned.code_tip
        and planned.get("memoryTip") == binding.planned.memory_tip
        and planned.get("codeTree", binding.planned.code_tree) == binding.planned.code_tree
        and planned.get("memoryTree", binding.planned.memory_tree) == binding.planned.memory_tree
    )


def _matches_json_claim(stated: str, binding: ExplainerBinding) -> bool | None:
    """``True`` if JSON claim matches ``binding``, ``False`` if another binding, ``None`` if not a binding JSON."""

    if not stated.startswith("{"):
        return None
    try:
        given = json.loads(stated)
    except ValueError:
        return None
    if not isinstance(given, dict):
        return None
    if given.get("digest") == binding_digest(binding) or given.get("claim") in _exact_claims(
        binding
    ):
        return True
    model_match = _matches_model_claim(given, binding)
    if model_match is not None:
        return model_match
    face_keys = (
        "owner",
        "number",
        "reference",
        "planned",
        "digest",
        "comparison_number",
    )
    return _matches_face_claim(given, binding) if any(k in given for k in face_keys) else None


def _looks_like_binding(stated: str) -> bool:
    return (
        stated == "planned"
        or stated.startswith(("planned@", "planned:", "#", "sha256:", "review-comparison:"))
        or stated.isdigit()
        or "#" in stated
    )


def require_claim(
    claim: str,
    binding: ExplainerBinding,
    *,
    prior: ExplainerBinding | None = None,
    held_at: Path | None = None,
) -> None:
    """Refuse a call made at a binding the environment has left (``binding-moved``).

    The refusal names the mismatch and where the agent reads the rest: the label and digest the
    environment is bound to, the label the call named, and ``held_at`` (its ``binding.json``). It
    carries no binding block (MIK-R211 v5 rule 11).
    """

    frame = binding.binding
    label = "planned" if binding.planned is not None else f"#{frame.comparison_number}"
    bound = f"{label} ({binding_digest(binding)}{'' if held_at is None else f', {held_at}'})"
    stated = claim.strip()
    if stated in _exact_claims(binding):
        return
    json_match = _matches_json_claim(stated, binding)
    if json_match is True:
        return
    if json_match is False:
        raise ExplainerRefused(
            "binding-moved",
            f"the environment is bound to {bound}; {claim!r} is another binding, so nothing was "
            "written at it",
        )
    if binding.planned is None and frame.comparison_number > 0:
        same_prior = (
            prior is None
            or prior.binding.comparison_number != frame.comparison_number
            or same_binding(prior.binding, frame)
        )
        if same_prior and stated in {
            label,
            str(frame.comparison_number),
            frame.comparison_reference,
            f"{frame.comparison_owner}#{frame.comparison_number}",
        }:
            return
    if _looks_like_binding(stated):
        raise ExplainerRefused(
            "binding-moved",
            f"the environment is bound to {bound}; {claim!r} is another binding, so nothing was "
            "written at it",
        )
    raise ExplainerRefused(
        "binding-unknown",
        f"{claim!r} names no binding of this environment: give its label or digest, {bound}",
    )


def relative_parts(path: str) -> tuple[str, ...]:
    """One relative path as parts, refusing traversal, an absolute path and a separator lie."""

    if not path:
        return ()
    parts = tuple(path.split("/"))
    for part in parts:
        if part in {"", ".", "..", ".git"} or "\\" in part or ":" in part or "\x00" in part:
            raise ExplainerRefused(
                "path-refused",
                f"{path!r} is not a plain relative path: '..', an empty segment, a drive letter "
                "or a backslash is refused",
            )
    return parts


def object_name(value: str) -> str:
    """One revision as Git spells it: a name, a range or ``<rev>:<path>``, never a host path.

    An option is passed in ``options``, not in a revision's place, so a value starting with ``-``
    names what it is; an absolute path or a drive is a path outside the tree (MIK-R211 v4 rule 3).
    """

    if not value or value.startswith("-"):
        raise ExplainerRefused(
            "revision-refused", f"{value!r} is not a revision; pass an option in 'options'"
        )
    if value.startswith(("/", "\\")) or _DRIVE.match(value):
        raise ExplainerRefused("path-refused", f"{value} is a path outside the tree")
    return value


def tree_path(value: str) -> str:
    """One path inside a tree (a ``show``/``diff``/``ls-tree`` filter), never a worktree path."""

    if not value or value.startswith("-"):
        raise ExplainerRefused("path-refused", f"{value!r} is not one path inside a tree")
    relative_parts(value)
    return value
