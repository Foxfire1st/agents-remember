"""Listing of existing taskless receipts; exact validation stays with their owner."""

from __future__ import annotations

import stat
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from agents_remember.application.role_launch_context import selection_binding
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleSelection


def taskless_execution_receipts(
    config: McpRuntimeConfig, selection: RoleSelection
) -> list[tuple[Path, dict[str, Any]]]:
    from agents_remember.cli.role_launch_receipts import (  # noqa: PLC0415
        _read_receipt,
        _taskless_session_directory,
    )

    directory = _taskless_session_directory(config, selection.role)
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError:
        return []
    except OSError as error:
        raise HTTPException(
            status_code=409, detail="Taskless role executions cannot be inspected safely."
        ) from error
    if not stat.S_ISDIR(mode):
        raise HTTPException(
            status_code=409, detail="The taskless role execution store is not a directory."
        )
    expected_selection = selection_binding(selection)
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in directory.glob("*.json"):
        receipt = _read_receipt(path)
        if receipt is None:
            continue
        try:
            request_id = uuid.UUID(str(receipt.get("requestId")))
        except (ValueError, TypeError, AttributeError) as error:
            raise HTTPException(
                status_code=409,
                detail="A saved taskless role execution receipt has no valid requestId.",
            ) from error
        if (
            path.name != f"{request_id}.json"
            or receipt.get("requestId") != str(request_id)
            or receipt.get("role") != selection.role
            or receipt.get("selection") != expected_selection
        ):
            raise HTTPException(
                status_code=409,
                detail="A saved taskless role execution receipt does not match its request address and role selection.",
            )
        records.append((path, receipt))
    return sorted(
        records,
        key=lambda row: (str(row[1].get("createdAt", "")), str(row[1].get("requestId", ""))),
        reverse=True,
    )
