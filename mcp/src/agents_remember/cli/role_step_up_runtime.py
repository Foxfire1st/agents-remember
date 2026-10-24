"""Step-up wiring at the task/receipt/launch boundary; no additional runtime store."""

from __future__ import annotations

import os
import selectors
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents_remember.cli.role_launch_routes import _PreparedRoleStart

from agents_remember.application.role_launch_context import (
    RoleLaunchContext,
    selection_binding,
)
from agents_remember.cli.role_handover_artifacts import (
    takeover_artifact_line,
    write_step_up_artifact,
)
from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path
from agents_remember.cli.role_step_up import LAUNCH_FIELDS, select_step_up
from agents_remember.cli.role_step_up_decisions import recorded_step_up
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import TASKLESS_ROLES
from agents_remember.models.role_launcher import RoleDispatchRequest, RoleSelection
from agents_remember.worktrees.review_history import review_count_unavailable, role_review_state

# Inventory is advisory and runs under the shared start lock. Both roots together
# consume at most twice these per-root budgets, regardless of tree population.
_INVENTORY_TIMEOUT_SECONDS = 1.0
_INVENTORY_MAX_BYTES = 65_536
_INVENTORY_MAX_FILES = 512
_INVENTORY_STDERR_BYTES = 4096
_INVENTORY_REAP_SECONDS = 0.2


def selected_defaults(
    config: McpRuntimeConfig,
    context: RoleLaunchContext,
    defaults: dict[str, Any],
    reason: str | None,
) -> dict[str, Any]:
    selection = RoleSelection.model_validate(selection_binding(context))
    prior = None
    receipt = None
    document = context.effective_task.document if context.effective_task else None
    if context.effective_task is not None and context.role not in TASKLESS_ROLES:
        receipt = _read_receipt(_receipt_path(config, selection))
        prior = receipt.get("stepUp") if receipt else None
        prior = prior or recorded_step_up(document, context.role)
    unreadable = (
        document.review_read_error
        or review_count_unavailable(role_review_state(document, context.role))
        if document is not None and context.role in {"worker", "curator"} and defaults.get("stepUp")
        else None
    )
    selected = select_step_up(
        {**defaults, "role": context.role},
        document,
        reason=reason,
        prior=prior,
    )
    if receipt is None and prior and selected.get("stepUpSelection"):
        selected["stepUpSelection"]["from"] = _recorded_predecessor(prior)
    if unreadable:
        choice = (
            "the explicit or earlier step-up is retained"
            if selected.get("stepUpSelection")
            else "this start takes the role's own choices"
        )
        selected = {
            **selected,
            "stepUpWarning": " ".join(
                filter(
                    None,
                    (
                        selected.get("stepUpWarning"),
                        f"The review count could not be read; {choice}. "
                        f"{context.effective_task.path if context.effective_task else ''}: {unreadable}",
                    ),
                )
            ),
        }
    return selected


def _recorded_predecessor(prior: dict[str, Any]) -> dict[str, Any]:
    """A retained alternative is not proof that a pre-creation decision was applied."""
    destination = prior.get("to") or {}
    observed = "requestedModel" in prior
    return {
        "agent": destination.get("agent") if observed else None,
        **{key: destination.get(key) if observed else None for key in LAUNCH_FIELDS},
        "provenance": (
            "recorded applied step-up destination"
            if observed
            else "prior step-up destination unconfirmed"
        ),
    }


def step_up_request(
    request: RoleDispatchRequest, options: dict[str, Any], current: dict[str, Any] | None
) -> RoleDispatchRequest:
    """Infer the existing holder only after the alternative passed host validation."""
    if not options.get("stepUp") or current is None or current.get("stepUp"):
        return request
    holder = current.get("agentId")
    if not isinstance(holder, str):
        return request
    return request.model_copy(
        update={
            "replaces_agent_id": request.replaces_agent_id or holder,
            "replaces_interrupt_turn": False,
        }
    )


def bind_step_up_predecessor(prepared: dict[str, Any], current: dict[str, Any]) -> None:
    """Use the replaced receipt's applied choices, not today's configured base."""
    step = prepared.get("stepUp")
    if not step:
        return
    if step.get("from", {}).get("provenance"):
        # Receipt loss may leave only an older base receipt in history. It is not
        # the predecessor of the durable later step-up destination.
        return
    applied = current.get("agent") or {}
    options = current.get("sessionOptions") or {}
    step["from"] = {
        "agent": applied.get("id") or current.get("provider"),
        **{key: applied.get(key, options.get(key)) for key in LAUNCH_FIELDS},
        "notes": launch_choice_metadata(current.get("choices") or options.get("choices") or {}).get(
            "notes"
        ),
    }


def successor_prompt(start: _PreparedRoleStart, prompt: str) -> str:
    prepared = start.role_handover.handover
    if start.replaces_agent_id and isinstance(prepared.get("predecessorReports"), dict):
        earlier = prepared["predecessorReports"].get("report") or {}
        paths = (
            f"its earlier report paths are {earlier.get('path')} (canonical {earlier.get('canonicalPath')}). "
            if earlier.get("path")
            else f"its earlier request id is {prepared['predecessorReports'].get('requestId')}. "
        )
        prompt = f"{prompt}\nSuccessor start: this seat replaces agent {start.replaces_agent_id}; {paths}Take over from what is written there."
    step = start.role_handover.session_options.get("stepUp")
    if step:
        state = (
            role_review_state(start.context.effective_task.document, start.context.role)
            if start.context.effective_task
            else None
        )
        verdict = state.recordedRounds[-1].verdictRef if state and state.recordedRounds else None
        material = {
            "fromModel": step["from"].get("model"),
            "fromProvenance": step["from"].get("provenance", "predecessor launch choices"),
            "source": step["source"],
            "lastVerdict": verdict,
            "remainingFindingIds": state.remainingFindingIds if state else [],
            "uncommittedFiles": uncommitted_files(start.role_handover.workspace),
        }
        reference = write_step_up_artifact(prepared["taskReportPath"], material)
        prepared["stepUpArtifact"] = reference
        prompt += f"\nStep-up: take over this seat from model {material['fromModel'] or 'unknown'} ({material['fromProvenance']}); {step['source']}. {takeover_artifact_line(reference)} (lastVerdict, remainingFindingIds, uncommittedFiles). The worktree is unchanged; you own the candidate and must report what you kept."
    return prompt


def uncommitted_files(workspace: dict[str, str]) -> dict[str, Any]:
    """Capture bounded, explicitly partial inventories; the frozen sidecar carries recovery."""
    return {
        key: _git_inventory(workspace[key])
        for key in ("codeRoot", "memoryRoot")
        if key in workspace
    }


@dataclass(slots=True)
class _InventoryCapture:
    output: bytearray = field(default_factory=bytearray)
    errors: bytearray = field(default_factory=bytearray)
    bytes_captured: int = 0

    def append(self, channel: str, chunk: bytes) -> str:
        remaining = _INVENTORY_MAX_BYTES - self.bytes_captured
        self.bytes_captured += min(remaining, len(chunk))
        target = self.output if channel == "stdout" else self.errors
        target.extend(chunk[:remaining])
        if len(chunk) > remaining:
            return "Git inventory byte limit reached"
        if len(self.errors) > _INVENTORY_STDERR_BYTES:
            del self.errors[_INVENTORY_STDERR_BYTES:]
            return "Git inventory diagnostic byte limit reached"
        if self.output.count(0) > _INVENTORY_MAX_FILES:
            return "Git inventory file limit reached (rename paths count separately)"
        return ""


def _git_inventory(root: str) -> dict[str, Any]:
    capture = _InventoryCapture()
    command = [
        "git",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-C",
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        reason = f"Git inventory unreadable: {str(error)[:400]}"
    else:
        try:
            reason = _read_inventory(process, capture)
        except OSError as error:
            reason = f"Git inventory unreadable: {str(error)[:400]}"
        finally:
            cleanup = _reap_inventory(process)
        reason = reason or cleanup
    return _inventory_result(root, capture, reason)


def _read_inventory(process: subprocess.Popen[bytes], capture: _InventoryCapture) -> str:
    deadline = time.monotonic() + _INVENTORY_TIMEOUT_SECONDS
    assert process.stdout is not None and process.stderr is not None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "Git inventory time limit reached"
            for key, _ in selector.select(remaining):
                size = min(4096, _INVENTORY_MAX_BYTES - capture.bytes_captured + 1)
                chunk = os.read(key.fd, size)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                reason = capture.append(key.data, chunk)
                if reason:
                    return reason
    try:
        code = process.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        return "Git inventory time limit reached"
    if code:
        return f"Git inventory unreadable (exit {code})"
    if capture.output and not capture.output.endswith(b"\0"):
        return "Git inventory ended with an incomplete status record"
    return ""


def _reap_inventory(process: subprocess.Popen[bytes]) -> str:
    """End the child's group, including helpers that might still hold an output pipe."""
    try:
        if process.returncode is None and os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=_INVENTORY_REAP_SECONDS)
    except subprocess.TimeoutExpired:
        return "Git inventory termination has not been confirmed; inspect the Git child before retrying"
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    return ""


def _inventory_result(root: str, capture: _InventoryCapture, reason: str) -> dict[str, Any]:
    # Discard an unfinished last record rather than presenting a truncated filename as fact.
    records = capture.output.split(b"\0")[:-1][:_INVENTORY_MAX_FILES]
    result: dict[str, Any] = {
        "root": root,
        "status": [record.decode("utf-8", errors="replace") for record in records],
        "complete": not reason,
        "bytesCaptured": capture.bytes_captured,
    }
    if reason:
        result.update(
            incomplete=reason,
            unreadable=capture.errors.decode("utf-8", errors="replace"),
            recovery=(
                "This frozen inventory is incomplete. Outside role_start, run "
                f"git -C {shlex.quote(root)} status --porcelain=v1 -z --untracked-files=all "
                "and save the full output in your role report; do not rewrite the frozen takeover material."
            ),
        )
    return result
