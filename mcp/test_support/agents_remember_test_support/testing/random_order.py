"""Deterministic collection-order randomization for scheduled pytest runs."""

from __future__ import annotations

import random
from typing import Any


def shuffle_items(items: list[Any], seed: int) -> None:
    random.Random(seed).shuffle(items)
