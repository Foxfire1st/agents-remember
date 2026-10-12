"""Response model for the direct-execution-grant operation (MIK-R94 rule 2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agents_remember.models.base import ToolResponse


class DirectExecutionGrantResponse(ToolResponse):
    """One grant give/take-back/reclaim result.

    ``state`` names the outcome (``granted`` / ``revoked`` / ``reclaimed`` /
    ``unchanged`` / ``refused``); a refusal also carries ``status`` and
    ``detail``. ``grant`` carries the stored record when one exists after the
    call. ``reclaimed``/``preserved`` carry the reclaim outcome per leaf.
    """

    operation: Literal["direct_execution_grant"] = "direct_execution_grant"
    state: Literal["granted", "revoked", "reclaimed", "unchanged", "refused"]
    summary: str = Field(default="", max_length=8192)
    status: str | None = None
    detail: str | None = Field(default=None, max_length=8192)
    nextAction: str | None = None
    changed: bool = False
    grant: dict[str, Any] | None = None
    reclaimed: list[str] = Field(default_factory=list)
    preserved: list[dict[str, str]] = Field(default_factory=list)
