"""MIK-R199 round-1 finding L192-F06: root advice may not call a parameter the schema does not admit.

``AGENTS.md`` is the source checkout's own instruction file and it told every agent to call
``context_packet(repo_id=..., include_providers=true)``. The parameter is gone: ``ContextPacketRequest``
and the shipped tool schema do not carry it, so the advice directed an invalid invocation.

This test derives the admitted parameter names from the real request model -- not a list written
here -- reads every ``context_packet(...)`` example out of the root instruction file, and refuses
any example naming a parameter the request does not admit. It fails on the pre-repair bytes, where
``include_providers`` appears in the example.

The coordinator variant under ``agents-md-files/`` is read too when it carries such an example: it
must satisfy the same rule.
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

from agents_remember.application.context_packet import ContextPacketRequest

ROOT = Path(__file__).resolve().parents[2]
INSTRUCTION_FILES = (ROOT / "AGENTS.md", ROOT / "agents-md-files" / "coordinator" / "AGENTS.md")
CALL = re.compile(r"context_packet\(([^)]*)\)")
NAME = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _admitted() -> set[str]:
    return {field.name for field in fields(ContextPacketRequest)}


def _examples(path: Path) -> list[tuple[int, list[str]]]:
    found: list[tuple[int, list[str]]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for match in CALL.finditer(line):
            found.append((number, NAME.findall(match.group(1))))
    return found


def test_every_documented_context_packet_call_names_only_admitted_parameters() -> None:
    admitted = _admitted()
    assert "include_providers" not in admitted, "the model unexpectedly still admits the parameter"

    offenders: list[str] = []
    seen = 0
    for path in INSTRUCTION_FILES:
        if not path.exists():
            continue
        for number, names in _examples(path):
            seen += 1
            unknown = [name for name in names if name not in admitted]
            if unknown:
                offenders.append(f"{path.relative_to(ROOT)}:{number} names {unknown}")

    assert seen > 0, "no context_packet example was found to check; the instruction moved"
    assert offenders == [], (
        "instruction names a parameter the request model does not admit: " + "; ".join(offenders)
    )
