"""Bounds for MCP tool descriptions (MIK-R112 rule 7, amended in place under D126).

A description says in plain sentences what the tool does and when to call it, and names
the arguments an ordinary call needs where their names do not say it. It holds no example
of an answer, no list of refusal codes, no list of every argument and no history. It ends
by naming the one place where the longer explanation is — every argument, every operation,
every refusal: the skill that teaches the tool, or the tool's section of the reference.
``TOOL_DESCRIPTION_MAX_CHARS`` holds for every registered tool; there is no exception
table. The schema description of an argument, where one exists, holds to
``TOOL_ARGUMENT_DESCRIPTION_MAX_CHARS``.

Who writes which: L109, L110 and L111 write the descriptions of the tools whose answers
they rebuild (MIK-R108 rule 7), and L73 those of the tools it adds; this leaf writes all
others. Until its leaf lands, a tool with an interim row of L109 or L110 in the
table of MIK-R108 rule 6 keeps its description, held to the length it has when this leaf
lands. ``INTERIM_DESCRIPTION_OWNERS`` names that ownership; after L108 lands the test
reads the interim marking from its table instead. L111 has landed: its tools hold
final descriptions under ``TOOL_DESCRIPTION_MAX_CHARS`` above.
"""

from __future__ import annotations

TOOL_DESCRIPTION_MAX_CHARS = 600
TOOL_ARGUMENT_DESCRIPTION_MAX_CHARS = 120

INTERIM_DESCRIPTION_OWNERS: dict[str, str] = {
    "memory_quality_check": "L110",
    "citation_fix": "L110",
    "route_index_refresh": "L110",
    "memory_init": "L110",
    "memory_baseline_status": "L110",
    "memory_baseline_adopt": "L110",
    "memory_carryover_plan": "L110",
    "memory_carryover_apply": "L110",
    "knowledge_integrity_check": "L110",
    "curator_coherence": "L110",
}
