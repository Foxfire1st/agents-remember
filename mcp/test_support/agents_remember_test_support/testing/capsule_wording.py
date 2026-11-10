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
    "worker": "Send messages through the assigned routes",
    "reviewer": "Messages and working files",
    "curator": "Send messages and keep working files",
}
MESSAGE_OPERATION_ROUTES = {
    "worker": "Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.",
    "reviewer": "Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.",
    "curator": "Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.",
}
ROLE_OPERATION_ROUTES = {
    "worker": (
        (
            "Before writing the report, follow [Implementation outputs]"
            "(../operations/implementation.md#outputs) "
            "for report contents, checks and the freeze procedure."
        ),
        (
            "Before deciding whether a handover was taken or responding to a provider, seat or busy "
            "refusal, read [implementation boundaries](../operations/implementation.md#boundaries) "
            "for consumption evidence and the owner\N{RIGHT SINGLE QUOTATION MARK}s recovery route."
        ),
    ),
    "reviewer": (
        (
            "Before inspecting the assigned packet or candidate, follow [the review intake "
            "procedure](../operations/review.md#receive-the-intake-and-hand-over-the-leaf-verdict)"
            " for the mode, handover scope and chat boundary."
        ),
        (
            "Before recording an ordinary leaf code round, read [code review round recording]"
            "(../operations/review.md#record-code-review-rounds) for begin and result records. "
            "Before recording a memory round, requesting an extra round or handling provider, seat "
            "or busy refusals, read [review boundaries](../operations/review.md#boundaries) for the "
            "recording owner, approval and recovery routes."
        ),
    ),
    "curator": (
        "Before intake and writer admission, read [required curation inputs](../operations/curation.md#required-inputs) for the candidate, scope and lists, and [bound intake and capabilities](../operations/curation.md#bound-paseo-intake-and-capabilities) for the serving writer and capability checks.",
        "The curation operation owns the workflow sequence.",
    ),
}
MEMORY_HANDOVER_SOURCE = "reference/curator/memory-handover.md"
MEMORY_HANDOVER_ACTION = (
    "Before sending a memory candidate for review or returning a memory repair, read"
)
MEMORY_DETAIL_FACETS = frozenset(
    {
        "identity and whole stretch",
        "memory lane and count",
        "memory leaves code untouched",
        "memory ordinary limit",
        "memory extra approval",
    }
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
    """Read the active seat's own autonomy duties."""
    instruction_owner(root, role)
    return (root / f"roles/{role}.md").read_text()


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
    # The source route is checked above, including its exact target. Delivery has
    # rebound links, so check its action while delivered_step checks the frozen file.
    assert "Before responding to a full-store refusal, read" in instructions
    return text


def _memory_surface(root: Path, report_path: Path) -> str:
    role_text = (root / "roles/curator.md").read_text()
    section = re.search(r"(?ms)^## Reconcile, author and check\n(.*?)(?=^## |\Z)", role_text)
    assert section is not None
    assert "{{detail:" + MEMORY_HANDOVER_SOURCE + "}}" in section[1]
    pointer = "{{detail:" + MEMORY_HANDOVER_SOURCE + "}}"
    paragraph = next(part for part in section[1].split("\n\n") if pointer in part)
    assert MEMORY_HANDOVER_ACTION in normalized(paragraph)
    assert "Reuse it when returning a repair." in paragraph
    instructions, text = delivered_step(
        root,
        ("curator", "curation"),
        MEMORY_HANDOVER_SOURCE,
        MEMORY_HANDOVER_ACTION,
        report_path,
    )
    assert (
        "Before sending a memory candidate for review or returning a memory repair, read [memory handover]("
        in instructions
    )
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
    target = quote(owner.resolve().as_posix(), safe="/:@") + "#boundaries"
    expected = (
        "Before sending a memory candidate for review, read [Review boundaries](" + target + ")"
    )
    assert handover.count(expected) == 1, ("memory review owner", expected)
    links = re.findall(r"\[Review boundaries\]\(([^)\n]+)\)", handover)
    assert links == [target], ("memory review owner", target, links)


def _memory_review_surface(root: Path, report_path: Path) -> str:
    route = "Before sending a memory candidate for review, read [Review boundaries](../../operations/review.md#boundaries)"
    assert route in (root / MEMORY_HANDOVER_SOURCE).read_text()
    with shipped_composition_corpus() as (shipped, _manifest):
        owner = shipped / "operations/review.md"
        assert owner.read_bytes() == (root / "operations/review.md").read_bytes()
        handover = _memory_surface(root, report_path)
        assert_memory_review_owner_link(handover, owner)
        return _owner_section(shipped, "operations/review.md", "Boundaries")


def _curator_memory_surface(root: Path, source: str, name: str, report_path: Path) -> str:
    instruction_surface(root, source)
    # P5-18: the Curator's one memory-handover pointer is in its role file, for every operation.
    route = "Before sending a memory candidate for review or returning a memory repair, read [memory handover]({{detail:reference/curator/memory-handover.md}})"
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
    referral = "Before handing over a frozen candidate, follow [Hand over the frozen candidate](../operations/implementation.md#hand-over-the-frozen-candidate)"
    assert referral in role
    assert "Preserve the producer data verbatim for that exact freeze." in role
    instruction_surface(root, source)
    return _owner_section(root, "operations/implementation.md", "Hand over the frozen candidate")


# Full pointer sentences (P5-23: restored from fragments after the P5-15 content clauses).
CURATION_REFUSED_HANDOVER_POINTER = (
    "Before responding to a non-work reply, an empty or doubled-seat refusal, an "
    "unreachable-Manager notice, or failed delivery of a required Manager notice, read "
    "[Report a refused handover](implementation.md#report-a-refused-handover) for the "
    "seat/problem report, pending state, independent continuation and notice retry."
)
REFUSED_HANDOVER_POINTER = (
    "Before responding to a non-work reply, empty or doubled-seat refusal, or an "
    "unreachable-Manager notice, read [Repair defects and report a refused handover]"
    "(implementation.md#repair-defects-and-report-a-refused-handover) for its seat/problem "
    "report and retry procedure."
)


def _upward_notice_surface(root: Path, source: str) -> str:
    instruction_surface(root, source)
    if source.startswith("roles/"):
        role = _leaf_role(source)
        operation = "operations/" + ROLE_OPERATIONS[role] + ".md"
    else:
        operation = source
    if operation == "operations/curation.md":
        section = _owner_section(root, operation, "Handoff / exit")
        assert CURATION_REFUSED_HANDOVER_POINTER in section
    elif operation != "operations/implementation.md":
        assert REFUSED_HANDOVER_POINTER in (root / operation).read_text()
    return _owner_section(
        root, "operations/implementation.md", "Repair defects and report a refused handover"
    )


def clause_surface(  # noqa: PLR0911
    root: Path, source: str, name: str, report_path: Path
) -> str:
    """Resolve each relocated obligation through its admitted role and named action."""
    if name == "busy retry":
        return _message_surface(root, source, report_path)
    if source in {"roles/curator.md", "operations/curation.md"} and name in MEMORY_DETAIL_FACETS:
        return _curator_memory_surface(root, source, name, report_path)
    if source == "roles/worker.md" and name in {"Reviewer start notice", "producer data preserved"}:
        return _worker_handover_surface(root, source)
    if name == "action evidence":
        # P5-23 b3: the rule's own section, not the whole shared file.
        return _owner_section(root, "core/every-role.md", "Parallel work and waiting")
    if name == "upward notice recovery":
        return _upward_notice_surface(root, source)
    if source in {"roles/reviewer.md", "operations/review.md"}:
        instructions = instruction_surface(root, source)
        if name == "post-pass concern":
            route = "Before answering a Curator code finding, read [Curation failure handling](curation.md#failure-handling) for the Curator's evidenced concern and the Reviewer's recorded finding or explained rejection. Send your answer directly to the Curator and Worker."
            assert route in (root / "operations/review.md").read_text()
            assert normalized(route) in normalized(instructions)
            return _owner_section(root, "operations/curation.md", "Failure handling")
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
                    "record it as pending",
                    "continue independent work",
                    "retry the notice through role_message on agents-remember-task",
                    "Do not turn that delivery failure into a question to the developer in your own chat",
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
    inputs = instructions.split("## Confirm the assignment before inspection\n", 1)[1].split(
        "## Bind the seam and catalogs", 1
    )[0]
    assert "Before inspecting the assigned packet or candidate, follow" in inputs
    assert "#receive-the-intake-and-hand-over-the-leaf-verdict)" in inputs
    intake = instructions.split("## Receive the intake and hand over the leaf verdict\n", 1)[1]
    intake = intake.split("\n## ", 1)[0]
    assert "If the opening names no review mode," in intake
    for clause in (
        "ask your starter for its first `role_message`",
        "and wait",
        "A Reviewer started from the dashboard has no starter",
        "asks the developer in its own chat instead",
        "Inspect nothing and infer no mode before the answer arrives",
    ):
        assert clause in intake, clause
    received = inputs.split("Once intake arrives,", 1)[1]
    for clause in (
        "require every applicable input below; refuse and report omissions rather than guessing",
        "Exactly one review mode",
        "baseline",
        "fix-verification",
    ):
        assert clause in received, clause
