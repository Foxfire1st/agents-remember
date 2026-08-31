"""Control submission and bounded state waits for the ambient-role scenario."""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Callable

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.models.conversations.control_wire import ControlSubmission
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.harness_control_client import (
    read_control_snapshot,
    read_submission_authority,
    submit_control_prompt,
)
from agents_remember.serving.terminal_catalog import TerminalCatalog

WAIT_SECONDS = 60.0


def submit_control(catalog: TerminalCatalog, session_id: str, prompt: str) -> None:
    entry = wait_for_entry(
        catalog,
        session_id,
        lambda current: current.control_endpoint is not None and current.status == "running",
    )
    # The catalog records the launch transaction's last persisted control state; live bridge
    # readiness belongs to the control endpoint. Waiting on the cached ``starting`` value here
    # would deadlock a healthy session whose socket is already ready and idle.
    wait_until(lambda: _control_idle(entry, catalog), description=f"idle control {session_id}")
    current = catalog.get(session_id)
    if current is None:
        raise RuntimeError(f"control target {session_id} disappeared")
    authority = read_submission_authority(current)
    receipt = submit_control_prompt(
        current,
        prompt,
        ControlSubmission(
            source="cockpit",
            request_id=f"arspawn-e2e-{uuid.uuid4().hex}",
            expected_bridge_epoch=authority.bridge_epoch,
        ),
    )
    if receipt.acceptance not in {"immediate", "queued"}:
        raise RuntimeError(f"control prompt was not accepted: {receipt}")


def _control_idle(entry: TerminalCatalogEntry, catalog: TerminalCatalog) -> bool:
    current = catalog.get(entry.id)
    if current is None or current.control_endpoint is None:
        return False
    with contextlib.suppress(Exception):
        snapshot = read_control_snapshot(current)
        return snapshot.control == "ready" and snapshot.activity == "idle"
    return False


def wait_for_seat(
    catalog: TerminalCatalog,
    document: TaskDocumentRef,
    role: str,
) -> TerminalCatalogEntry:
    return wait_until(
        lambda: catalog.active_for_task(document, seat_role=role),
        description=f"running {document.path}:{role} seat",
    )


def wait_for_new_seat(
    catalog: TerminalCatalog,
    document: TaskDocumentRef,
    role: str,
    previous_id: str,
) -> TerminalCatalogEntry:
    return wait_until(
        lambda: (
            entry
            if (entry := catalog.active_for_task(document, seat_role=role)) is not None
            and entry.id != previous_id
            else None
        ),
        description=f"replacement {document.path}:{role} seat",
    )


def wait_for_entry(
    catalog: TerminalCatalog,
    session_id: str,
    predicate: Callable[[TerminalCatalogEntry], bool],
) -> TerminalCatalogEntry:
    return wait_until(
        lambda: (
            entry if (entry := catalog.get(session_id)) is not None and predicate(entry) else None
        ),
        description=f"catalog state for {session_id}",
    )


def wait_for_inbox_id(
    inbox: OperatorInboxStore,
    entry_id: str | None,
) -> OperatorInboxEntry:
    if entry_id is None:
        raise RuntimeError("seat did not retain its dispatch brief entry id")
    return wait_until(
        lambda: inbox.current().get(entry_id),
        description=f"inbox row {entry_id}",
    )


def wait_for_accepted_brief(
    inbox: OperatorInboxStore,
    seat: TerminalCatalogEntry | None,
) -> OperatorInboxEntry | None:
    """Give durable queued delivery a short convergence window without accepting it as success."""

    if seat is None or seat.dispatch_brief_entry_id is None:
        return None
    entry_id = seat.dispatch_brief_entry_id
    try:
        return wait_until(
            lambda: (
                row
                if (row := inbox.current().get(entry_id)) is not None
                and row.deliveryState == "delivered"
                and row.adapterDeliveryState in {"accepted", "completed"}
                else None
            ),
            description=f"accepted dispatch brief {entry_id}",
            timeout=30.0,
        )
    except TimeoutError:
        return inbox.current().get(entry_id)


def wait_for_inbox_response(
    inbox: OperatorInboxStore,
    response: str,
    *,
    predicate: Callable[[OperatorInboxEntry], bool] = lambda _row: True,
) -> OperatorInboxEntry:
    return wait_until(
        lambda: next(
            (
                row
                for row in inbox.current().values()
                if row.response == response and predicate(row)
            ),
            None,
        ),
        description=f"inbox response {response!r}",
    )


def wait_until[T](
    probe: Callable[[], T | bool | None],
    *,
    description: str,
    timeout: float = WAIT_SECONDS,
) -> T:
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        try:
            last = probe()
        except (OSError, ValueError) as exc:
            last = repr(exc)
        if last:
            return last  # type: ignore[return-value]
        time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for {description}; last={last!r}")
