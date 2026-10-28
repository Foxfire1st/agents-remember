"""``explainer_control``: the architect's Update, Retire and claim over one master's explainers.

One tool for both requirements that meet here: MIK-R186 rule 6 gives the architect Update and Retire
for a leaf's explainer, and MIK-R189 rule 7 adds ``claim``, which any hosted architect seat may call
to take a master over. The declared arguments are the selection the sibling tools take -- a
repository id, the master folder's name, and (for the two leaf actions) the leaf's id -- plus a
Retire's receipt id; never a path and never an agent id.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from .lazy import tools


def register_explainer_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the architect's one explainer tool (MIK-R186 rule 6, MIK-R189 rule 7)."""

    @server.tool()
    def explainer_control(
        action: str,
        repo_id: str,
        task_name: str,
        *,
        leaf_id: str | None = None,
        receipt_id: str | None = None,
    ) -> dict[str, Any]:
        """Update or retire one leaf's explainer, or claim a master as its architect. `action` is
        `update`, `retire` or `claim` (any other value is refused `unknown-action` before lookup);
        `leaf_id` names the leaf of a leaf action. `retire` carries its `receipt_id` and
        removes the workspace whole, chat archived, in any seat state. `claim` is open to any
        hosted architect seat and writes the master-to-architect record.
        `update` and `retire` are accepted only from the seat that record names; any other caller
        is refused `not-this-masters-architect` and claims first.
        Reference: docs/reference/mcp-tools.md."""
        return tools.explainer_control_payload(
            config,
            tools.ExplainerControlCall(
                action=action,
                repo_id=repo_id,
                task_name=task_name,
                leaf_id=leaf_id,
                receipt_id=receipt_id,
            ),
        )
