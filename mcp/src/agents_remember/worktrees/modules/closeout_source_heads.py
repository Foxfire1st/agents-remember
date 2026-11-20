"""Which source heads a closeout accepts: the task's base, or the commit its own landing recorded.

A closeout refuses a source branch that moved since the task started, but a finished integration
or a checkpoint of the same contract moved it on purpose; that recorded commit is an expected head
too.
"""

from __future__ import annotations

from agents_remember.worktrees.modules.git import branch_commit


def landed_source_heads(contract, base: str, integrated: str) -> set[str]:
    """The source heads this contract's own landing may legitimately have produced.

    Both a finished integration and a checkpoint move the recorded source branch forward and
    record the commit they moved it to, so both make that commit an expected head. Keying only
    on ``completed`` refused a checkpointed series on its next closeout with "source branch
    moved since task start", describing the checkpoint's own recorded landing as foreign
    movement. A source that genuinely moved elsewhere is still refused, because it matches
    neither head.
    """

    expected = {base}
    if contract.integration_status in {"completed", "checkpointed"} and integrated:
        expected.add(integrated)
    return expected


def format_expected_heads(expected: set[str]) -> str:
    return ", ".join(sorted(expected))


def validate_closeout_source_heads(contract) -> None:
    """Refuse a closeout whose code or external memory source moved anywhere but its own landing."""

    current_code_source = branch_commit(contract.code_repo_path, contract.code_source_branch)
    expected_code_heads = landed_source_heads(
        contract, contract.code_base_commit, contract.integrated_code_commit
    )
    if current_code_source not in expected_code_heads:
        raise RuntimeError(
            "code source branch moved since task start: "
            f"{contract.code_source_branch} is {current_code_source}, "
            f"expected {format_expected_heads(expected_code_heads)}"
        )
    if (
        contract.memory_mode == "external"
        and contract.memory_repo_path is not None
        and contract.memory_base_commit
    ):
        current_memory_source = branch_commit(
            contract.memory_repo_path, contract.memory_source_branch
        )
        expected_memory_heads = landed_source_heads(
            contract, contract.memory_base_commit, contract.integrated_memory_content_commit
        )
        if current_memory_source not in expected_memory_heads:
            raise RuntimeError(
                "memory source branch moved since task start: "
                f"{contract.memory_source_branch} is {current_memory_source}, "
                f"expected {format_expected_heads(expected_memory_heads)}"
            )
