"""Content-addressed pytest proof reuse for local quality-gate retries.

A full pytest pass can still be followed by a CRAP or changed-lines coverage
failure.  When the only subsequent repository changes are selected pytest test
modules, rerunning the entire already-passing suite is wasteful.  This module
keeps the successful test/coverage proof in the worktree's common Git directory
and prepares either:

* an exact-tree retry, which reuses the report without starting pytest; or
* a test-delta retry, which removes changed-test and collection contexts from
  the old Coverage.py data before the changed test modules append fresh data.

Every other change refuses reuse.  The manifest fingerprints the repository
bytes, selected tests, resolved diff base, Python/tool versions, invocation
environment, and measurement settings.  CI is always fresh.  Cache corruption,
missing context data, unsupported test arguments, or any ambiguity falls back
to the ordinary suite.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from coverage import CoverageData

from agents_remember.kernel import git_command

SCHEMA_VERSION = 1
CACHE_DIRECTORY = "agents-remember-quality-retry"
CACHED_CONTEXT = "agents-remember:unchanged-test-proof"
DISABLE_ENV = "AR_QUALITY_NO_RETRY"
CI_INVOCATION = "ci"

Printer = Callable[[str], None]


@dataclass(frozen=True)
class RetryInputs:
    """Stable inputs that decide whether a pytest proof can be reused."""

    project_root: Path
    targeted: bool
    base_revision: str
    threshold: float
    top: int
    diff_floor: float
    coverage_paths: tuple[Path, ...]
    test_arguments: tuple[Path, ...]
    test_roots: tuple[Path, ...]
    untracked_paths: tuple[Path, ...]


@dataclass
class RetryPlan:
    """One prepared run; mutable result fields are filled by the wrapper."""

    mode: str
    cache_dir: Path
    manifest_path: Path
    proof_data_path: Path
    proof_json_path: Path
    active_data_path: Path
    snapshot: dict[str, str]
    selected_tests: tuple[Path, ...]
    compatibility_key: str
    delta_tests: tuple[Path, ...] = ()
    pytest_passed: bool = False
    pytest_ran: bool = False
    _prepared: bool = field(default=False, repr=False)

    @property
    def exact(self) -> bool:
        return self.mode == "exact"

    @property
    def delta(self) -> bool:
        return self.mode == "delta"

    @property
    def pytest_test_paths(self) -> tuple[Path, ...] | None:
        return self.delta_tests if self.delta else None

    @property
    def append_coverage(self) -> bool:
        return self.delta

    def prepare_artifacts(self, coverage_json: Path) -> None:
        """Create the active data artifact or restore an exact proof."""
        self.active_data_path.unlink(missing_ok=True)
        if self.exact:
            shutil.copy2(self.proof_data_path, self.active_data_path)
            shutil.copy2(self.proof_json_path, coverage_json)
            self.pytest_passed = True
        elif self.delta:
            _filtered_coverage_data(
                self.proof_data_path,
                self.active_data_path,
                self.delta_tests,
            )
        self._prepared = True

    def record_pytest(self, return_code: int) -> None:
        self.pytest_ran = True
        self.pytest_passed = return_code == 0

    def prepare_full_fallback(self, coverage_json: Path) -> None:
        """Discard conservative delta data before a conclusive full rerun."""
        self.active_data_path.unlink(missing_ok=True)
        coverage_json.unlink(missing_ok=True)
        self.mode = "fresh"
        self.delta_tests = ()
        self.pytest_passed = False
        self.pytest_ran = False

    def finish(self, coverage_json: Path, *, quality_passed: bool) -> None:
        """Publish only a fresh full proof, or clear proof after success."""
        try:
            if quality_passed:
                _remove_proof(self)
            elif self.mode == "fresh" and self.pytest_passed:
                _publish_proof(self, coverage_json)
        finally:
            self.active_data_path.unlink(missing_ok=True)


def prepare(inputs: RetryInputs, *, printer: Printer) -> RetryPlan | None:
    """Return a fail-closed retry plan, or None when caching is unavailable."""
    disabled_reason = _disabled_reason(inputs)
    if disabled_reason is not None:
        printer(f"retry-proof: disabled ({disabled_reason})")
        return None
    try:
        plan = _fresh_plan(inputs)
    except (OSError, RuntimeError) as error:
        printer(f"retry-proof: unavailable ({error}); running fresh")
        return None
    if plan is None:
        printer("retry-proof: disabled (pytest selection has no concrete test modules)")
        return None
    manifest = _load_manifest(plan)
    if manifest is None:
        printer("retry-proof: no compatible prior pytest proof; running fresh")
        return plan
    return _reuse_plan(plan, manifest, inputs, printer)


def _fresh_plan(inputs: RetryInputs) -> RetryPlan | None:
    cache_dir = _cache_dir(inputs.project_root)
    selected = _selected_test_modules(inputs)
    if not selected:
        return None
    return RetryPlan(
        mode="fresh",
        cache_dir=cache_dir,
        manifest_path=cache_dir / "manifest.json",
        proof_data_path=cache_dir / "proof.coverage",
        proof_json_path=cache_dir / "proof.json",
        active_data_path=cache_dir / f"active-{os.getpid()}.coverage",
        snapshot=_repository_snapshot(inputs.project_root),
        selected_tests=selected,
        compatibility_key=_compatibility_key(inputs),
    )


def _reuse_plan(
    plan: RetryPlan,
    manifest: Mapping[str, object],
    inputs: RetryInputs,
    printer: Printer,
) -> RetryPlan:
    previous_snapshot = manifest["snapshot"]
    changed = _snapshot_delta(previous_snapshot, plan.snapshot)
    if not changed:
        plan.mode = "exact"
        printer("retry-proof: exact repository proof restored; pytest will not restart")
        return plan
    if not _selection_is_compatible(manifest, plan.selected_tests, changed):
        printer("retry-proof: selected pytest population changed ambiguously; running fresh")
        return plan
    delta_tests = _eligible_test_delta(
        changed, previous_snapshot, plan.selected_tests, inputs.test_roots
    )
    if delta_tests is None:
        printer("retry-proof: repository/config delta is not test-only; running fresh")
        return plan
    try:
        _validate_context_proof(plan.proof_data_path)
    except (OSError, RuntimeError) as error:
        printer(f"retry-proof: prior context proof is unusable ({error}); running fresh")
        return plan
    plan.mode = "delta"
    plan.delta_tests = delta_tests
    rendered = ", ".join(path.as_posix() for path in delta_tests)
    printer(f"retry-proof: test-only delta accepted; pytest input={rendered}")
    return plan


def _selection_is_compatible(
    manifest: Mapping[str, object],
    current: Sequence[Path],
    changed: Sequence[Path],
) -> bool:
    previous_value = manifest.get("selectedTests")
    if not isinstance(previous_value, list) or not all(
        isinstance(value, str) for value in previous_value
    ):
        return False
    previous = {Path(value) for value in previous_value}
    current_set = set(current)
    changed_set = set(changed)
    return not (previous - current_set) and (current_set - previous) <= changed_set


def _disabled_reason(inputs: RetryInputs) -> str | None:
    if os.environ.get(DISABLE_ENV):
        return f"{DISABLE_ENV} is set"
    if os.environ.get("AR_QUALITY_INVOCATION") == CI_INVOCATION:
        return "CI requires a fresh matrix proof"
    if inputs.untracked_paths:
        return "relevant untracked files are outside the content-addressed snapshot"
    if not inputs.coverage_paths:
        return "the run has no Coverage.py measurement scope"
    return None


def _cache_dir(project_root: Path) -> Path:
    completed = git_command.run_git(project_root, ["rev-parse", "--git-common-dir"])
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Git common directory is unavailable")
    common = Path(completed.stdout.strip())
    if not common.is_absolute():
        common = (project_root / common).resolve()
    worktree_key = hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:20]
    result = common / CACHE_DIRECTORY / worktree_key
    result.mkdir(parents=True, exist_ok=True)
    return result


def _selected_test_modules(inputs: RetryInputs) -> tuple[Path, ...]:
    tracked = _tracked_paths(inputs.project_root)
    selected: set[Path] = set()
    for argument in inputs.test_arguments:
        if _is_test_module(argument):
            selected.add(argument)
            continue
        selected.update(
            path for path in tracked if path.is_relative_to(argument) and _is_test_module(path)
        )
    return tuple(sorted(selected, key=lambda path: path.as_posix()))


def _tracked_paths(project_root: Path) -> tuple[Path, ...]:
    completed = git_command.run_git(project_root, ["-c", "core.quotePath=false", "ls-files", "-z"])
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "tracked file inventory is unavailable")
    return tuple(Path(value) for value in completed.stdout.split("\0") if value)


def _repository_snapshot(project_root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for relative in _tracked_paths(project_root):
        path = project_root / relative
        try:
            # Git stores a symlink's target text, not the bytes reachable through it.
            # Following a tracked directory symlink (dashboard/node_modules in this repo)
            # either fingerprints an external tree or raises IsADirectoryError, disabling
            # proof reuse exactly where local installs commonly expose that link.
            payload = (
                b"symlink\0" + os.fsencode(os.readlink(path))
                if path.is_symlink()
                else path.read_bytes()
            )
        except OSError as error:
            raise RuntimeError(
                f"could not fingerprint tracked input {relative}: {error}"
            ) from error
        snapshot[relative.as_posix()] = hashlib.sha256(payload).hexdigest()
    return snapshot


def _compatibility_key(inputs: RetryInputs) -> str:
    payload = {
        "schema": SCHEMA_VERSION,
        "targeted": inputs.targeted,
        "baseRevision": inputs.base_revision,
        "threshold": inputs.threshold,
        "top": inputs.top,
        "diffFloor": inputs.diff_floor,
        "coveragePaths": [path.as_posix() for path in inputs.coverage_paths],
        "python": {
            "implementation": sys.implementation.name,
            "version": platform.python_version(),
            "platform": platform.platform(),
        },
        "tools": {
            name: importlib.metadata.version(name) for name in ("coverage", "pytest", "pytest-cov")
        },
        "environment": _environment_digest(os.environ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _environment_digest(environment: Mapping[str, str]) -> str:
    # Store only the digest: test-affecting secrets remain secret while any value
    # change still invalidates the proof. COVERAGE_FILE is wrapper-owned per run.
    pairs = sorted((key, value) for key, value in environment.items() if key != "COVERAGE_FILE")
    encoded = json.dumps(pairs, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_manifest(plan: RetryPlan) -> dict[str, object] | None:
    try:
        raw = json.loads(plan.manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) and _manifest_is_valid(raw, plan) else None


def _manifest_is_valid(raw: Mapping[str, object], plan: RetryPlan) -> bool:
    snapshot = raw.get("snapshot")
    selected = raw.get("selectedTests")
    valid_snapshot = isinstance(snapshot, dict) and all(
        isinstance(key, str) and isinstance(value, str) for key, value in snapshot.items()
    )
    valid_selection = isinstance(selected, list) and all(
        isinstance(value, str) for value in selected
    )
    return bool(
        raw.get("schema") == SCHEMA_VERSION
        and raw.get("compatibilityKey") == plan.compatibility_key
        and valid_snapshot
        and valid_selection
        and _artifact_matches(plan.proof_data_path, raw.get("coverageDataSha256"))
        and _artifact_matches(plan.proof_json_path, raw.get("coverageJsonSha256"))
    )


def _artifact_matches(path: Path, expected: object) -> bool:
    return isinstance(expected, str) and path.is_file() and _file_digest(path) == expected


def _snapshot_delta(previous: object, current: Mapping[str, str]) -> tuple[Path, ...]:
    if not isinstance(previous, dict):
        return tuple(Path(path) for path in sorted(current))
    keys = set(previous) | set(current)
    return tuple(Path(path) for path in sorted(keys) if previous.get(path) != current.get(path))


def _eligible_test_delta(
    changed: Sequence[Path],
    previous_snapshot: object,
    selected_tests: Sequence[Path],
    test_roots: Sequence[Path],
) -> tuple[Path, ...] | None:
    if not isinstance(previous_snapshot, dict):
        return None
    selected = set(selected_tests)
    eligible: list[Path] = []
    for path in changed:
        # Deleted tests cannot be rerun, and support modules/conftest can affect
        # every test without owning a pytest context of their own.
        if path.as_posix() not in previous_snapshot and path in selected:
            eligible.append(path)
            continue
        if path not in selected or not _within_roots(path, test_roots) or not _is_test_module(path):
            return None
        eligible.append(path)
    return tuple(sorted(eligible, key=lambda path: path.as_posix()))


def _within_roots(path: Path, roots: Sequence[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _is_test_module(path: Path) -> bool:
    return path.suffix == ".py" and (
        path.name.startswith("test_") or path.name.endswith("_test.py")
    )


def _validate_context_proof(path: Path) -> None:
    data = CoverageData(basename=str(path))
    data.read()
    if not data.has_arcs():
        raise RuntimeError("branch coverage data is missing")
    if not any(context for context in data.measured_contexts()):
        raise RuntimeError("pytest test contexts are missing")


def _filtered_coverage_data(source: Path, destination: Path, changed: Sequence[Path]) -> None:
    """Keep only unchanged runtime contexts in a new branch-coverage database.

    The empty collection/import context is deliberately dropped.  That makes the
    retained proof a conservative subset: a delta can lose coverage and cause a
    fallback, but can never pass from collection work belonging to an edited test.
    """
    prior = CoverageData(basename=str(source))
    prior.read()
    if not prior.has_arcs():
        raise RuntimeError("cached Coverage.py data does not contain branch arcs")
    prefixes = "|".join(re.escape(path.as_posix()) for path in changed)
    allowed = rf"^(?!(?:{prefixes})::).+$"
    prior.set_query_contexts([allowed])
    arcs = {
        filename: measured
        for filename in sorted(prior.measured_files())
        if (measured := prior.arcs(filename))
    }
    destination.unlink(missing_ok=True)
    filtered = CoverageData(basename=str(destination))
    filtered.set_context(CACHED_CONTEXT)
    if arcs:
        filtered.add_arcs(arcs)
    filtered.write()


def _publish_proof(plan: RetryPlan, coverage_json: Path) -> None:
    if not plan.active_data_path.is_file() or not coverage_json.is_file():
        return
    try:
        _validate_context_proof(plan.active_data_path)
    except RuntimeError:
        return
    data_temp = plan.cache_dir / f"proof-{os.getpid()}.coverage.tmp"
    json_temp = plan.cache_dir / f"proof-{os.getpid()}.json.tmp"
    manifest_temp = plan.cache_dir / f"manifest-{os.getpid()}.json.tmp"
    shutil.copy2(plan.active_data_path, data_temp)
    shutil.copy2(coverage_json, json_temp)
    manifest = {
        "schema": SCHEMA_VERSION,
        "compatibilityKey": plan.compatibility_key,
        "snapshot": plan.snapshot,
        "selectedTests": [path.as_posix() for path in plan.selected_tests],
        "coverageDataSha256": _file_digest(data_temp),
        "coverageJsonSha256": _file_digest(json_temp),
    }
    manifest_temp.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    data_temp.replace(plan.proof_data_path)
    json_temp.replace(plan.proof_json_path)
    manifest_temp.replace(plan.manifest_path)


def _remove_proof(plan: RetryPlan) -> None:
    plan.manifest_path.unlink(missing_ok=True)
    plan.proof_data_path.unlink(missing_ok=True)
    plan.proof_json_path.unlink(missing_ok=True)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
