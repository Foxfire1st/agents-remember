"""Final full memory-coherence certification (CCR-R08 Gate 5).

The complete validation surface that certifies full memory coherence over one exact
code/memory candidate pair: the deterministic complete final catalog (plan and
content-addressed subresults), the Gate 1-4 prerequisite adapter, the coherence
record and candidate-pair authority binding, and the R21 Gate-5 semantic-input
assembly. The certification itself never mutates code or memory.
"""

from agents_remember.certification.final_certification_models import (
    FinalCatalogItemIdentity,
    FinalCatalogItemResult,
)
from agents_remember.memory_quality.final_certification.catalog import (
    FINAL_FULL_CATALOG_VERSION,
    complete_final_catalog,
    final_catalog_readiness,
)

__all__ = [
    "FINAL_FULL_CATALOG_VERSION",
    "FinalCatalogItemIdentity",
    "FinalCatalogItemResult",
    "complete_final_catalog",
    "final_catalog_readiness",
]
