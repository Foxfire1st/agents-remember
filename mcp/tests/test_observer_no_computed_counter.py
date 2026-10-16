"""MIK-R115 rule 1: no token number computed from the event log is projected.

The dashboard's gauge, "series tokens", cockpit total and the fields behind them
are gone. The ``tokens`` of a single event stay as a fact of the Event River
(MIK-R114 rule 3): events keep them, the projection folds nothing from them.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agents_remember.observer import projection as projection_module
from agents_remember.observer import reducer as reducer_module
from agents_remember.observer.projection import Metrics, TaskEntry, WorkspaceProjection
from agents_remember.observer.reducer import project_workspace
from agents_remember.observer.reducer_impl._types import WorkspaceStructure
from agents_remember.serving.projections.projection_store import write_projection
from test_observer_projection import FRESH


class NoComputedCounterTests(unittest.TestCase):
    """The removed fields stay removed; event tokens stay on the event."""

    def test_projection_models_carry_no_token_fields(self) -> None:
        self.assertNotIn("totalTokens", Metrics.model_fields)
        self.assertNotIn("seriesTokenTotal", TaskEntry.model_fields)
        self.assertNotIn("TokenSample", dir(projection_module))
        self.assertNotIn("token_series", dir(reducer_module))

    def test_served_schema_carries_no_token_fields(self) -> None:
        schema = WorkspaceProjection.model_json_schema()
        names = ["TokenSample", "tokenSeries", "totalTokens", "seriesTokenTotal"]
        blob = str(schema)
        for name in names:
            self.assertNotIn(name, blob)

    def test_store_write_over_old_field_file_reads_back_clean(self) -> None:
        # Expected Evidence 2(a) (MIK-R115): a stored projection with the old fields
        # lies where the store writes; the store's normal write runs over it; the
        # file read back holds none of the fields, and nothing failed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "observer"
            root.mkdir()
            old = {
                "generatedAt": "2026-10-08T00:00:00+00:00",
                "metrics": {"totalTokens": 1500, "lifecycleCount": 0},
                "lifecycles": [
                    {
                        "id": "LC-old",
                        "tokens": 1500,
                        "tokenSeries": [{"at": "2026-10-08T00:00:00+00:00", "tokens": 1500}],
                    }
                ],
            }
            (root / "latest-state.json").write_text(json.dumps(old))
            proj = project_workspace(
                structure=WorkspaceStructure(enclosures=[], providers=[]),
                now=FRESH,
            )
            write_projection(root, proj)
            blob = (root / "latest-state.json").read_text()
            read = json.loads(blob)
            for name in ("tokens", "tokenSeries", "totalTokens", "seriesTokenTotal"):
                self.assertNotIn(name, blob)
            self.assertNotIn("lifecycles", read)

    def test_workspace_metrics_carry_no_token_total(self) -> None:
        proj = project_workspace(
            structure=WorkspaceStructure(enclosures=[], providers=[]),
            now=FRESH,
        )
        self.assertNotIn("totalTokens", proj.metrics.model_dump())


if __name__ == "__main__":
    unittest.main()
