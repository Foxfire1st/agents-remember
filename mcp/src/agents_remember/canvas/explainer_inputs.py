"""The bound seat's own inputs, in a light canonical owner (MIK-R204 @ v1 rule 1).

For a bound seat the registration entry runs *during* discovery, so everything that entry touches at
registration time is a discovery dependency. `ExplainerInputs`, the coordination root readback and the clock
therefore live here, importing only `dataclasses`, `pathlib`, `collections.abc`, `datetime` and the refusal
type — and `canvas.explainer_server` re-exports the same objects because `__all__` has always named
`ExplainerInputs` there and callers import it from that path.

Identity is not claimed in prose: `tests/test_explainer_inputs.py` asserts `is` for the class and both helpers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.canvas.explainer_state import ExplainerRefused

#: Reads the bound fingerprints for a set of names; the identity the judgment carries.
FingerprintReader = Callable[[tuple[str, ...]], Mapping[str, str]]

#: Files one report to the master's architect and answers its tray path.
ReportSender = Callable[[str, str], str]


@dataclass(frozen=True)
class ExplainerInputs:
    """The product-side facts one tool server needs beside its workspace.

    ``task_root`` is the leaf's task folder, where the retained pages, the reference folder, the
    grade store and the report log lie. ``coordination_root`` is where the developer messages and
    the knowledge index cache are; it is read back from the workspace's own place when omitted.
    """

    task_root: Path | None = None
    coordination_root: Path | None = None
    code_repository: Path | None = None
    memory_repository: Path | None = None
    fingerprints: FingerprintReader | None = None
    sender: ReportSender | None = None
    now: Callable[[], str] | None = None


def coordination_of(workspace: Path) -> Path:
    """``<coordination>/explainer/<repository>/<task>/<leaf>`` read back to its coordination root."""

    if len(workspace.parents) < 4:
        raise ExplainerRefused(
            "workspace-unplaced",
            f"{workspace} is not <coordination>/explainer/<repository>/<task>/<leaf>",
        )
    return workspace.parents[3]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
