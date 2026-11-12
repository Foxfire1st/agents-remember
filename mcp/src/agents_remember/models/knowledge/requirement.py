"""The requirement owner reference and the owner's resolution result.

A knowledge record that names a requirement names it by the task plane's own reference, and whether
that reference resolves stays the owner's answer. This module owns the two values that carry those
facts. It declares no record payload: requirement meaning is not stored as a record of its own.

Two properties are load-bearing and neither is incidental:

* **The owner reference is the task plane's own three components, spelled its way.** It
  carries ``path`` / ``stableId`` / ``version``: the same three names
  :class:`agents_remember.models.task_intent.ApprovedRequirementPacketRef` uses, so the two sides
  cannot spell one version two ways and drift apart in comparison. The admitted version spelling is
  that reference's own ``^v[1-9][0-9]*$``. A fourth identifier -- a UUID, a content address, a
  database-local surrogate -- is refused, not by a denylist but by ``extra="forbid"`` on a shape
  that declares exactly three fields.
* **The reference is not policed here.** The three components are bounded; the path is **not**
  confined to a task root, a ``.md`` suffix is not required, and the packet's own metadata rows are
  not checked. All three are the owner's refusals
  (:func:`agents_remember.tasks.task_intent._approved_packet_ref`), and a substrate that pre-refused
  them would be substituting its own answer for the owner's. The owner's answer is carried instead,
  as data: :class:`RequirementOwnerResolution`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PATH_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    KnowledgeModel,
)

# The admitted version spelling is the task plane's own --
# ``models/task_intent/__init__.py``, ``ApprovedRequirementPacketRef.version``. Declared here as the
# same pattern rather than a second, wider one, because two admitted spellings for one version is
# exactly the drift requirement 2.2 forbids.
REQUIREMENT_PACKET_VERSION_PATTERN = r"^v[1-9][0-9]*$"


# The two owner-resolution outcomes, as the closed vocabulary the payload stores.
OwnerResolutionState = Literal["resolved", "unresolved"]


class RequirementOwnerRef(KnowledgeModel):
    """The canonical owner of one requirement obligation, in the task plane's own three components.

    The field names are :class:`…task_intent.ApprovedRequirementPacketRef`'s, deliberately, so the
    substrate's stored reference and the task plane's typed one compare literally rather than
    through a translation that could disagree. ``extra="forbid"`` is what refuses a fourth
    addressing scheme: there is no field for a UUID or a content address to arrive in.

    Nothing here confines the path or requires a Markdown target. Those are the owner's refusals and
    are carried as the owner's own codes; see :class:`RequirementOwnerResolution`.
    """

    path: str = Field(min_length=1, max_length=PATH_MAX_LENGTH)
    stableId: str = Field(min_length=1, max_length=LABEL_MAX_LENGTH)
    version: str = Field(pattern=REQUIREMENT_PACKET_VERSION_PATTERN)

    @model_validator(mode="after")
    def _require_nonblank_components(self) -> RequirementOwnerRef:
        for name in ("path", "stableId"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"owner reference {name} must not be blank")
        return self


class RequirementOwnerResolution(KnowledgeModel):
    """The owner's resolution *result*, consumed rather than re-derived.

    ``resolved`` means the owner's own resolver accepted this exact reference. ``unresolved``
    carries the refusal the owner returned, verbatim: ``refusal_code`` is the owner's own status
    string (for example ``task-intent-requirement-packet-missing``) and ``refusal_detail`` is its
    own message. Nothing in this record group constructs either value, so a store can never report
    an owner's refusal the owner did not give.

    The resolution carries no components of its own. The reference it is about is the record's one
    ``owner`` field, which is never rewritten by a resolution -- so a resolution cannot disagree
    with the reference it resolves, and a failed resolution leaves the reference intact.
    """

    state: OwnerResolutionState
    refusal_code: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    refusal_detail: str | None = Field(default=None, max_length=PROSE_MAX_LENGTH)

    @model_validator(mode="after")
    def _require_one_outcome(self) -> RequirementOwnerResolution:
        carried = (self.refusal_code, self.refusal_detail)
        if self.state == "resolved":
            if any(value is not None for value in carried):
                raise ValueError("a resolved owner carries no refusal")
            return self
        if any(value is None or not str(value).strip() for value in carried):
            raise ValueError(
                "an unresolved owner carries the refusal the owner returned: code and detail"
            )
        return self
