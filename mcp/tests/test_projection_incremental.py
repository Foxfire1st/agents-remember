"""MIK-R81 rule 2 (F09): path-directed incremental projection, no full-bore refresh.

A one-keystroke draft (a documentary task change: same contracts, same enclosures)
refreshes only the affected domain inputs -- the downstream cascade (lifecycles,
engine facts, drift) stays put. Only a structural task change flips it. Repo
surfaces (no watch root names them) refresh on full passes and when their TTL
lapses; every other pass -- change ticks and heartbeats alike -- retains them
without reaching a surface reader.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.projections.projection_inputs import (
    ProjectionDomain,
    ProjectionInputState,
    ProjectionReaders,
    ProjectionRefresh,
    RefreshPass,
)

DOC = {
    "schema": "ar-task-document/v1",
    "id": "t1",
    "slug": "t1",
    "title": "one",
    "kind": "light",
    "status": "planning",
    "repo": "r",
    "createdAt": "2026-10-13T00:00:00+00:00",
}


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


class ProjectionIncrementalTests(unittest.TestCase):
    def test_documentary_draft_skips_the_downstream_cascade(self) -> None:
        """A title keystroke updates the retained docs; lifecycles/engine/drift stay put."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "tasks" / "r" / "t1" / "task.json"
            doc.parent.mkdir(parents=True)
            doc.write_text(json.dumps(DOC), encoding="utf-8")
            observer = root / "obs"
            (observer / "workspace").mkdir(parents=True)
            (observer / "workspace" / "operator-inbox.jsonl").write_text("", encoding="utf-8")
            config = _config(root)
            calls = {"lifecycle": 0, "surfaces": 0}

            def count_lifecycle(path: Path) -> list:
                calls["lifecycle"] += 1
                return []

            def count_surfaces(cfg: McpRuntimeConfig, moment: datetime) -> tuple:
                calls["surfaces"] += 1
                return ([], [], [])

            readers = ProjectionReaders(
                lifecycle=count_lifecycle,
                repo_surfaces=count_surfaces,
                landing_state=None,
            )
            state = ProjectionInputState()
            now = datetime.now(UTC)

            first = state.read(
                config,
                readers,
                observer_root=observer,
                pass_=RefreshPass(now=now, refresh=ProjectionRefresh.full()),
            )
            self.assertEqual(first.task_documents[0].title, "one")
            self.assertEqual(calls, {"lifecycle": 1, "surfaces": 1})

            # One keystroke in the draft: same structure, new projected title.
            doc.write_text(json.dumps({**DOC, "title": "one edited"}), encoding="utf-8")
            second = state.read(
                config,
                readers,
                observer_root=observer,
                pass_=RefreshPass(
                    now=now,
                    refresh=ProjectionRefresh.change(frozenset({ProjectionDomain.TASKS})),
                ),
            )
            self.assertEqual(second.task_documents[0].title, "one edited")
            self.assertEqual(
                calls,
                {"lifecycle": 1, "surfaces": 1},
                "documentary change refreshes only the TASKS inputs",
            )

    def test_structural_task_change_flips_the_cascade(self) -> None:
        """A new (even unparseable) contract moves the skipped set: lifecycles re-read."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "tasks" / "r" / "t1" / "task.json"
            doc.parent.mkdir(parents=True)
            doc.write_text(json.dumps(DOC), encoding="utf-8")
            observer = root / "obs"
            (observer / "workspace").mkdir(parents=True)
            (observer / "workspace" / "operator-inbox.jsonl").write_text("", encoding="utf-8")
            config = _config(root)
            calls = {"lifecycle": 0}

            def count_lifecycle(path: Path) -> list:
                calls["lifecycle"] += 1
                return []

            readers = ProjectionReaders(
                lifecycle=count_lifecycle,
                repo_surfaces=lambda cfg, moment: ([], [], []),
                landing_state=None,
            )
            state = ProjectionInputState()
            now = datetime.now(UTC)
            state.read(
                config,
                readers,
                observer_root=observer,
                pass_=RefreshPass(now=now, refresh=ProjectionRefresh.full()),
            )
            self.assertEqual(calls["lifecycle"], 1)

            contract = root / "tasks" / "enclosures" / "l1" / "series-contract.md"
            contract.parent.mkdir(parents=True)
            contract.write_text("not a contract\n", encoding="utf-8")
            state.read(
                config,
                readers,
                observer_root=observer,
                pass_=RefreshPass(
                    now=now,
                    refresh=ProjectionRefresh.change(frozenset({ProjectionDomain.TASKS})),
                ),
            )
            self.assertEqual(
                calls["lifecycle"],
                2,
                "a moved contract/enclosure set re-reads the cascade",
            )

    def test_noop_passes_reach_no_surface_reader_until_the_ttl_lapses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "tasks" / "r" / "t1" / "task.json"
            doc.parent.mkdir(parents=True)
            doc.write_text(json.dumps(DOC), encoding="utf-8")
            observer = root / "obs"
            (observer / "workspace").mkdir(parents=True)
            (observer / "workspace" / "operator-inbox.jsonl").write_text("", encoding="utf-8")
            config = _config(root)
            calls = {"surfaces": 0}

            def count_surfaces(cfg: McpRuntimeConfig, moment: datetime) -> tuple:
                calls["surfaces"] += 1
                return ([], [], [])

            readers = ProjectionReaders(
                lifecycle=lambda path: [],
                repo_surfaces=count_surfaces,
                landing_state=None,
            )
            state = ProjectionInputState()
            now = datetime.now(UTC)
            state.read(
                config,
                readers,
                observer_root=observer,
                pass_=RefreshPass(now=now, refresh=ProjectionRefresh.full()),
            )
            self.assertEqual(calls["surfaces"], 1)
            # Change ticks for other domains and heartbeats retain unread.
            state.read(
                config,
                readers,
                observer_root=observer,
                pass_=RefreshPass(
                    now=now,
                    refresh=ProjectionRefresh.change(frozenset({ProjectionDomain.WORKSPACE})),
                ),
            )
            state.read(
                config,
                readers,
                observer_root=observer,
                pass_=RefreshPass(now=now, refresh=ProjectionRefresh.heartbeat()),
            )
            self.assertEqual(calls["surfaces"], 1)
            # Past the TTL the next pass refreshes: memory edits stay visible.
            state.read(
                config,
                readers,
                observer_root=observer,
                pass_=RefreshPass(
                    now=now + timedelta(seconds=121),
                    refresh=ProjectionRefresh.heartbeat(),
                ),
            )
            self.assertEqual(calls["surfaces"], 2)
