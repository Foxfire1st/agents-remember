"""Read the ledger's attribution out of the memory commits that carry it, and write it.

The ledger is a lookup from a code commit to the memory commit landed beside it. That fact
used to live only in a tracked ``memory.md`` table, which is why the table had to be merged
by hand on every sync and why three hand-authored tables in one day carried a superseded row,
a wrong order, and a header disagreeing with its own first row. The attribution now lives in
the memory commit itself, as a ``Code-Commit: <sha>`` trailer inside the hashed object, and
this module is the one place that reads it back -- and, since the key is one literal that both
directions must agree on, the one place that renders it too.

What the commits tell us is exactly the table, and nothing has to be guessed to get it:

* every commit reachable from the memory state whose message carries the trailer names one
  mapping, ``trailer value -> that commit``;
* the branch's own rows are its own commits, newest first, and the trailing rows are the
  attributed commits of the complete source, in their own order, which is the source's first
  parent order;
* a commit with no trailer contributes no row. That is a memory commit with no code
  counterpart to name -- a policy edit, a settings edit, a README, the ledger commit itself
  -- and ``git rev-list`` is the census, so nothing is silently dropped.

Two rules keep this honest rather than merely plausible. The census is git's own ancestry
walk, so a row exists only for a commit that really carries the trailer. And the value has to
name a commit the code repository really holds before the row can mean anything, which is the
same truth test the projection already applies to every row it keeps.

One reading rule is transitional by construction and says so: a commit written before the
trailer rule carries no trailer, and its attribution is still whatever the ledger it carried
said. That commit is read from its own blob, *per commit*, because the alternative -- a mode
that decides once whether to trust trailers or the file -- is the compatibility layer this
change exists to remove. Backfilling the history writes the trailer onto those commits, the
per-commit fallback then finds nothing left to read, and it can be deleted without touching
any caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_ledger import LedgerRow

# The trailer every memory-content commit carries, declared ONCE, here, and imported by the
# model that renders it (``models/closeout/input.py``). One literal with two directions of use
# is the point: a writer that changed its own copy would emit trailers this reader silently
# ignores, and that failure looks like "no attribution exists" rather than like a bug.
#
# The direction is kernel -> models, not the reverse, for two concrete reasons rather than a
# preference. ``layers.toml`` ranks ``kernel`` below ``models`` and permits an import only when
# ``rank(Q) < rank(P)``, so a kernel module importing a model would break the declared layer
# contract at the moment it is armed. And this module is the reader of a hashed object, which is
# the lower-level fact: the writer is a caller of it, not its owner.
CODE_COMMIT_TRAILER_KEY = "Code-Commit"

# The object name and the trailer value, on two lines. ``-z`` terminates each record with a
# NUL, so a message -- which is never emitted here -- cannot be split into a second record, and
# no separator has to appear in an argv we hand to git.
_LOG_FORMAT = f"%H%n%(trailers:key={CODE_COMMIT_TRAILER_KEY},valueonly)"

# ``Code-Commit: <object name>``. Git's trailer machinery is case-insensitive on the key and
# tolerant of surrounding space; the value is an object name and nothing else. Anchored at the
# line start because ``%(trailers:...)`` emits one trailer per line.
_TRAILER_LINE = re.compile(
    rf"^{CODE_COMMIT_TRAILER_KEY}:[ \t]*(?P<value>[0-9a-fA-F]{{4,64}})[ \t]*$",
    re.IGNORECASE,
)


def render_memory_content_message(body: str, code_commit: str) -> str:
    """The memory-content commit body: the caller's message verbatim, then the attribution.

    This is the ONE writer of the trailer, and it sits in the module that reads it back,
    beside the one literal both directions use: a copy of the format in a producer would
    emit something ``parse_code_commit_trailer`` silently ignores, and that failure looks
    like "no attribution exists" rather than like a bug.

    The caller's body is kept byte for byte and the trailer is appended as its own final
    paragraph -- never substituted for the body and never merged into it. That is the whole
    reason this lives apart from any one caller's message: a producer's commit message can
    be a public argument of another tool, so the body may be several paragraphs and its own
    last paragraph may itself be ``Key: value`` lines. Git reads a trailer only from the
    final block, so a body line can never be mistaken for this attribution, and the blank
    line is what keeps the caller's last paragraph out of that block instead of folded into
    it. A producer therefore never edits the message string it was given; it renders the
    commit message with this function at the commit site.

    Called by every memory-content producer: ``EffectiveCloseoutInput.memory_content_message``
    (worktree closeout and, through it, the closeout recovery route when it still owes its
    memory commit), the direct-landing memory leg, the prepared memory-content leg, and the
    memory carryover and baseline-adoption routes. A producer that has no code commit to
    name writes no trailer at all rather than a fabricated one.
    """

    return f"{body}\n\n{CODE_COMMIT_TRAILER_KEY}: {code_commit}"


class MemoryAttributionError(RuntimeError):
    """A memory history that cannot be walked at all, with the reason git gave."""


@dataclass(frozen=True)
class AttributedCommit:
    """One memory commit and the code commit its trailer names, when it names one."""

    memory_commit: str
    code_commit: str | None

    @property
    def is_attributed(self) -> bool:
        return self.code_commit is not None

    def row(self, *, code_repository: Path | None = None) -> LedgerRow | None:
        """This commit's ledger row, or ``None`` when it attributes nothing.

        ``code_repository`` is optional because a caller that has already proved the code
        commit exists -- the projection does, for every row it keeps -- is asking a different
        question than "may this row mean anything at all". Passing it applies that test here.
        """

        if self.code_commit is None:
            return None
        if code_repository is not None and not code_commit_exists(
            code_repository, self.code_commit
        ):
            return None
        return LedgerRow(self.code_commit, self.memory_commit)


def parse_code_commit_trailer(message: str) -> str | None:
    """The object name a commit message attributes, or ``None`` when it attributes none.

    The last trailer block wins, which is what ``git interpret-trailers --parse`` reports and
    what ``%(trailers:key=...)`` renders: a message whose body merely mentions the key is not
    an attribution, and a caller cannot append a second one and have it read as the first.
    """

    attributed = [
        match.group("value")
        for line in message.splitlines()
        if (match := _TRAILER_LINE.match(line.strip()))
    ]
    return attributed[-1] if attributed else None


def attributed_commits(
    repository: Path,
    *,
    tip: str,
    exclude: str = "",
) -> list[AttributedCommit]:
    """Every commit reachable from ``tip`` and not from ``exclude``, with its attribution.

    The whole ancestry, not the first-parent line, because a memory line merges: this repository's
    own ledger carries 35 rows whose memory commit is not on the tip's first-parent line, and 21 of
    those are still ancestors. Reading only the line would make a merged-in mapping invisible, which
    is the "partial coverage looks like a gap" failure the trailer rule exists to prevent.

    Order is ``git log``'s own -- commit order, newest first -- which is the order the ledger
    records its rows in: a row is introduced by the ledger commit that first carried it, so the
    newer a mapping is, the nearer the top it sits. ``exclude`` is how a caller asks for one
    branch's own commits: everything the work branch added on top of the source it started from.

    A commit that cannot be read is not a commit that attributes nothing, so an unreadable history
    refuses here instead of quietly contributing no rows.
    """

    args = ["log", "--date-order", "-z", f"--format={_LOG_FORMAT}", tip]
    if exclude:
        args.append(f"^{exclude}")
    result = run_git(repository, args)
    if result.returncode != 0:
        raise MemoryAttributionError(
            f"the memory history at {tip} cannot be walked in {repository.as_posix()}: "
            f"{result.stderr.strip() or 'git log failed'}"
        )
    return _attributed_records(result.stdout)


def _attributed_records(stdout: str) -> list[AttributedCommit]:
    commits: list[AttributedCommit] = []
    for record in stdout.split("\0"):
        lines = [line for line in record.splitlines() if line.strip()]
        if lines:
            commits.append(AttributedCommit(lines[0].strip(), _trailer_value_from(lines[1:])))
    return commits


def _trailer_value_from(value_lines: Sequence[str]) -> str | None:
    """The attributed object name from the value lines ``%(trailers:key=...)`` emitted.

    The value-only template emits exactly one line per matching trailer, so several lines mean
    several trailers; the last one is the attribution, for the same reason the message parse
    takes the last one. Anything that is not an object name is not an attribution.
    """

    matches = [
        match.group("value")
        for line in value_lines
        if (match := _TRAILER_LINE.match(f"{CODE_COMMIT_TRAILER_KEY}: {line.strip()}"))
    ]
    return matches[-1] if matches else None


def code_commit_exists(repository: Path, commit: str) -> bool:
    """Whether the code repository really holds ``commit`` as a commit object."""

    return run_git(repository, ["cat-file", "-e", f"{commit}^{{commit}}"]).returncode == 0


def ledger_rows_from_attribution(
    commits: Iterable[AttributedCommit],
    *,
    code_repository: Path | None = None,
) -> list[LedgerRow]:
    """The ledger the attributed commits project, newest first, with no invented order.

    ``commits`` arrives newest first, which is the order ``git log`` reports and the order the
    ledger records, so this is a map rather than a sort: a commit that attributes nothing
    contributes nothing, and a commit whose attributed code commit the code repository does
    not hold contributes nothing either -- the same truth test the projection applies, applied
    at the point the row is created rather than after it has been written down.
    """

    rows: list[LedgerRow] = []
    for commit in commits:
        row = commit.row(code_repository=code_repository)
        if row is not None:
            rows.append(row)
    return rows
