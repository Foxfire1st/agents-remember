"""Branch-safe stable IDs for knowledge records and sidecar entries (MIK-R21 rule 2).

An ID is ``<KIND>-<body>``, where the body is Crockford base32 (``0-9`` and ``A-Z`` without ``I``,
``L``, ``O`` and ``U``):

* **Minted IDs** have a 6-character body drawn at random by :func:`mint_id`, for example
  ``INV-7K3F9Q``. Randomness is what makes them branch-safe: two parallel leaves never coordinate,
  so a sequential counter (``INV-0143``) would be minted twice. Thirty bits make a collision
  between two leaves' mints negligible, and the validator's uniqueness check (MIK-R22) refuses the
  one that does occur.
* **Derived IDs** have an 8-character body and are used only for records and entries exported from
  the legacy knowledge database (MIK-R24 rule 4). The body is the first eight Crockford base32
  characters of the SHA-256 digest of the derivation material, read most significant bit first.
  :func:`derived_record_id` derives from ``origin.legacyId``; :func:`derived_realization_id`
  derives from the invariant's legacy ID, the path and the locator, so it does not change when the
  legacy claim ID does. The same material always yields the same ID on every line.

Record kinds and their prefixes, the two entry kinds, and the history row (``ROW-``, MIK-R07), are
closed vocabularies declared here once.
A filename's slug is display only (``<ID>-<slug>.json``): renaming a slug never changes the ID.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Mapping
from typing import Final, Literal

CROCKFORD_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MINTED_BODY_LENGTH: Final = 6
DERIVED_BODY_LENGTH: Final = 8

RecordKind = Literal[
    "invariant",
    "family",
    "decision",
    "incident",
    "assumption",
    "limitation",
    "failure_mode",
    "scenario",
    "diagnostic",
    "term",
]
EntryKind = Literal["realization", "proof"]
RowKind = Literal["history_row"]

# The one table of record kinds: kind -> (ID prefix, directory under ``knowledge/``, schema name).
RECORD_PREFIXES: Final[Mapping[RecordKind, str]] = {
    "invariant": "INV",
    "family": "FAM",
    "decision": "DEC",
    "incident": "INC",
    "assumption": "ASM",
    "limitation": "LIM",
    "failure_mode": "FLM",
    "scenario": "SCN",
    "diagnostic": "DGN",
    "term": "TRM",
}
ENTRY_PREFIXES: Final[Mapping[EntryKind, str]] = {"realization": "RLZ", "proof": "PRF"}
# A history row's ID (MIK-R07 rule 1): minted by the writer like every other ID, never derived.
ROW_PREFIXES: Final[Mapping[RowKind, str]] = {"history_row": "ROW"}

_BODY: Final = f"[{CROCKFORD_ALPHABET}]{{{MINTED_BODY_LENGTH}}}(?:[{CROCKFORD_ALPHABET}]{{2}})?"


def id_pattern(*prefixes: str) -> str:
    """Return the anchored regular expression for IDs with any of ``prefixes``."""

    return rf"^(?:{'|'.join(prefixes)})-{_BODY}$"


RECORD_ID_PATTERN: Final = id_pattern(*RECORD_PREFIXES.values())
REALIZATION_ID_PATTERN: Final = id_pattern(ENTRY_PREFIXES["realization"])
PROOF_ID_PATTERN: Final = id_pattern(ENTRY_PREFIXES["proof"])
ENTRY_ID_PATTERN: Final = id_pattern(*ENTRY_PREFIXES.values())
ROW_ID_PATTERN: Final = id_pattern(ROW_PREFIXES["history_row"])


_ALL_PREFIXES: Final[Mapping[str, str]] = {**RECORD_PREFIXES, **ENTRY_PREFIXES, **ROW_PREFIXES}


def _prefix(kind: RecordKind | EntryKind | RowKind) -> str:
    try:
        return _ALL_PREFIXES[kind]
    except KeyError:
        raise ValueError(f"unknown knowledge ID kind: {kind!r}") from None


def mint_id(kind: RecordKind | EntryKind | RowKind) -> str:
    """Mint a new random ID for ``kind``, for example ``INV-7K3F9Q``."""

    body = "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(MINTED_BODY_LENGTH))
    return f"{_prefix(kind)}-{body}"


def crockford_base32(data: bytes, length: int) -> str:
    """Encode the leading ``length * 5`` bits of ``data`` in Crockford base32, MSB first."""

    if length * 5 > len(data) * 8:
        raise ValueError("not enough input bits for the requested length")
    number = int.from_bytes(data, "big")
    shift = len(data) * 8
    characters = []
    for index in range(length):
        value = (number >> (shift - 5 * (index + 1))) & 0b11111
        characters.append(CROCKFORD_ALPHABET[value])
    return "".join(characters)


def derived_id(kind: RecordKind | EntryKind, material: bytes) -> str:
    """Derive an 8-character ID for ``kind`` from ``material`` (MIK-R24 rule 4)."""

    digest = hashlib.sha256(material).digest()
    return f"{_prefix(kind)}-{crockford_base32(digest, DERIVED_BODY_LENGTH)}"


def derived_record_id(kind: RecordKind, legacy_id: str) -> str:
    """Derive an exported record's ID from its ``origin.legacyId`` (UTF-8 bytes)."""

    if not legacy_id:
        raise ValueError("a derived record ID needs a nonempty legacy ID")
    return derived_id(kind, legacy_id.encode("utf-8"))


def derived_realization_id(
    invariant_legacy_id: str, path: str, locator: Mapping[str, object]
) -> str:
    """Derive an exported realization entry's ID from (invariant legacy ID, path, locator).

    The material is the UTF-8 bytes of the compact, key-sorted JSON array
    ``[invariant_legacy_id, path, locator]`` (separators ``,`` and ``:``, non-ASCII unescaped).
    ``locator`` is the **new sidecar locator form** exactly as the sidecar stores it --
    ``{"kind": "symbol", "name": …}``, ``{"kind": "line_range", "start": …, "end": …}`` or
    ``{"kind": "file"}`` -- never the legacy ``qualified_name``/``language`` or
    ``start_line``/``end_line`` spelling: the conversion (MIK-R24) translates the legacy locator
    first and derives from the result. Fixing one serialization here is what makes the ID the same
    on every line that converts the same claim; a fixed expected-value test pins it.
    """

    if not invariant_legacy_id or not path:
        raise ValueError("a derived realization ID needs the invariant legacy ID and the path")
    material = json.dumps(
        [invariant_legacy_id, path, dict(locator)],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return derived_id("realization", material.encode("utf-8"))
