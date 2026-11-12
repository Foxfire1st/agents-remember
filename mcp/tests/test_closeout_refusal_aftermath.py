"""Rule 9 closeout-refusal aftermath: honest refusal text and takeback (moved, F07).

Moved verbatim from ``test_knowledge_gate_routes.py`` so that module stays below the
1200-line hard rail. Shared fixtures and helpers are imported, not copied.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.worktrees.modules import closeout_external
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from test_knowledge_closeout_gate import (
    LEAF,
    Gated,
    build_gated,
    commit,
    write,
)
from test_knowledge_gate_routes import (
    _answer,
    _closeout,
)

HISTORY = f"knowledge/history/{LEAF}.json"


@pytest.fixture
def world(tmp_path: Path) -> Gated:
    return build_gated(tmp_path)


@pytest.fixture
def ports() -> Any:
    providers = mock.Mock()
    providers.setup_status.return_value = {}
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=providers,
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


def test_a_closeout_refusal_after_the_code_commit_says_what_is_true(
    world: Gated, ports: None
) -> None:
    """Rule 9: a refusal after the code commit names it, nothing integrated, the rerun."""

    _answer(world)
    code_commit = commit(world.code, {})
    write(world.memory, {"onboarding/pkg/a.py.md": "# a [4]\n"})
    never = mock.Mock(side_effect=AssertionError("committed an invalid tree"))
    with (
        mock.patch.object(closeout_external, "begin_git_mutation", never),
        pytest.raises(RuntimeError) as refused,
    ):
        _closeout(world, code_commit)
    never.assert_not_called()
    detail = str(refused.value)
    assert "R22.3-markers" in detail  # the gate's own reason stays first
    assert code_commit in detail
    assert "nothing is integrated" in detail
    assert "rerunning the closeout completes the pair" in detail


def test_a_refused_closeout_leaves_an_edited_ignore_file_alone_and_names_it(
    world: Gated, ports: None
) -> None:
    """Rule 9: the takeback skips a file edited after the closeout wrote it, and names it."""

    _answer(world)
    history = world.memory / HISTORY
    open_bytes = history.read_bytes()
    ignore = world.memory / ".gitignore"
    code_commit = commit(world.code, {})

    def _write_marker(repository: Path) -> bytes:
        before = ignore.read_bytes() if ignore.is_file() else b""
        marker = b"# closeout-test-ignore-rule\n"
        if marker not in before:
            ignore.write_bytes(before + marker)
        return ignore.read_bytes()

    def _edit_then_refuse(_contract: Any, _code_commit: str, _judged: str) -> None:
        ignore.write_bytes(ignore.read_bytes() + b"# edited while the gate ran\n")
        raise RuntimeError("R52-rule-9-test-refusal")

    with (
        mock.patch.object(closeout_external, "ignore_memory_cache", side_effect=_write_marker),
        mock.patch.object(
            closeout_external, "_refuse_ungated_memory", side_effect=_edit_then_refuse
        ),
        pytest.raises(RuntimeError) as refused,
    ):
        _closeout(world, code_commit)
    detail = str(refused.value)
    assert "R52-rule-9-test-refusal" in detail
    assert ignore.as_posix() in detail  # the edited file is left alone and named
    assert b"# edited while the gate ran\n" in ignore.read_bytes()
    assert b"# closeout-test-ignore-rule\n" in ignore.read_bytes()
    assert history.read_bytes() == open_bytes  # the unedited closing was taken back
    assert code_commit in detail  # the refusal is still after the code commit
    assert "rerunning the closeout completes the pair" in detail
