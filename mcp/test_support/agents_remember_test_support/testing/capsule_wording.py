"""Check affirmative clauses on their delivered role/stage surface after R125 relocation."""

import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

from agents_remember_test_support.testing.leaf_instruction_wording import (
    LEAF_CLAUSES,
    autonomy_clauses,
    delivered_step,
    normalized,
)

# Same facets, on their actual D301 stage; never admit these for other roles.
CURATOR_EQUIVALENTS = {
    "memory to reviewer": "Send the memory candidate directly to Reviewer for its whole memory check",
    "identity and whole stretch": "Each handover names leaf, code head/change hash, memory identity, report/verdict path and whole next stretch.",
    "post-pass code finding": "Send code concerns to Reviewer and Worker, including after code PASS.",
    "post-pass code finding role": "Send code questions and evidenced concerns under `../operations/curation.md` §Failure handling, including after code PASS.",
    "code answer": "The Reviewer records a finding and reopens the point, or explains its rejection.",
    "finding and answer report": "Report the finding and answer;",
    "memory lane and count": "Reviewer records `begin_review`/`record_review`, `reviewKind=memory`: separate sealed memory IDs/reports/count",
    "memory ordinary limit": "code-review limit; code state unchanged.",
    "memory extra approval": "Extras need Manager-recorded architect/developer approval.",
    "upward notice recovery": "If required Manager notice fails delivery, report it pending, continue independent work and retry via `role_message` on `agents-remember-task`; never ask the developer in your chat instead.",
}


def instruction_surface(root: Path, source: str) -> str:
    """Curator procedures moved to its addressed stage; never concatenate unfiltered sources."""
    if source != "roles/curator.md":
        return (root / source).read_text()
    manifest = (root / "composition-manifest.json").read_bytes()
    binding = CapsuleBinding(
        operation="curation",
        admitted=CapsuleAdmittedFacts(
            task_reference="fixture/leaf.json",
            task_document_digest=compute_content_digest(b'{"id":"leaf"}'),
            seat=CapsuleRoleSeat("curator", "leaf"),
            repository_id="fixture",
            work_branch="fixture",
            tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
        ),
    )
    admission = routed_admission_for(
        root, "composition-manifest.json", manifest, CapsuleSeatAddress("curator", "curation")
    )
    outcome = compile_admitted_capsule(binding, admission)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    return outcome.result.render_instructions()


def surface_clauses(source: str, clauses: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """Keep the same promises and mutation checks, accepting their fixed stage wording too."""
    if source not in {"roles/curator.md", "operations/curation.md"}:
        return clauses
    stage = LEAF_CLAUSES["operations/curation.md"] if source == "roles/curator.md" else {}
    return {
        name: tuple(
            dict.fromkeys(
                (
                    *alternatives,
                    *stage.get(name, ()),
                    *((CURATOR_EQUIVALENTS[name],) if name in CURATOR_EQUIVALENTS else ()),
                    *(
                        (CURATOR_EQUIVALENTS["post-pass code finding role"],)
                        if name == "post-pass code finding" and source == "roles/curator.md"
                        else ()
                    ),
                )
            )
        )
        for name, alternatives in clauses.items()
    }


def clause_surface(root: Path, source: str, name: str, report_path: Path) -> str:
    """Only the code-lane-isolation facet moved to the named handoff step file."""
    if (
        source in {"roles/curator.md", "operations/curation.md"}
        and name == "memory leaves code untouched"
    ):
        instructions, text = delivered_step(
            root,
            ("curator", "curation"),
            "reference/curator-handoff-d301.md",
            "when you reach Curation workflow 7: Undergo the independent fact-check, not before; reuse at Curation workflow 8: Repair, then republish",
            report_path,
        )
        assert "Read [handoff procedures](" in instructions
        return text
    return instruction_surface(root, source)


def clause_mutations(
    root: Path, roles: Iterable[str]
) -> Iterator[tuple[str, str, dict[str, tuple[str, ...]], str, str]]:
    """Yield the same complete facet/deletion controls without growing the test module."""
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
    for source, declared_clauses in surfaces.items():
        clauses = surface_clauses(source, declared_clauses)
        for name, alternatives in clauses.items():
            with tempfile.TemporaryDirectory() as directory:
                reading = normalized(
                    clause_surface(root, source, name, Path(directory) / "report.md")
                )
            mutant = reading
            for clause in alternatives:
                mutant = mutant.replace(normalized(clause), "")
            yield source, name, clauses, reading, mutant


def assert_git_comparison_scope(root: Path) -> None:
    """Keep code and both memory trees in the exact delivered comparison, with deletion control."""
    with tempfile.TemporaryDirectory() as directory:
        _, handoff = delivered_step(
            root,
            ("curator", "curation"),
            "reference/curator-handoff-d301.md",
            "when you reach Curation workflow 7: Undergo the independent fact-check, not before; reuse at Curation workflow 8: Repair, then republish",
            Path(directory) / "report.md",
        )
    comparison = "The reviewer compares the leaf's base and candidate from Git (the code trees and the knowledge files of the two memory trees), so the curator records no separate comparison."
    assert normalized(comparison) in normalized(handoff)
    mutant = normalized(handoff).replace(normalized(comparison), "")
    assert mutant != normalized(handoff)
    assert normalized(comparison) not in mutant
