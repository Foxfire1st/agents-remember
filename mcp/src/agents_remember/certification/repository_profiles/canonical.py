"""Canonical bytes and digest authority for repository certification profiles."""

from __future__ import annotations

from agents_remember.certification.repository_profiles.models import (
    CanonicalRepositoryCertificationProfile,
    RepositoryCertificationProfile,
    _normalize_repository_profile,
    repository_profile_digest,
)
from agents_remember.kernel.generated_copies import DECLARATION_PATH, parse_generated_copies


def canonicalize_repository_profile(
    profile: RepositoryCertificationProfile,
) -> CanonicalRepositoryCertificationProfile:
    """Normalize one profile and require its declared digest to authorize those bytes."""

    normalized = _normalize_repository_profile(profile)
    digest = repository_profile_digest(normalized)
    if normalized.profileDigest != digest:
        raise ValueError(
            "repository certification profile digest does not match its canonical content"
        )
    return CanonicalRepositoryCertificationProfile(
        profile=normalized,
        profileDigest=digest,
    )


def project_generated_skills(
    profile: RepositoryCertificationProfile,
    declaration: bytes,
) -> RepositoryCertificationProfile:
    """Derive the skill input alone; unrelated profile authority stays literal."""
    canonicalize_repository_profile(profile)
    projections = parse_generated_copies(declaration)
    scopes = tuple(
        sorted(
            target.path for one in projections if one.source == "skills" for target in one.targets
        )
    )
    if not scopes:
        raise ValueError("the declaration holds no generated skills")
    inputs = tuple(
        one.model_copy(
            update={
                "generatedScopes": scopes,
                "sourceScopes": tuple(sorted({*one.sourceScopes, DECLARATION_PATH})),
            }
        )
        if one.inputId == "generated-skills"
        else one
        for one in profile.generatedInputs
    )
    projected = profile.model_copy(update={"generatedInputs": inputs})
    return projected.model_copy(update={"profileDigest": repository_profile_digest(projected)})


__all__ = [
    "canonicalize_repository_profile",
    "project_generated_skills",
    "repository_profile_digest",
]
