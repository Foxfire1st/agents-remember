You are evaluating the Agents Remember drift-detection workflow.

Use `repos/agents-remember-md` as the target source checkout. Use the benchmark-local `ar-coordination/` directory for Agents Remember context.

Run control:
The required C-08/C-02 onboarding drift gate is startup work only. Passing the drift check is not task completion.

After the drift gate is clean:
1. Do not final-answer with only the drift summary.
2. Continue immediately to the primary task below.
3. Mention the drift check only briefly as prerequisite status.
4. Final-answer only after the primary task completion criteria are satisfied.

Primary task:
Inspect C-02, C-08, and the drift helper implementation. Explain the drift workflow, including resolver handoff, sidecar metadata checks, inline digest checks, report generation, and C-05 handoff. Do not edit source files.

Completion criteria:
The final answer must include the workflow explanation and file references. A clean drift check alone is not sufficient.
