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
