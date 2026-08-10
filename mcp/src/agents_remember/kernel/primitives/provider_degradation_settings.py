"""Settings for the provider degradation detector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.errors import AgentsRememberError

KNOWN_PROVIDER_DEGRADATION_FIELDS = frozenset(
    {
        "enabled",
        "failSafeEnabled",
        "memoryDegradedRatio",
        "memoryCriticalRatio",
        "degradedSamples",
        "criticalSamples",
        "healthySamples",
        "watcherLagDegradedCommits",
        "watcherLagCriticalCommits",
        "watcherLagDegradedMinutes",
        "watcherLagCriticalMinutes",
        "probeDegradedMs",
        "probeCriticalMs",
        "setupFailureDegradedStreak",
        "setupFailureCriticalStreak",
        "recentSampleLimit",
    }
)


class ProviderDegradationSettingsError(AgentsRememberError):
    """Raised when provider degradation settings are malformed."""


@dataclass(frozen=True)
class ProviderDegradationSettings:
    """Provider-only degradation detector thresholds."""

    enabled: bool = True
    fail_safe_enabled: bool = True
    memory_degraded_ratio: float = 0.80
    memory_critical_ratio: float = 0.92
    degraded_samples: int = 3
    critical_samples: int = 2
    healthy_samples: int = 3
    watcher_lag_degraded_commits: int = 5
    watcher_lag_critical_commits: int = 20
    watcher_lag_degraded_minutes: int = 10
    watcher_lag_critical_minutes: int = 30
    probe_degraded_ms: int = 2_000
    probe_critical_ms: int = 10_000
    setup_failure_degraded_streak: int = 2
    setup_failure_critical_streak: int = 3
    recent_sample_limit: int = 120


def parse_provider_degradation_settings(raw: object) -> ProviderDegradationSettings:
    """Parse the optional ``providerDegradation`` authority block."""

    if raw is None:
        return ProviderDegradationSettings()
    if not isinstance(raw, dict):
        raise ProviderDegradationSettingsError("providerDegradation settings must be an object")
    unknown = sorted(set(raw) - KNOWN_PROVIDER_DEGRADATION_FIELDS)
    if unknown:
        allowed = ", ".join(sorted(KNOWN_PROVIDER_DEGRADATION_FIELDS))
        unknown_text = ", ".join(unknown)
        raise ProviderDegradationSettingsError(
            f"unsupported providerDegradation setting(s): {unknown_text}; allowed: {allowed}"
        )
    defaults = ProviderDegradationSettings()
    return ProviderDegradationSettings(
        enabled=_bool_setting(raw, "enabled", defaults.enabled),
        fail_safe_enabled=_bool_setting(
            raw,
            "failSafeEnabled",
            defaults.fail_safe_enabled,
        ),
        memory_degraded_ratio=_ratio_setting(
            raw,
            "memoryDegradedRatio",
            defaults.memory_degraded_ratio,
        ),
        memory_critical_ratio=_ratio_setting(
            raw,
            "memoryCriticalRatio",
            defaults.memory_critical_ratio,
        ),
        degraded_samples=_positive_setting(raw, "degradedSamples", defaults.degraded_samples),
        critical_samples=_positive_setting(raw, "criticalSamples", defaults.critical_samples),
        healthy_samples=_positive_setting(raw, "healthySamples", defaults.healthy_samples),
        watcher_lag_degraded_commits=_positive_setting(
            raw,
            "watcherLagDegradedCommits",
            defaults.watcher_lag_degraded_commits,
        ),
        watcher_lag_critical_commits=_positive_setting(
            raw,
            "watcherLagCriticalCommits",
            defaults.watcher_lag_critical_commits,
        ),
        watcher_lag_degraded_minutes=_positive_setting(
            raw,
            "watcherLagDegradedMinutes",
            defaults.watcher_lag_degraded_minutes,
        ),
        watcher_lag_critical_minutes=_positive_setting(
            raw,
            "watcherLagCriticalMinutes",
            defaults.watcher_lag_critical_minutes,
        ),
        probe_degraded_ms=_positive_setting(raw, "probeDegradedMs", defaults.probe_degraded_ms),
        probe_critical_ms=_positive_setting(raw, "probeCriticalMs", defaults.probe_critical_ms),
        setup_failure_degraded_streak=_positive_setting(
            raw,
            "setupFailureDegradedStreak",
            defaults.setup_failure_degraded_streak,
        ),
        setup_failure_critical_streak=_positive_setting(
            raw,
            "setupFailureCriticalStreak",
            defaults.setup_failure_critical_streak,
        ),
        recent_sample_limit=_positive_setting(
            raw, "recentSampleLimit", defaults.recent_sample_limit
        ),
    )


def _bool_setting(raw: dict[str, Any], key: str, default: bool) -> bool:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, bool):
        raise ProviderDegradationSettingsError(f"providerDegradation.{key} must be a boolean")
    return value


def _positive_setting(raw: dict[str, Any], key: str, default: int) -> int:
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProviderDegradationSettingsError(
            f"providerDegradation.{key} must be a positive integer"
        )
    return value


def _ratio_setting(raw: dict[str, Any], key: str, default: float) -> float:
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
        raise ProviderDegradationSettingsError(
            f"providerDegradation.{key} must be a number in (0, 1]"
        )
    return float(value)
