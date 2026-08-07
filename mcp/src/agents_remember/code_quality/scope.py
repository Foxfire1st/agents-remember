"""Derive and validate the source-quality wrapper's repository scope."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.kernel import git_command


class ScopeError(RuntimeError):
    """The gate could not work out what it is supposed to certify."""


@dataclass(frozen=True)
class GateScope:
    """The concrete paths each quality rail receives.

    The first four fields retain the wrapper's established public shape. ``scope_roots``
    names the directories where an untracked sibling would join a rail once added, and
    ``untracked_paths`` records the non-ignored files currently omitted by index and diff
    enumeration. Reporting those files does not add them to any measurement.
    """

    lint_paths: list[Path]
    type_paths: list[Path]
    coverage_paths: list[Path]
    test_paths: list[Path]
    size_paths: list[Path] = field(default_factory=list)
    scope_roots: list[Path] = field(default_factory=list)
    untracked_paths: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class DashboardBuildInputs:
    panda_include: tuple[str, ...]
    vite_inputs: tuple[Path, ...]


def git_ls_files(project_root: Path, *patterns: str) -> list[Path]:
    """Tracked paths matching ``patterns``, relative to ``project_root``."""
    arguments = ["ls-files", "-z", "--", *patterns]
    failed = f"could not list tracked files (git {' '.join(arguments)})"
    try:
        completed = git_command.run_git(project_root, arguments)
    except (OSError, subprocess.SubprocessError) as error:
        raise ScopeError(f"{failed}: {error}") from error
    if completed.returncode != 0:
        raise ScopeError(f"{failed}: exit {completed.returncode}: {completed.stderr.strip()}")
    return [Path(entry) for entry in completed.stdout.split("\0") if entry]


def git_untracked_files(project_root: Path, roots: list[Path]) -> list[Path]:
    """Non-ignored untracked files below ``roots``, preserving all path characters."""
    arguments = [
        "ls-files",
        "-z",
        "--others",
        "--exclude-standard",
        "--",
        *(root.as_posix() for root in roots),
    ]
    failed = "could not enumerate non-ignored untracked files inside the quality scope"
    try:
        completed = git_command.run_git(project_root, arguments)
    except (OSError, subprocess.SubprocessError) as error:
        raise ScopeError(f"{failed}: {error}") from error
    if completed.returncode != 0:
        raise ScopeError(f"{failed}: exit {completed.returncode}: {completed.stderr.strip()}")
    return sorted(Path(entry) for entry in completed.stdout.split("\0") if entry)


def top_level_packages(tracked: list[Path]) -> list[Path]:
    """Tracked importable packages whose parent is not itself a package."""
    packages = {path.parent for path in tracked if path.name == "__init__.py"}
    return sorted(
        package
        for package in packages
        if package.parent == package or package.parent not in packages
    )


def toml_section(data: Mapping[str, object], keys: tuple[str, ...]) -> Mapping[str, object]:
    current: object = data
    for key in keys:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key, {})
    return current if isinstance(current, Mapping) else {}


def read_pyproject(project_root: Path) -> tuple[Path, Mapping[str, object]]:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        raise ScopeError(f"no pyproject.toml at {pyproject}; the gate cannot resolve its config")
    try:
        with pyproject.open("rb") as handle:
            return pyproject, tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ScopeError(f"could not parse {pyproject}: {error}") from error


def pytest_testpaths(project_root: Path) -> list[Path]:
    """Where the suite lives, read from pytest's own declaration."""
    pyproject, data = read_pyproject(project_root)
    testpaths = toml_section(data, ("tool", "pytest", "ini_options")).get("testpaths")
    if not isinstance(testpaths, list) or not testpaths:
        raise ScopeError(
            "[tool.pytest.ini_options] testpaths is missing or empty in "
            f"{pyproject}; the gate refuses to guess where the suite lives"
        )
    return [Path(str(entry)) for entry in testpaths]


def file_size_armed(project_root: Path) -> bool:
    """Whether the wrapper's file-size rail fails the run on a violation.

    Read from ``[tool.agents_remember] file_size_armed``. The key is deliberately
    explicit: the check is wired into the wrapper first, and a repo only starts
    failing on it after the tree has been remediated and the owner flips the key.
    Absent means unarmed, so the wrapper keeps working in repositories that have
    not adopted the arming switch yet.
    """
    _path, data = read_pyproject(project_root)
    section = toml_section(data, ("tool", "agents_remember"))
    value = section.get("file_size_armed", False)
    if not isinstance(value, bool):
        raise ScopeError(
            f"[tool.agents_remember] file_size_armed must be a boolean; got {type(value).__name__}"
        )
    return value


def validate_quality_config(project_root: Path) -> None:
    """Refuse missing or inert configuration used by an ordinary wrapper run."""
    pyproject, data = read_pyproject(project_root)
    required = (
        ("tool.ruff", ("tool", "ruff")),
        ("tool.pyright", ("tool", "pyright")),
        ("tool.radon", ("tool", "radon")),
        ("tool.coverage.run", ("tool", "coverage", "run")),
        ("tool.pytest.ini_options", ("tool", "pytest", "ini_options")),
    )
    findings: list[str] = []
    for label, keys in required:
        if not toml_section(data, keys):
            findings.append(
                f"[{label}] is missing or empty; add the project-owned settings to {pyproject}"
            )

    coverage = toml_section(data, ("tool", "coverage", "run"))
    if coverage and coverage.get("branch") is not True:
        findings.append(
            "[tool.coverage.run] branch must be true; set it to true because CRAP requires "
            "branch data"
        )

    pyright = toml_section(data, ("tool", "pyright"))
    if pyright:
        includes = pyright.get("include")
        if not isinstance(includes, list) or not includes:
            findings.append(
                "[tool.pyright] include is missing or empty; declare the checkout scope it "
                "must type-check"
            )
        try:
            validate_pyright_venv(project_root, pyright, pyproject)
        except ScopeError as error:
            findings.append(str(error))

    test_paths: list[Path] = []
    try:
        test_paths = pytest_testpaths(project_root)
    except ScopeError as error:
        findings.append(str(error))
    if test_paths and not python_files_under(project_root, test_paths):
        findings.append(
            f"configured pytest testpaths contain zero Python files: "
            f"{', '.join(path.as_posix() for path in test_paths)}; add the missing tests or "
            "correct testpaths"
        )
    if findings:
        rendered = "\n".join(f"  - {finding}" for finding in findings)
        raise ScopeError(f"quality configuration has {len(findings)} finding(s):\n{rendered}")


def validate_pyright_venv(
    project_root: Path,
    pyright: Mapping[str, object],
    pyproject: Path,
) -> None:
    """Reject a declared virtual environment that cannot resolve in this checkout."""
    venv_path = pyright.get("venvPath")
    venv = pyright.get("venv")
    if venv_path is None and venv is None:
        return
    if not isinstance(venv_path, str) or not isinstance(venv, str):
        raise ScopeError(
            f"[tool.pyright] venvPath and venv must either both be strings or both be absent in "
            f"{pyproject}"
        )
    resolved = (project_root / venv_path / venv).resolve()
    if not resolved.is_dir():
        raise ScopeError(
            f"[tool.pyright] venvPath/venv resolves to missing directory {resolved}; "
            "repair the declaration or remove it when --pythonpath owns interpreter resolution"
        )


def path_is_within(path: Path, root: Path) -> bool:
    if root == Path("."):
        return True
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def derive_scope_roots(
    project_root: Path,
    tracked: list[Path],
    coverage_paths: list[Path],
    test_paths: list[Path],
) -> list[Path]:
    """Roots where an untracked sibling is relevant to an existing quality rail."""
    roots = set(coverage_paths) | set(test_paths)
    measured_roots = coverage_paths + test_paths
    for path in tracked:
        if any(path_is_within(path, root) for root in measured_roots):
            continue
        roots.add(Path(path.parts[0]) if len(path.parts) > 1 else Path("."))
    if (project_root / "dashboard" / "package.json").is_file():
        roots.add(Path("dashboard"))
    return sorted(roots, key=lambda path: path.as_posix())


def python_files_under(project_root: Path, roots: list[Path]) -> list[Path]:
    """Python files currently present below configured roots, including untracked ones."""
    found: set[Path] = set()
    for root in roots:
        absolute = project_root / root
        if absolute.is_file() and absolute.suffix == ".py":
            found.add(root)
        elif absolute.is_dir():
            found.update(path.relative_to(project_root) for path in absolute.rglob("*.py"))
    return sorted(found)


def eslint_result_files(dashboard: Path) -> list[Path]:
    """The exact result set resolved by the dashboard's installed ESLint."""
    executable = dashboard / "node_modules" / ".bin" / "eslint"
    if not executable.is_file():
        raise ScopeError(
            f"ESLint executable is missing at {executable}; run npm ci before reporting lint scope"
        )
    completed = subprocess.run(
        [executable.as_posix(), ".", "--format", "json"],
        cwd=dashboard,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ScopeError(
            f"ESLint could not resolve its machine-readable result set "
            f"(exit {completed.returncode}): {detail}"
        )
    try:
        rows = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ScopeError(
            f"ESLint returned invalid JSON while resolving lint scope: {error}"
        ) from error
    if not isinstance(rows, list):
        raise ScopeError("ESLint machine-readable result is not a list; cannot report lint scope")
    findings: list[str] = []
    paths: list[Path] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("filePath"), str):
            findings.append(f"result row {index} has no string filePath")
            continue
        paths.append(Path(row["filePath"]))
    if findings:
        rendered = "\n".join(f"  - {finding}" for finding in findings)
        raise ScopeError(f"ESLint scope result has {len(findings)} finding(s):\n{rendered}")
    if not paths:
        raise ScopeError(
            "ESLint resolved zero files; correct its configured input and ignore scope"
        )
    return paths


def config_string_array(path: Path, prefix: str, label: str) -> tuple[str, ...]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ScopeError(f"could not read {label} {path}: {error}") from error
    match = re.search(rf"{prefix}\s*\[(?P<body>.*?)\]", source, flags=re.DOTALL)
    if match is None:
        raise ScopeError(f"could not resolve {label} from {path}; keep its input array explicit")
    values = tuple(re.findall(r'"([^"\n]+)"', match.group("body")))
    if not values:
        raise ScopeError(f"{label} in {path} resolves zero entries")
    return values


def dashboard_build_inputs(dashboard: Path) -> DashboardBuildInputs:
    panda = dashboard / "panda.config.ts"
    vite = dashboard / "vite.config.ts"
    panda_include = config_string_array(panda, r"\binclude\s*:\s*", "Panda include")
    vite_names = config_string_array(
        vite,
        r"\bconst\s+BUILD_INPUT_FILES\s*=\s*",
        "Vite BUILD_INPUT_FILES",
    )
    vite_inputs = tuple(dashboard / name for name in vite_names)
    missing = [path for path in vite_inputs if not path.is_file()]
    if missing:
        rendered = ", ".join(path.as_posix() for path in missing)
        raise ScopeError(
            f"Vite BUILD_INPUT_FILES names missing inputs: {rendered}; restore or remove each entry"
        )
    return DashboardBuildInputs(panda_include=panda_include, vite_inputs=vite_inputs)


def coverage_json_file_count(coverage_json: Path) -> int:
    try:
        data = json.loads(coverage_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScopeError(f"could not read Coverage.py JSON {coverage_json}: {error}") from error
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict) or not files:
        raise ScopeError(f"Coverage.py JSON {coverage_json} contains zero file records")
    return len(files)


def derive_scope(project_root: Path) -> GateScope:
    """Derive index paths, configured roots, and report-only untracked exposure."""
    tracked = git_ls_files(project_root, "*.py")
    if not tracked:
        raise ScopeError(f"git tracks no Python files under {project_root}")
    package_paths = top_level_packages(tracked)
    if not package_paths:
        raise ScopeError(
            "no tracked top-level Python package (a directory holding __init__.py) under "
            f"{project_root}; coverage and CRAP would have nothing to measure"
        )
    test_paths = pytest_testpaths(project_root)
    # 260731-EFA-L7 R8: the test tree joins the coverage measurement and the CRAP
    # input. Test modules execute under pytest, so their coverage approaches 1.0
    # and ``crap_score`` degenerates to raw cyclomatic complexity against the
    # threshold; that gates over-complex test helpers. File size is not CRAP's job
    # and stays the file-size rail's.
    coverage_paths = list(dict.fromkeys([*package_paths, *test_paths]))
    roots = derive_scope_roots(project_root, tracked, coverage_paths, test_paths)
    dashboard_ts = git_ls_files(project_root, "dashboard/src/*.ts", "dashboard/src/*.tsx")
    return GateScope(
        lint_paths=tracked,
        type_paths=tracked,
        coverage_paths=coverage_paths,
        test_paths=test_paths,
        size_paths=sorted(set(tracked) | set(dashboard_ts)),
        scope_roots=roots,
        untracked_paths=git_untracked_files(project_root, roots),
    )
