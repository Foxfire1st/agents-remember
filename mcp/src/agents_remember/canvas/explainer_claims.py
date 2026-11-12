"""The explainer's authored claim map, admitted from the captured revision assets.

The immutable evidence input stays unchanged. Claims are authored output, checked by
existing models and readiness against that input, and carried into the published revision.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from agents_remember.canvas.explainer_errors import ExplainerRefused
from agents_remember.models.knowledge.review_evidence import ClaimMap, EvidencePacket

CLAIM_MAP_ASSET = "claim-map.json"


def with_authored_claims(packet: EvidencePacket, assets: Mapping[str, bytes]) -> EvidencePacket:
    """Admit the canonical claim-map asset after confined asset collection, if supplied."""

    raw = assets.get(CLAIM_MAP_ASSET)
    if raw is None:
        return packet
    try:
        claims = ClaimMap.model_validate_json(raw)
    except ValidationError as error:
        raise ExplainerRefused("claim-map-invalid", str(error)) from error
    return packet.model_copy(update={"claims": claims})
