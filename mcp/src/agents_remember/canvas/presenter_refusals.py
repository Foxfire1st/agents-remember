"""The review-presenter capability's one refusal type, in a light owner (MIK-R204 @ v1 rule 1).

`PresenterRefused` is raised by the store, by the assets guard and by the presenter's own acts, and it is
caught by the answer boundary every explainer tool passes through. Those two sides sit on opposite sides of
the import boundary: the store pulls the frame, evidence and comparison implementations (and, through them,
the worktree packages), while a declaration module that only needs to *catch* a refusal must load none of
that. So the class lives here, importing nothing, and `canvas.presenter_store` imports it back and keeps its
own public name.

One class, not two: `presenter_store.PresenterRefused` is this object, so an `except` written against
either spelling catches the same exception, and `StartOutcomeUnknown` still subclasses it.
"""

from __future__ import annotations


class PresenterRefused(ValueError):
    """A presenter act refused with one nameable reason and an actionable detail."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


__all__ = ["PresenterRefused"]
