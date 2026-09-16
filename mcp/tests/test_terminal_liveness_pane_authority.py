"""Pane classification stays diagnostic-only for turn and terminal truth.

The adapter control snapshot and the canonical terminal-evidence projection own the
catalog's turn state, terminal outcome, terminal identity, interruption origin and
state-signal eligibility. A captured pane's own reading may only be persisted as the
row's ``paneDiagnostic`` detail.
"""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlState,
)
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
)
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
)
from agents_remember.serving.terminal_evidence import TerminalEvidenceRead
from agents_remember.serving.terminal_liveness import (
    LivenessProbe,
    PaneCapturer,
    SnapshotReader,
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
    TerminalEvidenceReader,
    observe_terminal_liveness,
)
from agents_remember.serving.terminal_tmux import TmuxProbeResult
from test_terminal_liveness import _Clock, _entry, _FakeHost, _ready_snapshot, _snapshot

TERMINAL_LIVENESS_SOURCE = MCP_SRC / "agents_remember" / "serving" / "terminal_liveness.py"


CODEX_IDLE_PANE = "\u203a"
"""Codex's empty composer: the pane's own reading of it is ``turn-ended``."""
CODEX_WORKING_PANE = "esc to interrupt"
"""A Codex busy marker: the pane's own reading of it is ``working``."""
CODEX_BLOCKED_PANE = "do you want to proceed? (y/n)"
"""A Codex permission modal: the pane's own reading of it is ``awaiting-input``."""

_PANE_AUTHORITY_FIELDS = frozenset(
    {
        "control_acceptance",
        "control_activity",
        "control_state",
        "interrupted_by",
        "state_signal_emitted_for",
        "terminal_evidence_id",
        "terminal_outcome",
        "terminal_outcome_at",
        "turn_state",
        "turn_state_changed_at",
    }
)
"""Catalog fields only a canonical adapter snapshot or terminal-evidence projection may write."""

_PANE_WRITER_KEYWORDS = {"_record_adapter_turn_state": frozenset({"state"})}
"""Writer keywords whose argument lands in an authoritative turn/terminal field."""

_PANE_CLASSIFIERS = frozenset({"capture_pane", "classify_pane_signal", "classify_turn_state"})
"""Calls whose result is a captured pane reading, never adapter or terminal evidence."""


def _pane(text: str) -> PaneCapturer:
    """A capturer that always returns one fixed pane reading."""
    return lambda _tmux_name: text


def _pane_diagnostic(entry: TerminalCatalogEntry) -> object:
    """The row's persisted pane reading, or ``None`` when it carries no diagnostic."""
    return (entry.control_raw or {}).get("paneDiagnostic")


def _connected_entry(session_id: str, **overrides: object) -> TerminalCatalogEntry:
    """A harness row that already projected a live control bridge."""
    defaults: dict[str, object] = {
        "control_state": "ready",
        "control_endpoint": Path(f"/tmp/{session_id}.sock"),
        "control_activity": "idle",
        "control_acceptance": "immediate",
    }
    return replace(_entry(session_id), **{**defaults, **overrides})


def _booting_entry(session_id: str) -> TerminalCatalogEntry:
    """A harness row whose control bridge has not connected yet."""
    return replace(
        _entry(session_id),
        control_state="starting",
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
    )


def _running_snapshot(entry: TerminalCatalogEntry) -> AdapterSnapshot:
    """The canonical adapter snapshot of a bridge inside an active turn."""
    return _snapshot(entry, control="ready", activity="running", acceptance="immediate")


def _degraded_snapshot(entry: TerminalCatalogEntry, control: ControlState) -> AdapterSnapshot:
    """The canonical adapter snapshot of a bridge the adapter reports dropped or failed."""
    return _snapshot(entry, control=control, activity="unknown", acceptance="unknown")


def _no_terminal_evidence(_entry: TerminalCatalogEntry) -> TerminalEvidenceRead:
    """A terminal surface with nothing new to claim and no cursor to advance."""
    return TerminalEvidenceRead(projection=None)


def _failed_snapshot(_entry: TerminalCatalogEntry) -> AdapterSnapshot:
    raise HarnessControlError("bridge unavailable")


def _failed_terminal_read(_entry: TerminalCatalogEntry) -> TerminalEvidenceRead:
    raise HarnessControlError("terminal evidence page unavailable")


def _forbidden_control_read(_entry: TerminalCatalogEntry) -> AdapterSnapshot:
    """A control read a legacy row must never reach: it has no control surface to read."""
    raise AssertionError("a row without a control_endpoint has no bridge to read")


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_pane_expression(node: ast.AST, tainted: set[str]) -> bool:
    """Whether one expression's value derives from captured pane text."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and (child.id in tainted or "pane" in child.id.lower()):
            return True
        if isinstance(child, ast.Attribute) and "pane" in child.attr.lower():
            return True
        if isinstance(child, ast.Constant) and "pane" in str(child.value).lower():
            return True
        if isinstance(child, ast.Call) and _call_name(child) in _PANE_CLASSIFIERS:
            return True
    return False


def _assignment_values(tree: ast.AST) -> list[tuple[list[str], ast.expr]]:
    """Every simple assignment's target names and its value expression."""
    found: list[tuple[list[str], ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if names:
                found.append((names, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                found.append(([node.target.id], node.value))
    return found


def _pane_derived_names(tree: ast.AST) -> set[str]:
    """Every local name that transitively carries a captured pane reading."""
    assignments = _assignment_values(tree)
    tainted: set[str] = set()
    growing = True
    while growing:
        growing = False
        for names, value in assignments:
            if not _is_pane_expression(value, tainted):
                continue
            fresh = [name for name in names if name not in tainted]
            tainted.update(fresh)
            growing = growing or bool(fresh)
    return tainted


def _keyword_writes(node: ast.Call, tainted: set[str]) -> list[str]:
    """Authoritative-field keyword arguments on one call that carry a pane reading."""
    writer = _call_name(node) or ""
    authoritative = _PANE_AUTHORITY_FIELDS | _PANE_WRITER_KEYWORDS.get(writer, frozenset())
    return [
        f"line {node.lineno}: {keyword.arg}=<pane reading>"
        for keyword in node.keywords
        if keyword.arg in authoritative and _is_pane_expression(keyword.value, tainted)
    ]


def _dict_writes(node: ast.Dict, tainted: set[str]) -> list[str]:
    """Authoritative-field dict keys on one literal whose value carries a pane reading."""
    return [
        f"line {node.lineno}: [{key.value!r}]=<pane reading>"
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant)
        and key.value in _PANE_AUTHORITY_FIELDS
        and _is_pane_expression(value, tainted)
    ]


def _assign_writes(node: ast.Assign, tainted: set[str]) -> list[str]:
    """Authoritative-field attribute stores on one assignment that carry a pane reading."""
    return [
        f"line {node.lineno}: .{target.attr}=<pane reading>"
        for target in node.targets
        if isinstance(target, ast.Attribute)
        and target.attr in _PANE_AUTHORITY_FIELDS
        and _is_pane_expression(node.value, tainted)
    ]


def _authoritative_writes(node: ast.AST, tainted: set[str]) -> list[str]:
    """Authoritative-field writes on one node that would make a pane reading authoritative."""
    if isinstance(node, ast.Call):
        return _keyword_writes(node, tainted)
    if isinstance(node, ast.Dict):
        return _dict_writes(node, tainted)
    if isinstance(node, ast.Assign):
        return _assign_writes(node, tainted)
    return []


def _pane_authority_offenders(source: str) -> list[str]:
    """Every write in one module that would give a pane reading turn/terminal authority."""
    tree = ast.parse(source)
    tainted = _pane_derived_names(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        offenders += _authoritative_writes(node, tainted)
    return offenders


class PaneDiagnosticAuthorityTests(unittest.TestCase):
    """Pane classification stays diagnostic; adapter snapshots and evidence own turn truth."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.clock = _Clock(datetime(2026, 7, 7, tzinfo=UTC))
        self.host = _FakeHost(TmuxProbeResult(exists=True, evidence="alive"))

    def tearDown(self) -> None:
        self._dir.cleanup()

    def _sweeper(
        self,
        *,
        pane_capturer: PaneCapturer,
        snapshot_reader: SnapshotReader,
        terminal_reader: TerminalEvidenceReader = _no_terminal_evidence,
        sweep_interval_seconds: float = 0.0,
    ) -> TerminalCatalogLivenessSweeper:
        return TerminalCatalogLivenessSweeper(
            self.catalog,
            self.host,
            now=self.clock,
            probe=LivenessProbe(
                hysteresis=TerminalCatalogLivenessConfig(
                    failure_threshold=3,
                    minimum_failure_window_seconds=5.0,
                    pane_gone_failure_threshold=1,
                    sweep_interval_seconds=sweep_interval_seconds,
                ),
                pane_capturer=pane_capturer,
                snapshot_reader=snapshot_reader,
                terminal_reader=terminal_reader,
            ),
        )

    def _row(self, session_id: str) -> TerminalCatalogEntry:
        row = self.catalog.get(session_id)
        assert row is not None
        return row

    def test_pane_and_adapter_disagreement_keeps_turn_truth_adapter_owned(self) -> None:
        self.catalog.upsert(_connected_entry("pane-idle", control_activity="running"))
        self.catalog.upsert(
            _connected_entry(
                "pane-working",
                turn_state="working",
                turn_state_changed_at="2026-07-07T00:00:00+00:00",
            )
        )

        def snapshot_reader(entry: TerminalCatalogEntry) -> AdapterSnapshot:
            return _running_snapshot(entry) if entry.id == "pane-idle" else _ready_snapshot(entry)

        sweeper = self._sweeper(
            pane_capturer=lambda tmux_name: (
                CODEX_IDLE_PANE if tmux_name == "ar-pane-idle" else CODEX_WORKING_PANE
            ),
            snapshot_reader=snapshot_reader,
        )
        sweeper.refresh()

        idle_pane_row = self._row("pane-idle")
        # The pane reads turn-ended; the adapter's running turn still owns the seat state.
        self.assertEqual(idle_pane_row.turn_state, "working")
        self.assertEqual(_pane_diagnostic(idle_pane_row), "turn-ended")
        working_pane_row = self._row("pane-working")
        # The pane reads working; the adapter's settled turn still ends it.
        self.assertEqual(working_pane_row.turn_state, "turn-ended")
        self.assertEqual(_pane_diagnostic(working_pane_row), "working")

    def test_a_pane_reading_cannot_authorize_readiness_over_the_adapter(self) -> None:
        for session_id in ("readiness-disconnected", "readiness-failed"):
            self.catalog.upsert(_connected_entry(session_id, control_activity="running"))

        def snapshot_reader(entry: TerminalCatalogEntry) -> AdapterSnapshot:
            if entry.id == "readiness-failed":
                return _degraded_snapshot(entry, "failed")
            return _degraded_snapshot(entry, "disconnected")

        sweeper = self._sweeper(
            pane_capturer=_pane(CODEX_WORKING_PANE),
            snapshot_reader=snapshot_reader,
        )
        sweeper.refresh()

        # Both rows keep reading "working" on the pane. Readiness, activity and acceptance stay
        # the adapter's dropped/failed projection, and the turn claim stays non-terminal: a pane
        # reading never authorizes a dispatchable seat over a bridge the adapter reported gone.
        for session_id, control in (
            ("readiness-disconnected", "disconnected"),
            ("readiness-failed", "failed"),
        ):
            with self.subTest(control=control):
                row = self._row(session_id)
                self.assertEqual(row.control_state, control)
                self.assertEqual(row.control_activity, "unknown")
                self.assertEqual(row.control_acceptance, "unknown")
                self.assertEqual(row.turn_state, "stale")
                self.assertEqual(_pane_diagnostic(row), "working")

    def test_connected_row_keeps_its_control_and_turn_truth_below_the_read_threshold(self) -> None:
        self.catalog.upsert(
            _connected_entry(
                "retained",
                control_activity="running",
                turn_state="working",
                turn_state_changed_at="2026-07-07T00:00:00+00:00",
            )
        )
        sweeper = self._sweeper(
            pane_capturer=_pane(CODEX_IDLE_PANE),
            snapshot_reader=_failed_snapshot,
        )

        for expected_failures in (1, 2):
            sweeper.refresh()
            row = self._row("retained")
            assert row.control_raw is not None
            self.assertEqual(row.control_state, "ready")
            self.assertEqual(row.control_activity, "running")
            self.assertEqual(row.turn_state, "working")
            self.assertEqual(row.turn_state_changed_at, "2026-07-07T00:00:00+00:00")
            self.assertEqual(row.control_raw.get("controlReadFailures"), expected_failures)
            # The pane's contradicting turn-ended reading stays a diagnostic only.
            self.assertEqual(_pane_diagnostic(row), "turn-ended")

    def test_the_failure_threshold_owns_the_stale_transition_not_the_pane(self) -> None:
        self.catalog.upsert(
            _connected_entry(
                "threshold",
                control_activity="running",
                turn_state="working",
                turn_state_changed_at="2026-07-07T00:00:00+00:00",
            )
        )
        sweeper = self._sweeper(
            pane_capturer=_pane(CODEX_WORKING_PANE),
            snapshot_reader=_failed_snapshot,
        )

        for _ in range(2):
            sweeper.refresh()
        connected = self._row("threshold")
        self.assertEqual((connected.control_state, connected.turn_state), ("ready", "working"))

        sweeper.refresh()
        stale = self._row("threshold")
        assert stale.control_raw is not None
        self.assertEqual(stale.control_state, "disconnected")
        self.assertEqual(stale.control_activity, "unknown")
        self.assertEqual(stale.control_acceptance, "unknown")
        self.assertEqual(stale.turn_state, "stale")
        self.assertEqual(stale.control_raw.get("controlReadFailures"), 3)
        self.assertIsNone(stale.terminal_outcome)
        self.assertIsNone(stale.terminal_evidence_id)
        # The pane never stopped reading "busy": the strike count owns the transition.
        self.assertEqual(_pane_diagnostic(stale), "working")

    def test_alive_starting_row_keeps_its_prior_turn_truth_through_any_read_failure(self) -> None:
        self.catalog.upsert(
            replace(
                _booting_entry("booting"),
                turn_state="awaiting-input",
                turn_state_changed_at="2026-07-07T00:00:00+00:00",
            )
        )
        sweeper = self._sweeper(
            pane_capturer=_pane(CODEX_WORKING_PANE),
            snapshot_reader=_failed_snapshot,
        )

        for expected_failures in range(1, 6):
            sweeper.refresh()
            row = self._row("booting")
            assert row.control_raw is not None
            self.assertEqual(row.control_state, "starting")
            self.assertEqual(row.turn_state, "awaiting-input")
            self.assertEqual(row.turn_state_changed_at, "2026-07-07T00:00:00+00:00")
            self.assertEqual(row.control_raw.get("controlReadFailures"), expected_failures)
            self.assertEqual(_pane_diagnostic(row), "working")

    def test_legacy_row_without_a_control_endpoint_projects_stale_for_any_pane(self) -> None:
        self.catalog.upsert(
            replace(
                _entry("legacy-idle"),
                control_state="ready",
                turn_state="turn-ended",
                turn_state_changed_at="2026-07-07T00:00:00+00:00",
            )
        )
        self.catalog.upsert(_entry("legacy-working"))
        sweeper = self._sweeper(
            pane_capturer=lambda tmux_name: (
                CODEX_IDLE_PANE if tmux_name == "ar-legacy-idle" else CODEX_WORKING_PANE
            ),
            snapshot_reader=_forbidden_control_read,
        )
        sweeper.refresh()

        readings = (("legacy-idle", "turn-ended"), ("legacy-working", "working"))
        for session_id, pane_reading in readings:
            with self.subTest(session=session_id):
                row = self._row(session_id)
                self.assertEqual(row.control_state, "unsupported")
                self.assertEqual(row.control_activity, "unknown")
                self.assertEqual(row.control_acceptance, "unsupported")
                self.assertEqual(row.turn_state, "stale")
                self.assertEqual(_pane_diagnostic(row), pane_reading)
                self.assertIsNone(row.terminal_outcome)
                self.assertIsNone(row.terminal_outcome_at)
                self.assertIsNone(row.terminal_evidence_id)
                self.assertIsNone(row.interrupted_by)

    def test_failed_terminal_read_advances_no_terminal_truth_but_keeps_turn_truth(self) -> None:
        self.catalog.upsert(
            _connected_entry(
                "terminal-failed",
                control_activity="running",
                terminal_outcome="completed",
                terminal_outcome_at="2026-07-07T00:00:05+00:00",
                terminal_evidence_id="turn-41",
                terminal_evidence_sequence=41,
            )
        )
        sweeper = self._sweeper(
            pane_capturer=_pane(CODEX_IDLE_PANE),
            snapshot_reader=_running_snapshot,
            terminal_reader=_failed_terminal_read,
        )
        sweeper.refresh()

        row = self._row("terminal-failed")
        self.assertEqual(row.terminal_outcome, "completed")
        self.assertEqual(row.terminal_outcome_at, "2026-07-07T00:00:05+00:00")
        self.assertEqual(row.terminal_evidence_id, "turn-41")
        self.assertEqual(row.terminal_evidence_sequence, 41)
        self.assertIsNone(row.interrupted_by)
        # The separately successful canonical snapshot still projects non-terminal turn truth.
        self.assertEqual(row.turn_state, "working")
        self.assertEqual(_pane_diagnostic(row), "turn-ended")

    def test_startup_prime_and_steady_pass_share_one_pane_diagnostic_boundary(self) -> None:
        self.catalog.upsert(_booting_entry("steady"))

        def snapshot_reader(entry: TerminalCatalogEntry) -> AdapterSnapshot:
            return (
                _ready_snapshot(entry) if entry.id == "prime-active" else _running_snapshot(entry)
            )

        sweeper = self._sweeper(
            pane_capturer=lambda tmux_name: (
                CODEX_WORKING_PANE if tmux_name == "ar-prime-active" else CODEX_IDLE_PANE
            ),
            snapshot_reader=snapshot_reader,
            sweep_interval_seconds=10.0,
        )
        sweeper.refresh()
        steady = self._row("steady")
        self.assertEqual(
            (steady.control_state, steady.turn_state, _pane_diagnostic(steady)),
            ("ready", "working", "turn-ended"),
        )

        # Both late rows appear inside the sweep blackout, so this refresh takes the
        # starting-row fast path rather than the steady full sweep over every row.
        self.catalog.upsert(_booting_entry("prime-idle"))
        self.catalog.upsert(_booting_entry("prime-active"))
        sweeper.refresh()
        self.assertEqual(self.host.calls, 3)

        prime_idle = self._row("prime-idle")
        self.assertEqual(
            (prime_idle.control_state, prime_idle.turn_state, _pane_diagnostic(prime_idle)),
            ("ready", "working", "turn-ended"),
        )
        prime_active = self._row("prime-active")
        # The pane reads working and the adapter has settled: the pane makes no claim.
        self.assertEqual(
            (prime_active.control_state, prime_active.turn_state, _pane_diagnostic(prime_active)),
            ("ready", None, "working"),
        )

    def test_no_pane_reading_changes_a_turn_or_terminal_field(self) -> None:
        readings = (CODEX_IDLE_PANE, CODEX_WORKING_PANE, CODEX_BLOCKED_PANE, "")
        authoritative: list[tuple[object, ...]] = []
        diagnostics: list[object] = []
        for index, pane_text in enumerate(readings):
            row = self._sweep_one_pane_reading(pane_text, index)
            authoritative.append(
                (
                    row.turn_state,
                    row.turn_state_changed_at,
                    row.terminal_outcome,
                    row.terminal_outcome_at,
                    row.terminal_evidence_id,
                    row.interrupted_by,
                    row.state_signal_emitted_for,
                )
            )
            diagnostics.append(_pane_diagnostic(row))

        # The matrix is not vacuous: each reading classified, and every authoritative field
        # still holds the adapter/terminal projection it held for the first reading.
        self.assertEqual(set(diagnostics), {"turn-ended", "working", "awaiting-input", "stale"})
        for pane_text, fields in zip(readings, authoritative, strict=True):
            with self.subTest(pane=pane_text):
                self.assertEqual(fields, authoritative[0])

    def _sweep_one_pane_reading(self, pane_text: str, index: int) -> TerminalCatalogEntry:
        """One identical connected row observed with exactly one pane reading."""
        catalog = TerminalCatalog(self.tmp / f"pane-matrix-{index}.json")
        entry = _connected_entry(
            "matrix",
            control_activity="running",
            terminal_outcome="completed",
            terminal_outcome_at="2026-07-07T00:00:05+00:00",
            terminal_evidence_id="turn-41",
        )
        catalog.upsert(entry)
        observe_terminal_liveness(
            catalog,
            self.host,
            entry,
            checked_at=self.clock(),
            probe=LivenessProbe(
                hysteresis=TerminalCatalogLivenessConfig(sweep_interval_seconds=0.0),
                pane_capturer=_pane(pane_text),
                snapshot_reader=_running_snapshot,
                terminal_reader=_no_terminal_evidence,
            ),
        )
        row = catalog.get("matrix")
        assert row is not None
        return row

    def test_no_source_write_can_make_a_pane_reading_authoritative(self) -> None:
        source = TERMINAL_LIVENESS_SOURCE.read_text(encoding="utf-8")
        self.assertEqual(_pane_authority_offenders(source), [])

        leaks = (
            "\ndef direct(entry, pane_diagnostic):\n"
            "    return replace(entry, turn_state=pane_diagnostic.state)\n",
            "\ndef indirect(entry, pane_text):\n"
            "    reading = classify_turn_state(pane_text)\n"
            "    return replace(entry, terminal_outcome=reading.state)\n",
            "\ndef keyword_state(catalog, entry, pane_diagnostic, checked_at):\n"
            "    return _record_adapter_turn_state(\n"
            "        catalog, entry, state=pane_diagnostic.state, checked_at=checked_at\n"
            "    )\n",
        )
        for leak in leaks:
            with self.subTest(leak=leak.splitlines()[1]):
                self.assertEqual(len(_pane_authority_offenders(source + leak)), 1)
