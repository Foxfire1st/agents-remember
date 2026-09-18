"""Canonical closeout commit-message plans and normalized input."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_mode import MemoryMode

# The attribution trailer every memory-content commit carries is declared in the kernel module
# that READS it back and renders it (``kernel/memory_attribution.py``), and this model reaches
# it through the one renderer rather than naming the key. One literal, two directions of use: a
# writer that changed its own copy would emit trailers the reader silently ignores, which is the
# worst failure this system can have because it looks like "no attribution exists" rather than
# like a bug. The direction is kernel -> models because ``layers.toml`` ranks ``kernel`` below
# ``models`` and permits an import only from a lower rank; importing the other way would put the
# reader above the writer and break that contract, and it would also be a real cycle risk rather
# than a theoretical one. Every producer -- worktree closeout and the recovery route through it,
# direct landing, the prepared memory-content leg, memory carryover, baseline adoption -- renders
# through ``render_memory_content_message``, so the key and its format cannot drift apart.
#
# What the key means, and what it does not: it names the code commit the same closeout landed,
# and it is written into the commit object's message at the one point that knows both, because
# that is what binds it -- ``git interpret-trailers --parse`` and
# ``git log --format='%(trailers:key=Code-Commit)'`` both read it as data, and no later step can
# add or change it without rewriting the object (``git notes`` is not bound by the hash). A
# memory commit with no code counterpart to name -- the ledger commit, settings, a README --
# carries none: absence is the detection, not a gap to paper over.

CloseoutInputRoute = Literal["worktree", "direct-landing"]
CloseoutCommitLegName = Literal["code", "memory"]
CloseoutLegState = Literal["enabled", "not-applicable"]
CloseoutPublicMessageField = Literal[
    "code_commit_message",
    "memory_commit_message",
]
CloseoutMessageObservation = Literal[
    "omitted",
    "empty",
    "whitespace-only",
    "stale-or-forged",
]


class CloseoutMessageInput(BaseModel):
    """Untrusted public message observations before plan resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str | None = None
    memory: str | None = None


class CloseoutLegPlan(BaseModel):
    """Whether one commit leg can write during the effective lifecycle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: CloseoutLegState
    reason: str


class ResolvedCloseoutPlan(BaseModel):
    """Contract-derived enabledness, independent of caller message validity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: CloseoutInputRoute
    contractKind: Literal["leaf", "series"]
    memoryMode: MemoryMode
    code: CloseoutLegPlan
    memory: CloseoutLegPlan


class CloseoutInvalidField(BaseModel):
    """One exact public input cell refused under its resolved plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: CloseoutPublicMessageField | Literal["effectiveInput"]
    leg: Literal["code", "memory", "plan"]
    observation: CloseoutMessageObservation
    code: str


class CloseoutCorrectedCall(BaseModel):
    """Sanitized exact call shape returned with a typed input refusal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    arguments: dict[str, object]


class EnabledCloseoutLeg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["enabled"] = "enabled"
    reason: str
    message: str

    @field_validator("message")
    @classmethod
    def _require_normalized_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("enabled closeout message must be nonblank")
        if normalized != value:
            raise ValueError("enabled closeout message must already be stripped")
        return value


class NotApplicableCloseoutLeg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["not-applicable"] = "not-applicable"
    reason: str


EffectiveCloseoutLeg = Annotated[
    EnabledCloseoutLeg | NotApplicableCloseoutLeg,
    Field(discriminator="state"),
]


class EffectiveCloseoutInput(BaseModel):
    """The sole message-bearing input used after closeout validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: CloseoutInputRoute
    contractKind: Literal["leaf", "series"]
    memoryMode: MemoryMode
    code: EffectiveCloseoutLeg
    memory: EffectiveCloseoutLeg

    def message_for(self, leg: CloseoutCommitLegName) -> str:
        value = getattr(self, leg)
        if not isinstance(value, EnabledCloseoutLeg):
            raise RuntimeError(f"closeout {leg} commit leg is not applicable")
        return value.message

    def memory_content_message(self, code_commit: str) -> str:
        """Render the memory-content commit body: this closeout's message and its attribution.

        The closeout's own message is used verbatim and the trailer is appended as a
        separate final paragraph, never substituted for it -- ``git interpret-trailers``
        reads a trailer only from that final block, which is also why a line the caller
        wrote earlier in the body can never be mistaken for this attribution.

        The rendering itself is ``kernel.memory_attribution.render_memory_content_message``,
        the one writer of the trailer, and this method is a closeout-shaped way in to it: an
        enabled memory leg is the only leg that has an attribution to render, and every other
        producer reaches the same function without this type. The attribution is rendered
        where the message and the code commit the same closeout landed are both in hand, and
        not by a later step: the message is hashed into the commit object, and the commit that
        carries it is already proved and journalled by the time this call returns. Appending
        afterwards would mean rewriting that exact object, and a trailer that can be added
        later is a trailer that can be changed later.
        """
        return render_memory_content_message(self.message_for("memory"), code_commit)

    def enabled(self, leg: CloseoutCommitLegName) -> bool:
        return isinstance(getattr(self, leg), EnabledCloseoutLeg)
