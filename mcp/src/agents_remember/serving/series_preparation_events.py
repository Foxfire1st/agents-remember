"""Offer one watch batch without I/O, and name bounded preparation overload once."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)


def offer_series_changes(
    offer: Callable[[str, str], bool] | None,
    owners: Iterable[tuple[str, str]],
) -> None:
    if offer is None:
        return
    dropped = sum(not offer(*owner) for owner in owners)
    if dropped:
        logger.warning(
            "series preparation queue declined %d master offer(s) in this batch", dropped
        )
