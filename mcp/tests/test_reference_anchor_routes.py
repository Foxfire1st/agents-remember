"""MIK-R198 rule 5 over a leaf pair whose only fault is a stale reference anchor.

The publication routes judge a leaf pair through one shared judgment: does this code commit and this
memory commit hold together? A reference anchor that no longer names the paired code it claims is a
fault that judgment refuses. These cases drive that judgment over the same pair twice -- once with the
anchor stale against the code, once with it repaired -- so the refusal is attributable to the anchor
and not to the fixture. The pair world is the closeout gate's own, imported rather than copied.
"""

from __future__ import annotations

import json
from typing import Any

from agents_remember.models.knowledge_files import canonical_text
from agents_remember.worktrees.modules import closeout_external
from test_knowledge_closeout_gate import (
    A,
    Gated,
    anchor_at,
    commit,
    ports,  # noqa: F401 -- the pytest fixture
    world,  # noqa: F401 -- the pytest fixture
)


def test_a_stale_reference_anchor_refuses_the_pair_the_publication_routes_judge(
    world: Gated,  # noqa: F811 -- imported so pytest resolves the closeout gate's own fixture
    ports: Any,  # noqa: F811 -- the same
) -> None:
    """A stale anchor refuses the pair judgment; repairing that anchor alone clears it.

    The judgment is the one every leaf publication makes about a (code commit, memory commit) pair,
    so this holds the shared rule at the boundary the routes cross rather than in the validator's own
    API: a pair whose reference anchor no longer names the code it claims is refused and the refusal
    names the rule, and the same judgment over the repaired pair carries no anchor debt.
    """

    ports()
    path = A
    document = json.loads((world.memory / f"onboarding/{path}.json").read_text(encoding="utf-8"))
    document["references"] = {
        "1": {
            "targets": [
                {
                    "kind": "code",
                    "anchor": anchor_at(
                        world.code, path, {"kind": "file"}, (world.code / path).read_bytes()
                    ),
                }
            ],
            "note": "The module as a whole is anchored here.",
        }
    }
    memory_tip = commit(world.memory, {f"onboarding/{path}.json": canonical_text(document)})

    # The cited source moves and nothing re-anchors the reference: the pair is refused.
    moved_code = commit(
        world.code, {path: (world.code / path).read_text(encoding="utf-8") + "\n# a later line\n"}
    )
    refused = closeout_external.leaf_pair_refusal(
        world.contract, code_commit=moved_code, memory_commit=memory_tip, bases=(world.memory_base,)
    )
    assert refused is not None
    assert "R198.1-reference-anchor" in refused, refused

    # Repairing the anchor is what clears it: the same call no longer carries the anchor debt.
    repaired = json.loads(json.dumps(document))
    repaired["references"]["1"]["targets"][0]["anchor"] = anchor_at(
        world.code, path, {"kind": "file"}, (world.code / path).read_bytes()
    )
    fixed_tip = commit(world.memory, {f"onboarding/{path}.json": canonical_text(repaired)})
    after = closeout_external.leaf_pair_refusal(
        world.contract, code_commit=moved_code, memory_commit=fixed_tip, bases=(world.memory_base,)
    )
    assert after is None or "R198.1-reference-anchor" not in after, after
