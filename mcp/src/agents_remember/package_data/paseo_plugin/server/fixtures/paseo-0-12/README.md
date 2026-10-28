# A synthetic Paseo 0.12.0 installation

This tree is a stand-in, written by hand for `server/acp_providers.test.ts`. It is
**not copied source**: it carries only the shapes MIK-R216 rule 1 reads from the
installed `@getpaseo` packages, at the paths the discovery expects.

```
@getpaseo/
  server/package.json                     the version the file publishes
  server/dist/server/server/plugins/
    plugin-process.js                     the argv1 path the plugin's own process names
  server/dist/server/server/agent/
    provider-registry.js                  the class imports and PROVIDER_CLIENT_FACTORIES
    providers/                            one file per client class, and its base classes
      acp-agent.js                        declares ACPAgentClient, the ACP base
      generic-acp-agent.js                extends ACPAgentClient
      copilot-acp-agent.js                extends ACPAgentClient
      cursor-acp-agent.js                 extends GenericACPAgentClient
      kimi-, kiro-, trae- acp-agent.js    extend GenericACPAgentClient
      claude/agent.js, codex-…, opencode/…, pi/…, omp/…
                                          no base class: not on an ACP connection
      mock-load-test-agent.js, mock-slow-provider.js
                                          the dummy factories, keyed `mock` and `mock-slow`
  protocol/dist/provider-manifest.js      AGENT_PROVIDER_DEFINITIONS, each with nested modes
```

The manifest's definitions carry `modes` arrays whose members have `id`s of their
own (`default`, `auto`, `auto-review`, `full`, `build` and a session-mode URL).
Those are not provider ids, and the discovery's depth scan must not take them for
any; that is what the fixture is shaped to prove.
