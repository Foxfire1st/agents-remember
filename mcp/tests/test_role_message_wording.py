"""MIK-R117 rule 7: the role texts and handover templates on messages as files.

Every role text and every handover template says: a message says what is
asked or what is handed over, and names the files. Evidence, verdicts, lists
and logs are files. They name the folder and the inputs ``body`` and
``attach``. They name no number; they say "the limit of the settings".

The oracle has three parts (L117-C1-F03): the normative prohibition is
tested with negative variants that must hit and allowed controls that must
not; the scan runs over the role sources, every generated copy, and the
actual compiled capsule prompt and handover for every handover role; the
surface census comes from the product inventory (``scripts/sync-skills.py``
targets), and a dropped surface fails the census assertion rather than
passing silent.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from agents_remember.errors import CapsuleManifestError
from agents_remember.models.role_capsules.types import CapsuleOperation, CapsuleRole
from agents_remember_test_support.testing.capsule_wording import (
    MEMORY_HANDOVER_ACTION,
    clause_surface,
    instruction_owner,
)
from agents_remember_test_support.testing.curator_contracts import compile_matrix
from agents_remember_test_support.testing.leaf_instruction_wording import (
    LEAF_CLAUSES,
    MESSAGE_HANDOVER_ACTIONS,
    MESSAGE_HANDOVER_SOURCE,
    assert_emitted_detail_links,
    autonomy_clauses,
    delivered_step,
    missing_clauses,
    normalized,
)
from test_role_instruction_wording import PACKAGED
from test_role_instruction_wording_handover import HandoverTextWordingTests

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = "l-01-agent-lifecycles"


def _sync_targets() -> Any:
    """The product inventory of skill surfaces (canonical plus copies)."""
    location = REPOSITORY_ROOT / "scripts" / "sync-skills.py"
    spec = importlib.util.spec_from_file_location("sync_skills_inventory", location)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SYNC = _sync_targets()


def surfaces() -> list[Path]:
    """Every skill surface the product ships, canonical first."""
    roots = [Path(_SYNC.CANONICAL_SKILLS)]
    roots.extend(Path(target.path) for target in _SYNC.skill_targets())
    return [root / LIFECYCLE for root in roots]


#: The two surfaces the inventory names today (MIK-R196 rule 7): the canonical tree and the
#: packaged serving copy, which is the only generated copy the product ships. A dropped or
#: added surface fails here, visibly, instead of passing silent on a smaller census.
EXPECTED_SURFACE_COUNT = 2

ROLE_FILES = tuple(
    f"roles/{name}.md"
    for name in (
        "architect",
        "orchestrator",
        "manager",
        "worker",
        "reviewer",
        "curator",
        "investigator",
        "bootstrap",
    )
)

OPERATION_FILES = (
    "operations/coordination.md",
    "operations/implementation.md",
    "operations/review.md",
    "operations/curation.md",
)

HANDOVER_TEMPLATE_FILES = (
    "templates/manager-brief.md",
    "templates/curator-brief.md",
    "templates/verdict.md",
    "templates/turn-report.md",
    "templates/curator-handoff-list.md",
    "templates/master-handover-packet.md",
)

OPERATION_RECIPIENTS = {
    "operations/coordination.md": ("architect", "orchestrator", "manager"),
    "operations/implementation.md": ("worker",),
    "operations/review.md": ("reviewer", "manager", "orchestrator", "architect"),
    "operations/curation.md": ("curator", "manager"),
}


#: The roles whose handover the launch code compiles (seven public capsules).
COMPILED_ROLES = (
    "architect",
    "orchestrator",
    "manager",
    "worker",
    "reviewer",
    "curator",
    "investigator",
)

#: What every role text and handover template must say about messages.
REQUIRED_MARKERS = (
    "limit of the settings",
    "`body`",
    "`attach`",
    "names the files",
    "never pasted into a message",
)

COMMON_MARKER_EQUIVALENTS = {
    "limit of the settings": ("settings' limit", "limited by the settings"),
    "names the files": ("name the files",),
    "never pasted into a message": ("never paste them into a message",),
}

REVIEWER_MARKER_EQUIVALENTS = {
    "limit of the settings": "Text must stay within the settings limit.",
    "names the files": "Messages state requests or handovers and name files;",
    "never pasted into a message": "evidence, verdicts, lists and logs stay in files, never pasted.",
}


CURATOR_MARKER_EQUIVALENTS = {
    "limit of the settings": "Text never exceeds the settings limit.",
    "names the files": "Messages state requests or handovers and name files;",
    "never pasted into a message": "never paste evidence, verdicts, lists or logs.",
}

RETIRED_REVIEWER_OUTPUT_CLAUSES = (
    "hand it directly to the leaf's Worker as `../operations/review.md` §Outputs states",
    "Write/record the verdict and hand it directly to the leaf\u2019s Worker per `../operations/review.md` §Outputs.",
)

CURATION_HANDOFF_FACETS = (
    ("operations/review.md", "memory extra approval"),
    ("roles/curator.md", "memory to reviewer"),
    ("roles/curator.md", "identity and whole stretch"),
    ("roles/curator.md", "memory lane and count"),
    ("roles/curator.md", "memory leaves code untouched"),
    ("roles/curator.md", "memory ordinary limit"),
)


def reviewer_outputs_duplicates(source: str) -> list[str]:
    """Keep retired duplicate verdict directions out of the artifact section."""
    match = re.search(r"(?ms)^## Write the review artifacts\n(.*?)(?=^## |\Z)", source)
    assert match is not None
    reading = normalized(match[1])
    return [clause for clause in RETIRED_REVIEWER_OUTPUT_CLAUSES if normalized(clause) in reading]


def require_curation_handoff_targets(root: Path) -> None:
    """Follow the operation and delivered-detail routes for all six moved facets."""
    operation = (root / "operations/curation.md").read_text()
    assert re.search(r"(?m)^## Handoff / exit\s*$", operation)
    reviewer = (root / "operations/review.md").read_text()
    for clause in (
        "Memory review uses separate sealed reports, finding identifiers and its own pass count.",
        "The Reviewer records its own lane with `begin_review` and `record_review`, using `reviewKind=memory`.",
        "Code state stays unchanged.",
        "Its ordinary pass limit is the same as code review's.",
        "A pass beyond the settings limit needs the Architect's or developer's instruction recorded by the Manager.",
    ):
        assert normalized(clause) in normalized(reviewer), clause
    curator = (root / "roles/curator.md").read_text()
    assert (
        "[Obtain the independent fact-check](../operations/curation.md#obtain-the-independent-fact-check)"
        in curator
    )
    assert "The curation operation owns the workflow sequence." in curator
    assert "{{detail:reference/curator/memory-handover.md}}" in curator
    with tempfile.TemporaryDirectory() as directory:
        _, detail = delivered_step(
            root,
            ("curator", "curation"),
            "reference/curator/memory-handover.md",
            MEMORY_HANDOVER_ACTION,
            Path(directory) / "handoff.md",
        )
        assert re.search(r"(?m)^## Send the whole requested review or repair\s*$", detail)
        for source, name in CURATION_HANDOFF_FACETS:
            reading = clause_surface(
                root, source, name, Path(directory) / source / name / "report.md"
            )
            assert missing_clauses(reading, {name: LEAF_CLAUSES[source][name]}) == []


def message_step(root: Path, role: str, operation: str = "orientation") -> tuple[str, str]:
    """Read the payload rules only through their actual declared delivery."""
    owner = instruction_owner(root, role)
    with tempfile.TemporaryDirectory() as directory:
        instructions, text = delivered_step(
            root,
            (cast(CapsuleRole, owner), cast(CapsuleOperation, operation)),
            MESSAGE_HANDOVER_SOURCE,
            MESSAGE_HANDOVER_ACTIONS[owner],
            Path(directory) / "report.md",
        )
    return instructions, text


def missing_markers(relative: str, text: str) -> list[str]:
    """Check each file-message facet, preserving role-scoped legacy controls."""
    role_equivalents = {
        "roles/reviewer.md": REVIEWER_MARKER_EQUIVALENTS,
        "roles/curator.md": CURATOR_MARKER_EQUIVALENTS,
    }.get(relative, {})
    clauses = {}
    for marker in REQUIRED_MARKERS:
        alternatives = (marker, *COMMON_MARKER_EQUIVALENTS.get(marker, ()))
        if marker in role_equivalents:
            alternatives += (role_equivalents[marker],)
        clauses[marker] = alternatives
    return missing_clauses(text, clauses)


def template_message_text(root: Path, relative: str) -> str:
    """Resolve the two current template actions to their exact authoritative owner."""
    source = (root / relative).read_text()
    sender_action = {
        "templates/manager-brief.md": "Before composing a message, read",
        "templates/curator-brief.md": "Before composing a message, read",
        "templates/verdict.md": "Before composing a message, read",
        "templates/turn-report.md": "Before composing a message, read",
        "templates/curator-handoff-list.md": "Before sending a message or handover, read",
        "templates/master-handover-packet.md": "Before sending the packet, read",
    }[relative]
    owner = root / MESSAGE_HANDOVER_SOURCE
    for action, heading, fragment in (
        (
            sender_action,
            "Send a message that names the files",
            "send-a-message-that-names-the-files",
        ),
        (
            "Before handling a full-store refusal, read",
            "Handle a full-store refusal",
            "handle-a-full-store-refusal",
        ),
    ):
        target = "../reference/message-handover.md#" + fragment
        paragraphs = [
            part for part in source.split("\n\n") if action in part and f"]({target})" in part
        ]
        assert len(paragraphs) == 1, (relative, action, target)
        assert paragraphs[0].index(action) < paragraphs[0].index(f"]({target})")
        assert ((root / relative).parent / target.split("#", 1)[0]).resolve() == owner.resolve()
        text = owner.read_text()
        assert re.search(r"(?m)^## " + re.escape(heading) + r"$", text), (relative, heading)
    assert "#handle-held-delivery" not in source
    return source + "\n" + owner.read_text()


HELD_DELIVERY_CLAUSES = (
    "A held receipt means queued eventual delivery, possibly many turns later.",
    "Never resend it.",
    "Count a handover as taken only when the recipient's reply, report or intake names the exact object and shows the requested work or its start.",
    "Open every supplied recipient artifact before you call one absent.",
    "Withdraw a superseded held message with `withdraw_held_message_id`.",
    "A receiver does not poll.",
)
HELD_DELIVERY_PREFIX = "Architect, Orchestrator, Manager, Worker, Curator, Reviewer, Investigator: "


def _assert_held_shared_line(text: str) -> None:
    assert text.count(HELD_DELIVERY_PREFIX + " ".join(HELD_DELIVERY_CLAUSES)) == 1


def assert_held_shared_delivery(root: Path) -> None:
    """Verify every messaging role's actual shared compiler stream without a detail read."""
    source = (root / "core/authority.md").read_text()
    assert " ".join(HELD_DELIVERY_CLAUSES) in source
    seen = set()
    for delivery in compile_matrix(root):
        if delivery.role not in COMPILED_ROLES:
            assert delivery.role == "bootstrap"
            continue
        shared = delivery.shared_block
        assert shared is not None
        assert shared == (root / "core/every-role.md").read_text()
        _assert_held_shared_line(shared)
        assert "#handle-held-delivery" not in delivery.result.render_instructions()
        for clause in HELD_DELIVERY_CLAUSES:
            changed = shared.replace(clause, "", 1)
            assert changed != shared
            with unittest.TestCase().assertRaises(AssertionError):
                _assert_held_shared_line(changed)
        with unittest.TestCase().assertRaises(AssertionError):
            _assert_held_shared_line(
                shared + "\n" + HELD_DELIVERY_PREFIX + " ".join(HELD_DELIVERY_CLAUSES)
            )
        seen.add(delivery.role)
    assert seen == set(COMPILED_ROLES)


#: A text that tells the sender to put file-grade content into the message
#: text. Addressing a message by agent id stays legal (the preservation
#: boundary keeps the address by id or by role), as do naming a file's path,
#: attaching a file, and refusing a pasted brief.
FORBIDDEN_PATTERNS = (
    # put/paste a verdict, report, evidence, findings, list or log into the message
    re.compile(
        r"\bput\b.{0,120}\b(verdict|report|evidence|findings|logs?|lists?)\b"
        r".{0,120}\b(in\b.{0,20}\b\w*message|into\b.{0,120}\bmessage)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bpaste\b.{0,120}\b(verdict|report|evidence|findings|logs?|lists?)\b"
        r".{0,120}\b(in\b.{0,20}\b\w*message|into\b)",
        re.IGNORECASE,
    ),
    # send/include the text of file-grade content in the message
    re.compile(
        r"\bsend\b.{0,80}\b(verdict|report|evidence|findings)\b.{0,40}\btext\b"
        r".{0,40}\bin\b.{0,40}\b\w*message",
        re.IGNORECASE,
    ),
    re.compile(
        r"\binclude\b.{0,80}\b(verdict|report|evidence|findings)\b.{0,40}\bin\b"
        r".{0,30}\b\w*message",
        re.IGNORECASE,
    ),
)

#: The message limit is the settings', never a number in a role text.
FORBIDDEN_NUMBERS = (
    re.compile(r"1,?000 characters per message"),
    re.compile(r"message limit of \d"),
    re.compile(r"\b(1000|1,000)\b.{0,40}\bcharacters\b"),
    re.compile(r"\bmessage\b.{0,40}\blimit of \d+"),
    re.compile(r"\blimit of \d+.{0,20}\bcharacters\b"),
)

#: Directive variants the oracle must catch (negative controls). The first three
#: are the sealed round-1 counterexamples, locked verbatim (L117-C1-F03).
MUST_CATCH = (
    "Send the full verdict text in the role_message text parameter.",
    "Paste the complete evidence in the message.",
    "The message has a limit of 600 characters.",
    "Put the full report into the message.",
    "Paste the evidence into the message for the reviewer.",
    "Send the verdict text in the message.",
    "Include the findings in your message.",
    "The message limit is 1000 characters.",
    "Keep every text under 1000 characters per message.",
)

#: Legal lines the oracle must not catch (positive controls).
MUST_KEEP = (
    "addressed to its agent ID",
    "hand it directly to the leaf's Worker as `../operations/review.md` §Outputs states",
    "The verdict travels as a file: the handover message names its path, never its pasted text.",
    "Messages wake; files carry.",
    "Send that actual answer or ruling to the requesting Manager or Orchestrator with `role_message`",
    "A text holds at most the limit of the settings.",
    "Attach the verdict file with `attach` and name its path.",
)


def scan_lines(label: str, lines: list[str]) -> list[str]:
    """Forbidden hits in lines."""
    forbidden: list[str] = []
    for lineno, line in enumerate(lines, start=1):
        for pattern in (*FORBIDDEN_PATTERNS, *FORBIDDEN_NUMBERS):
            if pattern.search(line):
                forbidden.append(f"{label}:{lineno}: {line.strip()[:160]}")
    return forbidden


def scan(roots: list[Path], files: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Missing markers and forbidden places, each as ``surface/relative: detail``."""
    missing: list[str] = []
    forbidden: list[str] = []
    for root in roots:
        label = root.relative_to(REPOSITORY_ROOT).as_posix()
        for relative in files:
            if relative in OPERATION_RECIPIENTS:
                operation = Path(relative).stem
                for role in OPERATION_RECIPIENTS[relative]:
                    text, detail = message_step(root, role, operation)
                    short = f"{label}/{relative}/{role}"
                    for marker in missing_markers(f"roles/{role}.md", text + "\n" + detail):
                        missing.append(f"{short}: missing {marker!r}")
                    forbidden.extend(scan_lines(short, text.splitlines()))
                    forbidden.extend(scan_lines(short + "/message-step", detail.splitlines()))
                continue
            path = root / relative
            text = path.read_text()
            short = f"{label}/{relative}"
            if relative == "roles/bootstrap.md":
                for clause in (
                    "with no task document, brief or parent.",
                    "Start no role. Bootstrap messages no one.",
                    "in your own chat",
                ):
                    if normalized(clause) not in normalized(text):
                        missing.append(f"{short}: missing {clause!r}")
                forbidden.extend(scan_lines(short, text.splitlines()))
                continue
            detail = (
                message_step(root, Path(relative).stem)[1]
                if relative in ROLE_FILES
                else template_message_text(root, relative)
            )
            for marker in missing_markers(relative, text + "\n" + detail):
                missing.append(f"{short}: missing {marker!r}")
            forbidden.extend(scan_lines(short, text.splitlines()))
            forbidden.extend(scan_lines(short + "/message-step", detail.splitlines()))
    return missing, forbidden


def compiled_texts() -> dict[str, str]:
    """The compiled capsule prompt and handover of every handover role."""
    case = HandoverTextWordingTests("test_developer_question_channel_matches_role_and_parent")
    case.setUp()
    try:
        texts: dict[str, str] = {}
        for role in COMPILED_ROLES:
            prompt, handover = case.compiled(role, None)
            texts[f"compiled/{role}/prompt"] = prompt
            texts[f"compiled/{role}/handover"] = json.dumps(handover, sort_keys=True, default=str)
        return texts
    finally:
        case.doCleanups()


class RoleMessageWordingTests(unittest.TestCase):
    def test_the_surface_census_matches_the_product_inventory(self) -> None:
        roots = surfaces()
        self.assertEqual(
            len(roots),
            EXPECTED_SURFACE_COUNT,
            "skill surfaces changed: update the census, never scan fewer.\n"
            + "\n".join(root.relative_to(REPOSITORY_ROOT).as_posix() for root in roots),
        )
        for root in roots:
            self.assertTrue(root.is_dir(), f"inventory surface is not on disk: {root}")

    def test_the_oracle_catches_directives_and_keeps_legal_lines(self) -> None:
        for line in MUST_CATCH:
            hits = [
                pattern
                for pattern in (*FORBIDDEN_PATTERNS, *FORBIDDEN_NUMBERS)
                if pattern.search(line)
            ]
            self.assertTrue(hits, f"oracle misses a forbidden directive: {line!r}")
        for line in MUST_KEEP:
            hits = [
                pattern
                for pattern in (*FORBIDDEN_PATTERNS, *FORBIDDEN_NUMBERS)
                if pattern.search(line)
            ]
            self.assertEqual(hits, [], f"oracle bans a legal line: {line!r}")

    def test_every_role_text_states_messages_as_files(self) -> None:
        missing, forbidden = scan(surfaces(), ROLE_FILES)
        self.assertEqual(missing, [])
        self.assertEqual(
            forbidden,
            [],
            "every place that told a sender to paste:\n" + "\n".join(forbidden),
        )

    def test_every_handover_template_states_messages_as_files(self) -> None:
        for root in surfaces():
            assert_held_shared_delivery(root)
        missing, forbidden = scan(surfaces(), HANDOVER_TEMPLATE_FILES)
        self.assertEqual(missing, [])
        self.assertEqual(
            forbidden,
            [],
            "every place that told a sender to paste:\n" + "\n".join(forbidden),
        )

        for relative in HANDOVER_TEMPLATE_FILES:
            with self.subTest(template=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / LIFECYCLE
                shutil.copytree(surfaces()[0], root)
                path = root / relative
                text = path.read_text()
                target = "/reference/message-handover.md"
                mutant = text.replace(target, "/reference/missing.md")
                self.assertNotEqual(mutant, text)
                path.write_text(mutant)
                with self.assertRaises(AssertionError):
                    template_message_text(root, relative)
        for relative, before, after, consumer in (
            (
                "templates/curator-handoff-list.md",
                "#send-a-message-that-names-the-files)",
                "#missing-sender-rules)",
                "templates/curator-handoff-list.md",
            ),
            (
                MESSAGE_HANDOVER_SOURCE,
                "## Send a message that names the files\n",
                "## Missing sender rules\n",
                "templates/manager-brief.md",
            ),
            (
                "templates/curator-handoff-list.md",
                "#handle-a-full-store-refusal)",
                "#missing-full-store-rules)",
                "templates/curator-handoff-list.md",
            ),
            (
                MESSAGE_HANDOVER_SOURCE,
                "## Handle a full-store refusal\n",
                "## Missing full-store rules\n",
                "templates/manager-brief.md",
            ),
        ):
            with self.subTest(target=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / LIFECYCLE
                shutil.copytree(surfaces()[0], root)
                path = root / relative
                text = path.read_text()
                mutant = text.replace(before, after)
                self.assertNotEqual(mutant, text)
                path.write_text(mutant)
                with self.assertRaises(AssertionError):
                    template_message_text(root, consumer)

    def test_the_messaging_operations_state_messages_as_files(self) -> None:
        missing, forbidden = scan(surfaces(), OPERATION_FILES)
        self.assertEqual(missing, [])
        self.assertEqual(
            forbidden,
            [],
            "every place that told a sender to paste:\n" + "\n".join(forbidden),
        )

    def test_compiled_capsules_state_messages_as_files(self) -> None:
        forbidden: list[str] = []
        missing: list[str] = []
        for label, text in compiled_texts().items():
            if label.endswith("/prompt"):
                role = label.split("/")[1]
                _, detail = message_step(PACKAGED / LIFECYCLE, role)
                forbidden.extend(scan_lines(f"compiled/{role}/message-step", detail.splitlines()))
                for marker in missing_markers(f"roles/{role}.md", text + "\n" + detail):
                    missing.append(f"{label}: missing {marker!r}")
            forbidden.extend(scan_lines(label, text.splitlines()))
        self.assertEqual(missing, [])
        self.assertEqual(
            forbidden,
            [],
            "a compiled capsule told a sender to paste:\n" + "\n".join(forbidden),
        )

    def test_report_returns_use_role_task_addressing_on_every_surface(self) -> None:
        """Report returns retain role/task addressing and the instructed recipient exception."""
        manager_clauses = {
            "general report address": (
                "When another agent starts you, report to that agent's role with its task references, "
                "unless the Architect changes your reporting recipient as described below.",
            ),
            "report address": (
                "Address that final message to the parent's role with its task references.",
            ),
            "recipient change": (
                "If the Architect that started you names an Orchestrator's agent identifier in a message "
                "when a second master starts, record the instruction and recipient identifier in your "
                "durable report. Send developer-needed matters to that Orchestrator from then on. "
                "This changes your reporting recipient, not your fixed launch parent. "
                "No other message changes whom you report to.",
            ),
            "parentless": ("A Manager started from the dashboard has no parent and needs none.",),
        }
        investigator_clauses = {
            "report address": ("addressed to the parent's role with its task references",),
            "parentless": ("Without a parent, the developer reads the report in your own chat.",),
        }
        for root in surfaces():
            for relative, clauses in (
                ("roles/manager.md", manager_clauses),
                ("roles/investigator.md", investigator_clauses),
            ):
                with self.subTest(surface=root, source=relative):
                    self.assertEqual(missing_clauses((root / relative).read_text(), clauses), [])

    def test_reviewer_equivalents_keep_positive_facets_and_reject_deletion(self) -> None:
        root = surfaces()[0]
        source = (root / "roles/reviewer.md").read_text()
        clauses = {**LEAF_CLAUSES["roles/reviewer.md"], **autonomy_clauses("reviewer")}
        verdict = {"verdict to worker": clauses.pop("verdict to worker")}
        expected = (
            "2. Send the verdict directly to the Worker through `role_message` on "
            "`agents-remember-task`. The message names the verdict file; it never pastes the text. "
            "A block names the sealed-findings report and requests the whole repair stretch."
        )
        self.assertEqual(verdict["verdict to worker"], (expected,))
        self.assertIn(
            "Before inspecting the assigned packet or candidate, follow [the review intake "
            "procedure](../operations/review.md#receive-the-intake-and-hand-over-the-leaf-verdict)",
            source,
        )
        operation = (root / "operations/review.md").read_text()
        section = re.search(
            r"(?ms)^## Receive the intake and hand over the leaf verdict\n(.*?)(?=^## |\Z)",
            operation,
        )
        self.assertIsNotNone(section)
        assert section is not None
        reading = normalized(section[1])
        self.assertEqual(missing_clauses(reading, verdict), [])
        mutant = reading.replace(normalized(expected), "")
        self.assertNotEqual(mutant, reading)
        self.assertIn("verdict to worker", missing_clauses(mutant, verdict))
        self.assertEqual(reviewer_outputs_duplicates(source), [])
        for retired in RETIRED_REVIEWER_OUTPUT_CLAUSES:
            with self.subTest(deleted_outputs_clause=retired):
                self.assertIn("verdict to worker", missing_clauses(retired, verdict))
                restored = source.replace(
                    "## Write the review artifacts\n",
                    "## Write the review artifacts\n\n" + retired + "\n",
                )
                self.assertNotEqual(restored, source)
                self.assertIn(retired, reviewer_outputs_duplicates(restored))
        with tempfile.TemporaryDirectory() as directory:
            for name, alternatives in clauses.items():
                with self.subTest(obligation=name):
                    if name in autonomy_clauses("reviewer"):
                        text = source
                    else:
                        text = clause_surface(
                            root,
                            "roles/reviewer.md",
                            name,
                            Path(directory) / name / "report.md",
                        )
                    reading = normalized(text)
                    self.assertEqual(missing_clauses(reading, {name: alternatives}), [])
                    mutant = reading
                    for alternative in alternatives:
                        mutant = mutant.replace(normalized(alternative), "")
                    self.assertNotEqual(mutant, reading, "control must remove real instructions")
                    self.assertIn(name, missing_clauses(mutant, {name: alternatives}))

    def test_curation_handoff_clause_targets_resolve_and_reject_a_renamed_heading(self) -> None:
        root = surfaces()[0]
        require_curation_handoff_targets(root)
        for relative, before, after in (
            ("operations/curation.md", "## Handoff / exit\n", "## Renamed exit\n"),
            (
                "reference/curator/memory-handover.md",
                "## Send the whole requested review or repair\n",
                "## Renamed review or repair\n",
            ),
        ):
            with self.subTest(target=relative), tempfile.TemporaryDirectory() as directory:
                copied = Path(directory) / LIFECYCLE
                shutil.copytree(root, copied)
                target = copied / relative
                text = target.read_text()
                renamed = text.replace(before, after)
                self.assertNotEqual(renamed, text)
                target.write_text(renamed)
                with self.assertRaises(AssertionError):
                    require_curation_handoff_targets(copied)

    def test_message_step_requires_pointer_declaration_audience_and_action(self) -> None:
        for mutation in (
            "pointer",
            "declaration",
            "audience",
            "empty-step",
            "action",
            "duplicate-action",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / LIFECYCLE
                shutil.copytree(surfaces()[0], root)
                manifest_path = root / "composition-manifest.json"
                manifest = json.loads(manifest_path.read_text())
                if mutation == "pointer":
                    for path in root.rglob("*.md"):
                        path.write_text(
                            path.read_text().replace(
                                "{{detail:" + MESSAGE_HANDOVER_SOURCE + "}}", ""
                            )
                        )
                elif mutation == "declaration":
                    del manifest["references"]["message-handover"]
                elif mutation == "audience":
                    manifest["references"]["message-handover"]["roles"].remove("reviewer")
                elif mutation == "empty-step":
                    manifest["references"]["message-handover"]["step"] = ""
                else:
                    path = root / "roles/reviewer.md"
                    original = path.read_text()
                    if mutation == "action":
                        mutant = original.replace(
                            MESSAGE_HANDOVER_ACTIONS["reviewer"], "After the turn ends, read"
                        )
                    else:
                        line = next(
                            line
                            for line in original.splitlines()
                            if MESSAGE_HANDOVER_ACTIONS["reviewer"] in line
                        )
                        mutant = original + "\n" + line + "\n"
                    self.assertNotEqual(mutant, original)
                    path.write_text(mutant)
                manifest_path.write_text(json.dumps(manifest))
                if mutation == "empty-step":
                    with self.assertRaisesRegex(
                        CapsuleManifestError,
                        "references.message-handover needs source, roles and step",
                    ):
                        message_step(root, "reviewer")
                else:
                    with self.assertRaises(AssertionError):
                        message_step(root, "reviewer")

        compiled = "Before the action, read [detail]({{detail:reference/example.md}}#step)."
        path = Path("/tmp/detail with space.md")
        delivered = "Before the action, read [detail](/tmp/detail with space.md#step)."
        assert_emitted_detail_links(delivered, compiled, "reference/example.md", path)
        encoded = delivered.replace("detail with space.md", "detail%20with%20space.md")
        assert_emitted_detail_links(encoded, compiled, "reference/example.md", path)
        for mutant in (
            delivered.replace("detail with space.md", "wrong.md"),
            delivered.replace("detail with space.md", "detail%2520with%2520space.md"),
            delivered.replace("#step", "#wrong"),
            delivered + "\n" + delivered,
            delivered.replace("#step", "?unexpected=1#step"),
            delivered.replace("/tmp/", "https://example.test/tmp/"),
            "",
        ):
            with self.subTest(emitted_url=mutant), self.assertRaises(AssertionError):
                assert_emitted_detail_links(mutant, compiled, "reference/example.md", path)

    def test_reviewer_equivalents_keep_each_message_facet_and_reject_deletion(self) -> None:
        # The shipped source now states every facet at the declared shared step.
        # Delete each actual facet, not an obsolete inline spelling.
        for role in ("reviewer", "curator"):
            text = message_step(surfaces()[0], role)[1]
            relative = f"roles/{role}.md"
            self.assertEqual(missing_markers(relative, text), [])
            for marker in REQUIRED_MARKERS:
                with self.subTest(role=role, marker=marker):
                    alternatives = (marker, *COMMON_MARKER_EQUIVALENTS.get(marker, ()))
                    self.assertTrue(any(value in text for value in alternatives))
                    mutant = text
                    for value in alternatives:
                        mutant = mutant.replace(value, "")
                    self.assertNotEqual(mutant, text)
                    self.assertIn(marker, missing_markers(relative, mutant))
        # The same alternatives cannot silently replace an untouched role's wording.
        equivalents = " ".join((*REVIEWER_MARKER_EQUIVALENTS.values(), "`body`", "`attach`"))
        self.assertEqual(missing_markers("roles/reviewer.md", equivalents), [])
        for relative in (*ROLE_FILES, *OPERATION_FILES, *HANDOVER_TEMPLATE_FILES):
            if relative == "roles/curator.md":
                # Only the file-naming sentence is deliberately shared by these roles.
                self.assertEqual(
                    missing_markers(relative, equivalents),
                    ["limit of the settings", "never pasted into a message"],
                )
            elif relative != "roles/reviewer.md":
                self.assertEqual(
                    missing_markers(relative, equivalents), list(REVIEWER_MARKER_EQUIVALENTS)
                )
        curator_equivalents = " ".join((*CURATOR_MARKER_EQUIVALENTS.values(), "`body`", "`attach`"))
        self.assertEqual(missing_markers("roles/curator.md", curator_equivalents), [])
        for relative in (*ROLE_FILES, *OPERATION_FILES, *HANDOVER_TEMPLATE_FILES):
            if relative == "roles/reviewer.md":
                self.assertEqual(
                    missing_markers(relative, curator_equivalents),
                    ["limit of the settings", "never pasted into a message"],
                )
            elif relative != "roles/curator.md":
                self.assertEqual(
                    missing_markers(relative, curator_equivalents), list(CURATOR_MARKER_EQUIVALENTS)
                )


if __name__ == "__main__":
    unittest.main()
