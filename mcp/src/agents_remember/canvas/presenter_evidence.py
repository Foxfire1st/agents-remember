"""The presenter's additional read grant uses R102's exact-tree source reader, in full.

The packet is the authority. The author chooses a side and relative path, never a repository,
revision or current checkout. The caller admits this packet for its own repository/task/leaf;
every tool call rechecks its immutable digest. Historical/worktree roots stay exactly as admitted,
not silently rebound to the configured current checkout.
"""

from __future__ import annotations

from agents_remember.application.review_evidence_read import read_bound_source
from agents_remember.models.knowledge.review_evidence import EvidencePacket
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord


def source(
    packet: EvidencePacket, side: str, path: str, *, start: int, end: int | None = None
) -> dict:
    """One path at one of the packet's sides, from line ``start`` to ``end`` or the file's end."""

    binding = packet.comparison
    record = ReviewTreeComparisonRecord.model_validate(
        {
            "task_id": binding.task_id,
            "leaf_id": binding.leaf_id,
            "number": binding.comparison_number,
            "recorded_at": binding.recorded_at,
            "converted_base": (
                binding.converted_base.model_dump() if binding.converted_base else None
            ),
            **{
                name: getattr(binding, name).model_dump(
                    include={"repository", "tree", "commit", "ref"}
                )
                for name in ("code_base", "code_candidate", "memory_base", "memory_candidate")
            },
        }
    )
    return read_bound_source(record, side, path, start_line=start, end_line=end).model_dump()
