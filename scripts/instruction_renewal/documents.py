"""Replace one generated JSON part without reformatting its authored neighbors."""

from __future__ import annotations

import json
import re


def value_spans(text: str) -> dict[str, tuple[int, int]]:
    """Find the value tokens of a top-level JSON object."""
    decoder = json.JSONDecoder()
    position = text.index("{") + 1
    spans = {}
    while True:
        position = _skip_space(text, position)
        if text[position] == "}":
            return spans
        key, position = decoder.raw_decode(text, position)
        position = _skip_space(text, position)
        if text[position] != ":":
            raise ValueError("expected a JSON member separator")
        start = _skip_space(text, position + 1)
        _, end = decoder.raw_decode(text, start)
        spans[key] = (start, end)
        position = _skip_space(text, end)
        if text[position] == ",":
            position += 1


def _skip_space(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def replace_json_value(payload: bytes, key: str, value: object) -> bytes:
    """Replace a complete top-level value while retaining unrelated raw bytes."""
    text = payload.decode("utf-8")
    spans = value_spans(text)
    match = re.search(r'^([ \t]+)"', text, re.MULTILINE)
    prefix = match[1] if match else "  "
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    encoded = encoded.replace("\n", "\n" + prefix)
    if key in spans:
        start, end = spans[key]
        return (text[:start] + encoded + text[end:]).encode("utf-8")
    closing = text.rfind("}")
    separator = "," if spans else ""
    addition = separator + "\n" + prefix + json.dumps(key) + ": " + encoded + "\n"
    return (text[:closing].rstrip() + addition + text[closing:]).encode("utf-8")


def replace_derived(payload: bytes, derived: dict) -> bytes:
    """Publish the complete generated part and leave authored parts untouched."""
    return replace_json_value(payload, "derived", derived)
