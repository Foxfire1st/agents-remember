"""The MCP tool surface, one module per tool family.

``create_server`` owns process wiring -- the compact-content shim, the ambient lifecycle, the
``FastMCP`` instance -- and nothing else; every ``@server.tool()`` definition lives here. Each
module exposes one ``register_*_tools(server, config)`` that declares its family against the
server it is handed, and ``TOOL_REGISTRARS`` lists them in the order the server advertises
them. Adding a tool means editing one family module; the advertised set stays pinned by
``agents_remember.mcp.tools.PUBLIC_TOOLS``, which the tool-list test checks against a live
server.

FastMCP publishes tools in registration order -- never reorder ``TOOL_REGISTRARS``, nor the
``_register_*`` calls inside a family module.
"""

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from ..config import McpRuntimeConfig
from .benchmarks import register_benchmark_tools
from .closeout import register_closeout_tools
from .code_search import register_code_search_tools
from .core import register_core_tools
from .gates import register_gate_tools
from .lifecycle import register_lifecycle_tools
from .memory import register_memory_tools
from .orchestration import register_orchestration_tools
from .providers import register_provider_tools
from .sessions import register_session_tools
from .tasks import register_task_tools
from .worktrees import register_worktree_tools

ToolRegistrar = Callable[[FastMCP, McpRuntimeConfig], None]

TOOL_REGISTRARS: tuple[ToolRegistrar, ...] = (
    register_core_tools,
    register_session_tools,
    register_memory_tools,
    register_provider_tools,
    register_code_search_tools,
    register_worktree_tools,
    register_closeout_tools,
    register_task_tools,
    register_benchmark_tools,
    register_lifecycle_tools,
    register_gate_tools,
    register_orchestration_tools,
)

__all__ = ["TOOL_REGISTRARS", "ToolRegistrar"]
