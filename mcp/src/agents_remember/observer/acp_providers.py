"""Which providers are on an ACP connection, as the plugin read its host, and the wait text.

MIK-R216 rule 1 makes ``<PASEO_HOME>/agents-remember/acp-providers.json`` the one place the
answer is recorded. Its one writer is the Paseo plugin, which reads the installation it runs in
at its own start; the launch reads the file the way the product reads ``host-activity.json``
(:mod:`agents_remember.observer.host_activity`) and never guesses a provider out of a name.

Rule 3 adds one sentence to the first message of a seat on such a connection, at launch and
before the message is published, so the stored first message already carries it and the retry
check of a saved message still matches. A missing or unreadable file, or a provider the file
does not name, adds nothing; the launch receipt then says why, in a field of its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ACP_PROVIDERS_FILE = "acp-providers.json"
ACP_PROVIDERS_SCHEMA = "ar-acp-providers/v1"

# MIK-R216 rule 3, verbatim. The keyword keeps its backticks: the plugin reads the line, not
# the Markdown, and the seat has to write the capitalised word at the start of a line.
ACP_WAIT_INSTRUCTION = (
    "This host does not return a background helper's result to you, and does not wake you when "
    "it finishes. If you end a turn to wait for helpers, end it with a line that starts "
    "`WAITING FOR SUBAGENTS:`, followed by a progress report: each helper, what it does, its "
    "status, and what came back. You will be asked for their progress; then report again, "
    "collect what finished, and declare again or go on. While you wait, write your progress only "
    "here in your chat; do not send it to your parent. Do not wait inside a turn, and do not "
    "write a script to wake yourself."
)

# The receipt's own reason, one per case the file can answer.
UNREADABLE = "acp-providers-unreadable"
NOT_NAMED = "provider-not-named"
NOT_ACP = "provider-not-acp"


class AcpProviderVerdict(BaseModel):
    """One provider as the plugin's discovery resolved it.

    ``class`` is the resolved client class or null, ``acp`` is the verdict, and ``reason`` is the
    short kebab string the discovery used. Only the verdict is required: a provider the
    discovery could not name a class for still carries its answer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    client_class: str | None = Field(default=None, alias="class")
    acp: bool
    reason: str = ""


class AcpProviders(BaseModel):
    """The whole file: the installation read, its providers, and the providers that dropped out.

    ``dropouts`` is not read here — the plugin folds it into the verdict — but it is part of the
    document, so an unread field would make the reader refuse the file the plugin writes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: str = Field(alias="schema")
    paseoVersion: str | None = None
    readAt: str | None = None
    files: tuple[str, ...] = ()
    providers: dict[str, AcpProviderVerdict] = Field(default_factory=dict)
    dropouts: dict[str, str] = Field(default_factory=dict)


def acp_providers_path(home: Path) -> Path:
    """The file's place in the host's own home, beside ``host-activity.json``."""

    return home / "agents-remember" / ACP_PROVIDERS_FILE


def read_acp_providers(path: Path) -> AcpProviders | None:
    """Read the plugin's publication, or nothing: every failure is the same answer.

    A missing file, a file that is not JSON, a document this reader does not know, and a document
    written for another schema all mean the same thing to the launch — no instruction, and the
    receipt names the state (rule 1). Pydantic reports a JSON syntax error as a
    ``ValidationError`` as well, so one clause covers the file's bytes.
    """

    try:
        document = AcpProviders.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return None
    return document if document.schema_ == ACP_PROVIDERS_SCHEMA else None


def acp_launch_instruction(
    publication: AcpProviders | None, provider: str
) -> tuple[str | None, dict[str, Any]]:
    """The sentence to append for this seat, and the receipt field that says what happened.

    ``publication`` is the read result, ``None`` standing for every way the file gave no answer.
    The receipt body is written for both outcomes, so a reader of a receipt never has to infer
    from an absent key whether a seat is on an ACP connection.
    """

    if publication is None:
        return None, _skipped(provider, UNREADABLE)
    entry = publication.providers.get(provider)
    if entry is None:
        return None, _skipped(provider, NOT_NAMED)
    if not entry.acp:
        return None, _skipped(provider, NOT_ACP)
    return ACP_WAIT_INSTRUCTION, {"provider": provider, "acp": True, "instruction": "added"}


def _skipped(provider: str, reason: str) -> dict[str, Any]:
    return {"provider": provider, "acp": False, "instruction": "skipped", "reason": reason}
