"""How an explainer tool answers: refusals become data, never a crash (MIK-R204 @ v1 rule 1).

These two functions are the boundary every explainer tool body passes through, so a declaration module has
to reach them without loading the readers, writers and services behind them. They live here, importing only
the three refusal types -- each from its own light owner: ``canvas.explainer_state``, ``canvas.paths`` and
``canvas.presenter_refusals`` (MIK-R204 @ v1 rule 1). `canvas.explainer_access` re-exports the same objects,
so a caller's import and a declaration owner's import are the same function and cannot diverge.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.canvas.paths import PathRefused
from agents_remember.canvas.presenter_refusals import PresenterRefused


def refusal(reason: str, detail: str) -> dict[str, Any]:
    """One refused call, answered as data: the page or the tool shows the name, never a crash."""

    return {"state": "refused", "refusal": reason, "detail": detail}


def answered(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one tool body, turning its typed refusals into the same data shape."""

    try:
        return call()
    except (ExplainerRefused, PresenterRefused) as refused:
        return refusal(refused.reason, refused.detail)
    except PathRefused as held:
        return refusal("path-held", str(held))
    except ValueError as error:  # the kept authoring tools refuse by raising a named ValueError
        return refusal("refused", str(error))
    except OSError as error:
        return refusal("io-refused", str(error))


__all__ = ["answered", "refusal"]
