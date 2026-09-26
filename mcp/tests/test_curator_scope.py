"""The ordinary curator list records authored semantic scope and refuses unfinished curation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import pytest
from test_knowledge_curator_ingest_list import (
    CODE_FILE,
    CODE_SYMBOL,
    _private_pair,
    entry,
    run,
    symbol,
    target,
)

pytestmark = pytest.mark.evidence_unit


def test_authored_scope_survives_ingest_and_scope_change_is_not_an_exact_retry(tmp_path: Path):
    pair = _private_pair(tmp_path / "source")
    authored = entry("budget", targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL))])
    authored["scope"] = {
        "applicability": "Retry attempts admitted by the shared budget.",
        "conditions": ["The caller supplies its remaining deadline."],
        "exclusions": ["Interactive retries outside this budget."],
    }
    first = run(pair, tmp_path, [authored])
    assert len(first.committed) == 1 and not first.refused
    database = Path(first.candidate_directory) / "knowledge-candidate.sqlite"
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        row = connection.execute(
            "SELECT applicability, conditions, exclusions FROM invariant_revision"
        ).fetchone()
    assert row is not None
    assert row[0] == authored["scope"]["applicability"]
    assert json.loads(row[1]) == authored["scope"]["conditions"]
    assert json.loads(row[2]) == authored["scope"]["exclusions"]
    replay = run(pair, tmp_path, [authored])
    assert len(replay.committed) == 1 and not replay.refused
    changed = deepcopy(authored)
    changed["scope"]["applicability"] = "All calls, including interactive retries."
    refused = run(pair, tmp_path, [changed])
    assert not refused.committed and len(refused.refused) == 1
    assert refused.refused[0].refusal.startswith("allocation_content_conflict:")


@pytest.mark.parametrize("scope", [None, {"applicability": "Guessed scope"}])
def test_unfilled_scope_is_a_named_per_entry_refusal_without_writes(tmp_path: Path, scope):
    pair = _private_pair(tmp_path / "source")
    authored = entry("unfilled", targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL))])
    authored["scope"] = scope
    planned = run(pair, tmp_path, [authored], dry_run=True)
    assert not planned.committed and len(planned.refused) == 1
    assert planned.refused[0].refusal.startswith("unfilled_curation_scope:")
    assert not (tmp_path / "candidate").exists()
    refused = run(pair, tmp_path, [authored])
    assert not refused.committed and len(refused.refused) == 1
    assert refused.refused[0].refusal.startswith("unfilled_curation_scope:")
    database = Path(refused.candidate_directory) / "knowledge-candidate.sqlite"
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM invariant_revision").fetchone() == (0,)
