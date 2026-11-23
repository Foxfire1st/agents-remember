# The pinned Paseo 0.16.2 installation the ACP discovery reads

This tree is **copied source**, not a stand-in: every file under `@getpaseo/` is a byte-for-byte copy
of the corresponding file in an installed `@getpaseo` distribution at the version this build pins
(`paseo_host/contract.json`). `fixture.json` records where each file came from, its SHA-256 and its
size.

- **Source**: `@getpaseo/server`, `@getpaseo/protocol`, `@getpaseo/plugin` and `@getpaseo/cli` at
  `0.16.2`, from an installation of that distribution. The canonical prefix this build installs into
  is `~/.local/share/agents-remember/paseo/prefix/node_modules/@getpaseo`; `fixture.json`'s
  `source.installedAt` names the tree this copy was taken from and `source.canonicalPrefix` the
  default the renewal reads when no path is given.
- **Renewal**: `node server/fixtures/renew-paseo-fixture.mjs [<installed @getpaseo dir>]` rewrites the
  tree and `fixture.json`; `--check` verifies both without writing, and fails when any file differs
  from its source or when the record does not describe the tree. The script refuses a distribution
  whose four package versions are not the pinned one.
- **Owner**: the host-contract route of this plugin (`package_data/paseo_host/contract.json`).
- **Test**: `server/acp_providers.test.ts` (it also asserts `fixture.json` against the files, so a
  hand edit cannot pass).
- **Consumers**: `server/acp_providers.test.ts` only.

```
@getpaseo/
  server/package.json                     the version the ACP providers file publishes
  server/dist/server/server/plugins/
    plugin-process.js                     the argv1 path the plugin's own process names
  server/dist/server/server/agent/
    provider-registry.js                  the class imports and PROVIDER_CLIENT_FACTORIES
    create-agent-mode.js                  the mode vocabulary the registry names
    providers/                            one file per client class, and its base classes
      acp-agent.js                        declares ACPAgentClient, the ACP base
      generic-acp-agent.js                extends ACPAgentClient
      copilot-acp-agent.js                extends ACPAgentClient
      cursor-acp-agent.js                 extends GenericACPAgentClient
      kimi-, kiro-, trae-acp-agent.js     extend GenericACPAgentClient
      claude/agent.js, codex-app-server-agent.js, opencode/runtime-client.js,
      pi/agent.js, omp/agent.js           no base class: not on an ACP connection
      mock-load-test-agent.js, mock-slow-provider.js
                                          the dummy factories, keyed `mock` and `mock-slow`
  protocol/dist/provider-manifest.js      AGENT_PROVIDER_DEFINITIONS, each with nested modes
```

The manifest's definitions carry `modes` arrays whose members have `id`s of their own (`default`,
`auto`, `auto-review`, `full`, `build` and a session-mode URL). Those are not provider ids, and the
discovery's depth scan must not take them for any; that is what this tree is read to prove.
