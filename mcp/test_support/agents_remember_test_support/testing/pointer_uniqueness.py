"""One pointer per action per native first message (L84 P5-18).

A pointer is any Markdown link outside a fence whose target resolves to a skill file:
``{{detail:F}}#a``, the served ``skill://…/l-01-agent-lifecycles/F#a`` form, a relative path,
or a same-file ``#a``. Its key is the target *file*: a whole-file link and a section link into
one file, or links to two of its sections, reach the same file. Links into one file from one
sentence (or one table row) count once. A pointer's action is its sentence's opening
Before/When/For clause (``manifest_step_derivation.action_of``); otherwise the sentence text
before the link, a table row's first cell, or, for a sentence that opens with the link, the
text after it. Every link counts, whatever its sentence's opener.

In one first message (every instruction unit of the compiled capsule plus the shared
``core/every-role.md`` block), a file reached from two or more sentences is a duplicate
unless every action is different and each is listed below for that file with the reason it
is a different action, from the source file it is listed for. The same action twice is never
admitted, nor one action under two wordings, nor a listed action re-added in another source.
"""

from __future__ import annotations

import re
from collections import defaultdict

from agents_remember_test_support.testing.manifest_step_derivation import (
    LINK,
    sentence_start,
)
from agents_remember_test_support.testing.pointer_policy import wording_policy
from agents_remember_test_support.testing.pointer_sentences import action as _action
from agents_remember_test_support.testing.pointer_sentences import resolve_target as _resolve

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

DISTINCT_ACTIONS, ACTION_SOURCES, MULTI_LINK_SENTENCES, _EXCLUSIVE = wording_policy()


def pointers(text: str, source: str) -> list[tuple[str, str]]:
    """(target file, action) once per sentence and file, fences excluded."""
    found: list[tuple[str, str]] = []
    fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        seen: set[tuple[int, str]] = set()
        row = line.lstrip().startswith("|")
        for match in LINK.finditer(line):
            target = _resolve(match.group(1), source)
            if target is None:
                continue
            begin = 0 if row else sentence_start(line, match.start())
            if (begin, target) in seen:
                continue
            seen.add((begin, target))
            found.append((target, _action(line, match.start(), match.end())))
    return found


def multi_link_sentences(text: str, source: str) -> list[tuple[str, str]]:
    """(target file, action) for each sentence that links one file two or more times."""
    found: list[tuple[str, str]] = []
    fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        counts: dict[tuple[int, str], int] = defaultdict(int)
        first: dict[tuple[int, str], tuple[int, int]] = {}
        row = line.lstrip().startswith("|")
        for match in LINK.finditer(line):
            target = _resolve(match.group(1), source)
            if target is None:
                continue
            key = (0 if row else sentence_start(line, match.start()), target)
            counts[key] += 1
            first.setdefault(key, (match.start(), match.end()))
        for key, count in counts.items():
            if count > 1:
                found.append((key[1], _action(line, *first[key])))
    return found


def duplicate_pointers(units: list[tuple[str, str]]) -> list[str]:
    """Problems for one first message given its (source path, text) units."""
    by_file: dict[str, list[str]] = defaultdict(list)
    sources: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for source, text in units:
        for target, action in pointers(text, source):
            by_file[target].append(action)
            sources[target].add((source, action))
    problems = [
        f"{target}: one sentence links it twice, unlisted {action!r}"
        for source, text in units
        for target, action in multi_link_sentences(text, source)
        if (source, action) not in MULTI_LINK_SENTENCES.get(target, frozenset())
    ]
    for target, actions in sorted(by_file.items()):
        if len(actions) < 2:
            continue
        admitted, _reason = DISTINCT_ACTIONS.get(target, (frozenset(), ""))
        if len(set(actions)) != len(actions):
            problems.append(f"{target}: same action twice {sorted(actions)}")
        elif not set(actions) <= admitted:
            problems.append(f"{target}: unlisted repeat {sorted(set(actions) - admitted)}")
        elif any(len(set(actions) & pair) > 1 for pair in _EXCLUSIVE):
            problems.append(f"{target}: one action under two wordings {sorted(actions)}")
        elif foreign := sorted(
            (source, action)
            for source, action in sources[target]
            if source not in ACTION_SOURCES.get(target, {}).get(action, frozenset())
        ):
            problems.append(f"{target}: listed action from another source {foreign}")
    return problems
