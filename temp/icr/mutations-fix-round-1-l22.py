"""Fix-round mutations: restore each old behaviour and require the new case to go red."""
import subprocess
import sys
from pathlib import Path

LEAF = Path("/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l22-ar/260921-icr-l22")
PRISTINE = Path("/home/firefox/projects/ar-coordination/temp/l22/pristine")
PYTHON = "/home/firefox/projects/agents-remember/mcp/.venv/bin/python"
TARGETS = {
    "app": (
        LEAF / "mcp/src/agents_remember/application/review_sync_rebinding.py",
        PRISTINE / "app/review_sync_rebinding.py",
    ),
    "model": (
        LEAF / "mcp/src/agents_remember/models/knowledge/review_sync_rebinding.py",
        PRISTINE / "model/review_sync_rebinding.py",
    ),
    "adapter": (
        LEAF / "mcp/src/agents_remember/application/knowledge_review.py",
        PRISTINE / "app/knowledge_review.py",
    ),
    "movement": (
        LEAF / "mcp/src/agents_remember/application/review_sync_movement.py",
        PRISTINE / "app/review_sync_movement.py",
    ),
}

MUTATIONS = {
    "MF1a blanket 'not a completed sync' for every non-carrying state": (
        "app",
        """        carried_nothing = _CARRIED_NOTHING.get(state)
        if carried_nothing is not None:
            block_state, detail = carried_nothing
            return {"state": block_state, "detail": detail}""",
        """        if True:
            return {
                "state": "not-applicable",
                "detail": (
                    "this result is not a completed sync, so no source/knowledge pair was resolved "
                    "for a review to be measured against"
                ),
            }""",
        "every_state_that_carried_nothing",
    ),
    "MF1b already-current measured as a carrying state": (
        "app",
        """        "synced",
        "sync-pass-completed-memory-skipped",""",
        """        "already-current",
        "synced",
        "sync-pass-completed-memory-skipped",""",
        "every_state_that_carried_nothing",
    ),
    "MF1c the removed codeBaseCommit conjunct restored": (
        "app",
        """    return (
        payload.get("operation") == "worktree_sync"
        and str(payload.get("state", "")) in _CARRYING_SYNC_STATES
    )""",
        """    return (
        payload.get("operation") == "worktree_sync"
        and str(payload.get("state", "")) in _CARRYING_SYNC_STATES
        and "codeBaseCommit" in payload
    )""",
        "every_state_that_carried_nothing",
    ),
    "MF2 the old 'head carries candidate tree' wording": (
        "model",
        '''        capture = self.resolved_candidate_code_tree_id
        located = (
            f"candidate tree {capture} captured from the leaf's worktree at work branch head "
            f"{self.resolved_code_head}"
        )''',
        '''        capture = self.resolved_candidate_code_tree_id
        located = (
            f"the work branch head {self.resolved_code_head} carries candidate tree {capture}"
        )''',
        "locator_not_a_carrier",
    ),
    "MF3 the selection's own state and sentence published as the block's": (
        "app",
        """    return _no_generation_block(selection)""",
        """    return {"state": selection.state, "detail": selection.detail}""",
        "measurable_generation",
    ),
    "MF6a the adapter not folding the measured movement": (
        "adapter",
        """    staleness = review_staleness_with_sync_movement(
        review_staleness(identity, request.previous_binding_digest), sync_movement
    )""",
        """    staleness = review_staleness(identity, request.previous_binding_digest)""",
        "live_review_read",
    ),
    "MF6b the live read not measuring at all": (
        "adapter",
        """    sync_movement = review_sync_movement(resolved)""",
        """    sync_movement = None""",
        "live_review_read",
    ),
    "MF6c a movement that claims movement without the resolved identity": (
        "movement",
        """        resolved_code_head=record.resolved_code_head if code_moved else None,""",
        """        resolved_code_head=None,""",
        "live_review_read",
    ),
    "MF4 the old reader absence sentence": (
        "app",
        '''            detail=(
                f"nothing is recorded at {destination} for comparison generation "
                f"{generation_id} of {leaf_id}; a record this leaf's own reclamation discarded and "
                "one that was never written read the same here"
            ),''',
        '''            detail=(
                f"no managed sync has measured comparison generation {generation_id} of {leaf_id}: "
                f"nothing is recorded at {destination}"
            ),''',
        "after_its_own_reclamation",
    ),
}


def restore() -> None:
    for target, pristine in TARGETS.values():
        target.write_bytes(pristine.read_bytes())


def main() -> int:
    restore()
    for name, (kind, old, new, keyword) in MUTATIONS.items():
        target, _ = TARGETS[kind]
        text = target.read_text()
        if old not in text:
            print(f"{name}: TARGET NOT FOUND")
            restore()
            continue
        target.write_text(text.replace(old, new, 1))
        run = subprocess.run(
            [
                PYTHON, "-m", "pytest", "mcp/tests/test_review_sync_rebinding.py",
                "-q", "-m", "", "-p", "no:randomly", "-k", keyword,
            ],
            cwd=LEAF,
            text=True,
            capture_output=True,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "HOME": "/home/firefox",
                "PYTHONPATH": "mcp/src:mcp/test_support",
            },
        )
        tail = (run.stdout or "").strip().splitlines()[-1:] or [""]
        verdict = "test FAILS (fix load-bearing)" if run.returncode else "test PASSES (NOT load-bearing)"
        print(f"{name}: rc={run.returncode} :: {tail[0]} :: {verdict}")
        restore()
    restore()
    print("restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
