"""The gate vocabulary, as the control-plane records import it.

``GateKind`` / ``GateState`` are the Literals of ``kernel.primitives.gate_vocab``.
"""

from __future__ import annotations

from agents_remember.kernel.primitives.gate_vocab import (
    GATE_KINDS,
    GateKind,
    GateState,
    coerce_gate_kind,
)

__all__ = [
    "GATE_KINDS",
    "GateKind",
    "GateState",
    "coerce_gate_kind",
]
