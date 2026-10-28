"""The bound seat's own inputs, in a light canonical owner (MIK-R204 @ v1 rule 1).

For a bound seat the registration entry runs *during* discovery, so everything that entry touches at
registration time is a discovery dependency. `ExplainerInputs` and the clock therefore live here, importing
only `dataclasses`, `pathlib`, `collections.abc` and `datetime` -- and `canvas.explainer_server` re-exports
`ExplainerInputs` because `__all__` has always named it there and callers import it from that path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

#: Reads the bound fingerprints for a set of names; the identity the judgment carries.
FingerprintReader = Callable[[tuple[str, ...]], Mapping[str, str]]


@dataclass(frozen=True)
class ExplainerInputs:
    """The product-side facts the four tools need beside their workspace.

    ``task_root`` is the leaf's task folder, where the published revisions and the grade store lie.
    """

    task_root: Path | None = None
    fingerprints: FingerprintReader | None = None
    now: Callable[[], str] | None = None


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
