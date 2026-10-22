"""Release-owned host and Node pins; settings never select another build."""

from __future__ import annotations

import json
from pathlib import Path

HOST_DATA = Path(__file__).resolve().parents[2] / "package_data" / "paseo_host"
HOST_CONTRACT = json.loads((HOST_DATA / "contract.json").read_text(encoding="utf-8"))
PASEO_PACKAGE: str = HOST_CONTRACT["package"]
PASEO_VERSION: str = HOST_CONTRACT["version"]
NODE_VERSION: str = HOST_CONTRACT["node"]["version"]

# The provider ids whose host provider options carry extra command-line arguments for the harness
# (the pinned host spreads ``providerOptions.extraArgs`` into that provider's spawn; MIK-R192).
# Which providers a host can hand arguments to is a fact of the host build, like its version.
EXTRA_ARGS_PROVIDER_IDS: tuple[str, ...] = ("claude",)
