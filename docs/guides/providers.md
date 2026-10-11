# Providers

Agents Remember ships two optional context providers. They are opt-in: enable
them in the MCP settings, and they run as Docker services.

| Provider id | Capability | What it indexes |
| --- | --- | --- |
| `codegraphcontext-code` (CGC) | code-relationship search | your code repository |
| `grepai-memory` (grepai) | semantic-memory search | the memory / onboarding |

CGC answers structural questions (who calls this, what does this depend on);
grepai answers "where is the memory about X" over onboarding content.

## Requirements

- **Docker** running on the host (both providers run as containers).
- **Ollama** with a pulled embedding model for `grepai-memory` (grepai's embedder
  runs against Ollama). CGC does not need Ollama.

If Docker is not running, nothing provider-side starts; if Ollama is not
reachable, grepai indexes nothing. Operation is retired (see below); these
requirements matter only for leftover processes from older installations.

## Enable

Add the providers you want to the MCP settings `providers` block:

```json
{
  "providers": {
    "codegraphcontext-code": {},
    "grepai-memory": {}
  }
}
```

Omit a provider (or the whole block) to run without it. See the
[settings reference](../reference/settings-json.md) for the full shape.

## Operation retired (MIK-R112)

The provider tools left the surface: no product path installs, starts, or
watches the two providers, and the provider lifecycle CLI is removed.
`runtime_install` stops leftover watcher processes as its only watcher
action and reports enabled provider keys once as unused; verify with
`server_info`:

```text
server_info()
```

The settings `providers` block still parses so older installations keep
loading, but enabled keys drive nothing. Keep the block only to document
which keys an older installation carried; omit it (or the whole block)
for a clean installation. If Docker is not running, nothing provider-side
starts; grepai simply indexes nothing.

## Troubleshooting

- **Old provider keys present** — `server_info` reports them once as
  unused, and the install result carries the same list. Remove them from
  settings when the older installation is retired.
- **Leftover watcher processes** — `runtime_install` stops them before
  refreshing the runtime tree; a failed stop is recorded in the install
  result and the install continues.
- **\"run runtime_install before provider operations\"** — the provider
  runner integrity check failed; run `runtime_install()` first.

Providers are optional: Agents Remember's memory, onboarding, drift, and task
workflows all work without them. They add faster code/memory search on top.
