"""The identity column each attachment endpoint kind populates in the derived index's attachment table.

The authored facet payload models, the explanation subjects and the authored commands lived here
while the canonical database stored facets; facets are text files now (``models/knowledge_files``),
and only the endpoint vocabulary the index's reader still queries remains.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

# The route is deliberately absent: it is the envelope's own association, and a second route
# mechanism here would be a competing one.
AttachmentEndpointKind = Literal[
    "invariant_revision",
    "family_revision",
    "source_anchor",
    "realization_claim",
]


# The identity column each endpoint kind populates in ``facet_attachment``. The stored row carries a
# checked foreign-key group per kind and a constraint that exactly one group is populated and
# matches the stored kind, so which column an endpoint lands in is a fact of the table rather than a
# convention the read path remembers.
ENDPOINT_COLUMNS: Mapping[AttachmentEndpointKind, str] = {
    "invariant_revision": "invariant_revision_id",
    "family_revision": "family_revision_id",
    "source_anchor": "anchor_id",
    "realization_claim": "claim_id",
}


__all__ = ["ENDPOINT_COLUMNS", "AttachmentEndpointKind"]
