"""The curator-led knowledge bootstrap: the delivered procedure, where it is reached from, and the
commands it tells a seat to run.

THE SUBJECT
-----------
A repository's knowledge foundation is *authored*, and before this leaf the only shipped instruction
that authored any was the leaf route (``agents-remember knowledge-ingest``), which requires an
enclosure contract. A repository whose first knowledge is being written has no leaf and no contract,
so first-time creation had no operational process at all. The delivery this module protects is made of
separately failable readings, and they are deliberately kept apart:

* the **procedure** exists and is served — a fresh session discovers it through the shipped skill
  catalog, and the bytes it reads are the canonical tree's bytes;
* the **commands the procedure prints** exist on the shipped command line, and every option it prints
  belongs to the subcommand it prints it with. A document that describes commands which do not exist is
  the failure the requirement names, and it is mechanical to catch;
* the **capsules** the launch compiler produces for the seats the procedure names carry it: the
  ``bootstrap`` free agent's, and the ``curator``'s both with no task document and on one. A capsule
  compiled with no task document is bound to the named absence ``free-agent:<role>``, never to a
  document;
* the instructions that **are** delivered name the procedure: to the bootstrap free agent, to the
  curator shape the compiler produces, and through the two served skills an ordinary setup follows.

WHAT DEFENDS WHAT
-----------------
    reading                                              defended by
    --------------------------------------------------   ----------------------------------------
    the shipped catalog publishes the procedure, and      ``test_the_served_catalog_publishes...``,
      the served bytes are the canonical tree's            which drives the catalog's own list and
      bytes                                                read entry points and compares what a
                                                           reader receives with ``skills/``
    every invocation the procedure prints parses          ``test_every_invocation_the_procedure...``,
      through the shipped command line                     which fills each printed placeholder and
                                                           runs the real parser over the exact
                                                           subcommand and options
    the bootstrap free agent's own instructions reach     ``test_the_bootstrap_free_agent...``,
      the procedure and the state vocabulary, bound to     which compiles that seat with no task
      the named absence of a task document                 document and reads its capsule
    the curator's capsule compiles on a task document     ``test_the_curator_capsule_compiles...``,
      AND with none, under different task references       which compiles ``role=curator`` both ways
                                                           and reads each capsule's binding
    the curator SHAPE's compiled instructions name it     ``test_the_compiled_curator_shape...``,
                                                           which compiles that shape through the launch
                                                           compiler and names only what it measured
    the ordinary setup and onboarding surfaces a seat      ``test_the_served_setup_and_onboarding...``,
      follows name the procedure and the knowledge          which reads the served bodies of the two
      states                                                skills those seats follow

WHAT THIS DOES NOT COVER (stated, not implied)
---------------------------------------------
- **Whether the curation itself is correct.** The authored records, family guarantees, memberships,
  interruption and publication outcomes are driven against a real coordination world by
  ``mcp/tests/test_knowledge_bootstrap.py``, which owns that subject; this module never re-drives it.
- **Whether a harness has already installed the tree.** Installation state is a measurement of the
  host, not a property of this checkout: ``scripts/sync-skills.py --check`` owns the nine packaged
  copies (byte for byte, in ``mcp/tests/test_sync_scripts.py``), and an installed harness root is owned
  by whoever runs the install.
- **Which role a launch admits without a task document.** The launch compiler produces a capsule for
  any declared role; whether a launch of that role may start at all is the role launch's decision and
  its own tests' subject. This module measures the compiler's answer and does not decide that policy.
- **The wording of the procedure.** These cases assert that the named surfaces are published, reached,
  compiled and parseable. They do not read the prose for meaning, and a wrong sentence in a live
  document is the curator's and the verifier's subject rather than this module's.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.launch import compile_launch_capsule
from agents_remember.application.skill_resources.catalog import (
    read_served_file,
    require_servable,
)
from agents_remember.application.skill_resources.operation import (
    skill_catalog_registry,
)
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.application.skill_resources.responses import (
    skill_catalog_list_response,
    skill_catalog_read_response,
)
from agents_remember.cli.__main__ import build_parser
from agents_remember.cli.role_instruction_details import compose_launch_instructions
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_capsules.types import compute_content_digest
from agents_remember.serving.capsule_delivery import CapsuleDelivery
from agents_remember.serving.launch_capsule import LaunchCapsuleRequest
from agents_remember_test_support.testing.capsule_budget_d695 import (
    current_budget_view,
    load_current_budget_view,
)
from agents_remember_test_support.testing.leaf_instruction_wording import (
    assert_action_pointer,
    normalized,
)
from test_capsule_launch_wiring import LEAF_REF, _scratch_world

pytestmark = pytest.mark.evidence_unit

REPOSITORY_ROOT = Path(__file__).parents[2]
CANONICAL_SKILLS = REPOSITORY_ROOT / "skills"
PROCEDURE_SKILL = "c-14-knowledge-bootstrap"
PROCEDURE_FILE = CANONICAL_SKILLS / PROCEDURE_SKILL / "SKILL.md"
FOUNDATION_SOURCE = "reference/curator/repository-foundation.md"
FOUNDATION_ACTION = "Before a repository's first or resumed knowledge foundation, read"
FOUNDATION_HEADING = "# Curator repository foundation"
FOUNDATION_WORKFLOW_CLAUSE = (
    "Before authoring the foundation, follow [the knowledge-bootstrap workflow]"
    "(skill://agents-remember/skills/c-14-knowledge-bootstrap/SKILL.md)."
)
FOUNDATION_PROCEDURE_CLAUSES = (
    "Repository foundation is regular task work.",
    "The user-started Architect opens the real foundation task under a master and starts the Curator on that task document.",
    "The Curator uses its actual enclosure contract.",
    "Receive the repository identifier, resolved context, scope, sources and current knowledge state. "
    "No incidental code delta is required.",
    FOUNDATION_WORKFLOW_CLAUSE,
    "Onboarding is optional input and never a start prerequisite.",
    "This foundation procedure authors no onboarding.",
)
FOUNDATION_AUTHORITY_CLAUSES = (
    "For setup\N{RIGHT SINGLE QUOTATION MARK}s state report and next step, follow [Name the next step]",
    "An instructed session cannot replace task admission.",
    "Report a missing task; never fabricate a task, worktree or enclosure.",
)


def delivered_foundation(
    capsule: CapsuleDelivery, root: Path, report_path: Path, clauses: tuple[str, ...]
) -> str:
    """Verify this compiled shape's declared, pointed-at, delivered foundation text.

    The budget declaration is a guard assertion here, not a claimed compiler refusal.
    """
    budgets = current_budget_view(load_current_budget_view(root / "capsule-budgets-current.json"))
    assert FOUNDATION_SOURCE in budgets["step_details"], "undeclared budget detail"
    manifest = json.loads((root / "composition-manifest.json").read_text())
    declared = manifest["references"]["curator/repository-foundation"]
    assert declared["source"] == FOUNDATION_SOURCE
    assert declared["step_detail"] is True
    assert "curator" in declared["roles"]
    assert declared["step"].strip()
    assert_action_pointer(capsule.trusted_instructions, FOUNDATION_SOURCE, FOUNDATION_ACTION)
    assert "]({{detail:" + FOUNDATION_SOURCE + "}})" in capsule.trusted_instructions
    references = [r for r in capsule.detail_references if r.source_path == FOUNDATION_SOURCE]
    assert len(references) == 1
    reference = references[0]
    assert reference.step == declared["step"]
    assert reference.content == (root / FOUNDATION_SOURCE).read_bytes()
    instructions, record = compose_launch_instructions(capsule, str(report_path), {})
    entries = [r for r in record["detailReferences"] if r["sourcePath"] == FOUNDATION_SOURCE]
    assert len(entries) == 1
    assert entries[0]["step"] == reference.step
    path = Path(entries[0]["path"])
    assert f"]({path.as_posix()})" in instructions
    assert "{{detail:" not in instructions

    # The emitted copy equals the source byte for byte except that each relative link is
    # rewritten to the absolute shipped file; every rewritten target names the same file
    # and anchor (A004 pinned plain byte equality before the foundation gained links).
    link = re.compile(r"\]\(([^)]+)\)")
    emitted_text, source_text = path.read_text(), reference.content.decode("utf-8")
    assert link.sub("](target)", emitted_text) == link.sub("](target)", source_text)
    with shipped_composition_corpus() as (origin, _manifest):
        owner = origin / FOUNDATION_SOURCE
        for emitted, written in zip(
            link.findall(emitted_text), link.findall(source_text), strict=True
        ):
            if written.startswith("skill://"):
                assert emitted == written
                continue
            emitted_path, _, emitted_anchor = emitted.partition("#")
            written_path, _, written_anchor = written.partition("#")
            assert emitted_anchor == written_anchor, (emitted, written)
            assert Path(emitted_path).resolve() == (owner.parent / written_path).resolve()
    detail = path.read_text()
    assert detail.splitlines()[0] == FOUNDATION_HEADING
    for clause in clauses:
        assert normalized(clause) in normalized(detail), clause
    return detail


def foundation_delivery_controls(
    capsule: CapsuleDelivery,
    root: Path,
    output: Path,
    clauses: tuple[str, ...],
    sentence: str,
) -> None:
    """All five corruptions fail the same delivery proof, without editing shipped sources.

    These are assertion-boundary controls on a captured capsule and copied declarations;
    they make no claim that the compiler consumes the budget registry.
    """
    for mutation in (
        "step_details",
        "heading",
        "sentence",
        "undeclared-pointer",
        "relocated-pointer",
    ):
        copied = output / mutation / "lifecycle"
        for relative in (
            "capsule-budgets-current.json",
            "composition-manifest.json",
            FOUNDATION_SOURCE,
        ):
            target = copied / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
        changed_capsule = capsule
        if mutation == "step_details":
            target = copied / "capsule-budgets-current.json"
            budgets = json.loads(target.read_text())
            del budgets["current_measurements"]["step_details"][FOUNDATION_SOURCE]
            target.write_text(json.dumps(budgets))
        elif mutation == "relocated-pointer":
            marker = "{{detail:" + FOUNDATION_SOURCE + "}}"
            assert capsule.trusted_instructions.count(marker) == 1
            changed = capsule.trusted_instructions.replace(marker, "", 1)
            changed += "\n\nAt an unrelated action, read [foundation](" + marker + ")."
            assert FOUNDATION_ACTION in changed
            assert changed.count(marker) == 1
            changed_capsule = replace(capsule, trusted_instructions=changed)
        elif mutation == "undeclared-pointer":
            changed = capsule.trusted_instructions.replace(
                "{{detail:" + FOUNDATION_SOURCE + "}}", "{{detail:reference/undeclared.md}}"
            )
            assert changed != capsule.trusted_instructions
            changed_capsule = replace(capsule, trusted_instructions=changed)
        else:
            target = copied / FOUNDATION_SOURCE
            original = target.read_text()
            changed = original.replace(
                FOUNDATION_HEADING if mutation == "heading" else sentence,
                "# Renamed foundation" if mutation == "heading" else "",
            )
            assert changed != original
            target.write_text(changed)
            content = target.read_bytes()
            changed_capsule = replace(
                capsule,
                detail_references=tuple(
                    replace(r, content=content, revision=compute_content_digest(content))
                    if r.source_path == FOUNDATION_SOURCE
                    else r
                    for r in capsule.detail_references
                ),
            )
        with pytest.raises(AssertionError):
            delivered_foundation(changed_capsule, copied, output / mutation / "report.md", clauses)


#: The shipped umbrella CLI's own program name, as the procedure prints it.
PROGRAM = "agents-remember"

#: Both launchers printed by the procedure. A launcher opens the line (optionally indented), so an
#: inline mention inside a sentence is prose rather than a command line the document prints.
_INVOCATION = re.compile(
    rf'^\s*(?:{PROGRAM}\s+|PYTHONPATH="\$AR_SERVING_PARENT"\s+'
    r'"\$AR_SERVING_PYTHON"\s+-P\s+-m\s+agents_remember\.cli\s+)'
)
_CONTINUATION = re.compile(r"^\s+--")
_PLACEHOLDER = re.compile(r"<[^>]*>")

#: What a printed placeholder is replaced with: it only has to satisfy the parser's arity, never a
#: real path, because this case grades the command line's SHAPE and runs no command.
PLACEHOLDER_VALUE = "placeholder"


def procedure_text() -> str:
    """The canonical procedure's bytes, as the shipped source tree holds them."""

    return PROCEDURE_FILE.read_text(encoding="utf-8")


def digest(text: str) -> str:
    """The content address the catalog publishes a served file under."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def body_of(text: str) -> str:
    """A skill's body: everything after its frontmatter block.

    The frontmatter is discovery metadata — a description a catalog lists — while the body is what a
    seat reads and follows. A routing case that accepted the description would pass on a document whose
    instructions had lost the step entirely, so the body is what the reachability cases read.
    """

    lines = text.splitlines()
    if lines and lines[0] == "---":
        closing = lines.index("---", 1)
        return "\n".join(lines[closing + 1 :])
    return text


def printed_invocations(text: str) -> Iterator[list[str]]:
    """Each ``agents-remember`` invocation ``text`` prints, as a placeholder-filled argv.

    Join explicit shell continuations before recognizing the launcher, then include option-only
    continuation lines. Normalize the serving-bound Python launcher to the parser's program name;
    every following token remains subject to the real parser. Placeholders are filled before shell
    tokenization because their descriptive text can contain spaces and apostrophes.
    """

    lines = re.sub(r"\\\r?\n", "", text).splitlines()
    for index, line in enumerate(lines):
        found = _INVOCATION.match(line)
        if found is None:
            continue
        span = [line[found.end() :]]
        for following in lines[index + 1 :]:
            if not _CONTINUATION.match(following):
                break
            span.append(following)
        filled = _PLACEHOLDER.sub(PLACEHOLDER_VALUE, "\n".join(span))
        yield [PROGRAM, *shlex.split(filled)]


@pytest.mark.parametrize(
    "launcher",
    [
        PROGRAM,
        'PYTHONPATH="$AR_SERVING_PARENT" "$AR_SERVING_PYTHON" -P -m agents_remember.cli \\\n',
    ],
)
def test_printed_invocations_preserve_arguments_and_parser_failures(launcher: str) -> None:
    command = (
        f"{launcher} knowledge-ingest --contract <this leaf's enclosure contract> \\\n"
        "    --list <hand-off list> --authorization-ref <ref> --commit --json"
    )
    invocations = list(
        printed_invocations(f"Prose mentions {PROGRAM} knowledge-ingest.\n{command}")
    )
    assert invocations == [
        [
            PROGRAM,
            "knowledge-ingest",
            "--contract",
            PLACEHOLDER_VALUE,
            "--list",
            PLACEHOLDER_VALUE,
            "--authorization-ref",
            PLACEHOLDER_VALUE,
            "--commit",
            "--json",
        ]
    ]
    parser = build_parser()
    parser.parse_args(invocations[0][1:])
    for invalid in (command + " --not-a-real-option", f"{launcher} not-a-real-subcommand"):
        [argv] = list(printed_invocations(invalid))
        with pytest.raises(SystemExit) as refused:
            parser.parse_args(argv[1:])
        assert refused.value.code == 2


def served_body(skill_name: str) -> str:
    """One skill's body as the shipped catalog serves it, refusing an unservable tree.

    The listing is checked first for unreadable skills because the read entry point refuses the whole
    tree while one exists: a case that read one skill while another was unservable would be grading a
    delivery the server does not make.
    """

    listing = skill_catalog_list_response(skill_catalog_registry())
    assert listing.unreadable == [], (
        "the shipped skills tree carries unservable skill(s), so no body can be served: "
        + "; ".join(f"{item.skillPath}: {item.reason}" for item in listing.unreadable)
    )
    entries = [entry for entry in listing.skills if entry.name == skill_name]
    assert len(entries) == 1, (
        f"the shipped catalog publishes {len(entries)} entries named {skill_name!r}; a fresh session "
        "discovers what it may read through this list"
    )
    read = _read_shipped_skill(entries[0].uri)
    assert read.skillName == skill_name, read.skillName
    return read.content


def _read_shipped_skill(uri: str):
    """One shipped skill file through the staying registry path (no removed tool)."""

    catalog = skill_catalog_registry()
    require_servable(catalog, action="reading a skill file")
    served = read_served_file(catalog, uri)
    root = served.entry.root
    return skill_catalog_read_response(served, skill_revision="" if root is None else root.revision)


def free_agent_capsule(role: str) -> CapsuleDelivery:
    """The capsule the launch COMPILER produces for one role with no task document.

    This is the compiler's answer, not an admission: it takes any declared role, and whether a launch
    of that role may start is decided elsewhere.

    The world holds no task artifact of any kind: no settings beyond the two roots, no task document,
    no enclosure, no worktree, no memory repository. That absence is the point — the compiler's
    free-agent path takes any declared role and must produce its instructions without one.
    """

    with tempfile.TemporaryDirectory(prefix="l27-instruction-") as raw:
        workspace = Path(raw)
        config = McpRuntimeConfig(
            config_path=workspace / "settings.json",
            coordination_root=workspace,
            workspace_root=workspace,
            transcript_root=workspace / "logs",
            repositories={},
        )
        launched = compile_launch_capsule(
            config, LaunchCapsuleRequest(role=role, workspace_root=workspace)
        )
        assert not launched.is_refusal, launched.explain()
        delivery = launched.delivery
        assert delivery is not None, launched.explain()
        return delivery


def test_the_bootstrap_free_agent_receives_the_foundation_step(tmp_path: Path) -> None:
    """The bootstrap seat compiles with no document, and its own instructions reach the foundation.

    The bootstrap seat is the read-and-report carrier for a repository whose foundation is absent: its
    capsule is bound to the named absence of a task document. Its default compiled instructions
    name the procedure and bind the state-reporting action to the exact emitted state detail.
    Compilation proves delivery, not authoring admission; the separate Curator case owns its
    compiler shape.
    """

    capsule = free_agent_capsule("bootstrap")
    instructions = capsule.trusted_instructions
    source = "reference/bootstrap/foundation-state.md"
    action = "Before reading and reporting the knowledge foundation's state, read"
    root = CANONICAL_SKILLS / "l-01-agent-lifecycles"
    declared = json.loads((root / "composition-manifest.json").read_text())["references"][
        "bootstrap/foundation-state"
    ]
    assert declared["source"] == source
    assert declared["step_detail"] is True
    assert "bootstrap" in declared["roles"]
    assert declared["step"].strip()
    assert_action_pointer(instructions, source, action)
    [reference] = [r for r in capsule.detail_references if r.source_path == source]
    assert reference.step == declared["step"]
    assert reference.content == (root / source).read_bytes()
    assert reference.revision == compute_content_digest(reference.content)
    emitted, record = compose_launch_instructions(capsule, str(tmp_path / "report.md"), {})
    [entry] = [r for r in record["detailReferences"] if r["sourcePath"] == source]
    path = Path(entry["path"])
    assert f"]({path.as_posix()}#read-the-state)" in emitted
    assert "{{detail:" not in emitted
    assert entry["step"] == reference.step
    assert entry["sizeBytes"] == len(path.read_bytes())
    assert entry["revision"] == compute_content_digest(path.read_bytes())
    state_detail = normalized(path.read_text())
    assert PROCEDURE_SKILL in state_detail, (
        "the default-emitted Bootstrap foundation detail must reach the registered procedure "
        f"({capsule.binding.as_report()})"
    )
    assert (
        "Author or refresh no onboarding content. Do not edit a memory file to make a check pass. "
        "Author no knowledge records, run no knowledge writer, invent no invariants and populate "
        "no database to claim readiness. Do not organise their production." in instructions
    )
    for clause in (
        "Read memory_init's knowledge block and report its state:",
        "not-recorded",
        "recorded, with the exact identity",
        "unusable",
        "a refused admission, with its own next action.",
        "Before acting on the reported state, apply",
        "for its setup-only authority and authoring prohibitions.",
        "Name one next step in both the conversation and report: start the Architect.",
        "do not claim that Bootstrap created or completed the foundation.",
    ):
        assert normalized(clause) in state_detail, clause
    assert capsule.binding.task_path == "free-agent:bootstrap", capsule.binding.as_report()


def test_the_curator_capsule_compiles_on_a_task_document_and_taskless(tmp_path: Path) -> None:
    """The curator's capsule compiles WITH a task document, and it also compiles with none.

    On the task document the capsule is bound to that document, the dispatched route the procedure's
    leaf entry states. Without one it is bound to the named absence ``free-agent:curator``: the
    capsule of an instructed session that authors a first knowledge foundation before any task
    exists (no curator seat is started without a task document). Both carry the curator role, and
    the two are separately attributable: they must not report the same task reference.
    """

    config, _repo = _scratch_world(tmp_path)
    launched = compile_launch_capsule(
        config,
        LaunchCapsuleRequest(role="curator", workspace_root=tmp_path, task_document_ref=LEAF_REF),
    )
    assert launched.delivery is not None, launched.explain()
    bound = launched.delivery.binding
    assert bound.role == "curator", bound.as_report()

    taskless = free_agent_capsule("curator").binding
    assert taskless.role == "curator", taskless.as_report()
    assert taskless.task_path == "free-agent:curator", taskless.as_report()
    assert bound.task_path != taskless.task_path, (
        "a document-bound curator and a taskless one must not report the same task reference"
    )


def test_the_served_catalog_publishes_the_bootstrap_procedure() -> None:
    """A fresh session discovers the procedure, and the bytes it reads are the canonical tree's.

    The catalog is the delivery route: an agent does not read ``skills/`` directly, it lists what the
    server publishes and reads one entry back. Serving a stale or hand-edited copy would therefore be
    indistinguishable, from inside a session, from shipping the wrong instruction — so the served
    content is compared with the canonical file rather than trusted.
    """

    listing = skill_catalog_list_response(skill_catalog_registry())
    entries = [entry for entry in listing.skills if entry.name == PROCEDURE_SKILL]
    assert entries, (
        f"the shipped catalog publishes no {PROCEDURE_SKILL!r} skill, so no fresh session can "
        f"discover the procedure; published: {sorted(entry.name for entry in listing.skills)}"
    )
    assert entries[0].uri.endswith(f"/{PROCEDURE_SKILL}/SKILL.md"), entries[0].uri
    assert "SKILL.md" in entries[0].files, entries[0].files

    canonical = procedure_text()
    assert served_body(PROCEDURE_SKILL) == canonical, (
        "the served bytes differ from the canonical tree's; run python3 scripts/sync-skills.py"
    )
    assert entries[0].revision == f"sha256:{digest(canonical)}", entries[0].revision


def test_every_invocation_the_procedure_prints_resolves_in_the_shipped_parser() -> None:
    """The requirement's own failure: a document that describes commands which do not exist.

    Each printed invocation is filled in and run through the real parser, so both halves are graded at
    once — the subcommand must be declared, and every option printed with it must belong to that
    subcommand. An option that moved to another subcommand is a failure here, which is the shape a
    reorganisation produces. Nothing is executed: the parser is the whole subject.
    """

    parser = build_parser()
    invocations = list(printed_invocations(procedure_text()))
    assert len(invocations) >= 3, (
        f"only {len(invocations)} invocation(s) were extracted from the procedure; the extraction "
        "stopped covering the document it grades"
    )
    ingest = [argv for argv in invocations if argv[1] == "knowledge-ingest"]
    assert any("--commit" not in argv for argv in ingest), "the planning invocation was not read"
    assert any("--commit" in argv for argv in ingest), "the commit invocation was not read"
    assert any(argv[1] == "knowledge-validate" for argv in invocations), (
        "the validation invocation was not read"
    )

    problems: list[str] = []
    for argv in invocations:
        assert argv[0] == PROGRAM, argv
        try:
            parser.parse_args(argv[1:])
        except SystemExit as exit_status:
            problems.append(f"`{' '.join(argv)}` does not parse ({exit_status.code})")
    assert problems == [], (
        "the procedure prints commands the shipped command line does not accept:\n  "
        + "\n  ".join(problems)
    )


def test_the_compiled_curator_shape_names_the_procedure_it_would_receive(tmp_path: Path) -> None:
    """The curator SHAPE's compiled instructions name the procedure — the compiler, not an admission.

    This case is deliberately named for the compiler, because that is all it measures: the launch
    compiler builds a synthetic free-agent shape for any declared role. This measures its actual
    pointer and delivered detail; it admits no taskless Curator or instructed foundation session.
    Foundation work requires the real task admission stated in that detail.
    """

    capsule = free_agent_capsule("curator")
    detail = delivered_foundation(
        capsule,
        CANONICAL_SKILLS / "l-01-agent-lifecycles",
        tmp_path / "report.md",
        FOUNDATION_PROCEDURE_CLAUSES,
    )
    assert PROCEDURE_SKILL in detail
    assert "knowledge-bootstrap" in detail
    foundation_delivery_controls(
        capsule,
        CANONICAL_SKILLS / "l-01-agent-lifecycles",
        tmp_path / "controls",
        FOUNDATION_PROCEDURE_CLAUSES,
        FOUNDATION_WORKFLOW_CLAUSE,
    )


def test_the_served_setup_and_onboarding_surfaces_reach_the_procedure() -> None:
    """The two skills an ordinary setup follows name the procedure and report the knowledge state.

    This is the reachability half of the delivery, read through the same served route as the procedure
    itself and on the bodies rather than on the frontmatter descriptions: the setup skill a first run
    follows, and the onboarding skill whose handoff follows it. Both are asserted on the bytes a
    session actually loads and follows, not on the files beside them.
    """

    setup = body_of(served_body("c-13-install-and-onboard"))
    assert PROCEDURE_SKILL in setup, (
        "the served setup skill's instructions never name the knowledge-bootstrap procedure, so "
        "ordinary setup cannot reach the repository's knowledge foundation"
    )
    assert "not-recorded" in setup, (
        "the served setup skill's instructions name no knowledge state, so it cannot report a "
        "repository whose foundation is absent without reporting it ready"
    )

    onboarding = body_of(served_body("c-03-repo-bootstrap"))
    assert PROCEDURE_SKILL in onboarding, (
        "the served onboarding skill's handoff never names the knowledge-foundation procedure, so "
        "onboarding output reads as the repository's whole foundation"
    )
