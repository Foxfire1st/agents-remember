"""Served-build preflight for execution-topology schema writes.

The 3.0.0rc7 failure class (ar-coordination l9-issues.md:9-19): the cutover data
write emitted ``executionNature``/``executionGraph`` into the persistent task tree
while the served build could not parse the new fields (its ``TaskDocument`` model
predates them and uses ``extra="forbid"``), which forced a snapshot restore and a
re-apply after deploy. Graph authoring/migration operations therefore verify the
serving runtime understands the topology schema *before* writing, refusing with
upgrade guidance otherwise. The check has two legs:

1. a model self-probe of the process that will serve (the MCP server running the
   tool IS the serving runtime for in-process invocations), and
2. an installed-distribution check: when the installed ``agents-remember-mcp``
   distribution is a non-editable wheel older than the documented floor, refuse
   even if the checkout code on ``sys.path`` is current -- that is exactly the
   mixed build that wrote rc7-unreadable rows.

An editable install passes leg 2 by construction (the checkout code is the
serving code). A source-tree run with no installed distribution passes leg 2 and
relies on the operator contract: run authoring through the deployed serving
server, never from a checkout whose server is a different build.
"""

from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path

from packaging.version import InvalidVersion, Version

import agents_remember
from agents_remember.errors import AgentsRememberError
from agents_remember.tasks.document import TaskDocument

# The source tree that the running ``agents_remember`` package resolves from
# (``mcp/src/`` in a checkout). A resolved distribution whose metadata directory
# sits inside it is served from the checkout, whatever the install mechanism.
_PACKAGE_SOURCE_ROOT = Path(agents_remember.__file__).resolve().parents[1]

# The topology schema version the graph authoring/migration operations emit.
TOPOLOGY_SCHEMA_VERSION = "ar-execution-topology/v1"
# The first served distribution known to carry the topology fields
# (``executionNature``/``executionGraph`` on ``TaskDocument``). 3.0.0rc7 predates
# them; every later release (3.0.0rc8+, 3.0.0, 3.1.0, ...) compares above the floor.
TOPOLOGY_SERVING_VERSION_FLOOR = "3.0.0rc8"

_TOPOLOGY_MODEL_FIELDS = ("executionNature", "executionGraph")


class TopologyServingBuildError(AgentsRememberError):
    """The serving runtime cannot parse the execution-topology schema."""


def require_serving_topology_schema() -> None:
    """Refuse a topology write whose serving runtime cannot parse the schema.

    Fail-closed: an unverifiable serving build refuses instead of risking the
    rc7 restore class. The refusal names the upgrade path
    (``docs/reference/execution-topology-migration.md``).
    """

    missing = [name for name in _TOPOLOGY_MODEL_FIELDS if name not in TaskDocument.model_fields]
    if missing:
        raise TopologyServingBuildError(
            "task-execution-topology-serving-build-unsupported: the running build's "
            f"TaskDocument model lacks topology field(s) {missing!r}; upgrade the served "
            "build before authoring an execution graph -- see "
            "docs/reference/execution-topology-migration.md (served-build preflight)"
        )
    dist = _installed_distribution()
    if dist is None:
        # Source-tree run without an installed distribution: leg 2 cannot prove the
        # serving build. The operator contract (run authoring through the deployed
        # serving server) is documented in execution-topology-migration.md.
        return
    if _is_editable_install(dist):
        # The editable install resolves to this checkout, so the checkout code is
        # the serving code; the model self-probe above already proved it.
        return
    if _below_floor(dist.version):
        raise TopologyServingBuildError(
            "task-execution-topology-serving-build-unsupported: the installed "
            f"agents-remember-mcp {dist.version} predates the execution-topology schema "
            f"(floor {TOPOLOGY_SERVING_VERSION_FLOOR}); upgrade the served build before "
            "authoring an execution graph -- see "
            "docs/reference/execution-topology-migration.md (served-build preflight)"
        )


def _installed_distribution() -> metadata.Distribution | None:
    try:
        return metadata.distribution("agents-remember-mcp")
    except metadata.PackageNotFoundError:
        return None


def _is_editable_install(dist: metadata.Distribution) -> bool:
    """Whether the resolved distribution is served from this checkout.

    ``importlib.metadata`` resolves the distribution that ``sys.path`` finds
    first. Three signals prove the checkout code is the serving code:

    1. the metadata directory (an ``*.egg-info`` when ``mcp/src`` is on the
       import path, or the dist-info of an editable install) sits inside the
       running package's source tree;
    2. ``direct_url.json`` declares an editable install;
    3. an ``__editable__*.pth`` sits beside the dist-info directory.

    A real installed wheel resolves to a site-packages dist-info outside the
    source tree and fails all three, leaving the version-floor check in charge.
    """

    info_dir = getattr(dist, "_path", None)
    if info_dir is not None and _path_is_within(_PACKAGE_SOURCE_ROOT, info_dir):
        return True
    direct_url_text: str | None = None
    try:
        direct_url_text = dist.read_text("direct_url.json")
    except (OSError, ValueError):
        direct_url_text = None
    if direct_url_text:
        try:
            direct = json.loads(direct_url_text)
        except json.JSONDecodeError:
            direct = None
        if isinstance(direct, dict) and isinstance(direct.get("dir_info"), dict):
            return direct["dir_info"].get("editable") is True
    # Legacy editable marker: an ``__editable__*.pth`` beside the dist-info dir.
    if info_dir is not None and info_dir.is_dir():
        return any(path.name.startswith("__editable__") for path in info_dir.parent.iterdir())
    return False


def _path_is_within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _below_floor(version_text: str) -> bool:
    try:
        parsed = Version(version_text)
    except InvalidVersion:
        # An unparseable version is not provably the stale build; do not refuse on
        # an unknown, but the self-probe still guards the fields themselves.
        return False
    if parsed.is_devrelease or parsed.is_postrelease or parsed.local is not None:
        # A dev/post/local build of current code is not provably the released rc7
        # build; only proven pre-floor releases refuse (L15-R4).
        return False
    return parsed < Version(TOPOLOGY_SERVING_VERSION_FLOOR)
