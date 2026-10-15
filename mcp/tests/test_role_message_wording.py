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
import sys
import unittest
from pathlib import Path
from typing import Any

from test_role_instruction_wording import HandoverTextWordingTests

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


#: The ten surfaces the inventory names today: the canonical tree plus nine
#: generated copies. A dropped or added surface fails here, visibly, instead
#: of passing silent on a smaller census.
EXPECTED_SURFACE_COUNT = 10

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
        "designer",
        "strategist",
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
    "templates/worker-brief.md",
    "templates/manager-brief.md",
    "templates/curator-brief.md",
    "templates/architect-brief.md",
    "templates/verdict.md",
    "templates/turn-report.md",
    "templates/curator-handoff-list.md",
    "templates/master-handover-packet.md",
    "templates/conversation-handover-packet.md",
)

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
            path = root / relative
            text = path.read_text()
            short = f"{label}/{relative}"
            for marker in REQUIRED_MARKERS:
                if marker not in text:
                    missing.append(f"{short}: missing {marker!r}")
            forbidden.extend(scan_lines(short, text.splitlines()))
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
        missing, forbidden = scan(surfaces(), HANDOVER_TEMPLATE_FILES)
        self.assertEqual(missing, [])
        self.assertEqual(
            forbidden,
            [],
            "every place that told a sender to paste:\n" + "\n".join(forbidden),
        )

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
                for marker in REQUIRED_MARKERS:
                    if marker not in text:
                        missing.append(f"{label}: missing {marker!r}")
            forbidden.extend(scan_lines(label, text.splitlines()))
        self.assertEqual(missing, [])
        self.assertEqual(
            forbidden,
            [],
            "a compiled capsule told a sender to paste:\n" + "\n".join(forbidden),
        )

    def test_report_returns_use_role_task_addressing_on_every_surface(self) -> None:
        """Ordinary report returns go to the parent's role (C117 doctrine follow-up).

        Developer questions stay ID-addressed; the report-written notice, the
        manager report line and the completion report-path message go by
        role/task on the canonical tree and every generated copy.
        """
        expected = (
            "addressed to the parent's role with its task references",
            "report to that agent's role with its task references, unless the Architect instructs the reporting-recipient change below",
        )
        missing: list[str] = []
        for root in surfaces():
            label = root.relative_to(REPOSITORY_ROOT).as_posix()
            text = (root / "roles/manager.md").read_text()
            for sentence in expected:
                if sentence not in text:
                    missing.append(f"{label}/roles/manager.md: missing {sentence!r}")
            investigator = (root / "roles/investigator.md").read_text()
            if expected[0] not in investigator:
                missing.append(f"{label}/roles/investigator.md: missing {expected[0]!r}")
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
