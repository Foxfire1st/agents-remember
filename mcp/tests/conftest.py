"""Certifying pytest composition: Dagger admission, then reusable bootstrap."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# This stdlib-only pin must precede the first production import. It prevents an editable-install
# ``.pth`` entry from validating one checkout and collecting another.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MCP_SRC = REPOSITORY_ROOT / "mcp" / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.testing.certifying_bootstrap import (
    CertifyingPytestBootstrap,
)
from agents_remember.testing.certifying_bootstrap import (
    prepare_certifying_pytest_bootstrap as _prepare_certifying_pytest_bootstrap,
)
from agents_remember.testing.dagger_admission import DaggerAdmissionError
from agents_remember.testing.global_state import begin_pytest_process
from agents_remember.testing.hermetic_bootstrap import (
    BootstrapConfigurationError,
    EnvironmentLease,
    activate_current_pytest_environment,
)


def prepare_certifying_pytest_bootstrap() -> CertifyingPytestBootstrap:
    """Refuse before plugin loading, collection, execution, or artifact publication."""

    try:
        return _prepare_certifying_pytest_bootstrap(REPOSITORY_ROOT)
    except (DaggerAdmissionError, BootstrapConfigurationError) as error:
        raise pytest.UsageError(str(error)) from error


CERTIFYING_BOOTSTRAP = prepare_certifying_pytest_bootstrap()
_ENVIRONMENT_LEASE: EnvironmentLease = activate_current_pytest_environment(
    CERTIFYING_BOOTSTRAP.process,
    os.environ,
)
begin_pytest_process()

# Pytest imports this only after the module-level admission above succeeds. The diagnostic route
# loads ``agents_remember.testing.pytest_bootstrap`` directly and therefore never imports this
# certifying service composition.
pytest_plugins = ("agents_remember.testing.pytest_certifying_bootstrap",)


def pytest_unconfigure(config: pytest.Config) -> None:
    del config
    _ENVIRONMENT_LEASE.close()
