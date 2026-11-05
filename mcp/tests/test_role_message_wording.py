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

from agents_remember.models.role_capsules.types import CapsuleOperation, CapsuleRole
from agents_remember_test_support.testing.leaf_instruction_wording import (
    LEAF_CLAUSES,
    MESSAGE_HANDOVER_SOURCE,
    MESSAGE_HANDOVER_STEP,
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
    """Require S-R1's deletion as well as the retained Process-8 duty."""
    match = re.search(r"(?ms)^## Outputs\n(.*?)(?=^## |\Z)", source)
    assert match is not None
    reading = normalized(match[1])
    return [clause for clause in RETIRED_REVIEWER_OUTPUT_CLAUSES if normalized(clause) in reading]


def require_curation_handoff_targets(root: Path) -> None:
    """Resolve the six corrected clause pointers to their complete real heading."""
    for source, name in CURATION_HANDOFF_FACETS:
        for clause in LEAF_CLAUSES[source][name]:
            pointer = re.search(r"`([^`]+)` §([^.;\n]+)(?:[.;]|$)", clause)
            assert pointer is not None, (source, name, clause)
            target = ((root / source).parent / pointer[1]).resolve()
            assert target.is_relative_to(root.resolve()), target
            heading = pointer[2].strip()
            assert re.search(r"(?m)^#{1,6} " + re.escape(heading) + r"\s*$", target.read_text()), (
                source,
                name,
                str(target),
                heading,
            )


def message_step(root: Path, role: str, operation: str = "orientation") -> tuple[str, str]:
    """Read the payload rules only through their actual declared delivery."""
    with tempfile.TemporaryDirectory() as directory:
        instructions, text = delivered_step(
            root,
            (cast(CapsuleRole, role), cast(CapsuleOperation, operation)),
            MESSAGE_HANDOVER_SOURCE,
            MESSAGE_HANDOVER_STEP,
            Path(directory) / "report.md",
        )
    return instructions, text


def missing_markers(relative: str, text: str) -> list[str]:
    """Equivalent wording is role-specific; every marker still has its own check."""
    equivalents = {
        "roles/reviewer.md": REVIEWER_MARKER_EQUIVALENTS,
        "roles/curator.md": CURATOR_MARKER_EQUIVALENTS,
    }.get(relative, {})
    return [
        marker
        for marker in REQUIRED_MARKERS
        if marker not in text and not (marker in equivalents and equivalents[marker] in text)
    ]


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
                    "Bootstrap starts no role and messages none.",
                    "in your own chat",
                ):
                    if normalized(clause) not in normalized(text):
                        missing.append(f"{short}: missing {clause!r}")
                forbidden.extend(scan_lines(short, text.splitlines()))
                continue
            detail = message_step(root, Path(relative).stem)[1] if relative in ROLE_FILES else ""
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

    def test_reviewer_equivalents_keep_positive_facets_and_reject_deletion(self) -> None:
        source = (surfaces()[0] / "roles/reviewer.md").read_text()
        reading = normalized(source)
        clauses = {**LEAF_CLAUSES["roles/reviewer.md"], **autonomy_clauses("reviewer")}
        # Only this transport duty moved: all other Reviewer duties remain at
        # their role home, so a missing role clause cannot hide in another file.
        transport = {"busy retry": clauses.pop("busy retry")}
        # S-R1 removed the duplicate Outputs sentence. Process step 8 remains
        # the owner of the complete direct-verdict duty, not another section.
        verdict = {"verdict to worker": clauses.pop("verdict to worker")}
        self.assertEqual(
            verdict["verdict to worker"],
            ("Write and record the verdict, then hand it directly to the leaf's Worker.",),
        )
        step = re.search(r"(?m)^8\. (.+)$", source)
        self.assertIsNotNone(step)
        assert step is not None
        step_reading = normalized(step[1])
        self.assertEqual(missing_clauses(step_reading, verdict), [])
        mutant = step_reading.replace(normalized(verdict["verdict to worker"][0]), "")
        self.assertNotEqual(mutant, step_reading)
        self.assertIn("verdict to worker", missing_clauses(mutant, verdict))
        self.assertEqual(reviewer_outputs_duplicates(source), [])
        for retired in RETIRED_REVIEWER_OUTPUT_CLAUSES:
            with self.subTest(deleted_outputs_clause=retired):
                # Neither a dead-only alternative nor a restored Outputs duplicate
                # may satisfy the live-duty/deletion pair.
                self.assertIn("verdict to worker", missing_clauses(retired, verdict))
                restored = source.replace("## Outputs\n", "## Outputs\n\n" + retired + "\n")
                self.assertNotEqual(restored, source)
                self.assertIn(retired, reviewer_outputs_duplicates(restored))
        self.assertEqual(missing_clauses(reading, clauses), [])
        for name, alternatives in clauses.items():
            with self.subTest(obligation=name):
                mutant = reading
                for alternative in alternatives:
                    mutant = mutant.replace(normalized(alternative), "")
                self.assertNotEqual(mutant, reading, "control must remove real instructions")
                self.assertIn(name, missing_clauses(mutant, clauses))
        detail = message_step(surfaces()[0], "reviewer")[1]
        self.assertEqual(missing_clauses(detail, transport), [])
        for clause in transport["busy retry"]:
            mutant = normalized(detail).replace(normalized(clause), "")
            self.assertNotEqual(mutant, normalized(detail))
            self.assertIn("busy retry", missing_clauses(mutant, transport))

    def test_curation_handoff_clause_targets_resolve_and_reject_a_renamed_heading(self) -> None:
        root = surfaces()[0]
        require_curation_handoff_targets(root)
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / LIFECYCLE
            shutil.copytree(root, copied)
            target = copied / "operations/curation.md"
            text = target.read_text()
            renamed = text.replace("## Handoff / exit\n", "## Renamed exit\n")
            self.assertNotEqual(renamed, text)
            target.write_text(renamed)
            with self.assertRaises(AssertionError):
                require_curation_handoff_targets(copied)

    def test_message_step_requires_pointer_declaration_audience_and_exact_step(self) -> None:
        for mutation in ("pointer", "declaration", "audience", "step"):
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
                else:
                    manifest["references"]["message-handover"]["step"] = "after the turn ends"
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaises(AssertionError):
                    message_step(root, "reviewer")

    def test_reviewer_equivalents_keep_each_message_facet_and_reject_deletion(self) -> None:
        # The shipped source now states every facet at the declared shared step.
        # Delete each actual facet, not an obsolete inline spelling.
        for role in ("reviewer", "curator"):
            text = message_step(surfaces()[0], role)[1]
            relative = f"roles/{role}.md"
            self.assertEqual(missing_markers(relative, text), [])
            for marker in REQUIRED_MARKERS:
                with self.subTest(role=role, marker=marker):
                    self.assertIn(marker, text)
                    mutant = text.replace(marker, "")
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
