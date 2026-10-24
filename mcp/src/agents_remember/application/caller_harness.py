"""Which host provider is the caller's own harness, when the caller says so.

MIK-R196 rule 1 starts the free seat on the caller's own harness, at that harness's own default
model and effort, and forbids selecting the first harness the host happens to offer when the
caller's own cannot be hosted. Two things make that possible without inventing a harness:

* the **signal** is the MCP client name the harness itself sends in its ``initialize`` request. It
  is what the transport reports about its client, not an argument a caller can set;
* the **match** compares that name with the provider ids the host reports: equal, one a leading
  part of the other, or one a whole dash-separated segment of the other. No harness name is written
  in this module, so the launch code names no harness and AR invents none.

A name that matches nothing leaves the identity unknown. Unknown is reported as unknown: nothing
here substitutes a provider for the caller's own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_NON_NAME = re.compile(r"[^a-z0-9]+")


def normalize_harness_name(name: str | None) -> str | None:
    """``name`` as a comparable key: lower case, single separators, nothing around it."""

    if not isinstance(name, str):
        return None
    folded = _NON_NAME.sub("-", name.strip().lower()).strip("-")
    return folded or None


def provider_for_caller(harness_name: str | None, offered: Iterable[str]) -> str | None:
    """The provider id that is this caller's own harness, or ``None`` when none is.

    ``offered`` is the catalog's provider ids. A client name that says more than an id matches it
    on the leading part; an id that says more than the client name matches on a whole segment.
    Anything else matches nothing.
    """

    key = normalize_harness_name(harness_name)
    if key is None:
        return None
    key_parts = key.split("-")
    for one in offered:
        provider = normalize_harness_name(one)
        if not provider:
            continue
        if key == provider:
            return one
        if key.startswith(f"{provider}-") or provider.startswith(f"{key}-"):
            return one
        if provider in key_parts or key in provider.split("-"):
            return one
    return None
