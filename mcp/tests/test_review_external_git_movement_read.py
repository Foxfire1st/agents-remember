"""ICR-R23@v1: the raw-Git identity boundary the shipped review and closeout surfaces publish.

`worktree_sync` is the one route that moves a leaf's declared identities under measurement. Everything
else that can move them -- `git rebase`, `git cherry-pick`, `git revert`, a checkout that leaves its
declared branch -- is ordinary Git and leaves no record behind, so a reader holding only "a comparison
generation was frozen" cannot tell a rewritten branch from an untouched one.

These cases drive real Git in real enclosures and then read the shipped entry points: the ordinary
review read (`application.knowledge_review.read_knowledge_review`), the closeout preview and apply
tools, and the integration tool. They pin the support matrix the code publishes against the matrix the
repository documents, and they pin the states that must never be flattened into one another -- an
untouched leaf, an advanced branch, a rewritten one, a checkout that left its branch, a recorded
object that is gone, and a generation that cannot be read.

It lives beside `test_review_sync_movement_read.py` rather than inside it: that module owns what the
*managed* sync's rebinding renders on the same read, and the two together are past the file-size rail,
so the seam is the one the production owners already have -- the managed measurement beside the raw
one. The enclosure fixture and the closeout enclosure are the siblings' own, shared rather than
rebuilt, for the same reason.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.knowledge_review import read_knowledge_review
from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    read_manifest,
)
from agents_remember.application.review_external_git_movement import (
    GIT_TRANSITION_SUPPORT,
    external_git_movement_result_block,
    render_git_transition_support,
    supported_recovery,
    unsupported_transitions,
)
from agents_remember.models.knowledge.review_external_movement import ExternalGitMovement
from agents_remember.worktrees.worktree_contract import load_contract
from pydantic import ValidationError
from test_review_final_output_receipt import (
    _closeout,
    _freeze_review,
    _publish_dataset,
    _review_closeout_fixture,
)
from test_review_sync_rebinding import NEWLINE, ReviewSyncFixture, commit_file, git


class RawGitIdentityBoundaryTests(unittest.TestCase):
    """ICR-R23@v1: the read renders a raw Git transition, and the support matrix says what exists.

    The defect these cases protect is the packet's non-conforming example in its second form: a review
    whose declared identities a raw operation replaced keeps reading as current because no managed sync
    ran and therefore no rebinding record exists. Every case drives real Git in the enclosure's own
    work branch and then reads through the shipped entry point.
    """

    def test_a_raw_rebase_is_measured_and_the_review_stops_reading_as_current(self) -> None:
        """A rewritten work branch is a measured movement, and the generation survives it.

        The review is frozen, the work branch is then rebased inside the fixture with no managed sync
        anywhere in the case, and the ordinary read is asked again. It must report the replaced head,
        disable submission, name the successor-generation recovery, and leave the reviewed generation
        byte-identical: measuring movement is not the act of publishing a successor.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            recorded_head = git(fixture.contract.code_worktree, "rev-parse", "HEAD")
            assert reviewed.source.candidate_capture.observedCodeHead == recorded_head
            directory = fixture.generation_directory(reviewed.generation_id)

            # A real Git rewrite of the very branch the contract declared: the official line grows one
            # commit and the leaf's own commit is replayed onto it, so the recorded head is no longer
            # in the branch's history. Nothing here calls worktree_sync.
            rebased_tip = _rebase_work_branch_onto_a_new_official_commit(fixture)
            assert rebased_tip != recorded_head
            assert git(fixture.contract.code_worktree, "rev-parse", "HEAD") == rebased_tip
            assert (
                _git_ok(
                    fixture.contract.code_repo_path,
                    "merge-base",
                    "--is-ancestor",
                    recorded_head,
                    rebased_tip,
                )
                is False
            )
            # ...and the object is still there: the record was replaced, not lost, which is the fact a
            # reader acts on differently.
            assert git(fixture.contract.code_repo_path, "cat-file", "-t", recorded_head) == "commit"

            read = read_knowledge_review(fixture.config, fixture.review_request())

            assert read.state == "review", read.refusal
            payload = read.payload
            assert payload is not None
            movement = payload.external_git_movement
            assert movement is not None, read
            assert movement.binding_state == "stale", movement
            assert movement.transitions[0] == "rebase", movement
            assert movement.moved_identities == (f"code-work-branch:{recorded_head}",), movement
            assert movement.reason is None, movement
            assert movement.generation_id == reviewed.generation_id
            assert movement.generation_index == reviewed.generation_index
            assert movement.reviewed_binding_digest == reviewed.binding_digest
            assert movement.declared_work_branch == fixture.contract.code_work_branch
            assert movement.observed_code_work_branch_head == rebased_tip, movement
            assert movement.generation_readable is True
            # The exact identity the manifest recorded is the identity the report names as replaced,
            # and the tree the review captured is still readable beside it: the object survived the
            # rewrite, which is why the generation stays reopenable and why the replacement -- not a
            # loss -- is what the reader is told.
            assert f"code-work-branch:{recorded_head}" in movement.moved_identities
            assert (
                git(
                    fixture.contract.code_repo_path,
                    "cat-file",
                    "-t",
                    reviewed.source.candidate_code_tree_id,
                )
                == "tree"
            )
            # The recovery is a successor generation, and nothing in this read performed one.
            assert "freeze_review_comparison" in movement.successor_action, movement
            assert supported_recovery("rebase").recovery_action in movement.successor_action, (
                movement
            )
            # The packet's failure clause: stale assessment cannot be reused as current.
            assert payload.staleness.state == "stale", payload.staleness
            assert payload.staleness.moved == movement.moved_identities
            assert payload.submission.state == "disabled_stale", payload.submission
            assert payload.staleness.statement == movement.statement
            # The boundary example: the reviewed generation stays inspectable, byte for byte.
            kept = read_manifest(directory / COMPARISON_MANIFEST_NAME)
            assert kept.model_dump(mode="json") == reviewed.model_dump(mode="json")

    def test_a_managed_sync_alone_is_not_reported_as_a_raw_transition(self) -> None:
        """The control: the route that *is* managed moves the pair without triggering this report.

        The same fixture, the same freeze, and then the shipped sync -- not raw Git. The recorded head
        is still an ancestor of the branch the sync left behind, so the boundary measures an ordinary
        append and the read keeps its comparison; the movement this leaf reports is the *unmanaged*
        one, and a report that also claimed the managed one would be measuring the same fact twice and
        naming it wrong once.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            worktree = fixture.contract.code_worktree
            extra = worktree / "src" / "extra.py"
            extra.parent.mkdir(parents=True, exist_ok=True)
            extra.write_text("VALUE = 'carried by both sides'" + NEWLINE, encoding="utf-8")
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            recorded_head = git(worktree, "rev-parse", "HEAD")

            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(
                    fixture.code_repo, "src/extra.py", "VALUE = 'carried by both sides'"
                ),
            )
            payload = fixture.sync(memory_sync_choice="skip-memory")
            assert payload["ok"] is True, payload
            # The sync left a merge whose parent is the recorded head, so nothing was replaced.
            assert git(worktree, "rev-parse", "HEAD") != recorded_head
            assert (
                _git_ok(
                    fixture.contract.code_repo_path,
                    "merge-base",
                    "--is-ancestor",
                    recorded_head,
                    git(worktree, "rev-parse", "HEAD"),
                )
                is True
            )

            read = read_knowledge_review(fixture.config, fixture.review_request())
            assert read.state == "review", read.refusal
            body = read.payload
            assert body is not None
            movement = body.external_git_movement
            assert movement is not None, read
            assert movement.binding_state == "current", movement
            assert movement.transitions == ("ordinary-append",), movement
            assert movement.moved_identities == (), movement
            assert movement.reason is None, movement
            assert movement.generation_id == reviewed.generation_id
            assert movement.observed_code_work_branch_head == git(worktree, "rev-parse", "HEAD")
            # The managed movement is still reported by its own owner, and the two are separate fields
            # because they are separate measurements.
            assert body.sync_movement is not None, body
            # The comparison the read composed is the one the sync left, so the stale state the
            # rebase case earned is not being claimed here.
            assert body.staleness.state in {"current", "stale"}, body.staleness

    def test_a_worktree_that_left_its_declared_branch_reports_the_switch(self) -> None:
        """A switch makes the work-branch comparison unmeasurable, and unmeasurable is not current.

        A detached HEAD is the shape a person leaves behind by checking something else out. Nothing
        can be concluded about the declared work branch from it, so the report says ``not-measured``
        with the reason and names the two steps a person takes -- there is no silent comparison
        against whatever happens to be checked out.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            worktree = fixture.contract.code_worktree
            git(worktree, "checkout", "--detach", "HEAD")
            assert git(worktree, "branch", "--show-current") == ""

            read = read_knowledge_review(fixture.config, fixture.review_request())
            assert read.state == "review", read.refusal
            payload = read.payload
            assert payload is not None
            movement = payload.external_git_movement
            assert movement is not None, read
            assert movement.binding_state == "not-measured", movement
            assert "branch-switch" in movement.transitions, movement
            assert movement.moved_identities == (), movement
            assert movement.reason is not None, movement
            assert "detached HEAD" in movement.reason, movement.reason
            assert "declared work branch" in movement.reason, movement.reason
            assert movement.observed_code_work_branch_head is None, movement
            assert movement.observed_declared_source_branch_head is not None, movement
            assert movement.declared_work_branch == fixture.contract.code_work_branch
            assert f"code-work-branch:{movement.declared_work_branch}" not in (
                movement.moved_identities
            )
            assert movement.generation_id == reviewed.generation_id
            # The absence is never promoted into a movement, the recovery is still stated, and the
            # boundary's own sentence is what the review surface publishes: the mounted sentence
            # cannot claim the comparison is current when a channel was never compared (H3), and it
            # does not disable submission, because no movement was observed.
            assert (
                supported_recovery("branch-switch").recovery_action in movement.successor_action
            ), movement
            assert "return the worktree to its declared work branch" in movement.successor_action
            assert "current" not in movement.transitions
            assert payload.staleness.state == "not-measured", payload.staleness
            assert payload.staleness.statement == movement.statement, payload.staleness
            assert "current comparison" not in payload.staleness.statement, payload.staleness
            assert payload.submission.state != "disabled_stale", payload.submission

    def test_each_forward_moving_transition_is_exercised_and_reported_as_what_it_is(self) -> None:
        """The other three named transitions, each performed for real and each honestly reported.

        A cherry-pick, a revert and an ordinary commit all move the branch *forward*: the head the
        review recorded stays in its history, so no ancestry check can tell them apart, and this
        module's matrix says so instead of implying that it detects them. Each case performs the real
        operation in the enclosure's own repository and reads the same entry point, requiring the
        report to name the shape it measured and to leave the recovery route in place -- because the
        packet forbids claiming universal raw-Git automation and an unsupported transition has to be
        stated as unsupported rather than silently implied by a green read.
        """

        cases = {
            "cherry-pick": _cherry_pick_an_official_commit_into_the_work_branch,
            "revert": _revert_the_leaves_own_commit,
            "ordinary-append": _commit_another_leaf_change,
        }
        for transition, operation in cases.items():
            with self.subTest(transition=transition), tempfile.TemporaryDirectory() as tmp:
                fixture = ReviewSyncFixture(Path(tmp))
                fixture.commit_leaf_candidate()
                reviewed = fixture.freeze_review()
                recorded_head = git(fixture.contract.code_worktree, "rev-parse", "HEAD")
                directory = fixture.generation_directory(reviewed.generation_id)
                tip = operation(fixture)
                assert tip != recorded_head, transition

                read = read_knowledge_review(fixture.config, fixture.review_request())
                assert read.state == "review", read.refusal
                assert read.payload is not None
                movement = read.payload.external_git_movement
                assert movement is not None, read
                assert movement.binding_state == "current", (transition, movement)
                assert movement.transitions == ("ordinary-append",), (transition, movement)
                assert movement.moved_identities == (), (transition, movement)
                assert movement.reason is None, (transition, movement)
                assert movement.observed_code_work_branch_head == tip, (transition, movement)
                assert movement.generation_id == reviewed.generation_id
                # The transition itself is documented, and the matrix states its reconciliation
                # rather than leaving a reader to infer one.
                row = supported_recovery(transition)
                assert row.reconciliation in {"supported", "unsupported"}
                assert row.transition == transition
                # Nothing in the read published a successor; the reviewed generation is intact.
                assert read_manifest(directory / COMPARISON_MANIFEST_NAME).model_dump(
                    mode="json"
                ) == reviewed.model_dump(mode="json"), transition

    def test_a_rewritten_official_line_replaces_the_recorded_source_base(self) -> None:
        """The declared source branch is a channel of its own, and a rewrite of it is measured too.

        A leaf forks from a recorded base. Rewriting that branch's history leaves the leaf's own work
        branch untouched and the recorded base no longer in the source line, so a boundary that only
        compared the work branch would report this repository as unaffected. The case therefore
        asserts the source channel specifically: the recorded base is named as replaced and the state
        is stale, which is the difference between "the leaf's work moved" and "the line it forked
        from moved".
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            recorded_base = fixture.contract.code_base_commit
            assert reviewed.source.baseline_code_tree_id == recorded_base
            work_head = git(fixture.contract.code_worktree, "rev-parse", "HEAD")

            repo = fixture.contract.code_repo_path
            source = fixture.contract.code_source_branch
            # The recorded base *itself* is rewritten: stage one change, hard-reset back to the base
            # and amend it, so the source branch stands at a different commit with no descendant link
            # to the one the leaf forked from.
            _on_branch(repo, source, lambda: commit_file(repo, "src/first.py", "VALUE = 'first'"))
            _on_branch(repo, source, lambda: git(repo, "reset", "--hard", recorded_base))
            _on_branch(repo, source, lambda: git(repo, "commit", "--amend", "-m", "rewritten base"))
            rewritten_tip = git(repo, "rev-parse", source)
            assert rewritten_tip != recorded_base
            assert (
                _git_ok(repo, "merge-base", "--is-ancestor", recorded_base, rewritten_tip) is False
            )
            # The leaf's own branch was not touched by that rewrite.
            assert git(fixture.contract.code_worktree, "rev-parse", "HEAD") == work_head

            read = read_knowledge_review(fixture.config, fixture.review_request())

            assert read.state == "review", read.refusal
            assert read.payload is not None
            movement = read.payload.external_git_movement
            assert movement is not None, read
            assert movement.binding_state == "stale", movement
            assert f"declared-source-branch:{recorded_base}" in movement.moved_identities, movement
            assert movement.observed_declared_source_branch_head == rewritten_tip, movement
            assert movement.reason is None, movement
            # The work branch is still where the review captured it, and the evidence says so rather
            # than the report blaming a channel that did not move.
            assert ("code-work-branch", "current") in movement.transition_evidence, movement
            assert read.payload.submission.state == "disabled_stale", read.payload.submission

    def test_an_untouched_leaf_reports_no_transition_at_all(self) -> None:
        """B1: the commonest state of all names no event, because none happened.

        A leaf nobody has touched produces this value on every ordinary review, and its three channels
        are all exactly at their recorded identities. Labelling that ``ordinary-append`` asserted an
        advance the branch never made, and the sentence beside it described work continuing under a
        frozen generation. The report must instead observe that there is no transition, say so in its
        own words, and keep the per-channel evidence that proves it.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            head = git(fixture.contract.code_worktree, "rev-parse", "HEAD")

            read = read_knowledge_review(fixture.config, fixture.review_request())

            assert read.state == "review", read.refusal
            payload = read.payload
            assert payload is not None
            movement = payload.external_git_movement
            assert movement is not None, read
            assert movement.binding_state == "current", movement
            assert movement.transitions == ("unchanged",), movement
            assert movement.transition_evidence == (
                ("code-work-branch", "current"),
                ("declared-source-branch", "current"),
                ("memory-work-branch", "current"),
            ), movement
            assert movement.moved_identities == (), movement
            assert movement.reason is None, movement
            assert movement.observed_code_work_branch_head == head, movement
            assert movement.generation_id == reviewed.generation_id
            # The sentence names the shape it observed -- which is none -- and never an advance.
            assert "observed no transition" in movement.statement, movement.statement
            assert "advanced" not in movement.statement, movement.statement
            assert "hash-level" not in movement.statement, movement.statement
            # L5: the agreement sentence has no uncompared-channel clause, because a ``current``
            # state is reachable only when every channel was compared.
            assert "not compared" not in movement.statement, movement.statement
            assert supported_recovery("unchanged").recovery_action in movement.successor_action, (
                movement
            )
            # The review keeps R17's own current state: nothing about the reader's comparison moved.
            assert payload.staleness.state == "current", payload.staleness
            assert payload.submission.state != "disabled_stale", payload.submission

    def test_a_missing_recorded_object_is_not_reported_as_a_branch_switch(self) -> None:
        """B1 (second state): a recorded object that is gone is not a checkout nobody performed.

        The worktree is still on the branch the contract declared; what is missing is the recorded
        head's object itself, which the repository's own reclamation may remove once the branch has
        been rewritten away from it. Deriving the switch from "this channel could not be compared"
        named a branch switch that never happened. The state must stay ``not-measured``, the shape
        list must contain no switch, and the sentence must not claim one.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            fixture.freeze_review()
            recorded_head = git(fixture.contract.code_worktree, "rev-parse", "HEAD")
            _rebase_work_branch_onto_a_new_official_commit(fixture)
            branch = fixture.contract.code_work_branch
            assert git(fixture.contract.code_worktree, "branch", "--show-current") == branch
            _delete_loose_object(fixture.contract.code_worktree, recorded_head)
            assert (
                _git_ok(fixture.contract.code_repo_path, "cat-file", "-e", recorded_head) is False
            )

            read = read_knowledge_review(fixture.config, fixture.review_request())

            assert read.state == "review", read.refusal
            payload = read.payload
            assert payload is not None
            movement = payload.external_git_movement
            assert movement is not None, read
            assert movement.binding_state == "not-measured", movement
            assert "branch-switch" not in movement.transitions, movement
            assert movement.moved_identities == (), movement
            assert movement.observed_code_work_branch_head is None, movement
            assert movement.reason is not None, movement
            assert "not a readable commit object" in movement.reason, movement.reason
            assert "branch switch" not in movement.statement, movement.statement
            # The absence sentence is true about what *was* compared: the source branch advanced, so
            # saying the boundary compared nothing would itself be false about the store.
            assert "did not compare every declared identity" in movement.statement, (
                movement.statement
            )
            assert "the identities it did compare are reported beside this value" in (
                movement.statement
            ), movement.statement
            assert payload.staleness.state == "not-measured", payload.staleness
            assert payload.staleness.statement == movement.statement, payload.staleness

    def test_a_generation_that_cannot_be_read_is_unavailable_not_absent(self) -> None:
        """H1: "never reviewed" and "the review record cannot be read" are two different values.

        The generation store distinguishes a leaf that published nothing from one whose generation
        directories hold no readable manifest; the boundary flattened both into "no measurement". The
        second is ``unavailable``, it carries the selection's own detail and the recovery that names
        it, and the review surface publishes it instead of a current comparison.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            manifest_path = (
                fixture.generation_directory(reviewed.generation_id) / COMPARISON_MANIFEST_NAME
            )
            manifest_path.write_text("{not a manifest at all", encoding="utf-8")

            read = read_knowledge_review(fixture.config, fixture.review_request())

            assert read.state == "review", read.refusal
            payload = read.payload
            assert payload is not None
            movement = payload.external_git_movement
            assert movement is not None, read
            assert movement.binding_state == "unavailable", movement
            assert movement.generation_readable is False, movement
            assert movement.transitions == (), movement
            assert movement.transition_evidence == (), movement
            assert movement.moved_identities == (), movement
            assert movement.generation_id == "00000000-0000-0000-0000-000000000000", movement
            # The selection's own detail travels: the reader is told which fact was measured.
            assert movement.reason is not None, movement
            assert "none holds a readable manifest" in movement.reason, movement.reason
            assert "restore the readable comparison generation" in movement.statement, (
                movement.statement
            )
            assert "current comparison" not in movement.statement, movement.statement
            assert payload.staleness.state == "not-measured", payload.staleness
            assert payload.staleness.statement == movement.statement, payload.staleness

        # The contrasting case in the same expression: a leaf that published nothing is an absence of
        # a boundary rather than an unreadable one, and it publishes no value at all.
        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            read = read_knowledge_review(fixture.config, fixture.review_request())
            assert read.state == "review", read.refusal
            assert read.payload is not None
            assert read.payload.external_git_movement is None, read.payload.external_git_movement

    def test_the_documented_matrix_and_the_published_matrix_are_one_table(self) -> None:
        """The support matrix is documented where a reader looks, and the two cannot drift.

        The packet requires the four named transitions to be *exercised and documented* rather than
        implied, so the documentation is asserted against the table the code publishes -- rendered
        from it, so a row whose verdict changes changes the document -- and every transition's own
        record is required to state its reconciliation rather than leaving it to be inferred.
        """

        documented = (
            Path(__file__).resolve().parents[2] / "docs" / "reference" / "worktrees-c09.md"
        ).read_text(encoding="utf-8")
        rendered = render_git_transition_support()
        assert rendered in documented, rendered
        assert "Raw Git Identity Boundary" in documented

        for row in GIT_TRANSITION_SUPPORT:
            # Every transition the packet names is documented with both verdicts visible.
            assert f"`{row.transition}`" in documented, row
            assert f"**{row.reconciliation}**" in rendered, row
            found = supported_recovery(row.transition)
            assert found == row
            if row.reconciliation == "unsupported":
                assert f"{row.transition}:unsupported" in unsupported_transitions()
                assert "not" in row.recovery_action or "never" in row.recovery_action, row
        # The transitions this leaf must exercise, named in the matrix, each with a row of its own.
        for transition in ("rebase", "cherry-pick", "revert", "branch-switch"):
            assert f"`{transition}`" in documented, transition
            assert supported_recovery(transition).transition == transition
        # The two shapes this system genuinely reconciles, and the one recovery it performs.
        assert supported_recovery("ordinary-append").reconciliation == "supported"
        assert supported_recovery("unchanged").reconciliation == "supported"
        assert "freeze_review_comparison" in supported_recovery("rebase").recovery_action
        assert unsupported_transitions() == (
            "rebase:unsupported",
            "cherry-pick:unsupported",
            "revert:unsupported",
            "branch-switch:unsupported",
        )
        with pytest.raises(KeyError):
            supported_recovery("reset --hard")
        # L1: the prose that introduces the table counts what the table holds. It said "the two that
        # this system does not reconcile" while four rows are unsupported.
        unsupported_rows = [
            row for row in GIT_TRANSITION_SUPPORT if row.reconciliation == "unsupported"
        ]
        assert len(unsupported_rows) == 4
        assert "the two that this system does not reconcile" not in documented
        assert "the four\nthis system does not reconcile" in documented
        # B2: the cherry-pick row's recovery names only routes that measurably exist for a pick. The
        # rebinding exists only after a sync, the reopen channel reports the recorded generation's
        # availability, and this boundary's own state stays current -- so the row must not point a
        # reader at an existing measurement of the pick.
        pick = supported_recovery("cherry-pick").recovery_action
        assert "read the existing measurements that do take it" not in pick, pick
        assert "reopen channel" in pick, pick
        assert "only once a sync has carried the official line and resolved a pair" in pick, pick
        assert "A sync that carries nothing resolves no pair and records no rebinding" in pick, pick
        assert "publish a successor generation" in pick, pick
        assert pick in documented, pick

    def test_the_movement_validator_refuses_each_false_shape(self) -> None:
        """The report cannot claim a measurement it did not make.

        Each forgery below departs from exactly one clause, and the real published movement is the
        accepted control -- so removing a clause makes its forgery read as a valid report. The
        mutation each guard answers is named beside it.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            fixture.freeze_review()
            _rebase_work_branch_onto_a_new_official_commit(fixture)
            read = read_knowledge_review(fixture.config, fixture.review_request())
            assert read.payload is not None
            published = read.payload.external_git_movement
            assert published is not None, read
            accepted = published.model_dump(mode="json")
            assert ExternalGitMovement.model_validate(accepted).binding_state == "stale"

            identity = "1" * 40
            forgeries = {
                # Remove the "a stale report names what was replaced" clause and this reads as a
                # movement nobody observed.
                "stale_with_nothing_replaced": {**accepted, "moved_identities": ()},
                # Remove the "only a measurement is reasonless" clause and this publishes an absence
                # beside a measurement.
                "measurement_with_a_reason": {**accepted, "reason": "nothing was compared"},
                # Remove the "a named replacement is stale" clause and a movement reads as current.
                "replaced_while_current": {
                    **accepted,
                    "binding_state": "current",
                    "moved_identities": (f"code-work-branch:{identity}",),
                },
                # Remove the "an absence carries its reason" clause and a reader cannot tell an
                # unperformed check from an unaffected repository.
                "absence_without_a_reason": {
                    **accepted,
                    "binding_state": "not-measured",
                    "moved_identities": (),
                    "reason": None,
                },
                # Remove the "generation_readable follows from the state" clause and an unreadable
                # generation claims a measurement taken from it (L2: the clause this pins). Every
                # other clause is satisfied on purpose -- a nonempty reason, no replacement, no other
                # absence -- so this forgery is refused by that clause and by no neighbour.
                "unreadable_but_read": {
                    **accepted,
                    "binding_state": "unavailable",
                    "moved_identities": (),
                    "reason": "the record could not be read",
                    "generation_readable": True,
                },
                # Remove the "'unchanged' means nothing moved" clause and the commonest state of all
                # travels beside a replacement it says did not happen.
                "unchanged_beside_a_replacement": {
                    **accepted,
                    "transitions": ["unchanged", "rebase"],
                },
                # ...and beside a state that is not a measurement at all.
                "unchanged_while_not_measured": {
                    **accepted,
                    "binding_state": "not-measured",
                    "transitions": ["unchanged"],
                    "moved_identities": (),
                },
            }
            refused: list[str] = []
            for label, forged in forgeries.items():
                with pytest.raises(ValidationError):
                    ExternalGitMovement.model_validate(forged)
                refused.append(label)
            assert refused == list(forgeries)

    def test_the_unchanged_value_is_the_one_the_control_state_publishes(self) -> None:
        """B1: the accepted control for the agreement state, so the two new clauses are pinned.

        The published control state is validated as it is, and the refusal above is what proves the
        clauses bite; this case pins the positive direction -- the value the shipped read produces on
        an untouched leaf is a valid report, not a forgery the validator happens to allow.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            fixture.freeze_review()
            read = read_knowledge_review(fixture.config, fixture.review_request())
            assert read.payload is not None
            published = read.payload.external_git_movement
            assert published is not None, read
            accepted = published.model_dump(mode="json")
            assert accepted["transitions"] == ["unchanged"]
            assert accepted["binding_state"] == "current"
            assert ExternalGitMovement.model_validate(accepted).transitions == ("unchanged",)


def _delete_loose_object(repository: Path, commit: str) -> Path:
    """Remove one commit object from the object store, so the recorded identity is *gone*, not merely
    unreachable.

    The fixture repositories are built fresh and never packed, so the object is a loose file under the
    shared object store; the caller asserts afterwards that Git really can no longer read it, which is
    what makes this a missing object rather than a moved one.
    """

    common = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    path = common / "objects" / commit[:2] / commit[2:]
    assert path.is_file(), f"{path} is not a loose object, so this fixture cannot remove it"
    path.unlink()
    return path


def _rebase_work_branch_onto_a_new_official_commit(fixture: ReviewSyncFixture) -> str:
    """Rewrite the leaf's own work branch with real Git, and return the branch tip it left.

    One new commit lands on the declared source branch, and the work branch is replayed onto it -- the
    raw operation the packet names, performed in the enclosure's own repositories with no managed
    transaction involved. The returned tip is read back from the branch itself rather than assumed.
    """

    repo = fixture.contract.code_repo_path
    branch = fixture.contract.code_work_branch
    source = fixture.contract.code_source_branch
    base = fixture.contract.code_base_commit
    _on_branch(repo, source, lambda: commit_file(repo, "src/official.py", "VALUE = 'landed'"))
    # Run the rebase inside the worktree: the branch is checked out there, which is the state a person
    # performing the operation is in, and Git refuses to move a branch from the repository that does
    # not hold it.
    git(fixture.contract.code_worktree, "rebase", "--onto", git(repo, "rev-parse", source), base)
    return git(repo, "rev-parse", branch)


def _cherry_pick_an_official_commit_into_the_work_branch(fixture: ReviewSyncFixture) -> str:
    """Apply one official-line commit to the leaf's branch with real ``git cherry-pick``."""

    repo = fixture.contract.code_repo_path
    source = fixture.contract.code_source_branch
    _on_branch(repo, source, lambda: commit_file(repo, "src/picked.py", "VALUE = 'picked'"))
    picked = git(repo, "rev-parse", source)
    git(fixture.contract.code_worktree, "cherry-pick", picked)
    return git(fixture.contract.code_worktree, "rev-parse", "HEAD")


def _revert_the_leaves_own_commit(fixture: ReviewSyncFixture) -> str:
    """Undo the leaf's captured commit with real ``git revert`` on its own branch."""

    worktree = fixture.contract.code_worktree
    recorded_head = git(worktree, "rev-parse", "HEAD")
    git(worktree, "revert", "--no-edit", recorded_head)
    return git(worktree, "rev-parse", "HEAD")


def _commit_another_leaf_change(fixture: ReviewSyncFixture) -> str:
    """Advance the work branch with one more ordinary commit, which is the shape all three share."""

    commit_file(fixture.contract.code_worktree, "src/continued.py", "VALUE = 'continued'")
    return git(fixture.contract.code_worktree, "rev-parse", "HEAD")


def _on_branch(repo: Path, branch: str, action) -> None:
    """Run ``action`` with ``repo`` checked out on ``branch``, restoring the previous checkout."""

    previous = git(repo, "symbolic-ref", "--short", "HEAD")
    git(repo, "checkout", branch)
    try:
        action()
    finally:
        git(repo, "checkout", previous)


def _git_ok(repo: Path, *args: str) -> bool:
    """Run one predicate-shaped Git command and answer with its exit status rather than its output."""

    return (
        subprocess.run(
            ["git", *args], cwd=repo, text=True, capture_output=True, check=False
        ).returncode
        == 0
    )


def test_the_closeout_and_integration_results_carry_the_boundary_statement(
    tmp_path, worktree_services
) -> None:
    """H2: the closeout boundary states the measured movement, and never gates on it.

    The packet names three boundaries -- review, authoring and closeout -- and the closeout owner
    already carries the attachment shape R21 and R22 use. This case drives the real
    ``worktree_closeout_preview_tool``, the real ``worktree_closeout_apply_tool`` and the real
    ``worktree_integrate_tool`` over an enclosure whose work branch was rewritten after the review was
    frozen, and requires each result to carry the boundary's own measured statement **beside** the
    work it finalised: the closeout still closes and the integration still lands, because an attached
    statement is not a gate.

    It reuses the R21 module's enclosure fixture rather than building a second closeout-ready
    enclosure: that fixture already owns what "a leaf the closeout door admits" means, and a second
    one would be a second place for it to drift.
    """

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    worktree = contract.code_worktree
    git(worktree, "add", "--all")
    git(worktree, "commit", "-m", "the leaf's committed candidate")
    recorded_head = git(worktree, "rev-parse", "HEAD")
    frozen = _freeze_review(enclosure)
    _publish_dataset(contract, enclosure.candidate_database)
    assert frozen.source.candidate_capture.observedCodeHead == recorded_head
    # A real rewrite of the reviewed head: the tree is unchanged, so nothing but the recorded
    # *identity* moved, which is exactly the fact this boundary measures and the closeout door does
    # not.
    git(worktree, "commit", "--amend", "-m", "rewritten after the review was frozen")
    assert git(worktree, "rev-parse", "HEAD") != recorded_head

    preview, applied = _closeout(enclosure)

    for result in (preview, applied):
        assert result["ok"] is True, result
        reported = result["external_git_movement"]
        assert reported["binding_state"] == "stale", reported
        assert reported["moved_identities"] == [f"code-work-branch:{recorded_head}"], reported
        # The work branch was rewritten; another channel may also have advanced, and the report
        # names every shape it observed rather than choosing one.
        assert "rebase" in reported["transitions"], reported
        assert reported["generation_id"] == frozen.generation_id, reported
        assert list(unsupported_transitions()) == reported["unsupported"], reported
        assert "is no longer in its history" in reported["statement"], reported
    # It is a statement and not a gate: the closeout really closed, with the same receipt it always
    # carried beside the new block.
    assert applied["state"] == "closed", applied
    assert applied["final_output_receipt"]["state"] == "recorded", applied

    integrated = worktree_tools.worktree_integrate_tool(
        enclosure.config, contract_path=contract.contract_path.as_posix(), strategy="ff-only"
    )
    assert integrated["ok"] is True, integrated
    reported = integrated["external_git_movement"]
    assert reported["binding_state"] == "stale", reported
    assert list(unsupported_transitions()) == reported["unsupported"], reported


def test_a_result_whose_leaf_published_nothing_states_the_absence(
    tmp_path, worktree_services
) -> None:
    """H2/R2-F1: a result with no generation states *that* absence rather than a disjunction.

    ``external_git_movement_result_block`` never raises and never refuses, so a leaf the boundary
    cannot measure still gets a typed block naming the state and repeating the four routes this system
    does not reconcile. This leaf declares a work branch and IS a leaf enclosure, so the only cause
    left is the store's own: the detail must name the unpublished generation in the store's words,
    never offer the two causes that do not apply.
    """

    enclosure = _review_closeout_fixture(tmp_path)
    preview, applied = _closeout(enclosure)
    assert enclosure.contract.kind == "leaf"
    assert enclosure.contract.code_work_branch

    for result in (preview, applied):
        assert result["ok"] is True, result
        reported = result["external_git_movement"]
        assert reported["state"] == "not-measured", reported
        assert "no boundary was measured" in reported["detail"], reported
        assert "no comparison generation is published" in reported["detail"], reported
        assert "declares no work branch" not in reported["detail"], reported
        assert "records kind" not in reported["detail"], reported
        assert list(unsupported_transitions()) == reported["unsupported"], reported
    assert applied["state"] == "closed", applied


def test_each_cause_of_a_missing_boundary_gets_its_own_sentence(
    tmp_path, worktree_services
) -> None:
    """R2-F1: the absence detail names the cause the measurement established, never a disjunction.

    ``external_git_movement_result_block`` is the production entry point the closeout and integration
    tools call. It used to publish one sentence covering every cause -- "this contract declares no work
    branch, *or* the leaf has published no comparison generation" -- which told a reader that something
    applied without saying which, and in the commonest case (a leaf that was never reviewed) offered a
    cause that did not apply. Each cause is now named by the arm that established it, so this case
    drives three real contracts to three distinct details: a non-leaf enclosure, a leaf that declares
    no work branch, and a leaf that published nothing (driven through the real closeout tools by the
    case above).
    """

    enclosure = _review_closeout_fixture(tmp_path)
    leaf = enclosure.contract
    assert leaf.parent_contract_path is not None
    series = load_contract(leaf.parent_contract_path)
    assert series.kind == "series" and series.code_work_branch

    blocks = {
        "not-a-leaf": external_git_movement_result_block(series, {"ok": True})[
            "external_git_movement"
        ],
        "no-work-branch": external_git_movement_result_block(
            replace(leaf, code_work_branch=""), {"ok": True}
        )["external_git_movement"],
    }

    for cause, reported in blocks.items():
        assert reported["state"] == "not-measured", (cause, reported)
        assert list(unsupported_transitions()) == reported["unsupported"], (cause, reported)
        assert "no boundary was measured" in reported["detail"], (cause, reported)

    assert "records kind 'series'" in blocks["not-a-leaf"]["detail"], blocks["not-a-leaf"]
    assert "declares no work branch" in blocks["no-work-branch"]["detail"], blocks["no-work-branch"]
    # Each cause names itself and not its neighbours: a reader can tell "not a leaf enclosure" from
    # "never reviewed" from "no declared work branch".
    assert "declares no work branch" not in blocks["not-a-leaf"]["detail"]
    assert "records kind" not in blocks["no-work-branch"]["detail"]
    assert "comparison generation is published" not in blocks["not-a-leaf"]["detail"]
    assert "comparison generation is published" not in blocks["no-work-branch"]["detail"]

    # A failed operation still carries nothing at all: the statement is about finalised work.
    failed = external_git_movement_result_block(replace(leaf, code_work_branch=""), {"ok": False})
    assert failed == {"ok": False}
