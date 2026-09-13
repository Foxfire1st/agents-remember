"""Every memory-content producer attributes the code commit it was written against.

A memory commit that names no code commit contributes no ledger row, and a projected ledger
cannot tell that apart from a commit whose producer kept the old shape: the pairing is simply
gone. So the producer surface has to be total rather than mostly converted, and this module holds
the two things that make it total -- the census of producers, and the one renderer they all reach
-- together with the two producers outside the closeout routes, driven through their real public
operations on real repositories.

The census is the master's 2026-09-13T22:05 decision, re-derived from source at 5bb124d4. It
corrects that decision in one place. ``worktrees/queue/closeout_recovery.py`` is NOT a
memory-content producer: its commit sites are the recovery route's code leg (``:209``) and its
ledger legs (``:278``, ``:357``), and ``resume_external_commits`` only proves an already-journaled
memory commit. The recovery route's memory-content commit, when it still owes one, is created by
``worktrees/modules/closeout_external.py`` -- the same producer the first attempt uses. The
producer the census missed is ``worktrees/integration/closeout/preparation/memory_output.py``,
whose prepared memory-content intent is published to the live memory ref by
``.../preparation/finalization.py``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from agents_remember.application.memory_tools import (
    CarryoverCommitMessages,
    CarryoverSelection,
    MemoryBranches,
    memory_baseline_adopt_tool,
    memory_carryover_apply_tool,
)
from agents_remember.kernel.memory_attribution import (
    CODE_COMMIT_TRAILER_KEY,
    parse_code_commit_trailer,
    render_memory_content_message,
)
from agents_remember.kernel.memory_ledger import load_ledger
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from test_closeout_queue import MASTER_A, QueueFixture
from test_worktree_support import git, init_repo

CODE_ONE = "1" * 40
CODE_TWO = "2" * 40

# The five producers, and the shared renderer entry each one calls. The two closeout-shaped
# producers go through the model method, which delegates to the one renderer; every other
# producer reaches the renderer directly, because it has no EffectiveCloseoutInput to hand.
_PRODUCERS = {
    "worktrees/modules/closeout_external.py": "memory_content_message(",
    "worktrees/integration/direct_landing/direct_landing_execution.py": "memory_content_message(",
    "worktrees/integration/closeout/preparation/memory_output.py": (
        "render_memory_content_message("
    ),
    "memory/carryover.py": "render_memory_content_message(",
    "memory/baseline.py": "render_memory_content_message(",
}
_ONE_MODULE = "kernel/memory_attribution.py"
_KEY_NAME = "CODE_COMMIT_TRAILER_KEY"


def _production_source() -> Path:
    return Path(__file__).resolve().parents[2] / "mcp" / "src" / "agents_remember"


def _attribution(repository: Path, commit: str) -> str:
    """The trailer the committed object carries, read the way Git documents it."""

    return git(repository, "log", "-1", "--format=%(trailers:key=Code-Commit)", commit)


def _parsed_trailer(repository: Path, commit: str, scratch: Path) -> str:
    """``git interpret-trailers --parse`` over the committed message, as data."""

    message = scratch / "attributed-message.txt"
    message.write_text(git(repository, "log", "-1", "--format=%B", commit) + "\n", encoding="utf-8")
    return git(repository, "interpret-trailers", "--parse", message.as_posix())


def test_the_attribution_key_is_named_and_rendered_in_exactly_one_module() -> None:
    """One key, one interpolation, one module -- the guard the whole transition rests on.

    ``kernel/memory_attribution.py`` declares the key once, renders it once, and reads it back
    once. A producer that imported the key and hand-built the trailer, or worse wrote its own copy
    of the spelling, would emit trailers this reader silently ignores, and that failure looks like
    "no attribution exists" rather than like a bug. Three separate halves, because a divergent
    writer has three ways to appear: naming the constant (the drift a second copy introduces),
    interpolating it (the act of rendering), and spelling it out as a quoted literal (a copy that
    never mentions the constant at all). Prose that merely mentions the trailer's spelling is
    documentation and is not counted.
    """

    root = _production_source()
    sources = sorted(path for path in root.rglob("*.py"))
    contents = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in sources
    }
    named = [name for name, text in contents.items() if _KEY_NAME in text]
    rendered = [name for name, text in contents.items() if "{" + _KEY_NAME + "}" in text]
    quoted = [
        name
        for name, text in contents.items()
        if f'"{CODE_COMMIT_TRAILER_KEY}:' in text or f"'{CODE_COMMIT_TRAILER_KEY}:" in text
    ]

    assert named == [_ONE_MODULE], named
    assert rendered == [_ONE_MODULE], rendered
    # Nobody starts the trailer as a string literal: the one definition above is the only place
    # the spelling may exist, and a declared trailer always begins with the key it interpolates.
    assert quoted == [], quoted


def test_every_census_producer_reaches_the_shared_renderer() -> None:
    """The census itself: five producers, and each one routes its message through the renderer.

    This is a source census rather than a behavioural case because one of the five -- the
    prepared memory-content leg -- has no public entry point yet: its route
    (``certification/execution.execute_selected_closeout``) is wired in
    ``build_default_worktree_services`` but has no production caller, so nothing can be published
    through it to observe. The other four are covered end to end elsewhere: worktree closeout and
    direct landing in ``test_transaction_only_worktree_delivery`` and ``test_direct_landing``,
    carryover and baseline adoption in this module.
    """

    root = _production_source()
    reached = {
        name: entry in (root / name).read_text(encoding="utf-8")
        for name, entry in _PRODUCERS.items()
    }

    assert reached == dict.fromkeys(_PRODUCERS, True), reached


def test_the_one_renderer_keeps_the_callers_body_verbatim_and_its_trailer_final() -> None:
    """A caller's body may itself close with ``Key: value`` lines; the attribution is still ours.

    Carryover and baseline take their commit message as an argument of another tool, so the body
    is not this module's to edit: it is committed byte for byte, and the trailer is a separate
    final paragraph. That is what makes the attribution unambiguous -- Git reads a trailer only
    from the final block, so the caller's own lookalike line cannot be mistaken for it, and a
    producer that prepended or merged instead would be read as the caller's, not the code commit's.
    """

    body = (
        "Carry over landed branch memory\n"
        "\n"
        "The caller's own closing notes.\n"
        "\n"
        "Reviewed-By: someone@example.invalid\n"
        f"{CODE_COMMIT_TRAILER_KEY}: {CODE_ONE}\n"
    )

    rendered = render_memory_content_message(body, CODE_TWO)

    assert rendered == f"{body}\n\n{CODE_COMMIT_TRAILER_KEY}: {CODE_TWO}"
    assert parse_code_commit_trailer(rendered) == CODE_TWO


def _carryover_world(root: Path) -> tuple[QueueFixture, WorktreeContract, str, str, str]:
    """One open external leaf whose source branch memory describes landed official code."""

    fixture = QueueFixture(root, memory_mode="external")
    contract = fixture.contracts[MASTER_A]
    code = fixture.code
    memory = fixture.memory
    memory_worktree = contract.memory_worktree
    assert memory_worktree is not None
    old_base = git(code, "rev-parse", "main")
    feature = "FEATURE = 1\n"
    # The same content lands twice: once on the branch that describes it, once on the official
    # line. That is the ``patch-id-match`` evidence carryover auto-carries without a review.
    git(code, "switch", "-q", "-c", "source", old_base)
    (code / "feature.py").write_text(feature, encoding="utf-8")
    git(code, "add", "feature.py")
    git(code, "commit", "-q", "-m", "Add feature on the source branch")
    git(code, "switch", "-q", "-c", "official", old_base)
    (code / "feature.py").write_text(feature, encoding="utf-8")
    git(code, "add", "feature.py")
    git(code, "commit", "-q", "-m", "Land the feature officially")
    official_head = git(code, "rev-parse", "official")
    git(code, "switch", "-q", "main")
    # Carryover requires the target leaf's code worktree to be unchanged at the official tip.
    git(contract.code_worktree, "reset", "--hard", official_head)
    git(contract.code_worktree, "clean", "-qfd")
    git(memory_worktree, "clean", "-qfd")
    # Carryover writes only with declared target route-index authority, and it requires a clean
    # target, so the authority is committed here as the settings edit it is: no code commit to
    # name, no trailer.
    settings = memory_worktree / "system" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "version": 2,
                "onboarding": {
                    "storage": {"mode": "memory-repo"},
                    "pathRules": {
                        "include": {"paths": ["*"], "fileTypes": [".md", ".py"]},
                        "exclude": {"paths": []},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    git(memory_worktree, "add", "system/settings.json")
    git(memory_worktree, "commit", "-q", "-m", "Declare target-memory route authority")
    write_contract(contract.contract_path, replace(contract, code_base_commit=official_head))
    contract = load_contract(contract.contract_path)
    # The branch's memory is a real second checkout of the memory repository: carryover reads its
    # merge base against the target, so a plain directory would not be the same world.
    source_memory = fixture.coord / "source-memory"
    git(memory, "switch", "-q", "-c", "branch-memory", "main")
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding" / "feature.py.md").write_text(
        "# Feature\n"
        "\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"| lastVerifiedCommitHash | `{old_base}` |\n"
        "| lastVerifiedCommitDate | 2026-01-01T00:00:00+00:00 |\n",
        encoding="utf-8",
    )
    git(memory, "add", "onboarding/feature.py.md")
    git(memory, "commit", "-q", "-m", "Describe the feature on the branch memory")
    git(memory, "switch", "-q", "main")
    git(memory, "worktree", "add", "--detach", source_memory.as_posix(), "branch-memory")
    return fixture, contract, old_base, official_head, source_memory.as_posix()


def test_carryover_attributes_its_memory_content_commit_to_the_official_head(tmp_path) -> None:
    """``memory_carryover_apply`` lands a body the caller still owns, plus its attribution.

    The caller's message is a public argument and is deliberately hostile here: its last
    paragraph is itself ``Key: value`` lines, including a ``Code-Commit:`` line that is not this
    carryover's code commit. The landed body has to keep that paragraph verbatim and the trailer
    has to be the separate final block, so the attribution names ``official_head`` -- the code
    commit the ledger row the same call writes already names. The ledger-only commit carries none.
    """

    fixture, contract, old_base, official_head, source_memory = _carryover_world(tmp_path)
    memory_worktree = contract.memory_worktree
    assert memory_worktree is not None and contract.ledger_path is not None
    body = (
        "Carry over landed branch memory\n"
        "\n"
        "The reviewer's own closing notes.\n"
        "\n"
        "Reviewed-By: someone@example.invalid\n"
        f"{CODE_COMMIT_TRAILER_KEY}: {CODE_ONE}\n"
    )

    result = memory_carryover_apply_tool(
        fixture.cfg,
        CarryoverSelection(
            repo_id=contract.repo_name,
            contract_path=contract.contract_path.as_posix(),
            source_memory=source_memory,
            official_code_ref="official",
            source_code_ref="source",
            old_base=old_base,
        ),
        intent_note="carry the landed branch memory",
        messages=CarryoverCommitMessages(memory=body, ledger="Record branch memory carryover"),
    )

    assert result["ok"] is True, result
    assert result["state"] == "carried-over", result
    content = str(result["memory_content_commit"])
    ledger_commit = str(result["ledger_commit"])
    landed = git(memory_worktree, "log", "-1", "--format=%B", content)
    # The caller's body, byte for byte, and exactly one added trailer block after it.
    assert landed == f"{body.rstrip()}\n\n{CODE_COMMIT_TRAILER_KEY}: {official_head}"
    assert landed.count(CODE_COMMIT_TRAILER_KEY) == 2
    assert _attribution(memory_worktree, content) == f"Code-Commit: {official_head}"
    assert _parsed_trailer(memory_worktree, content, tmp_path) == f"Code-Commit: {official_head}"
    # The ledger-only commit names no code commit: it carries memory.md and has no counterpart.
    assert _attribution(memory_worktree, ledger_commit) == ""
    row = load_ledger(contract.ledger_path).rows[0]
    assert (row.code_commit, row.memory_commit) == (official_head, content)


def test_baseline_attributes_its_memory_content_commit_to_the_code_source_branch(tmp_path) -> None:
    """``memory_baseline_adopt`` attributes the first memory commit it creates, once.

    The code commit is resolved once and used twice -- as the trailer and as the initial ledger
    row -- because two resolutions of "the code source-branch commit" is how an attribution and
    its ledger row come to disagree. The adopted memory content is the first commit in an unborn
    memory history, and the ledger-only commit that bootstraps the table carries no trailer.
    """

    root = tmp_path / "world"
    coordination = root / "coordination"
    code = root / "repo"
    memory = coordination / "memory-repos" / "ar-repo"
    init_repo(code)
    (code / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-q", "-m", "Add module")
    coordination.mkdir(parents=True, exist_ok=True)
    # The MCP settings file must sit outside the coordinator root it points at.
    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coordination.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {"repo": {}},
            }
        ),
        encoding="utf-8",
    )
    init_repo(memory)
    # Adoption is the bootstrap exception on the exact unborn branch memory_init mints.
    git(memory, "config", "agents-remember.defaultBranch", "main")
    git(memory, "update-ref", "-d", "refs/heads/main")
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding" / "module.md").write_text("# Module\n", encoding="utf-8")
    (memory / "system").mkdir(parents=True, exist_ok=True)
    (memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")

    result = memory_baseline_adopt_tool(
        load_config(config_path),
        repo_id="repo",
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )

    assert result["ok"] is True, result
    assert result["state"] == "adopted", result
    content = str(result["bootstrap"]["memoryContentCommit"])
    ledger_commit = str(result["bootstrap"]["ledgerCommit"])
    source_commit = git(code, "rev-parse", "main")
    assert _attribution(memory, content) == f"Code-Commit: {source_commit}"
    assert _parsed_trailer(memory, content, tmp_path) == f"Code-Commit: {source_commit}"
    assert _attribution(memory, ledger_commit) == ""
    row = load_ledger(memory / "memory.md").rows[0]
    assert (row.code_commit, row.memory_commit) == (source_commit, content)
