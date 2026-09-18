"""Incremental NDJSON decoding for eve's durable session stream.

The stream is read as bytes and split on newlines because a frame can span reads; the absolute
event index is derived here, not from any field the server sends, so a caller that persists the
returned index can resume with zero gap and zero duplicate.
"""

from __future__ import annotations

from collections.abc import Iterable

from agents_remember.errors import HarnessControlError
from agents_remember.serving.eve_protocol import EveStreamEvent, parse_event_frame

DEFAULT_MAX_FRAME_BYTES = 1024 * 1024


class EveNdjsonDecoder:
    """One stream connection's incremental frame decoder with a fixed frame ceiling."""

    def __init__(self, *, start_index: int = 0, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES):
        if start_index < 0:
            raise HarnessControlError("eve stream start index must be nonnegative")
        if max_frame_bytes < 1:
            raise HarnessControlError("eve stream frame ceiling must be positive")
        self._buffer = bytearray()
        self._index = start_index
        self._max_frame_bytes = max_frame_bytes

    @property
    def next_index(self) -> int:
        """The absolute index the next decoded frame will carry."""

        return self._index

    def feed(self, chunk: bytes) -> list[EveStreamEvent]:
        """Consume one transport read and return every frame it completed, in stream order."""

        self._buffer.extend(chunk)
        if len(self._buffer) > self._max_frame_bytes and b"\n" not in self._buffer:
            raise HarnessControlError("eve stream frame exceeded its bounded size")
        events: list[EveStreamEvent] = []
        while (newline := self._buffer.find(b"\n")) != -1:
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            event = self._decode(line)
            if event is not None:
                events.append(event)
        return events

    def finish(self) -> list[EveStreamEvent]:
        """Decode a trailing frame that arrived without its newline, then reset the buffer."""

        line = bytes(self._buffer).strip()
        self._buffer.clear()
        event = self._decode(line)
        return [] if event is None else [event]

    def _decode(self, line: bytes) -> EveStreamEvent | None:
        text = line.strip()
        if not text:
            # eve may keep the connection open with blank keep-alive lines; they are not events
            # and must not consume an index, or the persisted cursor would drift from the record.
            return None
        if len(text) > self._max_frame_bytes:
            raise HarnessControlError("eve stream frame exceeded its bounded size")
        event = parse_event_frame(text.decode("utf-8"), index=self._index)
        self._index += 1
        return event


def join_frame_text(events: Iterable[EveStreamEvent], key: str) -> str:
    """Concatenate one delta field across events in stream order."""

    parts: list[str] = []
    for event in events:
        value = event.data.get(key)
        if isinstance(value, str):
            parts.append(value)
    return "".join(parts)
