"""Release-owned host and Node pins; settings never select another build."""

from __future__ import annotations

import json
from pathlib import Path

HOST_DATA = Path(__file__).resolve().parents[2] / "package_data" / "paseo_host"
HOST_CONTRACT = json.loads((HOST_DATA / "contract.json").read_text(encoding="utf-8"))
PASEO_PACKAGE: str = HOST_CONTRACT["package"]
PASEO_VERSION: str = HOST_CONTRACT["version"]
NODE_VERSION: str = HOST_CONTRACT["node"]["version"]
