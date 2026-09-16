# Agents Remember eve runtime

One pinned, AR-owned [eve](https://github.com/vercel/eve) application. AR's native eve
adapter (`mcp/src/agents_remember/serving/eve_adapter.py`) launches this application and
controls it exclusively through eve's documented HTTP session protocol — health, session
create, follow-up message, `inputResponses`, turn cancel, and the durable NDJSON event
stream. Nothing here replaces or reimplements eve's tool loop, compaction engine or
session store; the runtime is the unmodified published `eve` package.

## Pinned dependency

`package.json` pins exact versions (no ranges):

| Package                 | Version   |
| ----------------------- | --------- |
| `eve`                   | `0.56.0`  |
| `ai`                    | `7.0.102` |
| `@ai-sdk/openai-compatible` | `3.0.49` |
| `zod`                   | `4.6.5`   |

`node_modules/` is not committed. Install it once per checkout:

```bash
cd eve_runtime
PATH=<node 24 bin>:$PATH npm install --no-audit --no-fund
```

Node 24 or newer is required by eve's own `engines` field. The adapter resolves one itself: it
prefers the newest nvm runtime under `$HOME/.nvm`, then the first `node` on `PATH`, and refuses
with the versions it tried rather than starting eve under an unsupported runtime. Set
`AR_EVE_NODE` to name one explicitly.

## Environment contract

The adapter owns every value below and passes it through the launch environment. Nothing is
read from the user's shell.

| Variable                  | Meaning                                                                 |
| ------------------------- | ----------------------------------------------------------------------- |
| `AR_EVE_RUNTIME_ROOT`     | Application root (set by the adapter to its own resolved runtime path).  |
| `AR_EVE_MODEL`            | Native model id the running session selects; echoed as launch evidence.  |
| `AR_EVE_EFFORT`           | Native reasoning effort, or `provider-default`.                          |
| `AR_EVE_PROVIDER_BASE_URL`| OpenAI-compatible provider base URL for the selected model.              |
| `AR_EVE_PROVIDER_API_KEY` | Credential for that provider.                                            |
| `AR_EVE_PROVIDER_NAME`    | Provider name registered with the AI SDK.                                |
| `AR_EVE_NODE`             | Node executable to start the application with (else nvm, then `PATH`).    |
| `AR_EVE_STATE_ROOT`       | Where each epoch's staged application directory is created.              |
| `AR_EVE_CONTEXT_WINDOW_TOKENS` | Declared context window for a provider absent from the AI Gateway catalog. |
| `AR_WORKSPACE_ROOT`       | The admitted worktree this run's tools operate on.                       |
| `AR_BINDING_REF`          | Server-authenticated AR binding reference applied before the first model call. |
| `AR_CAPSULE_DIGEST`       | Digest of the frozen capsule the binding names.                          |

`AR_WORKSPACE_ROOT` and `AR_BINDING_REF` are the seam the capsule/workspace leaf plugs into.
This application consumes them; it does not compile or select a capsule.

## Layout

```text
eve_runtime/
├── package.json
├── package-lock.json             # committed lockfile: exact transitive pins for the four above
├── README.md
└── agent/
    ├── agent.ts                  # pinned model + explicit context-window override
    ├── instructions.md           # stable runtime identity and standing rules
    ├── instructions/
    │   └── ar-binding.ts         # dynamic, pre-model AR binding application
    ├── channels/
    │   └── eve.ts                # queue-turn policy + local-development auth
    ├── lib/
    │   └── workspace.ts          # confines a tool path to the admitted worktree
    └── tools/
        ├── ar_workspace_read.ts  # read a file inside the admitted worktree
        └── ar_workspace_write.ts # write a file inside the admitted worktree
```

`node_modules/` and the generated `.eve/` and `.output/` trees are machine-local and gitignored;
nothing else in this directory is generated.

The launcher is not a script in this tree: `serving/eve_runtime_client.py` starts the application
(`node_modules/eve/bin/eve.js dev --no-ui --host … --port …`) and speaks its HTTP session API, and
`mcp/tests/live_eve_native_fixture.py` is the executable native fixture that drives it end to end.

## Running it by hand

```bash
cd eve_runtime
PATH=<node 24 bin>:$PATH AR_EVE_MODEL=<provider>/<model> npm run dev -- --port 2000
```

`GET /eve/v1/health` answers `{"ok":true,"status":"ready","workflowId":...}` once the
application is serving. The adapter polls exactly that route before it reports a
ready control state.
