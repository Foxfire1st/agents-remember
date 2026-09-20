"""The guidance a tool response carries must be bound to that response's own task.

`260918-TSIP` `T54`: a `worktree_closeout_apply` response for one leaf of one master shipped a
`nextStep` whose `nextArgs.contract_path` named a **different master's** contract, while the
outer `nextTool` named the caller's own operation. The closeout itself was unambiguous
(`state: closed`, the right commits, the right leaf) -- only the guidance block, the thing a
seat is told to follow, pointed into another task's enclosure.

**The mechanism, traced rather than assumed.** Guidance is produced by
``application/next_step.py::next_step_for`` from the *process-global* ambient lifecycle: it
reads ``LifecycleState.enclosure`` and runs the worktree guidance state machine against that
contract. The response, meanwhile, has its own address. The only thing that connects the two is
``application/tool_response.py::bound_next_step``, and that guard fired on neither of its two
exits for the recorded payload:

* it returned the step unchecked when the response declared **no** contract path of its own --
  and a closed closeout declared none, because ``status_payload`` emits the snake_case
  ``contract_path`` while the guard reads the envelope's ``contractPath``/``enclosurePath``;
* it required the guidance's path set to be **exactly** ``{expected}``, so a hint carrying both
  spellings survived whenever only one of them was the response's.

Both are repaired in this leaf: the guard withholds unvalidatable guidance and rejects any
disagreeing spelling, and `_closed_result_payload` now declares the response's own
``contractPath``. The cases below pin the guard's behaviour at its own level, because that is
where the mechanism lives and where the next regression will be cheapest to see.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest import mock

from agents_remember.application.tool_response import bound_next_step
from agents_remember.memory_quality.style.citations import migration
from agents_remember.memory_quality.style.citations.drafts import Result
from agents_remember.memory_quality.style.citations.work_order import Item
from agents_remember.models.base import NextStep
from agents_remember.models.worktree import WorktreeCloseoutApplyResponse
from agents_remember.worktrees.modules import closeout as closeout_module
from agents_remember.worktrees.modules.closeout import (
    _bounded_paths,
    _refreshed_onboarding_paths,
)
from agents_remember.worktrees.queue.closeout_recovery import MemoryCloseoutOutcome
from test_tool_refusal_conformance import refusal_axes

if TYPE_CHECKING:
    from agents_remember.memory_quality.style.citations import source_index

OWN = "/coord/tasks/repo/own-master/enclosures/own-leaf/series-contract.md"
# The product sets BOTH spellings to the contract FILE -- measured on a real `worktree_status`
# response and on all four producers (`guidance.py:171-176` `contract_next_args`, `:443-444`
# `_status_payload_with_landing`, `start_result.py:121`, `reopen.py:104`). An earlier form of
# this fixture used a directory here, a shape no producer emits, which is `L6`'s `F3`.
OWN_ENCLOSURE = OWN
OTHER = "/coord/tasks/repo/other-master/enclosures/other-leaf/series-contract.md"


def guidance(contract_path: str, enclosure_path: str | None = None) -> NextStep:
    """The guidance shape the worktree state machine emits, as `contract_next_args` builds it."""

    args: dict[str, object] = {
        "contract_path": contract_path,
        "enclosure_path": enclosure_path if enclosure_path is not None else contract_path,
        "strategy": "ff-only",
        "dry_run": True,
    }
    return NextStep(
        summary="Closeout completed; integrate the task branches back into their source branches.",
        nextTool="worktree_integrate",
        nextArgs=args,
    )


def closeout_response(**paths: str) -> WorktreeCloseoutApplyResponse:
    """A real closeout response model, so the guard is driven by the envelope it will meet."""

    return WorktreeCloseoutApplyResponse(ok=True, contractPath=paths.get("contractPath"))


def test_guidance_naming_another_task_is_withheld_from_a_response_with_no_address() -> None:
    """The recorded `T54` shape: the response declares nothing, so nothing could be checked.

    This is the case the old guard let through -- `if not response_paths: return step`. A
    response that cannot state its own address cannot vouch for guidance derived from the
    process's ambient enclosure, so the guidance is withheld rather than emitted unchecked.
    """

    response = closeout_response()
    assert response.contractPath is None
    assert bound_next_step(response, guidance(OTHER)) is None


def test_guidance_agreeing_with_the_responses_own_address_is_kept() -> None:
    """The positive control: the guard is not a blanket refusal of guidance.

    Without this case the repair above would be satisfied by dropping every hint, which would
    cost a seat the operational chain the guidance exists to carry.
    """

    response = closeout_response(contractPath=OWN)
    kept = bound_next_step(response, guidance(OWN, OWN_ENCLOSURE))
    assert kept is not None, "the guard dropped guidance that agreed with its own response"
    assert kept.nextTool == "worktree_integrate"
    assert kept.nextArgs is not None
    assert kept.nextArgs["contract_path"] == OWN


def test_one_stale_path_spelling_is_enough_to_withhold_the_guidance() -> None:
    """The second exit: agreement must hold for EVERY spelling the guidance carries.

    The old form compared the guidance's path set to ``{expected}`` as a whole, so a hint
    carrying both ``contract_path`` and ``enclosure_path`` survived when only one of them named
    the response's task. A seat following that hint is sent to the stale one.
    """

    response = closeout_response(contractPath=OWN)
    assert bound_next_step(response, guidance(OWN, OTHER)) is None
    assert bound_next_step(response, guidance(OTHER, OWN)) is None


def test_guidance_that_names_no_artifact_is_left_alone() -> None:
    """Guidance with no path in its args cannot contradict anything, so it is kept."""

    response = closeout_response()
    step = NextStep(
        summary="Notify the developer and stop.", nextTool="lifecycle_turn_end_notification"
    )
    assert bound_next_step(response, step) is step
    assert bound_next_step(response, None) is None


def test_the_refreshed_onboarding_census_names_every_entry_it_counts() -> None:
    """`T71`: a list that counts entries it cannot name is not a report of what was refreshed.

    ``worktrees/modules/onboarding.py::_refresh_regenerated_documents`` appends
    ``{"source_path": "", "onboarding_file": <the document>}`` for a regenerated **document** --
    it has no source file, and its own path is the only identity it has. The closeout payload
    read ``item["source_path"]`` alone, so those entries arrived as empty strings: L5's closeout
    reported ``count 19`` with 2 real paths and 17 blanks, and L4's ``count 42`` with 13 paths.

    Asserted on the two functions the payload is built from, so the repair and the count cannot
    drift: every entry is named, and a blank is counted by neither.
    """

    entries = [
        {"source_path": "mcp/src/a.py", "onboarding_file": "/mem/onboarding/mcp/src/a.py.md"},
        {"source_path": "", "onboarding_file": "/mem/onboarding/mcp/tests/overview.md"},
        {"source_path": "", "onboarding_file": "/mem/onboarding/docs/record.md"},
    ]
    paths = _refreshed_onboarding_paths(entries)
    assert paths == [
        "mcp/src/a.py",
        "/mem/onboarding/mcp/tests/overview.md",
        "/mem/onboarding/docs/record.md",
    ]
    assert all(path.strip() for path in paths), "a counted entry arrived with no name"

    bounded = _bounded_paths(paths)
    assert bounded["count"] == len(entries)
    assert bounded["sample"] == paths

    # The control: the OLD expression, over the same entries, is exactly the defect -- three
    # counted, one named. Run here so the case proves it can see the shape it repaired.
    old = [item["source_path"] for item in entries]
    assert old == ["mcp/src/a.py", "", ""], "the old expression over these entries"
    assert sum(1 for path in old if not path) == 2, "two of the three it counted had no name"
    # And the count itself no longer counts what it cannot name: the same old list through
    # `_bounded_paths` now reports ONE entry, because the blank entries are not paths. The
    # `count 19 / 2 paths / 17 blanks` shape cannot be reproduced by this payload again.
    assert _bounded_paths(old) == {"count": 1, "sample": ["mcp/src/a.py"]}
    assert _bounded_paths(old)["count"] != len(old)


class _StubIndex:
    """The one method `payload` calls on the source index: its telemetry block.

    `payload` reads the index only for `telemetry()`; everything else it reports comes from the
    `Result` it is handed. A stub here is therefore not a double of the behaviour under test --
    it removes the one dependency that would make this case an integration test, and the case
    still executes the builder's own decision line.
    """

    def telemetry(self, *, post_fix_recheck: bool = False) -> dict[str, object]:
        return {"postFixRecheck": post_fix_recheck}


def _migration_payload(*, declined: int = 0, remaining: int = 0, dry_run: bool):
    """Call the real `migration.payload` and return what it produced.

    `Result` is the builder's own input type and its fields default to zero, so the two facts
    `ok` is read off -- `declined` and `remaining` -- are set explicitly and nothing else is
    synthesised.
    """

    result = Result(declined=[_declined_item() for _ in range(declined)], remaining=remaining)
    return migration.payload(
        Path("/coord/memory/onboarding"),
        Path("/coord/repo"),
        result,
        # `payload` reads exactly one member of the index -- `telemetry()` -- so this double is
        # partial BY DESIGN: building a real `RepositoryIndex` would require a live leased
        # snapshot (database handle, lock file, cache paths, metrics) and would turn this case
        # into an integration test of the source index rather than of the migration's `ok`.
        cast("source_index.RepositoryIndex", _StubIndex()),
        dry_run=dry_run,
    )


def _declined_item():
    """One decline, in the shape `work_order.counted`/`orders` read."""

    return Item(
        document="onboarding/a.md",
        line=1,
        kind="table-row",
        code="ambiguous-anchor",
        tier=1,
        action="review",
        message="one extent",
    )


def test_a_migration_preview_reports_its_outcome_instead_of_a_bare_not_ok() -> None:
    """`T64`: two previews of one family answered the same question with opposite `ok` values.

    `citation_migrate(dry_run=True)` returned ``ok: false`` while its sibling `citation_fix`
    previewed the equivalent shape as ``ok: true``, so a caller could not tell "nothing to
    migrate" from "the operation did not happen". The old ``ok`` folded two questions together
    ("did anything change" and "did the call succeed"); the builder now answers the second and
    declares the first separately, in ``state``, ``outcome`` and ``findingsRemaining``.

    **This case CALLS the builder.** An earlier form of it grepped
    ``inspect.getsource(migration.payload)`` for the three literals, which pinned the text and
    not the behaviour: forcing ``blocked = True`` left every case in this leaf green while every
    preview reported ``ok:false`` (the reviewer's M12). What is asserted here is the value the
    builder produces, so that mutation is red by construction.
    """

    planned = _migration_payload(dry_run=True)
    assert planned["ok"] is True, (
        "a dry run that declined nothing did what it set out to do; ok:false here is the "
        "pre-repair shape T64 recorded"
    )
    assert planned["state"] == "planned"
    assert planned["outcome"] == "planned"
    assert planned["dryRun"] is True
    assert planned["findingsRemaining"] is None, (
        "a dry run re-measures nothing, so it must not report a number a reader could mistake "
        "for the post-migration tree"
    )

    converted = _migration_payload(remaining=0, dry_run=False)
    assert converted["ok"] is True
    assert converted["state"] == "converted"
    assert converted["findingsRemaining"] == 0

    declined = _migration_payload(declined=1, dry_run=True)
    assert declined["ok"] is False, "a decline is the ONE thing a dry run may report as a refusal"
    assert declined["state"] == "refused"
    assert declined["declinedCount"] == 1

    still_open = _migration_payload(remaining=3, dry_run=False)
    assert still_open["ok"] is False, "a real run that left work behind did not finish"
    assert still_open["findingsRemaining"] == 3

    # THE DRY-RUN CLAUSE, and it is the one that matters most: a preview is read off `declined`
    # alone. `remaining` is the POST-write re-measurement -- `migrate_onboarding_root` only fills
    # it on the non-dry branch, and `findingsRemaining` above already reports `None` on a dry run
    # precisely because nothing was re-measured. A builder that read `remaining` while previewing
    # would answer `ok:false, state:"planned"` for the input a preview most often has, which is
    # `T64`'s symptom returning through the other fact the old `ok` folded in. The reviewer's M17
    # drops only the `not dry_run` guard, and this row is what catches it.
    preview_with_remaining = _migration_payload(remaining=3, dry_run=True)
    assert preview_with_remaining["ok"] is True, (
        "a preview must not read `remaining`: it is a post-write measurement, and reading it "
        "here reports a plan as a failure"
    )
    assert preview_with_remaining["state"] == "planned"
    assert preview_with_remaining["findingsRemaining"] is None

    # The pre-repair shape is still recognisable as unmarked, so the pin that recorded it was
    # about a real shape rather than about a payload nobody ever emitted.
    assert refusal_axes({"ok": False, "operation": "citation_migrate", "dryRun": True}) == (
        False,
        False,
        False,
    )


def _closed_payload_stub(index: int = 0) -> tuple[str, dict]:
    """Produce a closed-closeout payload through the REAL builder, with one stub.

    `_closed_result_payload` is the producer of the response's own address, and the reviewer's
    M16 showed nothing pinned it: removing its `contractPath` line left every case in this leaf
    green. Driving it needs a contract and a memory outcome; rather than fabricate both, this
    patches the ONE collaborator that is a different responsibility (`status_payload`, the status
    projection of the same contract) and lets the builder's own address line run for real. What
    is asserted is the value the builder produced, not text about it.
    """

    class _Contract:
        contract_path = Path(OWN)

    memory = MemoryCloseoutOutcome(memory_commit="0" * 40)

    with (
        mock.patch.object(
            closeout_module, "status_payload", return_value={"contract_path": OWN, "leaf_id": "L"}
        ),
        mock.patch.object(closeout_module, "_memory_ledger_repair", return_value=None),
    ):
        facts = closeout_module._CloseoutResultFacts(
            code_commit="0" * 40,
            memory=memory,
            integration_reopen={},
            gate_guard=None,
        )
        return OWN, closeout_module._closed_result_payload(_Contract(), facts)


def test_the_closed_closeout_payload_declares_its_own_address_in_the_envelope_spelling() -> None:
    """The producer half of `T54`, driven through the real builder and then through the guard.

    `status_payload` emits the snake_case `contract_path`; the guard reads `contractPath`/
    `enclosurePath`. Before the repair a closed closeout declared **neither** camelCase key, so
    `response_paths` was empty and the repaired guard withholds the guidance entirely -- the seat
    silently loses "integrate the task branches". Both facts are asserted here in one chain:
    the builder emits the key, and the guard then KEEPS guidance that agrees with it.
    """

    path, payload = _closed_payload_stub()

    assert payload["contractPath"] == path, (
        "the closed-closeout payload stopped declaring its own contractPath, which is what lets "
        "bound_next_step validate the guidance it carries (T54, producer half)"
    )
    # `status_payload` contributes the snake_case spelling and nothing else; the camelCase key is
    # the builder's own line, which is exactly why it has to be declared rather than assumed.
    assert payload["contract_path"] == path

    # `ok` is supplied by `_worktree_result` at the application boundary
    # (`{**result.payload, "ok": result.returncode == 0, "operation": operation}`), so it is
    # supplied the same way here rather than pretended to come from the builder.
    response = WorktreeCloseoutApplyResponse.model_validate(
        {"ok": True, "operation": "worktree_closeout_apply", **payload}
    )
    kept = bound_next_step(response, guidance(path, path))
    assert kept is not None and kept.nextArgs is not None, (
        "the payload's own address was not enough for the guard to keep its guidance"
    )
    assert kept.nextArgs["contract_path"] == path
    # And the same payload carrying ANOTHER task's guidance withholds it.
    assert bound_next_step(response, guidance(OTHER, OTHER)) is None
