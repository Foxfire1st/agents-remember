"""Served-build preflight for execution-topology schema writes.

The 3.0.0rc7 failure class (ar-coordination l9-issues.md:9-19): the cutover data
write emitted ``executionNature``/``executionGraph`` into the persistent task tree
while the served build could not parse the new fields (its ``TaskDocument`` model
predates them and uses ``extra="forbid"``), which forced a snapshot restore and a
re-apply after deploy. Graph authoring/migration operations therefore verify the
serving runtime understands the topology schema *before* writing, refusing with
upgrade guidance otherwise.

The check is a model self-probe of the process that will serve: the MCP server
running the tool IS the serving runtime for in-process invocations. A task-plane
edit never consults the installed distribution version.
"""

from __future__ import annotations

from agents_remember.errors import AgentsRememberError
from agents_remember.tasks.document import TaskDocument

# The topology schema version the graph authoring/migration operations emit.
TOPOLOGY_SCHEMA_VERSION = "ar-execution-topology/v1"

_TOPOLOGY_MODEL_FIELDS = ("executionNature", "executionGraph")
_MIGRATION_GUIDE = "docs/reference/execution-topology-migration.md"


class TopologyServingBuildError(AgentsRememberError):
    """The serving runtime cannot parse the execution-topology schema."""


def require_serving_topology_schema() -> None:
    """Refuse a topology write whose serving runtime cannot parse the schema."""

    missing = [name for name in _TOPOLOGY_MODEL_FIELDS if name not in TaskDocument.model_fields]
    if missing:
        raise TopologyServingBuildError(
            "task-execution-topology-serving-build-unsupported: the running build's "
            f"TaskDocument model lacks topology field(s) {missing!r}; upgrade the served "
            "build before authoring an execution graph -- see "
            f"{_MIGRATION_GUIDE} (served-build preflight)"
        )
