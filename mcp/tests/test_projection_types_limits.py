"""MIK-R190 rule 13: the generated projection mirrors the served provider limit exactly.

The dashboard's contract files are generated from the Python wire schema. The served limit states
its exact reset moments as date-times, and the generator must keep that constraint instead of
refusing the schema or a hand-maintained substitute dropping it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "mcp" / "test_support"))
sys.path.insert(0, str(REPO_ROOT / "mcp" / "src"))

from agents_remember_test_support.code_quality.projection_types import (
    SCHEMA_OUTPUT,
    TYPESCRIPT_OUTPUT,
    generated_files,
    stale_generated_files,
    workspace_projection_schema,
)

PRODUCER = workspace_projection_schema()
# The generator hands back a plain object tree; the cases index the definitions it holds.
DEFINITIONS = cast("dict[str, dict[str, Any]]", PRODUCER["$defs"])


def generated_schema() -> dict:
    return json.loads(generated_files()[SCHEMA_OUTPUT])


def test_the_producer_states_the_limits_exact_moments() -> None:
    """The owning schema is the producer's, and its date-times carry their format."""
    assert DEFINITIONS["ProviderLimit"]["properties"]["until"]["format"] == "date-time"
    assert DEFINITIONS["LimitedSeat"]["properties"]["resetsAt"]["format"] == "date-time"


def test_the_generated_schema_repeats_the_producer_definition_for_definition() -> None:
    """Producer-schema equality: no dropped constraint and no renamed substitute."""
    schema = generated_schema()

    for name in ("ProviderLimit", "LimitedSeat"):
        assert schema["$defs"][name] == DEFINITIONS[name]
    assert "ProviderLimitSeat" not in schema["$defs"]
    assert schema["$defs"]["LimitedSeat"]["properties"]["resetsAt"]["format"] == "date-time"
    assert schema["$defs"]["ProviderLimit"]["properties"]["until"]["format"] == "date-time"


def test_the_generated_typescript_names_the_producers_limit_and_keeps_the_refinement() -> None:
    """The TypeScript mirror is the producer's model, with the served instant's format beside it."""
    text = generated_files()[TYPESCRIPT_OUTPUT]

    assert "export interface LimitedSeat {" in text
    assert "ProviderLimitSeat" not in text
    assert '  /** JSON Schema refinements: {"format":"date-time"} */\n  resetsAt: string;' in text
    assert '  /** JSON Schema refinements: {"format":"date-time"} */\n  until: string;' in text


def test_the_committed_contract_files_are_the_generated_ones() -> None:
    """The canonical drift check passes on the bytes in the tree."""
    assert stale_generated_files(REPO_ROOT) == ()
