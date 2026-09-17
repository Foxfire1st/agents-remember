"""The shared exclusion register: what a citation index may not read, and why.

One register, three sources, all reduced to one answer -- *is this path outside the
candidate population, and which rule said so*:

``pathRules.exclude``
    The exclusion register agreed with the user before memory closeout and persisted to the
    memory layer's settings (``onboarding.pathRules.exclude.paths``). Matched with the same
    :func:`matches_any` semantics the storage resolver and the drift check already use, so a
    pattern means the same thing everywhere it is read.

``.gitignore``
    The code repository's own ignore rules. Inside a Git work tree Git is the authority --
    ``ls-files --exclude-standard`` has already removed them -- and the register records the
    patterns that were in force. Outside a work tree (the documented non-Git fallback) the
    register applies the same patterns through a bounded matcher: comments and blank lines
    skipped, ``!`` negation honoured last-match-wins, a leading ``/`` anchored to the code
    root, a trailing ``/`` directory-only, ``*``/``?`` not crossing ``/`` and ``**`` crossing
    it. A file, not a directory tree, is what the walk needs, so a directory is pruned only
    while the ignore file carries no negation rule; once one exists the walk descends and
    decides per file. Nested ``.gitignore`` files are read only in a work tree, where Git
    owns them.

``caller``
    Excludes supplied with one operation (``exclude=`` on ``citation_fix`` and ``--exclude``
    on the CLI), matched exactly like ``pathRules.exclude`` and scoped to that call.

The register is a *record* as much as a filter: it serializes onto the manifest of every
published generation, so a later reader can see which rule set produced the index instead of
having to reconstruct it from the checkout.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.coordination_context.storage import matches_any
from agents_remember.memory_quality.style.citations.citation_index_settings import (
    CitationIndexSettings,
    read_citation_index_settings,
)
from agents_remember.memory_quality.style.citations.source_index_state import (
    EXCLUSION_SOURCE_CALLER,
    EXCLUSION_SOURCE_PATH_RULES,
    GITIGNORE_AUTHORITY_ABSENT,
    ExclusionRegister,
    ExclusionRule,
    SourceIndexError,
)

GITIGNORE_NAME = ".gitignore"


@dataclass(frozen=True)
class GitIgnoreRule:
    """One ``.gitignore`` line, reduced to what the fallback walk actually needs."""

    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool
    expression: re.Pattern[str]

    def matches(self, relative: str, *, is_directory: bool) -> bool:
        target = relative.rstrip("/")
        if not target:
            return False
        if self.directory_only and not is_directory:
            return False
        return self.expression.fullmatch(target) is not None


def parse_gitignore(text: str) -> tuple[GitIgnoreRule, ...]:
    """Every effective rule in one ``.gitignore`` body, in file order."""
    rules: list[GitIgnoreRule] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # A trailing backslash escapes a trailing space; nothing else is unescaped here.
        if not line.endswith("\\ "):
            line = line.rstrip()
        if not line:
            continue
        negated = line.startswith("!")
        pattern = line[1:] if negated else line
        if not pattern:
            continue
        directory_only = pattern.endswith("/")
        if directory_only:
            pattern = pattern[:-1]
        anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
        pattern = pattern.lstrip("/")
        if not pattern:
            continue
        rules.append(
            GitIgnoreRule(
                pattern=pattern,
                negated=negated,
                directory_only=directory_only,
                anchored=anchored,
                expression=_gitignore_expression(pattern, anchored=anchored),
            )
        )
    return tuple(rules)


def _gitignore_expression(pattern: str, *, anchored: bool) -> re.Pattern[str]:
    """Git's wildcard rules: ``*``/``?`` stay inside one segment, ``**`` crosses them.

    A character class (``[abc]``) is not part of this subset and is matched literally, which
    is why no unescaped bracket ever reaches :mod:`re`.
    """
    segments = pattern.split("/")
    body: list[str] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            # `a/**/b` spans zero or more directories; a trailing `**` spans everything.
            body.append(".*" if last else "(?:[^/]+/)*")
            continue
        body.append(_segment_expression(segment))
        if not last:
            body.append("/")
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(prefix + "".join(body), re.DOTALL)


def _segment_expression(segment: str) -> str:
    out: list[str] = []
    for character in segment:
        if character == "*":
            out.append("[^/]*")
        elif character == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(character))
    return "".join(out)


def excluding_rule(
    register: ExclusionRegister,
    relative: str,
    *,
    is_directory: bool = False,
) -> ExclusionRule | None:
    """The first path rule that excludes ``relative``, or ``None`` when it is a candidate.

    Only the ``pathRules.exclude`` and ``caller`` components are consulted here. The register's
    ``.gitignore`` component is a separate branch on purpose: inside a work tree Git has already
    applied it (``--exclude-standard``), and outside one it is applied by
    :class:`FallbackIgnoreMatcher` with Git's own anchoring and negation rules, which
    ``matches_any`` does not implement. A pattern is therefore never read twice.
    """
    for rule in register.rules:
        if _pattern_excludes(rule.pattern, relative, is_directory=is_directory):
            return rule
    return None


def _pattern_excludes(pattern: str, relative: str, *, is_directory: bool) -> bool:
    """A path rule excludes a file by name, and a directory by name or by its contents."""
    return matches_any([pattern], relative) or (
        is_directory and matches_any([pattern], f"{relative.rstrip('/')}/")
    )


def validate_caller_excludes(excludes: Sequence[str]) -> tuple[str, ...]:
    """The caller's own excludes, refused by name when one cannot mean anything.

    A pattern is a code-root-relative path glob matched like ``pathRules.exclude``. An empty
    pattern, an absolute one, or one carrying ``..`` is refused rather than quietly matching
    nothing, because "I excluded it and it is still indexed" is a harder failure to see than a
    refusal at the call.
    """
    validated: list[str] = []
    for one in excludes:
        pattern = str(one).strip()
        if not pattern:
            raise SourceIndexError("citation exclude patterns must not be empty")
        if pattern.startswith("/") or pattern.startswith("\\"):
            raise SourceIndexError(
                f"citation exclude pattern {pattern!r} must be relative to the code root"
            )
        if ".." in pattern.split("/"):
            raise SourceIndexError(
                f"citation exclude pattern {pattern!r} must not escape the code root"
            )
        validated.append(pattern)
    return tuple(validated)


def resolve_exclusion_register(
    *,
    code_root: Path,
    memory_root: Path,
    caller: tuple[str, ...] = (),
    settings: CitationIndexSettings | None = None,
    gitignore_authority: str = GITIGNORE_AUTHORITY_ABSENT,
) -> ExclusionRegister:
    """Build the register for one acquisition from the settings, the ignore file, and the call.

    ``gitignore_authority`` is the caller's statement about how the ignore file was honoured --
    ``git`` inside a work tree (Git applied it), ``register`` outside one (this module's bounded
    matcher applies it), ``absent`` when the root has no ignore file at all. It is an explicit
    argument because the acquisition has already asked Git the same question and must not ask
    it twice.
    """
    if settings is None:
        settings = read_citation_index_settings(memory_root)
    return ExclusionRegister(
        rules=(
            *(
                ExclusionRule(source=EXCLUSION_SOURCE_PATH_RULES, pattern=one)
                for one in settings.excludes
            ),
            *(ExclusionRule(source=EXCLUSION_SOURCE_CALLER, pattern=one) for one in caller),
        ),
        gitignore_authority=gitignore_authority,
        gitignore_patterns=read_gitignore_patterns(code_root),
        settings_path=settings.settings_path,
        caps=settings.caps,
    )


def read_gitignore_patterns(code_root: Path) -> tuple[str, ...]:
    """The code root's own ``.gitignore`` lines, as written (comments and blanks removed).

    The patterns are recorded whether or not this register applies them: the record's job is
    to say which rule set was in force, and in a work tree the answer is "these, honoured by
    Git".
    """
    path = code_root / GITIGNORE_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return ()
    except OSError as error:
        raise SourceIndexError(
            f"citation index .gitignore {path} is unreadable: {error}"
        ) from error
    return tuple(
        line.rstrip("\r").rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


@dataclass(frozen=True)
class FallbackIgnoreMatcher:
    """The bounded ``.gitignore`` reading the non-Git fallback walk uses.

    ``has_negation`` is reported rather than hidden because it changes how the walk behaves: with
    a negation rule present a directory is never pruned, so the walk descends and decides per
    file. Pruning a directory a later ``!`` rule re-includes would be the silent omission this
    subset must not make.

    **A file inherits its ancestors' rules, and that is not optional.** `vendor/` is
    *directory-only*: it never matches the file `vendor/lib.py` directly, so a matcher that only
    tested the file against each rule would index every file under an ignored directory. The
    decision is therefore taken over the path's whole ancestor chain, last match wins, so a deeper
    rule beats a shallower one and `!vendor/keep.py` re-includes exactly that file while its
    siblings stay ignored. Dropping the ancestor walk is what made a negation silently disable
    directory-only exclusion (L14R-4).

    **This is the register's rule, not Git's, and the difference is deliberate.** Git does *not*
    re-include a file whose parent directory is excluded -- `git check-ignore vendor/keep.py`
    reports it ignored under `vendor/` plus `!vendor/keep.py`, because Git never descends into an
    excluded directory to find the negation. The register does re-include it, because the
    register's contract is different from Git's: it answers *"did the exclusion review's rules
    admit this file?"* rather than *"what would `git add` do?"*, and an operator who writes
    `!vendor/keep.py` beside `vendor/` has given an instruction whose only sensible reading is that
    one file is wanted. The direction is the safe one -- the register admits a file, it never
    silently drops one Git would have kept -- and it is pinned by
    ``test_the_register_admits_a_negated_file_under_an_excluded_directory_where_git_does_not``, so a
    future reader cannot mistake the divergence for an accident. The two acquisition routes stay
    consistent about the divergence: inside a Git work tree Git decides the population and this
    matcher is not consulted at all.
    """

    rules: tuple[GitIgnoreRule, ...] = ()

    @property
    def has_negation(self) -> bool:
        return any(one.negated for one in self.rules)

    def excludes(self, relative: str, *, is_directory: bool) -> bool:
        """Whether ``relative`` is ignored, over every path prefix from the root down."""
        decision = False
        for candidate in _ancestry(relative, is_directory=is_directory):
            for rule in self.rules:
                if rule.matches(candidate.path, is_directory=candidate.is_directory):
                    decision = not rule.negated
        return decision


@dataclass(frozen=True)
class _PathPrefix:
    """One path on the way to a file, and whether it names a directory."""

    path: str
    is_directory: bool


def _ancestry(relative: str, *, is_directory: bool) -> tuple[_PathPrefix, ...]:
    """The path itself plus every ancestor directory, shallowest first, so last match wins."""
    parts = [part for part in relative.rstrip("/").split("/") if part]
    if not parts:
        return ()
    prefixes = [
        _PathPrefix(path="/".join(parts[: index + 1]), is_directory=True)
        for index in range(len(parts) - 1)
    ]
    prefixes.append(_PathPrefix(path="/".join(parts), is_directory=is_directory))
    return tuple(prefixes)


def fallback_matcher(register: ExclusionRegister) -> FallbackIgnoreMatcher:
    """The register's ignore patterns as a matcher, for a root that is not a work tree."""
    text = "\n".join(register.gitignore_patterns)
    return FallbackIgnoreMatcher(rules=parse_gitignore(text))
