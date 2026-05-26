#!/usr/bin/env python3
"""Compatibility facade for Agents Remember benchmark prepare/run/analyze tools."""

from __future__ import annotations

import shutil  # noqa: F401 - compatibility surface for existing benchmark tests.
import subprocess  # noqa: F401 - compatibility surface for existing benchmark tests.
import sys

from agents_remember.benchmarks.runner_modules import workspace as _workspace
from agents_remember.benchmarks.runner_modules.analysis import *  # noqa: F403
from agents_remember.benchmarks.runner_modules.cli import *  # noqa: F403
from agents_remember.benchmarks.runner_modules.cli import main
from agents_remember.benchmarks.runner_modules.commands import *  # noqa: F403
from agents_remember.benchmarks.runner_modules.commands import repo_has_commit, run_command
from agents_remember.benchmarks.runner_modules.constants import *  # noqa: F403
from agents_remember.benchmarks.runner_modules.execution import *  # noqa: F403
from agents_remember.benchmarks.runner_modules.filesystem import *  # noqa: F403
from agents_remember.benchmarks.runner_modules.filesystem import remove_path
from agents_remember.benchmarks.runner_modules.manifest import *  # noqa: F403
from agents_remember.benchmarks.runner_modules.mcp_registration import *  # noqa: F403
from agents_remember.benchmarks.runner_modules.models import *  # noqa: F403
from agents_remember.benchmarks.runner_modules.roots import *  # noqa: F403
from agents_remember.benchmarks.runner_modules.services import *  # noqa: F403
from agents_remember.providers import provider_setup  # noqa: F401


def prepare_repo(repository, repo_root, dry_run, force_clone=False):
    """Prepare a benchmark repository while preserving facade monkeypatch hooks."""
    _workspace.run_command = run_command
    _workspace.remove_path = remove_path
    _workspace.repo_has_commit = repo_has_commit
    return _workspace.prepare_repo(repository, repo_root, dry_run, force_clone=force_clone)


if __name__ == "__main__":
    sys.exit(main())
