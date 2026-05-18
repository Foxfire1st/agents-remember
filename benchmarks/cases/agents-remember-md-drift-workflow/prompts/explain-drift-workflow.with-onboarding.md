You are evaluating the Agents Remember drift-detection workflow.

Use `repos/agents-remember-md` as the target source checkout. Use the benchmark-local `ar-coordination/` directory for Agents Remember context.

Execution discipline:
This is a non-interactive benchmark run. Do not ask the user questions, request
approval, or pause for follow-up. Make reasonable assumptions from the available
source and memory evidence and complete the primary task in one final answer.

Constraints:

1. Do not final-answer with only the drift summary.
2. Continue immediately to the primary task below.
3. Mention the drift check only briefly as prerequisite status.
4. Final-answer only after the primary task completion criteria are satisfied.

Run control:
The required C-08/C-02 onboarding drift gate is startup work only. Passing the drift check is not task completion.

Completion criteria:
The final answer must include the workflow explanation and file references. A clean drift check alone is not sufficient.

Primary task:
Explain the drift workflow, including resolver handoff, sidecar metadata checks, inline digest checks, report generation, onboarding handoff.
