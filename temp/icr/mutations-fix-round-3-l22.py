"""Fix-round-3 bite-proof: disable each of the four previously-unpinned validator clauses."""
import subprocess
import sys
from pathlib import Path

LEAF = Path("/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l22-ar/260921-icr-l22")
PRISTINE = Path("/home/firefox/projects/ar-coordination/temp/l22/pristine")
TARGET = LEAF / "mcp/src/agents_remember/models/knowledge/review_staleness.py"
BACKUP = PRISTINE / "model/review_staleness.py"
PYTHON = "/home/firefox/projects/agents-remember/mcp/.venv/bin/python"

CLAUSES = {
    "H1a stale requires something moved": (
        '        if state == "stale" and not moved:',
        "        if False:",
    ),
    "H1b record_readable agrees with unavailable": (
        '        if (state == "unavailable") == self.record_readable:',
        "        if False:",
    ),
    "H1c no resolved identity unless stale": (
        '        if state != "stale" and any(named):',
        "        if False:",
    ),
    "H1d stale names a resolved identity": (
        '        if state == "stale" and not any(named):',
        "        if False:",
    ),
}


def restore() -> None:
    TARGET.write_bytes(BACKUP.read_bytes())


def main() -> int:
    restore()
    for name, (old, new) in CLAUSES.items():
        text = TARGET.read_text()
        if old not in text:
            print(f"{name}: TARGET NOT FOUND")
            continue
        TARGET.write_text(text.replace(old, new, 1))
        run = subprocess.run(
            [
                PYTHON, "-m", "pytest", "mcp/tests/test_review_sync_rebinding.py",
                "-q", "-m", "", "-p", "no:randomly", "-k", "refuses_each_false_shape",
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
        verdict = "test FAILS (clause pinned)" if run.returncode else "test PASSES (clause still unpinned)"
        print(f"{name}: rc={run.returncode} :: {tail[0]} :: {verdict}")
        restore()
    restore()
    print("restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
