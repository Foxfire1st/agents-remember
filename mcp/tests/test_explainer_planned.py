"""MIK-R189 rule 5: the planned-mode page a planning leaf shows, with no changes yet.

Each case builds the payload from the sources the route reads -- the leaf's own task document, the
planned comparison's tips, the memory tip's statements, each packet's text, the reference folder's
names and the code tip's paths -- and asserts one promise of the rule:

* every declared effect is a ``planned · <effect>`` row; an existing subject shows its current
  statement and a ``new:`` subject shows its hand-off label;
* a packet's Scope paths resolve against the code tip or are listed as text;
* the packets, the Prepared context (or ``absent``) and the reference files appear;
* the top line reads ``planned · no changes`` and only deterministic sorts are offered;
* the module is pure: it imports no host, service or bridge, and its payload counts zero agent
  starts and zero model turns.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_planned import (
    INTENDED_NOTICE,
    PARTS,
    PLANNED_SORTS,
    PREPARED_CONTEXT_HEADING,
    TOP_LINE,
    planned_page,
    scope_paths,
)
from agents_remember.canvas.explainer_planned_read import planned_payload
from agents_remember.canvas.parts import part_id_problem
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.planned_comparison import (
    PlannedComparisonTips,
    planned_comparison,
)

TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L183"
SLUG = "183_the-review-pages-explainer"
PACKET = "requirements/MIK-R189-v1-an-intent-review-before-the-work-starts.md"
OTHER_PACKET = "requirements/MIK-R102-v2-comparison-bound-evidence.md"

PACKET_TEXT = """# MIK-R189 @ v1 — An intent review before the work starts

| Field | Value |
| --- | --- |
| Stable ID | MIK-R189 |

## Problem

- The page is decided at `mcp/src/agents_remember/tasks/document.py`, which is inside the section.

## Scope

- `mcp/src/agents_remember/models/knowledge/review_evidence.py` the claim's authority;
- `mcp/src/agents_remember/not-here.py` a path nothing answers to;
- `mcp/src/agents_remember/models/knowledge/review_evidence.py` named twice.

## Exclusions

- `mcp/src/agents_remember/excluded.py` is not in the Scope section.
"""


def _document(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": LEAF,
        "slug": SLUG,
        "title": "The review page's explainer",
        "kind": "light",
        "status": "planning",
        "repo": "agents-remember",
        "createdAt": "2026-10-22T00:00:00Z",
        "expectedKnowledgeEffects": [
            {
                "subject": "invariant:INV-AAAAAA",
                "effect": "clarify",
                "requirementRef": "MIK-R189@v1",
            },
            {
                "subject": "family:FAM-CCCCCC",
                "effect": "strengthen",
                "requirementRef": "MIK-R189@v1",
            },
            {
                "subject": "new:Toast queue for held reports",
                "effect": "introduce",
                "requirementRef": "MIK-R102@v2",
            },
        ],
        "requirements": [
            f"[MIK-R189@v1]({PACKET}) — owned",
            f"[MIK-R102@v2]({OTHER_PACKET}) — owned",
        ],
        "sections": [
            {"kind": "freeform", "heading": "Prepared context", "body": "the tray mockup, picked"},
            {"kind": "freeform", "heading": "Notes", "body": "not the prepared context"},
        ],
    }
    payload.update(overrides)
    return payload


def _comparison() -> Any:
    return planned_comparison(
        PlannedComparisonTips(
            task_id=TASK,
            leaf_id=LEAF,
            code_repository="/code",
            code_commit="a" * 40,
            code_tree="b" * 40,
            memory_repository="/memory",
            memory_commit="c" * 40,
            memory_tree="d" * 40,
        )
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "document": _document(),
        "comparison": _comparison(),
        "statements": {"invariant:INV-AAAAAA": "a claim's authority rests on a resolving basis"},
        "packet_texts": {PACKET: PACKET_TEXT},
        "reference_files": ["picks.md", "mockups/tray.html"],
        "code_tip_paths": [
            "mcp/src/agents_remember/models/knowledge/review_evidence.py",
            "mcp/src/agents_remember/tasks/document.py",
        ],
    }
    arguments.update(overrides)
    return planned_page(**arguments)


# --- the rows -----------------------------------------------------------------------------------


def test_the_document_is_a_real_task_document_and_its_effects_become_planned_rows() -> None:
    document = TaskDocument.model_validate(_document())

    payload = _payload(document=document.model_dump(by_alias=True))

    assert [(row["subject"], row["effect"]) for row in payload["rows"]] == [
        ("invariant:INV-AAAAAA", "clarify"),
        ("family:FAM-CCCCCC", "strengthen"),
        ("new:Toast queue for held reports", "introduce"),
    ]
    assert [row["title"] for row in payload["rows"]] == [
        "planned · clarify",
        "planned · strengthen",
        "planned · introduce",
    ]


def test_an_existing_subject_shows_its_current_statement_and_a_new_subject_its_label() -> None:
    rows = _payload()["rows"]

    assert rows[0] == {
        "subject": "invariant:INV-AAAAAA",
        "kind": "existing",
        "effect": "clarify",
        "title": "planned · clarify",
        "statement": "a claim's authority rests on a resolving basis",
        "label": None,
        "statementState": "statement",
        "requirement": "MIK-R189@v1",
        "part": "effects",
    }
    assert rows[2]["kind"] == "new"
    assert rows[2]["label"] == "Toast queue for held reports"
    assert rows[2]["statement"] is None


def test_an_existing_subject_the_memory_tip_does_not_hold_is_unread_not_invented() -> None:
    rows = _payload()["rows"]

    assert rows[1]["subject"] == "family:FAM-CCCCCC"
    assert rows[1]["statement"] is None
    assert rows[1]["statementState"] == "unread"


# --- the packets and their scope ----------------------------------------------------------------


def test_a_packet_is_rendered_with_its_title_and_its_scope_paths() -> None:
    packets = _payload()["packets"]

    assert packets[0]["ref"] == "MIK-R189@v1"
    assert packets[0]["path"] == PACKET
    assert packets[0]["title"] == "MIK-R189 @ v1 — An intent review before the work starts"
    assert packets[0]["state"] == "rendered"
    assert packets[0]["body"] == PACKET_TEXT.strip()
    assert packets[0]["scope"] == [
        {
            "path": "mcp/src/agents_remember/models/knowledge/review_evidence.py",
            "state": "resolved",
            "resolvedPath": "mcp/src/agents_remember/models/knowledge/review_evidence.py",
            "exists": True,
            "tipAddress": f"{'b' * 40}:mcp/src/agents_remember/models/knowledge/review_evidence.py",
        },
        {
            "path": "mcp/src/agents_remember/not-here.py",
            "state": "text",
            "resolvedPath": None,
            "exists": False,
            "tipAddress": None,
        },
    ]


def test_a_scope_path_resolves_at_the_code_tip_or_is_listed_as_text() -> None:
    resolved, unresolved = _payload()["packets"][0]["scope"]

    assert resolved["state"] == "resolved"
    assert resolved["resolvedPath"] == "mcp/src/agents_remember/models/knowledge/review_evidence.py"
    assert resolved["exists"] is True
    assert (
        resolved["tipAddress"]
        == f"{'b' * 40}:mcp/src/agents_remember/models/knowledge/review_evidence.py"
    )
    assert unresolved["state"] == "text"
    assert unresolved["path"] == "mcp/src/agents_remember/not-here.py"
    assert unresolved["resolvedPath"] is None
    assert unresolved["exists"] is False
    assert unresolved["tipAddress"] is None


def test_a_file_at_the_code_tip_resolves_its_scope_path() -> None:
    payload = _payload(
        code_tip_paths=["mcp/src/agents_remember/models/knowledge/review_evidence.py"]
    )

    assert payload["packets"][0]["scope"][0]["state"] == "resolved"


def test_a_packet_whose_text_was_not_read_says_so_rather_than_showing_an_empty_scope() -> None:
    packets = _payload()["packets"]

    assert packets[1]["ref"] == "MIK-R102@v2"
    assert packets[1]["state"] == "unread"
    assert packets[1]["body"] == ""
    assert packets[1]["scope"] == []


def test_a_package_relative_scope_path_resolves_to_the_one_file_that_ends_with_it() -> None:
    document = _document(requirements=["[MIK-R189@v1](requirements/MIK-R189-v1-x.md) — owned"])
    packet = "# X @ v1 — a packet\n\n## Scope\n\n- `tasks/document.py` the document\n"

    payload = planned_page(
        document=document,
        comparison=_comparison(),
        packet_texts={"requirements/MIK-R189-v1-x.md": packet},
        code_tip_paths=["mcp/src/agents_remember/tasks/document.py"],
    )

    assert payload["packets"][0]["scope"] == [
        {
            "path": "tasks/document.py",
            "state": "resolved",
            "resolvedPath": "mcp/src/agents_remember/tasks/document.py",
            "exists": True,
            "tipAddress": f"{'b' * 40}:mcp/src/agents_remember/tasks/document.py",
        }
    ]


def test_a_scope_path_two_tip_files_answer_is_listed_as_text() -> None:
    document = _document(requirements=["[MIK-R189@v1](requirements/MIK-R189-v1-x.md) — owned"])
    packet = "# X @ v1 — a packet\n\n## Scope\n\n- `app.py` the application\n"

    payload = planned_page(
        document=document,
        comparison=_comparison(),
        packet_texts={"requirements/MIK-R189-v1-x.md": packet},
        code_tip_paths=["a/app.py", "b/app.py"],
    )

    assert payload["packets"][0]["scope"] == [
        {
            "path": "app.py",
            "state": "text",
            "resolvedPath": None,
            "exists": False,
            "tipAddress": None,
        }
    ]


def test_scope_reads_only_the_scope_section() -> None:
    paths = scope_paths(PACKET_TEXT)

    assert paths == (
        "mcp/src/agents_remember/models/knowledge/review_evidence.py",
        "mcp/src/agents_remember/not-here.py",
    )
    assert all("excluded" not in path and "document.py" not in path for path in paths)


# --- context, references and the top line -------------------------------------------------------


def test_the_prepared_context_section_is_shown_or_reads_absent() -> None:
    present = _payload()["preparedContext"]
    absent = _payload(document=_document(sections=[]))["preparedContext"]

    assert present == {
        "heading": PREPARED_CONTEXT_HEADING,
        "state": "present",
        "body": "the tray mockup, picked",
        "part": "prepared-context",
    }
    assert absent == {
        "heading": PREPARED_CONTEXT_HEADING,
        "state": "absent",
        "body": "",
        "part": "prepared-context",
    }


def test_the_reference_files_are_the_names_in_the_leafs_folder() -> None:
    files = _payload(reference_files=["picks.md", "picks.md", "mockups/tray.html"])[
        "referenceFiles"
    ]

    assert files == [
        {
            "path": f"notes/reference/{LEAF}/mockups/tray.html",
            "name": "tray.html",
            "part": "reference-files",
        },
        {
            "path": f"notes/reference/{LEAF}/picks.md",
            "name": "picks.md",
            "part": "reference-files",
        },
    ]


def test_a_name_outside_the_leafs_reference_folder_is_not_listed() -> None:
    files = _payload(reference_files=["picks.md", "notes/reference/OTHER/pick.md"])[
        "referenceFiles"
    ]

    assert [entry["name"] for entry in files] == ["picks.md"]


def test_the_top_line_reads_planned_and_no_changes() -> None:
    payload = _payload()

    assert TOP_LINE == "planned · no changes"
    assert payload["topLine"] == TOP_LINE
    assert payload["mode"] == "planned"
    assert payload["binding"] == {
        "owner": "planned",
        "number": 0,
        "reference": f"review-comparison:{TASK}/{LEAF}/planned",
        "label": "planned",
    }
    assert payload["authority"] == "intended"


def test_the_tips_are_the_planned_comparisons_own() -> None:
    tips = _payload()["tips"]

    assert tips == {
        "code": "a" * 40,
        "codeTree": "b" * 40,
        "memory": "c" * 40,
        "memoryTree": "d" * 40,
        "recordedAt": "planned-master-tip",
    }


# --- deterministic, and nothing starts ----------------------------------------------------------


def test_only_deterministic_sorts_are_offered() -> None:
    sorts = _payload()["sorts"]

    assert [entry["id"] for entry in sorts] == ["declared", "subject", "effect"]
    assert sorts == [dict(entry) for entry in PLANNED_SORTS]
    for entry in sorts:
        assert not any(
            word in entry["id"] or word in entry["label"] or word in entry["order"]
            for word in ("judgment", "grade", "strength")
        )


def test_the_page_has_no_action_that_could_start_anything() -> None:
    payload = _payload()

    assert payload["actions"] == []
    assert payload["modelTurns"] == 0
    assert payload["agentStarts"] == 0


def test_the_page_names_parts_the_products_map_admits() -> None:
    payload = _payload()

    assert payload["parts"] == [dict(entry) for entry in PARTS]
    for part in payload["parts"]:
        assert part_id_problem(part["id"]) is None


def test_the_reads_are_counted_not_claimed() -> None:
    reads = _payload()["reads"]

    assert reads == {
        "taskDocuments": 1,
        "knowledgeStatements": 2,
        "packets": 2,
        "scopePaths": 2,
    }


def test_equal_inputs_answer_an_equal_payload() -> None:
    assert _payload() == _payload()


def test_the_page_leaves_the_task_document_and_its_status_unchanged() -> None:
    document = _document()
    before = json.dumps(document, sort_keys=True, ensure_ascii=False)

    _payload(document=document)

    assert json.dumps(document, sort_keys=True, ensure_ascii=False) == before
    assert TaskDocument.model_validate(document).status == "planning"


def test_the_module_imports_no_host_service_or_bridge() -> None:
    probe = (
        "import sys\n"
        "import agents_remember.canvas.explainer_planned\n"
        "danger = ('explainer_server', 'explainer_service', 'explainer_workspace',\n"
        "          'explainer_state', 'paseo', 'presenter_host', 'role_launch',\n"
        "          'worktrees.modules')\n"
        "print(sorted(name for name in sys.modules if name.startswith(\n"
        "    'agents_remember') and any(word in name for word in danger)))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "[]"


def test_the_intended_notice_travels_with_the_page() -> None:
    payload = _payload()

    assert payload["intendedNotice"] == INTENDED_NOTICE
    assert INTENDED_NOTICE == "Intended, not a fact about the code"


def test_planned_payload_discovers_nested_reference_files_and_packet_body(
    tmp_path: Path,
) -> None:
    task_root = tmp_path / TASK
    packet_file = task_root / PACKET
    packet_file.parent.mkdir(parents=True)
    packet_file.write_text(PACKET_TEXT, encoding="utf-8")

    nested_ref = task_root / "notes" / "reference" / LEAF / "mockups" / "sub" / "tray.html"
    nested_ref.parent.mkdir(parents=True)
    nested_ref.write_text("<html>tray</html>", encoding="utf-8")
    top_ref = task_root / "notes" / "reference" / LEAF / "picks.md"
    top_ref.write_text("# picks\n", encoding="utf-8")

    code_repo = tmp_path / "code-repo"
    code_repo.mkdir(parents=True)
    subprocess.run(["git", "-C", str(code_repo), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(code_repo), "config", "user.email", "t@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(code_repo), "config", "user.name", "t"], check=True)
    target = code_repo / "mcp/src/agents_remember/models/knowledge/review_evidence.py"
    target.parent.mkdir(parents=True)
    target.write_text("# evidence\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(code_repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(code_repo), "commit", "-q", "-m", "init"], check=True)
    code_tree = subprocess.run(
        ["git", "-C", str(code_repo), "rev-parse", "HEAD^{tree}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    comp = planned_comparison(
        PlannedComparisonTips(
            task_id=TASK,
            leaf_id=LEAF,
            code_repository=str(code_repo),
            code_commit="a" * 40,
            code_tree=code_tree,
            memory_repository="/memory",
            memory_commit="c" * 40,
            memory_tree="d" * 40,
        )
    )

    payload = planned_payload(
        document=_document(expectedKnowledgeEffects=[]),
        comparison=comp,
        task_root=task_root,
        coordination_root=tmp_path,
        code_repository=code_repo,
    )

    assert payload["packets"][0]["body"] == PACKET_TEXT.strip()
    assert [entry["path"] for entry in payload["referenceFiles"]] == [
        f"notes/reference/{LEAF}/mockups/sub/tray.html",
        f"notes/reference/{LEAF}/picks.md",
    ]
