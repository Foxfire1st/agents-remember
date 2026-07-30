"""Reader-domain invalidation and bounded-retention regressions for live projection."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.observer import projection_inputs
from agents_remember.observer.projection_inputs import (
    ProjectionDomain,
    ProjectionInputState,
    ProjectionRefresh,
)
from agents_remember.observer.snapshots import (
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
                    observer_root=observer_root,
                    now=NOW,
                    refresh=ProjectionRefresh.full(),
                    landing_state=None,
                    lifecycle_reader=lambda _root: [],
                    repo_surface_reader=lambda _config, _now: ([], [], []),
                )
                heartbeat = state.read(
                    config,
                    observer_root=observer_root,
                    now=NOW + timedelta(seconds=5),
                    refresh=ProjectionRefresh.heartbeat(),
                    landing_state=None,
                    lifecycle_reader=lambda _root: [],
                    repo_surface_reader=lambda _config, _now: ([], [], []),
                )
                state.read(
                    config,
                    observer_root=observer_root,
                    now=NOW + timedelta(seconds=6),
                    refresh=ProjectionRefresh.change(frozenset({ProjectionDomain.LIFECYCLES})),
                    landing_state=None,
                    lifecycle_reader=lambda _root: [],
                    repo_surface_reader=lambda _config, _now: ([], [], []),
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
                    observer_root=observer_root,
                    now=NOW,
                    refresh=ProjectionRefresh.full(),
                    landing_state=None,
                    lifecycle_reader=lambda _root: [],
                    repo_surface_reader=lambda _config, _now: ([], [], []),
                )
                self.assertEqual(len(first.task_documents), min(size, TASK_DOCUMENT_SUMMARY_LIMIT))

                for path in paths[::2]:
                    path.unlink()
                refreshed = state.read(
                    config,
                    observer_root=observer_root,
                    now=NOW + timedelta(seconds=1),
                    refresh=ProjectionRefresh.change(frozenset({ProjectionDomain.TASKS})),
                    landing_state=None,
                    lifecycle_reader=lambda _root: [],
                    repo_surface_reader=lambda _config, _now: ([], [], []),
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
