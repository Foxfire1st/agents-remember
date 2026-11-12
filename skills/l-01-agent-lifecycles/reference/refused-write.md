# Correct-input write refusal — the same-tool ladder

Roles with an admitted writer or record operation use this procedure when that tool refuses a correct write. It covers only the refused tool and input class; it grants no new role or target authority.

## Classify the refusal first

Use the primary tool first. If its refusal identifies a fault in your input, correct the input through the same tool.

A missing tool or unadmitted capability is not a refused correct write. Report the missing capability and what remains unwritten.

Before applying a stop, classify the refused operation. A correct-input refusal of a covered write follows the ladder below. An admitted `task_doc` retirement or folder movement is included.

## Covered writes

Only when admitted to your role, this procedure covers:

- `knowledge_write` or admitted `knowledge-ingest`;
- `citation_fix`;
- `citation_note_revision`;
- `curator_coherence` publication;
- `task_doc` edits, including `append_decision`, `begin_review`, `record_review` and `record_route_review`.

## Follow the same-tool ladder

1. **Check the build and route.** Compare the serving build, invoked build, admitted target and input shape. Retain the tool's actual refusal. For the Curator's command-line interface binding, read [Bind the command to the serving build](knowledge/serving-build.md#bind-command-line-work-to-the-serving-build) before invoking that interface.
2. **Take an honest route that the tool admits.** Keep the claim true, the serving build unchanged and the target authority intact. Record every input and refusal. A route requiring hidden edits, an older build without a check, false tool authorship or a weakened false claim is not an admitted route.
3. **Otherwise make that write openly by hand.** Tell the owner of the record or machine state before writing. This exception covers only the same tool and input class that refused; every other write still uses its tool.

For a manual write:

- record the write and its provenance;
- run every check that still works;
- record the defect in an issue row;
- put the tool, every input tried, every refusal, the actual write, all check results and the issue row in the full report;
- claim no tool receipt for manual work.

The tool repair is a separate task when the developer slots it. The next Curator re-records the affected knowledge.

## Apply the procedure to a task document or folder

For a refused correct `task_doc` edit, record the attempted inputs and refusal, notify the owner, and amend the canonical document openly. The owner's next `task_doc` publication refreshes renders and projections. Rendered copies are never parsed back.

For task retirement or folder movement, use the product tool first. If it refuses correct input, follow the ladder above: record the refusal, tell the owner and perform the logged manual step. Do not retain a blanket ban on hand movement.

The task-folder exception covers only the refused retirement or movement step, never a Git commit or another transaction step. This procedure gives Bootstrap no new writer, task-document or migration duty.

## Preserve the stops

Read, check, validation and sync refusals, starts, product guards and commit gates keep their boundaries. Repair every validator and gate finding at once. Never bypass a check on your own; only the developer may allow `--no-verify`, for one occasion. Never set a finding aside as pre-existing. This write ladder does not waive them.

Before acting on a denied or unanswered harness request, read [Honor harness permission decisions](../operations/orientation.md#honor-harness-permission-decisions) for the waiting, denial and non-bypass rules. Other tools and input classes retain their normal routes.
