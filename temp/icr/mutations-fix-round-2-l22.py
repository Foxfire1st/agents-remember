"""Fix-round-2 mutations: restore each round-2 behaviour and require a case to go red."""
import subprocess
import sys
from pathlib import Path

LEAF = Path("/home/firefox/projects/ar-coordination/worktrees/agents-remember/260921-icr-l22-ar/260921-icr-l22")
PRISTINE = Path("/home/firefox/projects/ar-coordination/temp/l22/pristine")
PYTHON = "/home/firefox/projects/agents-remember/mcp/.venv/bin/python"
TARGETS = {
    "movement": (
        LEAF / "mcp/src/agents_remember/application/review_sync_movement.py",
        PRISTINE / "app/review_sync_movement.py",
    ),
    "staleness": (
        LEAF / "mcp/src/agents_remember/models/knowledge/review_staleness.py",
        PRISTINE / "model/review_staleness.py",
    ),
    "rebinding": (
        LEAF / "mcp/src/agents_remember/application/review_sync_rebinding.py",
        PRISTINE / "app/review_sync_rebinding.py",
    ),
}

MUTATIONS = {
    "MG1a the two-way reading: 'unmeasured' mapped to current": (
        "movement",
        """    "unmeasured": "not-measured",""",
        """    "unmeasured": "current",""",
        "uncompared_knowledge_channel",
    ),
    "MG1b the unmeasured state rendered with the agreement sentence": (
        "movement",
        """    if state == "not-measured":
        return (
            f"a managed sync completed and {generation} does not fully describe the pair it "
            f"resolved: {reason}. Only the part that was compared is reported as measured, and the "
            f"rest is unmeasured; {_SUPERSESSION}"
        )""",
        """    if state == "not-measured":
        return (
            f"a managed sync completed and {generation} still describes the pair it resolved: "
            "the reviewed dataset is the one the leaf holds"
        )""",
        "uncompared_knowledge_channel",
    ),
    "MG1c an unreadable record collapsed into 'nothing recorded'": (
        "movement",
        """    if read.state == "unreadable":
        return _unavailable(manifest, read.detail)""",
        """    if read.state == "unreadable":
        return None""",
        "cannot_be_used",
    ),
    "MG1d the cross-check bypassed for a record that names another generation": (
        "movement",
        """    record = rebinding_names_the_generation(read, manifest)
    if record is None:
        if read.state == "not-recorded":
            return None""",
        """    record = read.rebinding
    if record is None:
        if read.state == "not-recorded":
            return None""",
        "cannot_be_used",
    ),
    "MG1e the absence-without-a-reason rule removed": (
        "staleness",
        """        if reports_absence and not (self.reason or "").strip():""",
        """        if False:""",
        "uncompared_knowledge_channel",
    ),
    "MG1f the moved-input-implies-stale rule removed": (
        "staleness",
        """        if moved and state != "stale":""",
        """        if False:""",
        "uncompared_knowledge_channel",
    ),
    "MG2 the success conjunct removed again": (
        "rebinding",
        """        and bool(payload.get("ok"))
""",
        "",
        "carrying_state_reported_as_a_failure",
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
