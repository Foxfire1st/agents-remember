from __future__ import annotations

from agents_remember.kernel.coordination_context.models import CrossRepoAllowEntry
from agents_remember.kernel.coordination_context.paths import clean_scalar

LEGACY_CROSS_REPO_REASON = (
    "legacy string crossRepo.allow entries are invalid for v2; expectedBranch is required"
)


def handle_cross_repo_line(parser, indent: int, stripped: str) -> None:
    if indent == 2 and stripped.startswith("allow:"):
        parser.saw_cross_repo = True
        parser.in_cross_repo_allow = True
        append_legacy_cross_repo_allow(parser, stripped.split(":", 1)[1].strip())
    elif indent == 4 and parser.in_cross_repo_allow and stripped.startswith("- "):
        append_legacy_cross_repo_entry(parser, clean_scalar(stripped[2:]))


def append_legacy_cross_repo_allow(parser, raw_value: str) -> None:
    if raw_value.startswith("[") and raw_value.endswith("]"):
        for value in raw_value[1:-1].split(","):
            append_legacy_cross_repo_entry(parser, clean_scalar(value))
    elif raw_value and raw_value != "[]":
        append_legacy_cross_repo_entry(parser, clean_scalar(raw_value))


def append_legacy_cross_repo_entry(parser, repo: str) -> None:
    if not repo:
        return
    parser.cross_repo.allow.append(
        CrossRepoAllowEntry(
            repo=repo,
            expected_branch="",
            state="excluded",
            reason=LEGACY_CROSS_REPO_REASON,
        )
    )
    parser.cross_repo.errors.append(LEGACY_CROSS_REPO_REASON)
