"""The dashboard's declared full catalog mirror stays bidirectional with the server wire."""

from __future__ import annotations

import re
from pathlib import Path

from agents_remember.serving.response_contract import TerminalCatalogEntryWire

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TERMINAL_CATALOG_TYPE = PROJECT_ROOT / "dashboard" / "src" / "types" / "terminalCatalog.ts"


def _dashboard_catalog_fields() -> set[str]:
    source = TERMINAL_CATALOG_TYPE.read_text(encoding="utf-8")
    interface = re.search(
        r"^export interface TerminalCatalogRow \{(?P<body>.*?)^\}",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert interface is not None, "TerminalCatalogRow interface is missing"
    return set(
        re.findall(
            r"^\s{2}([A-Za-z][A-Za-z0-9]*)\??:",
            interface.group("body"),
            flags=re.MULTILINE,
        )
    )


def test_dashboard_full_catalog_mirror_matches_server_aliases_bidirectionally() -> None:
    server = {field.alias or name for name, field in TerminalCatalogEntryWire.model_fields.items()}
    dashboard = _dashboard_catalog_fields()

    assert sorted(server - dashboard) == []
    assert sorted(dashboard - server) == []
    assert len(server) == 68
