# Benchmark Workspace Instructions

This directory is an isolated memory-enabled benchmark workspace. Treat it as
the Codex project root for this run. Do not read or follow `AGENTS.md` files
from parent directories outside this workspace.
Do not apply unrelated user-level or global documentation lookup rules unless
the benchmark prompt explicitly asks for external SDK/API documentation.

Read and follow `{coordination_root}/AGENTS.md` before working in the benchmark repository.
Treat these rules as workspace instructions for this isolated benchmark only.

Benchmark case: `{case_id}`
Target code repository: `{repository_name}`
Target code checkout: `{repo_relative_path}`
Benchmark coordination root: `{coordination_root}`
Benchmark memory repository: `{memory_repository_name}`

When resolving Agents Remember context for this case, use `{repo_relative_path}` as the code repository root and this workspace's `{coordination_root}/` directory as the coordination root.

Execution discipline:
This is a non-interactive benchmark run. Do not ask the user questions, request
approval, or pause for follow-up. Make reasonable assumptions from the prompt,
source evidence, and memory evidence, then complete the requested task in one
final answer.

@{coordination_root}/AGENTS.md
