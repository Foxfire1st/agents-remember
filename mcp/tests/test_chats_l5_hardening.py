"""260718-CHATS-L5 evidence-backed hardening regressions.

Each test in this module reproduces the EXACT failure class named in a master hardening
obligation before proving the bounded fix, so the regression fails loudly if the guard is
ever removed. See notes/reports/260718-CHATS-L5-worker-report.md for the before/after proof.

H1 -- ``hosted_interactions`` vendor-correlation 500 breaking the whole terminal catalog sweep.
H2 -- L1 unknown-input provenance validator (see test_chats_l5_provenance_hardening below /
      characterization in the worker report).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.controlplane.operator_inbox_records import create_operator_inbox_entry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.errors import HarnessControlError
from agents_remember.serving.conversation.active.store import ProjectionStore
from agents_remember.serving.conversation.models import (
    ConversationCorrelation,
    ConversationItem,
    TextBlock,
)
from agents_remember.serving.conversation.projectors.common import (
    MappedItem,
    unknown_input_provenance,
)
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
)
from agents_remember.serving.hosted_interactions import HostedInteractionSynchronizer
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
)

NOW = "2026-07-20T10:00:00+00:00"


def _harness_entry(root: Path, session_id: str) -> TerminalCatalogEntry:
    """A running harness row with a control endpoint -- i.e. one the sweep will project + observe."""
    return TerminalCatalogEntry(
        id=session_id,
        label=f"Worker {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id="L1",
        cwd=root,
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at=NOW,
        last_attached_at=NOW,
        status="running",
        control_state="ready",
        control_endpoint=root / f"{session_id}.sock",
        control_protocol="ar-harness-control/v1",
    )


def _snapshot(entry: TerminalCatalogEntry) -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
        control="ready",
        activity="idle",
        acceptance="immediate",
    )


class _AliveHost:
    """A terminal host that reports every requested session alive without touching tmux."""

    def get(self, _sid: str) -> None:
        return None

    def has_session(self, _tmux_name: str) -> bool:
        return True


# The poisoned completion: an adapter terminal result carrying no requestId and a
# vendorCorrelationId that matches no accepted inbox row -- the exact H1 failure input.
_POISON_TRANSCRIPT = (
    {
        "sequence": 1,
        "role": "result",
        "text": "done",
        "createdAt": NOW,
        "requestId": None,
        "vendorCorrelationId": "orphan-correlation",
        "terminalResult": {"outcome": "completed", "completedAt": NOW},
    },
)


def test_h1_poisoned_completion_raises_from_synchronizer_standalone(tmp_path: Path) -> None:
    """Pin the exact failure class: the synchronizer DOES raise on the poisoned completion.

    This is deliberately the pre-fix contract of the completion-correlation path (unchanged by
    H1): a terminal result whose vendor correlation matches no accepted inbox row is a hard error.
    H1 does not soften this contract; it stops the error from taking down the whole catalog sweep.
    """
    entry = _harness_entry(tmp_path, "poison-1")
    with (
        mock.patch(
            "agents_remember.serving.hosted_interactions.read_control_transcript",
            return_value=_POISON_TRANSCRIPT,
        ),
        pytest.raises(HarnessControlError, match="does not match an accepted inbox row"),
    ):
        HostedInteractionSynchronizer(tmp_path).observe(entry, _snapshot(entry))


def test_h1_poisoned_row_is_quarantined_and_never_breaks_the_catalog_sweep(
    tmp_path: Path,
) -> None:
    """H1 regression: one poisoned hosted-session completion must not 500 the whole catalog.

    Wire the REAL ``HostedInteractionSynchronizer.observe`` as the sweep's control-snapshot
    observer -- exactly as ``app.py`` does -- with two alive harness rows: one whose transcript
    carries the poisoned completion and one whose transcript is clean. Before the H1 guard, the
    poisoned row's ``HarnessControlError`` propagated out of the per-entry list comprehension in
    ``TerminalCatalogLivenessSweeper.refresh`` (inside ``catalog.batch()``), aborting the sweep for
    EVERY row and 500-ing ``/api/terminal/sessions``. After the guard the sweep completes, the
    poison is surfaced loudly on its own row, and the healthy row projects untouched.
    """
    catalog = TerminalCatalog(tmp_path / "terminal-sessions.json")
    poison = _harness_entry(tmp_path, "poison-1")
    healthy = _harness_entry(tmp_path, "healthy-1")
    catalog.upsert(poison)
    catalog.upsert(healthy)

    def _transcript_for(entry: TerminalCatalogEntry):
        return _POISON_TRANSCRIPT if entry.id == "poison-1" else ()

    synchronizer = HostedInteractionSynchronizer(tmp_path)
    sweeper = TerminalCatalogLivenessSweeper(
        catalog,
        _AliveHost(),
        now=lambda: datetime(2026, 7, 20, tzinfo=UTC),
        config=TerminalCatalogLivenessConfig(sweep_interval_seconds=0.0),
        pane_capturer=lambda _name: "",
        snapshot_reader=_snapshot,
        on_control_snapshot=synchronizer.observe,
    )

    with mock.patch(
        "agents_remember.serving.hosted_interactions.read_control_transcript",
        side_effect=_transcript_for,
    ):
        swept = sweeper.refresh()  # MUST NOT raise; returns the projected rows

    observed_ids = {entry.id for entry in swept}
    assert observed_ids == {"poison-1", "healthy-1"}, "the whole catalog must still project"

    rows = {entry.id: entry for entry in catalog.list()}
    # The poisoned row is fail-loud on its OWN row/state, never silently swallowed.
    assert (rows["poison-1"].control_raw or {}).get("interactionSyncError")
    assert "does not match an accepted inbox row" in rows["poison-1"].control_raw["interactionSyncError"]
    # The healthy row is untouched by its neighbour's poison.
    assert "interactionSyncError" not in (rows["healthy-1"].control_raw or {})


def test_f2_quarantine_logs_on_state_change_and_heals(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """260718-CHATS-L5 F2: the H1 quarantine logs on STATE CHANGE, not on every ~10 s sweep.

    An orphan completion is the steady state of every cockpit-driven hosted chat, so the failure
    re-fires on every sweep. The warning must fire once (first occurrence), stay silent while the
    same failure persists, and emit a recovery log when it heals -- while the per-sweep marker keeps
    the wire honestly degraded for as long as the fault persists.
    """
    logger_name = "agents_remember.serving.terminal_liveness"
    catalog = TerminalCatalog(tmp_path / "terminal-sessions.json")
    catalog.upsert(_harness_entry(tmp_path, "poison-1"))
    synchronizer = HostedInteractionSynchronizer(tmp_path)
    state = {"transcript": _POISON_TRANSCRIPT}

    def _make_sweeper() -> TerminalCatalogLivenessSweeper:
        # A fresh sweeper per sweep sidesteps the intra-process rate limiter with a fixed clock.
        return TerminalCatalogLivenessSweeper(
            catalog,
            _AliveHost(),
            now=lambda: datetime(2026, 7, 20, tzinfo=UTC),
            config=TerminalCatalogLivenessConfig(sweep_interval_seconds=0.0),
            pane_capturer=lambda _name: "",
            snapshot_reader=_snapshot,
            on_control_snapshot=synchronizer.observe,
        )

    def _warnings() -> list[logging.LogRecord]:
        return [
            record
            for record in caplog.records
            if record.name == logger_name
            and "hosted interaction synchronizer failed" in record.getMessage()
        ]

    def _recoveries() -> list[logging.LogRecord]:
        return [
            record
            for record in caplog.records
            if record.name == logger_name
            and "hosted interaction synchronizer recovered" in record.getMessage()
        ]

    with (
        mock.patch(
            "agents_remember.serving.hosted_interactions.read_control_transcript",
            side_effect=lambda _entry: state["transcript"],
        ),
        caplog.at_level(logging.INFO, logger=logger_name),
    ):
        _make_sweeper().refresh()  # first occurrence: one warning
        assert len(_warnings()) == 1
        assert (catalog.list()[0].control_raw or {}).get("interactionSyncError")

        _make_sweeper().refresh()  # same failure persists: no new warning, marker still refreshed
        assert len(_warnings()) == 1, "the steady-state orphan completion must not log every sweep"
        assert (catalog.list()[0].control_raw or {}).get("interactionSyncError")

        state["transcript"] = ()  # the fault stops: heal logs once and drops the marker
        _make_sweeper().refresh()
        assert len(_recoveries()) == 1
        assert "interactionSyncError" not in (catalog.list()[0].control_raw or {})


def test_h1_healthy_completion_still_synchronizes_through_the_sweep(tmp_path: Path) -> None:
    """The quarantine guard must not suppress the normal, non-poisoned synchronizer effect."""
    catalog = TerminalCatalog(tmp_path / "terminal-sessions.json")
    entry = _harness_entry(tmp_path, "healthy-1")
    catalog.upsert(entry)

    inbox = OperatorInboxStore(tmp_path)
    inbox.append(
        create_operator_inbox_entry(
            entry_id="inbox-1",
            now=NOW,
            lifecycle_id="L1",
            agent_id="healthy-1",
            ask="Continue",
            response="Please continue",
            created_by="manager-1",
            created_via="cli",
        ).model_copy(
            update={
                "deliveryState": "delivered",
                "deliveredToSession": "healthy-1",
                "adapterDeliveryState": "accepted",
                "adapterRequestId": "inbox-1",
            }
        )
    )
    transcript = (
        {
            "sequence": 1,
            "role": "result",
            "text": "done",
            "createdAt": NOW,
            "requestId": "inbox-1",
            "terminalResult": {"outcome": "completed", "completedAt": NOW},
        },
    )
    synchronizer = HostedInteractionSynchronizer(tmp_path)
    sweeper = TerminalCatalogLivenessSweeper(
        catalog,
        _AliveHost(),
        now=lambda: datetime(2026, 7, 20, tzinfo=UTC),
        config=TerminalCatalogLivenessConfig(sweep_interval_seconds=0.0),
        pane_capturer=lambda _name: "",
        snapshot_reader=_snapshot,
        on_control_snapshot=synchronizer.observe,
    )
    with mock.patch(
        "agents_remember.serving.hosted_interactions.read_control_transcript",
        return_value=transcript,
    ):
        sweeper.refresh()

    completed = inbox.current()["inbox-1"]
    assert completed.adapterDeliveryState == "completed"
    assert "interactionSyncError" not in (catalog.list()[0].control_raw or {})


# ---------------------------------------------------------------------------
# H2 -- L1 unknown-input provenance validator 500 (L4 verdict E2)
# ---------------------------------------------------------------------------


def _unknown_input_user_item(item_id: str, request_id: str, text: str) -> ConversationItem:
    """A fresh native-history codex user re-map: the honest unknown-input default."""
    return ConversationItem(
        item_id=item_id,
        revision=1,
        global_ordinal=1,
        turn_id="turn-1",
        lane="unknown-input",
        source="native-history",
        provenance=unknown_input_provenance("codex", observed_at=NOW),
        role="user",
        kind="message",
        phase="completed",
        blocks=(TextBlock(block_id="text-0", text=text),),
        correlation=ConversationCorrelation(request_id=request_id),
    )


def _revalidate(item: ConversationItem) -> None:
    """Re-validate an item exactly as the active-page route does when serializing a response.

    ``ProjectionStore`` builds items via ``model_copy(update=...)`` which, in pydantic v2, does NOT
    re-run ``model_validator`` -- so an authority-inconsistent item is stored silently and only
    500s later at the route's response re-validation. This mirrors that boundary.
    """
    ConversationItem.model_validate(item.model_dump(by_alias=True))


def test_h2_native_remap_after_resolution_never_splits_input_authority(tmp_path: Path) -> None:
    """H2 regression: a native re-map after provenance resolution must stay model-valid.

    Reproduces L4 verdict E2 at the store where it originates: (1) a codex user message arrives as
    honest ``unknown-input``/``native-only``; (2) its provenance resolves to ``cockpit`` -> the
    store flips it to ``operator``/``cockpit-composer``/``exact``; (3) codex re-maps the SAME native
    user item (an ordinary full-item re-map under real traffic), which re-emits the ``unknown-input``
    default. Before the H2 fix the upsert adopted the candidate's ``unknown-input`` lane/source while
    preserving the resolved ``exact`` provenance, producing an item that violates
    ``preserve_input_authority`` -- stored silently, then 500-ing the active-page route at response
    re-validation. After the fix the resolved authority triple is preserved intact.
    """
    del tmp_path
    store = ProjectionStore()
    store.apply_item(MappedItem(_unknown_input_user_item("turn-1-user", "req-turn-1", "go")))

    # Provenance resolves to a real submission source -> operator/cockpit-composer/exact.
    store.apply_provenance("req-turn-1", "cockpit")
    resolved = store.items()[0]
    assert resolved.lane == "operator"
    assert resolved.provenance.strength == "exact"
    _revalidate(resolved)

    # Codex re-maps the same native user item -> a fresh unknown-input candidate.
    store.apply_item(MappedItem(_unknown_input_user_item("turn-1-user", "req-turn-1", "go")))

    remapped = store.items()[0]
    # The resolved input authority must not be reverted OR split by the re-map.
    assert remapped.item_id == "turn-1-user"
    assert remapped.lane == "operator", "a native re-map must not revert a resolved lane"
    assert remapped.source == "cockpit-composer"
    assert remapped.provenance.strength == "exact"
    # The core assertion: the stored item is model-valid (would not 500 the active-page route).
    _revalidate(remapped)


def test_h2_unresolved_user_item_stays_honest_unknown_input(tmp_path: Path) -> None:
    """The fix must not disturb the honest path: an unresolved user item stays unknown-input.

    When no provenance record is found (``source=None``), the item must remain
    ``unknown-input``/``native-only`` across re-maps -- never guessed into an authority (R6.4).
    """
    del tmp_path
    store = ProjectionStore()
    store.apply_item(MappedItem(_unknown_input_user_item("turn-9-user", "req-turn-9", "hello")))
    store.apply_provenance("req-turn-9", None)  # not-found -> stays honest
    store.apply_item(MappedItem(_unknown_input_user_item("turn-9-user", "req-turn-9", "hello world")))

    item = store.items()[0]
    assert item.lane == "unknown-input"
    assert item.source == "native-history"
    assert item.provenance.strength == "native-only"
    assert item.provenance.producer is None
    _revalidate(item)


def test_f4_identical_remap_after_resolution_is_a_true_no_op(tmp_path: Path) -> None:
    """260718-CHATS-L5 F4: an identical native re-map of a resolved user item is a true no-op.

    After provenance resolution a codex native re-map re-emits the ``unknown-input``/``native-history``
    default -- it differs from the resolved item ONLY in the authority fields a re-map must never own
    (which ``_preserved_input_authority`` restores). The store's idempotence docstring promises a
    re-fed identical item reproduces the identical projection, so this re-map must emit NO mutation
    and NOT bump the revision, rather than a redundant revision-bumping ``upsert-item``.
    """
    del tmp_path
    store = ProjectionStore()
    store.apply_item(MappedItem(_unknown_input_user_item("turn-1-user", "req-turn-1", "go")))
    store.apply_provenance("req-turn-1", "cockpit")
    resolved = store.items()[0]
    assert resolved.lane == "operator"
    baseline_revision = resolved.revision

    mutations = store.apply_item(
        MappedItem(_unknown_input_user_item("turn-1-user", "req-turn-1", "go"))
    )
    assert mutations == [], "an identical resolved-user re-map must not emit a redundant upsert"
    after = store.items()[0]
    assert after.revision == baseline_revision, "an identical re-map must not bump the revision"
    assert after.lane == "operator"
    assert after.source == "cockpit-composer"
    assert after.provenance.strength == "exact"
    _revalidate(after)
