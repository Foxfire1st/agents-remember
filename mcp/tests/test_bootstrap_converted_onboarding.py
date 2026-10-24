"""Bootstrap produces converted onboarding (MIK-R45@v2): one test per rule.

A repository that is set up for the first time gets onboarding in the text format, using
the tools that exist; no new tool is added. Each case drives the real public operation on
a real Git repository, and every refusal is read from the refusal's own text.

    RULE 1  the skill writes converted cards: the c-03 templates and skill text hold no
            stamp, no metadata table and no Update History; the package copies equal the
            sources (pinned by ``test_sync_scripts``; asserted here by content).
    RULE 2  the fixer runs before the first baseline: the five admission conditions one
            by one, the ``pre-baseline`` result, the refusal after the baseline, and
            ``route_index_refresh``.
    RULE 3  the first baseline holds no old-format card: the adoption state for each of
            the four old-format signs, and its independence from ``accept_drift``.
    RULE 4  the order of the first hour is true: ``memory_init``, onboarding with the
            fixer, ``memory_baseline_adopt``, then the knowledge foundation (text).
    RULE 5  the hand-off is truthful: the c-03 handoff names cards, the fixer dry run,
            the baseline state and the knowledge state (text, plus the journey below).
    RULE 6  installed copies: regenerated with ``scripts/sync-skills.py``.

``test_bootstrap_journey_from_init_to_second_fixer_refusal`` is the composed packet test:
``memory_init``, three cards written as the templates say, the fixer,
``memory_baseline_status``, ``memory_baseline_adopt``, ``read_ar_files``, ``drift_check``,
and the refused second fixer call.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace as _replace_scope
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember_test_support.testing.leaf_instruction_wording import delivered_step

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application import memory_tools as tools
from agents_remember.application.memory_tools import (
    MemoryBranches,
    citation_fix_tool,
    drift_check_tool,
    memory_baseline_adopt_tool,
    memory_baseline_status_tool,
    memory_init_tool,
    route_index_refresh_tool,
)
from agents_remember.application.read_files import read_ar_files_tool
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from test_worktree_support import git, init_repo

REPO_ID = "repo"


def _world(tmp_path: Path) -> tuple[Path, Path]:
    """A code repository, a coordination root, and a settings file outside both."""

    root = tmp_path / "world"
    code = root / REPO_ID
    init_repo(code, "main")
    (code / "pkg").mkdir(parents=True, exist_ok=True)
    (code / "pkg" / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
    (code / "pkg" / "beta.py").write_text("print('beta')\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-qm", "init")
    (root / "coordination").mkdir(parents=True, exist_ok=True)
    path = root / "settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coordination").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO_ID: {}},
            }
        ),
        encoding="utf-8",
    )
    return code, path


def _fresh_converted_memory(tmp_path: Path) -> tuple[Any, Path, Path]:
    """A converted memory root with no baseline and no cards."""

    _code, path = _world(tmp_path)
    memory_init_tool(load_config(path), repo_id=REPO_ID, initial_branch="main")
    config = load_config(path)
    memory = config.repositories[REPO_ID].memory_root
    assert memory is not None, "the fixture configures an external memory root"
    return config, _code, memory


@pytest.fixture
def bound_validation() -> Any:
    """Bind real worktree services so the first baseline validates before it commits."""

    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=mock.Mock(),
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
        )
    )
    yield
    reset_worktree_services()


def _branches() -> MemoryBranches:
    return MemoryBranches(source_branch="main", work_branch="main")


CARD_TABLE = (
    "| Finding | Anchor | Source |\n| --- | --- | --- |\n| {finding} | {anchor} | {source} |\n"
)


def _write_card(memory: Path, card: str, finding: str, source: str) -> None:
    path = memory / "onboarding" / card
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# {card}\n\n## Evidence\n\n"
        + CARD_TABLE.format(finding=finding, anchor="", source=source),
        encoding="utf-8",
    )


# --- rule 2: the pre-baseline admission --------------------------------------


def test_fixer_without_contract_refuses_an_undeclared_repository(tmp_path: Path) -> None:
    """Condition 1: the repository must be declared in the settings."""

    _code, path = _world(tmp_path)
    config = load_config(path)
    with pytest.raises(AuthorityError, match="not allowed by MCP settings"):
        citation_fix_tool(config, repo_id="ghost")


def test_fixer_without_contract_refuses_an_unconverted_root(tmp_path: Path) -> None:
    """Condition 3: the root must hold knowledge/layout.json."""

    config, _code, memory = _fresh_converted_memory(tmp_path)
    (memory / "knowledge" / "layout.json").unlink()
    with pytest.raises(AuthorityError, match=r"knowledge/layout\.json"):
        citation_fix_tool(config, repo_id=REPO_ID)


def test_fixer_without_contract_names_a_foreign_resolved_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Condition 2: the resolved memory root must be the declared one."""

    config, _code, _memory = _fresh_converted_memory(tmp_path)
    real_scope = tools._memory_scope

    def _forged(config: Any, *, repo_id: str, contract_path: Any) -> Any:
        scope = real_scope(config, repo_id=repo_id, contract_path=contract_path)
        return _replace_scope(scope, onboarding_root=tmp_path / "elsewhere" / "onboarding")

    monkeypatch.setattr(tools, "_memory_scope", _forged)
    with pytest.raises(AuthorityError, match="not the declared one"):
        citation_fix_tool(config, repo_id=REPO_ID)


def test_fixer_without_contract_authors_cards_before_the_baseline(
    tmp_path: Path,
) -> None:
    """The admitted call writes the named card and its sidecar, and says so."""

    config, _code, memory = _fresh_converted_memory(tmp_path)
    _write_card(memory, "pkg/alpha.py.md", "The alpha module.", "pkg/alpha.py")

    result = citation_fix_tool(config, repo_id=REPO_ID)

    assert result["ok"] is True, result
    assert result["admission"] == "pre-baseline", result
    assert (memory / "onboarding" / "pkg" / "alpha.py.json").is_file()
    text = (memory / "onboarding" / "pkg" / "alpha.py.md").read_text(encoding="utf-8")
    assert "| Finding | Anchor | Source |" not in text, text
    assert "[1]" in text, text


def test_fixer_without_contract_is_refused_once_the_baseline_exists(
    tmp_path: Path, bound_validation: Any
) -> None:
    """After the baseline the same call refuses and names the leaf route."""

    config, _code, _memory = _fresh_converted_memory(tmp_path)
    adopted = memory_baseline_adopt_tool(config, repo_id=REPO_ID, branches=_branches())
    assert adopted["state"] == "adopted", adopted
    with pytest.raises(AuthorityError, match="already has an adopted memory baseline"):
        citation_fix_tool(config, repo_id=REPO_ID)
    with pytest.raises(AuthorityError, match="requires a leaf contract_path"):
        citation_fix_tool(config, repo_id=REPO_ID)


def test_fixer_without_contract_refuses_a_root_with_a_linked_worktree(
    tmp_path: Path,
) -> None:
    """Condition 5: no leaf may depend on the root."""

    config, _code, memory = _fresh_converted_memory(tmp_path)
    git(memory, "add", "-A")
    git(memory, "commit", "-qm", "unattributed content")
    linked = tmp_path / "linked-memory"
    proc = subprocess.run(
        ["git", "worktree", "add", linked.as_posix()],
        cwd=memory,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    try:
        with pytest.raises(AuthorityError, match="linked worktrees"):
            citation_fix_tool(config, repo_id=REPO_ID)
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", linked.as_posix()],
            cwd=memory,
            text=True,
            capture_output=True,
            check=False,
        )


def test_route_index_apply_without_contract_is_admitted_before_the_baseline(
    tmp_path: Path,
) -> None:
    """``route_index_refresh`` apply takes the same admission through the same helper."""

    config, _code, memory = _fresh_converted_memory(tmp_path)
    _write_card(memory, "overview.md", "The repo root.", "pkg/alpha.py")

    result = route_index_refresh_tool(config, repo_id=REPO_ID, dry_run=False)

    assert result["ok"] is True, result
    assert result["admission"] == "pre-baseline", result
    assert (memory / "onboarding" / "overview.index.json").is_file()


def test_route_index_apply_without_contract_is_refused_after_the_baseline(
    tmp_path: Path, bound_validation: Any
) -> None:
    config, _code, _memory = _fresh_converted_memory(tmp_path)
    adopted = memory_baseline_adopt_tool(config, repo_id=REPO_ID, branches=_branches())
    assert adopted["state"] == "adopted", adopted
    with pytest.raises(AuthorityError, match="already has an adopted memory baseline"):
        route_index_refresh_tool(config, repo_id=REPO_ID, dry_run=False)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_refused_reauthor_card_and_sidecar_stay_byte_identical(tmp_path: Path) -> None:
    """F01: nothing of a refused card is written by either fixer stage.

    A re-author row ``[1]`` kept beside its evidence line refuses the card in the
    authoring stage; the mechanical re-recording stage must then leave that card's
    sidecar alone too, while a successful sibling card is still authored.
    """

    config, code, memory = _fresh_converted_memory(tmp_path)
    _write_card(memory, "pkg/alpha.py.md", "Alpha is printed.", "pkg/alpha.py:1-1")
    _write_card(memory, "pkg/beta.py.md", "Beta is printed.", "pkg/beta.py")
    first = citation_fix_tool(config, repo_id=REPO_ID)
    assert first["ok"] is True, first

    card = memory / "onboarding" / "pkg" / "alpha.py.md"
    sidecar = memory / "onboarding" / "pkg" / "alpha.py.json"
    (code / "pkg" / "alpha.py").write_text(
        "print('alpha')\n# a comment outside the reference\n", encoding="utf-8"
    )
    card.write_text(
        card.read_text(encoding="utf-8")
        + "\n| Finding | Anchor | Source |\n| --- | --- | --- |\n| Re-authored [1] | | pkg/alpha.py:1-1 |\n",
        encoding="utf-8",
    )
    before = {"card": _sha(card), "sidecar": _sha(sidecar)}

    result = citation_fix_tool(config, repo_id=REPO_ID)

    assert result["authoring"]["refused"], result
    assert result["ok"] is False, result
    assert _sha(card) == before["card"], "a refused card was written"
    assert _sha(sidecar) == before["sidecar"], "a refused card's sidecar was rewritten"
    assert "onboarding/pkg/alpha.py.json" not in result.get("rewrittenSidecars", []), result


# --- rule 3: the first baseline holds no old-format card ----------------------

LEGACY_SIGNS = {
    "doc_type": "| doc_type | `repo-overview` |\n",
    "doc_type_backticked": "| `doc_type` | `repo-overview` |\n",
    "lastVerifiedCommitHash": "| lastVerifiedCommitHash | `abc` |\n",
    "lastVerifiedCommitDate": "| lastVerifiedCommitDate | 2026-01-01T00:00:00+00:00 |\n",
    "update_history": "## Update History\n\n<!-- newest first; append-only -->\n",
}


@pytest.mark.parametrize("sign", sorted(LEGACY_SIGNS))
def test_each_old_format_sign_blocks_the_baseline(tmp_path: Path, sign: str) -> None:
    """Rule 3: each of the four signs reports blocked-legacy-cards with the card."""

    config, _code, memory = _fresh_converted_memory(tmp_path)
    card = memory / "onboarding" / "overview.md"
    card.write_text(f"# overview\n\n{LEGACY_SIGNS[sign]}", encoding="utf-8")

    status = memory_baseline_status_tool(config, repo_id=REPO_ID)

    assert status["state"] == "blocked-legacy-cards", status
    assert status["ok"] is False, status
    assert status["legacyCards"] == ["onboarding/overview.md"], status


@pytest.mark.parametrize("sign", sorted(LEGACY_SIGNS))
def test_accept_drift_does_not_lift_blocked_legacy_cards(
    tmp_path: Path, bound_validation: Any, sign: str
) -> None:
    """Rule 3: accept_drift accepts stale anchors only, never an old-format card."""

    config, _code, _memory = _fresh_converted_memory(tmp_path)
    card = _memory / "onboarding" / "overview.md"
    card.write_text(f"# overview\n\n{LEGACY_SIGNS[sign]}", encoding="utf-8")
    for accept in (False, True):
        refused = memory_baseline_adopt_tool(
            config, repo_id=REPO_ID, accept_drift=accept, branches=_branches()
        )
        assert refused["ok"] is False and refused["state"] == "blocked-legacy-cards", (
            sign,
            accept,
            refused,
        )


def test_prose_and_code_mentions_of_legacy_keys_are_not_legacy_cards(
    tmp_path: Path,
) -> None:
    """F02: only a metadata-key first cell is a stamp row.

    A glossary explaining a key, a finding about one, or a code span naming one in
    a later cell is a valid converted card: the detector must not forbid every
    reference to these words.
    """

    config, _code, memory = _fresh_converted_memory(tmp_path)
    (memory / "onboarding" / "overview.md").write_text(
        "# Repository overview\n\n## Domain terms\n\n| Term | Meaning |\n| --- | --- |\n"
        "| document type | The parser accepts a `doc_type` key. |\n"
        "| stamp | The run records `lastVerifiedCommitHash` for context. |\n"
        "| date | `lastVerifiedCommitDate` is never authored by hand. |\n",
        encoding="utf-8",
    )
    status = memory_baseline_status_tool(config, repo_id=REPO_ID)
    assert status["state"] == "ready", status
    assert status["legacyCards"] == [], status


def test_correcting_the_listed_cards_unblocks_adoption(
    tmp_path: Path, bound_validation: Any
) -> None:
    """Rule 3 failure behavior: a refused adoption changes nothing; the rerun adopts."""

    config, _code, memory = _fresh_converted_memory(tmp_path)
    card = memory / "onboarding" / "overview.md"
    card.write_text("# overview\n\n## Update History\n", encoding="utf-8")
    refused = memory_baseline_adopt_tool(config, repo_id=REPO_ID, branches=_branches())
    assert refused["state"] == "blocked-legacy-cards", refused

    card.write_text("# overview\n\nA thin root overview.\n", encoding="utf-8")
    adopted = memory_baseline_adopt_tool(config, repo_id=REPO_ID, branches=_branches())
    assert adopted["ok"] is True and adopted["state"] == "adopted", adopted


def test_legacy_cards_cannot_refuse_an_adopted_repository(
    tmp_path: Path, bound_validation: Any
) -> None:
    """Rule 3 is an adoption gate, not a validator rule: adopted trees stay adopted."""

    config, _code, memory = _fresh_converted_memory(tmp_path)
    adopted = memory_baseline_adopt_tool(config, repo_id=REPO_ID, branches=_branches())
    assert adopted["state"] == "adopted", adopted
    (memory / "onboarding" / "overview.md").write_text(
        "# overview\n\n## Update History\n", encoding="utf-8"
    )
    assert memory_baseline_status_tool(config, repo_id=REPO_ID)["state"] in {
        "already-adopted",
        "ready",
    }


# --- rules 1/4/5: the skill texts --------------------------------------------

CANONICAL_SKILL_FILES = (
    "skills/c-03-repo-bootstrap/SKILL.md",
    "skills/c-03-repo-bootstrap/templates/repo-overview-template.md",
    "skills/c-03-repo-bootstrap/templates/route-local-overview-template.md",
    "skills/c-03-repo-bootstrap/templates/file-card-template.md",
    "skills/c-03-repo-bootstrap/templates/curator-review-template.md",
    "skills/c-03-repo-bootstrap/templates/bootstrap-handoff-template.md",
    "skills/c-13-install-and-onboard/SKILL.md",
    "skills/l-01-agent-lifecycles/operations/bootstrap.md",
)


def test_bootstrap_skill_texts_carry_no_legacy_card_format() -> None:
    """Rule 1: no stamp, no metadata table and no Update History in the skill texts."""

    for relative in CANONICAL_SKILL_FILES:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "lastVerifiedCommitHash" not in text, relative
        assert "lastVerifiedCommitDate" not in text, relative
        assert "| doc_type |" not in text, relative
        assert "## Update History" not in text, relative


def test_bootstrap_skill_texts_state_the_fixer_and_the_order(tmp_path: Path) -> None:
    """Rules 1/4/5: the fixer replaces the tables; the first hour runs in order."""

    bootstrap = (REPO_ROOT / "skills/c-03-repo-bootstrap/SKILL.md").read_text(encoding="utf-8")
    assert "runs the fixer on the card" in bootstrap
    assert "replaces each table with `- <finding> [n]` lines" in bootstrap
    assert "Never write sidecar JSON by hand" in bootstrap

    install = (REPO_ROOT / "skills/c-13-install-and-onboard/SKILL.md").read_text(encoding="utf-8")
    # MIK-R196 rules 4-5: setup initialises the memory root and authors no onboarding, so the order
    # claim follows the step that survives -- the root before its baseline, the baseline before the
    # foundation is handed to the Architect.
    assert "Setup authors no onboarding" in install
    assert "citation fixer here" in install, "setup must not run the fixer"
    assert install.index("Initialise the memory repo, and author no onboarding") < install.index(
        "Adopt the first attributed baseline"
    )
    assert install.index("Adopt the first attributed baseline") < install.index(
        "Reach the repository's knowledge foundation"
    )

    operation = (REPO_ROOT / "skills/l-01-agent-lifecycles/operations/bootstrap.md").read_text(
        encoding="utf-8"
    )
    # MIK-R196 rule 5 removed the onboarding-scaffold/fixer step from this operation, so the order
    # it protected now runs from the memory root's own init: the root is initialized (or repaired)
    # before its baseline is adopted, and the baseline before the foundation is reported.
    assert operation.index("Initialize or repair the memory root") < operation.index(
        "Adopt the first attributed baseline"
    )
    # The foundation left this operation for the Architect (MIK-R196 rule 4), so the order claim
    # follows the step that survives: the baseline still comes before the foundation is reported
    # and handed over. Retiring the assertion instead would drop the order it protects.
    assert operation.index("Adopt the first attributed baseline") < operation.index(
        "Report the knowledge foundation's state and hand its making to the Architect"
    )
    instructions, _foundation = delivered_step(
        REPO_ROOT / "skills/l-01-agent-lifecycles",
        ("bootstrap", "bootstrap"),
        "reference/bootstrap-foundation-d301.md",
        # MIK-R196 rule 4 inserted the user-settings step and moved the foundation to the
        # Architect's work: the eighth step still stands after the baseline, and it now reads
        # "Report the knowledge foundation's state and hand its making to the Architect".
        "when you reach step 8, not before",
        tmp_path / "bootstrap.md",
    )
    # MIK-R196 rule 4: the foundation is the Architect's work and the operation reports it at
    # step 8. Read off the compiled instruction rather than reconstructed:
    assert (
        "8. **Report the knowledge foundation's state and hand its making to the Architect.**"
        in (instructions)
    )
    assert instructions.index("Adopt the first attributed baseline") < instructions.index(
        "8. **Report the knowledge foundation's state and hand its making to the Architect.**"
    )
    # MIK-R196 rule 4 moved the foundation's ordering rules out of the bootstrap detail and
    # they survive on the install-and-onboard surface; read from the owner that states them.
    flat_install = " ".join(install.split())
    assert "runs after the baseline" in flat_install
    assert "before the first baseline no leaf can exist" in flat_install
    assert "already has a commit" in flat_install


def test_bootstrap_skill_texts_state_taskless_route_and_audit_trail() -> None:
    """Journey findings 1-2: the no-contract pre-baseline route; bootstrap retention."""

    bootstrap = (REPO_ROOT / "skills/c-03-repo-bootstrap/SKILL.md").read_text(encoding="utf-8")
    assert "run the fixer with no contract" in bootstrap
    assert "admits `pre-baseline`" in bootstrap
    assert "never part of a memory commit" not in bootstrap
    assert "audit trail" in bootstrap

    install = (REPO_ROOT / "skills/c-13-install-and-onboard/SKILL.md").read_text(encoding="utf-8")
    assert "never part of a memory commit" not in install
    assert "audit trail" in install


def test_first_baseline_retains_onboarding_bootstrap_audit_trail(
    tmp_path: Path, bound_validation: Any
) -> None:
    """Journey finding 2: onboarding/bootstrap/ working files ship with the commit."""

    config, _code, memory = _fresh_converted_memory(tmp_path)
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding" / "overview.md").write_text("# Overview\n", encoding="utf-8")
    bootstrap = memory / "onboarding" / "bootstrap"
    bootstrap.mkdir(parents=True, exist_ok=True)
    (bootstrap / "STATE.md").write_text("# state\n", encoding="utf-8")
    (bootstrap / "input-ledger.md").write_text("# ledger\n", encoding="utf-8")

    adopted = memory_baseline_adopt_tool(config, repo_id=REPO_ID, branches=_branches())

    assert adopted["ok"] is True, adopted
    assert adopted["state"] == "adopted", adopted
    committed = git(memory, "ls-files", "--", "onboarding/bootstrap").splitlines()
    assert "onboarding/bootstrap/STATE.md" in committed, committed
    assert "onboarding/bootstrap/input-ledger.md" in committed, committed


# --- the composed packet journey ----------------------------------------------


def test_bootstrap_journey_from_init_to_second_fixer_refusal(
    tmp_path: Path, bound_validation: Any
) -> None:
    """The packet journey: init, three cards, fixer, status, adopt, reads, refusal."""

    _code, path = _world(tmp_path)
    initialized = memory_init_tool(load_config(path), repo_id=REPO_ID, initial_branch="main")
    assert initialized["ok"] is True, initialized
    # Before the first baseline the memory line has no commit, so the knowledge
    # admission names that fact (with its own next step) instead of a state.
    assert initialized["knowledge"]["state"] == "context-not-admitted", initialized["knowledge"]
    assert initialized["knowledge"]["code"] == "memory_revision_unavailable", initialized[
        "knowledge"
    ]
    assert initialized["knowledge"]["nextAction"], initialized["knowledge"]
    config = load_config(path)
    memory = config.repositories[REPO_ID].memory_root
    assert memory is not None

    _write_card(memory, "overview.md", "The repo prints alpha.", "pkg/alpha.py")
    _write_card(memory, "pkg/overview.md", "The pkg route prints beta.", "pkg/beta.py")
    _write_card(memory, "pkg/alpha.py.md", "Alpha prints alpha.", "pkg/alpha.py")

    fixed = citation_fix_tool(config, repo_id=REPO_ID)
    assert fixed["ok"] is True and fixed["admission"] == "pre-baseline", fixed
    assert sorted(fixed["authoring"]["authoredCards"]) == [
        "onboarding/overview.md",
        "onboarding/pkg/alpha.py.md",
        "onboarding/pkg/overview.md",
    ], fixed

    status = memory_baseline_status_tool(config, repo_id=REPO_ID)
    assert status["state"] == "ready", status

    adopted = memory_baseline_adopt_tool(config, repo_id=REPO_ID, branches=_branches())
    assert adopted["ok"] is True and adopted["state"] == "adopted", adopted

    reread = memory_init_tool(load_config(path), repo_id=REPO_ID)
    assert reread["knowledge"]["state"] == "not-recorded", reread["knowledge"]
    assert "developer start the Architect" in reread["knowledge"]["nextAction"], reread["knowledge"]
    assert "knowledge-bootstrap" not in reread["knowledge"]["nextAction"], reread["knowledge"]

    reading = read_ar_files_tool(config, repo_id=REPO_ID, files=["pkg/alpha.py"])
    assert reading["ok"] is True and reading["state"] == "named", reading
    # MIK-R109 rule 1: the read names the governing card; it carries no
    # text. The converted onboarding of the file is asserted in the card
    # file the answer names, read here with the test's own file read.
    (row,) = reading["files"]
    assert (row["path"], row["exists"]) == ("pkg/alpha.py", True), row
    assert row["card"], row
    assert "Alpha prints alpha." in Path(row["card"]).read_text(encoding="utf-8"), row

    checked = drift_check_tool(config, repo_id=REPO_ID)
    assert checked["ok"] is True, checked

    with pytest.raises(AuthorityError, match="already has an adopted memory baseline"):
        citation_fix_tool(config, repo_id=REPO_ID)
