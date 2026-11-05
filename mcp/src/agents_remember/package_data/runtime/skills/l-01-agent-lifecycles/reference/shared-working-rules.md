# Shared working rules

## Record times from a clock read

Read the clock in the same step for each recorded time; append corrections rather than rewriting a wrong time.

## Count evidence from the producing call

Read the producing call before counting; claim absence only from an unfiltered list.

## Compare the serving build before diagnosing a tool mismatch

Compare the serving tool build with the code before reporting mismatched behavior as a defect.

## Remove scratch paths safely

Avoid forced recursive removal through a shell variable; use a fresh folder or an approved literal path.

## Bind sandbox harness settings per run

Do not write user-level harness configuration; bind a sandbox actor per run or stop and ask.

## Report the user-level Pi MCP file

Leave an agent-created ~/.pi/agent/mcp.json unchanged and tell the parent, who routes one notice to the developer.

## Report graphics measurements on the measured setup

Name the graphics setup behind a browser measurement; do not claim it represents the developer’s browser without its evidence.

## Compare sandbox versions with the developer installation

Name sandbox host, plug-in and build versions; treat a difference from the developer’s installation as a finding.

## Use the admitted sandbox model

Claude test actors need no extra developer permission and no prior-proof-admission condition. Use the cheap Claude model under D250, naming it in the command and record; ordinary sandbox isolation still applies.

## Keep product settings out of user-owned harness files

The product never edits a harness file the user owns; a setting AR needs is shipped through its own launch settings, or else documented on that harness's page.

## stopping a process or operating on the live host

Stop or signal a process only by a checked PID or the sandbox’s own PID file. Change nothing on the developer’s live host except the steps already allowed: dashboard rebuild under D212, host step under D170 and seat reloads.
