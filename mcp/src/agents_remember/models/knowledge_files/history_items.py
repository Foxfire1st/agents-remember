"""The token boundary for newly authored history, not stored label parsing."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

_ITEM_TOKEN = re.compile(r"sha256:[0-9a-fA-F]{64}")


def invalid_history_items(items: Iterable[str]) -> Iterator[str]:
    """Yield each invalid token verbatim; never normalize historical labels."""

    for item in items:
        if _ITEM_TOKEN.fullmatch(item) is None:
            yield item
