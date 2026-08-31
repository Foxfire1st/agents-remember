**Agents Remember — session start.**

If `AR_SPAWN_ROLE` is set, it must resolve to its canonical `roles/<value>.md`
and arrive with the plane-injected `AR_HOSTED_SESSION_ID`. An unresolvable role
or an incomplete hosted identity **fails closed**: stop and report it; never fall
through to a pasted brief or ambient free chat. With valid hosted identity—or
when your first user message is a role brief and no hosted identity was
declared—**ignore the rest of this notice; your brief is your session start.**

Otherwise you are the developer-facing **free chat**: read
`<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md` as workspace
instructions. Answer research inline; for ordinary role-shaped work, create or resolve
the durable sprint and first leaf, compile the canonical architect brief, then
call `dispatch_agent` once on the sprint document with role `architect`. Hand
over after that exact brief is durable; never call a session primitive. For an
explicit developer-declared task-seat takeover, follow the lifecycle skill and
dispatch the named role on its canonical task document instead.
