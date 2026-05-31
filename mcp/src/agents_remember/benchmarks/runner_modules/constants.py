from __future__ import annotations

import shutil
from pathlib import Path

AGENTS_MD_TARGETS = {
    Path("runtime/agents-md-files/coordinator/AGENTS.md"): Path("AGENTS.md"),
    Path("runtime/agents-md-files/system/AGENTS.md"): Path("system/AGENTS.md"),
    Path("runtime/agents-md-files/skills/AGENTS.md"): Path("skills/AGENTS.md"),
    Path("runtime/agents-md-files/tasks/AGENTS.md"): Path("tasks/AGENTS.md"),
}
PROVIDER_ASSET_DIRS = (Path("requirements"), Path("patches"))
BENCHMARK_PROVIDER_IDS = ("grepai-memory", "codegraphcontext-code")

# Token fields Codex reports in the single `turn.completed` event's `usage`
# object. They are cumulative totals for the turn; runs sum them across turns.
USAGE_TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)

WORKSPACE_AGENTS_TEMPLATE = Path("templates/workspace-AGENTS.md")
SOURCE_ONLY_AGENTS_TEMPLATE = Path("templates/source-only-AGENTS.md")
BENCHMARK_ROOT_MARKER = ".benchmark-root"
SKILLS_EXPOSURE_NAMESPACE = "agents-remember-md"
CODEX_HARNESS_DIR = ".codex"
COPYTREE_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
SKILL_EXPOSURE_MODES = ("copy", "none")
CODEX_EXECUTABLE_NAME = "codex"
CODEX_EXECUTABLE_RESOLUTION = "PATH"
CODEX_SANDBOX_DANGER_FULL_ACCESS = "danger-full-access"
CODEX_SANDBOX_DEFAULT = "default"
# Secure-by-default: the public codex_benchmark_run tool defaults to Codex's own
# sandbox. Operators who need full host access must opt in explicitly with
# codex_sandbox="danger-full-access".
CODEX_BENCHMARK_SANDBOX = CODEX_SANDBOX_DEFAULT
CODEX_BENCHMARK_SANDBOX_MODES = (CODEX_SANDBOX_DANGER_FULL_ACCESS, CODEX_SANDBOX_DEFAULT)
CODEX_BENCHMARK_SCOPE = "codex-benchmark-only"
BENCHMARK_MCP_SERVER_NAME = "agents_remember_benchmark"
BENCHMARK_MCP_SETTINGS_NAME = "agents-remember-benchmark.settings.json"
BENCHMARK_MCP_STARTUP_TIMEOUT_SECONDS = 120
BENCHMARK_MCP_SOURCE_ENV = "AGENTS_REMEMBER_BENCHMARK_MCP_SRC"
