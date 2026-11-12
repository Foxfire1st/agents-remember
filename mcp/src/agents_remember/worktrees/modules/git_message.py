"""Git-compatible publication message cleanup, independent of ref publication."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.worktrees.modules.git_text import (
    _nul_records,
    _transport_safe_git_diagnostic,
)
from agents_remember.worktrees.modules.models import CommitPublicationRefusal

_CLEANUP_MODES = ("default", "whitespace", "scissors", "strip", "verbatim")
_SCISSORS = " ------------------------ >8 ------------------------\n"
_AUTO_COMMENT_CANDIDATES = "#;@!$%^&|:"
_AS_GIT_COMMIT = "so nothing was published (git commit refuses it too)"


def _cleaned_message(repo: Path, message: str) -> str:
    """The message as ``git commit -m`` stores it, or a refusal where that command aborts.

    These are the steps of ``git commit -m`` in Git 2.54 (``builtin/commit.c``), and the tests
    compare the result with a real ``git commit -m`` for every mode and comment setting. The
    message ends with a newline. Unless ``commit.cleanup`` is ``verbatim`` its whitespace is
    cleaned (:func:`_stripspace`); without an editor ``default`` and ``scissors`` do no more than
    ``whitespace``. A true ``commit.verbose`` cuts the message at the scissors line. ``strip``
    drops the lines that begin with the comment string: ``core.commentChar`` or
    ``core.commentString``, whichever is set last, and for ``auto`` the character Git selects
    (:func:`_auto_comment_character`).

    Git aborts the commit for an invalid mode, for a message that is empty after these steps (only
    blank and ``Signed-off-by`` lines; under ``verbatim`` only a message of no bytes) and for an
    ``auto`` with no free character. Each of them is refused here, before anything is touched.
    """

    settings = _message_settings(repo)
    mode = settings.get("cleanup", "default")
    if mode not in _CLEANUP_MODES:
        raise CommitPublicationRefusal(
            f"Invalid cleanup mode {mode} (commit.cleanup), {_AS_GIT_COMMIT}"
        )
    cleaning = mode != "verbatim"
    text = message if not message or message.endswith("\n") else f"{message}\n"
    if cleaning:
        text = _stripspace(text)
    comment = settings.get("comment", "#")
    if comment.lower() == "auto":
        comment = _auto_comment_character(text)
    if _commit_verbose(repo):
        text = text[: _scissors_start(text, comment)]
    if cleaning:
        text = _stripspace(text, comment if mode == "strip" else None)
    lines = text.split("\n")
    if not text or (
        cleaning and all(line.startswith("Signed-off-by: ") or not line for line in lines)
    ):
        raise CommitPublicationRefusal(f"the commit message is empty, {_AS_GIT_COMMIT}")
    return text


def _message_settings(repo: Path) -> dict[str, str]:
    """``commit.cleanup`` and the comment string as Git reads them: the value set last wins."""

    pattern = r"^(commit\.cleanup|core\.comment(char|string))$"
    found = run_git(repo, ["config", "-z", "--get-regexp", pattern])
    settings: dict[str, str] = {}
    for entry in _nul_records(found.stdout):
        key, _, value = entry.partition("\n")
        settings["cleanup" if key == "commit.cleanup" else "comment"] = value
    return settings


def _commit_verbose(repo: Path) -> bool:
    """Whether ``commit.verbose`` is on, which makes ``git commit`` cut at the scissors line."""

    found = run_git(repo, ["config", "--type=bool-or-int", "--get", "commit.verbose"])
    if found.returncode not in (0, 1):
        detail = _transport_safe_git_diagnostic(found.stderr.strip())
        raise CommitPublicationRefusal(f"{detail}, {_AS_GIT_COMMIT}")
    value = found.stdout.strip()
    return value == "true" or (value.isdigit() and int(value) > 0)


def _stripspace(text: str, comment: str | None = None) -> str:
    """Git's ``strbuf_stripspace``: the cleaning ``git commit`` and ``git stripspace`` share.

    Trailing whitespace leaves every line (Git counts space, tab, carriage return and newline),
    runs of empty lines become one, empty lines at both ends go, and the text ends with a newline.
    With ``comment`` the lines that begin with it are dropped. It is done here and not by
    ``git stripspace`` because the shared runner reads Git's output as text, which would turn a
    carriage return inside a line into a newline.
    """

    lines: list[str] = []
    gap = False
    for raw in text.removesuffix("\n").split("\n") if text else []:
        if comment is not None and raw.startswith(comment):
            continue
        line = raw.rstrip(" \t\r")
        if line and gap and lines:
            lines.append("")
        gap = not line
        if line:
            lines.append(line)
    return "".join(f"{line}\n" for line in lines)


def _scissors_start(text: str, comment: str) -> int:
    """Where Git cuts ``text`` at the scissors line of ``comment`` (``wt_status_locate_end``)."""

    cut = f"{comment}{_SCISSORS}"
    if text.startswith(cut):
        return 0
    found = text.find(f"\n{cut}")
    return len(text) if found < 0 else found + 1


def _auto_comment_character(text: str) -> str:
    """The character ``core.commentChar=auto`` selects for ``text`` (``adjust_comment_line_char``).

    ``#`` when the message holds none. Otherwise the first of Git's candidates that begins no line
    before the tail Git ignores (:func:`_ignored_tail_start`). Git aborts the commit when every
    candidate is in use.
    """

    if "#" not in text:
        return "#"
    end = _ignored_tail_start(text)
    used = {text[0]} | {text[at + 1] for at in range(end - 1) if text[at] in "\n\r"}
    free = [candidate for candidate in _AUTO_COMMENT_CANDIDATES if candidate not in used]
    if not free:
        raise CommitPublicationRefusal(
            "core.commentChar is auto and every character Git could select begins a line of the "
            f"commit message, {_AS_GIT_COMMIT}"
        )
    return free[0]


def _ignored_tail_start(text: str) -> int:
    """Where the tail begins that Git ignores when it selects (``ignored_log_message_bytes``).

    The tail is the trailing run of ``#`` lines and empty lines, with an old ``Conflicts:`` block
    and its tab-indented paths, and everything from a ``#`` scissors line on. The branches follow
    Git's function one for one, including that a run beginning at the first byte is not a tail.
    """

    end = _scissors_start(text, "#")
    tail, line, conflicts = 0, 0, False
    while line < end:
        if text.startswith("#", line, end) or text[line] == "\n":
            tail = tail or line
        elif text.startswith("Conflicts:\n", line):
            tail, conflicts = tail or line, True
        elif conflicts and text[line] == "\t":
            pass
        elif tail:
            tail, conflicts = 0, False
        line = text.find("\n", line) + 1 or len(text)
    return tail or end
