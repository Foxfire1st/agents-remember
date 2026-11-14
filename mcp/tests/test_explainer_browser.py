"""The browser the explainer publishes with: where it lives, what fetches it, and what refuses.

The load of a prospective revision imports Playwright and launches its Chromium, and neither ships
with the package. Each case here protects one operation of the one module that owns them
(``canvas/explainer_browser.py``):

1. the module and its browser live in the product's own data folder, named by the two variables the
   load reads, and a module the user named keeps both to itself;
2. the load runs on the Node the product installs, and on ``node`` only where that Node is absent;
3. a missing module and browser are fetched once, at the pinned version, with the product's npm,
   into that folder -- and the second explanation fetches nothing;
4. a fetch that fails refuses with what is missing, where it goes and the command that gets it, and
   leaves no half-installed folder behind;
5. a module the user named that is absent refuses before anything is fetched, and a system with no
   product Node is refused as such;
6. the pinned version is the checkout's own pin, so a release cannot fetch a Playwright the
   checkout's browser cannot install.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from agents_remember.canvas import explainer_browser
from agents_remember.canvas.explainer_browser import (
    BROWSER_NOTICE,
    BROWSERS_PATH_ENV,
    PLAYWRIGHT_ENV,
    PLAYWRIGHT_VERSION,
    CommandResult,
    browser_environment,
    browser_missing,
    browser_notice,
    ensure,
    home,
    node_executable,
)
from agents_remember.canvas.explainer_errors import ExplainerRefused
from agents_remember.cli import explainer_browser as cli
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_node_paths import NodeRuntime

STAGING = ".explainer-browser.staging"


def _product_node(tmp_path: Path) -> NodeRuntime:
    """A product Node whose executables exist, so the fetch may use it."""

    root = tmp_path / "data" / "agents-remember/node/node-v26-linux-x64"
    runtime = NodeRuntime(root=root, cache=tmp_path / "cache", url="", sha256="")
    runtime.node.parent.mkdir(parents=True, exist_ok=True)
    runtime.node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runtime.npm.parent.mkdir(parents=True, exist_ok=True)
    runtime.npm.write_text("// npm-cli.js\n", encoding="utf-8")
    return runtime


def _no_product_node() -> NodeRuntime:
    raise PaseoRuntimeFailure(
        "node_platform_unsupported", "node", "Node 26.11.1 for the host supports Linux x86_64"
    )


class _Fetcher:
    """A stand-in for the two commands a fetch runs, writing what npm and Playwright would."""

    def __init__(self, *, failure: str = "", browsers: bool = True, silent: bool = False) -> None:
        self.calls: list[tuple[list[str], float, Mapping[str, str] | None]] = []
        self._failure = failure
        self._browsers = browsers
        self._silent = silent

    def __call__(
        self,
        argv: Sequence[str],
        timeout_seconds: float,
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        command = [str(part) for part in argv]
        self.calls.append((command, timeout_seconds, environment))
        if self._failure:
            return CommandResult(
                1,
                "",
                "npm error code EAI_AGAIN\n"
                f"npm error request failed: {self._failure}\n"
                "npm error A complete log of this run can be found in: /tmp/npm-debug.log",
            )
        if "install" in command and "--prefix" in command:
            package = Path(command[command.index("--prefix") + 1]) / "node_modules/playwright"
            package.mkdir(parents=True, exist_ok=True)
            (package / "index.js").write_text("// playwright\n", encoding="utf-8")
            (package / "cli.js").write_text("// cli\n", encoding="utf-8")
            return CommandResult(0, "added 3 packages", "")
        if command[-2:] == ["install", "chromium"]:
            if self._silent:
                return CommandResult(0, "no browser written", "")
            if self._browsers:
                browsers = Path((environment or {})[BROWSERS_PATH_ENV])
                (browsers / "chromium-1254").mkdir(parents=True, exist_ok=True)
                (browsers / "chromium_headless_shell-1254").mkdir(parents=True, exist_ok=True)
                return CommandResult(0, "chromium downloaded", "")
        return CommandResult(1, "", "the fake fetch does not know this command")


@pytest.fixture
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A user's data home with no browser folder in it yet, and no module of their own named."""

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", data.as_posix())
    monkeypatch.delenv(PLAYWRIGHT_ENV, raising=False)
    monkeypatch.delenv(BROWSERS_PATH_ENV, raising=False)
    return data


def test_the_browser_lives_in_the_product_folder_and_is_named_by_the_two_variables(
    data_home: Path,
) -> None:
    """Fact 1: one product-owned folder, and the two names the load reads point into it."""

    folder = home()
    assert folder.root == data_home / "agents-remember/explainer-browser"
    assert folder.module == folder.root / "node_modules/playwright/index.js"
    assert folder.browsers == folder.root / "browsers"
    # Nothing fetched yet: the load is handed no module of this installation's, and the missing
    # part is named rather than the load silently importing the bare ``playwright`` specifier.
    assert browser_environment() == {}
    assert browser_missing() == (f"Playwright {PLAYWRIGHT_VERSION} at {folder.root}",)

    folder.module.parent.mkdir(parents=True)
    folder.module.write_text("// playwright\n", encoding="utf-8")
    (folder.browsers / "chromium-1254").mkdir(parents=True)
    assert browser_environment() == {
        PLAYWRIGHT_ENV: folder.module.as_posix(),
        BROWSERS_PATH_ENV: folder.browsers.as_posix(),
    }
    assert browser_missing() == ()


def test_a_module_the_user_names_keeps_its_own_browser_folder(
    data_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fact 1: ``AR_PLAYWRIGHT`` keeps working, and the product redirects nothing behind it."""

    named = tmp_path / "user/node_modules/playwright/index.js"
    named.parent.mkdir(parents=True)
    named.write_text("// playwright\n", encoding="utf-8")
    monkeypatch.setenv(PLAYWRIGHT_ENV, named.as_posix())

    assert browser_environment() == {}
    assert browser_missing() == ()


def test_the_load_runs_on_the_product_node_when_it_is_installed(
    data_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fact 2: the Node of the product's own is what the browser runs on, never the user's first."""

    runtime = _product_node(tmp_path)
    monkeypatch.setattr(explainer_browser, "product_node", lambda: runtime)
    assert node_executable() == runtime.node.as_posix()

    monkeypatch.setattr(explainer_browser, "product_node", _no_product_node)
    assert node_executable() == "node"


def test_a_missing_browser_is_fetched_once_at_the_pin_into_the_product_folder(
    data_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fact 3: one fetch, with the product's npm, at the pinned version, into its own folder."""

    runtime = _product_node(tmp_path)
    monkeypatch.setattr(explainer_browser, "product_node", lambda: runtime)
    fetcher = _Fetcher()
    ready = ensure(runner=fetcher)

    assert ready.state == "fetched"
    assert ready.module.is_file() and ready.browsers.is_dir()
    record = ready.record()
    assert record["playwright"] == PLAYWRIGHT_VERSION
    assert str(record["detail"]).startswith(
        f"Playwright {PLAYWRIGHT_VERSION} and its Chromium were missing"
    )
    staging = home().root.with_name(STAGING)
    module_command, module_budget, module_environment = fetcher.calls[0]
    assert module_command[:2] == [runtime.node.as_posix(), runtime.npm.as_posix()]
    assert module_command[module_command.index("--prefix") + 1] == staging.as_posix()
    assert f"playwright@{PLAYWRIGHT_VERSION}" in module_command
    assert module_environment is None and module_budget == explainer_browser.INSTALL_SECONDS
    browser_command, browser_budget, browser_environment_given = fetcher.calls[1]
    assert browser_command[1] == (staging / "node_modules/playwright/cli.js").as_posix()
    assert browser_command[-2:] == ["install", "chromium"]
    assert browser_budget == explainer_browser.BROWSER_SECONDS
    assert browser_environment_given == {BROWSERS_PATH_ENV: (staging / "browsers").as_posix()}

    again = ensure(runner=fetcher)
    assert again.state == "ready"
    assert len(fetcher.calls) == 2, "the second explanation fetched nothing"


def test_a_failed_fetch_refuses_with_what_is_missing_and_the_command_that_gets_it(
    data_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fact 4: a fetch that fails names the part, the folder and the command, and installs nothing."""

    monkeypatch.setattr(explainer_browser, "product_node", lambda: _product_node(tmp_path))
    with pytest.raises(ExplainerRefused) as refused:
        ensure(runner=_Fetcher(failure="EAI_AGAIN registry.npmjs.org"))

    assert refused.value.reason == "browser-fetch-failed"
    assert "npm error code EAI_AGAIN" in refused.value.detail
    assert "npm-debug.log" not in refused.value.detail
    assert f"Playwright {PLAYWRIGHT_VERSION}" in refused.value.detail
    assert home().root.as_posix() in refused.value.detail
    assert "agents-remember explainer-browser --fetch" in refused.value.detail
    assert not home().root.exists()
    assert not home().root.with_name(STAGING).exists()


def test_a_fetch_that_installs_no_browser_is_not_a_ready_browser(
    data_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fact 4: the module alone is not the browser; a fetch that stops short refuses and leaves nothing."""

    monkeypatch.setattr(explainer_browser, "product_node", lambda: _product_node(tmp_path))
    with pytest.raises(ExplainerRefused) as refused:
        ensure(runner=_Fetcher(silent=True))

    assert "the module or its Chromium is not where it belongs" in refused.value.detail
    assert not home().root.exists()


def test_a_module_that_is_named_and_absent_refuses_before_anything_is_fetched(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fact 5: the user's word is answered, not overruled by a fetch of the product's own."""

    fetcher = _Fetcher()
    monkeypatch.setenv(PLAYWRIGHT_ENV, "/opt/mod/playwright/index.js")
    with pytest.raises(ExplainerRefused) as refused:
        ensure(runner=fetcher)

    assert refused.value.reason == "browser-missing"
    assert "/opt/mod/playwright/index.js" in refused.value.detail
    assert fetcher.calls == []


def test_a_system_the_product_cannot_install_a_node_for_refuses_with_that_reason(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fact 5: where no product Node can exist, the refusal says so instead of fetching half of it."""

    monkeypatch.setattr(explainer_browser, "product_node", _no_product_node)
    with pytest.raises(ExplainerRefused) as refused:
        ensure(runner=_Fetcher())

    assert refused.value.reason == "browser-fetch-refused"
    assert "Linux x86_64" in refused.value.detail
    assert "macOS" in refused.value.detail


def test_the_fetched_version_is_the_checkout_pin() -> None:
    """Fact 6: one version, in the module and in the dashboard lock; a drift fails here."""

    lock = Path(__file__).resolve().parents[2] / "dashboard/package-lock.json"
    packages = json.loads(lock.read_text(encoding="utf-8"))["packages"]
    assert packages["node_modules/playwright"]["version"] == PLAYWRIGHT_VERSION
    assert packages["node_modules/@playwright/test"]["version"] == PLAYWRIGHT_VERSION


def test_the_first_page_is_announced_before_the_fetch_begins(data_home: Path) -> None:
    """Rule 1's "while": the sentence is ready before the download, and gone once the browser is here."""

    notice = browser_notice()
    assert notice == BROWSER_NOTICE
    assert "once" in notice, "the user is told the fetch happens once"
    assert "minute" in notice, "the user is told how long the first page takes"

    folder = home()
    folder.module.parent.mkdir(parents=True)
    folder.module.write_text("// playwright\n", encoding="utf-8")
    (folder.browsers / "chromium-1254").mkdir(parents=True)
    assert browser_notice() is None, "a page that fetches nothing says nothing"


def test_the_command_the_seat_runs_before_its_first_render_carries_the_sentence(
    data_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rule 1: the check reports the words to say, so the seat says the product's sentence, not its own."""

    exit_code = cli.run(argparse.Namespace(fetch=False))
    document = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert document["missing"], "a browser that is not here is what the seat learns"
    assert document["tellTheUser"] == BROWSER_NOTICE
