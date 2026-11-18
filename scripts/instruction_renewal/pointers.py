"""Renew wording bound to the hand-owned pointer exception identities."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from agents_remember.errors import AgentsRememberError
from agents_remember_test_support.testing.manifest_step_derivation import LINK, sentence_start
from agents_remember_test_support.testing.pointer_sentences import action as pointer_action
from agents_remember_test_support.testing.pointer_sentences import resolve_target

_LIFECYCLE = Path("skills/l-01-agent-lifecycles")
_RENEW = "mcp/.venv/bin/python scripts/renew-instructions.py"


@dataclass(frozen=True)
class Sentence:
    """One current source sentence reaching a target section."""

    source: str
    line: int
    target: str
    fragment: str
    wording: str
    text: str

    @property
    def group(self) -> tuple[str, str, str]:
        return self.source, self.target, self.fragment


def _sentences(root: Path, policy: dict) -> list[Sentence]:
    wanted: dict[str, set[str]] = defaultdict(set)
    for target, actions in policy["actionSources"].items():
        for sources in actions.values():
            for source in sources:
                wanted[source].add(target)
    found: list[Sentence] = []
    for source, targets in sorted(wanted.items()):
        found.extend(_source_sentences(root, source, targets))
    return found


def _source_sentences(root: Path, source: str, targets: set[str]) -> list[Sentence]:
    found: list[Sentence] = []
    fence = False
    for number, line in enumerate((root / _LIFECYCLE / source).read_text().splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        seen: set[tuple[int, str, str]] = set()
        for match in LINK.finditer(line):
            target = resolve_target(match[1], source)
            if target is None or target not in targets:
                continue
            begin = 0 if line.lstrip().startswith("|") else sentence_start(line, match.start())
            fragment = match[1].partition("#")[2]
            identity = (begin, target, fragment)
            if identity in seen:
                continue
            seen.add(identity)
            found.append(
                Sentence(
                    source, number, target, fragment, pointer_action(line, *match.span()), line
                )
            )
    return found


def _policy_uses(policy: dict) -> dict[str, list[str]]:
    uses: dict[str, list[str]] = defaultdict(list)
    for target, entry in policy.get("distinctActions", {}).items():
        for index, action in enumerate(entry["actions"]):
            uses[action].append(f"distinctActions[{json.dumps(target)}].actions[{index}]")
    for target, entries in policy.get("multiLinkSentences", {}).items():
        for index, entry in enumerate(entries):
            uses[entry["action"]].append(
                f"multiLinkSentences[{json.dumps(target)}][{index}].action"
            )
    for group, actions in enumerate(policy.get("exclusive", [])):
        for index, action in enumerate(actions):
            uses[action].append(f"exclusive[{group}][{index}]")
    return uses


def _targets(policy: dict) -> dict[str, str]:
    targets = {
        action: target for target, actions in policy["actionSources"].items() for action in actions
    }
    missing = {
        action: places for action, places in _policy_uses(policy).items() if action not in targets
    }
    if missing:
        details = "\n".join(
            f"  {action}: {', '.join(places)}" for action, places in sorted(missing.items())
        )
        raise AgentsRememberError(
            "Withdrawn pointer admissions still occur in authored pointer-actions.json:\n"
            f"{details}\nRemove each ID from every named authored place or restore its "
            f"actionSources admission, then run {_RENEW}; never edit generated wording."
        )
    return targets


def _explicit_bindings(
    bindings: tuple[str, ...], policy: dict, current: list[Sentence]
) -> dict[tuple[str, str, str], dict[str, Sentence]]:
    targets = _targets(policy)
    chosen: dict[tuple[str, str, str], dict[str, Sentence]] = defaultdict(dict)
    for binding in bindings:
        action, separator, location = binding.partition("=")
        source, colon, line = location.rpartition(":")
        source = source.removeprefix(_LIFECYCLE.as_posix() + "/")
        if not separator or not colon or not line.isdecimal() or action not in targets:
            raise AgentsRememberError(
                f"Invalid pointer binding {binding!r}; use ACTION_ID=FILE:LINE."
            )
        target = targets[action]
        if source not in policy["actionSources"][target][action]:
            raise AgentsRememberError(
                f"Pointer {action}: source {source!r} is not admitted by policy."
            )
        candidates = [
            sentence
            for sentence in current
            if sentence.source == source
            and sentence.line == int(line)
            and sentence.target == target
        ]
        if not candidates or len({sentence.wording for sentence in candidates}) != 1:
            raise AgentsRememberError(
                f"Pointer {action}: {location} names no unique admitted sentence."
            )
        for sentence in candidates:
            previous = chosen[sentence.group].get(action)
            if previous is not None and previous != sentence:
                raise AgentsRememberError(
                    f"Pointer {action}: conflicting explicit bindings in {source}."
                )
            chosen[sentence.group][action] = sentence
    return chosen


def _held_groups(held: dict, targets: dict[str, str]) -> dict:
    groups: dict[tuple[str, str, str], dict[str, str]] = defaultdict(dict)
    for action, rows in held["actions"].items():
        if action not in targets:
            continue
        for row in rows:
            groups[row["source"], targets[action], row["fragment"]][action] = row["wording"]
    return groups


def _problem(
    group: tuple[str, str, str], ids: set[str], sentences: list[Sentence]
) -> AgentsRememberError:
    source, target, fragment = group
    details = "\n".join(f"  {source}:{item.line}: {item.text}" for item in sentences) or "  none"
    return AgentsRememberError(
        f"Pointer binding unresolved in {source} -> {target}#{fragment}. "
        f"IDs: {', '.join(sorted(ids)) or 'none'}. Current sentences:\n{details}\n"
        f"Name each intended binding with {_RENEW} --bind-pointer ACTION_ID="
        f"skills/l-01-agent-lifecycles/{source}:LINE; admissions stay hand-owned. "
        "If an admission was withdrawn, remove its source pointer or revise the "
        "authored pointer-actions.json admission; never edit generated wording."
    )


def _match_group(
    group: tuple[str, str, str], held: dict[str, str], current: list[Sentence], explicit: dict
) -> dict[str, Sentence]:
    chosen: dict[str, Sentence] = dict(explicit)
    if len(set(chosen.values())) != len(chosen):
        raise _problem(group, set(held) | set(chosen), current)
    remaining = [sentence for sentence in current if sentence not in chosen.values()]
    for action, wording in held.items():
        if action in chosen:
            continue
        matches = [sentence for sentence in remaining if sentence.wording == wording]
        if len(matches) == 1:
            chosen[action] = matches[0]
            remaining.remove(matches[0])
    unresolved = set(held) - set(chosen)
    if len(current) == len(held) and len(unresolved) == len(remaining) == 1:
        chosen[unresolved.pop()] = remaining.pop()
    if unresolved or remaining or set(held) - set(chosen):
        raise _problem(group, unresolved or set(held) | set(chosen), remaining or current)
    if len(current) != len(held) and not explicit:
        raise _problem(group, set(held), current)
    return chosen


def plan(root: Path, bindings: tuple[str, ...]) -> tuple[dict, list[str]]:
    """Match held wording, then only an unambiguous sole residual; never infer admissions."""
    folder = root / _LIFECYCLE
    policy = json.loads((folder / "pointer-actions.json").read_text())
    held = json.loads((folder / "pointer-actions-derived.json").read_text())
    targets = _targets(policy)
    groups = _held_groups(held, targets)
    current: dict[tuple[str, str, str], list[Sentence]] = defaultdict(list)
    sentences = _sentences(root, policy)
    for sentence in sentences:
        current[sentence.group].append(sentence)
    explicit = _explicit_bindings(bindings, policy, sentences)
    result: dict[str, list[dict]] = {action: [] for action in targets}
    notices: list[str] = []
    for group in sorted(set(groups) | set(current) | set(explicit)):
        matched = _match_group(
            group, groups.get(group, {}), current[group], explicit.get(group, {})
        )
        for action, sentence in matched.items():
            result[action].append(
                {
                    "source": sentence.source,
                    "fragment": sentence.fragment,
                    "wording": sentence.wording,
                }
            )
            if groups.get(group, {}).get(action) != sentence.wording:
                notices.append(f"Pointer {action}: bound {sentence.source}:{sentence.line}.")
    for rows in result.values():
        rows.sort(key=lambda row: (row["source"], row["fragment"], row["wording"]))
    return {"actions": result}, notices
