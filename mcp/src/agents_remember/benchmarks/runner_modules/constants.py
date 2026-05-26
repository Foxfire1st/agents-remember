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

TOKEN_KEYS = {
    "input_tokens": "input_tokens",
    "inputTokens": "input_tokens",
    "total_input_tokens": "input_tokens",
    "totalInputTokens": "input_tokens",
    "fresh_input_tokens": "fresh_input_tokens",
    "freshInputTokens": "fresh_input_tokens",
    "output_tokens": "output_tokens",
    "outputTokens": "output_tokens",
    "total_output_tokens": "output_tokens",
    "totalOutputTokens": "output_tokens",
    "reasoning_tokens": "reasoning_tokens",
    "reasoningTokens": "reasoning_tokens",
}

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
CODEX_BENCHMARK_SANDBOX = CODEX_SANDBOX_DANGER_FULL_ACCESS
CODEX_BENCHMARK_SANDBOX_MODES = (CODEX_SANDBOX_DANGER_FULL_ACCESS, CODEX_SANDBOX_DEFAULT)
CODEX_BENCHMARK_SCOPE = "codex-benchmark-only"
BENCHMARK_MCP_SERVER_NAME = "agents_remember_benchmark"
BENCHMARK_MCP_SETTINGS_NAME = "agents-remember-benchmark.settings.json"
BENCHMARK_MCP_STARTUP_TIMEOUT_SECONDS = 120
BENCHMARK_MCP_SOURCE_ENV = "AGENTS_REMEMBER_BENCHMARK_MCP_SRC"
