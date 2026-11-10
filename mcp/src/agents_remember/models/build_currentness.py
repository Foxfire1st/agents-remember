"""Currentness shared by serving status and the public response envelope."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class BuildCurrentnessPayload(BaseModel):
    """The loaded Python build compared with the files currently at its package root."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["installed", "checkout"]
    state: Literal["current", "stale", "unknown"]
    loadedDigest: str | None = None
    installedDigest: str | None = None
    firstSeenAt: str | None = None
    reason: str | None = None
    remedy: str | None = None
