"""Shared fail-closed error for closeout queue application and lifecycle services."""

from agents_remember.errors import AgentsRememberError


class CloseoutQueueError(AgentsRememberError):
    """A queue request is malformed or violates the current mechanistic facts."""

    def __init__(self, status: str, detail: str) -> None:
        self.status = status
        super().__init__(f"{status}: {detail}")
