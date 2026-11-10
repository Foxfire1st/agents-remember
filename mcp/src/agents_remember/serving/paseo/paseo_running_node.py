"""Select an existing Node for talking to an already running, owned host."""

from __future__ import annotations

from pathlib import Path

from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.serving.paseo.paseo_process_record import (
    ProcessReader,
    inspect_record,
    read_process,
)


def communication_node(
    settings: PaseoRuntimeSettings, reader: ProcessReader = read_process
) -> Path:
    """Prefer this build's Node; otherwise use the proven live supervisor's executable.

    This selector grants no authority to start a host on an earlier Node. Callers that start
    one select the product Node directly instead.
    """
    node = product_node().node
    if node.is_file():
        return node
    record = inspect_record(settings.home, "node", reader)
    facts = reader(record.pid) if record.kind == "own" and record.pid is not None else None
    if facts is not None and facts.node_executable:
        running = Path(facts.node_executable)
        if running.is_file():
            return running
    raise PaseoRuntimeFailure(
        "node_not_installed",
        "node",
        f"Neither the product Node at {node} nor the owned running host's Node exists; "
        "run runtime_install, then agents-remember paseo provision with the active --config.",
    )
