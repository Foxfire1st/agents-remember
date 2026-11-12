"""MIK-R198 rule 5 at the publication owners: a stale anchor refuses, a repair publishes.

MIK-R198 rule 5 says a knowledge reference anchor must name the blob of its source path in the
**exact paired code tree** and resolve there to its recorded content identity. Rule 7 splits the
dispositions: a sync merge reports the debt it creates, and every publication refuses it.

The pair these cases publish is built, not injected. Two real code sides move the **cited source**
in the same file -- the leaf rewrites the module docstring, the official line appends a function
below it -- and every memory side that commits re-records its anchors through the shipped
mechanical fixer (``memory_quality.reference_state.fix_references``), whose own answer is that
nothing is left stale. So the ordinary-merge case combines two memory trees that were each current
at their own code commit, and the fast-forward case adopts a tree that was current at the line's
code while the code side settles a real merge of the two code moves. The combination is what goes
stale: the merged ``app.py`` blob is neither side's blob, so an anchor that was current at its own
commit no longer names the paired tree. Nothing here writes a fabricated ``blob``; every recorded
blob comes from ``git hash-object`` of bytes an actual commit holds.

Owners driven here, through their public entry points:

* ``worktrees.modules.sync.sync_result`` in fast-forward mode -- reports the debt it adopts (rule 7);
* ``worktrees.modules.sync.sync_result`` in ordinary merge mode -- reports the debt the merge
  creates from two valid sides (rule 7);
* ``worktrees.modules.record_landing.record_landing_result`` on a leaf -- **refuses** the stale pair
  and records the repaired one.

The recorded-landing case also shows the refusal is the anchor rule's and nothing else's: the same
entry accepts the same world before the cited source moves (``would-record``), every rule the
refusal names is ``R198.1-reference-anchor``, answering the leaf's worklist rows does not waive it,
and repairing the anchor alone then makes the entry ``recorded``.

The fixture is the conversion test support's two-line converted memory (``test_worktree_sync_knowledge_merge``),
whose sidecars carry real reference anchors written by the real conversion. That fixture also carries
an anchor to a source its own conversion then deleted; a leaf publication refuses that under MIK-R22
rule 6, so ``_clean`` brings the world to a state where the anchor rule under test is the only thing
a publication can refuse. The cleaning changes no reference anchor.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_worklist.leaf import recompute_leaf_worklist
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.memory_quality.reference_state import fix_references
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.record_landing import record_landing_result
from agents_remember.worktrees.modules.sync import sync_result
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract
from knowledge_conversion_test_support import APP, APP_SOURCE, commit_files, git
from test_worktree_sync import section
from test_worktree_sync_knowledge_merge import Lines, _converted_lines

RULE = "R198.1-reference-anchor"
APP_SIDECAR = f"onboarding/{APP}.json"
DELETED_SIDECAR = "onboarding/src/pkg/deleted.py.json"
# The leaf rewrites the docstring; the line appends below the anchored ranges. The two changes are
# one line each and far apart, so Git merges them, and neither is the base.
LEAF_SOURCE = '"""App module."""' + APP_SOURCE[len('"""App."""') :]
LINE_SOURCE = APP_SOURCE + "\n\ndef gamma(value):\n    return alpha(value) * 2\n"
RULE_ID = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9.\-]*)\]")


def _commit(root: Path, files: dict[str, bytes], message: str) -> str:
    for relative, data in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(root, "add", "-A", "--", *files)
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def _commit_the_fix(root: Path, report: dict[str, Any], message: str) -> str:
    """Commit exactly the sidecars the shipped fixer rewrote, and nothing else.

    The fixer reports what it left for judgment: an empty ``stale`` list is its own answer that
    every reference anchor of this tree is current at the code root it was given.
    """

    assert report["stale"] == [], report
    assert report["refreshedAnchors"] > 0, report
    rewritten = report["rewrittenSidecars"]
    assert rewritten, report
    return _commit(root, {one: (root / one).read_bytes() for one in rewritten}, message)


def _rule_ids(text: str) -> set[str]:
    return set(RULE_ID.findall(text))


@dataclass
class _World:
    """Two converted memory lines, their code repositories, and the owners' own entries."""

    root: Path
    lines: Lines

    @property
    def contract(self) -> WorktreeContract:
        return load_contract(self.lines.fixture.contract.contract_path)

    @property
    def code_repo(self) -> Path:
        return self.contract.code_repo_path

    @property
    def code_worktree(self) -> Path:
        assert self.contract.code_worktree is not None
        return self.contract.code_worktree

    def sync(self, **kwargs: Any) -> dict[str, Any]:
        """``worktrees.modules.sync.sync_result`` -- the entry ``worktree_sync`` calls."""

        return dict(
            sync_result(WorktreeArgs(contract_path=self.contract.contract_path, **kwargs)).payload
        )

    def record(self, landed_code: str, landed_memory: str, *, dry_run: bool = False) -> Any:
        """``worktrees.modules.record_landing.record_landing_result`` -- the pull-request owner."""

        return record_landing_result(
            WorktreeArgs(
                contract_path=self.contract.contract_path,
                approved=not dry_run,
                dry_run=dry_run,
                landed_code_commit=landed_code,
                landed_memory_content_commit=landed_memory,
            )
        )


def _world(tmp_path: Path) -> _World:
    lines = _converted_lines(tmp_path)
    return _World(tmp_path, lines)


def _author_the_merged_code_rows(world: _World) -> str:
    """Author the rows a leaf owes for the merged code, and close the attempt holding them.

    The merge brought a code change that no leaf row answers, so without this the entry under test
    would name the worklist before it ever reached the anchor. The rows are taken from the
    product's own worklist facts -- a ``no_invariant`` row for each unexplained hunk, a
    ``no_impact`` trace row for each card still describing its source -- and closed in the leaf's
    next attempt file, which is the shape a closeout leaves. Nothing here touches a reference
    anchor: the anchor rule is still the only thing the landing can refuse.
    """

    found = recompute_leaf_worklist(world.contract)
    assert found is not None, "the converted leaf's worklist cannot be computed"
    assert found[0]["state"] == "complete", found
    rows: list[dict[str, Any]] = []
    for item in found[0]["items"]:
        if item["kind"] == "unexplained_hunk":
            rows.append(
                {
                    "subject": item["facts"]["row"],
                    "disposition": "no_invariant",
                    "reason": "The merged function carries no invariant of its own.",
                }
            )
            continue
        rows.append(
            {
                "subject": item["subject"],
                "disposition": "no_impact",
                "reason": "The card still describes the source it covers.",
                "sources": [
                    {"path": path, "blob": blob}
                    for path, blob in sorted(item["facts"]["sourceBlobs"].items())
                ],
            }
        )
    assert rows, "the merged code owes the leaf no row; this world would not test the worklist"
    leaf = world.contract.leaf_id
    document = {
        "schema": "ar-history/v1",
        "leaf": leaf,
        "attempt": 2,
        "closed": True,
        "rows": [
            {"id": f"ROW-{number:06d}", "items": [], **row} for number, row in enumerate(rows)
        ],
    }
    return _commit(
        world.lines.leaf,
        {f"knowledge/history/{leaf}-attempt-2.json": canonical_text(document).encode("utf-8")},
        "author the merged code's rows and close the attempt",
    )


def _clean(world: _World) -> None:
    """Bring the fixture to a state where the anchor rule is the only publication refusal.

    The conversion fixture carries one anchor to a source the fixture then deleted (MIK-R22 rule 6
    refuses it at a leaf publication), and its leaf has no closed history file yet. Both are
    properties of the fixture, not of the citation under test, so they are settled first and the
    resulting tree is what the sync advances the leaf to.
    """

    contract = world.contract
    code = git(contract.code_repo_path, "rev-parse", "main")
    carried = json.loads((world.lines.line / DELETED_SIDECAR).read_text(encoding="utf-8"))
    assert carried["realizes"], "the fixture no longer carries the anchor this settling removes"
    history = canonical_text(
        {"schema": "ar-history/v1", "leaf": contract.leaf_id, "closed": True, "rows": []}
    )
    _commit(
        world.lines.line,
        {
            DELETED_SIDECAR: canonical_text({**carried, "realizes": []}).encode("utf-8"),
            f"knowledge/history/{contract.leaf_id}.json": history.encode("utf-8"),
        },
        render_memory_content_message("settle the carried anchor and close the leaf", code),
    )
    settled = world.sync()
    assert settled["state"] == "synced", settled
    assert section(settled, "memory")["state"] == "completed", settled
    assert RULE not in (settled.get("memoryRepairDebt") or {}).get("rules", []), settled


def _grow_the_cited_source(world: _World, *, the_leaf_reanchors: bool) -> tuple[str, str]:
    """Move the cited source on both code sides; re-record each side's anchors through the fixer.

    Returns the line's code commit and the leaf's code commit. When ``the_leaf_reanchors`` is false
    the leaf's memory is left exactly where the previous sync put it, which is what a memory
    fast-forward advances -- the leaf's code moved, so the tree the sync adopts is judged at the
    code side's settled result rather than at the leaf's own pair.
    """

    contract = world.contract
    line_code = commit_files(contract.code_repo_path, {APP: LINE_SOURCE}, "the line grows app.py")
    _commit_the_fix(
        world.lines.line,
        fix_references(world.lines.line, contract.code_repo_path),
        render_memory_content_message("the line re-records its anchors", line_code),
    )
    leaf_code = commit_files(
        world.code_worktree, {APP: LEAF_SOURCE}, "the leaf rewrites the docstring"
    )
    if the_leaf_reanchors:
        _commit_the_fix(
            world.lines.leaf,
            fix_references(world.lines.leaf, world.code_worktree),
            render_memory_content_message("the leaf re-records its anchors", leaf_code),
        )
    return line_code, leaf_code


def _blob(repository: Path, revision: str) -> str:
    return git(repository, "rev-parse", f"{revision}:{APP}")


def test_the_memory_fast_forward_names_the_anchor_debt_of_the_pair_it_adopts(
    tmp_path: Path,
) -> None:
    """MIK-R198 rules 5 and 7: a fast-forward reports the stale anchor it has just adopted.

    The tree the fast-forward takes was current at the line's code commit; the code side settles a
    real merge of two code moves, whose ``app.py`` is neither side's blob. The route must not
    silently publish that pair: the debt names the rule. Without the report channel the sync
    returns the same "synced" and the finding is dropped, which is why this assertion is on the
    named debt rather than on the state.
    """

    world = _world(tmp_path)
    _clean(world)
    line_code, leaf_code = _grow_the_cited_source(world, the_leaf_reanchors=False)

    result = world.sync()

    assert result["state"] == "synced", result
    memory = section(result, "memory")
    assert memory["state"] == "completed" and memory["plan"] == "fast-forward", memory
    debt = result.get("memoryRepairDebt")
    assert isinstance(debt, dict), result
    assert debt["side"] == "memory", debt
    assert RULE in debt["rules"], debt
    merged = git(world.code_worktree, "rev-parse", "HEAD")
    assert _blob(world.code_repo, line_code) != _blob(world.code_worktree, merged)
    assert _blob(world.code_worktree, leaf_code) != _blob(world.code_worktree, merged)


def test_the_ordinary_merge_names_the_anchor_debt_two_valid_sides_create(tmp_path: Path) -> None:
    """MIK-R198 rules 5 and 7: both sides were current at their own commit; the merge is not.

    Each side re-records its own anchors through the shipped fixer before it commits, and the
    fixer's own answer says nothing is left stale on either side. The merged pair is stale anyway,
    because its code blob is the combination. A merge judged by both parents reports rather than
    refuses (rule 7), so the route completes and carries the rule's name.
    """

    world = _world(tmp_path)
    _clean(world)
    line_code, leaf_code = _grow_the_cited_source(world, the_leaf_reanchors=True)

    result = world.sync(memory_sync_choice="merge-memory")

    assert result["state"] == "synced", result
    memory = section(result, "memory")
    assert memory["state"] == "completed" and memory["plan"] == "merge", memory
    debt = result.get("memoryRepairDebt")
    assert isinstance(debt, dict), result
    assert RULE in debt["rules"], debt
    merged = git(world.code_worktree, "rev-parse", "HEAD")
    line_blob, leaf_blob, merged_blob = (
        _blob(world.code_repo, line_code),
        _blob(world.code_worktree, leaf_code),
        _blob(world.code_worktree, merged),
    )
    assert len({line_blob, leaf_blob, merged_blob}) == 3, (line_blob, leaf_blob, merged_blob)


def test_a_recorded_leaf_landing_refuses_the_stale_pair_and_records_the_repaired_one(
    tmp_path: Path,
) -> None:
    """MIK-R198 rule 5 where it refuses: the recorded leaf landing, before and after the repair.

    Same entry, same world, four answers. Before the cited source moves it would record the pair.
    Once the merged pair exists with anchors that name the pre-merge blob it refuses, and every
    rule it names is the anchor rule -- so the refusal is attributable to the anchor rather than to
    the fixture. Authoring the rows the merged code owes does not change that: the worklist is
    answered and the anchor rule still refuses, because no row waives it. Repairing the anchors,
    and nothing else, then makes the same entry record the pair.
    """

    world = _world(tmp_path)
    _clean(world)
    healthy_code = git(world.code_repo, "rev-parse", "main")
    healthy_memory = git(world.lines.leaf, "rev-parse", "HEAD")

    before = world.record(healthy_code, healthy_memory, dry_run=True)

    assert before.returncode == 0, before.payload
    assert before.payload["state"] == "would-record", before.payload

    _grow_the_cited_source(world, the_leaf_reanchors=False)
    synced = world.sync()
    assert synced["state"] == "synced", synced
    assert RULE in (synced.get("memoryRepairDebt") or {}).get("rules", []), synced
    # A merged pull request leaves the landing target holding the pair the branch carries.
    landed_code = git(world.code_worktree, "rev-parse", "HEAD")
    git(world.code_repo, "update-ref", "refs/heads/main", landed_code)
    stale_memory = git(world.lines.leaf, "rev-parse", "HEAD")

    refused = world.record(landed_code, stale_memory)

    assert refused.returncode != 0, refused.payload
    assert refused.payload["state"] == "refused", refused.payload
    assert refused.payload["code"] == "knowledge-gate-refused", refused.payload
    detail = str(refused.payload["detail"])
    assert RULE in detail, detail
    assert _rule_ids(detail) == {RULE}, detail
    assert load_contract(world.contract.contract_path).integration_status == "not-started"

    # Everything except the anchor is answered now, and the anchor rule still refuses: the leaf's
    # rows are not a waiver (MIK-R198 rule 7 publishes no exemption on a publication route).
    answered_memory = _author_the_merged_code_rows(world)
    still_refused = world.record(landed_code, answered_memory)

    assert still_refused.returncode != 0, still_refused.payload
    assert _rule_ids(str(still_refused.payload["detail"])) == {RULE}, still_refused.payload

    repaired_memory = _commit_the_fix(
        world.lines.leaf,
        fix_references(world.lines.leaf, world.code_worktree),
        "re-record the anchors at the merged code",
    )

    recorded = world.record(landed_code, repaired_memory)

    assert recorded.returncode == 0, recorded.payload
    assert recorded.payload["state"] == "recorded", recorded.payload
    stored = load_contract(world.contract.contract_path)
    assert stored.integration_status == "completed", stored.integration_status
    assert stored.integrated_code_commit == landed_code, stored.integrated_code_commit


def test_the_merge_that_named_the_debt_then_refuses_and_publishes_the_repaired_pair(
    tmp_path: Path,
) -> None:
    """MIK-R198 rules 5 and 7 in sequence: the merge reports the debt, publication refuses it.

    The ordinary merge above proves the pair the merge creates carries the rule's name as repair
    debt. This is what that debt means for the next owner: the same merged pair, published, is
    refused -- after every worklist row the merged code owes is answered, so nothing else can be
    the reason -- and repairing the anchors alone makes the same entry record it.
    """

    world = _world(tmp_path)
    _clean(world)
    _grow_the_cited_source(world, the_leaf_reanchors=True)

    merged = world.sync(memory_sync_choice="merge-memory")

    assert merged["state"] == "synced", merged
    assert RULE in (merged.get("memoryRepairDebt") or {}).get("rules", []), merged
    landed_code = git(world.code_worktree, "rev-parse", "HEAD")
    git(world.code_repo, "update-ref", "refs/heads/main", landed_code)
    answered = _author_the_merged_code_rows(world)

    refused = world.record(landed_code, answered)

    assert refused.returncode != 0, refused.payload
    assert refused.payload["state"] == "refused", refused.payload
    detail = str(refused.payload["detail"])
    assert RULE in detail, detail
    assert _rule_ids(detail) == {RULE}, detail
    assert load_contract(world.contract.contract_path).integration_status == "not-started"

    repaired = _commit_the_fix(
        world.lines.leaf,
        fix_references(world.lines.leaf, world.code_worktree),
        "re-record the anchors at the merged code",
    )

    recorded = world.record(landed_code, repaired)

    assert recorded.returncode == 0, recorded.payload
    assert recorded.payload["state"] == "recorded", recorded.payload
    stored = load_contract(world.contract.contract_path)
    assert stored.integration_status == "completed", stored.integration_status
    assert stored.integrated_code_commit == landed_code, stored.integrated_code_commit
