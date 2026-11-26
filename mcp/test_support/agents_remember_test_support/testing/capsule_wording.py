"""Check current obligations on their admitted role, operation and detail surfaces."""

import json
import re
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from urllib.parse import quote

from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.role_capsules.vocabulary import CapsuleOperation, CapsuleRole
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

from agents_remember_test_support.testing.instruction_renewal import RENEW_COMMAND
from agents_remember_test_support.testing.leaf_instruction_wording import (
    LEAF_CLAUSES,
    MESSAGE_HANDOVER_ACTIONS,
    MESSAGE_HANDOVER_SOURCE,
    assert_action_pointer,
    autonomy_clauses,
    delivered_step,
    normalized,
)

ROLE_OPERATIONS: dict[CapsuleRole, CapsuleOperation] = {
    "worker": "implementation",
    "reviewer": "review",
    "curator": "curation",
}

MESSAGE_SECTIONS = {
    "worker": "Reference index",
    "reviewer": "Reference index",
    "curator": "Reference index",
}
MESSAGE_OPERATION_ROUTES = {
    "worker": (
        "Send Reviewer report path (no pasted text), head/hash + next stretch via `role_message`."
    ),
    "reviewer": (
        "Send verdict path directly to Worker via `role_message` "
        "(no pasted text; block names sealed report + repair stretch)."
    ),
    "curator": (
        "Send memory candidate to Reviewer via `role_message` (sibling role, leaf refs, whole IDs);"
    ),
}
ROLE_OPERATION_ROUTES = {
    "worker": (
        "[report]({{detail:templates/turn-report.md}}#shape)",
        "[sync]({{detail:reference/worker/sync-and-repairs.md}})",
        "[msg]({{detail:reference/message-handover.md}})",
    ),
    "reviewer": (
        "[rounds]({{detail:reference/review/rounds-and-late-findings.md}})",
        "[verdict](../templates/verdict.md)",
        "[msg]({{detail:reference/message-handover.md}})",
    ),
    "curator": (
        "[handover]({{detail:reference/curator/memory-handover.md}})",
        "[exceptions]({{detail:reference/curator/exceptions-and-recovery.md}})",
        "[msg]({{detail:reference/message-handover.md}})",
    ),
}
MEMORY_HANDOVER_SOURCE = "reference/curator/memory-handover.md"
MEMORY_HANDOVER_ACTION = "[handover]("
MEMORY_DETAIL_FACETS = frozenset(
    {
        "identity and whole stretch",
        "memory lane and count",
        "memory leaves code untouched",
        "memory ordinary limit",
        "memory extra approval",
    }
)
CURATOR_CODE_CONCERN_FACETS = frozenset(
    {"post-pass code finding", "code answer", "finding and answer report"}
)
WORKER_REFUSAL_FACETS = frozenset(
    {"nonaction notice", "empty doubled refusal", "sender owes contents"}
)


def _leaf_role(source: str) -> CapsuleRole:
    kind, filename = Path(source).parts
    owner = Path(filename).stem
    if kind == "roles":
        assert owner in ROLE_OPERATIONS, source
        return next(role for role in ROLE_OPERATIONS if role == owner)
    assert kind == "operations", source
    return next(role for role, operation in ROLE_OPERATIONS.items() if operation == owner)


def _instruction_text(
    root: Path, role: CapsuleRole, operation: CapsuleOperation | None = None
) -> str:
    operation = ROLE_OPERATIONS[role] if operation is None else operation
    manifest = (root / "composition-manifest.json").read_bytes()
    binding = CapsuleBinding(
        operation=operation,
        admitted=CapsuleAdmittedFacts(
            task_reference="fixture/leaf.json",
            task_document_digest=compute_content_digest(b'{"id":"leaf"}'),
            seat=CapsuleRoleSeat(role, json.loads(manifest)["roles"][role]["altitude"]),
            repository_id="fixture",
            work_branch="fixture",
            tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
        ),
    )
    admission = routed_admission_for(
        root, "composition-manifest.json", manifest, CapsuleSeatAddress(role, operation)
    )
    outcome = compile_admitted_capsule(binding, admission)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    return outcome.result.render_instructions()


def instruction_owner(root: Path, role: str) -> str:
    """Require the actual active role source; no retired method-role adaptation."""
    assert (root / f"roles/{role}.md").is_file(), role
    return role


def autonomy_surface(root: Path, role: str) -> str:
    """Read the active seat's own autonomy duties alongside shared harness-autonomy owners."""
    instruction_owner(root, role)
    sources = (
        f"roles/{role}.md",
        "core/authority.md",
        "core/every-role.md",
        "core/invariants.md",
        "SKILL.md",
    )
    return "\n".join((root / source).read_text(encoding="utf-8") for source in sources)


def instruction_surface(root: Path, source: str) -> str:
    """Read a leaf duty on its selected stage, never an unfiltered other-role source."""
    if source not in LEAF_CLAUSES:
        return (root / source).read_text()
    role = _leaf_role(source)
    instructions = _instruction_text(root, role)
    role_text = (root / f"roles/{role}.md").read_text()
    for route in ROLE_OPERATION_ROUTES[role]:
        assert normalized(route) in normalized(role_text), (source, route)
        assert normalized(route) in normalized(instructions), (source, route)
    return instructions


def surface_clauses(source: str, clauses: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """Check the declared leaf surface without adding historical alternatives."""
    assert source in LEAF_CLAUSES, source
    return clauses


def _message_surface(root: Path, source: str, report_path: Path) -> str:
    role = _leaf_role(source)
    role_text = (root / f"roles/{role}.md").read_text()
    heading = re.escape(MESSAGE_SECTIONS[role])
    messages = re.search(rf"(?ms)^## {heading}\n(.*?)(?=^## |\Z)", role_text)
    assert messages is not None, role
    assert "{{detail:" + MESSAGE_HANDOVER_SOURCE + "}}" in messages[1]
    assert_action_pointer(messages[1], MESSAGE_HANDOVER_SOURCE, MESSAGE_HANDOVER_ACTIONS[role])
    route = MESSAGE_OPERATION_ROUTES[role]
    assert normalized(route) in normalized(
        (root / f"operations/{ROLE_OPERATIONS[role]}.md").read_text()
    )
    instructions, text = delivered_step(
        root,
        (role, ROLE_OPERATIONS[role]),
        MESSAGE_HANDOVER_SOURCE,
        MESSAGE_HANDOVER_ACTIONS[role],
        report_path,
    )
    assert normalized(route) in normalized(instructions)
    return text


def _memory_surface(root: Path, report_path: Path) -> str:
    role_text = (root / "roles/curator.md").read_text()
    section = re.search(r"(?ms)^## Reference index\n(.*?)(?=^## |\Z)", role_text)
    assert section is not None
    pointer = "{{detail:" + MEMORY_HANDOVER_SOURCE + "}}"
    assert pointer in section[1]
    assert MEMORY_HANDOVER_ACTION + pointer + ")" in section[1]
    instructions, text = delivered_step(
        root,
        ("curator", "curation"),
        MEMORY_HANDOVER_SOURCE,
        MEMORY_HANDOVER_ACTION,
        report_path,
    )
    assert "[handover](" in instructions
    return text


def _owner_section(root: Path, source: str, heading: str) -> str:
    """Select the exact owning section, including its subordinate action headings."""
    text = (root / source).read_text()
    match = re.search(r"(?m)^(#{2,4}) " + re.escape(heading) + r"\n", text)
    assert match is not None, (source, heading)
    following = text[match.end() :]
    boundary = re.search(r"(?m)^#{1," + str(len(match[1])) + r"} ", following)
    return following[: boundary.start()] if boundary else following


def assert_memory_review_owner_link(handover: str, owner: Path) -> None:
    """Require the full emitted action, exact shipped owner, fragment and one occurrence."""
    target = quote(owner.resolve().as_posix(), safe="/:@") + "#rounds"
    expected = "Before sending a memory candidate for review, read [Review rounds](" + target + ")"
    assert handover.count(expected) == 1, ("memory review owner", expected)
    links = re.findall(r"\[Review rounds\]\(([^)\n]+)\)", handover)
    assert links == [target], ("memory review owner", target, links)


def _memory_review_surface(root: Path, report_path: Path) -> str:
    route = (
        "Before sending a memory candidate for review, read "
        "[Review rounds](../../operations/review.md#rounds)"
    )
    assert route in (root / MEMORY_HANDOVER_SOURCE).read_text()
    with shipped_composition_corpus() as (shipped, _manifest):
        owner = shipped / "operations/review.md"
        assert owner.read_bytes() == (root / "operations/review.md").read_bytes(), (
            f"Run {RENEW_COMMAND}"
        )
        handover = _memory_surface(root, report_path)
        assert_memory_review_owner_link(handover, owner)
        return _owner_section(shipped, "operations/review.md", "Rounds")


def _curator_memory_surface(root: Path, source: str, name: str, report_path: Path) -> str:
    instruction_surface(root, source)
    route = "[handover]({{detail:reference/curator/memory-handover.md}})"
    assert (root / "roles/curator.md").read_text().count(route) == 1
    if source == "operations/curation.md":
        assert (
            "memory-handover.md#send-the-whole-requested-review-or-repair"
            not in (root / source).read_text()
        )
    if name == "identity and whole stretch":
        return _memory_surface(root, report_path)
    return _memory_review_surface(root, report_path)


def _worker_handover_surface(root: Path, source: str) -> str:
    role = (root / source).read_text()
    assert "<!-- ar:gives freeze-handoff to reviewer:" in role
    assert "<!-- ar:gives producer-list to curator:" in role
    return instruction_surface(root, source)


def _upward_notice_surface(root: Path, source: str) -> str:
    instruction_surface(root, source)
    assert (
        "[sync]({{detail:reference/worker/sync-and-repairs.md}})"
        in (root / "roles/worker.md").read_text()
    )
    assert (
        "[sync]({{detail:reference/worker/sync-and-repairs.md}}#synchronize-the-leaf)"
        in (root / "roles/curator.md").read_text()
    )
    return _owner_section(root, "reference/worker/sync-and-repairs.md", "Report a refused handover")


def _reviewer_detail_surface(root: Path, source: str, name: str) -> str | None:
    if name == "post-pass concern":
        rounds = (root / "reference/review/rounds-and-late-findings.md").read_text()
        assert (
            "../curator/exceptions-and-recovery.md#memory-tool-failures-code-concerns-and-sync-conflicts"
            in rounds
        )
        return _owner_section(
            root,
            "reference/curator/exceptions-and-recovery.md",
            "Memory-tool failures, code concerns, and sync conflicts",
        )
    if name in {"reopen or refute answer", "extra round beyond the limit"}:
        instruction_surface(root, source)
        return (root / "reference/review/rounds-and-late-findings.md").read_text()
    if name == "preceding result":
        instruction_surface(root, source)
        return (root / "templates/verdict.md").read_text()
    return None


def _worker_detail_surface(root: Path, source: str, name: str) -> str | None:
    if name in {"Reviewer start notice", "producer data preserved"}:
        return _worker_handover_surface(root, source)
    if name == "seat address":
        return _owner_section(root, "operations/coordination.md", "Address and deliver a message")
    if name == "whole product identity":
        return (root / "SKILL.md").read_text()
    if name in WORKER_REFUSAL_FACETS:
        return _upward_notice_surface(root, source)
    return None


def _curator_detail_surface(root: Path, source: str, name: str, report_path: Path) -> str | None:
    if name in MEMORY_DETAIL_FACETS:
        return _curator_memory_surface(root, source, name, report_path)
    if name in CURATOR_CODE_CONCERN_FACETS:
        instruction_surface(root, source)
        return _owner_section(
            root,
            "reference/curator/exceptions-and-recovery.md",
            "Memory-tool failures, code concerns, and sync conflicts",
        )
    return None


def clause_surface(  # noqa: PLR0911
    root: Path, source: str, name: str, report_path: Path
) -> str:
    """Resolve each relocated obligation through its admitted role and named action."""
    if source.startswith("roles/") and name in autonomy_clauses(Path(source).stem):
        return autonomy_surface(root, Path(source).stem)
    if name == "busy retry":
        return _message_surface(root, source, report_path)
    if name == "action evidence":
        return _owner_section(root, "core/authority.md", "Hand over work and wait for messages")
    if name == "upward notice recovery":
        return _upward_notice_surface(root, source)
    if name == "factual independence":
        instruction_surface(root, source)
        assert (
            "[list](../templates/curator-handoff-list.md)" in (root / "roles/worker.md").read_text()
        )
        return (root / "templates/curator-handoff-list.md").read_text()
    if source == "roles/worker.md" and (detail := _worker_detail_surface(root, source, name)):
        return detail
    if source in {"roles/curator.md", "operations/curation.md"} and (
        detail := _curator_detail_surface(root, source, name, report_path)
    ):
        return detail
    if source in {"roles/reviewer.md", "operations/review.md"} and (
        detail := _reviewer_detail_surface(root, source, name)
    ):
        return detail
    return instruction_surface(root, source)


def clause_mutations(
    root: Path, roles: Iterable[str]
) -> Iterator[tuple[str, str, dict[str, tuple[str, ...]], str, str]]:
    """Delete every complete clause after resolving its actual delivered owner."""
    surfaces = dict(LEAF_CLAUSES)
    surfaces.update(
        {
            f"roles/{role}.md": {
                **LEAF_CLAUSES.get(f"roles/{role}.md", {}),
                **autonomy_clauses(role),
            }
            for role in roles
        }
    )
    for source, clauses in surfaces.items():
        for name, alternatives in clauses.items():
            with tempfile.TemporaryDirectory() as directory:
                reading = normalized(
                    clause_surface(root, source, name, Path(directory) / "report.md")
                )
            mutant = reading
            for clause in alternatives:
                mutant = mutant.replace(normalized(clause), "")
            yield source, name, clauses, reading, mutant
            if name == "upward notice recovery":
                for facet in (
                    "required Manager notice fails delivery",
                    "record the notice as pending",
                    "continue independent work",
                    "retry the notice through role_message",
                    "Never turn a delivery failure into a question to the developer in your own chat",
                ):
                    assert normalized(facet) in reading, (source, facet)
                    yield source, name, clauses, reading, reading.replace(normalized(facet), "")


def assert_git_comparison_scope(root: Path) -> None:
    """Require both code trees and both memories on the actual delivered detail."""
    with tempfile.TemporaryDirectory() as directory:
        handoff = _memory_surface(root, Path(directory) / "report.md")
    comparison = (
        "The Reviewer compares the leaf's base and candidate in Git: the code trees "
        "and the knowledge files in both memory trees. The Curator records no separate comparison."
    )
    assert normalized(comparison) in normalized(handoff)
    for component in (
        "leaf's base and candidate",
        "the code trees",
        "the knowledge files in both memory trees",
        "The Curator records no separate comparison.",
    ):
        mutant = normalized(handoff).replace(normalized(component), "")
        assert mutant != normalized(handoff)
        assert normalized(comparison) not in mutant


def assert_reviewer_initial_intake(instructions: str) -> None:
    """Check the delivered intake owner, both missing-mode routes and received inputs."""
    assert (
        "If mode is absent, ask starter via `role_message` (developer if parentless) and wait."
        in instructions
    )
    for clause in (
        "Require before inspecting:",
        "mode (`baseline`/`fix-verification`; no whole-review in `fix-verification`)",
        'exact candidate (no branch/"latest") + clean-copy checks (withhold PASS until passing)',
        "`system/tools.md` + `system/design-guidelines.md` + guidelines.",
    ):
        assert clause in instructions, clause
