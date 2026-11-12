"""MIK-R55 rule 2: exact B/C equality, among uncovered paths only.

Distinct cards keep distinct judgments. Cardless copies join the first card, or the first
path when none has a card. Coverage is supplied by its K_B owner, never inferred here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agents_remember.application.knowledge_worklist.code import CodeTrees


def equal_change_groups(
    code: CodeTrees, uncovered: Iterable[str], cards: Iterable[str]
) -> dict[str, list[str]]:
    before, after = code.base(), code.candidate()
    card_set = set(cards)
    equal: dict[tuple[str | None, str | None], list[str]] = {}
    for path in sorted(set(uncovered)):
        if path not in before and path not in after:
            continue  # no blob pair (e.g. submodules): not an identical blob change
        equal.setdefault((before.get(path), after.get(path)), []).append(path)
    groups: dict[str, list[str]] = {}
    for paths in equal.values():
        owners = [path for path in paths if path in card_set]
        representative = owners[0] if owners else paths[0]
        members = [path for path in paths if path == representative or path not in card_set]
        if len(members) > 1:
            groups[representative] = members
    return dict(sorted(groups.items()))


def group_representatives(groups: Mapping[str, Iterable[str]]) -> dict[str, str]:
    return {path: representative for representative, paths in groups.items() for path in paths}
