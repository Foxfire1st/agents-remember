"""Converted fixture memory trees for the MIK-R22 validator tests.

:func:`fixture_tree_files` assembles the MIK-R21 Doc14 §4 fixtures (``fixtures/knowledge_files``)
into one converted memory tree: the layout marker, the invariant, family, decision and incident
records, the file and route sidecars of ``direct_landing.py``, its test, ``integrate.py`` and the
``worktrees`` route, each beside its Markdown card. The two invariants the fixtures name but do not
define (the family's second member and the decision's link target) are added, so the tree is whole.
Every anchor path is a real path of this repository, listed in :data:`CODE_PATHS`.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from agents_remember.memory_quality.knowledge_validator import CodePathSet, KnowledgeTree
from agents_remember.models.knowledge_files import canonical_text

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge_files"

DIRECT_LANDING = "mcp/src/agents_remember/worktrees/direct_landing.py"
INTEGRATE = "mcp/src/agents_remember/worktrees/modules/integrate.py"
TEST_DIRECT_LANDING = "mcp/tests/test_direct_landing.py"
LIFECYCLE_DIRECT_LANDING = "mcp/src/agents_remember/application/lifecycle/direct_landing.py"
CODE_PATHS = frozenset({DIRECT_LANDING, INTEGRATE, TEST_DIRECT_LANDING, LIFECYCLE_DIRECT_LANDING})

INVARIANT = "knowledge/invariants/INV-7K3F9Q-landing-pair.json"
DIRECT_LANDING_CARD = f"onboarding/{DIRECT_LANDING}.md"
DIRECT_LANDING_SIDECAR = f"onboarding/{DIRECT_LANDING}.json"
INTEGRATE_CARD = f"onboarding/{INTEGRATE}.md"
INTEGRATE_SIDECAR = f"onboarding/{INTEGRATE}.json"
ROUTE_CARD = "onboarding/mcp/src/agents_remember/worktrees/overview.md"
ROUTE_SIDECAR = "onboarding/mcp/src/agents_remember/worktrees/overview.json"

DIRECT_LANDING_MARKDOWN = """# mcp/src/agents_remember/worktrees/direct_landing.py

## Purpose
Coordinates branch-addressed delivery. It verifies already committed code [2], accepts an exact
memory candidate [3], and creates or observes one direct-landing journal generation [4].

## Logic
`DirectLandingRequest` carries the exact code commit [1]. The application boundary owns configured
admission and serialized execution [6]; `signals[0]` and \\[9] are text, not markers.

```python
journal[1] = "fenced code holds no marker"
```

## Boundaries
- Apply requires external memory and the exact pre-commit candidate tree [5].
- Cached rows, bytes, or absence have no admission or recovery authority [7].
"""


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def encode(document: dict[str, Any]) -> bytes:
    return canonical_text(document).encode("utf-8")


def invariant_document(identifier: str, **changes: Any) -> dict[str, Any]:
    document = load_fixture("4.3-invariant-landing-pair.json")
    document["id"] = identifier
    document.update(changes)
    return document


def invariant_path(identifier: str, slug: str = "invariant") -> str:
    return f"knowledge/invariants/{identifier}-{slug}.json"


def fixture_tree_files() -> dict[str, bytes]:
    """The converted fixture tree, path to canonical bytes."""

    files = {
        "knowledge/layout.json": encode(load_fixture("layout.json")),
        INVARIANT: encode(load_fixture("4.3-invariant-landing-pair.json")),
        invariant_path("INV-R8M2TD"): encode(invariant_document("INV-R8M2TD")),
        invariant_path("INV-C0VR4G"): encode(invariant_document("INV-C0VR4G")),
        "knowledge/families/FAM-SEQNTS6C-attribution-and-landing-pairing.json": encode(
            load_fixture("4.2-family-attribution-and-landing-pairing.json")
        ),
        "knowledge/decisions/DEC-D12RTE-local-family-routes.json": encode(
            load_fixture("4.5-decision-d12-local-family-routes.json")
        ),
        "knowledge/incidents/INC-5WQ8HB-proof-evidence-dropped.json": encode(
            load_fixture("4.6-incident.json")
        ),
        DIRECT_LANDING_SIDECAR: encode(load_fixture("4.1-direct_landing.py.json")),
        DIRECT_LANDING_CARD: DIRECT_LANDING_MARKDOWN.encode("utf-8"),
        ROUTE_SIDECAR: encode(load_fixture("4.1-worktrees-overview.json")),
        ROUTE_CARD: b"# worktrees\n\nThe route's files are listed with their anchors [1].\n",
        f"onboarding/{TEST_DIRECT_LANDING}.json": encode(
            load_fixture("4.4-test_direct_landing.py.json")
        ),
        f"onboarding/{TEST_DIRECT_LANDING}.md": b"# test_direct_landing.py\n",
        INTEGRATE_SIDECAR: encode(load_fixture("r21-integrate.py.json")),
        INTEGRATE_CARD: b"# mcp/src/agents_remember/worktrees/modules/integrate.py\n",
    }
    return files


def tree(files: dict[str, bytes], label: str = "candidate") -> KnowledgeTree:
    return KnowledgeTree(label=label, files=dict(files))


def code(paths: frozenset[str] = CODE_PATHS, label: str = "code") -> CodePathSet:
    return CodePathSet(label=label, paths=paths)


def edit_json(files: dict[str, bytes], path: str, change: Any) -> dict[str, bytes]:
    """Return a copy of ``files`` whose JSON at ``path`` was changed by ``change(document)``."""

    edited = dict(files)
    document = copy.deepcopy(json.loads(edited[path]))
    change(document)
    edited[path] = encode(document)
    return edited


def write_tree(root: Path, files: dict[str, bytes]) -> None:
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
