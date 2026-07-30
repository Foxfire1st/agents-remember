"""Opaque evidence-reference minting for one active projection generation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectionEvidenceRefs:
    """Mint public-safe coordinates without exposing native payloads."""

    epoch_prefix: str

    @classmethod
    def from_bridge_epoch(cls, bridge_epoch: str) -> ProjectionEvidenceRefs:
        return cls(epoch_prefix=bridge_epoch[:12])

    def evidence(self, sequence: int) -> str:
        return f"ar-ev:{self.epoch_prefix}:{sequence}"

    def native(self, native_id: str) -> str:
        return f"ar-native:{self.epoch_prefix}:{native_id}"

    def echo(self, sequence: object) -> str:
        return f"ar-echo:{self.epoch_prefix}:{sequence}"
