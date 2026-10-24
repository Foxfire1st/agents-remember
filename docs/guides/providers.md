# Providers (Retired)

Agents Remember no longer ships its two optional context providers. Both are
**retired** (MIK-R112): no product path installs, enables, starts, indexes, or
queries them, the MCP tools that queried them left the surface, and the provider
lifecycle CLI is gone. This page is the retirement account: what the two
providers were, what an older installation can still leave behind, and how that
leftover state is reported and cleaned up.

| Provider id | Was | Indexed | State |
| --- | --- | --- | --- |
| `codegraphcontext-code` (CGC) | code-relationship search | your code repository | retired |
| `grepai-memory` (grepai) | semantic-memory search | the memory / onboarding | retired |

CGC answered structural questions (who calls this, what does this depend on);
grepai answered "where is the memory about X" over onboarding content. Neither
answers anything now. Finding knowledge runs on by-path memory, onboarding
notes, and drift checks, with the harness's own search tools for source code.

Durable memory was never theirs: Markdown + Git, drift-checked and
approval-gated, whatever surfaced a candidate. The providers were only ever
accelerators for *finding* knowledge, never the source of truth.

## What remains

Five things still touch the retired providers. None of them revives one.

1. **Cleanup of an older installation, on explicit request.** `runtime_install`
   never deletes provider runtime folders. It reports the leftover folders with
   the size of each tree; deletion belongs to the separate `providers-remove`
   command, and nothing is removed unless you run it:

   ```text
   agents-remember providers-remove --dry-run
   agents-remember providers-remove
   ```

   `--dry-run` reports the folders and their sizes and stops nothing. Without
   it, the command stops the configured watchers first, then removes the
   provider runtime folders, printing each folder's size and the total bytes
   freed. It refuses
   — and removes nothing — when its watcher stop fails, when a live process
   still holds a folder open (it names the pid and command), or when the
   provider root or a deletion target escapes the owned tree. Exit status is
   non-zero on any of those refusals, and `0` when nothing remained or
   everything was removed.

2. **Old settings keys, read and reported once.** The settings `providers`
   block still parses so an older installation keeps loading, but its keys
   never count as enabled and drive nothing. `runtime_install` reports them once
   as unused (`unusedProviderKeys`, mirrored by `server_info`); remove the block
   when the older installation is retired, or omit it entirely for a clean
   installation.

3. **An old watcher, stopped.** `runtime_install` stops leftover provider
   watcher processes; that stop is its only watcher action. A failed stop is
   recorded in the install result and the install continues.

4. **A periodic safety check over leftovers.** The provider-degradation
   detector still samples the central provider metrics log and writes its state
   under `<coordinationRoot>/logs/observer/providers/`. At `critical`, with
   `failSafeEnabled` set, it can degrade and stop a leftover stack. It is a
   safety check, not a revival: it starts nothing, and it is not a dashboard
   provider view — there is no provider panel to read.

5. **Unrelated host configuration.** `.paseoRuntime.providers` in the global
   agentic settings file lists the *model* providers (ACP harnesses) that the
   Paseo host launches. It is live configuration with no relationship to the
   retired search providers, and nothing on this page changes it.

## Requirements, only for leftovers

- **Docker** running on the host, only to inspect or clear leftover provider
  containers from an older installation.
- **Ollama** with a pulled embedding model (`nomic-embed-text`) was grepai's
  embedder, run as a Docker container. Nothing pulls or needs it now; it
  matters only when clearing an old install.

Nothing provider-side ever started without Docker, and grepai indexed nothing
without Ollama. With both providers retired, these requirements describe
leftover state only.

## Troubleshooting leftovers

- **Old provider keys present** — `server_info` reports them once as unused, and
  the install result carries the same list. Remove them from settings when the
  older installation is retired.
- **Leftover watcher processes** — `runtime_install` stops them before refreshing
  the runtime tree; a failed stop is recorded in the install result and the
  install continues.
- **Leftover runtime folders** — `runtime_install` reports them with their sizes.
  `agents-remember providers-remove --dry-run` lists them again and stops
  nothing; the same command without `--dry-run` deletes them, and refuses while
  a live process holds one open or a watcher stop fails.

Memory, onboarding, drift, and task workflows all work without the retired
providers.
