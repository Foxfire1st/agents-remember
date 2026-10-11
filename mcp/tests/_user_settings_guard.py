"""One guard for test-support writes of the user's settings file (MIK-R135).

No module of the test support writes the settings file of the user who runs
it, in whatever way it is run. Every such write reaches its path through
:func:`user_settings_path_for_write`, which raises before a folder or a file
is made when the path is the real one of the account running the process.

The real path is the one the product derives when ``XDG_CONFIG_HOME`` is not
set and the home is the account's home as the system's account database
names it. When that database cannot be read, the guard compares the candidate
against the earliest home it could observe and refuses on a match, saying
exactly what it compared with; it never writes to a matched path. A home
mutated before the guard is first imported is unobservable in-process, so the
observation is reported honestly as the earliest-observed home, never as a
proven process-start home.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import pwd
except ImportError:  # Windows carries no pwd module; the database is unreadable there.
    pwd = None  # type: ignore[assignment]

from agents_remember.kernel.agentic_settings import user_agentic_settings_path

# The earliest home this guard could observe: read at first import, before any
# caller of the guard runs. Only the unreadable-account-database fallback
# compares against it, and the refusal names it as observed, because a HOME
# mutated before this import is unrecoverable in-process.
_EARLIEST_OBSERVED_HOME = Path(os.path.expanduser("~"))


class UserSettingsWriteRefused(ValueError):
    """Test support must not write the user's real settings file."""


def _account_home() -> Path | None:
    """The account's home from the system account database, or None unreadable."""
    if pwd is None:
        return None
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        return None


def _refusal(candidate: Path, real: Path, detail: str) -> UserSettingsWriteRefused:
    return UserSettingsWriteRefused(
        f"Refusing to write the user's real settings file at {candidate}; "
        f"set XDG_CONFIG_HOME to point to a temporary folder so test support "
        f"never writes {real}. {detail}"
    )


def _same_settings_path(candidate: Path, real: Path) -> bool:
    return candidate == real or Path(os.path.realpath(candidate)) == Path(os.path.realpath(real))


def user_settings_path_for_write() -> Path:
    """Return the user's settings path for a write, refusing the real one.

    Raises :class:`UserSettingsWriteRefused` before a folder or a file is
    made when the path the product derives is the account's real settings
    file. When the account database cannot be read, the candidate is compared
    against the earliest observed home and refused on a match, naming that
    observation; only a provably moved path is returned. Creates nothing.
    """
    candidate = user_agentic_settings_path()
    account_home = _account_home()
    if account_home is not None:
        real = account_home / ".config" / "agents-remember" / "settings.json"
        if _same_settings_path(candidate, real):
            raise _refusal(candidate, real, "That path is the account's real settings file.")
        return candidate
    observed = _EARLIEST_OBSERVED_HOME
    fallback = observed / ".config" / "agents-remember" / "settings.json"
    if _same_settings_path(candidate, fallback):
        raise _refusal(
            candidate,
            fallback,
            "The account home cannot be read from the account database; "
            f"compared with the earliest home observed by the guard ({observed}).",
        )
    return candidate
