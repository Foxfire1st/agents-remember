from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator
from pydantic.alias_generators import to_camel

NonEmptyText = Annotated[str, Field(min_length=1)]

PositiveRevision = Annotated[int, Field(ge=1)]

PositiveOrdinal = Annotated[int, Field(ge=1)]


class WireModel(BaseModel):
    """Strict immutable camel-case wire model used across serving boundaries."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class _OpaqueToken(RootModel[str]):
    """Purpose-branded opaque value; signing and persistence belong to later services."""

    model_config = ConfigDict(frozen=True)
    token_prefix: ClassVar[str]

    @field_validator("root")
    @classmethod
    def require_purpose_prefix(cls, value: str) -> str:
        suffix = value.removeprefix(cls.token_prefix)
        if not suffix or suffix == value:
            raise ValueError(f"token must use {cls.token_prefix!r} purpose prefix")
        return value

    def __str__(self) -> str:
        return self.root


class OperationFingerprint(_OpaqueToken):
    token_prefix = "sha256:"

    @field_validator("root")
    @classmethod
    def require_canonical_sha256(cls, value: str) -> str:
        digest = value.removeprefix(cls.token_prefix)
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("operation fingerprint must be canonical lowercase SHA-256")
        return value
