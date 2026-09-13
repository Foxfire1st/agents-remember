"""Actual citation publication: fixer batches, migration conversion, and observed conflicts.

Both document-rewriting transactions live here. The fixer section covers accepted batches,
refusal isolation, and observed conflicts. The migration section covers the other production
caller, ``migration.migrate_onboarding_root``, which rewrites superseded-format tables and
consults the SAME continuity authority the fixer does: an anchor that left a live cited file
is refused, and a relocation across the tree needs the document's own verification provenance.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from agents_remember.memory_quality.style.citations import (
    deterministic_projection as projection,
)
from agents_remember.memory_quality.style.citations import (
    fixer,
    migration,
    old_form,
    source_index,
)
from agents_remember.memory_quality.style.citations.documents import (
    transaction as document_transaction,
)
from agents_remember.memory_quality.style.citations.resolution import Trees

STAMP = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
HISTORY = "\n## Update History\n\n- 2026-08-01T00:00:00+00:00: Original.\n"
DECLINED = "| Pooled. | `persist` | src/left.py:1-2; src/right.py:99-99 |"
MOVED = "| Single. | `unique` | src/gone.py:1-2 |"
SECOND = "| Second. | `another` | src/missing.py:1-2 |"


@dataclass(frozen=True)
class Scenario:
    code: Path
    onboarding: Path
    stamp: str

    @staticmethod
    def git(root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=root, text=True, capture_output=True, check=False
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return result.stdout.strip()

    def card(self, name: str, *rows: str) -> Path:
        path = self.onboarding / name
        path.write_text(
            "# Citation card\n\n"
            "| Field | Value |\n"
            "| --- | --- |\n"
            f"| lastVerifiedCommitHash | `{self.stamp}` |\n\n"
            "| Finding | Anchor | Source |\n"
            "| --- | --- | --- |\n" + "\n".join(rows) + "\n" + HISTORY,
            encoding="utf-8",
        )
        return path

    def fix(self, **options: Any) -> dict[str, Any]:
        with mock.patch.object(projection, "now_utc", return_value=STAMP):
            return fixer.fix_onboarding_root(self.onboarding, self.code, **options)

    def snapshot(self) -> str:
        trees = Trees(code_root=self.code, memory_root=self.onboarding.parent)
        with source_index.open_repository_index(trees) as index:
            return index.snapshot_id


@pytest.fixture
def scenario(tmp_path: Path) -> Scenario:
    code = tmp_path / "code"
    # A real top-level source directory makes absent members local move candidates.
    (code / "src").mkdir(parents=True)
    onboarding = tmp_path / "memory" / "onboarding"
    onboarding.mkdir(parents=True)
    for name, symbol in (
        ("src/left.py", "persist"),
        ("src/right.py", "persist"),
        ("src/current.py", "unique"),
        ("src/second.py", "another"),
    ):
        (code / name).write_text(f"def {symbol}():\n    return 1\n", encoding="utf-8")
    # The two citations under test name files this move removes, so the VERIFIED tree has to
    # hold them first: a relocation follows an exact name only when the extent the claim was
    # verified against is readable at the card's stamp, with the same extent kind.
    for name, symbol in (("src/gone.py", "unique"), ("src/missing.py", "another")):
        (code / name).write_text(f"def {symbol}():\n    return 0\n", encoding="utf-8")
    for args in (
        ("init", "--quiet"),
        ("config", "user.email", "fixture@example.invalid"),
        ("config", "user.name", "Fixture"),
        ("add", "--all"),
        ("commit", "--quiet", "-m", "verified"),
    ):
        Scenario.git(code, *args)
    stamp = Scenario.git(code, "rev-parse", "HEAD")
    (code / "src/gone.py").unlink()
    (code / "src/missing.py").unlink()
    return Scenario(code, onboarding, stamp)


def test_mixed_claims_publish_only_the_accepted_edit_and_history(scenario: Scenario) -> None:
    card = scenario.card("mixed.md", DECLINED, MOVED)
    result = scenario.fix()
    after = card.read_bytes()
    assert DECLINED.encode() in after
    assert b"| Single. | `unique` | src/current.py:1-2 |" in after
    assert after.count(b"Generated citation repair:") == 1
    assert b"`persist` repointed" not in after
    assert result["documentsWritten"] == result["claimsRepaired"] == result["projectionCount"] == 1
    assert result["declinedCount"] == 1
    assert result["projections"][0]["newDocumentDigest"] == sha256(after).hexdigest()
    assert result["repairs"][0]["was"] == "src/gone.py:1-2"
    again = scenario.fix()
    assert card.read_bytes() == after
    assert again["repairs"] == again["projections"] == []
    assert again["documentsWritten"] == 0
    assert again["declinedCount"] == 1


def test_preview_digest_matches_later_publication_and_binds_the_complete_batch(
    scenario: Scenario,
) -> None:
    card = scenario.card("accepted.md", MOVED, SECOND)
    before = card.read_bytes()
    preview = scenario.fix(dry_run=True)
    assert card.read_bytes() == before
    assert preview["documentsWritten"] == 0
    assert preview["projectionCount"] == preview["claimsRepaired"] == 2
    applied = scenario.fix()
    assert applied["projections"] == preview["projections"]
    assert applied["documentsWritten"] == 1
    assert all(
        p["newDocumentDigest"] == sha256(card.read_bytes()).hexdigest()
        for p in applied["projections"]
    )
    assert card.read_bytes().count(b"Generated citation repair:") == 2


def test_two_prose_source_cells_on_one_line_keep_their_original_offsets(scenario: Scenario) -> None:
    prose = (
        "First cit:([`unique`], src/gone.py:1-2) and second "
        "cit:([`another`], src/missing.py:1-2) remain distinct."
    )
    card = scenario.card("prose.md", prose)
    result = scenario.fix()
    expected = prose.replace("src/gone.py:1-2", "src/current.py:1-2").replace(
        "src/missing.py:1-2", "src/second.py:1-2"
    )
    assert expected in card.read_text()
    assert result["claimsRepaired"] == result["projectionCount"] == 2
    assert result["documentsWritten"] == 1
    assert result["ok"] is True


CHANGES: dict[str, Callable[[bytes], bytes | None]] = {
    "unrelated body": lambda body: body.replace(b"# Citation card", b"# Concurrent title"),
    "source cell": lambda body: body.replace(b"src/gone.py:1-2", b"src/other.py:7-8"),
    "cell padding": lambda body: body.replace(b" src/gone.py:1-2 ", b"  src/gone.py:1-2  "),
    "history": lambda body: body.replace(b"Original.", b"Concurrent history."),
    "truncated": lambda body: b"# Concurrent replacement\n",
    "deleted": lambda body: None,
}


@pytest.mark.parametrize("change", CHANGES)
def test_observed_conflict_refuses_the_whole_document_and_preserves_other_batches(
    scenario: Scenario, change: str
) -> None:
    conflicted = scenario.card("conflicted.md", MOVED, SECOND)
    independent = scenario.card("independent.md", MOVED)
    concurrent = CHANGES[change](conflicted.read_bytes())
    plan = projection.plan_projection

    def interleave(
        request: projection.ProjectionRequest,
    ) -> projection.Projection | projection.ProjectionDecline:
        outcome = plan(request)
        if request.relative == conflicted.name:
            if concurrent is None:
                conflicted.unlink(missing_ok=True)
            else:
                conflicted.write_bytes(concurrent)
        return outcome

    with mock.patch.object(projection, "plan_projection", side_effect=interleave):
        result = scenario.fix()
    assert (conflicted.read_bytes() if conflicted.exists() else None) == concurrent
    assert result["documentsWritten"] == result["projectionCount"] == result["claimsRepaired"] == 1
    assert result["declinedCount"] == 2
    assert {d["path"] for d in result["declined"]} == {conflicted.name}
    assert {d["code"] for d in result["declined"]} == {projection.PROJECTION_CONFLICT}
    assert {p["document"] for p in result["projections"]} == {independent.name}
    assert {r["path"] for r in result["repairs"]} == {independent.name}
    assert (
        result["projections"][0]["newDocumentDigest"]
        == sha256(independent.read_bytes()).hexdigest()
    )
    assert result["ok"] is False


def test_atomic_replace_failure_keeps_the_original_document(scenario: Scenario) -> None:
    card = scenario.card("failure.md", MOVED)
    before = card.read_bytes()
    atomic_write = document_transaction.atomic_write_bytes

    def fail_at_replace(path: Path, body: bytes) -> None:
        with mock.patch(
            "agents_remember.kernel.atomic_write.os.replace", side_effect=OSError("failed")
        ):
            atomic_write(path, body)

    with (
        mock.patch.object(
            document_transaction, "atomic_write_bytes", side_effect=fail_at_replace
        ) as writer,
        pytest.raises(OSError, match="failed"),
    ):
        scenario.fix()
    writer.assert_called_once()
    assert writer.call_args.args[0] == card
    assert card.read_bytes() == before
    assert list(scenario.onboarding.iterdir()) == [card]


def test_crlf_document_bytes_and_history_line_endings_survive(scenario: Scenario) -> None:
    card = scenario.card("crlf.md", MOVED)
    original = card.read_bytes().replace(b"\n", b"\r\n")
    card.write_bytes(original)
    result = scenario.fix()
    after = card.read_bytes()
    assert b"\n" not in after.replace(b"\r\n", b"")
    assert b"| Single. | `unique` | src/current.py:1-2 |\r\n" in after
    assert HISTORY.replace("\n", "\r\n").split("\r\n\r\n")[1].encode() in after
    assert result["projections"][0]["newDocumentDigest"] == sha256(after).hexdigest()
    assert scenario.fix()["documentsWritten"] == 0


def test_stale_explicit_snapshot_refuses_the_actual_scoped_fixer(scenario: Scenario) -> None:
    card = scenario.card("snapshot.md", MOVED)
    original = card.read_bytes()
    trees = Trees(code_root=scenario.code, memory_root=scenario.onboarding.parent)
    with source_index.open_repository_index(trees) as index:
        expected = index.snapshot_id
    (scenario.code / "src/current.py").write_text("def unique():\n    return 2\n")
    with source_index.open_repository_index(trees) as index:
        assert index.snapshot_id != expected
    with pytest.raises(source_index.SourceIndexError):
        scenario.fix(only=card.name, expected_snapshot=expected)
    assert card.read_bytes() == original


# --------------------------------------------------------------------------------------
# MIGRATION. The other document-rewriting transaction, over the same continuity authority.
# --------------------------------------------------------------------------------------

OLD_CITATION_HEADER = "| Finding | Citations | Source Path |"
NEW_CITATION_HEADER = "| Finding | Anchor | Source |"
MIGRATION_CARD = "caller.md"
MIGRATION_FINDING = "The registration entry point."
MIGRATION_ANCHOR = "`register_tool`"
CALLER = "src/caller.py"
CITED = "src/live.py"
DECLARED = "src/declared.py"


@dataclass(frozen=True)
class Migration:
    """A code tree and the superseded-format card the migration transaction rewrites."""

    code: Path
    onboarding: Path

    def source(self, name: str, body: str) -> Path:
        path = self.code / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def stamp(self) -> str:
        """Commit the verified tree and return it; evidence a move removes must exist here."""
        Scenario.git(self.code, "add", "--all")
        Scenario.git(self.code, "commit", "--quiet", "--allow-empty", "-m", "verified")
        return Scenario.git(self.code, "rev-parse", "HEAD")

    def card(self, *, stamp: str | None, cited: str = CITED) -> Path:
        """The superseded three-column card: Finding, Citations, Source Path."""
        metadata = [
            "# Citation card",
            "",
            "| Field | Value |",
            "| --- | --- |",
            "| repository | agents-remember |",
            f"| path | `{CALLER}` |",
        ]
        if stamp is not None:
            metadata.append(f"| lastVerifiedCommitHash | `{stamp}` |")
        card = self.onboarding / MIGRATION_CARD
        card.write_text(
            "\n".join(metadata) + "\n\n## Repo-Internal References\n\n"
            f"{OLD_CITATION_HEADER}\n| --- | --- | --- |\n"
            f"| {MIGRATION_FINDING} | {MIGRATION_ANCHOR} (L1-L2) | [{Path(cited).name}]({cited}) |\n",
            encoding="utf-8",
        )
        return card

    def migrate(self, **options: Any) -> dict[str, Any]:
        return migration.migrate_onboarding_root(self.onboarding, self.code, **options)

    def row(self) -> str:
        """The one citation row, located by whichever header the transaction left behind."""
        lines = (self.onboarding / MIGRATION_CARD).read_text(encoding="utf-8").splitlines()
        header = NEW_CITATION_HEADER if NEW_CITATION_HEADER in lines else OLD_CITATION_HEADER
        return lines[lines.index(header) + 2]


def build_migration(root: Path) -> Migration:
    """A migration fixture: a real code tree and the memory tree the card lives in."""
    code = root / "code"
    (code / "src").mkdir(parents=True)
    onboarding = root / "memory" / "onboarding"
    onboarding.mkdir(parents=True)
    for args in (
        ("init", "--quiet"),
        ("config", "user.email", "fixture@example.invalid"),
        ("config", "user.name", "Fixture"),
    ):
        Scenario.git(code, *args)
    return Migration(code=code, onboarding=onboarding)


@pytest.fixture
def migration_tree(tmp_path: Path) -> Migration:
    return build_migration(tmp_path)


def test_migration_declines_a_claim_whose_anchor_left_a_live_cited_file(
    migration_tree: Migration,
) -> None:
    """``anchor_left_live_file`` is the continuity refusal the migration caller can reach.

    The cited file still exists and no longer holds the anchor, so a tree-wide exact-name match
    is a different fact rather than this claim's location. The row is NOT migrated: it keeps
    the evidence its author wrote and the pointer is not moved to the declaration.
    """
    migration_tree.source(CITED, "OTHER_TOOLS = ('a', 'b')\n")
    stamp = migration_tree.stamp()
    migration_tree.source(DECLARED, "def register_tool():\n    return 1\n")
    migration_tree.card(stamp=stamp)

    result = migration_tree.migrate()

    assert result["declinedByReason"] == {"anchor_left_live_file": 1}
    assert result["rowsConverted"] == 0
    item = result["workOrders"][0]["items"][0]
    assert item["code"] == "anchor_left_live_file"
    assert CITED in item["message"]
    assert "still exists in the tree" in item["message"]
    assert f"{DECLARED}:1-2" in item["message"]
    row = migration_tree.row()
    assert CITED in row
    assert DECLARED not in row


@pytest.mark.parametrize("stamped", [True, False])
def test_migration_refuses_a_gone_cited_file_before_the_continuity_authority(
    tmp_path: Path, stamped: bool
) -> None:
    """A cited file that names no file in either tree is refused while the ROW is read.

    This is the migration caller's real answer for both origin shapes -- a card that carries a
    verification stamp and one that cannot prove an origin at all -- and it is strictly
    fail-closed: ``migration.row_paths`` declines the row before ``place`` runs, so the row
    keeps its old evidence and no pointer is moved. It also means the continuity refusal
    ``anchor_continuity_unproven`` is not reachable THROUGH this entry point; the case below
    pins that authority itself through the one gate standing in front of it.
    """
    migration_tree = build_migration(tmp_path)
    migration_tree.source(CITED, "def register_tool():\n    return 0\n")
    stamp = migration_tree.stamp() if stamped else None
    migration_tree.source(DECLARED, "def register_tool():\n    return 1\n")
    migration_tree.card(stamp=stamp)
    (migration_tree.code / CITED).unlink()

    result = migration_tree.migrate()

    assert result["declinedByReason"] == {"source_unresolvable": 1}
    assert result["rowsConverted"] == 0
    assert f"{DECLARED}:1-2" not in migration_tree.row()
    assert CITED in migration_tree.row()


def test_the_continuity_authority_decides_once_an_absent_source_reaches_placement(
    tmp_path: Path,
) -> None:
    """A SEAM TEST: it monkeypatches the gate in front of ``place``; it is not end-to-end.

    ``migration.place`` hands the document's verification provenance to the same resolver the
    fixer uses, so a tree-wide relocation is admitted only when the extent the claim was
    verified against can be read at its stamp and carries the same KIND as the extent found
    now. PRODUCTION CANNOT CURRENTLY REACH THOSE BRANCHES: ``migration.row_paths`` resolves the
    Source Path against both trees and ``plan_row`` refuses a source that names no existing
    file with ``source_unresolvable``, so every ``draft.paths`` entry handed to ``place`` is a
    path that still resolves and ``repair.plan`` can only ever answer ``anchor_left_live_file``
    (the case above pins that). The two branches below are therefore covered by REGRESSION
    PROTECTION ONLY -- they go live the moment anything upstream loosens ``row_paths``, which
    is why the wiring must not be deleted as dead code.

    Do not read a green run here as end-to-end migration coverage of a relocated file. To keep
    the seam honest it wraps ``row_paths`` with the real reader plus one rule: an expressable
    (non-URL) spelling that resolves to no file is carried through unchanged. Nothing else
    about row reading, placement, or the entry point differs, and both trees are driven through
    the real ``migrate_onboarding_root``.
    """
    unproven = build_migration(tmp_path / "unproven")
    unproven.source(CITED, "def register_tool():\n    return 0\n")
    unproven.source(DECLARED, "def register_tool():\n    return 1\n")
    unproven.card(stamp=None)
    (unproven.code / CITED).unlink()

    proven = build_migration(tmp_path / "proven")
    proven.source(CITED, "def register_tool():\n    return 0\n")
    stamp = proven.stamp()
    proven.source(DECLARED, "def register_tool():\n    return 1\n")
    proven.card(stamp=stamp)
    (proven.code / CITED).unlink()

    real = migration.row_paths

    def carry_absent_source(
        cell: str, subject: migration.Subject, run: migration.Pass
    ) -> tuple[tuple[str, ...], str]:
        paths, code = real(cell, subject, run)
        if paths:
            return paths, code
        absent = tuple(one for one in old_form.link_targets(cell) if old_form.URL_MARK not in one)
        return (absent, "") if absent else (paths, code)

    with mock.patch.object(migration, "row_paths", carry_absent_source):
        refused = unproven.migrate()
        relocated = proven.migrate()

    assert refused["declinedByReason"] == {"anchor_continuity_unproven": 1}
    assert refused["rowsConverted"] == 0
    assert CITED in unproven.row()
    assert DECLARED not in unproven.row()

    assert relocated["declinedByReason"] == {}
    assert relocated["rowsConverted"] == 1
    assert relocated["ok"] is True
    assert f"{DECLARED}:1-2" in proven.row()
    assert CITED not in proven.row()
