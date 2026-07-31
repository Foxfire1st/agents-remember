"""The pair of authorities that jointly decide which hosted sessions exist.

A hosted session is two things at once: a durable catalog row (identity, provenance, control
metadata) and a live tmux process. Neither answers "does this session exist, and what is it?" on
its own -- opening, reopening, retiring, delivering to and reconciling a seat all read the row and
probe the process together, and a row read against the *wrong* host is a silent correctness bug.

:class:`HostedSessionRuntime` makes that pairing a single value, so the two can only travel bound to
each other. It mirrors the composition-time bundles the serving layer already uses
(``ConversationRuntime``, ``SupervisorContext``): the authorities are frozen together once and then
passed as one thing.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog


@dataclass(frozen=True)
class HostedSessionRuntime:
    """The durable hosted-session catalog bound to the tmux host that runs its sessions."""

    catalog: TerminalCatalog
    host: TerminalHost


__all__ = ["HostedSessionRuntime"]
