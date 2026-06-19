# Benchmark Cases

Each case folder contains the manifest, prompts, and author-provided result reports for one benchmark target.

```text
cases/<case-id>/
  case.json
  prompts/
  author-results/
```

The mature memory repo for a case is not stored here. The case manifest pins its URL and commit, and `prepare` clones it into the matching generated workspace:

```text
workspaces/<case-id>/with-memory/ar-coordination/memory-repos/ar-<repo>/
```

The workspace itself is not committed. `prepare` creates it from the manifest plus templates, then creates one source-only environment and one memory-enabled environment. The source-only environment has a harness `AGENTS.md`, a benchmark root marker, and a pinned code checkout. The memory-enabled environment has the same pinned code checkout, the memory repo path C-08 uses during replay, a copied workspace-local `.codex/skills/agents-remember` exposure for harness skill discovery, and a `.codex/config.toml` MCP registration for provider access. `run` forwards that MCP registration to `codex exec` as `-c mcp_servers...` overrides. Use `--skill-exposure-mode none` only when skill discovery is handled outside the benchmark harness.
