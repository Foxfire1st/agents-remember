"""The one supported earlier spelling of the Investigator role.

Normalize reads and new output identities; keep persisted values and paths intact.
"""

ROLE_ALIAS_NOTICE = (
    "Role 'system-specialist' was supplied; it names the same role as 'investigator'."
)


def canonical_role(role: str) -> str:
    return "investigator" if role == "system-specialist" else role


def role_spellings(role: str) -> tuple[str, ...]:
    canonical = canonical_role(role)
    return ("investigator", "system-specialist") if canonical == "investigator" else (role,)


ROLE_LEVELS = {
    "architect": "portfolio",
    "investigator": "portfolio",
    "orchestrator": "portfolio",
    "manager": "master",
    "worker": "leaf",
    "reviewer": "leaf",
    "curator": "leaf",
    # The free agent a new user reaches through the one start call (MIK-R196): it holds no
    # task altitude, so no per-level override applies to it and its role row is the whole
    # of its launch choices.
    "bootstrap": "free-agent",
}
# The roles whose execution is keyed by its request id rather than by a document: a
# selection with no document at all. ``bootstrap`` joins them as the identity-free first
# seat (MIK-R196 rule 1), not as a second identity of any other role.
TASKLESS_ROLES = frozenset({"architect", "investigator", "bootstrap"})
LEAF_ROLES = frozenset({"worker", "reviewer", "curator"})
