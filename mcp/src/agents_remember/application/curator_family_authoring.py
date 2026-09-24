"""The curator's family plane as authored: the guarantees, memberships and outcomes a list declares.

ICR-R28@v2 states the curator's duty in the existing vocabulary: *where source evidence and project
intent justify a joint obligation, the curator authors the family identity, its independent
joint-guarantee revision, and memberships linking exact family and invariant revisions*. This module
reads exactly that authored plane out of a hand-off list and refuses what is incoherent, before
anything is resolved against a candidate or written. :mod:`…curator_family_planning` resolves what
this module read into exact identities and commands, and :mod:`…curator_family_coverage` reports it.

Four decisions are the whole of it, and each is a refusal to do something a reader might find
convenient:

* **Nothing is grouped by inference.** A family exists here exactly when the curator declared one.
  Two entries citing two constructs of one file, one route or one label are two obligations with no
  family until a joint obligation between them is authored -- the non-conforming import this
  requirement exists to refuse is a taxonomy derived from a directory listing, and no path, label,
  prefix, shared anchor or shared family key is read as one here.
* **The guarantee is the family's own text.** A member's statement is never concatenated into it, and
  the declared text is what travels to the revision that seals it.
* **An unexamined entry is not a family-free one.** Three outcomes are kept apart end to end: an
  entry placed in families, an entry for which the curator recorded a *deliberate* no-family outcome
  with its basis, and an entry that carries no family decision at all. The third is reported as
  unexamined coverage, never as the second, because a measured zero and an unmeasured one lead to
  opposite actions.
* **Reuse is by identity, never by label.** A declaration that names a ``family_id`` is checked
  against the candidate by the planning half; a later guarantee carrying the same label is a different
  record, which is the authored meaning the packet preserves.

``basis`` is required wherever the curator makes a family decision: a membership's justification and
a no-family outcome's basis are both authored text, and a blank one is refused rather than stored as
an unexplained claim.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    UUID_PATTERN,
)

__all__ = [
    "ENTRY_FAMILY_KEY",
    "FAMILY_ABSENT_STATE",
    "FAMILY_MEMBER_STATE",
    "EntryFamilyRefusal",
    "FamilyAssignment",
    "FamilyGuarantee",
    "FamilyMembership",
    "FamilyPlaneRead",
    "FamilyRetirement",
    "family_refusal",
    "no_family_condition",
    "read_family_plane",
]

# The hand-off key the curator's family plane travels under. It is a CURATOR-side key: the producer's
# thirteen fields are unchanged, and a list that carries no such key is a list whose family coverage
# the run reports as unexamined rather than as empty.
ENTRY_FAMILY_KEY = "family"

# The two authored states. ``member`` places the entry's exact revision in the families it names;
# ``no_family`` records the deliberate outcome and its basis. An entry with no family key at all is
# neither, and the three never merge.
FAMILY_MEMBER_STATE = "member"
FAMILY_ABSENT_STATE = "no_family"

_STATES = (FAMILY_MEMBER_STATE, FAMILY_ABSENT_STATE)

# The bounded shape of one local key. A hand-off key is a handle rather than prose, so it is a short
# single-line label; the bound is declared here so a malformed list is refused by name.
_KEY_MAX = 120


@dataclass(frozen=True)
class EntryFamilyRefusal:
    """One entry's family plane refused: the code that names why, and the sentence that says it."""

    entry_id: str
    code: str
    reason: str


# --------------------------------------------------------------------------------------------
# Step 1: the family plane as the curator authored it
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyGuarantee:
    """One declared family guarantee, under the entry that authored it.

    ``family_id`` is present only when the declaration *names* an identity the repository already
    holds; its absence means this operation allocates a family of its own. ``predecessor_revision_ids``
    is the declared predecessor set, which is what makes a changed guarantee a separately identified
    successor rather than a rewrite of the revision an earlier membership still cites.
    """

    key: str
    declared_by: str
    display_label: str
    display_version: str
    joint_guarantee: str
    predecessor_revision_ids: tuple[str, ...] = ()
    family_id: str | None = None


@dataclass(frozen=True)
class FamilyMembership:
    """One membership the curator authored, with the basis that justifies it.

    ``declaration`` is present exactly when this membership's entry is the one that authors the
    family's guarantee; ``stored_family_revision_id`` is present when the membership joins a revision
    the repository already holds. The two are mutually exclusive, and a membership with neither joins
    a key another entry in the same list declares.
    """

    key: str
    basis: str
    declaration: FamilyGuarantee | None = None
    stored_family_revision_id: str | None = None


@dataclass(frozen=True)
class FamilyRetirement:
    """One stored membership the curator retired, named by its own identity."""

    member_id: str


@dataclass(frozen=True)
class FamilyAssignment:
    """One entry's whole family decision: the state, its basis, and what it places or retires."""

    state: str
    basis: str | None = None
    memberships: tuple[FamilyMembership, ...] = ()
    retirements: tuple[FamilyRetirement, ...] = ()


@dataclass(frozen=True)
class FamilyPlaneRead:
    """The family plane of one list as read: what each entry decided, and what is incoherent.

    ``assignments`` is keyed by entry id and holds **only** entries that carry a family decision, so
    an entry absent from it is unexamined rather than family-free -- the distinction the report
    depends on. ``declarations`` is keyed by the local family key and holds the one declaration each
    key has; ``refusals`` carries every entry whose plane could not be read or whose membership names
    a key the list does not declare.
    """

    assignments: Mapping[str, FamilyAssignment]
    declarations: Mapping[str, FamilyGuarantee]
    refusals: Mapping[str, EntryFamilyRefusal]

    def assignment_of(self, entry_id: str) -> FamilyAssignment | None:
        """This entry's decision, or ``None`` when the curator authored none for it."""

        return self.assignments.get(entry_id)

    def refusal_of(self, entry_id: str) -> EntryFamilyRefusal | None:
        """Why this entry's family plane was refused, or ``None`` when it was readable."""

        return self.refusals.get(entry_id)


def read_family_plane(entries: Sequence[Mapping[str, Any]]) -> FamilyPlaneRead:
    """Read the curator's family plane out of a hand-off list, and refuse what is incoherent.

    Two passes, because the two are different questions. The first reads each entry's own decision (a
    state, its basis, its memberships and what each membership joins). The second asks the *list-level*
    question a single entry cannot answer: whether every joined key is declared exactly once somewhere
    in this list. Both produce per-entry refusals, so a malformed plane refuses the entry that authored
    it rather than the whole list.

    Whether a membership that names **no** declaration still resolves is deliberately not answered
    here. A key this list does not declare is a question about the dataset as much as about the list --
    the membership may name a stored ``family_revision_id``, and only the candidate knows whether it
    exists -- so it is answered once, where the candidate is in hand, by
    :func:`…curator_family_planning.plan_entry_family`. One refusal, one implementation.
    """

    assignments: dict[str, FamilyAssignment] = {}
    refusals: dict[str, EntryFamilyRefusal] = {}
    for raw in entries:
        entry_id = str(raw.get("id", ""))
        assignment, refusal = _read_assignment(entry_id, raw.get(ENTRY_FAMILY_KEY))
        if refusal is not None:
            refusals[entry_id] = refusal
        elif assignment is not None:
            assignments[entry_id] = assignment
    declarations, declared_refusals = _declared_keys(assignments)
    refusals.update(declared_refusals)
    return FamilyPlaneRead(
        assignments={key: value for key, value in assignments.items() if key not in refusals},
        declarations=declarations,
        refusals=refusals,
    )


def _read_assignment(
    entry_id: str, raw: object
) -> tuple[FamilyAssignment | None, EntryFamilyRefusal | None]:
    """One entry's authored decision, or the refusal that names why it is not readable."""

    if raw is None:
        return None, None
    if not isinstance(raw, Mapping):
        return None, family_refusal(
            entry_id, "family_declaration_malformed", f"{ENTRY_FAMILY_KEY} must be a JSON object"
        )
    state = raw.get("state")
    if not isinstance(state, str) or state not in _STATES:
        return None, family_refusal(
            entry_id,
            "family_state_unknown",
            f"{ENTRY_FAMILY_KEY}.state must name one of {', '.join(_STATES)}; no default is "
            "substituted, because an unknown state would silently decide the entry's family coverage",
        )
    if state == FAMILY_ABSENT_STATE:
        return _absent_assignment(entry_id, raw)
    return _member_assignment(entry_id, raw)


def _absent_assignment(
    entry_id: str, raw: Mapping[str, Any]
) -> tuple[FamilyAssignment | None, EntryFamilyRefusal | None]:
    """The deliberate no-family outcome: its basis is required and no membership may ride along."""

    basis = _authored_text(raw.get("basis"))
    if basis is None:
        return None, family_refusal(
            entry_id,
            "family_basis_missing",
            "a no_family decision must carry the basis it rests on: the outcome is the curator's "
            "deliberate one, and an unexplained absence is what the requirement forbids reporting as it",
        )
    if raw.get("memberships"):
        return None, family_refusal(
            entry_id,
            "family_state_conflict",
            "a no_family decision cannot also name memberships: the entry's outcome would then be "
            "both, and a reader could not tell which one the curator authored",
        )
    return FamilyAssignment(state=FAMILY_ABSENT_STATE, basis=basis), None


def _member_assignment(
    entry_id: str, raw: Mapping[str, Any]
) -> tuple[FamilyAssignment | None, EntryFamilyRefusal | None]:
    """The membership decision: at least one membership, each with a key and its basis."""

    raw_memberships = raw.get("memberships")
    if raw_memberships is None:
        # A retirement is authored alone: an obligation can leave a family without being placed in
        # another one, and requiring a membership beside it would invent a placement the curator did
        # not author. An absent key is therefore an empty list here, and the state check below still
        # refuses a decision that authored neither.
        raw_memberships = ()
    if not isinstance(raw_memberships, Sequence) or isinstance(raw_memberships, (str, bytes)):
        return None, family_refusal(
            entry_id,
            "family_membership_malformed",
            "a member decision must name a memberships list: the families this entry's revision is "
            "placed in are authored, never inferred",
        )
    memberships: list[FamilyMembership] = []
    for one in raw_memberships:
        membership, refusal = _read_membership(entry_id, one)
        if refusal is not None:
            return None, refusal
        assert membership is not None
        memberships.append(membership)
    retirements, refusal = _read_retirements(entry_id, raw.get("retire"))
    if refusal is not None:
        return None, refusal
    if not memberships and not retirements:
        return None, family_refusal(
            entry_id,
            "family_membership_missing",
            "a member decision names neither a membership nor a retirement, so its state would claim "
            "a family outcome the curator never authored; an examined entry that belongs nowhere is "
            "the no_family state and carries its basis",
        )
    return (
        FamilyAssignment(
            state=FAMILY_MEMBER_STATE, memberships=tuple(memberships), retirements=retirements
        ),
        None,
    )


def _read_membership(
    entry_id: str, raw: object
) -> tuple[FamilyMembership | None, EntryFamilyRefusal | None]:
    """One authored membership: the key it joins, the basis, and how it joins."""

    if not isinstance(raw, Mapping):
        return None, family_refusal(
            entry_id, "family_membership_malformed", "every membership is a JSON object"
        )
    key = _authored_text(raw.get("family"))
    if key is None or len(key) > _KEY_MAX:
        return None, family_refusal(
            entry_id,
            "family_membership_malformed",
            f"every membership names the family key it joins, in at most {_KEY_MAX} characters",
        )
    basis = _authored_text(raw.get("basis"))
    if basis is None:
        return None, family_refusal(
            entry_id,
            "family_basis_missing",
            f"the membership of {key!r} carries no basis: a joint obligation is authored where the "
            "evidence and intent justify it, and the justification is part of the authored record",
        )
    declaration, refusal = _read_declaration(entry_id, raw.get("declares"), key)
    if refusal is not None:
        return None, refusal
    stored, refusal = _join_shape(entry_id, raw, key, declaration)
    if refusal is not None:
        return None, refusal
    return (
        FamilyMembership(
            key=key, basis=basis, declaration=declaration, stored_family_revision_id=stored
        ),
        None,
    )


def _join_shape(
    entry_id: str,
    raw: Mapping[str, Any],
    key: str,
    declaration: FamilyGuarantee | None,
) -> tuple[str | None, EntryFamilyRefusal | None]:
    """How one membership joins its family: by authoring a guarantee, or by naming a stored revision.

    A membership that does both is refused rather than resolved by precedence: a declaration authors a
    new revision, and naming one is the reuse case, so the two are different authored claims about
    which revision this membership cites.
    """

    stored = _uuid_text(raw.get("family_revision_id"))
    if raw.get("family_revision_id") is not None and stored is None:
        return None, family_refusal(
            entry_id,
            "family_membership_malformed",
            "family_revision_id must be the UUID of a stored family revision",
        )
    if declaration is not None and stored is not None:
        return None, family_refusal(
            entry_id,
            "family_declaration_conflict",
            f"the membership of {key!r} both declares a guarantee and names a stored revision: a "
            "declaration authors a new revision, and naming one is the reuse case",
        )
    return stored, None


def _read_declaration(
    entry_id: str, raw: object, key: str
) -> tuple[FamilyGuarantee | None, EntryFamilyRefusal | None]:
    """The guarantee one membership declares, when it declares one."""

    if raw is None:
        return None, None
    if not isinstance(raw, Mapping):
        return None, family_refusal(
            entry_id, "family_declaration_malformed", "declares must be a JSON object"
        )
    label = _authored_text(raw.get("label"))
    version = _authored_text(raw.get("version"))
    guarantee = _authored_text(raw.get("guarantee"))
    if label is None or version is None or guarantee is None:
        return None, family_refusal(
            entry_id,
            "family_declaration_malformed",
            f"the declaration of {key!r} must carry a label, a display version and the family's own "
            "joint guarantee text",
        )
    family_id = _uuid_text(raw.get("family_id"))
    if raw.get("family_id") is not None and family_id is None:
        return None, family_refusal(
            entry_id,
            "family_declaration_malformed",
            "family_id must be the UUID of a family identity the repository already holds",
        )
    predecessors, refusal = _predecessors(entry_id, raw.get("predecessor_revision_ids"))
    if refusal is not None:
        return None, refusal
    return (
        FamilyGuarantee(
            key=key,
            declared_by=entry_id,
            display_label=label[:LABEL_MAX_LENGTH],
            display_version=version[:LABEL_MAX_LENGTH],
            joint_guarantee=guarantee[:PROSE_MAX_LENGTH],
            predecessor_revision_ids=predecessors,
            family_id=family_id,
        ),
        None,
    )


def _predecessors(entry_id: str, raw: object) -> tuple[tuple[str, ...], EntryFamilyRefusal | None]:
    """The declared predecessor set, each member a family revision identity."""

    if raw is None:
        return (), None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return (), family_refusal(
            entry_id,
            "family_declaration_malformed",
            "predecessor_revision_ids must be a list of stored family revision identities",
        )
    predecessors: list[str] = []
    for one in raw:
        named = _uuid_text(one)
        if named is None:
            return (), family_refusal(
                entry_id,
                "family_declaration_malformed",
                "predecessor_revision_ids must be a list of stored family revision identities",
            )
        predecessors.append(named)
    return tuple(predecessors), None


def _read_retirements(
    entry_id: str, raw: object
) -> tuple[tuple[FamilyRetirement, ...], EntryFamilyRefusal | None]:
    """The memberships this entry retires, each named by its own stored identity."""

    if raw is None:
        return (), None
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return (), family_refusal(
            entry_id,
            "family_retirement_malformed",
            "retire must be a list of membership identities",
        )
    retirements: list[FamilyRetirement] = []
    for one in raw:
        named = _uuid_text(one)
        if named is None:
            return (), family_refusal(
                entry_id,
                "family_retirement_malformed",
                "retire must name stored membership identities, so a removal cannot be authored "
                "against a row this run never read",
            )
        retirements.append(FamilyRetirement(member_id=named))
    return tuple(retirements), None


def _declared_keys(
    assignments: Mapping[str, FamilyAssignment],
) -> tuple[dict[str, FamilyGuarantee], dict[str, EntryFamilyRefusal]]:
    """Every key the list declares exactly once, and the refusals for the keys it declares twice.

    One family key carries one guarantee. Two entries declaring the same key are two different families
    wearing one handle, and the second is refused rather than merged: merging them would author one
    joint obligation out of two, and dropping one would lose a claim the curator made.
    """

    declarations: dict[str, FamilyGuarantee] = {}
    refusals: dict[str, EntryFamilyRefusal] = {}
    for entry_id, assignment in assignments.items():
        for membership in assignment.memberships:
            if membership.declaration is None:
                continue
            if membership.key in declarations:
                refusals[entry_id] = family_refusal(
                    entry_id,
                    "family_declared_twice",
                    f"{membership.key!r} is declared by "
                    f"{declarations[membership.key].declared_by!r} and again here: one family key "
                    "carries one authored guarantee, and the two declarations are not merged",
                )
                continue
            declarations[membership.key] = membership.declaration
    return declarations, refusals


# --------------------------------------------------------------------------------------------
# Shared reading helpers
# --------------------------------------------------------------------------------------------


def _authored_text(raw: object) -> str | None:
    """One authored field as non-blank stripped text, or ``None`` when it is absent or blank."""

    if raw is None or not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    return cleaned or None


def _uuid_text(raw: object) -> str | None:
    """One identity field as its canonical UUID text, or ``None`` when it is absent or malformed."""

    if raw is None:
        return None
    if isinstance(raw, UUID):
        return str(raw)
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if not re.fullmatch(UUID_PATTERN, cleaned):
        return None
    return str(UUID(cleaned))


def family_refusal(entry_id: str, code: str, reason: str) -> EntryFamilyRefusal:
    """One entry's family plane refused, with the reason truncated to the model's reference bound."""

    return EntryFamilyRefusal(entry_id=entry_id, code=code, reason=reason[:REFERENCE_MAX_LENGTH])


def no_family_condition(basis: str) -> str:
    """The recorded condition that keeps a deliberate no-family outcome readable in the dataset.

    It travels in the revision's own conditions -- the field the ingest already uses for the entry's
    provenance -- so the outcome is retained with the record rather than only in a run's report. The
    wording states exactly what was established: the obligation's family placement was examined and no
    joint obligation is supported, with the basis the curator gave.
    """

    return f"Family examination: no joint obligation is supported. Basis: {basis}"[
        :PROSE_MAX_LENGTH
    ]
