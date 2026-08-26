"""Immutable closeout preview arguments shared across lifecycle refusals."""

from __future__ import annotations

from agents_remember.models.lifecycles.operation import CloseoutOperationInput


def closeout_preview_args(operation_input: CloseoutOperationInput) -> dict[str, object]:
    args: dict[str, object] = {"contract_path": operation_input.contractPath}
    for leg, field in (
        ("code", "code_commit_message"),
        ("memory", "memory_commit_message"),
        ("ledger", "ledger_commit_message"),
    ):
        accepted = getattr(operation_input.effectiveInput, leg)
        if accepted.state == "enabled":
            args[field] = accepted.message
    return args


__all__ = ["closeout_preview_args"]
