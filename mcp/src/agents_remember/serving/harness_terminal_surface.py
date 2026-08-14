"""Readable transcript rendering and prompt input for the existing hosted xterm surface."""

from __future__ import annotations

from dataclasses import dataclass, field

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    SubmissionReceipt,
)
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_models import (
    TranscriptEntry,
    UncommittedDraft,
)


@dataclass
class HarnessTerminalSurface:
    """Own the human draft while routing only committed whole messages through the bridge.

    Draft preservation is a surface obligation, not an adapter or pane heuristic: automated
    delivery is submitted separately and has no path that can inject into, submit, or discard
    ``draft``. Only the human-facing ``update_draft`` and ``submit_draft`` operations touch it.
    """

    bridge: HarnessControlBridge
    _draft: UncommittedDraft = field(default_factory=UncommittedDraft, init=False)

    @property
    def draft(self) -> UncommittedDraft:
        return self._draft

    def update_draft(self, text: str) -> UncommittedDraft:
        """Replace the human-owned draft without sending it to the adapter."""

        self._draft = UncommittedDraft(text=text, revision=self._draft.revision + 1)
        return self._draft

    async def submit_draft(self, *, request_id: str | None = None) -> SubmissionReceipt:
        """Commit the current draft as the next whole terminal message.

        A rejected, unsupported, or ambiguous submission retains the draft. A newer edit made while
        the committed revision is in flight is also retained.
        """

        draft = self._draft
        if not draft.text:
            raise HarnessControlError("cannot submit an empty human draft")
        receipt = await self.submit_terminal(draft.text, request_id=request_id)
        if receipt.acceptance in {"immediate", "queued"} and self._draft.revision == draft.revision:
            self._draft = UncommittedDraft(revision=draft.revision + 1)
        return receipt

    async def submit_terminal(
        self, text: str, *, request_id: str | None = None
    ) -> SubmissionReceipt:
        """Submit caller-committed terminal text as one whole message, never draft keystrokes."""

        return await self.bridge.submissions().submit(
            self.bridge.prompt(text, source="terminal", request_id=request_id)
        )

    async def submit_durable(
        self, text: str, *, request_id: str | None = None
    ) -> SubmissionReceipt:
        """Submit one automated message without reading or mutating the human draft."""

        return await self.bridge.submissions().submit(
            self.bridge.prompt(text, source="durable", request_id=request_id)
        )

    def render(self, *, after_sequence: int = 0, limit: int = 500) -> bytes:
        lines = [
            _render_entry(entry)
            for entry in self.bridge.transcript(after_sequence=after_sequence, limit=limit)
        ]
        return ("\r\n".join(lines) + ("\r\n" if lines else "")).encode("utf-8")


def _render_entry(entry: TranscriptEntry) -> str:
    request = f" request={entry.request_id}" if entry.request_id else ""
    result = f" result={entry.terminal_result.outcome}" if entry.terminal_result is not None else ""
    return f"[{entry.created_at}] {entry.role}{request}{result}: {_readable(entry.text)}"


def _readable(text: str) -> str:
    """Keep newlines/tabs but strip terminal control bytes from vendor-provided transcript text."""

    return "".join(character for character in text if character in "\n\t" or ord(character) >= 32)
