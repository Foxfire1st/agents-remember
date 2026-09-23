"""ICR-R22@v1: what the *live* review read renders about the movement a managed sync measured.

`test_review_sync_rebinding.py` owns the sync-side cases: the states a managed sync can report and the
durable rebinding record it publishes. This module owns the other half of the same obligation -- what a
reader of the ordinary review sees through the shipped `read_knowledge_review`: that a recorded sync which
moved the reviewed inputs cannot keep rendering as untouched (the packet's own non-conforming example), that
an input the sync never compared is rendered as unmeasured rather than as agreement, that a record which
cannot be used is its own state, and that the movement value's own validator refuses every false shape a
producer could hand it.

It shares its enclosure fixture with the sync-side module rather than duplicating it: two fixtures would be
two places for "what a leaf enclosure is" to drift, and the sibling already owns that construction -- the
same way the repository's other case modules build on a sibling's fixture. It is a separate module because
the two together are past the file-size rail, and the seam chosen is the one the production owners already
have: *what the sync measured* beside *what the read says about it*. The raw-Git boundary this surface
also publishes is `test_review_external_git_movement_read.py`'s, which owns the other half of the same
question: what the read says when no managed sync ran at all.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import read_knowledge_review
from agents_remember.application.review_sync_rebinding import (
    rebinding_file_name,
    rebinding_result_block,
    resolved_pair_completed,
)
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.models.knowledge.review_staleness import ReviewSyncMovement
from pydantic import ValidationError
from test_review_sync_rebinding import NEWLINE, ReviewSyncFixture, commit_file, git


class LiveReviewMovementTests(unittest.TestCase):
    """The read-side cases: the shipped review read renders what the sync measured."""

    def test_the_live_review_read_renders_what_the_sync_moved(self) -> None:
        """F6: the ordinary review read cannot keep reading as untouched after a recorded sync.

        The packet's non-conforming example is exactly this state -- the old review staying current
        while its inputs lag the merged line -- so the primary surface, not only the sync payload and
        the reopen channel, has to render it. The read is the shipped one
        (:func:`read_knowledge_review`) and the measurement it renders is the durable rebinding
        record's, checked against the generation the leaf published.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            # The leaf already holds the content the official line is about to adopt, so the sync
            # below carries a commit without changing the reviewed capture -- a measured agreement.
            worktree = fixture.contract.code_worktree
            extra = worktree / "src" / "extra.py"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text("VALUE = 'carried by both sides'" + NEWLINE, encoding="utf-8")
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            fixture.publish_reviewed_candidate()

            # (1) No sync has reported yet: absence of a measurement, not a measured agreement, and
            # nothing about the read changes.
            untouched = read_knowledge_review(fixture.config, fixture.review_request())
            assert untouched.state == "review", untouched.refusal
            payload = untouched.payload
            assert payload is not None
            assert payload.sync_movement is None
            assert payload.staleness.state == "current", payload.staleness

            # (2) A sync that carried the official line without changing the reviewed inputs: the
            # generation still describes the pair, and the read says so as a measurement.
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(
                    fixture.code_repo, "src/extra.py", "VALUE = 'carried by both sides'"
                ),
            )
            carried = fixture.sync(memory_sync_choice="skip-memory")
            assert carried["ok"] is True, carried
            agreed = read_knowledge_review(fixture.config, fixture.review_request())
            payload = agreed.payload
            assert payload is not None
            movement = payload.sync_movement
            assert movement is not None, agreed
            assert movement.binding_state == "current", movement
            assert movement.moved_identities == (), movement
            assert movement.generation_id == reviewed.generation_id
            assert payload.staleness.state == "current", payload.staleness

            # (3) The official line moves again, the sync carries it, and the reviewed capture moves
            # with it: the ordinary read must now render the movement rather than a current review.
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(fixture.code_repo, "src/landed.py", "VALUE = 'landed'"),
            )
            moved = fixture.sync(memory_sync_choice="skip-memory")
            assert moved["ok"] is True, moved
            assert moved["review_rebinding"]["state"] == "moved", moved

            assert_the_read_renders_the_movement(fixture, reviewed, moved["review_rebinding"])

    def test_an_uncompared_knowledge_channel_is_rendered_unmeasured(self) -> None:
        """G1: a retained operand the sync never compared is never rendered as agreement.

        The record's own verdict has three outcomes, and this is the third: the leaf already holds the
        content the official line adopts (so the source channel is measured and matches), the
        generation retained a knowledge operand, and the declared publication location holds nothing
        -- so the record says ``unmeasured``. The live read must render that as an unmeasured channel
        with the record's own reason, never as a movement and never as an agreement about a dataset
        nothing compared.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            extra = worktree / "src" / "extra.py"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text("VALUE = 'carried by both sides'" + NEWLINE, encoding="utf-8")
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            # Deliberately NO publication: the declared location holds nothing, so the record's
            # knowledge channel is unmeasured.
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(
                    fixture.code_repo, "src/extra.py", "VALUE = 'carried by both sides'"
                ),
            )
            payload = fixture.sync(memory_sync_choice="skip-memory")
            assert payload["ok"] is True, payload
            assert payload["review_rebinding"]["state"] == "unmeasured", payload

            read = read_knowledge_review(fixture.config, fixture.review_request())
            assert read.state == "review", read.refusal
            body = read.payload
            assert body is not None
            movement = body.sync_movement
            assert movement is not None, read
            assert movement.binding_state == "not-measured", movement
            # No fabricated movement: nothing differed, so nothing is named as moved.
            assert movement.moved_identities == (), movement
            assert movement.reason is not None, movement
            assert "not-recorded" in movement.reason, movement.reason
            assert movement.generation_id == reviewed.generation_id
            # No agreement clause, and specifically none about the dataset nothing compared.
            statement = movement.statement
            assert "still describes the pair it resolved" not in statement, statement
            assert "the reviewed dataset" not in statement, statement
            assert "unmeasured" in statement, statement
            # R17's staleness stays its own fact about the composed comparison; the absence is
            # carried by the movement beside it rather than by a claim in this field.
            assert body.staleness.state in {"current", "stale", "not_compared"}, body.staleness
            assert body.staleness.statement != movement.statement, body.staleness

            # The movement's own validator refuses every combination that would render a false
            # claim: a measurement carrying a reason, an absence carrying none, and a moved input
            # reported as anything but a movement.
            published = movement.model_dump(mode="json")
            for forged in (
                {**published, "binding_state": "current"},
                {**published, "reason": None},
                {**published, "moved_identities": ("code-candidate-tree:" + "0" * 40,)},
            ):
                with pytest.raises(ValidationError):
                    ReviewSyncMovement.model_validate(forged)

    def test_a_record_that_cannot_be_used_is_reported_unavailable(self) -> None:
        """G1: an unusable record is its own state, not an absence and not a measurement.

        Two ways a record is unusable, and both must say so: its bytes are not a readable record, and
        it is a valid record that does not describe this generation. Neither may be silent, and
        neither may be rendered as a measurement.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            fixture.publish_reviewed_candidate()
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(fixture.code_repo, "src/landed.py", "VALUE = 'landed'"),
            )
            moved = fixture.sync(memory_sync_choice="skip-memory")
            assert moved["review_rebinding"]["state"] == "moved", moved
            destination = durable_reports_root(fixture.contract.task_root) / rebinding_file_name(
                fixture.contract.leaf_id, reviewed.generation_id
            )
            recorded = destination.read_text(encoding="utf-8")

            # (a) bytes that are not a readable record
            destination.write_text("{not a record", encoding="utf-8")
            unreadable = read_knowledge_review(fixture.config, fixture.review_request())
            assert unreadable.payload is not None
            movement = unreadable.payload.sync_movement
            assert movement is not None, unreadable
            assert movement.binding_state == "unavailable", movement
            assert movement.record_readable is False, movement
            assert movement.moved_identities == (), movement
            assert movement.reason is not None, movement
            assert "not a readable" in movement.reason, movement.reason
            assert "still describes the pair it resolved" not in movement.statement

            # (b) a valid record that names another comparison
            forged = json.loads(recorded)
            forged["supersedes_binding_digest"] = "0" * 64
            destination.write_text(json.dumps(forged), encoding="utf-8")
            other = read_knowledge_review(fixture.config, fixture.review_request())
            assert other.payload is not None
            movement = other.payload.sync_movement
            assert movement is not None, other
            assert movement.binding_state == "unavailable", movement
            assert movement.reason is not None, movement
            assert "does not describe" in movement.reason, movement.reason
            assert movement.moved_identities == (), movement

            # H2, pinned where it can be pinned without touching production bytes: BOTH `unavailable`
            # sub-cases report `record_readable` False, so that one field cannot separate an
            # unreadable artifact from a valid record that names another generation -- the sub-fact is
            # carried by `reason`, and the two reasons are distinguishable. The field-level fix
            # (renaming it, or narrowing its documented meaning) needs a production-byte change in
            # `models/knowledge/review_staleness.py` and is held for the master's ruling.
            assert movement.record_readable is False

    def test_a_carrying_state_reported_as_a_failure_is_not_measured(self) -> None:
        """G2: the success conjunct, so a failed result cannot be measured as a resolution.

        Every producer of the three carrying states returns zero today, so this pins the requirement
        rather than a live producer: a payload carrying ``ok: false`` beside a carrying state must not
        be recorded, and the block says so instead of measuring it.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            fixture.publish_reviewed_candidate()
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(fixture.code_repo, "src/landed.py", "VALUE = 'landed'"),
            )
            payload = fixture.sync(memory_sync_choice="skip-memory")

            assert resolved_pair_completed(payload) is True, payload
            failed = {**payload, "ok": False}
            assert resolved_pair_completed(failed) is False, failed

            destination = durable_reports_root(fixture.contract.task_root) / rebinding_file_name(
                fixture.contract.leaf_id, reviewed.generation_id
            )
            destination.unlink()
            blocked = rebinding_result_block(fixture.reload_contract(), failed)
            assert blocked["review_rebinding"]["state"] == "not-measured", blocked
            assert destination.exists() is False

    def test_the_movement_validator_refuses_each_false_shape(self) -> None:
        """H1: the four validator clauses no case pinned, each driven against a false movement.

        A per-clause sweep found these four unpinned: a movement with nothing moved, an unusable
        record reported as readable, an agreement naming a resolved identity, and a movement naming
        none. Each forgery below is built to violate exactly **one** of them -- so the refusal is that
        clause's and not a neighbour's -- and the real published movement is the accepted control.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            extra = worktree / "src" / "extra.py"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text("VALUE = 'carried by both sides'" + NEWLINE, encoding="utf-8")
            fixture.commit_leaf_candidate()
            fixture.freeze_review()
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(
                    fixture.code_repo, "src/extra.py", "VALUE = 'carried by both sides'"
                ),
            )
            payload = fixture.sync(memory_sync_choice="skip-memory")
            assert payload["ok"] is True, payload
            read = read_knowledge_review(fixture.config, fixture.review_request())
            assert read.payload is not None and read.payload.sync_movement is not None
            published = read.payload.sync_movement.model_dump(mode="json")

            # The real published movement is the accepted control.
            assert ReviewSyncMovement.model_validate(published).binding_state == "not-measured"

            identity = "1" * 40
            # The unusable state and the agreement state, each as a valid base so every forgery below
            # departs from exactly one clause.
            unusable = {**published, "binding_state": "unavailable", "record_readable": False}
            agreement = {**published, "binding_state": "current", "reason": None}
            forgeries = {
                # nothing moved, and an identity named so the missing-identity clause cannot be the
                # one that refuses it
                "stale_with_nothing_moved": {
                    **agreement,
                    "binding_state": "stale",
                    "resolved_code_head": identity,
                    "resolved_candidate_code_tree_id": identity,
                },
                # a record that could not be used, reported as readable
                "unusable_reported_readable": {**unusable, "record_readable": True},
                # an agreement naming a resolved identity
                "agreement_with_identity": {**agreement, "resolved_code_head": identity},
                # a movement naming no resolved identity
                "stale_without_identity": {
                    **agreement,
                    "binding_state": "stale",
                    "moved_identities": (f"code-candidate-tree:{identity}",),
                },
            }
            refused: list[str] = []
            for label, forged in forgeries.items():
                with pytest.raises(ValidationError):
                    ReviewSyncMovement.model_validate(forged)
                refused.append(label)
            assert refused == [
                "stale_with_nothing_moved",
                "unusable_reported_readable",
                "agreement_with_identity",
                "stale_without_identity",
            ]


def assert_the_read_renders_the_movement(fixture, reviewed, rebinding) -> None:
    """The phase the packet names: the ordinary read renders what the recorded sync moved."""

    read = read_knowledge_review(fixture.config, fixture.review_request())
    assert read.state == "review", read.refusal
    payload = read.payload
    assert payload is not None
    movement = payload.sync_movement
    assert movement is not None, read
    assert movement.binding_state == "stale", movement
    assert movement.moved_identities and all(
        identity.startswith("code-candidate-tree:") for identity in movement.moved_identities
    ), movement
    assert movement.generation_id == reviewed.generation_id
    assert movement.reviewed_binding_digest == reviewed.binding_digest
    assert movement.resolved_code_head == git(
        fixture.contract.code_worktree, "rev-parse", "HEAD"
    ), movement
    assert movement.resolved_candidate_code_tree_id == fixture.capture_tree()
    # The comparison this read composed is NOT the recorded generation, and the read says so.
    assert payload.comparison is not None
    assert payload.comparison.binding_digest != reviewed.binding_digest
    assert payload.staleness.state == "stale", payload.staleness
    assert payload.staleness.moved == movement.moved_identities
    assert payload.staleness.previous_comparison_ref == movement.reviewed_binding_digest
    assert "does not describe it" in payload.staleness.statement, payload.staleness.statement
    assert "freeze_review_comparison" in movement.successor_action
    # A review whose inputs a sync moved is not offered for submission.
    assert payload.submission.state == "disabled_stale", payload.submission
