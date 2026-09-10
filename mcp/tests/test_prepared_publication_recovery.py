"""Actual memory certification, private Git outputs, and interrupted publication recovery."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.worktrees.worktree_contract import load_contract
from test_closeout_memory_certification_reuse import (
    _assert_created_memory_requires_refresh,
    _created_prepared_memory_case,
    _fixture,
    _refresh_created_memory_coherence,
)
from test_worktree_support import git

pytestmark = pytest.mark.integration


def test_real_gate_five_prepares_then_publishes_memory_and_ledger(tmp_path: Path) -> None:
    script = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "from agents_remember_test_support.testing.global_state import begin_pytest_process",
            "from agents_remember.application.worktree_services import build_default_worktree_services",
            "from agents_remember.worktrees.services import bind_worktree_services",
            "begin_pytest_process()",
            "bind_worktree_services(build_default_worktree_services())",
            f"from test_prepared_publication_recovery import {_prepare_memory_output_scenario.__name__}",
            "_prepare_memory_output_scenario(Path(sys.argv[1]))",
        )
    )
    with subprocess.Popen(
        [sys.executable, "-B", "-c", script, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=900)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            pytest.fail(f"memory preparation worker timed out\n{stdout}\n{stderr}")
        assert process.returncode == 0, stdout + stderr


def _prepare_memory_output_scenario(root: Path) -> None:
    from agents_remember.kernel.memory_ledger import (  # noqa: PLC0415
        find_mapping,
        parse_ledger_text,
    )
    from agents_remember.worktrees.integration.closeout.preparation.code_view import (  # noqa: PLC0415
        prepare_code_view,
    )
    from agents_remember.worktrees.integration.closeout.preparation.memory_execution import (  # noqa: PLC0415
        observe_prepared_memory_candidate,
    )
    from agents_remember.worktrees.integration.closeout.preparation.memory_output import (  # noqa: PLC0415
        prepare_memory_outputs,
    )
    from agents_remember.worktrees.integration.closeout.preparation.memory_port import (  # noqa: PLC0415
        PreparedMemoryCertificationRequest,
    )
    from agents_remember.worktrees.integration.closeout.prepared_certification import (  # noqa: PLC0415
        PreparedMemoryCertificationAdapter,
    )

    with pytest.MonkeyPatch.context() as patch:
        fixture = _fixture(root, patch)
    # Restore the scheduler-only code-view stub before exercising the actual owner.
    handoff, view = prepare_code_view(fixture.handoff)
    assert view.disposition == "existing"
    candidate = observe_prepared_memory_candidate(handoff, view)
    assert len(handoff.selected.terminals) == 4
    request = PreparedMemoryCertificationRequest(handoff, candidate)
    adapter = PreparedMemoryCertificationAdapter()
    certified = adapter.certify(request)
    assert adapter.observe(replace(request, handoff=certified.handoff)) == certified.memoryInputs
    memory_root = handoff.contract.memory_worktree
    assert memory_root is not None
    roots = (handoff.contract.code_worktree, memory_root)
    before = tuple(git(path, "rev-parse", "HEAD") for path in roots)
    ledger_before = (memory_root / "memory.md").read_bytes()
    prepared = prepare_memory_outputs(certified)
    assert prepared.memory.intent.writeEnabled and prepared.ledger.intent.writeEnabled
    memory_private = Path(prepared.memory.intent.privateRoot or "")
    ledger_private = Path(prepared.ledger.intent.privateRoot or "")
    memory_commit = git(memory_private, "rev-parse", "HEAD")
    ledger_commit = git(ledger_private, "rev-parse", "HEAD")
    assert git(memory_private, "rev-parse", "HEAD^{tree}") == candidate.memoryTree
    assert git(memory_private, "rev-parse", "HEAD^") == before[1]
    assert git(ledger_private, "rev-parse", "HEAD^") == memory_commit
    assert git(memory_root, "diff", "--name-only", memory_commit, ledger_commit) == "memory.md"
    mapping = find_mapping(parse_ledger_text(prepared.ledgerBytes.decode()), view.codeCommit)
    assert mapping is not None and mapping.memory_commit == memory_commit
    assert (ledger_private / "memory.md").read_bytes() == prepared.ledgerBytes
    journal = handoff.store.path.read_bytes()
    repeated = prepare_memory_outputs(replace(certified, handoff=prepared.handoff))
    assert repeated == prepared
    assert handoff.store.path.read_bytes() == journal
    assert tuple(git(path, "rev-parse", "HEAD") for path in roots) == before
    assert (memory_root / "memory.md").read_bytes() == ledger_before

    _publish_and_recover_memory_outputs(
        prepared, view.codeCommit, memory_commit, ledger_commit, ledger_before
    )
    _prepare_memory_only_successor_scenario(root / "memory-only-successor")


def _prepare_memory_only_successor_scenario(  # noqa: PLR0915
    root: Path, *, complete_successor: bool = True
) -> None:
    """Exercise the public red Gate-5, corrective successor, and retained code output."""
    from datetime import UTC, datetime  # noqa: PLC0415
    from uuid import uuid4  # noqa: PLC0415

    from agents_remember.application import worktree_tools  # noqa: PLC0415
    from agents_remember.errors import FinalCertificationError  # noqa: PLC0415
    from agents_remember.kernel.primitives.runtime_config import load_config  # noqa: PLC0415
    from agents_remember.models.certification.corrective import (  # noqa: PLC0415
        CorrectiveInputChange,
        RedCatalogDisposition,
    )
    from agents_remember.models.lifecycles.operation import CloseoutOperationInput  # noqa: PLC0415
    from agents_remember.models.lifecycles.preparation import (  # noqa: PLC0415
        PreparedCloseoutOutput,
    )
    from agents_remember.worktrees.integration.closeout import (  # noqa: PLC0415
        prepared_certification,
    )
    from agents_remember.worktrees.integration.closeout.certification.execution import (  # noqa: PLC0415
        current_certification_handoff,
        execute_selected_closeout,
    )
    from agents_remember.worktrees.integration.closeout.certification.selection import (  # noqa: PLC0415
        load_typed,
    )
    from agents_remember.worktrees.integration.closeout.preparation.continuation import (  # noqa: PLC0415
        PreparedCloseoutContinuation,
    )
    from agents_remember.worktrees.integration.closeout.preparation.memory_execution import (  # noqa: PLC0415
        observe_prepared_memory_candidate,
    )
    from agents_remember.worktrees.integration.lifecycle import (  # noqa: PLC0415
        lifecycle_operations,
    )
    from agents_remember.worktrees.integration.lifecycle.control import (  # noqa: PLC0415
        cancellation,
    )
    from agents_remember.worktrees.integration.lifecycle.worker.termination import (  # noqa: PLC0415
        worker_process_fingerprint,
    )
    from agents_remember.worktrees.modules.quality.certification_records import (  # noqa: PLC0415
        certificate_store,
    )
    from agents_remember.worktrees.services import (  # noqa: PLC0415
        bind_worktree_services,
        worktree_services,
    )
    from closeout_input_test_support import start_operation_record  # noqa: PLC0415
    from test_closeout_memory_certification_reuse import _store  # noqa: PLC0415

    with pytest.MonkeyPatch.context() as patch:
        case = _created_prepared_memory_case(root)
        # Reuse the real stamping assertions before entering the lifecycle successor path.
        _assert_created_memory_requires_refresh(case)
        _refresh_created_memory_coherence(case)

        bind_worktree_services(
            replace(
                worktree_services(),
                certification_continuation=PreparedCloseoutContinuation(),
            )
        )
        quality = prepared_certification.run_memory_quality_check

        def red_quality(*args, **kwargs):
            observed = quality(*args, **kwargs)
            checks = dict(observed["checks"])
            check_name = next(iter(checks))
            check = dict(checks[check_name])
            check.update(ok=False, findingCount=max(1, int(check.get("findingCount", 0) or 0)))
            checks[check_name] = check
            return {**observed, "checks": checks}

        patch.setattr(prepared_certification, "run_memory_quality_check", red_quality)
        with pytest.raises(FinalCertificationError) as red:
            execute_selected_closeout(case.contract, case.handoff.record, case.handoff.store)
        assert red.value.status == "prepared-memory-certification-red"

    red_record = case.handoff.store.read()
    assert red_record is not None
    red_handoff = current_certification_handoff(case.contract, red_record, case.handoff.store)
    assert tuple(item.result.gate for item in red_handoff.selected.terminals) == (1, 2, 3, 4, 5)
    assert all(item.result.disposition == "green" for item in red_handoff.selected.terminals[:4])
    red_terminal = red_handoff.selected.terminals[-1]
    assert red_terminal.result.disposition == "red"
    assert case.handoff.record.preparation is not None
    original_code_leg = case.handoff.record.preparation.legs[0]
    assert original_code_leg.output is not None
    original_code_output = load_typed(
        certificate_store(case.contract.worktree_group),
        original_code_leg.output,
        PreparedCloseoutOutput,
    )

    memory_before = observe_prepared_memory_candidate(red_handoff, case.view).memoryTree
    with case.feature_doc.open("a", encoding="utf-8") as stream:
        stream.write("\nMemory-only corrective reconciliation.\n")
    git(case.memory, "add", case.feature_doc.relative_to(case.memory).as_posix())
    _refresh_created_memory_coherence(case)
    memory_after = observe_prepared_memory_candidate(red_handoff, case.view).memoryTree

    failed = [item for item in red_terminal.result.railResults if item.status == "fail"]
    blocked = [item for item in red_terminal.result.railResults if item.status == "blocked"]
    assert failed and not blocked, red_terminal.result.railResults

    original_input = red_record.input
    assert original_input is not None
    assert isinstance(original_input, CloseoutOperationInput)
    original_effective_input = original_input.effectiveInput
    original_code_message = original_effective_input.message_for("code")
    original_memory_message = original_effective_input.message_for("memory")
    original_ledger_message = original_effective_input.message_for("ledger")
    cancellation_request = worktree_tools.OperationControlRequest(
        contract_path=case.contract.contract_path.as_posix(),
        operation_kind="closeout",
        action="cancel",
        expected_generation=red_record.generation,
        intent_note="Cancel the red Gate-5 generation before its memory-only successor.",
    )

    def prove_fixture_exit(request):
        return request.model_copy(
            update={
                "state": "exited",
                "observedAt": datetime.now(UTC).isoformat(),
                "detail": "fixture proved the red Gate-5 worker exit",
            }
        )

    config_path = case.contract.coordination_root.parent / "settings.json"
    with mock.patch.object(
        cancellation,
        "signal_worker_and_prove_exit",
        side_effect=prove_fixture_exit,
    ):
        cancelled = worktree_tools.worktree_operation_control_tool(
            load_config(config_path),
            cancellation_request,
        )
    assert cancelled["ok"] is True and cancelled["state"] == "cancelled", cancelled

    cancelled_record = red_handoff.store.read()
    assert cancelled_record is not None and cancelled_record.status == "cancelled"
    change = CorrectiveInputChange(
        inputKind="memory-tree",
        inputId="candidate",
        beforeDigest=memory_before,
        afterDigest=memory_after,
    )
    dispositions = tuple(
        RedCatalogDisposition(
            rail=item.rail,
            priorStatus="fail",
            priorResultDigest=item.resultDigest,
            correctiveOwner=item.correctiveOwner,
            disposition="direct-repair",
            changedInputs=(change,),
            rationale="The curator published the exact corrected memory candidate tree.",
        )
        for item in failed
    )
    with mock.patch.object(lifecycle_operations, "launch_detached_worker") as launch:
        resumed = worktree_tools.worktree_operation_control_tool(
            load_config(config_path),
            worktree_tools.OperationControlRequest(
                contract_path=case.contract.contract_path.as_posix(),
                operation_kind="closeout",
                action="resume",
                expected_generation=cancelled_record.generation,
                intent_note="Resume the repaired memory-only closeout candidate.",
                code_commit_message=original_code_message,
                memory_commit_message=original_memory_message,
                ledger_commit_message=original_ledger_message,
                corrective_dispositions=dispositions,
            ),
        )
    assert resumed["ok"] is True and resumed["state"] == "queued", resumed
    launch.assert_called_once()

    successor_contract = load_contract(case.contract.contract_path)
    successor_store = _store(successor_contract)
    successor = successor_store.read()
    assert successor is not None and successor.generation == red_record.generation + 1
    assert successor.preparedCodeRetention is not None
    assert successor.preparation is not None and successor.preparation.legs[0].output is not None
    successor_output = load_typed(
        certificate_store(successor_contract.worktree_group),
        successor.preparation.legs[0].output,
        PreparedCloseoutOutput,
    )
    assert (
        successor_output.commit,
        successor_output.tree,
        successor_output.committerDate,
    ) == (
        original_code_output.commit,
        original_code_output.tree,
        original_code_output.committerDate,
    )

    fingerprint = worker_process_fingerprint(os.getpid())
    assert fingerprint is not None
    lease = uuid4().hex * 2
    successor_store.update(
        lambda record: record.model_copy(
            update={
                "workerPid": os.getpid(),
                "workerLease": lease,
                "workerProcessFingerprint": fingerprint,
            }
        )
    )
    owner = start_operation_record(successor_store)
    successor_handoff = current_certification_handoff(successor_contract, owner, successor_store)
    assert any(
        change.changeClass == "closeout-resume" and change.consumingGates == (5,)
        for change in successor_handoff.selected.recovery.semanticEnvelope.inputChanges
    )
    reuse = successor_handoff.selected.recovery.semanticEnvelope.reusePlan
    assert reuse.firstGateToRun == 5
    assert tuple(item.gate for item in reuse.reusedCertificates) == (1, 2, 3, 4)
    if not complete_successor:
        return
    completed = execute_selected_closeout(successor_contract, owner, successor_store)
    assert completed.returncode == 0
    final_contract = load_contract(case.contract.contract_path)
    assert final_contract.closeout_status == "completed"
    assert successor_store.read() is not None
    final_record = successor_store.read()
    assert final_record is not None
    final_handoff = current_certification_handoff(final_contract, final_record, successor_store)
    assert tuple(item.result.gate for item in final_handoff.selected.terminals) == (1, 2, 3, 4, 5)
    assert all(item.result.disposition == "green" for item in final_handoff.selected.terminals)


def _interrupt_and_resume_memory_publication(
    prepared, memory_commit: str, ledger_commit: str, ledger_before: bytes
):
    from agents_remember.worktrees.integration.closeout.preparation import (  # noqa: PLC0415
        finalization,
    )
    from agents_remember.worktrees.integration.closeout.preparation.continuation import (  # noqa: PLC0415
        PreparedCloseoutContinuation,
    )

    handoff = prepared.handoff
    memory_root = handoff.contract.memory_worktree
    assert memory_root is not None
    publish = finalization.publish_git_closeout_ref
    prove = finalization._record_proof
    publications: list[str] = []

    def interrupt_after_publication(capability):
        result = publish(capability)
        if result.command is not None:
            assert result.command.returncode == 0 and result.after.state == "new"
            publications.append(capability.binding.prepared_commit)
            raise RuntimeError("interrupt after actual ref publication")
        return result

    def interrupt_after_memory_proof(bundle, selected, leg, proof):
        prove(bundle, selected, leg, proof)
        if leg == "memory":
            raise RuntimeError("interrupt after actual memory proof")

    def resume():
        current = handoff.store.read()
        assert current is not None
        return finalization.resume_prepared_closeout(handoff.contract, current, handoff.store)

    with mock.patch.object(finalization, "publish_git_closeout_ref", interrupt_after_publication):
        with pytest.raises(RuntimeError, match="interrupt after actual ref publication"):
            PreparedCloseoutContinuation().finalize(handoff)
        interrupted = handoff.store.read()
        assert interrupted is not None
        assert interrupted.mutationEvidence["memory"].state == "mutation-intent"
        assert git(memory_root, "rev-parse", "HEAD") == memory_commit
        assert (memory_root / "memory.md").read_bytes() == ledger_before
        with (
            mock.patch.object(finalization, "_record_proof", interrupt_after_memory_proof),
            pytest.raises(RuntimeError, match="interrupt after actual memory proof"),
        ):
            resume()
        proven_memory = handoff.store.read()
        assert proven_memory is not None
        assert proven_memory.mutationEvidence["memory"].state == "commit-proven"
        assert proven_memory.mutationEvidence["ledger"].state == "pre-mutation"
        assert publications == [memory_commit]
        # This resume must reobserve the proven M ref before first publishing L.
        with pytest.raises(RuntimeError, match="interrupt after actual ref publication"):
            resume()
        interrupted_ledger = handoff.store.read()
        assert interrupted_ledger is not None
        assert interrupted_ledger.mutationEvidence["ledger"].state == "mutation-intent"
        assert git(memory_root, "rev-parse", "HEAD") == ledger_commit
        closed = resume()
    assert publications == [memory_commit, ledger_commit]
    return closed


def _publish_and_recover_memory_outputs(
    prepared, code_commit: str, memory_commit: str, ledger_commit: str, ledger_before: bytes
) -> None:
    from agents_remember.worktrees.integration.closeout.preparation import (  # noqa: PLC0415
        finalization,
    )

    handoff = prepared.handoff
    memory_root = handoff.contract.memory_worktree
    assert memory_root is not None
    closed = _interrupt_and_resume_memory_publication(
        prepared, memory_commit, ledger_commit, ledger_before
    )
    assert closed is not None and closed.returncode == 0
    assert git(handoff.contract.code_worktree, "rev-parse", "HEAD") == code_commit
    assert git(memory_root, "rev-parse", "HEAD") == ledger_commit
    assert (memory_root / "memory.md").read_bytes() == prepared.ledgerBytes
    contract = load_contract(handoff.contract.contract_path)
    assert contract.closeout_status == "completed"
    assert (contract.code_commit, contract.memory_content_commit, contract.ledger_commit) == (
        code_commit,
        memory_commit,
        ledger_commit,
    )
    current = handoff.store.read()
    assert current is not None
    journal = handoff.store.path.read_bytes()
    reopened = finalization.resume_prepared_closeout(contract, current, handoff.store)
    assert reopened == closed
    assert handoff.store.path.read_bytes() == journal
    assert git(memory_root, "rev-parse", "HEAD") == ledger_commit
