"""The census's Git-reading inventory and its writer (MIK-R20).

* :mod:`.inventory` -- :func:`take_inventory`: pin the code and memory commits and inventory them
  mechanically, refusing an unreadable baseline;
* :mod:`.writer` -- :class:`CensusWriter`: every census write into a converted memory working tree,
  checked by the census rules before anything is written.

Reading, checks, measures and the report are in :mod:`agents_remember.memory_quality.knowledge_census`.
"""

from __future__ import annotations

from agents_remember.memory.knowledge_census.inventory import (
    BaselineSide,
    CensusBaselineError,
    build_inventory,
    governing_route,
    onboarding_routes,
    take_inventory,
)
from agents_remember.memory.knowledge_census.writer import CensusWriteError, CensusWriter

__all__ = [
    "BaselineSide",
    "CensusBaselineError",
    "CensusWriteError",
    "CensusWriter",
    "build_inventory",
    "governing_route",
    "onboarding_routes",
    "take_inventory",
]
