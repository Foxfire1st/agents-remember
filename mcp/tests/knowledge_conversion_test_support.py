"""Fixture repositories for the MIK-R24 conversion tests: a code repository, and an unconverted
memory tree whose cards exercise every citation-row case, with a legacy knowledge database.

The legacy database holds only the tables and columns the export reads, in the real schema's
spelling, so the export is exercised against the same queries it runs on a real ``knowledge.sqlite``.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import apsw

APP = "src/pkg/app.py"
APP_CARD = f"onboarding/{APP}.md"
APP_SIDECAR = f"onboarding/{APP}.json"
OTHER_CARD = "onboarding/src/pkg/other.py.md"
ROUTE_CARD = "onboarding/src/overview.md"
ROOT_CARD = "onboarding/overview.md"
DELETED = "src/pkg/deleted.py"

APP_SOURCE = '''"""App."""


def alpha(value):
    return value + 1


def beta(value):
    total = alpha(value)
    return total * 2


class Holder:
    def method(self):
        return "signals"
'''
STRAY_ROW = f"| A row appended after the table. | `alpha` | {APP}:4-5 |"
TEST_SOURCE = "def test_alpha():\n    assert True\n"
GUIDE = "# Flow\n\nThe flow is alpha then beta.\n"


def git(repo: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=False, input=input_text
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def init_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "agents-remember@example.invalid")
    git(root, "config", "user.name", "Agents Remember")


def commit_files(root: Path, files: dict[str, str | None], message: str) -> str:
    for relative, text in files.items():
        target = root / relative
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


@dataclass(frozen=True)
class CodeFixture:
    root: Path
    first: str
    head: str
    deleted_blob: str
    app_blob: str


def code_repository(root: Path) -> CodeFixture:
    init_repository(root)
    first = commit_files(
        root,
        {
            DELETED: "GONE = 1\n",
            APP: APP_SOURCE,
            "tests/test_app.py": TEST_SOURCE,
            "docs/guide.md": GUIDE,
        },
        "first",
    )
    deleted_blob = git(root, "rev-parse", f"{first}:{DELETED}")
    head = commit_files(root, {DELETED: None}, "second")
    return CodeFixture(
        root=root,
        first=first,
        head=head,
        deleted_blob=deleted_blob,
        app_blob=git(root, "rev-parse", f"{head}:{APP}"),
    )


def _metadata(path: str, commit: str, governing: str) -> str:
    return (
        "| Field | Value |\n| --- | --- |\n| repository | demo |\n"
        f"| path | `{path}` |\n| doc_type | `file-level-onboarding` |\n"
        "| lastUpdated | 2026-09-01T00:00 |\n"
        f"| lastVerifiedCommitHash | `{commit}` |\n"
        "| lastVerifiedCommitDate | 2026-09-01T00:00:00+00:00 |\n"
        f"| governingOverview | `{governing}` |\n"
    )


def app_card(commit: str) -> str:
    return (
        f"# {APP}\n\n{_metadata(APP, commit, '../overview.md')}\n"
        "## Purpose\n\nAdds and doubles; callers read the result as signals[0].\n\n"
        "## Docs References\n\nNo docs are configured.\n\n"
        "| Finding | Anchor | Source |\n| --- | --- | --- |\n"
        "| No configured external domain-documentation evidence. | — | — |\n"
        '| The guide explains the flow. | "The flow is" | docs/guide.md:3-3; '
        "https://example.invalid/spec |\n\n"
        "## Repo-Internal References\n\nChecked at the first commit.\n\n"
        "| Finding | Anchor | Source |\n| --- | --- | --- |\n"
        f"| alpha and beta compute the total. | `alpha`; `beta` | {APP}:4-5; {APP}:8-11 |\n"
        f"| The method sits in the holder. | `method` | {APP}:14-15 |\n"
        f'| A quoted literal keeps its range. | "return value + 1" | {APP}:5-5 |\n'
        "| A removed file stays visible. | `gone` | src/pkg/gone.py:1-3 |\n"
        f"| A range past the end is unresolved. | — | {APP}:90-95 |\n"
        "| The test proves alpha. | `test_alpha` | tests/test_app.py:1-2 |\n"
        "| The whole guide. | — | docs/guide.md |\n\n"
        f"{STRAY_ROW}\n\n"
        "## Cross-Repo References\n\n"
        "| Finding | Anchor | Source |\n| --- | --- | --- |\n"
        "| No additional configured cross-repository evidence is claimed. | — | — |\n\n"
        "## Update History\n\n- 2026-09-01 — created; line(s) [3] repaired.\n"
    )


def other_card(commit: str) -> str:
    return (
        f"# src/pkg/other.py\n\n{_metadata('src/pkg/other.py', commit, '../../overview.md')}\n"
        "## Purpose\n\nNo evidence here.\n\n## Update History\n\n"
        "- 2026-09-02 — reviewed. No content impact: renamed a helper only.\n"
    )


def route_card(commit: str) -> str:
    return (
        "# src\n\n## Route Model\n\nThe source route.\n\n## Repo-Internal References\n\n"
        "| Finding | Anchor | Source |\n| --- | --- | --- |\n"
        f"| The app module computes. | `beta` | {APP}:8-11 |\n"
    )


def memory_files(commit: str) -> dict[str, str]:
    return {
        APP_CARD: app_card(commit),
        OTHER_CARD: other_card(commit[:10]),  # an abbreviated anchor commit
        ROUTE_CARD: route_card(commit),
        ROOT_CARD: "# demo\n\nThe repository root route.\n",
        "system/settings.md": "# settings\n",
    }


_SCHEMA = (
    "CREATE TABLE invariant (invariant_id TEXT, display_label TEXT)",
    "CREATE TABLE invariant_revision (revision_id TEXT, invariant_id TEXT, statement TEXT, "
    "applicability TEXT, conditions TEXT, exclusions TEXT, state_at_origin TEXT, provenance TEXT)",
    "CREATE TABLE invariant_predecessor (child_revision_id TEXT, parent_revision_id TEXT)",
    "CREATE TABLE family (family_id TEXT, display_label TEXT)",
    "CREATE TABLE family_revision (revision_id TEXT, family_id TEXT, joint_guarantee TEXT, "
    "state_at_origin TEXT, provenance TEXT)",
    "CREATE TABLE family_predecessor (child_revision_id TEXT, parent_revision_id TEXT)",
    "CREATE TABLE family_member (family_revision_id TEXT, invariant_revision_id TEXT)",
    "CREATE TABLE source_anchor (anchor_id TEXT, path TEXT, source_identity TEXT, locator TEXT)",
    "CREATE TABLE realization_claim (claim_id TEXT, invariant_revision_id TEXT, anchor_id TEXT, "
    "role TEXT, rationale TEXT, provenance TEXT)",
)
LEAF_ACTOR = "ar-coordination/tasks/demo/260101_fixture-task/RULING.md#R1 (260101-FIX-L3)"
DOC_ACTOR = "/x/ar-coordination/tasks/demo/260101_fixture-task/05_leaf-doc.json"


def _provenance(actor: str) -> str:
    return json.dumps({"actor_ref": actor})


def legacy_database(path: Path, code: CodeFixture, *, colliding: bool = False) -> None:
    """Two invariants (one with two revisions), one family, three head claims, one old claim."""

    connection = apsw.Connection(str(path))
    try:
        for statement in _SCHEMA:
            connection.execute(statement)
        rows = {
            "invariant": [("inv-one", "FIX-I-1"), ("inv-two", "FIX I@2 / two")],
            "invariant_revision": [
                (
                    "r1a",
                    "inv-one",
                    "Old.",
                    "Everywhere.",
                    '["Old."]',
                    "[]",
                    "proposed",
                    _provenance(LEAF_ACTOR),
                ),
                (
                    "r1b",
                    "inv-one",
                    "Alpha adds one.",
                    "Everywhere.",
                    '["Must hold.","Hand-off kind: clause.","Evidence: measured."]',
                    '["Never."]',
                    "proposed",
                    _provenance(LEAF_ACTOR),
                ),
                (
                    "r2",
                    "inv-two",
                    "Deleted code once held.",
                    "Old code.",
                    "[]",
                    "[]",
                    "accepted",
                    _provenance(DOC_ACTOR),
                ),
            ],
            "invariant_predecessor": [("r1b", "r1a")],
            "family": [("fam-one", "fixture family")],
            "family_revision": [
                ("f1", "fam-one", "Both hold.", "proposed", _provenance(DOC_ACTOR))
            ],
            "family_member": [("f1", "r1a"), ("f1", "r2")],
            "source_anchor": [
                (
                    "a1",
                    APP,
                    json.dumps({"object_id": code.app_blob}),
                    json.dumps({"kind": "symbol", "language": "python", "qualified_name": "alpha"}),
                ),
                (
                    "a2",
                    APP,
                    json.dumps({"object_id": code.app_blob}),
                    json.dumps({"kind": "line_range", "start_line": 8, "end_line": 11}),
                ),
                (
                    "a3",
                    DELETED,
                    json.dumps({"object_id": code.deleted_blob}),
                    json.dumps({"kind": "file"}),
                ),
            ],
            "realization_claim": [
                ("c0", "r1a", "a1", "support", "Old claim.", _provenance(LEAF_ACTOR)),
                (
                    "c1",
                    "r1b",
                    "a1",
                    "primary-authority",
                    "Alpha is the adder.",
                    _provenance(LEAF_ACTOR),
                ),
                ("c2", "r1b", "a2", "incidental", "Beta calls alpha.", _provenance(LEAF_ACTOR)),
                (
                    "c3",
                    "r2",
                    "a3",
                    "enforcement",
                    "The deleted file held it.",
                    _provenance(DOC_ACTOR),
                ),
            ],
        }
        if colliding:
            rows["source_anchor"].append(
                (
                    "a4",
                    APP,
                    json.dumps({"object_id": code.app_blob}),
                    json.dumps({"kind": "symbol", "language": "python", "qualified_name": "alpha"}),
                )
            )
            rows["realization_claim"].append(
                ("c4", "r1b", "a4", "support", "Duplicate.", _provenance(LEAF_ACTOR))
            )
        for table, values in rows.items():
            for value in values:
                marks = ", ".join("?" for _ in value)
                connection.execute(f"INSERT INTO {table} VALUES ({marks})", value)
    finally:
        connection.close()


def memory_repository(root: Path, code: CodeFixture, *, database: bool = True) -> str:
    """An unconverted memory repository whose cards were verified at ``code.first``."""

    init_repository(root)
    files = memory_files(code.first)
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if database:
        legacy_database(root / "knowledge.sqlite", code)
    (root / ".gitignore").write_text("*.index.json\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "legacy memory")
    return git(root, "rev-parse", "HEAD")
