"""The fixtures, hooks and assertions of the transaction-only delivery cases (CCR-R12).

``test_transaction_only_worktree_delivery.py`` holds the race, publication and integration
cases and the prose pins; ``test_transaction_only_worktree_delivery_cache.py`` holds the
memory-cache refresh cases. Both import this module, so the temp-Git fixtures, the real
pre-commit hooks, the seam racers and the attribution assertions have one home.
"""

from __future__ import annotations

import json
import shlex
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.worktree_tool_requests import (
    CloseoutApproval,
    CloseoutCommitMessages,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_cache import derive_memory_ledger, refresh_memory_cache
from agents_remember.kernel.memory_ledger import (
    LedgerRow,
    ledger_to_text,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.closeout.input import (
    EffectiveCloseoutInput,
    EnabledCloseoutLeg,
)
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.tasks import TaskEnclosureRef, read_task_doc, write_task_doc
from agents_remember.worktrees.integration.closeout import curator_coherence as coherence
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.modules import closeout_external
from agents_remember.worktrees.modules import git as git_owner
from agents_remember.worktrees.modules.landing_commit_check import LandingCommitCheckRefusal
from agents_remember.worktrees.modules.quality import closeout_memory as memory_quality
from agents_remember.worktrees.modules.quality import gate as quality_gate
from agents_remember.worktrees.queue import closeout_recovery
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import load_contract
from closeout_input_test_support import (
    ensure_fixture_waiting_door,
)
from integration_branch_authority_test_support import (
    _authority_fixture,
    _closed_external_leaf_worktrees,
)
from test_source_lineage import _fixture, _git

MESSAGES = CloseoutCommitMessages(
    code="Add transaction feature",
    memory="Document transaction feature",
)


def _public_config(root: Path, contract) -> McpRuntimeConfig:
    """Bind an existing temp Git fixture to MCP authority without a profile."""

    code_link = root / contract.repo_name
    if not code_link.exists():
        code_link.symlink_to(contract.code_repo_path, target_is_directory=True)
    if contract.memory_repo_path is not None:
        memory_link = contract.coordination_root / "memory-repos" / f"ar-{contract.repo_name}"
        memory_link.parent.mkdir(parents=True, exist_ok=True)
        if not memory_link.exists():
            memory_link.symlink_to(contract.memory_repo_path, target_is_directory=True)
    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": contract.coordination_root.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {contract.repo_name: {}},
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def _bind_task_without_review(contract) -> None:
    """Add only the canonical enclosure binding; leave review evidence absent.

    Both derived fields are bound, exactly as ``task_doc`` stamps them against a leaf
    contract: a leaf document missing its ``seriesContractPath`` is the damage a later
    start repairs, so it would model an unstarted-against document rather than this one.
    """

    task_path = contract.task_root / f"{contract.leaf_id.lower()}.json"
    document = read_task_doc(task_path)
    write_task_doc(
        task_path.parent,
        document.model_copy(
            update={
                "seriesContractPath": series_contract_path(contract.task_root).as_posix(),
                "enclosures": [
                    TaskEnclosureRef(
                        leafId=contract.leaf_id,
                        enclosurePath=contract.contract_path.as_posix(),
                    )
                ],
            }
        ),
    )


def _assert_no_profile_or_review(config, contract) -> None:
    """Make the transaction test's absent acceptance authorities explicit."""

    assert config.repositories[contract.repo_name].certification_profile is None
    document = read_task_doc(contract.task_root / f"{contract.leaf_id.lower()}.json")
    assert document.routeReview is None


def _forbid_acceptance_tools():
    """Patch historical acceptance entry points so an accidental call fails loudly."""

    return (
        mock.patch.multiple(
            quality_gate,
            run_strict_code_quality_gate=mock.Mock(
                side_effect=AssertionError("transaction called strict code quality")
            ),
            create=True,
        ),
        mock.patch.object(
            memory_quality,
            "run_memory_quality_phase",
            side_effect=AssertionError("transaction called memory quality"),
        ),
        mock.patch.object(
            coherence,
            "require_current_curator_coherence",
            side_effect=AssertionError("transaction called curator certification"),
        ),
    )


def _install_failing_pre_commit_hooks(contract, root: Path) -> tuple[Path, Path]:
    """Install real failing hooks in both repositories and return their logs."""

    logs = (root / "code-pre-commit.log", root / "memory-pre-commit.log")
    repositories = (contract.code_repo_path, contract.memory_repo_path)
    for repository, log in zip(repositories, logs, strict=True):
        assert repository is not None
        hook_directory = Path(_git(repository, "rev-parse", "--git-path", "hooks"))
        if not hook_directory.is_absolute():
            hook_directory = repository / hook_directory
        hook_directory.mkdir(parents=True, exist_ok=True)
        hook = hook_directory / "pre-commit"
        hook.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' transaction-hook-invoked >> {shlex.quote(log.as_posix())}\n"
            "exit 97\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        assert hook.is_file() and hook.stat().st_mode & 0o111
        probe = run_git(repository, ["hook", "run", "pre-commit"])
        assert probe.returncode == 97, probe
        assert log.read_text(encoding="utf-8").splitlines() == ["transaction-hook-invoked"]
        log.unlink()
    return logs


def _install_passing_pre_commit_hooks(contract, root: Path) -> tuple[Path, Path]:
    """Install recording passing hooks in both repositories and return their logs."""

    logs = (root / "code-pre-commit.log", root / "memory-pre-commit.log")
    repositories = (contract.code_repo_path, contract.memory_repo_path)
    for repository, log in zip(repositories, logs, strict=True):
        assert repository is not None
        log.unlink(missing_ok=True)
        hook_directory = Path(_git(repository, "rev-parse", "--git-path", "hooks"))
        if not hook_directory.is_absolute():
            hook_directory = repository / hook_directory
        hook_directory.mkdir(parents=True, exist_ok=True)
        hook = hook_directory / "pre-commit"
        hook.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' transaction-hook-invoked >> {shlex.quote(log.as_posix())}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        assert hook.is_file() and hook.stat().st_mode & 0o111
        probe = run_git(repository, ["hook", "run", "pre-commit"])
        assert probe.returncode == 0, probe
        assert log.read_text(encoding="utf-8").splitlines() == ["transaction-hook-invoked"]
        log.unlink()
    return logs


def _assert_landed_subject(
    repository: Path, expected: str | None, commit: str = "HEAD", hook: str = "none"
) -> None:
    """MIK-R152: a landed one-line message's `%s` subject carries the pass fact."""

    tree = _git(repository, "rev-parse", f"{commit}^{{tree}}")
    assert _git(repository, "log", "-1", "--format=%s", commit) == (
        f"{expected} Commit-Check-Pass: tree={tree} hook={hook}"
    )


def _assert_memory_attribution(
    memory_worktree: Path,
    *,
    code_commit: str,
    content_commit: str,
    scratch: Path,
    hook: str = "none",
) -> None:
    """The memory commit carries exactly one Code-Commit trailer and contains no cached ledger.

    The trailer is read back out of the committed object by both documented git readers, so
    this is the attribution being bound by the hash rather than recorded beside it.
    """

    body = _git(memory_worktree, "log", "-1", "--format=%B", content_commit)
    content_tree = _git(memory_worktree, "rev-parse", f"{content_commit}^{{tree}}")
    # MIK-R152: the memory commit carries the Commit-Check-Pass trailer for its tree
    # beside the single Code-Commit attribution trailer.
    check_trailer = f"Commit-Check-Pass: tree={content_tree} hook={hook}"
    assert body == f"{MESSAGES.memory}\n\nCode-Commit: {code_commit}\n{check_trailer}"
    assert body.count("Code-Commit:") == 1
    assert body.count("Commit-Check-Pass:") == 1
    message_file = scratch / "memory-content-message.txt"
    message_file.write_text(f"{body}\n", encoding="utf-8")
    assert (
        _git(memory_worktree, "interpret-trailers", "--parse", message_file.as_posix())
        == f"Code-Commit: {code_commit}\n{check_trailer}"
    )
    assert (
        _git(
            memory_worktree,
            "log",
            "-1",
            "--format=%(trailers:key=Code-Commit)",
            content_commit,
        )
        == f"Code-Commit: {code_commit}"
    )
    assert _git(memory_worktree, "ls-tree", "--name-only", content_commit, "--", "memory.md") == ""


def _closeout_fixture(tmp_path, *, converted: bool = False):
    """A leaf with a new code file and a new memory file, ready for the public closeout."""

    fixture = _fixture(
        tmp_path, external_memory=True, selected_profile=False, converted_memory=converted
    )
    contract = fixture.leaf_contract
    assert contract.memory_repo_path is not None and contract.memory_worktree is not None
    contract.memory_worktree.parent.mkdir(parents=True, exist_ok=True)
    contract.code_worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(
        contract.memory_repo_path,
        "worktree",
        "add",
        contract.memory_worktree.as_posix(),
        contract.memory_work_branch,
    )
    _git(
        fixture.code_repo,
        "worktree",
        "add",
        contract.code_worktree.as_posix(),
        contract.code_work_branch,
    )
    (contract.memory_worktree / "onboarding").mkdir(exist_ok=True)
    (contract.memory_worktree / "feature.md").write_text("# Feature\n", encoding="utf-8")
    (contract.code_worktree / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    ensure_fixture_waiting_door(contract, force_synthetic=True)
    contract = load_contract(contract.contract_path)
    _bind_task_without_review(contract)
    return contract, _public_config(tmp_path, contract)


def _apply(config, contract) -> dict:
    with mock.patch.object(lifecycle_operations, "require_first_ready_generation"):
        return worktree_tools.worktree_closeout_apply_tool(
            config,
            contract.contract_path.as_posix(),
            MESSAGES,
            CloseoutApproval(intent_note="approved confined transaction"),
        )


def _move_branch(repository: Path) -> None:
    """Publish one foreign commit on the repository's current branch, as another session would."""

    foreign = _git(repository, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "foreign")
    _git(repository, "update-ref", "HEAD", foreign)


SEAMS = {
    # Where inside ``publish_tree_commit`` the branch is moved, and what the refusal then says. The
    # tip check names a move that happened before it; only the expected-old move of the ref can
    # name one that happened after the commit object was written.
    "before-tip-check": r"moved from .* after this closeout admitted it, so nothing was published",
    "after-commit-written": r"moved while this closeout published, so nothing was published",
}


@contextmanager
def _branch_race(repository: Path, seam: str):
    """Move ``repository``'s branch with a foreign commit at one seam of the shared publication."""

    native = git_owner.run_git
    fired: list[bool] = []

    def race(repo, args, *extra):
        # The publication's own first read of the unfinished-action markers precedes its HEAD and
        # tip checks; ``commit-tree`` follows them.
        trigger = {"before-tip-check": "--git-path", "after-commit-written": "commit-tree"}[seam]
        before_call = seam == "before-tip-check" and trigger in args and "MERGE_HEAD" in args
        if repo == repository and not fired and before_call:
            fired.append(True)
            _move_branch(repository)
        result = native(repo, args, *extra)
        if repo == repository and not fired and seam == "after-commit-written" and trigger in args:
            fired.append(True)
            _move_branch(repository)
        return result

    with mock.patch.object(git_owner, "run_git", race):
        yield
    assert fired, f"the seam {seam} was never reached"


def _code_and_memory_state(contract) -> tuple[str, ...]:
    """Every fact a refused publication must give back: the refs, the index and the files."""

    return tuple(
        _git(repository, *arguments)
        for repository in (contract.code_worktree, contract.memory_worktree)
        for arguments in (
            ("rev-parse", "HEAD"),
            ("ls-files", "--stage"),
            ("status", "--porcelain=v1", "--untracked-files=all"),
        )
    )


def _seed_converted_memory_layout(memory_worktree) -> None:
    """The layout marker on converted memory, in canonical formatting."""

    layout = memory_worktree / "knowledge" / "layout.json"
    layout.parent.mkdir(exist_ok=True)
    layout.write_text(
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}), encoding="utf-8"
    )


def _seed_leaf_trace_row(contract, leaf_id: str, subject: str) -> None:
    """Seed the leaf's open history with the onboarding trace its new file needs.

    The closeout extends the open file with its own rows; the gate answers the trace item
    from this row.
    """

    path = subject.removeprefix("onboarding:")
    code = CodeSnapshot.capture(contract.code_worktree)
    history_dir = contract.memory_worktree / "knowledge" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / f"{leaf_id}.json").write_text(
        canonical_text(
            {
                "schema": "ar-history/v1",
                "leaf": leaf_id,
                "closed": False,
                "rows": [
                    {
                        "id": "ROW-000000",
                        "subject": subject,
                        "disposition": "no_impact",
                        "reason": "The new file carries no invariant.",
                        "items": [],
                        "sources": [{"path": path, "blob": code.blobs[path]}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _converted_closeout_fixture(tmp_path):
    """The public closeout fixture on converted memory, with a branch ``elsewhere`` on each side."""

    contract, config = _closeout_fixture(tmp_path, converted=True)
    assert contract.memory_worktree is not None
    _seed_converted_memory_layout(contract.memory_worktree)
    _seed_leaf_trace_row(contract, "leaf-1", "onboarding:feature.py")
    for repository in (contract.code_worktree, contract.memory_worktree):
        _git(repository, "branch", "elsewhere")
    return contract, config


def _memory_preparations(contract) -> tuple[object, ...]:
    """What a refused memory publication gives back: the leaf's history file and the ignore rule."""

    memory = contract.memory_worktree
    history = memory / f"knowledge/history/{contract.leaf_id}.json"
    ignore = memory / ".gitignore"
    return (
        history.read_bytes() if history.is_file() else None,
        ignore.read_bytes() if ignore.is_file() else None,
        _git(memory, "ls-files", "--stage"),
    )


UNADMITTED = {
    # (what happens to the checkout, the side, the moment) -> what the refusal says. The moment is
    # after the mandatory gate judged the tree ("judged") or when the publication takes the Git
    # snapshot it is bound to ("intent", after the closeout's own check just before it).
    ("switched", "memory", "intent"): r"memory worktree's HEAD names refs/heads/elsewhere, not",
    ("moved", "memory", "intent"): r"moved from \w+ to \w+ after this closeout read the memory",
    ("detached", "memory", "judged"): r"memory worktree's HEAD names no branch \(it is detached\)",
    # Review R3, finding 3: a failure between the preparation and the publication restores too.
    ("detached", "memory", "intent"): r"symbolic-ref",
    ("journal", "memory", "intent"): r"the operation journal could not record the intent",
    ("switched", "code", "intent"): r"code worktree's HEAD names refs/heads/elsewhere, not",
    ("moved", "code", "judged"): r"moved from \w+ to \w+ after this closeout read the code",
}


def _disturb(repository: Path, event: str) -> None:
    """Switch the checkout to another branch, detach it, or move its branch as another session."""

    if event == "switched":
        _git(repository, "symbolic-ref", "HEAD", "refs/heads/elsewhere")
    elif event == "detached":
        _git(repository, "update-ref", "--no-deref", "HEAD", _git(repository, "rev-parse", "HEAD"))
    elif event == "moved":
        _move_branch(repository)


@contextmanager
def _disturbed(contract, event: str, side: str, moment: str):
    """Apply ``event`` once to ``side``'s checkout at ``moment`` of the closeout; yields the log."""

    repository = contract.code_worktree if side == "code" else contract.memory_worktree
    owner = closeout_recovery if side == "code" else closeout_external
    begin, gate_calls, fired = owner.begin_git_mutation, [], []

    def judged(*_args, **_kwargs):
        # The gate is asked before the code commit (the preflight) and at the memory commit.
        gate_calls.append(True)
        if moment == "judged" and len(gate_calls) == (1 if side == "code" else 2):
            fired.append(True)
            _disturb(repository, event)

    def intent(args, **kwargs):
        if moment == "intent" and kwargs["leg"] == side and not fired:
            fired.append(True)
            _disturb(repository, event)
            if event == "journal":
                raise RuntimeError("the operation journal could not record the intent")
        return begin(args, **kwargs)

    with (
        mock.patch.object(closeout_external, "leaf_gate_refusal", side_effect=judged),
        mock.patch.object(owner, "begin_git_mutation", intent),
    ):
        yield fired


def _closeout_through_failing_then_passing_hooks(config, contract, messages, root):
    """MIK-R152: the landing refuses on failing hooks, then lands on passing ones."""

    _install_failing_pre_commit_hooks(contract, root)
    assert contract.code_worktree is not None and contract.memory_worktree is not None
    code_before = _git(contract.code_worktree, "rev-parse", "HEAD")
    memory_before = _git(contract.memory_worktree, "rev-parse", "HEAD")
    with ExitStack() as stack:
        for patcher in _forbid_acceptance_tools():
            stack.enter_context(patcher)
        # The existing helper publishes a deliberately non-applicable waiting door.  Its
        # disposable sprint has no queue projection, so bypass only that fixture fence while
        # retaining the public apply admission.
        stack.enter_context(
            mock.patch.object(lifecycle_operations, "require_first_ready_generation")
        )
        preview = worktree_tools.worktree_closeout_preview_tool(
            config, contract.contract_path.as_posix(), messages
        )
        assert preview["ok"] is True, preview
        assert preview["state"] == "would-closeout"
        with pytest.raises(LandingCommitCheckRefusal, match="commit check"):
            worktree_tools.worktree_closeout_apply_tool(
                config,
                contract.contract_path.as_posix(),
                messages,
                CloseoutApproval(intent_note="developer approved transaction"),
            )
        assert _git(contract.code_worktree, "rev-parse", "HEAD") == code_before
        assert _git(contract.memory_worktree, "rev-parse", "HEAD") == memory_before
        code_hook_log, memory_hook_log = _install_passing_pre_commit_hooks(contract, root)
        applied = worktree_tools.worktree_closeout_apply_tool(
            config,
            contract.contract_path.as_posix(),
            messages,
            CloseoutApproval(intent_note="developer approved transaction"),
        )
    assert applied["ok"] is True, applied
    assert applied["state"] == "closed", applied
    return applied, code_hook_log, memory_hook_log


def _effective_closeout_input() -> EffectiveCloseoutInput:
    leg = EnabledCloseoutLeg(reason="test", message="Test closeout commit")
    return EffectiveCloseoutInput(
        route="worktree",
        contractKind="leaf",
        memoryMode="external",
        code=leg,
        memory=leg,
    )


class _ClosedCacheLeaf:
    """One closed external leaf and the disposable cache derived from its Git attribution."""

    def __init__(self, root: Path) -> None:
        fixture = _authority_fixture(root, external_memory=True)
        closed = _closed_external_leaf_worktrees(fixture, root, publish_closeout_evidence=False)
        assert closed.memory_repo_path is not None and closed.memory_worktree is not None
        assert closed.ledger_path is not None
        self.contract = closed
        self.memory_worktree = closed.memory_worktree
        self.cache_path = closed.ledger_path
        self.canonical = derive_memory_ledger(
            self.memory_worktree, closed.memory_content_commit, repo_name=closed.repo_name
        )
        self.own_row = LedgerRow(closed.code_commit, closed.memory_content_commit)
        self.source_rows = [row for row in self.canonical.rows if row != self.own_row]
        self.content_path = self.memory_worktree / "candidate.md"
        self.content_bytes = self.content_path.read_bytes()
        self.code_path = closed.code_worktree / "candidate.txt"
        self.code_bytes = self.code_path.read_bytes()

    def render(self, rows: list[LedgerRow], *, header: tuple[str, str] | None = None) -> str:
        consistent = replace(
            self.canonical,
            rows=rows,
            last_verified_code_commit=rows[0].code_commit,
            last_memory_content_commit=rows[0].memory_commit,
        )
        text = ledger_to_text(consistent)
        if header is None:
            return text
        return text.replace(f'"{rows[0].code_commit}"', f'"{header[0]}"', 1).replace(
            f'"{rows[0].memory_commit}"', f'"{header[1]}"', 1
        )

    def refresh(self):
        return refresh_memory_cache(self.memory_worktree, repo_name=self.contract.repo_name)

    def git_state(self):
        return tuple(
            (
                _git(repository, "rev-parse", "HEAD"),
                _git(
                    repository,
                    "for-each-ref",
                    "--sort=refname",
                    "--format=%(refname) %(objectname)",
                ),
                tuple(
                    sorted(
                        line
                        for line in _git(
                            repository,
                            "cat-file",
                            "--batch-all-objects",
                            "--batch-check=%(objectname) %(objecttype)",
                        ).splitlines()
                        if line.endswith(" commit")
                    )
                ),
            )
            for repository in (self.contract.code_worktree, self.memory_worktree)
        )

    def assert_content_unchanged(self) -> None:
        assert self.content_path.read_bytes() == self.content_bytes
        assert self.code_path.read_bytes() == self.code_bytes
        assert _git(self.memory_worktree, "ls-files", "--", "memory.md") == ""
        assert _git(self.memory_worktree, "status", "--porcelain") == ""
        assert _git(self.contract.code_worktree, "status", "--porcelain") == ""
