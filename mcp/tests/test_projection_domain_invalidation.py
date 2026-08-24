"""Reader-domain invalidation and bounded-retention regressions for live projection."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from unittest import mock

from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.serving.projections import projection_inputs
from agents_remember.serving.projections.contract_snapshot import ContractSnapshot
from agents_remember.serving.projections.projection_inputs import (
    ProjectionDomain,
    ProjectionInputState,
    ProjectionReaders,
    ProjectionRefresh,
    RefreshPass,
)
from agents_remember.serving.projections.snapshots import (
    TASK_DOCUMENT_SCHEMA,
    TASK_DOCUMENT_SUMMARY_LIMIT,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _seed_task_documents(root: Path, count: int) -> list[Path]:
    task_root = root / "tasks" / "repo" / "series"
    task_root.mkdir(parents=True)
    paths: list[Path] = []
    for index in range(count):
        path = task_root / f"leaf-{index:04d}.json"
        path.write_text(
            json.dumps(
                {
                    "schema": TASK_DOCUMENT_SCHEMA,
                    "kind": "light",
                    "id": f"L{index:04d}",
                    "slug": f"leaf-{index:04d}",
                    "title": f"Task {index:04d}",
                    "repo": "repo",
                    "status": "planning",
                    "createdAt": NOW.isoformat(),
                }
            ),
            encoding="utf-8",
        )
        paths.append(path)
    return paths


class ProjectionInputStateTests(unittest.TestCase):
    def test_failed_task_refresh_is_atomic_and_retried_on_heartbeat(self) -> None:
        """A failed refresh retains the last good snapshot and cannot poison retry state."""
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            state = ProjectionInputState()
            config = _config(root)
            prior_contracts = ContractSnapshot(
                contracts=MappingProxyType({}),
                skipped=frozenset(),
            )
            refreshed_contracts = ContractSnapshot(
                contracts=MappingProxyType({}),
                skipped=frozenset(),
            )
            prior_enclosures = [mock.sentinel.prior_enclosure]
            prior_documents = [mock.sentinel.prior_document]
            prior_series = [mock.sentinel.prior_series]
            state._contracts = prior_contracts
            state._enclosures = prior_enclosures  # type: ignore[assignment]
            state._task_documents = prior_documents  # type: ignore[assignment]
            state._series = prior_series  # type: ignore[assignment]

            with (
                mock.patch.object(
                    state._contract_cache,
                    "build",
                    return_value=refreshed_contracts,
                ) as build,
                mock.patch.object(
                    projection_inputs,
                    "read_enclosures",
                    side_effect=[RuntimeError("transient task refresh failure"), []],
                ) as enclosure_reader,
                mock.patch.object(
                    projection_inputs,
                    "read_task_documents",
                    return_value=[],
                ) as task_reader,
                mock.patch.object(
                    projection_inputs,
                    "read_series_documents",
                    return_value=[],
                ) as series_reader,
            ):
                with self.assertRaisesRegex(RuntimeError, "transient task refresh failure"):
                    state._refresh_tasks(
                        config,
                        RefreshPass(now=NOW, refresh=ProjectionRefresh.full()),
                    )

                self.assertIs(state._contracts, prior_contracts)
                self.assertIs(state._enclosures, prior_enclosures)
                self.assertIs(state._task_documents, prior_documents)
                self.assertIs(state._series, prior_series)
                self.assertTrue(state._task_refresh_pending)

                changed = state._refresh_tasks(
                    config,
                    RefreshPass(
                        now=NOW + timedelta(seconds=1),
                        refresh=ProjectionRefresh.heartbeat(),
                    ),
                )

            self.assertTrue(changed)
            self.assertIs(state._contracts, refreshed_contracts)
            self.assertEqual(state._enclosures, [])
            self.assertEqual(state._task_documents, [])
            self.assertEqual(state._series, [])
            self.assertFalse(state._task_refresh_pending)
            self.assertEqual(build.call_count, 2)
            self.assertEqual(enclosure_reader.call_count, 2)
            task_reader.assert_called_once()
            series_reader.assert_called_once()

    def test_heartbeat_and_lifecycle_changes_skip_unrelated_heavy_readers(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            root = Path(raw_tmp)
            _seed_task_documents(root, 32)
            observer_root = root / "logs" / "observer"
            state = ProjectionInputState()
            config = _config(root)

            with (
                mock.patch.object(
                    projection_inputs,
                    "read_task_documents",
                    wraps=projection_inputs.read_task_documents,
                ) as task_reader,
                mock.patch.object(
                    projection_inputs,
                    "read_series_documents",
                    wraps=projection_inputs.read_series_documents,
                ) as series_reader,
                mock.patch.object(
                    projection_inputs,
                    "read_drift_snapshots",
                    wraps=projection_inputs.read_drift_snapshots,
                ) as drift_reader,
                mock.patch.object(
                    projection_inputs,
                    "read_engine_process_facts",
                    wraps=projection_inputs.read_engine_process_facts,
                ) as engine_reader,
                mock.patch.object(
                    projection_inputs,
                    "refresh_engine_process_landing",
                    wraps=projection_inputs.refresh_engine_process_landing,
                ) as landing_reader,
            ):
                full = state.read(
                    config,
                    ProjectionReaders(
                        lifecycle=lambda _root: [],
                        repo_surfaces=lambda _config, _now: ([], [], []),
                    ),
                    observer_root=observer_root,
                    pass_=RefreshPass(now=NOW, refresh=ProjectionRefresh.full()),
                )
                heartbeat = state.read(
                    config,
                    ProjectionReaders(
                        lifecycle=lambda _root: [],
                        repo_surfaces=lambda _config, _now: ([], [], []),
                    ),
                    observer_root=observer_root,
                    pass_=RefreshPass(
                        now=NOW + timedelta(seconds=5), refresh=ProjectionRefresh.heartbeat()
                    ),
                )
                state.read(
                    config,
                    ProjectionReaders(
                        lifecycle=lambda _root: [],
                        repo_surfaces=lambda _config, _now: ([], [], []),
                    ),
                    observer_root=observer_root,
                    pass_=RefreshPass(
                        now=NOW + timedelta(seconds=6),
                        refresh=ProjectionRefresh.change(frozenset({ProjectionDomain.LIFECYCLES})),
                    ),
                )

            self.assertEqual(task_reader.call_count, 1)
            self.assertEqual(series_reader.call_count, 1)
            self.assertEqual(drift_reader.call_count, 1)
            self.assertEqual(engine_reader.call_count, 1)
            self.assertEqual(landing_reader.call_count, 1)
            self.assertEqual(len(full.task_documents), 32)
            self.assertEqual(
                heartbeat.task_documents[0].ageSeconds,
                (full.task_documents[0].ageSeconds or 0.0) + 5.0,
            )

    def test_task_refresh_replaces_and_reclaims_retained_rows_at_two_sizes(self) -> None:
        for size in (32, 320):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as raw_tmp:
                root = Path(raw_tmp)
                paths = _seed_task_documents(root, size)
                observer_root = root / "logs" / "observer"
                state = ProjectionInputState()
                config = _config(root)

                first = state.read(
                    config,
                    ProjectionReaders(
                        lifecycle=lambda _root: [],
                        repo_surfaces=lambda _config, _now: ([], [], []),
                    ),
                    observer_root=observer_root,
                    pass_=RefreshPass(now=NOW, refresh=ProjectionRefresh.full()),
                )
                self.assertEqual(len(first.task_documents), min(size, TASK_DOCUMENT_SUMMARY_LIMIT))

                for path in paths[::2]:
                    path.unlink()
                refreshed = state.read(
                    config,
                    ProjectionReaders(
                        lifecycle=lambda _root: [],
                        repo_surfaces=lambda _config, _now: ([], [], []),
                    ),
                    observer_root=observer_root,
                    pass_=RefreshPass(
                        now=NOW + timedelta(seconds=1),
                        refresh=ProjectionRefresh.change(frozenset({ProjectionDomain.TASKS})),
                    ),
                )

                remaining = size // 2
                self.assertEqual(
                    len(refreshed.task_documents),
                    min(remaining, TASK_DOCUMENT_SUMMARY_LIMIT),
                )
                self.assertFalse(
                    {f"L{index:04d}" for index in range(0, size, 2)}
                    & {node.id for node in refreshed.task_documents}
                )


if __name__ == "__main__":
    unittest.main()
