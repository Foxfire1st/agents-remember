"""Durability contract for the two JSONL record stores under ``providers/`` (260731-EFA-L5).

The leaf's first pass put six ``controlplane/`` stores on ``ar-durable-store/1.0`` and left these
two behind, on the strength of nothing but their directory. They have the identical shape --
append-only JSONL plus a reclaim pass that rewrites the file whole -- so they had the identical
defect, and it was not theoretical:

* ``ProviderMetricsStore`` is a genuine TWO-PROCESS pairing. The MCP process appends
  index-lifecycle rows from its provider-setup thread (``providers/provider_setup.py``
  ``_record_index_state``) while the dashboard's ``_metrics_loop`` (``serving/app.py``) runs
  ``record`` and then ``compact``. Measured on this tree before the fix, 2 appenders against
  that loop and 800 records accepted: 287 / 320 / 294 of them (35.88% / 40.00% / 36.75%) were
  gone from disk afterwards, with a torn line in one run.
* ``ProviderDegradationStore`` has ONE writer today, which is exactly the argument this leaf
  already refused for attention-dismissals and supervisor-signals -- and refused for a measured
  reason, since the draft that left those unlocked measured 31.45% loss. Same shape, same
  measurement: 582 / 574 / 584 of 800 accepted events (72.75% / 71.75% / 73.00%) were gone.

Those six figures are NOT reproducible from anything this file declares, and saying so is cheaper
than letting the next reader take them for a property of the profile. Re-measured against a
``git archive`` of the base commit once ``harness_work_dir`` was fixed: ``STRESS_PROFILE`` as
shipped (4 appenders, 200 records) loses 1.50-3.50% of metrics rows and 3.00-7.50% of degradation
events, and the 2-appender/800-record shape named above loses 5.25-5.50% and 7.00-7.12%. The
pacing the originals were taken at is not recorded and the figures do not follow from the two
knobs that are, so treat them as evidence of DIRECTION -- these stores lost records and now lose
none -- and never as a rate this suite reproduces. The direction is what the assertions rest on
and it is unaffected; the arithmetic below does not depend on either number.

Seven classes pin seven things. Four answer a numbered leaf requirement and three do not; the
enumeration below is the whole file, because a list that names four of seven reads as coverage
for the three it omits.

R10  :class:`ProviderStoreDurabilityTests` -- no record an appender reports as written is missing
     afterwards, under real processes (``multiprocessing``, never threads -- the GIL would
     serialise the window under test).
R14  :class:`ProviderHarnessSensitivityTests` -- the harness can still fail, proven against a
     ``git archive`` of the leaf's base commit, so the proof does not depend on when the fix
     landed or on anyone's memory of running it.
R8   :class:`ProviderReadPolicyTests` -- the per-reader torn-line policy, and the two structural
     facts that make TOLERANT the right answer for both of these logs rather than the convenient
     one. Both facts are asserted, not argued.
R2   :class:`ProviderOwnershipTests` -- the ownership decisions, as assertions rather than as
     prose in a rationale string, including the structural single-call-site fact each rationale
     actually rests on.

     :class:`ProviderSchemaVersionTests` -- where ``schemaVersion`` is stamped, and the one
     document that deliberately carries none.
     :class:`ProviderReclaimShapeTests` -- the reclaim's edges: no log, an empty log, retention by
     age, and the unlink branch neither store has.
     :class:`ProviderCaseRegistryTests` -- the two provider cases are their own set in the shared
     instrument rather than silently inside the control-plane one.

Neither store is in the ``forced_unlink`` scenario, and that is a finding rather than an
omission: neither reclaim has an unlink branch to begin with (``compact`` returns 0 when the
kept set is empty, ``compact_events`` when the log is under its cap), at the base commit
included. Failure mode #2 was never present in this pair, and
:meth:`ProviderReclaimShapeTests.test_no_reclaim_on_either_log_ever_unlinks_it` holds that shut on
both stores and across the rewrite, not only on the branch that declines to rewrite.
"""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from _global_state import preserve_owned_mutable_state
from _store_durability import (
    ADAPTERS,
    PROVIDER_CASES,
    STRESS_PROFILE,
    extract_base_commit_tree,
    run_against_source,
    run_case,
)
from agents_remember.controlplane.durable_store import (
    SCHEMA_VERSION,
    CompactionOwnerError,
    declare_process_role,
)
from agents_remember.mcp.config import McpRuntimeConfig, load_config
from agents_remember.providers.degradation import (
    DEGRADATION_EVENT_SCHEMA,
    PROVIDER_DEGRADATION_OWNERSHIP,
    ProviderDegradationState,
    ProviderDegradationStore,
    evaluate_provider_degradation,
)
from agents_remember.providers.metrics import (
    PROVIDER_INDEX_STATE_SCHEMA,
    PROVIDER_METRICS_OWNERSHIP,
    PROVIDER_METRICS_SCHEMA,
    ContainerSample,
    MetricsSnapshot,
    ProviderMetricsStore,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "agents_remember"

# The reclaim each provider store owns, keyed by the class a local has to be bound to before a
# ``.compact``/``.compact_events`` reference counts as one of ITS call sites. Six other stores in
# this tree have a ``compact``; matching on the attribute name alone would count all of them.
PROVIDER_RECLAIMS = {
    "ProviderMetricsStore": "compact",
    "ProviderDegradationStore": "compact_events",
}

# A line cut off mid-write: what a crash, or an interleaved append into an unlocked log, leaves.
TORN_LINE = '{"schema":"ar-provider-metrics-sample/v1","sampledAt":"2026-0'

# A row from a build this one is not allowed to interpret. ``ar-durable-store/1.0`` refuses an
# unknown MAJOR and accepts an unknown minor, so this is the shape a reader must skip.
FUTURE_MAJOR_ROW = json.dumps(
    {
        "schema": PROVIDER_METRICS_SCHEMA,
        "sampledAt": "2027-01-01T00:00:00+00:00",
        "schemaVersion": "2.0",
    }
)


def _own_scope(node: ast.AST) -> Iterator[ast.AST]:
    """Every node in one function's OWN body, never descending into a nested function.

    ``ast.walk`` would hand a nested ``def``'s nodes to its enclosing function as well, so a
    reference inside the nested one would be attributed to both and counted twice.
    """
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield from _own_scope(child)


def _provider_store_locals(function: ast.AST) -> dict[str, str]:
    """The names in one function bound to a provider store, mapped to the store class.

    Both bindings the tree actually uses: a parameter annotated with the class
    (``_metrics_loop(..., metrics_store: ProviderMetricsStore)``) and an assignment from its
    constructor (``store = ProviderDegradationStore(...)``).
    """
    bound: dict[str, str] = {}
    for node in _own_scope(function):
        if (
            isinstance(node, ast.arg)
            and isinstance(node.annotation, ast.Name)
            and node.annotation.id in PROVIDER_RECLAIMS
        ):
            bound[node.arg] = node.annotation.id
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            called = node.value.func
            if isinstance(called, ast.Name) and called.id in PROVIDER_RECLAIMS:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bound[target.id] = called.id
    return bound


def provider_reclaim_call_sites() -> dict[str, list[str]]:
    """Every place in the package that names a provider store's reclaim, by store class.

    A REFERENCE, not only a call: ``asyncio.to_thread(metrics_store.compact)`` is how the
    dashboard reaches ``compact``, and a scan that insisted on ``compact()`` would find nothing
    and report the property held.

    STATED, NOT CLOSED. The receiver has to be a LOCAL of the function that names the reclaim --
    which is both call sites today, and the shape a new one would most likely take. A reclaim
    reached through an attribute (``self._store.compact()``), through a module-level global, or
    from a nested closure relying on the enclosing function's binding is not attributed to a
    store by this scan and would not be seen. Threading enclosing scopes and attribute chains
    through the resolver buys a guard against call sites nothing in this tree writes; the
    boundary is recorded here instead so a reader knows which half of the claim is machine-checked.
    """
    found: dict[str, list[str]] = {store: [] for store in PROVIDER_RECLAIMS}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            bound = _provider_store_locals(function)
            for node in _own_scope(function):
                if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                    continue
                store = bound.get(node.value.id)
                if store is not None and node.attr == PROVIDER_RECLAIMS[store]:
                    rel = path.relative_to(PACKAGE_ROOT).as_posix()
                    found[store].append(f"{rel}::{function.name}")
    return found


def _mentions_the_event_log(path: Path) -> bool:
    """Whether one module names ``degradation-events.jsonl`` or the property that resolves it."""
    text = path.read_text(encoding="utf-8")
    return "events_path" in text or "degradation-events" in text


def _degradation_config(root: Path) -> McpRuntimeConfig:
    """A real ``McpRuntimeConfig`` over a throwaway tree, with the detector's default thresholds.

    The real ``load_config``, not a stub: the point of the test that uses this is to run the
    actual consumer of ``metrics.jsonl`` against an actual log.
    """
    settings = root / ".codex" / "mcp" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": str(root / "ar-coordination"),
                "workspaceRoot": str(root / "workspace"),
                "repositories": {"agents-remember": {}},
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    return load_config(settings)


def _memory_sample(name: str, *, ratio: float) -> MetricsSnapshot:
    """One container sample carrying a memory ratio the detector will classify."""
    return MetricsSnapshot(
        schema=PROVIDER_METRICS_SCHEMA,
        sampledAt="2026-08-01T00:00:00+00:00",
        containers=[
            ContainerSample(
                name=name,
                provider="grepai-memory",
                instance="workspace",
                running=True,
                restarts=0,
                mem_bytes=int(ratio * 1_000),
                mem_limit_bytes=1_000,
            )
        ],
    )


def _describe(result: dict[str, object]) -> str:
    attempted = int(result["attempted"])  # type: ignore[arg-type]
    lost = int(result["lost"])  # type: ignore[arg-type]
    percent = (100.0 * lost / attempted) if attempted else 0.0
    return (
        f"{result['case']} / {result['scenario']}: {lost} of {attempted} records reported "
        f"written were missing afterwards ({percent:.2f}% loss); "
        f"sample={result['lost_sample']} torn_lines={result['torn_lines']} "
        f"reclaim_attempts={result.get('reclaim_attempts')} "
        f"successful_reclaims={result.get('successful_reclaims')} "
        f"stragglers={result['stragglers']}"
    )


class _TempRootTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def case_root(self, scenario: str, case: str) -> Path:
        """One throwaway store tree per (scenario, case): two runs must never share a log.

        The parent used to matter too, and that was the instrument's bug rather than this file's
        contract: ``run_stress`` kept its stop flag in ``root.parent/harness``, so two cases under
        one parent shared a stop flag and the second reclaimer found it already set and left the
        tick loop after ONE tick -- a green measured over almost nothing, on the UNFIXED tree as
        readily as on this one. Discovered here, working around it in this file only; since fixed
        at the source, where it also covered the control-plane suite that had the same layout and
        no such workaround. ``_store_durability.harness_work_dir`` now derives the scratch
        directory from ``root`` itself so no caller has to know any of this, and
        ``MIN_SUCCESSFUL_RECLAIMS`` refuses the result if a reclaimer barely compacts for any
        other reason.
        What is left here is the ordinary requirement that separate runs get separate stores.
        """
        return self.tmp / scenario / case / "observer"


class ProviderStoreDurabilityTests(_TempRootTest):
    """R10: real processes appending while one compacts, over both provider record types."""

    def test_no_record_is_lost_when_an_append_races_a_compaction(self) -> None:
        """The lost-update window: an append lands between the reclaim's read and its commit.

        Forced rather than raced, so it decides the same thing on every machine. The stress case
        below produces the RATE; this one produces the yes/no that a loaded CI box cannot blur.
        """
        for case in PROVIDER_CASES:
            with self.subTest(store=case):
                result = run_case("forced_lost_update", case, self.case_root("forced", case))
                self.assertEqual(result["attempted"], 1, _describe(result))
                self.assertEqual(result["lost"], 0, _describe(result))
                self.assertEqual(result["stragglers"], [], _describe(result))

    def test_no_record_is_lost_under_sustained_multi_process_write_and_compaction(self) -> None:
        """The unforced version: concurrent processes, no interposition anywhere.

        The reclaim tick count is load-bearing, and it is no longer checked here: this file's
        the successful-compaction floor moved into the instrument as
        ``MIN_SUCCESSFUL_RECLAIMS`` (10) and applies
        to every caller of ``run_stress`` now, including the control-plane suite that did not have
        it and the base-commit script entry point that could not have had it. A local repeat would
        be unreachable behind a floor ten times its size. Every loss number in this file is a
        function of how many times the reclaimer got to rewrite the log; a fix that "passed" by
        making the reclaimer slower would be a narrower window, not a safer store. ``_describe``
        carries the count into the failure text either way.
        """
        for case in PROVIDER_CASES:
            with self.subTest(store=case):
                result = run_case("stress", case, self.case_root("stress", case))
                self.assertEqual(result["stragglers"], [], _describe(result))
                self.assertEqual(
                    result["attempted"],
                    STRESS_PROFILE["appenders"] * STRESS_PROFILE["per_appender"],
                    f"{_describe(result)}; write calls raised: {result['append_errors']}",
                )
                self.assertEqual(result["lost"], 0, _describe(result))
                self.assertEqual(result["torn_lines"], 0, _describe(result))

    def test_concurrent_operation_never_raises_out_of_a_store_call(self) -> None:
        """Losing no records is half of it; the caller must also not be handed an exception.

        This is failure mode #3 for these two stores specifically. ``metrics.jsonl.compact.tmp``,
        ``metrics-current.json.tmp`` and ``degradation-events.jsonl.compact.tmp`` carried no pid,
        so two rewriters shared one temp path: one ``os.replace``d it away and the other got
        ``FileNotFoundError`` out of a store call that had reported nothing wrong.

        Its own run against its own root, so the sibling test's "the run actually happened"
        guards do not cover it and are repeated -- without them, an appender that died before its
        loop would write no error file and "zero raised" would be reported over zero calls.
        """
        for case in PROVIDER_CASES:
            with self.subTest(store=case):
                result = run_case("stress", case, self.case_root("raise", case))
                self.assertEqual(result["stragglers"], [], _describe(result))
                self.assertEqual(
                    result["attempted"],
                    STRESS_PROFILE["appenders"] * STRESS_PROFILE["per_appender"],
                    f"no error is only evidence over a run that happened: {_describe(result)}",
                )
                self.assertEqual(
                    result["append_error_count"],
                    0,
                    f"write calls raised: {result['append_errors']}",
                )
                self.assertEqual(
                    result["reclaim_error_count"],
                    0,
                    f"reclaim calls raised: {result['reclaim_errors']}",
                )


class ProviderHarnessSensitivityTests(_TempRootTest):
    """R14: the contract above must be able to fail, and here is the tree on which it does.

    "It failed before I fixed it" is not evidence a reviewer can re-check, least of all in a
    worktree where the fix lands beside the test. A ``git archive`` of the base commit is: it is
    the same two stores, byte for byte, whenever this runs.
    """

    def test_the_forced_scenario_detects_loss_in_the_base_commit(self) -> None:
        source = extract_base_commit_tree(self.tmp / "base")
        for module in ("metrics.py", "degradation.py"):
            self.assertTrue(
                (source / "agents_remember" / "providers" / module).is_file(),
                f"base-commit archive is missing providers/{module}",
            )

        result = run_against_source(
            source,
            {
                "scenario": "forced_lost_update",
                "cases": list(PROVIDER_CASES),
                "root": str(self.tmp / "base-run"),
                "handoff_seconds": 2.0,
                "timeout": 60.0,
            },
        )

        self.assertEqual(result["source_root"], str(source))
        lost = {row["case"]: row["lost"] for row in result["runs"]}
        self.assertEqual(
            lost,
            dict.fromkeys(PROVIDER_CASES, 1),
            f"the harness failed to detect the base-commit defect in the provider stores, so "
            f"the assertions above cannot be trusted to detect its return: {json.dumps(lost)}",
        )


class ProviderReadPolicyTests(_TempRootTest):
    """R8: both provider logs are read TOLERANTLY, and the reason is structural, not a shrug.

    The leaf's rule is that every rewrite of an authority-bearing log reads strictly, and that a
    store may rewrite from a tolerant read only if it carries no authority -- because such a
    rewrite drops the unreadable row PERMANENTLY. Two facts put this pair outside that rule, and
    each of them has a test here rather than a paragraph:

    1. NEITHER REWRITE PARSES. ``compact`` tails raw lines and ``compact_events`` slices raw
       lines, so a row no reader can read is retained by the reclaim, not deleted by it. The
       permanent-drop cost the rule is about does not arise.
       -- :meth:`test_neither_reclaim_deletes_a_row_it_could_not_parse`
    2. NOTHING IS DECIDED ON THE PRESENCE OF A ROW. The metrics log's one mutating consumer (the
       degradation detector) re-derives its whole state machine from a rolling window of live
       samples every 30s and consumes nothing -- it never writes to the log it reads, so no row
       is ever marked spent and no row's absence can carry a decision forward. The degradation
       event log has no production reader at all. Neither carries a marker whose ABSENCE permits
       something -- which is what made a dropped ``applied`` gate row re-open a replay window.
       -- :meth:`test_the_metrics_consumer_writes_nothing_back_to_the_log_it_reads` and
       :meth:`test_nothing_outside_the_degradation_module_reads_the_event_log`

    Fact 2 used to be argued in this docstring and asserted nowhere, which is precisely the shape
    the sentence above disclaims. It is asserted now.
    """

    def _metrics(self) -> ProviderMetricsStore:
        return ProviderMetricsStore(self.tmp)

    def _snapshot(self, sampled_at: str) -> MetricsSnapshot:
        return MetricsSnapshot(schema=PROVIDER_METRICS_SCHEMA, sampledAt=sampled_at)

    def _append_raw(self, path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def test_the_metrics_read_skips_only_the_row_it_cannot_read(self) -> None:
        """Torn, non-object and newer-major rows are skipped PER ROW, not per file.

        The rows appended after the bad ones are what separates a reader that drops one row from
        a reader that stops at the first: an empty projection is not a degraded one.
        """
        store = self._metrics()
        store.record(self._snapshot("2026-08-01T00:00:00+00:00"))
        self._append_raw(store.log_path, TORN_LINE)
        self._append_raw(store.log_path, "[1, 2, 3]")
        self._append_raw(store.log_path, FUTURE_MAJOR_ROW)
        self._append_raw(store.log_path, "   ")
        store.record(self._snapshot("2026-08-01T00:00:02+00:00"))

        rows = store.read_recent(limit=50)

        self.assertEqual(
            [row["sampledAt"] for row in rows],
            ["2026-08-01T00:00:00+00:00", "2026-08-01T00:00:02+00:00"],
            "the tolerant read must lose the unreadable rows and nothing else, including the "
            "rows written after them",
        )

    def test_a_row_with_no_schema_version_is_read_as_1_0(self) -> None:
        """Every row already on disk was written without the field, and must load unchanged.

        This is what makes stamping ``schemaVersion`` additive rather than a migration: absent
        means 1.0, so an existing ``metrics.jsonl`` -- and the ``metrics-current.json`` beside
        it -- is read by this build exactly as it was before the field existed.
        """
        store = self._metrics()
        legacy = {"schema": PROVIDER_METRICS_SCHEMA, "sampledAt": "2026-07-01T00:00:00+00:00"}
        self._append_raw(store.log_path, json.dumps(legacy))
        store.current_path.parent.mkdir(parents=True, exist_ok=True)
        store.current_path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

        self.assertEqual(store.read_recent(limit=5), [legacy])
        self.assertEqual(store.read_current(), legacy)

    def test_read_current_reports_nothing_it_cannot_interpret(self) -> None:
        """No file, and a file from a newer major, both read as "no current sample"."""
        store = self._metrics()
        self.assertIsNone(store.read_current())

        store.current_path.parent.mkdir(parents=True, exist_ok=True)
        store.current_path.write_text(FUTURE_MAJOR_ROW + "\n", encoding="utf-8")

        self.assertIsNone(
            store.read_current(),
            "a record whose major this build does not implement must not be handed to the "
            "status packet as though it were understood",
        )

    def test_neither_reclaim_deletes_a_row_it_could_not_parse(self) -> None:
        """Fact 1 above, on both logs: the rewrite keeps raw lines, so tolerance costs nothing.

        This is the assertion that makes the tolerant read policy safe rather than merely
        convenient. If either reclaim ever starts filtering by parsing, this fails -- and the
        read on that store has to become strict in the same change.
        """
        metrics = self._metrics()
        metrics.record(self._snapshot("2026-08-01T00:00:00+00:00"))
        self._append_raw(metrics.log_path, TORN_LINE)
        metrics.compact(retain_rows=10_000, max_bytes=0)

        degradation = ProviderDegradationStore(self.tmp)
        degradation.append_event({"schema": DEGRADATION_EVENT_SCHEMA, "id": "kept"})
        self._append_raw(degradation.events_path, TORN_LINE)
        degradation.compact_events(retain_rows=2)

        for label, path in (
            ("metrics.jsonl", metrics.log_path),
            ("degradation-events.jsonl", degradation.events_path),
        ):
            with self.subTest(log=label):
                self.assertIn(
                    TORN_LINE,
                    path.read_text(encoding="utf-8").splitlines(),
                    f"{label}: the reclaim deleted a row no reader could parse -- a tolerant "
                    f"read is only safe while the rewrite keeps what the read skipped",
                )
        self.assertEqual(len(metrics.read_recent(limit=50)), 1)

    def test_the_metrics_consumer_writes_nothing_back_to_the_log_it_reads(self) -> None:
        """Fact 2, first half: the one consumer that mutates anything consumes no metrics row.

        What makes a log an AUTHORITY log is that a row's absence reads as "the thing it records
        never happened", which then permits what the row existed to refuse -- a dropped
        ``applied`` gate marker re-opening a replay window. That cannot arise while nothing marks
        a metrics row spent, and the way to show it is to run the real consumer over a real log
        and compare the bytes, not to assert it in prose.

        Deliberately a NON-transition: the previous state is seeded to ``degraded`` and the
        samples classify ``degraded``, so the detector decides from these rows and emits no
        event. A transition would drag the event/alert/delivery path into a read-policy test
        without making the metrics log any more written-to than it is here.
        """
        config = _degradation_config(self.tmp)
        metrics = ProviderMetricsStore(config.coordination_root)
        ProviderDegradationStore(config.coordination_root).write_state(
            ProviderDegradationState(
                state="degraded", entered_at="2026-08-01", last_evaluated_at="2026-08-01"
            )
        )
        for index in range(3):
            metrics.record(_memory_sample(f"grepai-{index}", ratio=0.85))
        before = metrics.log_path.read_bytes()

        def unreachable_stopper(_: McpRuntimeConfig) -> dict[str, Any]:
            raise AssertionError("this evaluation must not reach the critical failsafe")

        verdict = evaluate_provider_degradation(config, stop_provider_stacks=unreachable_stopper)

        self.assertEqual(verdict["state"], "degraded", "these rows must have decided something")
        self.assertIsNone(verdict["event"], "a transition here would test the alert path instead")
        self.assertEqual(
            metrics.log_path.read_bytes(),
            before,
            "the metrics log's one mutating consumer wrote back to the log it reads. A consumed "
            "marker or a removed row is exactly the authority shape that makes the tolerant read "
            "policy of this class unsafe, and it would have to become a strict read",
        )

    def test_nothing_outside_the_degradation_module_reads_the_event_log(self) -> None:
        """Fact 2, second half: ``degradation-events.jsonl`` has no production reader at all.

        A log nothing reads cannot have a row whose absence permits anything, which is the whole
        of why tolerance costs nothing on this one. That is a claim about the PACKAGE rather than
        about the store, so it is checked against the package: ``events_path`` is the only path
        to the file and its filename is the only other way to name it, so a reader added anywhere
        else has to go through one of the two and lands here.
        """
        mentions = [
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in sorted(PACKAGE_ROOT.rglob("*.py"))
            if _mentions_the_event_log(path)
        ]

        self.assertEqual(
            mentions,
            ["providers/degradation.py"],
            "the degradation event log grew a reader outside its own store. The tolerant read "
            "policy asserted in this class rests on it having none, and has to be re-decided "
            "against whatever that reader now depends on a row being present for",
        )


class ProviderSchemaVersionTests(_TempRootTest):
    """The ``schemaVersion`` policy, at the two writes that stamp it."""

    def test_every_row_this_build_writes_carries_its_schema_version(self) -> None:
        """Stamped by the STORE at the only write, because that is the only moment it exists.

        ``degradation-events.jsonl`` has no production reader and is kept for a thousand events;
        a row written without its version could never afterwards be told apart from a future
        one. The metrics rows are stamped for the reader that does exist.
        """
        metrics = ProviderMetricsStore(self.tmp)
        metrics.record(MetricsSnapshot(schema=PROVIDER_METRICS_SCHEMA, sampledAt="2026-08-01"))
        metrics.record_index_state({"provider": "codegraphcontext", "repoId": "repo-a"})
        degradation = ProviderDegradationStore(self.tmp)
        degradation.append_event({"schema": DEGRADATION_EVENT_SCHEMA, "id": "event-1"})

        rows = metrics.read_recent(limit=10)
        events = [
            json.loads(line)
            for line in degradation.events_path.read_text(encoding="utf-8").splitlines()
        ]

        self.assertEqual([row["schemaVersion"] for row in rows], [SCHEMA_VERSION, SCHEMA_VERSION])
        self.assertEqual(rows[1]["schema"], PROVIDER_INDEX_STATE_SCHEMA)
        self.assertEqual([event["schemaVersion"] for event in events], [SCHEMA_VERSION])
        current = metrics.read_current()
        assert current is not None
        self.assertEqual(current["schemaVersion"], SCHEMA_VERSION)

    def test_the_state_document_carries_no_schema_version(self) -> None:
        """``degradation-state.json`` is a recomputed position, not a record, and holds no rows.

        Stated as a test because the alternative -- stamping it for uniformity -- would put a
        version on a document with no reader able to act on it, and the honest handling of an
        unknown major on a state file (refuse, or reset to healthy?) is a decision this leaf did
        not make. It still goes through the contract's rewrite, which is what pid-scopes
        ``degradation-state.json.tmp`` and fsyncs it.
        """
        store = ProviderDegradationStore(self.tmp)
        store.write_state(
            ProviderDegradationState(
                state="degraded", entered_at="2026-08-01", last_evaluated_at="2026-08-01"
            )
        )

        payload = json.loads(store.state_path.read_text(encoding="utf-8"))

        self.assertNotIn("schemaVersion", payload)
        self.assertEqual(store.read_state(now="2026-08-02").state, "degraded")
        self.assertEqual(
            sorted(path.name for path in store.state_path.parent.glob("*.tmp")),
            [],
            "the rewrite must leave no temp file behind, pid-scoped or otherwise",
        )


class ProviderOwnershipTests(_TempRootTest):
    """R2: who writes each log and who compacts it, as assertions rather than as prose.

    Neither store earned the operator inbox's ``compaction_owner=None``. The inbox earned it
    because BOTH processes must physically remove rows and neither removal travels without the
    decision it implements; here nothing in the MCP process removes a provider row at all, so a
    single owner is available -- and where one is available the leaf requires it.
    """

    def setUp(self) -> None:
        super().setUp()
        state = preserve_owned_mutable_state()
        state.__enter__()
        self.addCleanup(state.__exit__, None, None, None)

    def test_both_provider_logs_declare_the_dashboard_their_compaction_owner(self) -> None:
        """The declaration and the predicate that reads it -- NOT who calls the reclaim.

        Named for what it proves, after being named for what it did not. NEITHER provider reclaim
        consults ``is_compaction_owner``: both rationales say the owner is enforced structurally,
        by the reclaim having exactly one call site, so a second call site added from the MCP
        process would leave this test green. That property is
        :meth:`test_each_provider_reclaim_has_exactly_one_call_site`. What is checked here is the
        declaration those rationales are written against, and that the predicate answers from the
        role the process declared rather than from a constant.
        """
        for ownership in (PROVIDER_METRICS_OWNERSHIP, PROVIDER_DEGRADATION_OWNERSHIP):
            with self.subTest(store=ownership.store):
                self.assertEqual(ownership.compaction_owner, "dashboard")
                declare_process_role("dashboard")
                self.assertTrue(ownership.is_compaction_owner())
                declare_process_role("mcp")
                self.assertFalse(ownership.is_compaction_owner())

    def test_each_provider_reclaim_has_exactly_one_call_site(self) -> None:
        """The structural fact both rationales rest on, checked against the package.

        ``PROVIDER_METRICS_OWNERSHIP`` earns its single owner by saying "compact() has exactly
        one caller and it is inside the dashboard's loop", and ``PROVIDER_DEGRADATION_OWNERSHIP``
        says the same of ``compact_events``. No runtime check refuses a reclaim from the wrong
        process on either log -- ``rewrite_lines`` asks only whether the lock is held -- so those
        two sentences ARE the enforcement, and until this test they were only sentences.

        Both call sites resolve to the dashboard: ``_metrics_loop`` is the dashboard's sampling
        loop, and ``evaluate_provider_degradation`` has that same loop as its one production
        caller.
        """
        self.assertEqual(
            provider_reclaim_call_sites(),
            {
                "ProviderMetricsStore": ["serving/app.py::_metrics_loop"],
                "ProviderDegradationStore": [
                    "providers/degradation.py::evaluate_provider_degradation"
                ],
            },
            "a provider reclaim gained, lost or moved a call site. Compaction ownership on these "
            "two logs is structural and nothing refuses a reclaim at runtime, so the call site is "
            "the enforcement and the StoreOwnership rationale has to be re-decided with the new "
            "one in view",
        )

    def test_the_metrics_log_accepts_a_write_from_either_daemon(self) -> None:
        """Both processes really do append here, so the writer check must pass for both.

        ``record`` is the dashboard's sampling loop; ``record_index_state`` is the MCP's
        provider-setup thread. That pairing is the whole reason this store needed the lock.
        """
        store = ProviderMetricsStore(self.tmp)
        for role in ("dashboard", "mcp"):
            with self.subTest(role=role):
                declare_process_role(role)
                store.record(MetricsSnapshot(schema=PROVIDER_METRICS_SCHEMA, sampledAt=role))
                store.record_index_state({"provider": role})
        self.assertEqual(len(store.read_recent(limit=10)), 4)

    def test_the_degradation_log_refuses_a_write_from_the_mcp_process(self) -> None:
        """Single-writer, and made checkable in the one process that could contradict it.

        This is the check that can actually fire in this pair, and it is deliberately NOT what
        makes the log safe -- the lock is, exactly as on attention-dismissals and
        supervisor-signals, whose single-writer draft measured 31.45% loss. Both writes are
        guarded: the append and the state document, because the store has one of each.
        """
        store = ProviderDegradationStore(self.tmp)
        declare_process_role("mcp")

        with self.assertRaises(CompactionOwnerError):
            store.append_event({"schema": DEGRADATION_EVENT_SCHEMA, "id": "from-the-mcp"})
        with self.assertRaises(CompactionOwnerError):
            store.write_state(
                ProviderDegradationState(
                    state="critical", entered_at="2026-08-01", last_evaluated_at="2026-08-01"
                )
            )


class ProviderReclaimShapeTests(_TempRootTest):
    """The reclaim's edges, on BOTH stores: no log, an empty log, retention by age, no unlink."""

    def test_a_reclaim_with_no_log_yet_is_a_no_op(self) -> None:
        self.assertEqual(ProviderMetricsStore(self.tmp).compact(), 0)
        self.assertEqual(ProviderDegradationStore(self.tmp).compact_events(), 0)

    def test_no_reclaim_on_either_log_ever_unlinks_it(self) -> None:
        """Failure mode #2 -- the unlinked inode -- is absent from this pair, and stays absent.

        An appender holding an ``"a"``-mode handle across a rewrite that ``unlink``s the log
        writes into a file with no name left: the record vanishes with no torn line and no trace.
        Neither provider reclaim has ever had that branch, which is why they are not in the
        harness's ``forced_unlink`` scenario. This is the assertion that would notice one
        appearing.

        BOTH stores, and both paths through each, because the version of this that only drove
        ``ProviderMetricsStore`` over an empty file was narrower than it read. With nothing in the
        log, ``compact`` returns at ``if not kept`` and ``compact_events`` at its row cap --
        BEFORE either reaches ``rewrite_lines`` -- so that call alone could only ever catch an
        ``unlink`` written above those returns, in one of the two stores. The second pair of calls
        reclaims a log that really is rewritten, so the same assertion is made over the rewrite
        instead of over an early return.

        WHAT IT CATCHES, precisely: a reclaim that leaves the log ABSENT. An ``unlink`` placed
        immediately before ``rewrite_lines``' ``os.replace`` would be invisible to ``exists()``,
        because the replace puts a file back at that name -- and it would also be indistinguishable
        from the rewrite itself, which swaps the inode by design. That is why an appender re-opens
        under the lock for every record instead of holding a handle, and it is a property of
        ``append_line``, not of this test.
        """
        metrics = ProviderMetricsStore(self.tmp)
        degradation = ProviderDegradationStore(self.tmp)
        metrics.log_path.parent.mkdir(parents=True, exist_ok=True)
        metrics.log_path.write_text("", encoding="utf-8")
        degradation.events_path.write_text("", encoding="utf-8")

        self.assertEqual(metrics.compact(retain_rows=10, max_bytes=-1), 0)
        self.assertEqual(degradation.compact_events(retain_rows=0), 0)

        self.assertTrue(
            metrics.log_path.exists(), "metrics.jsonl: an empty reclaim must never unlink the log"
        )
        self.assertTrue(
            degradation.events_path.exists(),
            "degradation-events.jsonl: an empty reclaim must never unlink the log",
        )

        for index in range(20):
            metrics.record_index_state({"seq": index})
            degradation.append_event({"schema": DEGRADATION_EVENT_SCHEMA, "id": f"event-{index}"})

        self.assertEqual(metrics.compact(retain_rows=5, max_bytes=0), 5)
        self.assertEqual(degradation.compact_events(retain_rows=5), 15)

        self.assertTrue(
            metrics.log_path.exists(),
            "metrics.jsonl: the reclaim that DOES rewrite must replace the log, never remove it",
        )
        self.assertTrue(
            degradation.events_path.exists(),
            "degradation-events.jsonl: the reclaim that DOES rewrite must replace the log, never "
            "remove it",
        )

    def test_the_reclaim_keeps_the_newest_rows_and_returns_how_many(self) -> None:
        """The retention policy is unchanged by this leaf: rows go by AGE, never by content."""
        store = ProviderMetricsStore(self.tmp)
        for index in range(20):
            store.record_index_state({"seq": index})

        kept = store.compact(retain_rows=5, max_bytes=0)

        rows = store.read_recent(limit=50)
        self.assertEqual(kept, 5)
        self.assertEqual([row["seq"] for row in rows], [15, 16, 17, 18, 19])


class ProviderCaseRegistryTests(unittest.TestCase):
    """The two provider cases are registered, and are not silently inside the control-plane set."""

    def test_the_provider_cases_are_their_own_set(self) -> None:
        from _store_durability import CASES  # noqa: PLC0415 - read here, not at import time

        self.assertEqual(PROVIDER_CASES, ("provider_metrics", "provider_degradation"))
        self.assertEqual(set(PROVIDER_CASES) & set(CASES), set())
        for case in PROVIDER_CASES:
            self.assertEqual(ADAPTERS[case].torn_line_policy, "tolerant")


if __name__ == "__main__":
    unittest.main()
