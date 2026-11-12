"""The rule-8 generated-master selection test (L126-CODE-07).

On a generated 100,000-file nested master, an actual selected-task change issues the
page's exact bounded request set — the root folder first part, one reference
resolution, one recorded-reports listing — and the aggregate answer bytes stay under a
fixed declared ceiling. The same request set against a small master with identical
root entries answers byte-identical bytes, proving response size does not grow with
descendant count. A noteless master is preserved alongside.

The tree is built fresh in tmp (100 top dirs x 100 subdirs x 10 files; ~3s), so the
test is hermetic and retained. The full mounted BEFORE/AFTER comparison remains the
separate Owner-occasion AFTER run, not this test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.cli.role_report import list_task_reports
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.serving.notes import list_folder, resolve_notes_references

REPO = "R"
BIG = "260101_big"
SMALL = "260101_small"
EMPTY = "260101_empty"

# The fixed declared ceiling for one selected-task change's notes answers.
SELECTION_BYTES_CEILING = 1_000_000

REFERENCES = ["see top.md for the overview", "missing-note.md"]


def _config(tmp_path: Path) -> McpRuntimeConfig:
    code_root = tmp_path / "ws" / REPO
    code_root.mkdir(parents=True, exist_ok=True)
    return McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path / "coord",
        workspace_root=tmp_path / "ws",
        transcript_root=tmp_path / "logs",
        repositories={REPO: RepositoryScope(repo_id=REPO, path=code_root)},
    )


def _notes(config: McpRuntimeConfig, master: str) -> Path:
    root = config.coordination_root / "tasks" / REPO / master / "notes"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _build_big(root: Path) -> int:
    """100 top dirs x 100 subdirs x 10 files of filler: 100,000 nested notes."""
    count = 0
    for top in range(100):
        for sub in range(100):
            folder = root / f"d{top:03d}" / f"s{sub:03d}"
            folder.mkdir(parents=True)
            for index in range(10):
                (folder / f"note-{top:03d}-{sub:03d}-{index:02d}.md").write_text("filler\n")
                count += 1
    (root / "top.md").write_text("overview\n")
    return count + 1


def _build_small(root: Path) -> None:
    """Same 100 root directory names, one file each: identical root answers."""
    for top in range(100):
        folder = root / f"d{top:03d}"
        folder.mkdir(parents=True)
        (folder / "seed.md").write_text("filler\n")
    (root / "top.md").write_text("overview\n")


def _wire(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _selection_parts(config: McpRuntimeConfig, master: str, doc: str) -> list[dict[str, object]]:
    """The page's exact request set for one selected-task change, decoded."""
    folder = list_folder(config, REPO, master, "", 0)
    resolved = resolve_notes_references(config, REPO, master, REFERENCES)
    reports = json.loads(bytes(list_task_reports(config, REPO, master, doc).body).decode("utf-8"))
    return [folder, resolved, reports]


def _selection_bytes(parts: list[dict[str, object]]) -> bytes:
    return b"".join(_wire(part) for part in parts)


def _master_free(parts: list[dict[str, object]]) -> bytes:
    """The same answers with master-dependent names masked: what remains is provably
    independent of which master — big or small — answered."""
    return b"".join(
        _wire({key: ("" if key in {"master", "doc"} else value) for key, value in part.items()})
        for part in parts
    )


@pytest.fixture(scope="module")
def masters(tmp_path_factory: pytest.TempPathFactory) -> tuple[McpRuntimeConfig, int]:
    tmp_path = tmp_path_factory.mktemp("generated")
    config = _config(tmp_path)
    big_files = _build_big(_notes(config, BIG))
    _build_small(_notes(config, SMALL))
    # EMPTY keeps no notes folder at all: the young-series answer, not an empty folder.
    assert big_files == 100_001
    return config, big_files


def test_selection_change_bytes_are_bounded_and_independent_of_tree_size(
    masters: tuple[McpRuntimeConfig, int],
) -> None:
    config, big_files = masters
    assert big_files >= 100_000
    big_parts = _selection_parts(config, BIG, f"{BIG}/task.json")
    small_parts = _selection_parts(config, SMALL, f"{SMALL}/task.json")
    big = _selection_bytes(big_parts)
    assert len(big) < SELECTION_BYTES_CEILING
    # Identical root entries answer identical bytes once master-dependent names are
    # masked: nothing in the answers grows with the 100,000 nested descendants. (Raw
    # lengths differ only by the master name string itself.)
    assert _master_free(big_parts) == _master_free(small_parts)


def test_selection_change_on_a_noteless_master_stays_bounded(
    masters: tuple[McpRuntimeConfig, int],
) -> None:
    (config, _) = masters
    empty = _selection_bytes(_selection_parts(config, EMPTY, f"{EMPTY}/task.json"))
    assert len(empty) < SELECTION_BYTES_CEILING
    folder = list_folder(config, REPO, EMPTY, "", 0)
    assert folder["exists"] is False and folder["entries"] == []
    resolved = resolve_notes_references(config, REPO, EMPTY, REFERENCES)
    assert [item["path"] for item in resolved["resolutions"]] == [None, None]


# The retained generated-master relation for the mounted selection-transition test
# (dashboard/src/panels/taskPageCost.test.tsx): the answers the mounted change is
# served are byte-identical to these masters' live answers. The pinned fixture is
# regenerated only by rebuilding this exact tree and re-capturing; this test
# re-verifies it on every run, so the mounted proof cannot silently drift off the
# generated master. Provenance: built fresh above (100 top x 100 sub x 10 files),
# captured with the component's own request shapes (root first part, empty resolve,
# per-master doc reports).
PINNED_SELECTION_ANSWERS = (
    Path(__file__).resolve().parent.parent.parent
    / "dashboard"
    / "src"
    / "panels"
    / "__fixtures__"
    / "generatedSelectionChangeAnswers.json"
)


def _live_selection_answers(config: McpRuntimeConfig) -> dict[str, object]:
    live: dict[str, object] = {}
    for master in (BIG, SMALL):
        folder = list_folder(config, REPO, master, "", 0)
        resolved = resolve_notes_references(config, REPO, master, [])
        reports = json.loads(
            bytes(list_task_reports(config, REPO, master, f"{master}/task.json").body).decode(
                "utf-8"
            )
        )
        live[master] = {"folder": folder, "resolve": resolved, "reports": reports}
    return live


def test_pinned_selection_answers_match_live_generated_masters(
    masters: tuple[McpRuntimeConfig, int],
) -> None:
    (config, _) = masters
    pinned = json.loads(PINNED_SELECTION_ANSWERS.read_text(encoding="utf-8"))
    assert pinned["generator"]["files"] == 100_001
    assert _live_selection_answers(config) == {
        master: {key: pinned["masters"][master][key] for key in ("folder", "resolve", "reports")}
        for master in (BIG, SMALL)
    }
