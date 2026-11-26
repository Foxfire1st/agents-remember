<!-- ar:roles all -->
# Operation — Implementation

Pass `system/coding-guidelines.md` handover checks on a clean freeze copy (record check/tree/result; fix first). Focused tests (sync; prove collection on removals/renames); `## Final checks` post-PASS.

Write one local report (report template + `system/code-quality-report-template.md`; map evidence; per-finding repairs). Regenerate Curator list per freeze + `handoff-check` (`server_info`).

1. Freeze; tell Manager to start Reviewer.
2. Send Reviewer report path (no pasted text), head/hash + next stretch via `role_message`.
3. Repair sealed own-file findings directly; return freeze to same Reviewer.
4. After code PASS + Curator start, send Curator list + `handoff-check` directly.
5. Post-PASS: `## Final checks` on passed tree; send cmd/result/hash to Manager+Reviewer.

Manager notices: first-freeze Reviewer start, authority, seat starts/replacements, beyond-limit rounds, disputes, reserved sync steps; cross-leaf needs named dependency.
