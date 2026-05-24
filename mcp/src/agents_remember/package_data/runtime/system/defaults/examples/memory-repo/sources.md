# Sources Example

Copy or rename this file to the memory layer's `system/sources.md` when scaffolding a memory repo.

Use this file as the starter template for the active sources inventory. The derived path locations are documented in the same memory layer's `system/settings.md`.

## Task Sources (e.g. Github/Gitlab, MCPs, etc.)

- **<ticket-system>** — the source of truth for task requirements, constraints, and implementation details.

## Domain Documentation

- **<primary-domain-docs>** — the authoritative online/intranet source of technical domain knowledge, architecture docs, protocol definitions, and design decisions. Name the retrieval tool or MCP agents should use when the docs need to be searched live.
- **<local-mirror-if-any>** — local exports or cached copies of useful domain docs when they exist. Treat local mirrors as orientation caches only: they are useful for quick reading and line discovery, but are not complete or authoritative. If the local mirror has no relevant page, appears stale, or lacks enough evidence, agents must use the live retrieval path named above before recording that no domain documentation exists. By default, local domain documentation lives under the resolved memory layer's `docs/`.

## Techstack Documentation

- **<docs-mcps-etc>** — the main source for external documentation, languages, library references, and API specs.

## Database Schema

- `<where the runtime schema lives>`
