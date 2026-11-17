"""The browser the explainer publishes with: fetched at the first explanation, never installed.

The load of a prospective revision runs the packaged node scripts, which import Playwright and
launch its Chromium. Neither ships with the package: Playwright is a Node module, not a Python
distribution, so an installation from the package has no module to import, and a machine need not
have a Node of its own. This module is the one owner of where that module and its browser live and
of how they get there.

Three facts shape it:

- The product already installs a Node of its own for the host, under the user's data folder, and no
  explainer can start without that host. The fetch therefore runs the product's Node and npm; it
  never looks for a Node of the user's own.
- What it fetches lives in the product's own data folder, not in the user's Playwright cache: one
  folder that the product owns, names in its answers and can remove again. The two names the load
  needs are ``AR_PLAYWRIGHT`` (the module's ``index.js``) and ``PLAYWRIGHT_BROWSERS_PATH`` (that
  folder's browser builds).
- A user who sets ``AR_PLAYWRIGHT`` keeps working exactly as before: their module wins, and nothing
  is fetched or replaced behind it.

Nothing here runs at installation. :func:`ensure` is called at the one place that needs the browser,
the explainer's ``render``, and only when something is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.canvas.explainer_errors import ExplainerRefused
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.file_lock import LockCapabilityError, exclusive_file_lock
from agents_remember.kernel.primitives.host_environment import host_environment
from agents_remember.kernel.primitives.paseo_node_paths import NodeRuntime, product_node, xdg_home

__all__ = [
    "BROWSERS_PATH_ENV",
    "BROWSER_NAME",
    "PLAYWRIGHT_ENV",
    "PLAYWRIGHT_PACKAGE",
    "PLAYWRIGHT_VERSION",
    "BrowserHome",
    "BrowserReady",
    "browser_environment",
    "browser_missing",
    "chromium_builds",
    "ensure",
    "home",
    "module_file",
    "node_executable",
]

#: The Node module whose ``index.js`` the load imports.
PLAYWRIGHT_PACKAGE = "playwright"
#: The one version this build fetches. A checkout pins the same version in its dashboard lock, and
#: ``mcp/tests/test_explainer_browser.py`` fails when the two drift: the version is the contract,
#: not a preference, because a release that lost Ubuntu 26.04 support would publish nothing.
PLAYWRIGHT_VERSION = "1.65.0"
#: The browser build the load launches.
BROWSER_NAME = "chromium"
#: The variable a user sets to name a module of their own. Never overridden, never replaced.
PLAYWRIGHT_ENV = "AR_PLAYWRIGHT"
#: Playwright's own name for the folder its browser builds live in.
BROWSERS_PATH_ENV = "PLAYWRIGHT_BROWSERS_PATH"
#: The product-owned folder under the user's data home, beside the product's Node.
FOLDER = "explainer-browser"
#: One ``npm install`` of the module, and one browser download: each bounded, and each a child
#: process, so a stalled registry or CDN is a named failure rather than a render that never ends.
INSTALL_SECONDS = 300.0
BROWSER_SECONDS = 900.0
#: Two seats that reach their first render together fetch once; the second waits for the first.
ACQUIRE_SECONDS = 600.0
#: What the user reads, in one sentence, at the place where they asked, while this installation's
#: first page fetches its browser: the fetch happens once, and that first page waits for it. The
#: sentence must stand while the download runs, not only afterwards; a page with nothing to fetch
#: says nothing.
BROWSER_NOTICE = (
    "This installation fetches its browser once, for the first page: that page takes a minute or "
    "two while it does, and later pages are immediate."
)

COMMAND_NOT_FOUND = 127
COMMAND_TIMED_OUT = 124


@dataclass(frozen=True)
class CommandResult:
    """What one bounded child answered: its code, its standard output and its error stream."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def missing(self) -> bool:
        """The executable does not exist: nothing is installed at that path."""

        return self.returncode == COMMAND_NOT_FOUND


def run_bounded(
    argv: Sequence[str], timeout_seconds: float, environment: Mapping[str, str] | None = None
) -> CommandResult:
    """Run one bounded child here, in the canvas tier, and capture its output.

    The environment is the host's with a session identity stripped (the kernel's rule), plus the
    names this fetch adds for its own child alone. A missing executable and a child that runs out of
    time are answers, not exceptions: the caller turns them into the two named failures.
    """

    env = host_environment(os.environ)
    if environment is not None:
        env.update(environment)
    if len(argv) > 1 and str(argv[1]).endswith("/npm/bin/npm-cli.js"):
        env["PATH"] = str(Path(argv[0]).parent) + os.pathsep + env.get("PATH", "")
    try:
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            env=env,
        )
    except FileNotFoundError as error:
        return CommandResult(COMMAND_NOT_FOUND, "", str(error))
    except subprocess.TimeoutExpired:
        return CommandResult(COMMAND_TIMED_OUT, "", f"timed out after {timeout_seconds:g} s")
    except OSError as error:
        return CommandResult(1, "", str(error))
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


#: One bounded child of this module's own; a test substitutes its own through :func:`ensure`.
Runner = Callable[[Sequence[str], float, Mapping[str, str] | None], CommandResult]


@dataclass(frozen=True)
class BrowserHome:
    """The product-owned folder: the module in it, and the browser builds beside it."""

    root: Path

    @property
    def module(self) -> Path:
        return self.root / "node_modules" / PLAYWRIGHT_PACKAGE / "index.js"

    @property
    def cli(self) -> Path:
        return self.root / "node_modules" / PLAYWRIGHT_PACKAGE / "cli.js"

    @property
    def browsers(self) -> Path:
        return self.root / "browsers"

    def installed(self) -> bool:
        """Whether this folder holds both the module and a browser build it can launch."""

        return self.module.is_file() and bool(chromium_builds(self.browsers))


@dataclass(frozen=True)
class BrowserReady:
    """What the load can use, and how it came to be there.

    ``state`` is one word the answer shows: ``environment`` (a module ``AR_PLAYWRIGHT`` named),
    ``ready`` (this installation's own, already present) or ``fetched`` (fetched by this call).
    """

    state: str
    module: Path
    browsers: Path
    version: str = PLAYWRIGHT_VERSION
    seconds: float = 0.0

    def record(self) -> dict[str, object]:
        """The one record a render answer carries about the browser it loaded with."""

        record: dict[str, object] = {
            "state": self.state,
            "playwright": self.version,
            "module": self.module.as_posix(),
            "browsers": self.browsers.as_posix(),
        }
        if self.state == "fetched":
            record["detail"] = (
                f"Playwright {self.version} and its Chromium were missing, so this installation "
                f"fetched them into {self.browsers.parent.as_posix()} before the load, in "
                f"{self.seconds:.1f} s. Nothing is fetched at installation, and later renders use "
                "them as they are."
            )
        return record


def home() -> BrowserHome:
    """The product-owned browser folder under the user's data home."""

    data = xdg_home("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return BrowserHome(data / "agents-remember" / FOLDER)


def chromium_builds(browsers: Path) -> tuple[Path, ...]:
    """The browser builds in ``browsers`` that this module could have put there."""

    if not browsers.is_dir():
        return ()
    return tuple(
        sorted(
            item
            for item in browsers.iterdir()
            if item.is_dir()
            and (
                item.name.startswith(f"{BROWSER_NAME}-")
                or item.name.startswith("chromium_headless_shell-")
            )
        )
    )


def module_file(environment: Mapping[str, str] | None = None) -> Path | None:
    """The Playwright module this installation loads, or ``None`` when it has none.

    ``AR_PLAYWRIGHT`` wins whenever it is set, whether or not the file is there: a user who names
    a module is answered from that module, never quietly from another one.
    """

    ambient = os.environ if environment is None else environment
    named = (ambient.get(PLAYWRIGHT_ENV) or "").strip()
    if named:
        return Path(named)
    installed = home().module
    return installed if installed.is_file() else None


def node_executable() -> str:
    """The Node the browser runs on: the product's own, else whatever ``node`` the machine has."""

    try:
        runtime = product_node()
    except PaseoRuntimeFailure:
        return "node"
    return runtime.node.as_posix() if runtime.node.is_file() else "node"


def browser_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """The extra names the load needs when this installation supplies its own browser.

    Empty when a user named a module: their module, and the browser folder of their choice (which
    Playwright reads from its own defaults), stay theirs.
    """

    ambient = os.environ if environment is None else environment
    if (ambient.get(PLAYWRIGHT_ENV) or "").strip():
        return {}
    installed = home()
    if not installed.module.is_file():
        return {}
    return {
        PLAYWRIGHT_ENV: installed.module.as_posix(),
        BROWSERS_PATH_ENV: installed.browsers.as_posix(),
    }


def browser_missing() -> tuple[str, ...]:
    """What this installation still lacks before a load can run, in the words of the answer."""

    named = (os.environ.get(PLAYWRIGHT_ENV) or "").strip()
    if named:
        if Path(named).is_file():
            return ()
        return (f"the Playwright module AR_PLAYWRIGHT names at {named} does not exist",)
    installed = home()
    if not installed.module.is_file():
        return (f"Playwright {PLAYWRIGHT_VERSION} at {installed.root}",)
    if not chromium_builds(installed.browsers):
        return (f"the Chromium that Playwright launches, at {installed.browsers}",)
    return ()


#: The one command a user runs to fetch the browser themselves; the failure answer names it.
BROWSER_COMMAND = "agents-remember explainer-browser --fetch"


def browser_notice() -> str | None:
    """The one sentence the explainer says before its first render, when a fetch is coming."""

    return BROWSER_NOTICE if browser_missing() else None


def ensure(
    *, runner: Runner = run_bounded, clock: Callable[[], float] = time.monotonic
) -> BrowserReady:
    """Answer the module and browser the load may use, fetching what is missing at first use.

    A user's ``AR_PLAYWRIGHT`` is answered as it is. Otherwise this installation's own folder is
    used, fetched now when it is not there yet. A fetch that fails refuses with what is missing and
    the command that gets it: the caller shows that at the place it was asked, and publishes
    nothing.
    """

    named = (os.environ.get(PLAYWRIGHT_ENV) or "").strip()
    if named:
        module = Path(named)
        if not module.is_file():
            raise ExplainerRefused(
                "browser-missing",
                f"AR_PLAYWRIGHT names {named}, and no file is there. Set it to the module's "
                "index.js, or clear it to let this installation fetch Playwright and its Chromium "
                "of its own.",
            )
        return BrowserReady(
            state="environment",
            module=module,
            browsers=Path(os.environ.get(BROWSERS_PATH_ENV) or module.parent.parent / "browsers"),
        )
    installed = home()
    if installed.installed():
        return BrowserReady(state="ready", module=installed.module, browsers=installed.browsers)
    started = clock()
    placed = _fetch(installed, runner=runner)
    return BrowserReady(
        state="fetched" if placed else "ready",
        module=installed.module,
        browsers=installed.browsers,
        seconds=max(0.0, clock() - started) if placed else 0.0,
    )


def _fetch(installed: BrowserHome, *, runner: Runner) -> bool:
    """Install the module and its browser into the product's folder, or refuse naming the cause.

    Answers whether this call placed them; a second process that got there first makes this one a
    reader of the same folder, never a second download.
    """

    runtime = _product_runtime()
    try:
        with exclusive_file_lock(
            installed.root, "explainer browser", deadline=time.monotonic() + ACQUIRE_SECONDS
        ):
            if installed.installed():
                return False
            _place(installed, runtime, runner=runner)
            return True
    except LockCapabilityError as error:
        raise _refused(f"another process kept the browser folder busy: {error}") from error
    except OSError as error:
        raise _refused(str(error)) from error


def _product_runtime() -> NodeRuntime:
    """The Node and npm that fetch the browser, or the reason this system has none."""

    try:
        runtime = product_node()
    except PaseoRuntimeFailure as error:
        raise ExplainerRefused(
            "browser-fetch-refused",
            f"Playwright {PLAYWRIGHT_VERSION} and its Chromium are missing, and this installation "
            f"fetches them only where it installs its own Node: {error}. On another system, name "
            f"a Playwright module's index.js in {PLAYWRIGHT_ENV} and install its Chromium in "
            "Playwright's own browser folder; no macOS measurement exists for this path.",
        ) from error
    if not runtime.node.is_file() or not runtime.npm.is_file():
        raise ExplainerRefused(
            "browser-fetch-refused",
            f"Playwright {PLAYWRIGHT_VERSION} and its Chromium are missing, and the product Node "
            f"that fetches them is not installed at {runtime.root}. Install it with "
            "`agents-remember paseo provision --config <your settings file>`, then ask the "
            "explainer again.",
        )
    return runtime


def _place(installed: BrowserHome, runtime: NodeRuntime, *, runner: Runner) -> None:
    """One fetch, staged beside its destination and moved in whole when both parts are there."""

    staging = installed.root.with_name(f".{installed.root.name}.staging")
    _remove(staging)
    try:
        _install_module(staging, runtime, runner=runner)
        _install_browser(staging, runtime, runner=runner)
        if not BrowserHome(staging).installed():
            raise _refused(
                "the fetch finished but the module or its Chromium is not where it belongs"
            )
        _remove(installed.root)
        staging.rename(installed.root)
    finally:
        _remove(staging)


def _install_module(staging: Path, runtime: NodeRuntime, *, runner: Runner) -> None:
    npm = [runtime.node.as_posix(), runtime.npm.as_posix()]
    completed = runner(
        [
            *npm,
            "install",
            "--prefix",
            staging.as_posix(),
            f"{PLAYWRIGHT_PACKAGE}@{PLAYWRIGHT_VERSION}",
            "--no-audit",
            "--no-fund",
            "--no-save",
            "--loglevel=error",
        ],
        INSTALL_SECONDS,
        None,
    )
    _require(completed, f"npm could not install {PLAYWRIGHT_PACKAGE}@{PLAYWRIGHT_VERSION}")


def _install_browser(staging: Path, runtime: NodeRuntime, *, runner: Runner) -> None:
    cli = staging / "node_modules" / PLAYWRIGHT_PACKAGE / "cli.js"
    if not cli.is_file():
        raise _refused("npm installed no playwright command to fetch the browser with")
    # The browser is fetched inside the staging folder and moves with it, so a failed fetch leaves
    # no half-installed browser where the next attempt would read it as one.
    completed = runner(
        [runtime.node.as_posix(), cli.as_posix(), "install", BROWSER_NAME],
        BROWSER_SECONDS,
        {BROWSERS_PATH_ENV: (staging / "browsers").as_posix()},
    )
    _require(completed, f"Playwright could not install its {BROWSER_NAME}")


def _require(completed: CommandResult, what: str) -> None:
    if completed.returncode == 0:
        return
    raise _refused(f"{what}: {_cause(completed)}")


def _cause(completed: CommandResult) -> str:
    """The child's own first useful line: npm names the code, then buries it under a log path."""

    lines = [
        line.strip()
        for line in f"{completed.stderr}\n{completed.stdout}".splitlines()
        if line.strip() and "complete log of this run" not in line
    ]
    return lines[0] if lines else f"exit {completed.returncode}"


def _refused(cause: str) -> ExplainerRefused:
    """The one failure message: what is missing, where it goes, and the command that gets it."""

    return ExplainerRefused(
        "browser-fetch-failed",
        f"{cause}. Playwright {PLAYWRIGHT_VERSION} and its Chromium are missing from "
        f"{home().root}. Fetch them with `agents-remember explainer-browser --fetch`; a machine "
        "without access to the npm registry or to Playwright's download host needs that access "
        "once.",
    )


def _remove(target: Path) -> None:
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
