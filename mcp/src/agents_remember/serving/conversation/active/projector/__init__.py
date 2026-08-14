"""Compatibility export surface for the decomposed active projector."""

from .facade import ActiveSessionProjector
from .mutation_stream import CLOSE_SENTINEL
from .native_ingestion import EvidenceTimelineRegressed, ZipperEvidenceEvicted
from .rebuild_coordinator import PageResult

__all__ = [
    "CLOSE_SENTINEL",
    "ActiveSessionProjector",
    "EvidenceTimelineRegressed",
    "PageResult",
    "ZipperEvidenceEvicted",
]
