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

If Docker is not running, provider install and start will fail; if Ollama is not
reachable, grepai will report `degraded` and index nothing.

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

## Install the runtimes

`runtime_install` builds/pulls the provider images for the enabled providers
(it installs provider dependencies by default):

```text
runtime_install()
```

This builds the images but does **not** start indexing on its own.

## Start indexing and verify

Start the watchers so the providers index the configured repos and memory, then
confirm they are healthy:

```text
provider_watchers(action="start")   # start watchers
provider_watchers(action="refresh")  # re-seed after repo/memory changes
provider_status()                                   # compact readiness summary
provider_diagnostics()                              # raw provider-native detail
```

`provider_watchers` actions: `status`, `start`, `stop`, `restart`, `refresh`,
`shutdown-all`. `context_packet(repo_id=..., include_providers=true)` also
reports per-provider readiness, watcher state, and the target repo.

The `c-13-install-and-onboard` skill performs this start/verify step
(stage 4) as part of first-run setup.

## Use them

Once indexing, the search tools are available:

- **grepai** — `grepai_search` (semantic search), `grepai_trace` (relationship trace).
- **CGC** — `cgc_symbol_search`, `cgc_callers`, `cgc_callees`, `cgc_dependencies`,
  `cgc_complexity`, `cgc_visualize`.

See the [MCP tool reference](../reference/mcp-tools.md) for arguments.

## Troubleshooting

- **Provider `degraded` / indexes nothing** — run `provider_diagnostics()` for the
  exact gap. Usual causes: Docker not running, or (grepai) Ollama/model not
  available.
- **"run runtime_install before provider operations"** — the provider runner
  integrity check failed; run `runtime_install()` first.
- **Changed repo or memory not reflected** — `provider_watchers(action="refresh")`.

Providers are optional: Agents Remember's memory, onboarding, drift, and task
workflows all work without them. They add faster code/memory search on top.
