"""The disposable corpus of a sandbox: a small repository, two requirement packets, four tasks.

The task documents are not written here. They are handed, as field sets, to the tool server of the
PNT build under test (``build_tool_calls.py``), so the build's own task tooling creates documents it
can read and list. The requirement packets follow the build's canonical packet template
(``skills/w-02-light-task-workflow/requirement-packet-template.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .layout import INTEGRATION_BRANCH, REPOSITORY_ID

SPRINT_TASK = "sbx-sprint"
SPRINT_ID = "SBX-SPRINT"
MASTER_TASK = "sbx-text-helpers"
MASTER_ID = "SBX-M1"
CREATED_AT = "2026-10-02T00:00:00+02:00"
TEST_COMMAND = "python3 -m unittest"

REPOSITORY_FILES: dict[str, str] = {
    "README.md": f"""# Sandbox app

A disposable repository inside the PNT sandbox. Agents started from the sandbox's dashboard work
here; nothing in it is product code and it has no remote outside the sandbox.

`textkit` is a tiny text helper package. Run its tests from the repository root with:

    {TEST_COMMAND}
""",
    ".gitignore": "__pycache__/\n",
    "textkit/__init__.py": '"""Small text helpers of the sandbox app."""\n',
    "textkit/case.py": '''"""Letter-case helpers."""


def title_case(text: str) -> str:
    """Capitalize the first letter of every whitespace-separated word, keeping the spacing."""
    return " ".join(word[:1].upper() + word[1:] for word in text.split(" "))
''',
    "tests/__init__.py": "",
    "tests/test_case.py": """import unittest

from textkit.case import title_case


class TitleCaseTests(unittest.TestCase):
    def test_capitalizes_each_word_and_keeps_the_rest(self):
        self.assertEqual(title_case("hello wide wORLD"), "Hello Wide WORLD")

    def test_empty_text_stays_empty(self):
        self.assertEqual(title_case(""), "")


if __name__ == "__main__":
    unittest.main()
""",
}


@dataclass(frozen=True)
class Leaf:
    """One leaf: a function and its test, small enough for one worker turn."""

    leaf_id: str
    slug: str
    title: str
    stable_id: str
    packet_slug: str
    function: str
    module: str
    test_module: str
    behaviour: str
    conforming: str
    non_conforming: str
    boundary: str

    @property
    def packet_path(self) -> str:
        return f"requirements/{self.stable_id}-v1-{self.packet_slug}.md"

    @property
    def requirement_text(self) -> str:
        return (
            f"{self.stable_id}@v1 — `{self.module}::{self.function}` {self.behaviour} "
            f"`{self.test_module}` covers it and `{TEST_COMMAND}` passes."
        )


LEAVES = (
    Leaf(
        leaf_id="SBX-M1-L1",
        slug="01_slugify",
        title="Add slugify() and its test",
        stable_id="SBX-R01",
        packet_slug="slugify",
        function="slugify(text: str) -> str",
        module="textkit/slug.py",
        test_module="tests/test_slug.py",
        behaviour=(
            "lowercases the text, replaces every run of characters other than ASCII letters and "
            "digits with one hyphen, and removes leading and trailing hyphens."
        ),
        conforming='`slugify("  Hello,  Wide World! ")` returns `"hello-wide-world"`.',
        non_conforming='`slugify("a  b")` returns `"a--b"`: a run must become one hyphen.',
        boundary='`slugify("")` and `slugify("!!!")` return `""`.',
    ),
    Leaf(
        leaf_id="SBX-M1-L2",
        slug="02_word-count",
        title="Add word_count() and its test",
        stable_id="SBX-R02",
        packet_slug="word-count",
        function="word_count(text: str) -> int",
        module="textkit/count.py",
        test_module="tests/test_count.py",
        behaviour="returns the number of whitespace-separated words in the text.",
        conforming='`word_count("one  two\\nthree")` returns `3`.',
        non_conforming='`word_count("one  two")` returns `3` because the double space was counted.',
        boundary='`word_count("")` and `word_count("   ")` return `0`.',
    ),
)


def packet_markdown(leaf: Leaf) -> str:
    """One approved requirement packet in the build's canonical packet shape."""
    return f"""# {leaf.stable_id} @ v1 — {leaf.title}

| Field | Value |
| ----- | ----- |
| Stable ID | `{leaf.stable_id}` |
| Version | `v1` |
| State at packet freeze | `approved` |
| Developer approval | sandbox fixture: approved for disposable test work by `pnt-sandbox build`; not a product requirement |
| Supersedes | none |

## Normative Requirement

`{leaf.module}` provides `{leaf.function}`, which {leaf.behaviour}

## Problem

The sandbox app has no such helper. The leaf exists so that a role agent started from the sandbox
has a small, concrete change to make.

## Required Behavior

1. `{leaf.function}` exists in `{leaf.module}` and {leaf.behaviour}
2. `{leaf.test_module}` holds unit tests for it, including the boundary case below.
3. `{TEST_COMMAND}`, run from the repository root, passes.

## Rationale

One function and one test file is the smallest change that still exercises editing, testing and
reporting.

## Scope

`{leaf.module}` and `{leaf.test_module}` in the repository `{REPOSITORY_ID}`.

## Exclusions

Every other file of the repository. Packaging, dependencies and command-line entry points.

## Preservation Boundaries

- `textkit/case.py` and `tests/test_case.py` are unchanged and their tests still pass.
- The standard library is the only dependency.

## Failure And Recovery Behavior

- An argument that is not a string raises `TypeError`; nothing is coerced.

## Examples

- Conforming: {leaf.conforming}
- Non-conforming: {leaf.non_conforming}
- Boundary case: {leaf.boundary}

## Forbidden Overreach

Adding a dependency, changing the existing helper, or reformatting files outside the scope.

## Interaction Diagram

Prose is sufficient: one pure function, no state.

## Expected Evidence

### Deliverable Evidence

- Required class: code path and symbol.
- Expected anchors: `{leaf.module}` and the function named above.

### Verification Evidence

- Required class: test module and the command output.
- Demonstrated behavior: the conforming and boundary examples hold.
- Failure caught: the non-conforming example.

## Authority And Provenance

- Intent source: the PNT sandbox fixture (PNT-R11).
- Durable approval: none needed; the sandbox is disposable.
- Evidence used to compile this packet: none.
- Compiler: `pnt-sandbox build`.

## Dependencies

- Requires: none
- Constrains: none
- Independently executable manifestations: one leaf, `{leaf.leaf_id}`.

## Open Truth Gaps

- none

## Cold-Read Verification

| Field | Result |
| ----- | ------ |
| Reader | none: the packet is generated with the sandbox |
| Verdict | `pass` (by construction of the fixture) |

## Revision History

| Version | Date-Time | Developer ruling | Change | Acceptance invalidation |
| ------- | --------- | ---------------- | ------ | ----------------------- |
| v1 | {CREATED_AT[:16]} | sandbox fixture | initial | N/A |
"""


def packet_register() -> str:
    rows = "\n".join(
        f"| `{leaf.stable_id}` | `v1` | `approved` | [{leaf.title}]"
        f"({leaf.packet_path.removeprefix('requirements/')}) | `{leaf.leaf_id}` |"
        for leaf in LEAVES
    )
    return f"""# Requirement register — sandbox master {MASTER_ID}

| Stable ID | Version | State | Packet | Leaf |
| --------- | ------- | ----- | ------ | ---- |
{rows}
"""


def packet_files() -> dict[str, str]:
    """Packet files relative to the master's task folder."""
    files = {leaf.packet_path: packet_markdown(leaf) for leaf in LEAVES}
    files["requirements/README.md"] = packet_register()
    return files


def master_fields() -> dict[str, Any]:
    return {
        "id": MASTER_ID,
        "slug": "task",
        "title": "Sandbox master: text helpers for the sandbox app",
        "kind": "master",
        "status": "inProgress",
        "repo": REPOSITORY_ID,
        "type": "Master",
        "createdAt": CREATED_AT,
        "master": f"../{SPRINT_TASK}/task.md",
        "executionNature": "organizational",
        "objective": (
            "Add two independent text helpers to the sandbox app, one per leaf. The work is "
            "disposable: it exists so that role agents started from the sandbox have a real task."
        ),
        "subTasks": [
            {
                "number": leaf.leaf_id,
                "name": leaf.title,
                "file": f"{leaf.slug}.md",
                "status": "planning",
                "scope": f"`{leaf.module}` and `{leaf.test_module}`; {leaf.stable_id}@v1.",
            }
            for leaf in LEAVES
        ],
        "sections": [{"kind": "subTasks", "heading": "Leaves", "body": ""}],
    }


def sprint_fields() -> dict[str, Any]:
    return {
        "id": SPRINT_ID,
        "slug": "task",
        "title": "Sandbox sprint: text helpers",
        "kind": "master",
        "status": "inProgress",
        "repo": REPOSITORY_ID,
        "type": "Orchestration Sprint",
        "createdAt": CREATED_AT,
        "integrationBranch": INTEGRATION_BRANCH,
        "objective": "Run the one sandbox master through its two leaves.",
        "orchestrates": [MASTER_TASK],
        "subTasks": [
            {
                "number": MASTER_ID,
                "name": "Text helpers for the sandbox app",
                "status": "inProgress",
                "scope": "One master with two independent leaves.",
                "masterRef": {"repository": REPOSITORY_ID, "path": f"{MASTER_TASK}/task.json"},
            }
        ],
        "sections": [{"kind": "subTasks", "heading": "Commanded masters", "body": ""}],
    }


def leaf_fields(leaf: Leaf) -> dict[str, Any]:
    return {
        "id": leaf.leaf_id,
        "slug": leaf.slug,
        "title": leaf.title,
        "kind": "subTask",
        "status": "planning",
        "repo": REPOSITORY_ID,
        "type": "SubTask (sandbox fixture)",
        "createdAt": CREATED_AT,
        "master": "task.md",
        "objective": f"`{leaf.module}` provides `{leaf.function}`, which {leaf.behaviour}",
        "requirements": [
            leaf.requirement_text,
            {
                "kind": "approved-requirement-packet",
                "path": leaf.packet_path,
                "stableId": leaf.stable_id,
                "version": "v1",
            },
        ],
        "steps": [
            {"id": "S1", "title": f"Write `{leaf.module}` with `{leaf.function}`."},
            {"id": "S2", "title": f"Write `{leaf.test_module}`, including the boundary case."},
            {"id": "S3", "title": f"Run `{TEST_COMMAND}` from the repository root and report."},
        ],
        "references": [f"[{leaf.stable_id}@v1]({leaf.packet_path})"],
        "codeExamplesNote": "The packet's Examples section gives the cases to cover.",
    }


def task_document_calls() -> list[dict[str, Any]]:
    """The ``task_doc`` calls that create the corpus, each skipped when its document exists."""

    def create(name: str, task: str, fields: dict[str, Any], slug: str | None = None) -> dict:
        target = {"repo_id": REPOSITORY_ID, "task_name": task}
        if slug is not None:
            target["slug"] = slug
        return {
            "name": name,
            "tool": "task_doc",
            "arguments": {**target, "operation": "create", "fields": fields},
            "unless": {"tool": "task_doc", "arguments": {**target, "operation": "get"}},
        }

    return [
        create("master", MASTER_TASK, master_fields()),
        create("sprint", SPRINT_TASK, sprint_fields()),
        *(
            create(f"leaf {leaf.leaf_id}", MASTER_TASK, leaf_fields(leaf), leaf.slug)
            for leaf in LEAVES
        ),
    ]
