"""Cross-role handover agreement and corpus convergence checks for turn-truth doctrine.

This module pairs with ``test_lifecycle_turn_truth_doctrine.py``: that module defines the
normalization, vocabulary, and retired-claim detector plus its classifier/teeth/limit tests, while
this module checks the canonical lifecycle corpus itself -- roster agreement, owed boundary
clauses, direct worker-to-reviewer/curator handover routes, delivered coordinator capsule evidence
checks, and reconciled-site convergence teeth.
"""

from __future__ import annotations

import json
import re
import unittest

from test_lifecycle_turn_truth_doctrine import (
    COMPLETION_TRUTH_ROSTER,
    COMPLETION_TRUTH_VOCABULARY,
    DOCTRINE_TREE,
    RELAY_REFERENCE_SURFACES,
    REPOSITORY_ROOT,
    TREE_LITERAL,
    Reading,
    canonical_surfaces,
    classify_reading,
    contradictions,
    normalize,
    read_surface,
)

# The clause sets each declared surface owes. A surface may be legitimately silent on a clause; a
# surface on this roster is not, and the markers are the shipped wording for its own side of the
# boundary. Matching is on normalized words, so emphasis and re-wrapping are free.
#
# Required statements name each current owner's obligations. Architect methods retain
# their artifact-before-ruling boundary without pretending to be separate role turns.
OWED_STATEMENTS: dict[str, tuple[str, ...]] = {
    "skills/l-01-agent-lifecycles/core/acceptance.md": (
        "The relay neither validates artifact content nor invents a gate decision.",
        "means only that the provider turn ended normally",
        "does not establish that the required artifact exists, is current or satisfies its requirement",
        "The host reads that a turn ended and derives the mechanical seat-state signal.",
        "It delivers one line to the structurally current owner waiting for that agent, "
        "carrying the ended agent's last line.",
        "The relay never opens, parses or evaluates reports, verdicts, coherence records "
        "or acceptance envelopes.",
        "On wake, the current gate owner opens and validates the required artifact, candidate "
        "identity, evidence and acceptance envelope before advancing lifecycle state.",
        "it nudges, rejects, replaces or escalates instead of waiting for the relay to check the artifact",
        "Worker, Reviewer and Curator write their durable handoff artifact before ending a "
        "successful handoff turn.",
        "They then send it with `role_message` to the waiting agent.",
        "A filed artifact wakes nobody by itself.",
    ),
    "skills/l-01-agent-lifecycles/templates/master-handover-packet.md": (
        "Before deciding the received handover, read [Keep relay and gate-owner duties separate](../core/acceptance.md#keep-relay-and-gate-owner-duties-separate) for exact-artifact and candidate validation.",
        "The completion and handover boundary this template follows is owned by [acceptance](../core/acceptance.md).",
    ),
    "skills/l-01-agent-lifecycles/templates/turn-report.md": (
        "The [acceptance reference](../core/acceptance.md) owns acceptance",
        "[the checks before a freeze](../operations/implementation.md#operation--implementation) owns targeted handover checks.",
    ),
}


class AgreementAcrossTheRoleSetTests(unittest.TestCase):
    """The property R13 exists for: no canonical surface contradicts the boundary."""

    def test_no_canonical_surface_contradicts_the_boundary(self) -> None:
        surfaces = canonical_surfaces()
        # Fail closed on the tree itself: an empty or shrunken surface set would let this case pass
        # with nothing examined, which is the one way a sweep like this can lie.
        self.assertTrue(
            TREE_LITERAL.is_dir(),
            f"the canonical doctrine tree is missing: {DOCTRINE_TREE}",
        )
        self.assertGreater(
            len(surfaces),
            len(COMPLETION_TRUTH_ROSTER),
            "the contradiction sweep examined no more surfaces than the declared roster",
        )
        findings: list[str] = []
        for path in surfaces:
            surface = path.relative_to(REPOSITORY_ROOT).as_posix()
            findings.extend(
                finding.render(surface)
                for finding in contradictions(path.read_text(encoding="utf-8"))
            )
        self.assertEqual(
            findings,
            [],
            "canonical lifecycle doctrine contradicts the handoff authority boundary "
            "(terminal truth only ends a turn; the owner validates the artifact) in:\n  "
            + "\n  ".join(findings),
        )

    def test_the_boundarys_one_home_states_the_whole_boundary(self) -> None:
        """The owner carries the whole boundary; the sweep also checks every referring surface."""

        self._assert_owed("skills/l-01-agent-lifecycles/core/acceptance.md")

    def test_every_declared_surface_states_its_owed_clause(self) -> None:
        for relative in sorted(OWED_STATEMENTS):
            with self.subTest(surface=relative):
                self._assert_owed(relative)

    def _assert_owed(self, relative: str) -> None:
        """The surface must state its owed clauses AND not deny the boundary anywhere.

        Presence alone is not a pass: a surface that states the boundary and contradicts it in the
        next sentence holds no reading an agent can rely on, and it is exactly the disagreement R13
        exists to prevent.
        """

        raw = read_surface(relative)
        if relative.startswith("skills/l-01-agent-lifecycles/templates/"):
            target = (REPOSITORY_ROOT / relative).parent / "../core/acceptance.md"
            self.assertEqual(target.resolve(), (TREE_LITERAL / "core/acceptance.md").resolve())
            self.assertTrue(target.is_file())
            self._assert_owed("skills/l-01-agent-lifecycles/core/acceptance.md")
        text = normalize(raw)
        missing = [
            statement
            for statement in OWED_STATEMENTS[relative]
            if normalize(statement) not in text.casefold()
        ]
        self.assertEqual(
            missing,
            [],
            f"{relative} no longer states its side of the handoff authority boundary: {missing}",
        )
        self.assertEqual(
            [finding.render(relative) for finding in contradictions(raw)],
            [],
            f"{relative} states its owed boundary clauses and contradicts them elsewhere",
        )

    def test_the_declared_roster_equals_the_surfaces_that_speak_the_vocabulary(self) -> None:
        observed = tuple(
            path.relative_to(REPOSITORY_ROOT).as_posix()
            for path in canonical_surfaces()
            if COMPLETION_TRUTH_VOCABULARY.search(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(
            sorted(observed),
            sorted(COMPLETION_TRUTH_ROSTER),
            "every canonical surface that composes completion from terminal/finalizer truth must "
            "be on the roster (and the roster must not outlive the vocabulary): declare a new "
            "participant in COMPLETION_TRUTH_ROSTER and make it state the mechanical reading, or "
            "remove the row when the surface stops speaking the vocabulary",
        )

    def test_every_roster_surface_carries_the_mechanical_reading(self) -> None:
        for relative in sorted(COMPLETION_TRUTH_ROSTER):
            with self.subTest(surface=relative):
                self.assertEqual(
                    classify_reading(read_surface(relative)),
                    Reading.STATED,
                    f"{relative} speaks the completion-truth vocabulary without stating that the "
                    "terminal outcome only ends the turn",
                )

    def test_the_relay_reference_surfaces_point_to_the_owner(self) -> None:
        for relative in sorted(RELAY_REFERENCE_SURFACES):
            with self.subTest(surface=relative):
                self._assert_owed(relative)

    def test_a_worker_hands_its_frozen_report_directly_to_the_reviewer(self) -> None:
        worker_source = read_surface("skills/l-01-agent-lifecycles/roles/worker.md")
        operation_source = read_surface("skills/l-01-agent-lifecycles/operations/implementation.md")
        every_role_source = read_surface("skills/l-01-agent-lifecycles/core/every-role.md")
        authority_source = read_surface("skills/l-01-agent-lifecycles/core/authority.md")
        sync_repairs_source = read_surface(
            "skills/l-01-agent-lifecycles/reference/worker/sync-and-repairs.md"
        )
        # Stage seat creation goes through the Manager while the Worker sends
        # the complete frozen object directly to its Reviewer and Curator.
        routes = (
            (
                worker_source,
                "# Worker",
                (
                    "<!-- ar:gives freeze-handoff to reviewer: "
                    "freeze-report, candidate-identity, attempt-records -->",
                    "<!-- ar:gives producer-list to curator: "
                    "list-file, freeze-identity, handoff-check-result -->",
                    "No role starts, Reviewer/Curator acts, memory/status edits, closeout, "
                    "commit/merge/push/publish or requirement/packet rewrites.",
                ),
            ),
            (
                worker_source,
                "## Reference index",
                (
                    "[sync]({{detail:reference/worker/sync-and-repairs.md}})",
                    "[accept](../core/acceptance.md)",
                ),
            ),
            (
                operation_source,
                "# Operation — Implementation",
                (
                    "1. Freeze; tell Manager to start Reviewer.",
                    "2. Send Reviewer report path (no pasted text), head/hash + next stretch "
                    "via `role_message`.",
                    "4. After code PASS + Curator start, send Curator list + `handoff-check` "
                    "directly.",
                    "Manager notices: first-freeze Reviewer start, authority, seat "
                    "starts/replacements, beyond-limit rounds, disputes, reserved sync steps",
                ),
            ),
            (
                every_role_source,
                "## Parallel work and waiting",
                ("open supplied artifacts first.",),
            ),
            (
                every_role_source,
                "## Evidence and handovers",
                (
                    "Completion != acceptance: write artifact, name object/file, message "
                    "recipient (own chat if parentless); no model completion row.",
                ),
            ),
            (
                authority_source,
                "## Hand over work and wait for messages",
                (
                    "Count a handover as taken only when the recipient's reply, report or intake "
                    "names the exact object and shows the requested work or its start.",
                    "Open every supplied recipient artifact before you call one absent.",
                ),
            ),
            (
                authority_source,
                "## Bind the seat to its document",
                ("A role started from the dashboard or by a chat without a role has no parent.",),
            ),
            (
                sync_repairs_source,
                "## Report a refused handover",
                (
                    "When the Manager gives the occasion, send the handover yourself; "
                    "the Manager relays none of the contents.",
                ),
            ),
        )

        def require_route(source: str, heading: str, clauses: tuple[str, ...]) -> None:
            level = len(heading) - len(heading.lstrip("#"))
            boundary = r"(?=^#{1," + str(level) + r"} |\Z)"
            match = re.search(r"(?ms)^" + re.escape(heading) + r"\n(.*?)" + boundary, source)
            self.assertIsNotNone(match, heading)
            assert match is not None
            reading = normalize(match[1])
            for clause in clauses:
                self.assertIn(normalize(clause), reading)

        for source, heading, clauses in routes:
            require_route(source, heading, clauses)
            for clause in clauses:
                with self.subTest(heading=heading, route_clause=clause):
                    mutant = source.replace(clause, "")
                    self.assertNotEqual(mutant, source)
                    with self.assertRaises(AssertionError):
                        require_route(mutant, heading, clauses)

    def test_delivered_coordinator_capsules_check_candidate_evidence_before_acceptance(
        self,
    ) -> None:
        manifest = json.loads((TREE_LITERAL / "composition-manifest.json").read_text())
        cases = (
            (
                "architect",
                "review",
                (
                    "baseline covers full diff",
                    "tests != acceptance",
                    "never substituting for leaf build, review, curation, or gate",
                ),
            ),
            (
                "manager",
                "coordination",
                (
                    "inspect full diff",
                    "verdicts, and listed final checks before gates",
                    "never substitute shell commits/turns for acceptance",
                ),
            ),
            (
                "orchestrator",
                "coordination",
                (
                    "verified manager delivery",
                    "inspect diffs without taking over",
                    "paseo state does not record agents remember (ar) acceptance",
                    "keep reports, findings, review, curation, and publication separate",
                    "keep ar-paired enclosure (no paseo worktree/global memory)",
                ),
            ),
        )

        for role, operation, markers in cases:
            with self.subTest(role=role, operation=operation):
                role_entry = manifest["roles"][role]
                operation_entry = manifest["operations"][operation]
                self.assertIn(operation, role_entry["operations"])
                role_text = (TREE_LITERAL / role_entry["file"]).read_text(encoding="utf-8")
                operation_text = (TREE_LITERAL / operation_entry["source"]).read_text(
                    encoding="utf-8"
                )
                delivered = normalize(role_text + "\n" + operation_text)
                self.assertEqual(
                    [marker for marker in markers if normalize(marker) not in delivered],
                    [],
                    f"{role}+{operation} no longer checks its candidate/evidence boundary",
                )

                for marker in markers:
                    with self.subTest(removed_marker=marker):
                        mutant = delivered.replace(normalize(marker), " ")
                        self.assertNotEqual(mutant, delivered)
                        self.assertNotIn(normalize(marker), mutant)

                if role == "manager":
                    weakened = role_text.replace(
                        "Inspect full diff, verdicts, and listed final checks before gates",
                        "Accept each deliverable onward",
                        1,
                    )
                    self.assertNotEqual(weakened, role_text, "the mutation site must be present")
                    weakened_delivery = normalize(weakened + "\n" + operation_text)
                    self.assertIn(
                        "inspect full diff",
                        [
                            marker
                            for marker in markers
                            if normalize(marker) not in weakened_delivery
                        ],
                        "removing candidate inspection must make the guard fail",
                    )


class ConvergenceTeethTests(unittest.TestCase):
    """Current source must read clean while each paired contradiction remains detectable.

    The shipped side is read from the corpus; only the mutant is synthetic. Updating
    required wording or Markdown segmentation must not hollow out the detector.
    """

    #: site -> (corpus surface, synthetic contradicting variant, claim the variant must trip)
    RECONCILED_SITE_MUTANTS: tuple[tuple[str, str, str, str], ...] = (
        (
            "core/acceptance.md table attribution",
            "skills/l-01-agent-lifecycles/core/acceptance.md",
            "| LOCR-1 | x | The completed outcome accepts the handoff and validates the artifact |",
            "terminal-outcome-semantic-acceptance",
        ),
        (
            "roles/architect.md relay sentence",
            "skills/l-01-agent-lifecycles/roles/architect.md",
            "The relay evaluates the artifact and nudges the seat when a report is missing.",
            "relay-evaluates-artifact",
        ),
        (
            "roles/orchestrator.md sweep sentence",
            "skills/l-01-agent-lifecycles/roles/orchestrator.md",
            "The sweep inspects the durable report before the owner is woken.",
            "relay-evaluates-artifact",
        ),
        (
            "roles/reviewer.md negated instruction",
            "skills/l-01-agent-lifecycles/roles/reviewer.md",
            "**Never:** implement or edit code, run an unrequested full suite, or author a second "
            "completion row.",
            "second-completion-message",
        ),
    )

    def test_each_reconciled_site_reads_clean_and_its_mutant_still_fails(self) -> None:
        for site, relative, mutant, claim_id in self.RECONCILED_SITE_MUTANTS:
            with self.subTest(site=site):
                self.assertEqual(
                    [
                        finding.render(relative)
                        for finding in contradictions(read_surface(relative))
                    ],
                    [],
                    f"{site}: the surface stopped reading clean; the reconciliation went the wrong way",
                )
                self.assertIn(
                    claim_id,
                    [finding.claim_id for finding in contradictions(mutant)],
                    f"{site}: the detector no longer catches the contradiction it exists for",
                )

    def test_a_contradicting_sentence_added_to_a_roster_surface_still_fails_it(self) -> None:
        """The per-surface check is state-and-deny aware, and this is the packet's own named failure."""

        relative = "skills/l-01-agent-lifecycles/core/acceptance.md"
        shipped = read_surface(relative)
        self.assertEqual(contradictions(shipped), ())
        self.assertEqual(classify_reading(shipped), Reading.STATED)
        mutated = (
            shipped
            + "\nThe manager may treat a `completed` outcome as acceptance of the handoff.\n"
        )
        self.assertEqual(classify_reading(mutated), Reading.CONTRADICTED)

    def test_removing_an_owed_clause_from_the_shipped_text_still_fails_its_surface(self) -> None:
        # Delete each owed statement on its authoritative source or explicit referral.
        for relative, clauses in OWED_STATEMENTS.items():
            shipped = normalize(read_surface(relative))
            for clause in clauses:
                with self.subTest(surface=relative, clause=clause):
                    marker = normalize(clause)
                    self.assertIn(marker, shipped, f"{relative} no longer states its owed clause")
                    mutated = shipped.replace(marker, " ")
                    self.assertIn(
                        clause,
                        [owed for owed in clauses if normalize(owed) not in mutated],
                        "a surface that lost its owed clause must be reported as missing it",
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
