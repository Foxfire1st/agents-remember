# Clean-room E2E harnesses

`run.py` is the reusable Dagger entry point for real-client acceptance scenarios. The ambient
role-chat scenario uses pinned Codex `0.151.0`, lets Codex discover the candidate MCP over its
normal stdio configuration, and drives only the model's deterministic function-call choices from a
local Responses API. The scripted provider selects the non-Lite `gpt-5.5` model profile because
the current `gpt-5.6-sol` Responses Lite path omits tools for custom providers; this does not alter
production role settings. The harness does not replace or exercise a production starter: those
continue using the release-updating `uvx --refresh-package ... @latest` command.

The fixture gives the inner Codex process `danger-full-access` with approval policy `never` so the
current client can auto-approve MCP calls. That authority exists only inside the credential-free,
network-bounded Dagger clean room; the outer container remains the security boundary. The first
checkpoint requires the dispatched architect seat to exist, so a completed turn containing an
approval refusal cannot be reported as a successful tool invocation.

After the initial one-call brief transaction is accepted, the ambient caller repeats the exact same
architect dispatch once inside each replication and must retain the same canonical occupant and
brief row; this is the L2 idempotency assertion, not a failure retry. The
live client-observed dispatch advertisement is also subjected to controlled missing-field and
missing-caller-description mutations, both of which must fail at the canonical public-surface
validator. Cleanup always runs, and any cleanup error is preserved separately from the primary
scenario failure.

The fixture owns one isolated tmux server. Its Codex MCP registration explicitly forwards the
dynamic `TMUX_TMPDIR` into every candidate MCP process, so sessions created by ambient and hosted
dispatches inhabit the same server that liveness checks and teardown inspect. The server-scoped
`exit-empty` option keeps that namespace alive between the temporary anchor and the first role
session without changing a role pane's own exit semantics.

Both targeted and full quality modes call this same entry point. Targeted mode runs only when the
explicit dependency surface in `selection.py` intersects the candidate diff; full mode always runs.
Every invocation executes two planned fresh replications and zero retries, then writes structured
expected/actual/owner checkpoints below the quality report directory.
