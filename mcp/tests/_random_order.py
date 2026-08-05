"""Deterministic collection-order randomization for the scheduled suite run."""

from __future__ import annotations

import random
from typing import Any


def shuffle_items(items: list[Any], seed: int) -> None:
    """Shuffle the collected tests reproducibly with the exact reported seed."""
    random.Random(seed).shuffle(items)
