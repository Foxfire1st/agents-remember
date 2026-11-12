# Read the ingest report before claiming publication

Curators read this topic before interpreting an authored-knowledge ingest result or claiming publication. It explains planned, written and refused results, file-writing authority and report evidence.

Without `--commit`, ingest plans and validates without writing files. `--commit` authorizes file writes, never a Git commit or acceptance.

Read the actual report state, written paths, record and entry identifiers, and refusals. The ingest state is `written`, `planned` or `refused`. A refused ingest wrote nothing and names each problem with its own reason. Keep a partial handover visibly partial. Exit zero covers both a plan and a write. Even `written` with no written paths establishes no publication.

Before responding to a correct-input write refusal, read [correct-write recovery](../refused-write.md) for the procedure for the same tool and input class. A target without its required rationale is refused as `realization_rationale_absent`. The writer requires converted memory.
