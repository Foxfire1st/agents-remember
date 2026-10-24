"""MIK-R199 round-1 finding L192-F04: retired acknowledgement bytes must be physically reclaimed.

``AttentionDismissalRecord`` now refuses the retired provider kinds (``provider-down`` and the
provider-setup-only ``failed-setup``) on read and admission -- that part was already correct. But
the tolerant read *drops* those raw lines before ``reconcile`` compares records with kept, so a
file holding only retired rows folded to nothing, compared empty against empty, and never called the
rewrite: the bytes stayed on disk and were re-read and re-parsed on every tick, forever, with no
external input.

The repair counts the raw rows the fold did not read and forces the owner's physical rewrite when
any were dropped, including the all-retired case. A clean file with nothing dropped and nothing
reclaimed is still left untouched, and the live-acknowledgement and incomplete-observation rules are
unchanged.

Before the repair the reclamation case leaves the retired rows in the file; the fixed-point and the
live-control cases pass either way.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalStore,
    AttentionObservation,
)

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)
RETIRED = ("provider-down", "failed-setup")


def _row(item_id: str, kind: str, *, minutes_ago: int = 60) -> str:
    stamp = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return json.dumps(
        {
            "schema": "ar-attention-dismissal/v1",
            "itemId": item_id,
            "kind": kind,
            "dismissedAt": stamp,
        }
    )


def _seed(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


@pytest.mark.parametrize("kind", RETIRED)
@pytest.mark.parametrize("size", (1, 200))
def test_only_retired_rows_are_physically_removed(tmp_path: Path, kind: str, size: int) -> None:
    store = AttentionDismissalStore(tmp_path)
    _seed(store.log_path(), [_row(f"{kind}:legacy-{index}", kind) for index in range(size)])

    kept = store.reconcile([])

    assert kept == {}
    assert store.log_path().read_text() == "", "the retired rows stayed on disk"
    assert store.read() == []


@pytest.mark.parametrize("kind", RETIRED)
def test_a_retired_row_beside_a_live_row_is_removed_and_the_live_row_survives(
    tmp_path: Path, kind: str
) -> None:
    store = AttentionDismissalStore(tmp_path)
    _seed(
        store.log_path(),
        [
            _row(f"{kind}:legacy", kind),
            _row("actionable-drift:repo-a", "actionable-drift"),
        ],
    )

    kept = store.reconcile([])

    assert set(kept) == {"actionable-drift:repo-a"}
    assert kind not in store.log_path().read_text()
    assert {record.itemId for record in store.read()} == {"actionable-drift:repo-a"}


def test_a_clean_file_settles_and_is_not_rewritten_again(tmp_path: Path) -> None:
    store = AttentionDismissalStore(tmp_path)
    _seed(store.log_path(), [_row("actionable-drift:repo-a", "actionable-drift")])
    store.reconcile([])
    settled = store.log_path().read_text()
    before = store.log_path().stat().st_mtime_ns

    store.reconcile([])

    assert store.log_path().read_text() == settled
    assert store.log_path().stat().st_mtime_ns == before, "an unchanged clean file was rewritten"


def test_an_incomplete_observation_still_cannot_reclaim_a_live_row(tmp_path: Path) -> None:
    store = AttentionDismissalStore(tmp_path)
    _seed(store.log_path(), [_row("actionable-drift:repo-a", "actionable-drift")])
    incomplete = AttentionObservation(kind="actionable-drift", observed_before=NOW, complete=False)

    kept = store.reconcile([incomplete])

    assert set(kept) == {"actionable-drift:repo-a"}
    assert {record.itemId for record in store.read()} == {"actionable-drift:repo-a"}


def test_a_complete_observation_reclaims_only_what_it_no_longer_observes(tmp_path: Path) -> None:
    store = AttentionDismissalStore(tmp_path)
    _seed(
        store.log_path(),
        [
            _row("blocked-start:gone", "blocked-start"),
            _row("blocked-start:live", "blocked-start"),
        ],
    )
    observation = AttentionObservation(kind="blocked-start", observed_before=NOW)
    observation.observe("blocked-start:live")

    kept = store.reconcile([observation])

    assert set(kept) == {"blocked-start:live"}
    assert {record.itemId for record in store.read()} == {"blocked-start:live"}
