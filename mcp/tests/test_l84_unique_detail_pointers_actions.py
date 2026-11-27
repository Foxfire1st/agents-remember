"""Relocated pointers, named action reads, and retirement stop boundaries stay reachable."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.shared_block import filter_shared_block
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli.role_brief_message import shared_block
from agents_remember.models.role_capsules.types import CapsuleCompilationResult
from agents_remember_test_support.testing.digest_map import load_digest_map
from test_l84_capsule_delivery_proofs import (
    _assert_index_detail_pointer,
    _compiled,
    _compiled_outcome,
    _emitted_instruction_detail,
    _linked_instruction_source,
    _shipped_parsed,
)
from test_l84_unique_detail_pointers import _assert_moved_action_pointer

MOVED_INDEX_POINTERS: dict[str, tuple[str, tuple[tuple[str, str, str], ...]]] = {
    "curator": (
        "reference/knowledge/use.md",
        (("Knowledge/CLI", "use", ""),),
    ),
    "manager": (
        "reference/tasks/document-authoring.md",
        (
            (
                "Master (`task_doc`/`worktree_abandon`/`record_landing`), retire, cmds, msgs, sandbox, child",
                "authoring",
                "",
            ),
        ),
    ),
    "orchestrator": (
        "reference/tasks/document-authoring.md",
        (
            (
                "Manager brief, stages (`role_start`), hand-off, seats (`retire_child`), master/decisions (`task_doc`), closeout",
                "decisions",
                "#preserve-decisions-and-approved-intent",
            ),
        ),
    ),
    "worker": (
        "reference/tasks/document-authoring.md",
        (("Task/report/list", "task", ""),),
    ),
}
SETTINGS_AUTHORITY_POINTER = (
    "Before relying on storage or path eligibility, cross-repository allowances, or "
    "serving-build metadata, read [settings authority]"
    "(../reference/knowledge/use.md#read-the-source-that-owns-a-setting) for `server_info` "
    "and the other sources that govern those facts."
)


def test_moved_piece_pointers_reach_each_required_compile_and_reject_removal() -> None:
    manifest = _shipped_parsed()
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    shared = (root / "core/every-role.md").read_text()
    invariants = (root / "core/invariants.md").read_text()
    digest_units = {
        item["id"]: item for item in load_digest_map(root / "core/digest-map.json")["units"]
    }
    assert digest_units["IN8.8"]["disposition"] == "reference"
    assert digest_units["IN8.8"]["file"] == "core/invariants.md"
    _assert_moved_action_pointer(invariants, SETTINGS_AUTHORITY_POINTER)
    with pytest.raises(AssertionError):
        _assert_moved_action_pointer(
            invariants.replace(SETTINGS_AUTHORITY_POINTER, "", 1),
            SETTINGS_AUTHORITY_POINTER,
        )
    assert (
        "## Read the source that owns a setting\n"
        in (root / "reference/knowledge/use.md").read_text()
    )
    checked = set()
    for role, metadata in manifest.roles.items():
        for operation in metadata.operations:
            outcome = _compiled_outcome(role, operation)
            assert outcome.result is not None
            result = outcome.result
            assert outcome.shared_block is not None
            expected_shared = filter_shared_block(shared, role)
            assert outcome.shared_block == expected_shared
            assert shared_block(outcome.shared_block).count(expected_shared) == 1
            rendered = result.render_instructions()
            source = next(
                record for record in result.manifest.sources if record.path == "core/every-role.md"
            )
            assert source.revision == "sha256:" + sha256(shared.encode()).hexdigest()
            if role == "bootstrap":
                link = (
                    "[settings](../reference/knowledge/use.md#read-the-source-that-owns-a-setting)"
                )
                _assert_moved_action_pointer(rendered, link)
                with pytest.raises(AssertionError):
                    _assert_moved_action_pointer(rendered.replace(link, "", 1), link)
            if role in MOVED_INDEX_POINTERS:
                target, specs = MOVED_INDEX_POINTERS[role]
                for trigger, label, fragment in specs:
                    _assert_index_detail_pointer(rendered, target, trigger, label, fragment)
                    pointer_line = next(
                        line
                        for line in rendered.splitlines()
                        if line.lstrip().startswith("|")
                        and line.strip().strip("|").split("|", 1)[0].strip() == trigger
                    )
                    if role == "manager":
                        detail = next(
                            item
                            for item in result.capsule.detail_references
                            if item.source_path == "reference/tasks/document-authoring.md"
                        )
                        assert detail.content == (root / detail.source_path).read_bytes()
                        assert "codeExamplesNote" in detail.content.decode()
                    with pytest.raises(AssertionError):
                        _assert_index_detail_pointer(
                            rendered.replace(pointer_line, "", 1), target, trigger, label, fragment
                        )
                    with pytest.raises(AssertionError):
                        _assert_index_detail_pointer(
                            rendered + "\n" + pointer_line, target, trigger, label, fragment
                        )
            checked.add((role, operation))
    assert checked == {
        (role, operation)
        for operation, entry in manifest.operations.items()
        for role in entry.applies_to_roles
    }
    assert {role for role, _ in checked} == {
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "curator",
        "reviewer",
        "consolidator",
        "investigator",
        "bootstrap",
    }


LEAF_EVIDENCE_POINTER = (
    "Before creating a leaf through `task_doc`, read [retained-evidence lifecycle]"
    "(../../core/acceptance.md#record-how-retained-evidence-will-be-maintained) for the "
    "contract or expiry information its handoff must record for each new or retained "
    "fixture, recording, generator, shared support file or migration proof."
)
LEAF_AUTHORING_POINTERS = {
    "architect": ("Reqs/`task_doc`/`context_packet`/`knowledge_read`", "authoring", ""),
}


@pytest.mark.parametrize("role", ["architect"])
def test_leaf_creation_reaches_retained_evidence_lifecycle_and_rejects_removal(role: str) -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    authoring = (root / "reference/tasks/document-authoring.md").read_bytes()
    acceptance = (root / "core/acceptance.md").read_text()
    section = acceptance.split("## Record how retained evidence will be maintained\n", 1)[1]
    for clause in (
        "For each new or retained fixture, recording, generator, shared support file or "
        "migration proof, the handoff records one of these sets:",
        "1. Registered stable contract identity, real owner, executable evidence node "
        "and exact consumers.",
        "2. Expiry date, executable replacement or removal event, owner and compatibility "
        "consequence.",
        "Missing or contradictory catalog metadata requires implementation work.",
        "A satisfied requirement cannot waive missing lifecycle metadata for durable evidence.",
    ):
        assert clause in section
    checked = set()
    for operation in _shipped_parsed().roles[role].operations:
        result = _compiled(role, operation)
        rendered = result.render_instructions()
        trigger, label, fragment = LEAF_AUTHORING_POINTERS[role]
        _assert_index_detail_pointer(
            rendered, "reference/tasks/document-authoring.md", trigger, label, fragment
        )
        pointer = next(
            line
            for line in rendered.splitlines()
            if line.lstrip().startswith("|")
            and line.strip().strip("|").split("|", 1)[0].strip() == trigger
        )
        detail = next(
            item
            for item in result.capsule.detail_references
            if item.source_path == "reference/tasks/document-authoring.md"
        )
        assert detail.content == authoring
        assert detail.revision == "sha256:" + sha256(authoring).hexdigest()
        assert detail.step.strip()
        delivered = detail.content.decode()
        _assert_moved_action_pointer(delivered, LEAF_EVIDENCE_POINTER)
        for mutant in (rendered.replace(pointer, "", 1), rendered + "\n" + pointer):
            assert mutant != rendered
            with pytest.raises(AssertionError):
                _assert_index_detail_pointer(
                    mutant, "reference/tasks/document-authoring.md", trigger, label, fragment
                )
        for text, required in ((delivered, LEAF_EVIDENCE_POINTER),):
            with pytest.raises(AssertionError):
                _assert_moved_action_pointer(text.replace(required, "", 1), required)
            with pytest.raises(AssertionError):
                _assert_moved_action_pointer(text + "\n" + required, required)
        checked.add(operation)
    assert checked == set(_shipped_parsed().roles[role].operations)


ARCHITECT_ACTION_READS = (
    (
        "r29-architect",
        (
            "Reqs/`task_doc`/`context_packet`/`knowledge_read`",
            "diagnose",
            "#architect-diagnose-before-proposing-a-repair",
        ),
        "knowledge/use",
    ),
    ("0:0", ("Take closeout trees by filename", "", ""), "core/acceptance"),
    (
        "20:2",
        ("Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd", "seats", ""),
        "coordination/shared/seat-recovery",
    ),
    (
        "21:0",
        ("Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd", "master", ""),
        "coordination/shared/master-state",
    ),
    (
        "40:3",
        ("Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd", "seats", ""),
        "coordination/shared/seat-recovery",
    ),
    ("10:0", ("If your harness offers helpers (sub-agents)", "", ""), "core/authority"),
    (
        "43:2",
        ("Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd", "models", ""),
        "coordination/architect/models-and-settings",
    ),
    (
        "51:0",
        ("Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd", "models", ""),
        "coordination/architect/models-and-settings",
    ),
    (
        "40:0",
        ("Models/sandbox (`role_start`), master, seats (`retire_child`), 3rd", "master", ""),
        "coordination/shared/master-state",
    ),
)


ARCHITECT_ACTION_CLAUSES = {
    "r29-architect": "When diagnosing a failure before proposing a repair task, use attributed invariants, "
    "families, sibling code and proofs alongside code reads to form and check the cause. Carry "
    "the evidence and remaining gaps into the packet and Prepared context.",
    "0:0": "Take closeout trees by filename from the highest-numbered memory PASS and landing-check verdict, "
    "never from a message.",
    "20:2": "When every agent of a leaf is idle, read each agent's last lines. If a turn ended silently while "
    "helpers or a process it started still run, tell that seat to collect its results and finish. A valid "
    "declared helper or background wait (`WAITING FOR SUBAGENTS:` or `WAITING FOR BACKGROUND:`) remains a "
    "wait until its next undeclared end.",
    "21:0": "In the same turn as each start, role handover and landing, update the master document's parts "
    "changed by that act: its status note, counts, order state and **In work now** section. Include the "
    "task, stage, agents and time since that state began. Leave product-mirrored stage fields with "
    "their existing owner; do not write a contradictory second status.",
    "40:3": "Continue ended child turns with unfinished work and no valid wait/stop. "
    "A Manager's end line naming the working child and awaited stage declares a named wait.",
    "10:0": "If your harness offers helpers (sub-agents), they run on your own seat's model unless the developer names another.",
    "40:0": "At each status read, compare the master page with the Manager's last handover, renew lagging "
    "parts and tell the Manager. Identify stalls from task progress: fifteen minutes without an active "
    "turn, or a ready task left unstarted.",
    "43:2": "When a model the developer names is missing from a list, look it up online and check the harness's "
    "and host's versions before you ask him about another.",
    "51:0": "The developer may name role sets: complete role settings for different situations, for example one "
    "for when a provider is available and a cheaper one for when it is not. The Architect may switch between "
    "the sets he has named on its own, for example when a provider is out, and keeps the earlier "
    "settings bytes. Any other change to his settings needs his word for that case.",
}


def _assert_architect_index_read(text: str, spec: tuple[str, str, str], detail: str) -> None:
    trigger, label, fragment = spec
    _assert_index_detail_pointer(text, "reference/" + detail + ".md", trigger, label, fragment)


@pytest.mark.parametrize(("scenario_id", "spec", "detail"), ARCHITECT_ACTION_READS)
def test_architect_named_action_reads_and_pointer_removal(scenario_id, spec, detail) -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    for operation in _shipped_parsed().roles["architect"].operations:
        result = _compiled("architect", operation)
        text = result.render_instructions()
        if scenario_id in {"0:0", "10:0"}:
            owner_text = (root / f"{detail}.md").read_text(encoding="utf-8")
            clause = ARCHITECT_ACTION_CLAUSES[scenario_id]
            _assert_moved_action_pointer(owner_text, clause)
            for mutant in (
                owner_text.replace(clause, "", 1),
                owner_text + "\n" + clause,
            ):
                with pytest.raises(AssertionError):
                    _assert_moved_action_pointer(mutant, clause)
            if scenario_id == "0:0":
                assert "Before selecting closeout trees, read" not in text
            continue
        if scenario_id == "40:3":
            outcome = _compiled_outcome("architect", operation)
            assert outcome.result is not None
            assert outcome.shared_block is not None
            _assert_architect_index_read(text, spec, detail)
            clause = ARCHITECT_ACTION_CLAUSES[scenario_id]
            _assert_moved_action_pointer(outcome.shared_block, clause)
            for mutant in (
                outcome.shared_block.replace(clause, "", 1),
                outcome.shared_block + "\n" + clause,
            ):
                with pytest.raises(AssertionError):
                    _assert_moved_action_pointer(mutant, clause)
            authority = (root / "core/authority.md").read_text(encoding="utf-8")
            full_clause = (
                "Check report senders\u2019 turns. Send continuations immediately for ended turns "
                "with unfinished authorized work and no valid wait or stop. A Manager\u2019s end "
                "line naming the working child and awaited stage declares a named wait."
            )
            _assert_moved_action_pointer(authority, full_clause)
            continue
        _assert_architect_index_read(text, spec, detail)
        delivered = next(
            item
            for item in result.capsule.detail_references
            if item.source_path == "reference/" + detail + ".md"
        )
        assert delivered.content, scenario_id
        clause = ARCHITECT_ACTION_CLAUSES[scenario_id]
        content = delivered.content.decode()
        assert clause in content
        mutant = content.replace(clause, "", 1)
        assert mutant != content
        with pytest.raises(AssertionError):
            assert clause in mutant
        marker = "{{detail:reference/" + detail + ".md}}"
        trigger = spec[0]
        (action_line,) = (
            line
            for line in text.splitlines()
            if line.lstrip().startswith("|")
            and line.strip().strip("|").split("|", 1)[0].strip() == trigger
        )
        without_target = text.replace(action_line, action_line.replace(marker, "", 1), 1)
        assert without_target != text
        for mutant in (text.replace(trigger, "", 1), without_target):
            with pytest.raises(AssertionError):
                _assert_architect_index_read(mutant, spec, detail)


@pytest.mark.parametrize(
    ("trigger", "heading"),
    [
        (
            "Before a new task starts, write its task document with one major step per role: build, review, curation, knowledge check and landing.",
            "Give each role a major step in new tasks",
        ),
        (
            "An existing task gains a step only when the developer slots new scope into it.",
            "Preserve existing task structure",
        ),
    ],
)
def test_architect_task_layout_reaches_its_existing_section(trigger, heading) -> None:
    source = "reference/coordination/shared/requirement-design.md"
    row_trigger = "Reqs/`task_doc`/`context_packet`/`knowledge_read`"
    for operation in _shipped_parsed().roles["architect"].operations:
        result = _compiled("architect", operation)
        text = result.render_instructions()
        _assert_index_detail_pointer(text, source, row_trigger, "design", "")
        delivered = next(
            item for item in result.capsule.detail_references if item.source_path == source
        )
        content = delivered.content.decode()
        assert "## " + heading + "\n" in content
        assert trigger in content
        for mutant in (
            text.replace(row_trigger, "", 1),
            text.replace("{{detail:" + source + "}}", "", 1),
        ):
            assert mutant != text
            with pytest.raises(AssertionError):
                _assert_index_detail_pointer(mutant, source, row_trigger, "design", "")
        mutant = content.replace(trigger, "", 1)
        assert mutant != content
        with pytest.raises(AssertionError):
            assert trigger in mutant


def _assert_canonical_retirement_boundary(paragraph: str) -> None:
    for clause in (
        "including an admitted task retirement or folder movement",
        "same tool and input class before applying a stop",
        "Read, check, validation and sync refusals, starts, other product guards "
        "and commit gates retain their stops.",
    ):
        assert clause in paragraph


def _assert_digest_retirement_boundary(paragraph: str) -> None:
    for clause in (
        "For correct-input refused covered writes",
        "(same tool and input class) before applying a stop.",
        "Other refusals keep stops.",
    ):
        assert clause in paragraph


def _assert_retirement_permission_stop(result: CapsuleCompilationResult, report_path: Path) -> None:
    """The emitted refusal procedure reaches the complete permission-stop owner."""
    _instructions, detail = _emitted_instruction_detail(
        result, "reference/refused-write.md", report_path
    )
    for clause in (
        "An admitted `task_doc` retirement or folder movement is included.",
        "A missing tool or unadmitted capability is not a refused correct write.",
        "never a Git commit or another transaction step",
    ):
        assert clause in detail
    action = "Before acting on a denied or unanswered harness request, read"
    owner = _linked_instruction_source(
        detail,
        action,
        "operations/orientation.md",
        "honor-harness-permission-decisions",
        "Honor harness permission decisions",
    )
    for mutant in (
        detail.replace(action, "", 1),
        detail + "\n" + next(line for line in detail.splitlines() if action in line),
        detail.replace("#honor-harness-permission-decisions", "#formal-permissions", 1),
    ):
        with pytest.raises(AssertionError):
            _linked_instruction_source(
                mutant,
                action,
                "operations/orientation.md",
                "honor-harness-permission-decisions",
                "Honor harness permission decisions",
            )
    _assert_permission_owner_section(owner)


def _assert_served_retirement_permission_stop(paragraph: str) -> None:
    """Without a capsule detail, the shared skill:// link serves the same complete owner."""
    uri = "skill://agents-remember/skills/l-01-agent-lifecycles/reference/refused-write.md"
    assert paragraph.count("](" + uri + ")") == 1, "shared paragraph must link the served file"
    action = "Before acting on a denied or unanswered harness request, read"
    with shipped_composition_corpus() as (root, _manifest):
        detail = (root / "reference/refused-write.md").read_text()
        owner = (root / "operations/orientation.md").read_text()
    for clause in (
        "An admitted `task_doc` retirement or folder movement is included.",
        "A missing tool or unadmitted capability is not a refused correct write.",
        "never a Git commit or another transaction step",
    ):
        assert clause in detail
    link = (
        action + " [Honor harness permission decisions]"
        "(../operations/orientation.md#honor-harness-permission-decisions)"
    )
    assert detail.count(link) == 1
    _assert_permission_owner_section(owner)


def _assert_permission_owner_section(owner: str) -> None:
    section = owner.split("## Honor harness permission decisions\n", 1)[1].split("\n## ", 1)[0]
    clauses = (
        "Never bypass an open/denied harness permission; wait while open, "
        "else continue without it and report.",
    )
    for clause in clauses:
        assert clause in section
        with pytest.raises(AssertionError):
            assert clause in section.replace(clause, "", 1)
    for facet in (
        "Never bypass",
        "open/denied",
        "wait while open",
        "continue without it",
        "and report",
    ):
        damaged = section.replace(facet, "", 1)
        assert damaged != section
        with pytest.raises(AssertionError):
            assert all(clause in damaged for clause in clauses)


RETIREMENT_DETAIL_ROLES = frozenset({"manager"})


@pytest.mark.parametrize("role", ["architect", "orchestrator", "manager", "worker"])
def test_retirement_shared_source_and_delivered_detail_preserve_stops(role, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"
    authority = (root / "core/authority.md").read_text()
    canonical = next(
        line
        for line in authority.splitlines()
        if "correct-input refusal of a covered write" in line
    )
    _assert_canonical_retirement_boundary(canonical)
    for clause in (
        "including an admitted task retirement or folder movement",
        "same tool and input class before applying a stop",
        "Read, check, validation and sync refusals",
    ):
        with pytest.raises(AssertionError):
            _assert_canonical_retirement_boundary(canonical.replace(clause, "", 1))
    shared = (root / "core/every-role.md").read_text()
    paragraph = next(
        line for line in shared.splitlines() if "For correct-input refused covered writes" in line
    )
    _assert_digest_retirement_boundary(paragraph)
    for clause in (
        "For correct-input refused covered writes",
        "(same tool and input class) before applying a stop.",
        "Other refusals keep stops.",
    ):
        with pytest.raises(AssertionError):
            _assert_digest_retirement_boundary(paragraph.replace(clause, "", 1))
    operations = {
        "architect": "orientation",
        "orchestrator": "coordination",
        "manager": "coordination",
        "worker": "implementation",
    }
    for operation in (operations[role],):
        outcome = _compiled_outcome(role, operation)
        assert outcome.result is not None
        expected_shared = filter_shared_block(shared, role)
        assert outcome.shared_block == expected_shared
        assert shared_block(outcome.shared_block).count(expected_shared) == 1
        result = outcome.result
        assert outcome.shared_block is not None
        _assert_digest_retirement_boundary(
            next(
                line
                for line in outcome.shared_block.splitlines()
                if "For correct-input refused covered writes" in line
            )
        )
        # Only the Manager's retirement pointer delivers the detail; the other roles reach
        # the same owner through the shared paragraph's served link.
        delivered = any(
            item.source_path == "reference/refused-write.md"
            for item in result.capsule.detail_references
        )
        assert delivered == (role in RETIREMENT_DETAIL_ROLES), role
        if delivered:
            _assert_retirement_permission_stop(result, tmp_path / f"{role}-retirement.md")
        else:
            _assert_served_retirement_permission_stop(paragraph)
