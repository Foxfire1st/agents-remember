"""MIK-R199 round-1 finding L192-F03: the projection watch plan must not subscribe to retired
provider display state.

Provider display reading was removed, but ``logs/providers/status`` and ``logs/providers/setup``
still entered ``projection_input_roots`` and had no entry in ``projection_domains_for_paths`` -- so
an admitted event under them fell open to *every* projection domain and rebuilt projections from
state nothing reads. Those two roots are gone from the plan; the genuine task / observer-workspace /
drift / start-progress / tool-report roots keep their own domain mapping, and the independent
provider-safety sampler keeps its loop and needs no watch root.

The first case fails on the pre-repair candidate (the provider roots are present whenever the
directories exist); the rest are controls that must keep passing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agents_remember.providers import degradation, metrics
from agents_remember.serving import change_watcher as watcher
from agents_remember.serving.projections import paths as projection_paths
from test_paseo_role_agents import runtime_config

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def _coordinated(tmp_path: Path):
    config = runtime_config(tmp_path, None)
    root = config.coordination_root
    for relative in (
        "tasks",
        "temp/worktree-start",
        "temp/tool-reports",
        "logs/observer/workspace",
        "logs/observer/drift",
        # the retired provider display surfaces, present on disk so a plan that still lists
        # them would return them
        "logs/providers/status",
        "logs/providers/setup",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return config, root


def test_retired_provider_display_roots_are_not_projection_inputs(tmp_path: Path) -> None:
    config, root = _coordinated(tmp_path)

    roots = watcher.projection_input_roots(config)

    providers = root / "logs" / "providers"
    assert not [path for path in roots if path == providers or providers in path.parents], (
        f"a retired provider display root is still watched: {roots}"
    )


def test_genuine_projection_roots_keep_their_own_domain(tmp_path: Path) -> None:
    config, root = _coordinated(tmp_path)
    roots = watcher.projection_input_roots(config)

    expected = {
        root / "tasks": watcher.ProjectionDomain.TASKS,
        root / "logs" / "observer" / "workspace": watcher.ProjectionDomain.WORKSPACE,
        projection_paths.drift_snapshot_dir(root): watcher.ProjectionDomain.DRIFT,
        root / "temp" / "worktree-start": watcher.ProjectionDomain.START_PROGRESS,
        root / "temp" / "tool-reports": watcher.ProjectionDomain.TOOL_REPORTS,
    }
    for path, domain in expected.items():
        assert path in roots, f"{path} is no longer watched"
        assert watcher.projection_domains_for_paths(
            config, {str(path / "event.json")}
        ) == frozenset({domain}), f"{path} no longer invalidates exactly {domain}"


def test_the_independent_safety_sampler_survives_the_watch_plan_change() -> None:
    # The watch plan is display-only: the retained periodic sample / degrade / stop / store loop
    # is a separate caller and must still be present and importable.
    assert hasattr(metrics, "ProviderMetricsStore")
    assert hasattr(metrics, "sample_provider_containers")
    assert hasattr(degradation, "ProviderDegradationStore")
    assert hasattr(degradation, "evaluate_provider_degradation")
