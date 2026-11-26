"""Inline rules, shared short rules, and grouped detail owners preserve their clauses."""

from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.shared_block import filter_shared_block
from agents_remember.cli.role_brief_message import shared_block
from agents_remember_test_support.testing.capsule_audience_fixtures import (
    REMOVED_SHORT_RULE_ACTIONS,
)
from agents_remember_test_support.testing.digest_map import load_digest_map
from agents_remember_test_support.testing.instruction_renewal import RENEW_COMMAND
from test_l84_capsule_delivery_proofs import (
    _assert_index_detail_pointer,
    _compiled,
    _compiled_outcome,
    _emitted_instruction_detail,
    _shipped_parsed,
)


def _assert_inline_rule_section(body: str, heading: str, clauses: tuple[str, ...]) -> None:
    assert body.count(heading + "\n") == 1, heading
    section = body.split(heading + "\n", 1)[1]
    level = len(heading.split(maxsplit=1)[0])
    section = re.split(r"\n#{1," + str(level) + r"} ", section, maxsplit=1)[0]
    for clause in clauses:
        assert section.count(clause) == 1, clause


# Every previously retired rule mapped to its current owning surface (role, operation,
# core authoring source, every-role digest, or delivered detail for its role).
# (retired_source, owner_path, target_role_or_none, heading, clauses)
CURRENT_INLINE_RULE_CLAUSES: tuple[tuple[str, str, str | None, str, tuple[str, ...]], ...] = (
    (
        "reference/coord-topic-architect-bound-brief.md",
        "reference/coordination/architect/process-decisions.md",
        "architect",
        "## Keep briefs, masters, and side problems bounded",
        (
            "Keep every code or feature brief bounded to what was asked: a sighting next to the task "
            "does not widen it (the Curator's encountered-memory healing retains its separate rule).",
        ),
    ),
    (
        "reference/coord-topic-architect-bound-side-problem.md",
        "reference/coordination/architect/process-decisions.md",
        "architect",
        "## Keep briefs, masters, and side problems bounded",
        (
            "Give a hard, open-ended side problem a separate bounded step instead of folding it into "
            "another round.",
        ),
    ),
    (
        "reference/coord-topic-architect-compute-cost.md",
        "reference/coordination/architect/process-decisions.md",
        "architect",
        "## Weigh compute cost, review shortfalls, and document harness setup",
        (
            "Weigh context, machine, and paid-compute cost and choose the cheap shape; escalate gross "
            "waste once with a measurement and a proposed fix.",
        ),
    ),
    (
        "reference/coord-topic-architect-developer-check-failure.md",
        "roles/architect.md",
        "architect",
        "## Establish the work",
        ("Never bypass checks or freeze animations; gate repairs never free tasks under test.",),
    ),
    (
        "reference/coord-topic-architect-instruction-review.md",
        "reference/coordination/architect/process-decisions.md",
        "architect",
        "## Weigh compute cost, review shortfalls, and document harness setup",
        (
            "Offer the developer an instruction-shortfall review about every two weeks and at each "
            "master's end, and count a fix as made only when its words reach compiled first messages.",
        ),
    ),
    (
        "reference/coord-topic-architect-master-scope.md",
        "reference/coordination/architect/process-decisions.md",
        "architect",
        "## Keep briefs, masters, and side problems bounded",
        (
            "Finish the master's approved subject; do not park admitted work for a follow-up master "
            "or trim its promise to converge.",
        ),
    ),
    (
        "reference/coord-topic-architect-master-status-read.md",
        "reference/coordination/shared/master-state.md",
        "architect",
        "## Update the master after starts, handovers and landings",
        (
            "At each status read, compare the master page with the Manager's last handover, renew "
            "lagging parts and tell the Manager.",
            "Identify stalls from task progress: fifteen minutes without an active turn, or a ready "
            "task left unstarted.",
        ),
    ),
    (
        "reference/coord-topic-architect-page-proof.md",
        "roles/architect.md",
        "architect",
        "## Establish the work",
        ("Never bypass checks or freeze animations",),
    ),
    (
        "reference/coord-topic-architect-timing-result.md",
        "roles/architect.md",
        "architect",
        "## Delegate and decide",
        ("never gate landing on timing",),
    ),
    (
        "reference/coord-topic-manager-count-and-route-failures.md",
        "roles/manager.md",
        "manager",
        "## Start leaf agents and preserve their work",
        (
            "Group failures (`FAILED`/`SUBFAILED`/`ERROR`) by node; collection/import failure is a "
            "landed-leaf regression (route repair at once).",
        ),
    ),
    (
        "reference/coord-topic-shared-compare-builds-before-diagnosing-a-tool-mismatch.md",
        "core/invariants.md",
        None,
        "## Keep continuity in durable state",
        (
            "Before reporting mismatched behavior as a defect, compare the serving tool build with the "
            "code.",
        ),
    ),
    (
        "reference/coord-topic-shared-count-evidence-from-the-producing-call.md",
        "core/invariants.md",
        None,
        "## Keep continuity in durable state",
        (
            "For each recorded time, read the clock in the same step. Append a correction instead of "
            "rewriting a wrong time.",
            "Read the producing call before counting.",
            "claim absence only from an unfiltered list.",
        ),
    ),
    (
        "reference/coord-topic-shared-identify-the-setup-behind-a-graphics-measurement.md",
        "core/invariants.md",
        None,
        "## Keep continuity in durable state",
        (
            "Name the graphics setup behind a browser measurement. Do not claim it represents the "
            "developer's browser without evidence for that claim.",
        ),
    ),
    (
        "reference/coord-topic-shared-remove-scratch-paths-safely.md",
        "core/every-role.md",
        None,
        "## Keep your role and authority",
        ("Never force-remove via shell variable.",),
    ),
    (
        "reference/coord-topic-shared-stop-a-process-or-operate-on-the-live-host.md",
        "core/every-role.md",
        None,
        "## Keep your role and authority",
        (
            "Signal processes by checked PID only; dashboard rebuilds, host restarts, and seat "
            "reloads need developer or Architect assignment.",
        ),
    ),
    (
        "reference/coord-topic-worker-map-evidence-to-report-files.md",
        "reference/worker/sync-and-repairs.md",
        "worker",
        "## Focused test selection and freeze report details",
        (
            "Map numbered evidence items to report files at intake, freeze and final checks, "
            "and name any gaps.",
        ),
    ),
    (
        "reference/coord-topic-worker-regenerate-producer-lists-for-each-freeze.md",
        "operations/implementation.md",
        "worker",
        "# Operation \u2014 Implementation",
        (
            "Regenerate Curator list per freeze + `handoff-check` (`server_info`).",
            "prove collection on removals/renames",
        ),
    ),
    (
        "reference/curator-baseline-preservation.md",
        "operations/curation.md",
        "curator",
        "## Workflow",
        ("read scoped `knowledge_read` + full `knowledge_diff` before semantic changes.",),
    ),
    (
        "reference/curator-intake-details.md",
        "roles/curator.md",
        "curator",
        "# Curator",
        (
            "Open briefs (`read_ar_files`/`context_packet`: `task_context`; `knowledge_read`: `workspace.memoryRoot`).",
            "Curate uncommitted freeze (`knowledge_write` `commit: true` writes files, not Git).",
        ),
    ),
    (
        "reference/review-card-dates.md",
        "roles/reviewer.md",
        "reviewer",
        "## Bind the seam and catalogs",
        ("check card dates vs commits",),
    ),
    (
        "reference/review-knowledge-changes.md",
        "roles/reviewer.md",
        "reviewer",
        "## Bind the seam and catalogs",
        ("keep Curator producer fields",),
    ),
    (
        "reference/review-master-exit.md",
        "reference/review/non-leaf-seams-and-catalogs.md",
        "reviewer",
        "## Master, atomic-master, plan, and portfolio exit reviews",
        (
            "Account for every master requirement, leaf, substep and accepted small gap filled during the "
            "work.",
            "Completion against task documents is an affirmative duty, not a formatting check. Skipped or "
            "reshaped work needs a decision-log trail; a packet that reads as complete while silently "
            "omitting unfinished leaf work, a substep or an accepted gap-fill carries a blocking finding.",
        ),
    ),
    (
        "reference/review-memory-changes.md",
        "reference/review/rounds-and-late-findings.md",
        "reviewer",
        "## Omission repairs, whole-set findings, and memory changes",
        (
            "During memory review, inspect the quality result's reports of cards with substantial text "
            "removed (`cutCards`) and files added outside the knowledge (`addedFiles`), reporting a "
            "finding when text was removed without a named change or when a file was added outside "
            "the knowledge without a place in the task's requirement.",
        ),
    ),
    (
        "reference/review-omission-repairs.md",
        "reference/review/rounds-and-late-findings.md",
        "reviewer",
        "## Omission repairs, whole-set findings, and memory changes",
        (
            "Close an omission finding by listing and checking every behavior introduced by the diff.",
        ),
    ),
    (
        "reference/review-requirement-adjudication.md",
        "roles/reviewer.md",
        "reviewer",
        "## Bind the seam and catalogs",
        ("check `expectedKnowledgeEffects`",),
    ),
    (
        "reference/review-whole-set-findings.md",
        "reference/review/rounds-and-late-findings.md",
        "reviewer",
        "## Omission repairs, whole-set findings, and memory changes",
        (
            "For a finding about every member of a set, script a scan of the whole candidate and "
            "rerun it during verification.",
        ),
    ),
    (
        "reference/coord-topic-architect-harness-setup-documentation.md",
        "reference/coordination/architect/process-decisions.md",
        "architect",
        "## Weigh compute cost, review shortfalls, and document harness setup",
        (
            "Record any required harness setting or workaround at once in the master's harness list "
            "(symptom, cause, setting, and who makes it) for public documentation.",
        ),
    ),
    (
        "reference/curator-external-evidence.md",
        "reference/curator/exceptions-and-recovery.md",
        "curator",
        "## Quality-check card cuts and external citations",
        (
            "Cite an external document supporting a card as an `external` sidecar reference, never as a "
            "repository path with a Git blob. Name the document in the handover evidence for the record's "
            "`origin`, so each backed record identifies its supporting document.",
        ),
    ),
)


def _inline_rule_owner_body(
    root: Path,
    owner: str,
    shared_block_text: str | None,
    surfaces: tuple[dict[str, str], dict[str, str]],
) -> str | None:
    units_by_source, details_by_source = surfaces
    if owner == "core/every-role.md":
        assert shared_block_text is not None
        return shared_block_text
    if owner.startswith("core/"):
        return (root / owner).read_text(encoding="utf-8")
    if owner in units_by_source:
        return units_by_source[owner]
    if owner.startswith("operations/"):
        return None
    assert owner in details_by_source, owner
    return details_by_source[owner]


def _assert_inline_rule_with_mutations(body: str, heading: str, clauses: tuple[str, ...]) -> None:
    _assert_inline_rule_section(body, heading, clauses)
    for clause in clauses:
        for mutant in (body.replace(clause, "", 1), body.replace(clause, clause * 2, 1)):
            assert mutant != body
            with pytest.raises(AssertionError):
                _assert_inline_rule_section(mutant, heading, clauses)


def test_v13_inline_rules_reach_their_actual_owning_sections_and_reject_deletion() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    manifest = _shipped_parsed()
    checked = set()
    seen = set()
    for role, metadata in manifest.roles.items():
        for operation in metadata.operations:
            outcome = _compiled_outcome(role, operation)
            assert outcome.result is not None
            result = outcome.result
            units_by_source = {
                unit.block.source_path: unit.block.content
                for unit in result.capsule.instruction_units
            }
            details_by_source = {
                d.source_path: d.content.decode() for d in result.capsule.detail_references
            }
            for retired, owner, target_role, heading, clauses in CURRENT_INLINE_RULE_CLAUSES:
                assert retired not in details_by_source
                assert "{{detail:" + retired + "}}" not in result.render_instructions()
                if target_role is not None and role != target_role:
                    continue
                body = _inline_rule_owner_body(
                    root,
                    owner,
                    outcome.shared_block,
                    (units_by_source, details_by_source),
                )
                if body is None:
                    continue
                _assert_inline_rule_with_mutations(body, heading, clauses)
                seen.add(retired)
            checked.add((role, operation))
    assert checked == {
        (role, operation)
        for operation, entry in manifest.operations.items()
        for role in entry.applies_to_roles
    }
    assert seen == {retired for retired, *_ in CURRENT_INLINE_RULE_CLAUSES}


# Approved short rules: canonical core home, disposition, digest clause (when digested),
# audience roles, and canonical clauses verified against the core source.
CURRENT_SHORT_RULE_CLAUSES = (
    (
        "AU11.6",
        "core/authority.md",
        "digest",
        "Architect, Reviewer: Ceilings are `WIRE_TOKEN_CEILINGS` and "
        "`COMPILED_CAPSULE_TOKEN_CEILINGS` in "
        "`mcp/src/agents_remember/application/role_capsules/wire_budget.py`; "
        "only developer sets/raises them.",
        ("architect", "reviewer"),
        (
            "A role's Turn-1 wire ceiling is its entry in `WIRE_TOKEN_CEILINGS`, and a compiled "
            "capsule's ceiling is its entry in `COMPILED_CAPSULE_TOKEN_CEILINGS`, both in "
            "`mcp/src/agents_remember/application/role_capsules/wire_budget.py`; read the complete "
            "entry before you state it. Only the developer sets or raises a ceiling.",
        ),
    ),
    (
        "AC0.3",
        "core/acceptance.md",
        "reference",
        None,
        ("architect", "orchestrator", "manager"),
        (
            "Take closeout trees by filename from the highest-numbered memory PASS and landing-check "
            "verdict, never from a message.",
        ),
    ),
    (
        "AU11.4",
        "core/authority.md",
        "digest",
        "Pushing, PRs, and releases need developer delegation; never force a refused push; "
        "`--no-verify` needs developer's word.",
        ("architect", "orchestrator", "manager"),
        (
            "Never force a refused push. Using `--no-verify` requires the developer's word for that "
            "occasion. Send the failure through your parent to the developer.",
        ),
    ),
    (
        "AU10.4",
        "core/authority.md",
        "digest",
        "`held` = queued; never resend, withdraw via `withdraw_held_message_id`, "
        "open supplied artifacts first.",
        (
            "architect",
            "orchestrator",
            "manager",
            "worker",
            "curator",
            "reviewer",
            "investigator",
            "bootstrap",
        ),
        (
            "A held receipt means queued eventual delivery, possibly many turns later. Never resend it. "
            "Count a handover as taken only when the recipient's reply, report or intake names the exact "
            "object and shows the requested work or its start. Open every supplied recipient artifact "
            "before you call one absent. Withdraw a superseded held message with "
            "`withdraw_held_message_id`. A receiver does not poll.",
        ),
    ),
    (
        "AC0.5",
        "core/acceptance.md",
        "digest",
        "Completion != acceptance: write artifact, name object/file, message recipient "
        "(own chat if parentless); no model completion row.",
        (
            "architect",
            "orchestrator",
            "manager",
            "worker",
            "curator",
            "reviewer",
            "investigator",
            "bootstrap",
        ),
        (
            "Name the finished object and its evidence file. A finished turn or a filed report alone does "
            "not notify it.",
        ),
    ),
    (
        "AU10.3",
        "core/authority.md",
        "digest",
        "End waits with `WAITING FOR SUBAGENTS:` or `WAITING FOR BACKGROUND:` "
        "(process, id/log, expected end), sending no parent progress `role_message`.",
        (
            "architect",
            "orchestrator",
            "manager",
            "worker",
            "curator",
            "reviewer",
            "investigator",
            "bootstrap",
        ),
        (
            "If your harness offers helpers (sub-agents), they run on your own seat's model unless the "
            "developer names another.",
        ),
    ),
)


def _assert_short_shared_rule(body: str, line: str) -> None:
    assert body.count(line) == 1


def test_short_rules_use_shared_delivery_and_reject_removed_step_reads() -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    shared = (root / "core/every-role.md").read_text()
    units = {item["id"]: item for item in load_digest_map(root / "core/digest-map.json")["units"]}
    checked = set()
    for role, metadata in _shipped_parsed().roles.items():
        for operation in metadata.operations:
            outcome = _compiled_outcome(role, operation)
            assert outcome.result is not None
            assert outcome.shared_block is not None
            expected_shared = filter_shared_block(shared, role)
            assert outcome.shared_block == expected_shared
            assert shared_block(outcome.shared_block).count(expected_shared) == 1
            source = next(
                record
                for record in outcome.result.manifest.sources
                if record.path == "core/every-role.md"
            )
            assert source.revision == "sha256:" + sha256(shared.encode()).hexdigest()
            for (
                unit_id,
                owner,
                disposition,
                digest_clause,
                roles,
                clauses,
            ) in CURRENT_SHORT_RULE_CLAUSES:
                unit = units[unit_id]
                assert unit["file"] == owner
                assert set(roles) <= set(unit["audience"])
                assert unit["disposition"] == disposition
                start, end = unit["lines"]
                core = "\n".join((root / owner).read_text().splitlines()[start - 1 : end])
                assert sha256(core.encode()).hexdigest() == unit["sha256"], f"Run {RENEW_COMMAND}"
                for clause in clauses:
                    assert clause in core
                if disposition == "digest":
                    assert unit["digestPlace"]
                    assert digest_clause is not None
                    if role in roles:
                        _assert_short_shared_rule(outcome.shared_block, digest_clause)
                        for mutant in (
                            outcome.shared_block.replace(digest_clause, "", 1),
                            outcome.shared_block + "\n" + digest_clause,
                        ):
                            with pytest.raises(AssertionError):
                                _assert_short_shared_rule(mutant, digest_clause)
                    else:
                        assert digest_clause not in outcome.shared_block
            rendered = outcome.result.render_instructions()
            for actor, _source, action in REMOVED_SHORT_RULE_ACTIONS:
                if actor == role:
                    assert action not in rendered
            assert "#handle-held-delivery" not in rendered
            assert "#notify-the-seat-waiting-on-a-completed-step" not in rendered
            checked.add((role, operation))
    assert checked == {
        (role, operation)
        for operation, entry in _shipped_parsed().operations.items()
        for role in entry.applies_to_roles
    }


CURRENT_GROUPED_DETAIL_CLAUSES = (
    (
        "architect",
        "reference/coordination/shared/requirement-design.md",
        "Reqs/`task_doc`/`context_packet`/`knowledge_read`",
        "design",
        (
            "Give a clickable control one named location and state where it does not appear; avoid "
            "“wherever X is shown”.",
            "The dashboard is the developer's tool: before deferring, gating, moving or removing anything "
            "it depends on, check that he can open and use it with no agent active, or bring the conflict "
            "to him.",
        ),
    ),
    (
        "reviewer",
        "reference/review-baseline.md",
        "Baseline/knowledge",
        "baseline",
        (
            "Organise the route work yourself, using sub-agents if your harness offers them. No "
            "particular harness arrangement is required.",
            "Run the bound catalogs, required routes and exploratory mandate. Cover all three lenses "
            "explicitly, even to say a lens is clean: completion against task documents, scoped "
            "implementation evidence, and onboarding against code. Try to refute each claim before "
            "confirming it.",
            "Match evidence to the requirement's class:",
            "- Rendering or visibility needs proof from the mounted user interface.\n"
            "- Scheduling or ordering needs operation-level proof.\n"
            "- A persisted shape needs the parsed artifact.\n"
            "- Doctrine needs the file and the mechanism enforcing the rule.",
            "Evidence of the wrong class is a finding and cannot support PASS.",
            "A weakness record is never a finding, and a document's staleness is never a finding. A "
            "weakness found outside the task's scope and a document standing on code the task changed are "
            "recorded knowledge. They refuse no review, gate or closeout.",
            "An unrepaired weakness inside the task's scope is a finding. So is a document the task "
            "changed whose dependent record is not confirmed. A weakness record never substitutes for "
            "such a finding.",
            "An empty first-pass issue list is a valid terminating baseline.",
            "Select focused tests by",
            "which also names the fix-verification case.",
        ),
    ),
    (
        "worker",
        "reference/sandbox-actors.md",
        "Sandbox/ask",
        "sandbox",
        (
            "Do not write user-level harness configuration. Bind each sandbox actor per run, or stop and "
            "ask.",
            "Leave a user-level harness configuration file that an agent created unchanged and tell your "
            "parent, who routes one notice to the developer.",
            "Test actors need no extra permission under Agents Remember's rules; a harness's own approval "
            "review may still ask the developer. Use the cheapest model of the harness the run is set up "
            "for, and name it in the command and the record. Ordinary sandbox isolation applies.",
            "The product never edits a harness file the user owns. Ship a setting Agents Remember needs "
            "through its own launch settings, or document it on that harness's page.",
            "Keep credentials out of a sandbox-local harness home. Run model turns through the sandbox "
            "product's launch home.",
            "Before sandbox actors start, prove their tool-server binding with a stub. Count the host's "
            "agents before and after the first `role_start` on `agents-remember-task`.",
        ),
    ),
)


@pytest.mark.parametrize("case", CURRENT_GROUPED_DETAIL_CLAUSES)
def test_grouped_action_reads_the_whole_delivered_owner(
    case: tuple[str, str, str, str, tuple[str, ...]], tmp_path: Path
) -> None:
    role, source, trigger, label, clauses = case
    for operation in _shipped_parsed().roles[role].operations:
        result = _compiled(role, operation)
        rendered = result.render_instructions()
        _assert_index_detail_pointer(rendered, source, trigger, label, "")
        marker = "{{detail:" + source + "}}"
        row = next(
            line
            for line in rendered.splitlines()
            if line.lstrip().startswith("|")
            and line.strip().strip("|").split("|", 1)[0].strip() == trigger
        )
        assert row.count(f"[{label}]({marker})") == 1
        assert marker + "#" not in row
        for mutant in (
            rendered.replace(trigger, "", 1),
            rendered.replace(marker + ")", marker + "#wrong-section)", 1),
        ):
            assert mutant != rendered
            with pytest.raises(AssertionError):
                _assert_index_detail_pointer(mutant, source, trigger, label, "")
        _instructions, delivered = _emitted_instruction_detail(
            result, source, tmp_path / f"{role}-{operation}.md"
        )
        for clause in clauses:
            assert clause in delivered
            with pytest.raises(AssertionError):
                assert clause in delivered.replace(clause, "", 1)
