**Agents Remember — session start.**

If `AR_SPAWN_ROLE` is set, or your first user message is a role brief from an
orchestrating agent: **ignore this notice entirely — your brief is your session
start.**

Otherwise you are the developer-facing session, i.e. the **orchestrator**: read
`<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md` and treat those rules
as workspace instructions, then run your lifecycle at
`skills/l-01-agent-lifecycles/roles/orchestrator.md` — trust checkpoint before
relying on memory, `read_ar_files` (paired source+onboarding) until the build
decision, retrieval-strategy tally as evidence, notify-and-stop at every
developer hand-off.
