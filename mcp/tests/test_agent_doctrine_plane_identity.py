"""Keep control-plane correlations out of executable agent doctrine."""

from __future__ import annotations

from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SKILL = REPOSITORY_ROOT / "skills/l-01-agent-lifecycles"
CANONICAL_SKILL = REPOSITORY_ROOT / ".codex/skills/l-01-agent-lifecycles"
PACKAGED_SKILL = (
    REPOSITORY_ROOT / "mcp/src/agents_remember/package_data/runtime/skills/l-01-agent-lifecycles"
)
FORBIDDEN_AGENT_INSTRUCTIONS = (
    "operator_inbox_post",
    "operator_inbox_poll",
    "operator_inbox_consume",
    "spawn_agent_session",
    "hosted_session_readiness",
    "session_retire",
    "session_rename",
    "attach_terminal_session",
    "gateId",
    "gate_id",
    "lifecycleId",
    "lifecycle_id",
    "agentId",
    "agent_id",
    "sessionId",
    "session_id",
    "leaf_key",
    "qualified leaf",
    "coordination leaf",
)
CALLER_FACING_ROOTS = (
    REPOSITORY_ROOT / "skills",
    REPOSITORY_ROOT / "agents-md-files",
    REPOSITORY_ROOT / "docs",
    REPOSITORY_ROOT / "scripts/harness",
)
CALLER_FACING_FILES = (
    REPOSITORY_ROOT / "AGENTS.md",
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / ".pi/extensions/agents-remember-start.ts",
)
ROLE_DISPATCH_CONTEXTS = {
    "architect.md": "ambient-bootstrap target; plane-hosted caller after startup",
    "orchestrator.md": "plane-hosted caller; ambient takeover target",
    "manager.md": "plane-hosted caller; ambient takeover target",
    "strategist.md": "target-only role; ambient takeover target",
    "worker.md": "target-only role; ambient takeover target",
    "reviewer.md": "target-only role; ambient takeover target",
    "curator.md": "target-only role; ambient takeover target",
    "designer.md": "target-only role; ambient takeover target",
    "system-specialist.md": "target-only role; ambient takeover target",
}


def _agent_instruction_files(root: Path) -> list[Path]:
    return sorted((*root.joinpath("roles").glob("*.md"), *root.joinpath("templates").glob("*.md")))


def _caller_facing_instruction_files() -> list[Path]:
    discovered = {
        path
        for root in CALLER_FACING_ROOTS
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".md", ".txt"}
    }
    return sorted(discovered | set(CALLER_FACING_FILES))


@pytest.mark.parametrize("skill_root", [CANONICAL_SKILL, PACKAGED_SKILL])
def test_agent_doctrine_contains_no_control_plane_address_instructions(skill_root: Path) -> None:
    for path in _agent_instruction_files(skill_root):
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_AGENT_INSTRUCTIONS:
            assert forbidden.casefold() not in text.casefold(), (
                f"{path.relative_to(skill_root)}: {forbidden}"
            )


def test_packaged_agent_doctrine_is_the_canonical_skill_exactly() -> None:
    canonical = {
        path.relative_to(CANONICAL_SKILL): path.read_bytes()
        for path in CANONICAL_SKILL.rglob("*")
        if path.is_file()
    }
    packaged = {
        path.relative_to(PACKAGED_SKILL): path.read_bytes()
        for path in PACKAGED_SKILL.rglob("*")
        if path.is_file()
    }

    assert packaged == canonical


def test_caller_facing_instructions_never_advertise_internal_spawn_primitive() -> None:
    offenders = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in _caller_facing_instruction_files()
        if "spawn_agent_session" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


@pytest.mark.parametrize(("role_file", "caller_context"), ROLE_DISPATCH_CONTEXTS.items())
def test_every_role_tool_table_states_its_dispatch_caller_context(
    role_file: str, caller_context: str
) -> None:
    text = _doctrine(f"roles/{role_file}")
    assert f"| dispatch | {caller_context}" in text
    assert "`dispatch_agent`" in text
    assert "`dispatch` and `tools` are structural authority/capability descriptions" in text
    assert "never settings keys" in text


@pytest.mark.parametrize(
    "path",
    (
        SOURCE_SKILL / "SKILL.md",
        REPOSITORY_ROOT / "AGENTS.md",
        REPOSITORY_ROOT / "agents-md-files/coordinator/AGENTS.md",
        REPOSITORY_ROOT / "scripts/harness/shared/session-start-directive.md",
        REPOSITORY_ROOT / "scripts/harness/shared/workspace-directive.md",
        REPOSITORY_ROOT / "skills/c-13-install-and-onboard/SKILL.md",
    ),
)
def test_launcher_doctrine_separates_ordinary_bootstrap_from_explicit_takeover(path: Path) -> None:
    text = " ".join(path.read_text(encoding="utf-8").split()).casefold()
    assert "ordinary" in text, path.relative_to(REPOSITORY_ROOT)
    assert "task-seat takeover" in text, path.relative_to(REPOSITORY_ROOT)


def test_takeover_doctrine_makes_lineage_conflicts_resumable() -> None:
    text = _doctrine("SKILL.md")
    assert "source-lineage-stale" in text
    assert "source-lineage-unavailable" in text
    assert "resumable reconciliation phase" in text
    assert "advertised continuation" in text
    assert "semantic truth" in text


def test_malformed_role_identity_fails_closed_instead_of_falling_through() -> None:
    text = _doctrine("SKILL.md")
    assert "unresolvable `AR_SPAWN_ROLE`" in text
    assert "malformed hosted identity and fails closed" in text
    assert "never falls through to a pasted brief or free-chat routing" in text


def test_reviewer_doctrine_names_every_runtime_review_parent_and_altitude() -> None:
    text = _doctrine("roles/reviewer.md")
    for required in (
        "leaf reviewer binds the leaf and reports to its manager",
        "master-exit reviewer binds the master and reports to that manager",
        "portfolio plan reviewer binds the sprint and reports to the architect",
        "super-exit reviewer binds the sprint and reports to the orchestrator",
    ):
        assert required in text


def test_doctrine_never_advertises_removed_dispatch_env_arguments() -> None:
    offenders = [
        path.relative_to(SOURCE_SKILL).as_posix()
        for path in _agent_instruction_files(SOURCE_SKILL)
        if 'env={"AR_SPAWN_ROLE"' in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "path",
    (
        SOURCE_SKILL / "SKILL.md",
        SOURCE_SKILL / "templates/architect-brief.md",
        REPOSITORY_ROOT / "agents-md-files/coordinator/AGENTS.md",
        REPOSITORY_ROOT / "skills/c-13-install-and-onboard/SKILL.md",
        REPOSITORY_ROOT / "docs/reference/mcp-tools.md",
        REPOSITORY_ROOT / "scripts/harness/shared/session-start-directive.md",
        REPOSITORY_ROOT / "mcp/src/agents_remember/mcp/registration/sessions.py",
    ),
)
def test_launcher_doctrine_carries_the_complete_caller_kind_contract(path: Path) -> None:
    text = " ".join(path.read_text(encoding="utf-8").split()).casefold()
    for required in ("dispatch_agent", "plane", "ambient", "brief"):
        assert required in text, f"{path.relative_to(REPOSITORY_ROOT)}: {required}"
    assert "fallback" in text or "falls back" in text or "never becomes an ambient retry" in text


def _doctrine(path: str) -> str:
    return " ".join(SOURCE_SKILL.joinpath(path).read_text(encoding="utf-8").split())


def _assert_architect_doctrine(text: str) -> None:
    assert all(
        term in text
        for term in (
            "executionGraph",
            "executionNature",
            "recommend **yes**",
            "Never dispatch the strategist without the developer's yes",
            "Resolve this before step 1 above",
            "author_execution_graph",
            "An organizational master has no branch",
            "recommend skipping only when a ruled plan is complete",
        )
    )


def _assert_strategist_doctrine(text: str) -> None:
    assert all(
        term in text
        for term in (
            "Detection/judgment split",
            "`organizational`",
            "`atomic`",
            "critical / high / normal / low",
            "A common foundation required by leaves in multiple masters",
            "Large size alone is not a reason",
            "first, between waves, or last",
            "throwaway experiment",
            "facts compiled by the architect for an initial pass",
            "supplied by the orchestrator through the architect for a runtime reshape",
        )
    )


def _assert_orchestrator_doctrine(text: str) -> None:
    assert all(
        term in text
        for term in (
            "Execution loop — recompute after every material event",
            "stable graph tie-break",
            "Organizational:",
            "Atomic:",
            "integration branches are not workbenches",
            "bounded reprioritization are this seat's job",
            "multi-master reshape is substantial",
            "full acceptance once **before** moving super",
            "the orchestration task's Judgment Register",
            "rationale, evidence, author, confidence, and supersession",
            "Initial planning is already resolved before this seat is spawned",
            "The architect owns and rules the plan-review loop",
            "builder only when the developer sanctioned a strategist skip",
            "plan-review reviewer seats are architect children",
        )
    )


def _assert_manager_doctrine(text: str) -> None:
    assert all(
        term in text
        for term in (
            "Publish closeout-door truth; do not rank the portfolio",
            "An `organizational` leaf lands directly",
            "an `atomic` leaf lands only",
            "exact candidate tree, routes/seams, and complete admission evidence",
            "only after the orchestrator released",
        )
    )


def _assert_reviewer_doctrine(text: str) -> None:
    assert all(
        term in text
        for term in (
            "exact proposed final super candidate containing prior landed leaf contributions",
            "owning or reopened leaf",
            "integration branches are not repair workbenches",
        )
    )


def _assert_orchestration_task_doctrine(text: str) -> None:
    assert all(
        term in text
        for term in (
            "Mechanical Fact Inventory",
            "Judgment Register (canonical judgment authority)",
            "Evidence/fact refs",
            "Judgment id (required when selected into executionGraph)",
            "no relation selected into `executionGraph` may omit its judgment id",
            "Master Execution Nature (explicit judgment)",
            "Priority Register (explicit judgment)",
            "Canonical executionGraph Adoption Payload",
            "Derived Waves And Blocker Walk",
            "blocker-placement judgment <id>",
            "sprint decision log and Judgment Register",
        )
    )


def _assert_manager_brief_doctrine(text: str) -> None:
    assert all(
        term in text
        for term in (
            "Execution nature:",
            "Closeout-door publication:",
            "Build concurrency does not grant",
            "before it lands",
            "exact proposed final super candidate containing the master's prior landed "
            "contributions",
        )
    )


def test_execution_topology_doctrine_assigns_fact_judgment_and_queue_ownership() -> None:
    _assert_architect_doctrine(_doctrine("roles/architect.md"))
    _assert_strategist_doctrine(_doctrine("roles/strategist.md"))
    _assert_orchestrator_doctrine(_doctrine("roles/orchestrator.md"))
    _assert_manager_doctrine(_doctrine("roles/manager.md"))
    _assert_reviewer_doctrine(_doctrine("roles/reviewer.md"))
    _assert_orchestration_task_doctrine(_doctrine("templates/orchestration-task.md"))
    _assert_manager_brief_doctrine(_doctrine("templates/manager-brief.md"))

    lifecycle = _doctrine("SKILL.md")
    assert "organizational leaf requires super → leaf" in lifecycle
    assert "atomic path requires super → master → leaf" in lifecycle
    assert "| Portfolio plan | the architect |" in lifecycle
    assert "proposed final organizational super candidate before it lands" in _doctrine(
        "roles/worker.md"
    )

    handover = _doctrine("templates/master-handover-packet.md")
    assert "prior landed leaf commits plus the proposed final leaf" in handover
    assert "exact proposed final super candidate" in handover
    assert "before final organizational leaf moves super" in handover

    verdict = _doctrine("templates/verdict.md")
    assert "exact proposed organizational super candidate including final leaf" in verdict
    assert "owning or reopened leaf, or new scoped fix leaf" in verdict
    assert "proposed final organizational super candidate or atomic landing" in verdict
    assert "architect for the plan review" in verdict

    assert "proposed final organizational super candidate before it lands" in _doctrine(
        "templates/worker-brief.md"
    )

    plan_review = _doctrine("criteria/plan-review.md")
    assert "PR-6 — Detection/judgment boundary and runtime ownership" in plan_review
    assert "owner = architect" in plan_review
    assert "orchestrator on a sanctioned strategist skip" in plan_review

    doctrine_review = _doctrine("criteria/doctrine.md")
    assert "D-4 — Topology and authority sweep" in doctrine_review
    assert "D-5 — Detection is not judgment" in doctrine_review


def test_agent_doctrine_contains_no_retired_fixed_master_branch_topology() -> None:
    doctrine_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SOURCE_SKILL.rglob("*.md"))
    )
    retired = (
        "master branches off the **current super**",
        "master-A integration branch",
        "remediate on the super worktree",
        "before implementation starts on any of them",
        "if leaf-level cross-deps interleave, reshape master boundaries",
        "runs once per master at master integration",
        "leaf→master and master→super",
        "slug into the sprint doc's `orchestrates`",
        "super worktree",
        "super-worktree",
        "recommend skipping when a ruled plan already exists",
        "owner = orchestrator, builder = strategist",
        "owner = this seat · builder = strategist",
        "Strategist and separate designer seats are architect children; reviewers are manager "
        "children.",
    )
    for phrase in retired:
        assert phrase.casefold() not in doctrine_text.casefold(), phrase


def test_execution_topology_templates_have_rectangular_markdown_tables() -> None:
    for relative_path in (
        "templates/orchestration-task.md",
        "templates/master-handover-packet.md",
        "templates/verdict.md",
    ):
        table: list[str] = []
        for line in [*_doctrine(relative_path).splitlines(), ""]:
            if line.startswith("|"):
                table.append(line)
                continue
            if not table:
                continue
            widths = {row.count("|") for row in table}
            assert len(widths) == 1, f"{relative_path}: ragged table {table!r}"
            table = []
