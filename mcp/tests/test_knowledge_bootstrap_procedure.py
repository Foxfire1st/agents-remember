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
* the **seats** the procedure names are admitted by the shipped opener — measured at the route, because
  the launch *compiler* accepts any declared role while the *opener* admits a session with no task
  document only for the taskless seat roles. The procedure's own text states the gate, and this module
  pins it by observation rather than by reading the policy constant;
* the instructions that **are** delivered name the procedure: to the seat the opener admits taskless,
  to the curator shape the compiler produces, and through the two served skills an ordinary setup
  follows.

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
    a seat-less session is admitted for the taskless      ``test_a_session_with_no_task_document...``,
      roles and refused for the rest, by status            which drives the dashboard's own open route
                                                           over the shipped real coordination world
    the seat the opener admits taskless receives the      ``test_the_admitted_taskless_seat...``,
      procedure and the state vocabulary                   which opens that seat and reads its
                                                           compiled instructions
    the curator SEAT is the one opened on a task          ``test_the_curator_seat_is_admitted...``,
      document                                             which opens ``role=curator`` with the leaf
                                                           document and reads the delivered mode
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
- **Whether a taskless CURATOR seat may be opened.** It may not, and the seat case asserts the refusal
  as the shipped product returns it; admitting that seat is a policy change this leaf does not make.
- **The wording of the procedure.** These cases assert that the named surfaces are published, reached,
  admitted and parseable. They do not read the prose for meaning, and a wrong sentence in a live
  document is the curator's and the verifier's subject rather than this module's.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from agents_remember.application.role_capsules.launch import compile_launch_capsule
from agents_remember.application.skill_resources.operation import (
    skill_catalog_list_tool,
    skill_catalog_read_tool,
)
from agents_remember.cli.__main__ import build_parser
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.observer import reset_ambient
from agents_remember.serving.app import create_app
from agents_remember.serving.launch_capsule import LaunchCapsuleRequest
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from fastapi.testclient import TestClient
from test_capsule_launch_wiring import LEAF_REF, _scratch_world, _write_codex_settings

pytestmark = pytest.mark.evidence_unit

REPOSITORY_ROOT = Path(__file__).parents[2]
CANONICAL_SKILLS = REPOSITORY_ROOT / "skills"
PROCEDURE_SKILL = "c-14-knowledge-bootstrap"
PROCEDURE_FILE = CANONICAL_SKILLS / PROCEDURE_SKILL / "SKILL.md"

#: The shipped umbrella CLI's own program name, as the procedure prints it.
PROGRAM = "agents-remember"

#: A printed invocation: the program name opens the line (optionally indented), so an inline mention
#: of a command inside a sentence is prose rather than a command line the document prints.
_INVOCATION = re.compile(rf"^(\s*){PROGRAM}\s+([a-z][a-z0-9-]*)")
_LONG_OPTION = re.compile(r"--[a-z][a-z0-9-]*")
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

    A command in this corpus wraps across continuation lines that begin with an option, so one
    invocation's span is its own line plus every following line that starts with one. The extraction
    is deliberately over-inclusive: an option read from a neighbouring sentence still has to parse, so
    over-reading makes the case stricter rather than weaker.
    """

    lines = text.splitlines()
    for index, line in enumerate(lines):
        found = _INVOCATION.match(line)
        if found is None:
            continue
        span = [line[len(found.group(1)) :]]
        for following in lines[index + 1 :]:
            if not _CONTINUATION.match(following):
                break
            span.append(following)
        filled = _PLACEHOLDER.sub(PLACEHOLDER_VALUE, "\n".join(span))
        yield filled.split()


def served_body(skill_name: str) -> str:
    """One skill's body as the shipped catalog serves it, refusing an unservable tree.

    The listing is checked first for unreadable skills because the read entry point refuses the whole
    tree while one exists: a case that read one skill while another was unservable would be grading a
    delivery the server does not make.
    """

    listing = skill_catalog_list_tool()
    assert listing.unreadable == [], (
        "the shipped skills tree carries unservable skill(s), so no body can be served: "
        + "; ".join(f"{item.skillPath}: {item.reason}" for item in listing.unreadable)
    )
    entries = [entry for entry in listing.skills if entry.name == skill_name]
    assert len(entries) == 1, (
        f"the shipped catalog publishes {len(entries)} entries named {skill_name!r}; a fresh session "
        "discovers what it may read through this list"
    )
    read = skill_catalog_read_tool(entries[0].uri)
    assert read.skillName == skill_name, read.skillName
    return read.content


def free_agent_instructions(role: str) -> tuple[str, str]:
    """The instructions the launch COMPILER produces for a seat shape, and its own summary.

    This is the compiler's answer, not an admission. Whether a session of that shape may be opened at
    all is the opener's decision, and `test_a_session_with_no_task_document_is_admitted_only_for_the_
    taskless_seat_roles` measures that separately — the two are different surfaces and this module keeps
    them apart on purpose.

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
            config,
            LaunchCapsuleRequest(role=role, workspace_root=workspace, harness="codex"),
        )
        assert not launched.is_refusal, launched.explain()
        delivery = launched.codex_delivery
        assert delivery is not None, launched.explain()
        return delivery.trusted_instructions, launched.explain()


@contextmanager
def launch_world() -> Iterator[tuple[Path, McpRuntimeConfig, object]]:
    """The shipped real coordination world, with the dashboard's own open route over it.

    The world is the one ``mcp/tests/test_capsule_launch_wiring.py`` owns and keeps real — a
    coordination root, a task document, its enclosure contract, the code worktree and the external
    memory root — built here through that module's own builder rather than reimplemented, so this case
    drives the same production route that module already exercises. The root is short because the
    opener mints a Unix-domain socket under it.
    """

    root = Path(tempfile.mkdtemp(prefix="l27-"))
    try:
        config, host, _repo = _scratch_world(root)
        _write_codex_settings(root)
        yield root, config, host
    finally:
        shutil.rmtree(root, ignore_errors=True)


def open_seat(
    root: Path, config: McpRuntimeConfig, host: object, session: str, body: dict[str, object]
) -> tuple[int, dict[str, object]]:
    """Open one seat through ``POST /api/terminal/{session}`` and return its status and body."""

    reset_ambient()
    collaborators = replace(
        serving_collaborators(config),
        terminal_host=cast(TerminalHost, host),
        terminal_catalog=TerminalCatalog(root / "logs" / "dashboard" / "terminal-sessions.json"),
    )
    app = create_app(config, cadence=ProjectionCadence(interval=100), collaborators=collaborators)
    with TestClient(app) as client:
        response = client.post(f"/api/terminal/{session}", json=body)
    return response.status_code, response.json()


def test_a_session_with_no_task_document_is_admitted_only_for_the_taskless_seat_roles() -> None:
    """The seat gate, by its observed status: the product decides which roles open without a document.

    The requirement's whole new-project entry depends on which seat may be opened before a task
    exists, so this is measured at the real opener rather than inferred from what the launch compiler
    accepts — the compiler takes any declared role, and the opener is the surface that admits a
    session. The admitted arms are the control: without them, a refusal here would prove nothing.
    """

    with launch_world() as (root, config, host):
        observed = {
            role: open_seat(
                root,
                config,
                host,
                f"l27-{role}",
                {"kind": "harness", "harness": "codex", "role": role},
            )
            for role in ("bootstrap", "chat", "worker", "curator")
        }

    for role in ("bootstrap", "chat"):
        status, body = observed[role]
        assert status == 200, (role, body)

    bootstrap_mode = cast("dict[str, object]", observed["bootstrap"][1]["instructionMode"])
    assert bootstrap_mode["mode"] == "capsule", bootstrap_mode
    assert bootstrap_mode["taskReference"] == "free-agent:bootstrap", bootstrap_mode

    for role in ("worker", "curator"):
        status, body = observed[role]
        assert status == 400, (role, body)
        assert body["status"] == "task-binding-required", (role, body)


def test_the_admitted_taskless_seat_receives_the_foundation_step() -> None:
    """The bootstrap seat opens with no document, and its own instructions reach the foundation.

    This is the one seat the product admits without a task document *and* instructs about the
    repository's knowledge foundation, so it is the carrier that makes a greenfield repository reach
    the step at all. The instructions below are the admitted session's own compiled capsule.
    """

    instructions, summary = free_agent_instructions("bootstrap")
    assert PROCEDURE_SKILL in instructions, (
        "the taskless seat the opener admits is not instructed about the knowledge-foundation "
        f"procedure, so ordinary setup cannot reach it ({summary})"
    )
    assert "not-recorded" in instructions, (
        "the bootstrap seat's instructions name no knowledge state, so a repository whose foundation "
        "is absent cannot be reported as anything but a ready setup"
    )

    with launch_world() as (root, config, host):
        status, body = open_seat(
            root,
            config,
            host,
            "l27-bootstrap-instructed",
            {"kind": "harness", "harness": "codex", "role": "bootstrap"},
        )
    assert status == 200, body
    mode = cast("dict[str, object]", body["instructionMode"])
    assert mode["taskReference"] == "free-agent:bootstrap", mode


def test_the_curator_seat_is_admitted_on_a_task_document() -> None:
    """The curator's own seat is opened WITH a named role scope, and it receives the procedure.

    A session for the curator without a document is refused (`task-binding-required`); with the task
    document it is admitted, which is the route the procedure's leaf entry states. Both arms are
    asserted against the same opener so the difference is the document, not the world.
    """

    with launch_world() as (root, config, host):
        status, body = open_seat(
            root,
            config,
            host,
            "l27-curator-bound",
            {
                "kind": "harness",
                "harness": "codex",
                "role": "curator",
                "taskDocumentRef": LEAF_REF.model_dump(),
            },
        )
        assert status == 200, body
    mode = cast("dict[str, object]", body["instructionMode"])
    assert mode["role"] == "curator", mode


def test_the_served_catalog_publishes_the_bootstrap_procedure() -> None:
    """A fresh session discovers the procedure, and the bytes it reads are the canonical tree's.

    The catalog is the delivery route: an agent does not read ``skills/`` directly, it lists what the
    server publishes and reads one entry back. Serving a stale or hand-edited copy would therefore be
    indistinguishable, from inside a session, from shipping the wrong instruction — so the served
    content is compared with the canonical file rather than trusted.
    """

    listing = skill_catalog_list_tool()
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


def test_the_compiled_curator_shape_names_the_procedure_it_would_receive() -> None:
    """The curator SHAPE's compiled instructions name the procedure — the compiler, not an admission.

    This case is deliberately named for the compiler, because that is all it measures: the launch
    compiler builds a free-agent capsule for any declared role, including a role the opener will not
    admit without a task document. Whether a session of this shape may be opened is
    ``test_a_session_with_no_task_document_is_admitted_only_for_the_taskless_seat_roles``'s subject,
    and the procedure's own text states the same limit.
    """

    instructions, summary = free_agent_instructions("curator")
    assert PROCEDURE_SKILL in instructions, (
        "the curator shape's compiled instructions never name the knowledge-bootstrap procedure, so "
        f"a curator seat opened on a task document would not be told about it ({summary})"
    )
    assert "knowledge-bootstrap" in instructions, (
        "the curator shape's instructions name no taskless bootstrap entry, leaving the only printed "
        "route one that requires an enclosure contract"
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
