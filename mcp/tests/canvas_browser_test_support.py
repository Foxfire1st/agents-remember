"""Shared Playwright module and Chromium browser-path discovery for canvas browser tests."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest
from agents_remember.canvas.explainer_browser import home as browser_home

__all__ = [
    "DASHBOARD_PLAYWRIGHT",
    "REPO_ROOT",
    "configure_browser_env",
    "resolve_browsers_path",
    "resolve_playwright",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_PLAYWRIGHT = REPO_ROOT / "dashboard" / "node_modules" / "playwright" / "index.js"


def resolve_playwright() -> Path | None:
    """Locate the installed Playwright module from env, explainer home, or dashboard."""

    env_path = (os.environ.get("AR_PLAYWRIGHT") or "").strip()
    if env_path:
        candidate = Path(env_path)
        return candidate if candidate.is_file() else None
    product_home = browser_home()
    if product_home.installed():
        return product_home.module
    if DASHBOARD_PLAYWRIGHT.is_file():
        return DASHBOARD_PLAYWRIGHT
    return None


def resolve_browsers_path() -> Path | None:
    """Locate the Playwright Chromium browsers cache directory."""

    env_path = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if env_path:
        candidate = Path(env_path)
        return candidate if candidate.is_dir() else None
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    for candidate in (
        real_home / ".local" / "share" / "agents-remember" / "explainer-browser" / "browsers",
        real_home / ".cache" / "ms-playwright",
    ):
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate
    return None


def configure_browser_env(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Configure AR_PLAYWRIGHT and PLAYWRIGHT_BROWSERS_PATH or skip when unavailable."""

    playwright_module = resolve_playwright()
    if playwright_module is None:
        pytest.skip("Playwright module is not installed in this environment")
    monkeypatch.setenv("AR_PLAYWRIGHT", playwright_module.as_posix())
    browsers_path = resolve_browsers_path()
    if browsers_path is not None:
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", browsers_path.as_posix())
    return playwright_module
