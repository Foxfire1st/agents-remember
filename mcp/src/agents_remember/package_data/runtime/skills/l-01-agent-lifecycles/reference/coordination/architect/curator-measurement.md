# Curator measurement changes and discussion

This file gives the Architect the rules for running or discussing a Curator measurement. It grants no additional authority.

For measurement tooling, receive the candidate and case-set identity through the coordinating Manager. Return the go on that same route. This is a simple handover, not a review freeze; it starts no seat.

Run evaluations when the Curator's text, model or role set changes, and on the developer's request. No role text gains a grader step on every leaf. The grader code stays in `scripts/agent_evals/` with ordinary tests.

Every measurement belongs to the Architect; the Worker implements tooling and agreed edits. A run gets its own coordination report, raw evidence, and a one-page summary per case theme.

New-text trials use the tool build of the candidate whose text is measured. Every run binds the head, change hash, build, text signature, case hashes, model, mode, judge and capture versions.

The case set, its fourteen cases in seven fixed pairs (two cases per theme), and the run commands are described in `scripts/agent_evals/README.md` in the agents-remember repository.

- Report "all 3 right" and "at least 2 of 3" per case and per fixed pair, separately per model.
- A case below 2 of 3 on a model is a failure for the loop. A case at 2 of 3 is reported and discussed, and does not force another round.
- The loop: run the full set, discuss the failures with the developer, change the text, repeat the failing cases until none remain, then one final full run.
- A theme is worse, on a model, when fewer of its two cases reach at least 2 of 3 in the final run than in the old-text run. In the final run, only a worse theme reopens the loop. Other failures are reported.
- Unchanged text is re-run only to replace a trial spoiled by an infrastructure fault.
- No ruling record is kept per loop change.

An infrastructure failure, a missing capture or an unanswered prompt makes a trial unknown. Report it with its cause. The Architect fixes the cause and reruns those trials. Only a case that cannot run at all goes to the developer.

Record tokens per trial and report them with the results. Spending does not wait for the developer. Record tokens per attempt and per run, judge usage separately, and unknown usage distinctly.

**About 2.5 million tokens is the comparison guide, not a per-trial cap.**

This is the agreed observation/improvement process, **not a new product or automatic acceptance gate**.

