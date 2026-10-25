"""MIK-R198 rule 5 at the owner transactions: a stale anchor refuses, a repair publishes.

This module extends ``test_reference_anchor_publication_owners`` to publication owners whose
evidence the review still owed (L191-PRE-04):

* ``worktrees.modules.closeout.closeout_result`` -- the entry behind
  ``worktree_closeout_apply_tool`` -- on a fresh APPLY over the converted closeout gate's own world
  (its two invariants, family, cards and history are the gate's), and on the recovered-closeout
  output branch, where the commits a rerun resumes on are judged rather than trusted;
* ``application.lifecycle.direct_landing.direct_landing_tool``, the series direct landing;
* the closeout's selected memory-quality phase (a narrower check group), which passes over the same
  stale world while the publication still refuses: a selected check is not an anchor waiver.

The fault is one reference anchor, built rather than injected. A tool module is created on the
leaf's code side, its bytes are hashed into the code repository by ``git hash-object`` and the
recorded blob/content come from the product's own resolver (``anchor_at``). The curator's card
then cites it: ``references.1.targets.0`` on the ``pkg/a.py`` sidecar carries a ``symbol`` locator
naming ``tools/policy.py``'s ``policy``, and the card's Evidence line carries the ``[1]`` marker so
the note-side rules are answered. When the code side later appends a function below that symbol,
the located bytes are still exactly the recorded content and only the file's blob moved -- the
state MIK-R198 rule 5 refuses and the shipped mechanical fixer repairs by re-recording the blob.
Nothing here re-anchors ``realizes`` entries or touches the invariants, so the anchor is the only
thing a publication can refuse. Each branch is driven twice over two builds of the same fixture:
once with the anchor current, so the entry publishes and the fixture is shown to be publishable,
and once with only the anchor stale, so the refusal is attributable to it.

Every assertion is on the entry's own answer: the refusal names ``R198.1-reference-anchor`` and no
other rule, the refused run commits nothing on either side, and the repaired run publishes and
records. The closeout fixture's missing enclosure admission, settings and branch authority are
stubbed exactly where the closeout suite stubs them for this world; the entry that refuses, commits
and records is still ``closeout_result``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from agents_remember.application.knowledge_worklist.leaf import recompute_leaf_worklist
from agents_remember.application.lifecycle.direct_landing import direct_landing_tool
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.memory_quality.check import DEFAULT_STYLE_CHECKS
from agents_remember.memory_quality.reference_state import fix_references
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees.modules import closeout, closeout_external, onboarding
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.quality.closeout_memory import run_memory_quality_phase
from agents_remember.worktrees.queue import closeout_recovery
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from agents_remember.worktrees.worktree_contract import load_contract
from test_direct_landing import _converted_request, _converted_series_fixture
from test_knowledge_closeout_gate import (  # noqa: F401 -- fixtures pytest resolves here
    LEAF,
    A,
    Gated,
    anchor_at,
    build_gated,
    commit,
    git,
    ports,
    write,
)

RULE = "R198.1-reference-anchor"
POLICY = "tools/policy.py"
POLICY_V1 = "def policy():\n    return 1\n"
# The symbol's own bytes are unchanged by this edit: the file blob moves, the content does not.
POLICY_V2 = POLICY_V1 + "\n\ndef later():\n    return 2\n"
CARD = f"onboarding/{A}.md"
SIDECAR = f"onboarding/{A}.json"
RULE_ID = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9.\-]*)\]")
NAMED_RULE = re.compile(r"rule='([A-Za-z0-9][A-Za-z0-9.\-]*)'")
A_RULE = re.compile(r"^R\d")


@dataclass(frozen=True)
class _Context:
    """The resolved context the closeout's metadata refresh reads (the fixture has no settings)."""

    code_repository_root: Path
    onboarding_root: Path
    code_repository_name: str = "agents-remember"
    storage: StorageSettings = field(default_factory=StorageSettings)


def _effective() -> EffectiveCloseoutInput:
    return EffectiveCloseoutInput.model_validate(
        {
            "route": "worktree",
            "contractKind": "leaf",
            "memoryMode": "external",
            "code": {"state": "enabled", "reason": "leaf", "message": "code"},
            "memory": {"state": "enabled", "reason": "leaf", "message": "memory"},
        }
    )


def _named_findings(detail: str) -> list[str]:
    """The rule each finding line of a gate refusal names, one entry per finding.

    A gate refusal lists its findings one per ``- `` line, prefixed by the finding's category
    (``[knowledge-validator]``, ``[knowledge-item-open]``) and carrying the rule in the body. This
    answers "which rules refused" rather than "which categories did", so a refusal that also
    withheld a worklist item cannot read as an anchor-only refusal.
    """

    named = []
    for line in detail.splitlines():
        if not line.startswith("- "):
            continue
        found = set(NAMED_RULE.findall(line)) or {
            one for one in RULE_ID.findall(line) if A_RULE.match(one)
        }
        named.append(",".join(sorted(found)) if found else line.strip())
    return named


@dataclass
class _Cited:
    """The gate's world with one cited tool module, its reference anchor and the leaf's rows."""

    world: Gated

    def code_bytes(self, data: str) -> str:
        """Publish ``data`` as ``tools/policy.py`` on the leaf's code side; return the commit."""

        return commit(self.world.code, {POLICY: data})

    def memory_commit(self, files: dict[str, str], *, trailer: str | None = None) -> str:
        """Commit memory content with the ``Code-Commit`` trailer the landed pair binds (R52)."""

        return commit(
            self.world.memory,
            files,
            trailer=trailer or git(self.world.code, "rev-parse", "HEAD"),
        )

    def cite(self) -> str:
        """Record the card and the reference anchor at the code side's current bytes."""

        anchor = anchor_at(
            self.world.code,
            POLICY,
            {"kind": "symbol", "name": "policy"},
            (self.world.code / POLICY).read_bytes(),
        )
        document = json.loads((self.world.memory / SIDECAR).read_text(encoding="utf-8"))
        document["references"] = {
            "1": {"targets": [{"kind": "code", "anchor": {**anchor, "path": POLICY}}]}
        }
        card = "# a\n\n## Evidence\n\n- The policy refuses a second rule. [1]\n"
        return self.memory_commit({SIDECAR: canonical_text(document), CARD: card})

    def answer_the_rows(self, *, commit_rows: bool = True) -> str:
        """Close the leaf's history with the rows the changed tool module owes and nothing more.

        A source with no card of its own is admitted only by its onboarding trace: the worklist
        says so in ``facts.admits``, and the row binds the blobs the item names at this candidate.
        """

        found = recompute_leaf_worklist(self.world.contract)
        assert found is not None, "the fixture's worklist cannot be computed"
        assert found[0]["state"] == "complete", found[0]
        rows: list[dict[str, Any]] = []
        for item in found[0]["items"]:
            facts = item["facts"]
            if item["kind"] == "unexplained_hunk" and "onboarding_trace" in facts["admits"]:
                trace = facts["onboardingTrace"]
                rows.append(
                    {
                        "subject": trace["subject"],
                        "disposition": "no_impact",
                        "reason": "The cited tool module carries no invariant of its own.",
                        "sources": [
                            {"path": path, "blob": blob}
                            for path, blob in sorted(trace["sourceBlobs"].items())
                        ],
                    }
                )
                continue
            if item["kind"] == "onboarding_trace":
                rows.append(
                    {
                        "subject": item["subject"],
                        "disposition": "no_impact",
                        "reason": "The route card still describes what the repository is.",
                        "sources": [
                            {"path": path, "blob": blob}
                            for path, blob in sorted(facts["sourceBlobs"].items())
                        ],
                    }
                )
                continue
            if item["kind"] == "unexplained_hunk":
                rows.append(
                    {
                        "subject": f"hunk:{item['id']}",
                        "disposition": "no_invariant",
                        "reason": "The cited tool module carries no invariant of its own.",
                    }
                )
                continue
            raise AssertionError(f"this fixture owes a row kind it does not author: {item}")
        assert rows, "the cited module owes the leaf no row; this world would not test the gate"
        document = {
            "schema": "ar-history/v1",
            "leaf": LEAF,
            "closed": False,
            "rows": [
                {"id": f"ROW-{number:06d}", "items": [], **row} for number, row in enumerate(rows)
            ],
        }
        files = {f"knowledge/history/{LEAF}.json": canonical_text(document)}
        if not commit_rows:
            write(self.world.memory, files)
            return ""
        return self.memory_commit(files)

    def close_the_history(self) -> str:
        """Close the leaf's history in memory itself: the state a rerun with nothing left finds.

        An already-closed, committed latest history is what the closeout's own closing would
        write, so its closing write is the no-op that leaves both sides with nothing to commit.
        """

        base = self.world.memory / f"knowledge/history/{LEAF}.json"
        document = json.loads(base.read_text(encoding="utf-8"))
        document["closed"] = True
        return self.memory_commit({f"knowledge/history/{LEAF}.json": canonical_text(document)})


def _world(root: Path) -> _Cited:
    root.mkdir(parents=True, exist_ok=True)
    return _Cited(build_gated(root / "coord"))


def _sides(world: Gated) -> tuple[tuple[str, str], tuple[str, str]]:
    """Both sides' commits and uncommitted state: what a refused closeout must not change."""

    code = (git(world.code, "rev-parse", "HEAD"), git(world.code, "status", "--porcelain"))
    memory = (git(world.memory, "rev-parse", "HEAD"), git(world.memory, "status", "--porcelain"))
    return code, memory


def _apply(
    world: Gated,
    *,
    dry_run: bool = False,
    recovery_commits: Any = None,
) -> Any:
    """Run the real closeout entry. Only the fixture's absent authorities are stubbed.

    ``_closeout_entry`` normally re-proves the accepted candidate, the durable operation and the
    enclosure admission; this world has no enclosure, settings or branch authority, so the suite
    that owns it stubs them here too (``test_knowledge_reopen._public_closeout``). Everything the
    rule under test can refuse -- the preflight gate over the candidate, the exact memory tree's
    validator and gate at the commit, both commits, the closing write and the contract
    finalization -- runs unchanged.
    """

    contract, effective = world.contract, _effective()
    args = WorktreeArgs(
        contract_path=contract.contract_path,
        approved=not dry_run,
        dry_run=dry_run,
        approval_note="approved",
        closeout_input=effective,
        recovery_commits=recovery_commits,
    )
    context = _Context(world.code, world.memory / "onboarding")
    with (
        mock.patch.object(closeout, "_closeout_entry", return_value=(contract, effective, None)),
        mock.patch.object(closeout, "report_operation_progress"),
        mock.patch.object(closeout, "_refuse_unsatisfied_closeout_gate"),
        mock.patch.object(closeout, "_revalidate_candidate", return_value=contract),
        mock.patch.object(closeout, "_claim_closeout_gate", return_value=None),
        mock.patch.object(closeout, "require_ordinary_worktree"),
        mock.patch.object(closeout_recovery, "report_operation_progress"),
        mock.patch.object(closeout_external, "contract_context", return_value=context),
        mock.patch.object(closeout_external, "report_operation_progress"),
        mock.patch.object(onboarding, "contract_context", return_value=context),
    ):
        return closeout.closeout_result(args, contract)


def _cited_world(root: Path) -> _Cited:
    """A cited world whose changed tool module owes the leaf exactly the rows authored here."""

    cited = _world(root)
    cited.code_bytes(POLICY_V1)
    cited.cite()
    cited.answer_the_rows()
    return cited


def test_a_fresh_closeout_refuses_the_stale_anchor_and_publishes_the_repaired_pair(
    tmp_path: Path,
    ports: Any,  # noqa: F811 -- the closeout gate's own service composition
) -> None:
    """APPLY, fresh: the same entry accepts the pair, refuses the stale anchor, then publishes.

    The healthy world and the faulted one are the same fixture with the same card, the same
    reference anchor and the same answered rows. In the healthy world the cited symbol's blob is
    the one the anchor recorded, so the closeout commits both sides and records itself. In the
    faulted world one later code commit appends a function below the cited symbol, so the located
    bytes are still the recorded content and only the file's blob moved: the same entry refuses,
    names only ``R198.1-reference-anchor``, and leaves both worktrees exactly as they stood. The
    shipped fixer re-records that blob and nothing else, and the same entry then publishes.
    """

    ports()
    healthy = _cited_world(tmp_path / "healthy")
    healthy_path = healthy.world.contract_path()

    closed = _apply(healthy.world)

    assert (closed.returncode, closed.payload["state"]) == (0, "closed"), closed.payload
    assert load_contract(healthy_path).closeout_status == "completed"

    faulted = _cited_world(tmp_path / "faulted")
    faulted_path = faulted.world.contract_path()
    faulted_record = faulted_path.read_bytes()
    faulted.code_bytes(POLICY_V2)
    faulted.answer_the_rows()
    before = _sides(faulted.world)

    with pytest.raises(RuntimeError) as refused:
        _apply(faulted.world)

    detail = str(refused.value)
    assert RULE in detail, detail
    assert _named_findings(detail) == [RULE], detail
    assert _sides(faulted.world) == before, "a refused closeout committed something"
    assert faulted_path.read_bytes() == faulted_record, "a refused closeout amended the contract"

    report = fix_references(faulted.world.memory, faulted.world.code)
    assert report["stale"] == [], report
    assert report["refreshedAnchors"] == 1, report
    repaired = faulted.memory_commit(
        {
            one: (faulted.world.memory / one).read_text(encoding="utf-8")
            for one in report["rewrittenSidecars"]
        }
    )

    applied = _apply(faulted.world)

    assert (applied.returncode, applied.payload["state"]) == (0, "closed"), applied.payload
    recorded = load_contract(faulted_path)
    assert recorded.closeout_status == "completed", recorded.closeout_status
    assert recorded.memory_content_commit == applied.payload["memory_content_commit"]
    assert repaired != recorded.memory_content_commit  # the closeout closed the history on top
    assert git(faulted.world.code, "rev-parse", "HEAD") == applied.payload["code_commit"]


def test_a_recovered_closeout_output_refuses_the_stale_anchor_and_publishes_repaired(
    tmp_path: Path,
    ports: Any,  # noqa: F811 -- the closeout gate's own service composition
) -> None:
    """The recovered-closeout output branch: the pair a rerun resumes on is judged, not trusted.

    A closeout that resumes with commits already made records them as the leaf's output. This is
    the branch ``require_gated_recovery`` owns: the recovered pair is judged by the same validator
    and gate as a fresh candidate, so a pair whose reference anchor went stale after the memory
    commit was made is refused by name here rather than published on the strength of having been
    committed. The memory branch never moves: the recorded output is the commit the rerun resumed
    on, and after the shipped fixer re-records the blob the same entry publishes it.
    """

    ports()
    faulted = _world(tmp_path / "recovered")
    faulted_path = faulted.world.contract_path()
    faulted.code_bytes(POLICY_V1)
    faulted.cite()
    faulted.code_bytes(POLICY_V2)
    faulted.answer_the_rows()
    recovered_code = git(faulted.world.code, "rev-parse", "HEAD")
    recovered_memory = git(faulted.world.memory, "rev-parse", "HEAD")
    recovery = LifecycleOperationRecoveryCommits(
        codeCommit=recovered_code, memoryContentCommit=recovered_memory
    )

    with pytest.raises(RuntimeError) as refused:
        _apply(faulted.world, recovery_commits=recovery)

    detail = str(refused.value)
    assert RULE in detail, detail
    assert _named_findings(detail) == [RULE], detail
    assert git(faulted.world.memory, "rev-parse", "HEAD") == recovered_memory

    report = fix_references(faulted.world.memory, faulted.world.code)
    assert report["stale"] == [], report
    assert report["refreshedAnchors"] == 1, report
    repaired = faulted.memory_commit(
        {
            one: (faulted.world.memory / one).read_text(encoding="utf-8")
            for one in report["rewrittenSidecars"]
        }
    )
    resumed = LifecycleOperationRecoveryCommits(
        codeCommit=recovered_code, memoryContentCommit=repaired
    )

    applied = _apply(faulted.world, recovery_commits=resumed)

    assert (applied.returncode, applied.payload["state"]) == (0, "closed"), applied.payload
    recorded = load_contract(faulted_path)
    assert recorded.closeout_status == "completed", recorded.closeout_status
    assert recorded.memory_content_commit == repaired, recorded.memory_content_commit
    assert git(faulted.world.memory, "rev-list", "--count", f"{repaired}..HEAD") == "0"


def test_a_direct_landing_refuses_the_stale_anchor_and_lands_the_repaired_pair(
    tmp_path: Path,
) -> None:
    """Direct landing: the series publication refuses the stale anchor and then publishes.

    ``direct_landing_tool`` is the owner that lands one verified code commit with its memory. Over
    the same fixture it lands the healthy pair, refuses the pair whose cited blob moved with
    ``direct-landing-knowledge-gate-refused`` and the anchor rule named, and lands again once the
    shipped fixer re-records that blob. The memory branch is only moved by a landing that succeeded:
    after the refusal it is exactly where it stood.
    """

    bind_worktree_services(build_default_worktree_services())
    try:
        healthy = _converted_series_fixture(tmp_path / "healthy-landing")
        healthy_cited = _Cited(healthy["world"])
        healthy_cited.code_bytes(POLICY_V1)
        healthy_cited.cite()
        healthy_cited.answer_the_rows(commit_rows=False)
        healthy_code = git(healthy["code"], "rev-parse", "HEAD")

        landed = direct_landing_tool(healthy["config"], _converted_request(healthy, healthy_code))

        assert landed["ok"], landed["detail"]
        assert git(healthy["memory"], "rev-parse", "HEAD") != healthy["world"].memory_base

        faulted = _converted_series_fixture(tmp_path / "faulted-landing")
        faulted_cited = _Cited(faulted["world"])
        faulted_cited.code_bytes(POLICY_V1)
        faulted_cited.cite()
        cited_at = git(faulted_cited.world.code, "rev-parse", "HEAD")
        faulted_cited.code_bytes(POLICY_V2)
        faulted_cited.answer_the_rows(commit_rows=False)
        faulted_code = git(faulted["code"], "rev-parse", "HEAD")
        memory_before = git(faulted["memory"], "rev-parse", "HEAD")

        refused = direct_landing_tool(faulted["config"], _converted_request(faulted, faulted_code))
        assert not refused["ok"], refused
        assert refused["status"] == "direct-landing-knowledge-gate-refused", refused
        detail = str(refused["detail"])
        assert RULE in detail, detail
        assert _named_findings(detail) == [RULE], detail
        assert git(faulted["memory"], "rev-parse", "HEAD") == memory_before

        report = fix_references(faulted_cited.world.memory, faulted_cited.world.code)
        assert report["stale"] == [], report
        assert report["refreshedAnchors"] == 1, report
        # The repair is a memory commit of its own, attributed to the code commit the card was
        # written against: the landing still judges it rather than reading it as its own output.
        repaired = faulted_cited.memory_commit(
            {
                one: (faulted_cited.world.memory / one).read_text(encoding="utf-8")
                for one in report["rewrittenSidecars"]
            },
            trailer=cited_at,
        )

        landed_again = direct_landing_tool(
            faulted["config"], _converted_request(faulted, faulted_code)
        )

        assert landed_again["ok"], landed_again
        published = git(faulted["memory"], "rev-parse", "HEAD")
        assert landed_again["memoryContentCommit"] == published, landed_again
        assert published != repaired, "the landing closed the history on top of the repair"
        assert (
            json.loads(
                git(faulted["memory"], "show", f"{published}:knowledge/history/{LEAF}.json")
            )["closed"]
            is True
        )
        assert (
            git(faulted["memory"], "log", "-1", "--format=%(trailers:key=Code-Commit)", published)
            == f"Code-Commit: {faulted_code}"
        )
    finally:
        reset_worktree_services()


def test_a_selected_style_check_passes_and_the_publication_still_refuses(tmp_path: Path) -> None:
    """The selected-check non-bypass: a narrower quality phase is not an anchor waiver.

    The closeout's own memory-quality phase is a selection: the caller names the check group it
    runs, and the style group reads the onboarding documents' shape, not their anchors. Over the
    stale world that phase passes -- it is not the anchor rule and never claimed to be. The
    publication refuses anyway, and names the anchor rule alone: no narrower selected check clears
    the debt, because only the validator over the exact paired tree decides it.
    """

    bind_worktree_services(build_default_worktree_services())
    try:
        cited = _world(tmp_path / "selected")
        cited.code_bytes(POLICY_V1)
        cited.cite()
        cited.code_bytes(POLICY_V2)
        cited.answer_the_rows()

        selected = run_memory_quality_phase(
            _Context(cited.world.code, cited.world.memory / "onboarding"),
            DEFAULT_STYLE_CHECKS,
        )

        assert selected["ok"] is True, selected
        assert RULE not in json.dumps(selected), selected

        with pytest.raises(RuntimeError) as refused:
            _apply(cited.world)

        detail = str(refused.value)
        assert _named_findings(detail) == [RULE], detail
    finally:
        reset_worktree_services()


def test_a_closeout_with_nothing_left_to_commit_still_refuses_the_stale_anchor(
    tmp_path: Path,
    ports: Any,  # noqa: F811 -- the closeout gate's own service composition
) -> None:
    """APPLY with no memory commit: the tree the closeout records is still judged (rule 5).

    The leaf's code side already holds the cited source at its new blob and the contract's base is
    that commit, so the closeout owes no code change and its metadata refresh has nothing to write;
    the leaf's history is closed and committed, so its closing write changes nothing either. The
    memory commit this closeout records is therefore the one already there -- and that exact tree
    is still validated: the stale anchor refuses, the memory branch does not move, and after the
    shipped fixer re-records the blob the same entry publishes with no memory commit of its own.
    """

    ports()
    cited = _world(tmp_path / "nothing-to-commit")
    cited_path = cited.world.contract_path()
    cited.code_bytes(POLICY_V1)
    cited.cite()
    cited.code_bytes(POLICY_V2)
    cited.answer_the_rows()
    # The leaf is re-based onto the code it describes: the contract's bases are the two heads, so
    # this closeout owes a commit on neither side.
    closed_code = git(cited.world.code, "rev-parse", "HEAD")
    cited.world.code_base = closed_code
    cited.world.memory_base = git(cited.world.memory, "rev-parse", "main")
    cited.close_the_history()
    memory_head = git(cited.world.memory, "rev-parse", "HEAD")
    assert git(cited.world.memory, "status", "--porcelain") == ""
    assert git(cited.world.code, "status", "--porcelain") == ""

    with pytest.raises(RuntimeError) as refused:
        _apply(cited.world)

    detail = str(refused.value)
    assert RULE in detail, detail
    assert _named_findings(detail) == [RULE], detail
    assert git(cited.world.memory, "rev-parse", "HEAD") == memory_head

    report = fix_references(cited.world.memory, cited.world.code)
    assert report["stale"] == [], report
    assert report["refreshedAnchors"] == 1, report
    repaired = cited.memory_commit(
        {
            one: (cited.world.memory / one).read_text(encoding="utf-8")
            for one in report["rewrittenSidecars"]
        }
    )

    # The route-index caches the closeout's own refresh would write are brought current first, so
    # the closeout has nothing to write on the memory side at all.
    build_route_indexes(
        code_root=cited.world.code,
        onboarding_root=cited.world.memory / "onboarding",
        repository="agents-remember",
        storage=StorageSettings(),
        dry_run=False,
    )
    repaired = cited.memory_commit({})
    assert git(cited.world.memory, "status", "--porcelain") == ""

    applied = _apply(cited.world)

    assert (applied.returncode, applied.payload["state"]) == (0, "closed"), applied.payload
    recorded = load_contract(cited_path)
    assert recorded.closeout_status == "completed", recorded.closeout_status
    assert recorded.memory_content_commit == repaired, recorded.memory_content_commit
    assert git(cited.world.memory, "rev-list", "--count", f"{repaired}..HEAD") == "0"
