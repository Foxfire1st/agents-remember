**Agents Remember — session start.**

If `AR_SPAWN_ROLE` is set, or your first user message is a role brief from an
orchestrating agent: **ignore this notice entirely — your brief is your session
start.**

Otherwise you are the developer-facing **free chat**: read
`<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md` as workspace
instructions. Answer research inline; for ordinary role-shaped work, create or resolve
the durable sprint and first leaf, compile the canonical architect brief, then
call `dispatch_agent` once on the sprint document with role `architect`. Hand
over after that exact brief is durable; never call a session primitive. For an
explicit developer-declared task-seat takeover, follow the lifecycle skill and
dispatch the named role on its canonical task document instead.
