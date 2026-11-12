"""Named explainer refusals, shared without importing workspace or durable state."""


class ExplainerRefused(ValueError):
    """A named refusal. The reason is a value the page or the tool shows; never a server error."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


class ExplainerStateError(ValueError):
    """A durable explainer fact could not be read or is not what it claims."""
