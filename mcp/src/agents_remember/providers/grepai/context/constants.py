"""Docker-owned GrepAI provider constants."""

from __future__ import annotations

GREPAI_PROVIDER = "grepai"
GREPAI_PIN = "grepai==0.35.0"
GREPAI_NETWORK_NAME = "ar-grepai-memory"
GREPAI_RUNNER_CONTAINER_NAME = "ar-grepai-watcher"
GREPAI_RUNNER_IMAGE_REPOSITORY = "agents-remember/grepai"
GREPAI_POSTGRES_BACKEND_ID = "grepai-postgres"
GREPAI_POSTGRES_CONTAINER_NAME = "ar-grepai-postgres"
GREPAI_POSTGRES_DEFAULT_HOST = "127.0.0.1"
GREPAI_POSTGRES_DEFAULT_PORT = "5432"
GREPAI_OLLAMA_CONTAINER_NAME = "ar-grepai-ollama"
GREPAI_OLLAMA_IMAGE = "ollama/ollama:latest"
GREPAI_OLLAMA_DEFAULT_HOST = "127.0.0.1"
GREPAI_OLLAMA_DEFAULT_PORT = "11434"
