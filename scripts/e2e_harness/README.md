# Clean-room E2E harness

This directory holds the fresh-user acceptance: one clean-room repository's first hour, run step
by step through the product's own entry points.

```text
python scripts/e2e_harness/run_fresh_user.py --reports <dir> [--work-root <dir>]
```

`run_fresh_user.py` is the entry point. It runs on the host, not inside the Dagger graph: the
requirement is a clean environment, and the run reads and writes nothing outside its run root. The
run root is a fresh temporary directory unless `--work-root` names one. The transcript lands at
`<dir>/fresh-user-acceptance/run.json`, and the exit status carries the verdict.

`fresh_user_fixture.py` creates two repositories from nothing under the run root: one whose spear
branch is `dev`, with a source file over the per-file cap and a vendored tree that the exclusion
register excludes, and one ordinary `main` repository for the default path.

`fresh_user_scenario.py` drives the flow over both fixtures: runtime install, `memory_init` with
the spear-branch choice, the thin bootstrap, the exclusion review, baseline adoption, the first
worktree task, then the citation fix, the memory quality check and closeout validation. Every step
records the exact call it made and the real result it got. A step that cannot run in the
environment is recorded as `blocked` with its reason, never as completed.

`reporting.py` holds the checkpoint definition, the recorder that collects one run's checkpoint
records into the report, and the JSON writer of the transcript.

The modules import each other by bare name, so the directory has no `__init__.py`.
`mcp/tests/test_fresh_user_harness.py` pins the shape the scenario needs; it does not run the
acceptance.
