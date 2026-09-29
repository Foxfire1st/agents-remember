"""Converted fixture memory trees for the MIK-R22 validator tests.

:func:`fixture_tree_files` assembles the MIK-R21 Doc14 §4 fixtures (``fixtures/knowledge_files``)
into one converted memory tree: the layout marker, the invariant, family, decision and incident
records, the file and route sidecars of ``direct_landing.py``, its test, ``integrate.py`` and the
``worktrees`` route, each beside its Markdown card. The two invariants the fixtures name but do not
define (the family's second member and the decision's link target) are added, so the tree is whole.
The Doc14 §4.2 family's other realizations (``ledger_projection.py``, ``source.py``,
``authorship.py`` and ``curator_source_manifest.py``) get their sidecars, so its three routes satisfy
MIK-R04's Coverage and Non-empty. Every anchor path is a real path of this repository, listed in
:data:`CODE_PATHS`.
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
FAMILY = "knowledge/families/FAM-SEQNTS6C-attribution-and-landing-pairing.json"
# The Doc14 §4.2 family's other realizations: (source file, entry ID, symbol, blob).
FAMILY_REALIZATIONS = (
    (
        "mcp/src/agents_remember/worktrees/ledger_projection.py",
        "RLZ-PR0J3C",
        "resolve_memory_source_commit",
        "4928d1695e5b94efd6ce1e9eedcebf1c1caa9da5",
    ),
    (
        "mcp/src/agents_remember/models/knowledge/source.py",
        "RLZ-S0VRCE",
        "SourceAnchor",
        "26d29178fa3d9db0a179257bcf598df2ec146a14",
    ),
    (
        "mcp/src/agents_remember/models/knowledge/authorship.py",
        "RLZ-AVTH0R",
        "Authorship",
        "a5f7db9de3b6cbbd9f0b3b71938b820d857a14ae",
    ),
    (
        "mcp/src/agents_remember/application/curator_source_manifest.py",
        "RLZ-MAN1F5",
        "read_source_plane",
        "44f4415f06feeda21cb1905369184ea58ccdcf4b",
    ),
)
CODE_PATHS = frozenset(
    {
        DIRECT_LANDING,
        INTEGRATE,
        TEST_DIRECT_LANDING,
        LIFECYCLE_DIRECT_LANDING,
        *(path for path, *_ in FAMILY_REALIZATIONS),
    }
)

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


# The Doc14 §4.2 family is an export (``legacy-unassessed``), so every validation of the fixture tree
# carries MIK-R27's one report-only count of the live records still unassessed.
LEGACY_COUNT = "R27.4-legacy-unassessed"

# The two added invariants have no realization or proof of their own, so they claim only a criterion
# the validator does not check (MIK-R27: ``spans_locations`` and ``guarded_by_test`` are checked).
UNCHECKED_ADMISSION: dict[str, Any] = {
    "criteria": ["prevents_costly_mistake"],
    "justification": "A landing that pairs the wrong commits corrupts the memory ledger.",
}


def invariant_document(identifier: str, **changes: Any) -> dict[str, Any]:
    document = load_fixture("4.3-invariant-landing-pair.json")
    document["id"] = identifier
    document.update(changes)
    return document


def invariant_path(identifier: str, slug: str = "invariant") -> str:
    return f"knowledge/invariants/{identifier}-{slug}.json"


def realization_sidecar(
    path: str, entry: str, symbol: str, blob: str, invariant: str = "INV-7K3F9Q"
) -> dict[str, Any]:
    """A file sidecar whose one ``realizes`` entry anchors ``symbol`` of ``path``."""

    return {
        "path": path,
        "realizes": [
            {
                "anchor": {
                    "blob": blob,
                    "content": "sha256:" + "0" * 64,
                    "locator": {"kind": "symbol", "name": symbol},
                },
                "id": entry,
                "invariant": invariant,
                "rationale": f"{symbol} keeps what a record claims about its inputs exact.",
                "role": "enforcement",
            }
        ],
        "references": {},
        "schema": "ar-onboarding-file/v1",
    }


def fixture_tree_files() -> dict[str, bytes]:
    """The converted fixture tree, path to canonical bytes."""

    files = {
        "knowledge/layout.json": encode(load_fixture("layout.json")),
        INVARIANT: encode(load_fixture("4.3-invariant-landing-pair.json")),
        invariant_path("INV-R8M2TD"): encode(
            invariant_document("INV-R8M2TD", admission=UNCHECKED_ADMISSION)
        ),
        invariant_path("INV-C0VR4G"): encode(
            invariant_document("INV-C0VR4G", admission=UNCHECKED_ADMISSION)
        ),
        FAMILY: encode(load_fixture("4.2-family-attribution-and-landing-pairing.json")),
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
    for path, entry, symbol, blob in FAMILY_REALIZATIONS:
        files[f"onboarding/{path}.json"] = encode(realization_sidecar(path, entry, symbol, blob))
        files[f"onboarding/{path}.md"] = f"# {path}\n".encode()
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
