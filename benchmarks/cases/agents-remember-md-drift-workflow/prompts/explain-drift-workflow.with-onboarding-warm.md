You are evaluating the Agents Remember drift-detection workflow.

Use `repos/agents-remember-md` as the target source checkout. Use the benchmark-local `ar-coordination/` directory for Agents Remember context.

Warm-memory setup:
The benchmark harness has already validated that the pinned external-memory repo matches the pinned source checkout for this run. Treat benchmark-local onboarding and memory files as trusted current-state context.

Execution discipline:
This is a non-interactive benchmark run. Do not ask the user questions, request
approval, or pause for follow-up. Make reasonable assumptions from the available
source and memory evidence and complete the primary task in one final answer.

Constraints:
- Do not edit source files.
- Do not run C-02 drift detection.
- Do not run the C-08 resolver CLI.
- Do not final-answer with only setup or memory-status notes.
- Do read relevant benchmark-local onboarding files alongside the source files they describe.
- Use the benchmark-local memory repo only; do not use any parent workspace memory.

Completion criteria:
The final answer must include the workflow explanation and file references.

Primary task:
Inspect C-02, C-08, and the drift helper implementation. Explain the drift workflow, including resolver handoff, sidecar metadata checks, inline digest checks, report generation, and C-05 handoff.
