"""Credential observations and declarations, without emitting credential material."""

from __future__ import annotations

from pathlib import Path

from .adapters.execution import HERMES_GPT_ACTOR, LaunchProfile, actor_profile
from .artifacts import write_object
from .auth_observation import AuthObservation, AuthObservationError
from .codex_access import AccessOnlyError, validate_access_pool
from .trial_isolation import credential_borrowing


def record_auth_comparison(
    report: dict, profile: LaunchProfile, auth: AuthObservation | None
) -> bool:
    if auth is None:
        return True
    try:
        comparison = auth.compare()
    except AuthObservationError as error:
        report["unknown_class"] = "binding-unavailable"
        report["unknowns"].append(f"binding-unavailable: {error}")
        return False
    report["authContentComparison"] = comparison
    if comparison == "differs" and profile == HERMES_GPT_ACTOR:
        finding = "real Hermes auth content differs"
        report["authContentFinding"] = finding
        report["unknown_class"] = "binding-unavailable"
        report["unknowns"].append(f"binding-unavailable: {finding}")
        return False
    return True


def declare_credentials(
    report: dict, evidence: Path, profile: LaunchProfile, source_home: Path
) -> None:
    declaration = credential_borrowing(
        source_home,
        profile=profile if profile.harness == "hermes" else actor_profile("hermes"),
    )
    report["credentialBorrowing"] = declaration
    write_object(evidence / "credential-borrowing.json", declaration)


def actor_access_deadline(report: dict, profile: LaunchProfile, home: Path | None) -> float | None:
    if profile != HERMES_GPT_ACTOR:
        return None
    if home is None:
        raise AccessOnlyError("private Hermes home missing")
    expected_end = report.get("accessExpectedEnd")
    if not isinstance(expected_end, (int, float)) or isinstance(expected_end, bool):
        raise AccessOnlyError("actor expected end missing or invalid")
    report["accessOnlyPreflight"] = validate_access_pool(home, expected_end)
    return expected_end
