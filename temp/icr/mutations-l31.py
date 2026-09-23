"""Guard bite-proofs for ICR-R31: each mutation removes one guard, and one named case must fail.

Run from the leaf worktree with the leaf's own ``mcp/src`` on the path. Every mutation is reverted in
a ``finally`` block, and the script prints the file's digest before and after each one so a reader can
see the tree came back to the same bytes.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = ROOT / "mcp/src/agents_remember/application/review_family_context.py"
ROSTERS = ROOT / "mcp/src/agents_remember/application/review_family_rosters.py"
MODEL = ROOT / "mcp/src/agents_remember/models/knowledge/review_family_context.py"
PYTHON = "/home/firefox/projects/agents-remember/mcp/.venv/bin/python"

MUTATIONS: tuple[tuple[str, Path, str, str, str, str], ...] = (
    (
        "M1 the head rule stops refusing several heads",
        CONTEXT,
        '''    if len(before_heads) != 1 or len(after_heads) != 1:
        return "ambiguous" if before_heads and after_heads else "unresolved"
    return "compared"''',
        '''    if before_heads and after_heads:
        return "compared"
    return "unresolved"''',
        "mcp/tests/test_review_family_context.py",
        "ambiguous_family_lineage",
    ),
    (
        "M2 a truncated roster page claims to be complete",
        ROSTERS,
        '''        state="continued" if continued_from is not None else "first_page",
        counts=page.counts,
        complete=page.enumeration_complete,''',
        '''        state="continued" if continued_from is not None else "first_page",
        counts=page.counts,
        complete=True,''',
        "mcp/tests/test_review_family_context.py",
        "truncated_roster",
    ),
    (
        "M3 a measured zero is reported as a review that selected nothing",
        CONTEXT,
        '''    return _stated(
        "no_family_recorded",
        f"the recorded scope was read on both snapshots and holds no family membership for the "''',
        '''    return _stated(
        "no_subject_selected",
        f"the recorded scope was read on both snapshots and holds no family membership for the "''',
        "mcp/tests/test_review_family_context.py",
        "measured_zero",
    ),
    (
        "M4 the guarantee is read from the selected revision's predecessor instead of itself",
        ROSTERS,
        '''    stored = families.get_family_revision(store, revision_id)
    if stored is None:
        return None''',
        '''    stored = families.get_family_revision(store, revision_id)
    if stored is not None and stored.revision.predecessors:
        stored = families.get_family_revision(store, stored.revision.predecessors[0]) or stored
    if stored is None:
        return None''',
        "mcp/tests/test_review_family_context.py",
        "own_guarantees",
    ),
    (
        "M5 a recorded side stops checking its selected revision against the family's own list",
        MODEL,
        """        if recorded and self.family_revision_id not in self.recorded_revision_ids:
            raise ValueError(""",
        """        if False and self.family_revision_id not in self.recorded_revision_ids:
            raise ValueError(""",
        "mcp/tests/test_review_family_context_values.py",
        "does_not_record",
    ),
    (
        "M6 the context stops checking its unique member count against its rosters",
        MODEL,
        '''        if self.unique_member_revision_total != len(distinct):
            raise ValueError(''',
        '''        if False and self.unique_member_revision_total != len(distinct):
            raise ValueError(''',
        "mcp/tests/test_review_family_context_values.py",
        "counts_do_not_describe",
    ),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(test_path: str, expression: str) -> str:
    completed = subprocess.run(
        [
            PYTHON,
            "-m",
            "pytest",
            test_path,
            "-q",
            "-n0",
            "-p",
            "no:randomly",
            "-k",
            expression,
            "--no-header",
            "-x",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": f"{ROOT}/mcp/src:{ROOT}/mcp/test_support",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(ROOT),
        },
        timeout=900,
    )
    tail = [line for line in completed.stdout.strip().splitlines() if line.strip()]
    return " | ".join(tail[-3:])


def main() -> int:
    failures = 0
    for name, path, old, new, test_path, expression in MUTATIONS:
        original = path.read_text()
        before = digest(path)
        if old not in original:
            print(f"{name}: MUTATION TEXT NOT FOUND in {path}")
            failures += 1
            continue
        try:
            path.write_text(original.replace(old, new, 1))
            outcome = run(test_path, expression)
        finally:
            path.write_text(original)
        after = digest(path)
        reverted = "reverted-bytes-identical" if before == after else "REVERT-FAILED"
        print(f"{name}\n  mutation digest {before[:12]} -> {after[:12]} ({reverted})")
        print(f"  named case     : {test_path} -k {expression}")
        print(f"  observed       : {outcome}")
        if "failed" not in outcome:
            failures += 1
    print("all guards bit" if failures == 0 else f"{failures} guard(s) did not bite")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
