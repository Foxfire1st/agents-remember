"""Focused Light proofs for L29-R3 Git hook repairs R1-R6."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.worktrees.modules.landing_commit_check import run_landing_commit_check
from agents_remember_test_support.code_quality import scope_reporting
from agents_remember_test_support.code_quality.scope import GateScope

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO_ROOT / ".githooks/_gate.sh"
DASHBOARD_BIN = REPO_ROOT / "dashboard/node_modules/.bin"
OXLINT_BIN = DASHBOARD_BIN / "oxlint"
TSC_BIN = DASHBOARD_BIN / "tsc"
SCOPE_MODULE = "agents_remember_test_support.code_quality.scope_reporting"


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=True,
    )
    return completed.stdout


def _minimal_command_dir(tmp_path: Path, extra: tuple[str, ...] = ()) -> Path:
    commands = tmp_path / "commands"
    commands.mkdir(exist_ok=True)
    for name in ("git", "uname", "xargs", *extra):
        executable = shutil.which(name)
        assert executable is not None, f"missing command {name}"
        target = commands / name
        if not target.exists():
            target.symlink_to(executable)
    return commands


def _make_hook_repo(
    tmp_path: Path,
    python_script: str,
    npm_script: str,
) -> tuple[Path, dict[str, str]]:
    commands = _minimal_command_dir(tmp_path)
    _write_executable(commands / "npm", npm_script)
    env = dict(os.environ, PATH=str(commands))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "dashboard/node_modules").mkdir(parents=True)
    (repo / "dashboard/package.json").write_text("{}", encoding="utf-8")
    (repo / "scripts").mkdir()
    shutil.copyfile(
        REPO_ROOT / "scripts/python-runtime-contract.env",
        repo / "scripts/python-runtime-contract.env",
    )
    (repo / ".gitignore").write_text("mcp/.venv/\ndashboard/node_modules/\n", encoding="utf-8")
    _write_executable(repo / "mcp/.venv/bin/python", python_script)
    (repo / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "init", "-q", env=env)
    _git(repo, "add", ".", env=env)
    return repo, env


def _run_gate(
    repo: Path,
    env: dict[str, str],
    tier: str = "fast",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(GATE_SCRIPT), tier],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _populate_many_python_files(repo: Path, count: int, env: dict[str, str]) -> list[str]:
    deep_dir = repo / ("pkg_" + "a" * 60) / ("sub_" + "b" * 60) / ("mod_" + "c" * 60)
    deep_dir.mkdir(parents=True, exist_ok=True)
    (deep_dir / "with space.py").write_text("X = 1\n", encoding="utf-8")
    for idx in range(count):
        (deep_dir / f"file_{idx:04d}_{'d' * 120}.py").write_text("X = 1\n", encoding="utf-8")
    _git(repo, "add", ".", env=env)
    raw = _git(repo, "ls-files", "-z", "--", "*.py", env=env)
    return [entry for entry in raw.split("\0") if entry]


def _recording_python_stub(log_file: Path, fail_on_file: str = "") -> str:
    return (
        f'log="{log_file}"\n'
        f'fail_on="{fail_on_file}"\n'
        'case "$*" in\n'
        "  *check-python-runtime.py*|*'import pyright, ruff'*) exit 0 ;;\n"
        "  *-m\\ ruff\\ check*)\n"
        "    shift 3\n"
        '    printf "RUFF_CALL\\n" >> "$log"\n'
        '    for item in "$@"; do\n'
        '      printf "RUFF:%s\\n" "$item" >> "$log"\n'
        '      if [ -n "$fail_on" ] && [ "$item" = "$fail_on" ]; then\n'
        '        echo "simulated late ruff failure" >&2\n'
        "        exit 1\n"
        "      fi\n"
        "    done\n"
        "    exit 0\n"
        "    ;;\n"
        "  *-m\\ ruff\\ format\\ --check*)\n"
        "    shift 4\n"
        '    printf "FMT_CALL\\n" >> "$log"\n'
        '    for item in "$@"; do printf "FMT:%s\\n" "$item" >> "$log"; done\n'
        "    exit 0\n"
        "    ;;\n"
        "  *-m\\ pyright*)\n"
        "    shift 6\n"
        '    printf "PYRIGHT_CALL\\n" >> "$log"\n'
        '    for item in "$@"; do printf "PYRIGHT:%s\\n" "$item" >> "$log"; done\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )


def _extract_logged(log_file: Path, prefix: str) -> list[str]:
    return [
        line.removeprefix(prefix)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix)
    ]


def test_r1_xargs_batch_preserves_ordered_files_and_reduces_spawns(tmp_path: Path) -> None:
    log_file = tmp_path / "calls.log"
    repo, env = _make_hook_repo(tmp_path, _recording_python_stub(log_file), "exit 0\n")
    expected = _populate_many_python_files(repo, count=450, env=env)
    total_bytes = sum(len(item.encode("utf-8")) + 1 for item in expected)
    assert 131072 < total_bytes < 250000

    result = _run_gate(repo, env)
    assert result.returncode == 0, result.stderr + result.stdout
    for prefix, call_tag in (
        ("RUFF:", "RUFF_CALL"),
        ("FMT:", "FMT_CALL"),
        ("PYRIGHT:", "PYRIGHT_CALL"),
    ):
        assert _extract_logged(log_file, prefix) == expected
        if sys.platform.startswith("linux"):
            assert _extract_logged(log_file, call_tag) == [""]


def test_r1_xargs_splits_above_256k_and_propagates_late_failure(tmp_path: Path) -> None:
    log_file = tmp_path / "calls.log"
    repo, env = _make_hook_repo(tmp_path, "exit 0\n", "exit 0\n")
    expected = _populate_many_python_files(repo, count=950, env=env)
    total_bytes = sum(len(item.encode("utf-8")) + 1 for item in expected)
    assert total_bytes > 270000
    _write_executable(
        repo / "mcp/.venv/bin/python",
        _recording_python_stub(log_file, fail_on_file=expected[-1]),
    )

    result = _run_gate(repo, env)
    assert result.returncode == 1
    assert "result: ruff FAIL" in result.stderr
    assert len(_extract_logged(log_file, "RUFF_CALL")) >= 2
    assert _extract_logged(log_file, "RUFF:") == expected


def _build_scope_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "scope_repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "pyproject.toml").write_text(
        '[tool.ruff]\nline-length = 100\n[tool.pyright]\ninclude = ["."]\n'
        '[tool.radon]\ncc_min = "B"\n[tool.coverage.run]\nbranch = true\n'
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        '[tool.agents_remember]\nproduct_package_roots = ["pkg"]\n'
        "verification_package_roots = []\n",
        encoding="utf-8",
    )
    for directory in ("pkg", "tests", "scripts"):
        (repo / directory).mkdir()
    (repo / "pkg/__init__.py").write_text("", encoding="utf-8")
    (repo / "tests/test_pkg.py").write_text("def test_ok() -> None:\n    assert True\n", "utf-8")
    (repo / "scripts/sync-projection-types.py").write_text(
        'print("a.json <- schema")\nprint("a.ts <- schema")\n', encoding="utf-8"
    )
    (repo / "scripts/renew-instructions.py").write_text(
        'print("canonical: skills")\nprint("skills/one.md")\n', encoding="utf-8"
    )
    (repo / "scripts/sync-runtime.py").write_text('print("system: a -> b")\n', encoding="utf-8")
    _git(repo, "add", "-A")
    (repo / "pkg/untracked_file.py").write_text("U = 1\n", encoding="utf-8")
    return repo


def _run_scope_cli(repo: Path, args: list[str], env: dict[str, str]) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", SCOPE_MODULE, "--project-root", str(repo), *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=True,
    )
    return completed.stdout


def test_r2_fixed_step_scope_line_computes_python_files_under_lazily(tmp_path: Path) -> None:
    repo = _build_scope_repo(tmp_path)
    scope = GateScope(
        lint_paths=[Path("pkg/__init__.py")],
        type_paths=[Path("pkg/__init__.py")],
        coverage_paths=[Path("pkg")],
        test_paths=[Path("tests")],
        size_paths=[Path("pkg/__init__.py")],
    )
    with mock.patch.object(
        scope_reporting,
        "python_files_under",
        wraps=scope_reporting.python_files_under,
    ) as walked:
        lazy_steps = (
            "ruff",
            "ruff-format",
            "pyright",
            "evidence-lifecycle",
            "causal-preflight",
            "file-size",
            "layering",
        )
        for step in lazy_steps:
            scope_reporting.fixed_step_scope_line(step, repo, scope)
        scope_reporting.fixed_step_scope_line("pytest", repo, scope, targeted=True)
        assert walked.call_count == 0

        scope_reporting.fixed_step_scope_line("radon-cc", repo, scope)
        assert walked.call_count == 1
        scope_reporting.fixed_step_scope_line("pytest", repo, scope, targeted=False)
        assert walked.call_count == 3


@pytest.mark.parametrize(
    ("tier", "invocation", "push_updates"),
    [
        ("fast", "pre-commit-staged", ""),
        ("fast", "pre-commit-sequencer", ""),
        (
            "targeted",
            "pre-push",
            "refs/heads/main 1111111111111111111111111111111111111111 "
            "refs/heads/main 2222222222222222222222222222222222222222\n",
        ),
    ],
)
def test_r2_hook_batch_matches_separate_spawns_byte_for_byte(
    tmp_path: Path,
    tier: str,
    invocation: str,
    push_updates: str,
) -> None:
    repo = _build_scope_repo(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{REPO_ROOT / 'mcp/test_support'}:{REPO_ROOT / 'mcp/src'}"
    env["AR_QUALITY_INVOCATION"] = invocation
    if push_updates:
        env["AR_QUALITY_PUSH_UPDATES"] = push_updates

    keys_and_cmds: list[tuple[str, list[str]]] = [
        ("hook-tier", ["hook-tier", "--tier", tier]),
    ]
    if invocation == "pre-commit-sequencer":
        keys_and_cmds.append(("untracked", ["untracked"]))
    keys_and_cmds.extend(
        [
            (
                "generated-projection",
                [
                    "generated",
                    "--name",
                    "projection",
                    "--script",
                    "scripts/sync-projection-types.py",
                ],
            ),
            (
                "generated-skills",
                ["generated", "--name", "skills", "--script", "scripts/renew-instructions.py"],
            ),
            (
                "generated-runtime",
                ["generated", "--name", "runtime", "--script", "scripts/sync-runtime.py"],
            ),
            ("ruff", ["fixed-step", "--name", "ruff"]),
            ("ruff-format", ["fixed-step", "--name", "ruff-format"]),
            ("pyright", ["fixed-step", "--name", "pyright"]),
            ("evidence-lifecycle", ["fixed-step", "--name", "evidence-lifecycle"]),
        ]
    )
    separate = {key: _run_scope_cli(repo, cmd, env) for key, cmd in keys_and_cmds}

    out_dir = tmp_path / f"batch_{tier}_{invocation}"
    _run_scope_cli(repo, ["hook-batch", "--tier", tier, "--output-dir", str(out_dir)], env)
    batched = {key: (out_dir / key).read_text(encoding="utf-8") for key, _ in keys_and_cmds}
    assert batched == separate
    assert "".join(batched.values()) == "".join(separate.values())


def test_r2_gate_invokes_scope_batch_once_and_cleans_temp_dir(tmp_path: Path) -> None:
    scope_log = tmp_path / "scope.log"
    gate_tmp_root = tmp_path / "gate_tmp"
    gate_tmp_root.mkdir()
    py_stub = (
        f'log="{scope_log}"\n'
        'case "$*" in\n'
        f'  *"-m {SCOPE_MODULE}"*) printf \'%s\\n\' "$*" >> "$log" ;;\n'
        "esac\n"
        "exit 0\n"
    )
    repo, env = _make_hook_repo(tmp_path, py_stub, "exit 0\n")
    env["TMPDIR"] = str(gate_tmp_root)

    result = _run_gate(repo, env)
    assert result.returncode == 0, result.stderr + result.stdout
    calls = scope_log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1
    assert "hook-batch --tier fast --output-dir" in calls[0]
    assert list(gate_tmp_root.iterdir()) == []


def test_r3_concurrent_rails_overlap_and_print_python_then_dashboard(tmp_path: Path) -> None:
    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()
    sleep_bin = shutil.which("sleep")
    assert sleep_bin is not None
    py_stub = (
        f'markers="{marker_dir}"\n'
        'case "$*" in\n'
        "  *-m\\ ruff\\ check*)\n"
        '    : > "$markers/py.started"\n'
        "    i=0\n"
        '    while [ "$i" -lt 40 ] && [ ! -f "$markers/dash.finished" ]; do\n'
        f'      "{sleep_bin}" 0.02\n'
        "      i=$((i + 1))\n"
        "    done\n"
        '    echo "PYTHON_RAIL_BODY"\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    npm_stub = (
        f'markers="{marker_dir}"\n'
        'case "$*" in\n'
        "  *run\\ typecheck*)\n"
        '    echo "DASHBOARD_RAIL_BODY"\n'
        '    : > "$markers/dash.finished"\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    repo, env = _make_hook_repo(tmp_path, py_stub, npm_stub)
    result = _run_gate(repo, env)
    assert result.returncode == 0, result.stderr + result.stdout
    assert (marker_dir / "dash.finished").is_file()
    out = result.stdout
    assert out.index("PYTHON_RAIL_BODY") < out.index("DASHBOARD_RAIL_BODY")
    assert out.index("result: evidence-lifecycle PASS") < out.index(
        "result: dashboard-codegen PASS"
    )


def test_r3_dashboard_only_failure_fails_preserving_order_and_message(tmp_path: Path) -> None:
    npm_stub = (
        'case "$*" in\n'
        "  *run\\ lint*)\n"
        '    echo "oxlint: no-unused-vars in App.tsx" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    repo, env = _make_hook_repo(tmp_path, "exit 0\n", npm_stub)
    result = _run_gate(repo, env)
    assert result.returncode == 1
    assert "result: evidence-lifecycle PASS" in result.stdout
    assert "oxlint: no-unused-vars in App.tsx" in result.stderr
    assert "result: dashboard-lint FAIL" in result.stderr
    assert result.stderr.splitlines()[-1].endswith("result: fast-tier FAIL")


def test_r3_python_only_failure_fails_preserving_message(tmp_path: Path) -> None:
    py_stub = (
        'case "$*" in\n'
        "  *-m\\ ruff\\ check*)\n"
        '    echo "F401 unused import" >&2\n'
        "    exit 1\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    repo, env = _make_hook_repo(tmp_path, py_stub, "exit 0\n")
    result = _run_gate(repo, env)
    assert result.returncode == 1
    assert "F401 unused import" in result.stderr
    assert "result: ruff FAIL" in result.stderr
    assert result.stderr.splitlines()[-1].endswith("result: fast-tier FAIL")


def test_r4_gate_runs_dashboard_lint_without_eslint_cache_flags(tmp_path: Path) -> None:
    """oxlint has no --cache flag and runs cold in ~0.65s across the dashboard."""
    npm_log = tmp_path / "npm.log"
    npm_stub = f'printf "%s\\n" "$*" >> "{npm_log}"\nexit 0\n'
    repo, env = _make_hook_repo(tmp_path, "exit 0\n", npm_stub)

    result = _run_gate(repo, env)
    assert result.returncode == 0, result.stderr + result.stdout
    npm_calls = npm_log.read_text(encoding="utf-8").splitlines()
    assert "--prefix dashboard run lint --silent" in npm_calls
    assert all("--cache" not in call and ".eslintcache" not in call for call in npm_calls)


def test_r4_oxlint_catches_introduced_lint_error(tmp_path: Path) -> None:
    assert OXLINT_BIN.is_file(), f"missing oxlint binary at {OXLINT_BIN}"
    project = tmp_path / "oxlint_fixture"
    project.mkdir()
    (project / ".oxlintrc.json").write_text(
        json.dumps({"categories": {"correctness": "off"}, "rules": {"no-unused-vars": "error"}})
        + "\n",
        encoding="utf-8",
    )
    sample = project / "sample.js"
    sample.write_text("export const answer = 42;\n", encoding="utf-8")
    cmd = [str(OXLINT_BIN), "."]
    cold = subprocess.run(cmd, cwd=project, capture_output=True, text=True, check=False)
    assert cold.returncode == 0, cold.stdout + cold.stderr

    warm = subprocess.run(cmd, cwd=project, capture_output=True, text=True, check=False)
    assert warm.returncode == 0, warm.stdout + warm.stderr

    original_stat = sample.stat()
    sample.write_text("const unused = 1;\nexport const answer = 42;\n", encoding="utf-8")
    os.utime(sample, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    mutated = subprocess.run(cmd, cwd=project, capture_output=True, text=True, check=False)
    assert mutated.returncode != 0
    assert "no-unused-vars" in mutated.stdout + mutated.stderr


def test_r5_dashboard_tsconfigs_enable_incremental_buildinfo() -> None:
    for stem in ("tsconfig.app", "tsconfig.node", "tsconfig.driver"):
        data = json.loads((REPO_ROOT / "dashboard" / f"{stem}.json").read_text(encoding="utf-8"))
        options = data["compilerOptions"]
        assert options["incremental"] is True
        assert options["tsBuildInfoFile"] == f"./{stem}.tsbuildinfo"


def test_r5_tsc_b_incremental_cache_catches_introduced_type_error(tmp_path: Path) -> None:
    assert TSC_BIN.is_file(), f"missing tsc binary at {TSC_BIN}"
    project = tmp_path / "tsc_fixture"
    (project / "src").mkdir(parents=True)
    (project / "tsconfig.json").write_text(
        json.dumps({"files": [], "references": [{"path": "./tsconfig.app.json"}]}),
        encoding="utf-8",
    )
    (project / "tsconfig.app.json").write_text(
        json.dumps(
            {
                "compilerOptions": {
                    "target": "ES2022",
                    "module": "ESNext",
                    "strict": True,
                    "noEmit": True,
                    "incremental": True,
                    "tsBuildInfoFile": "./tsconfig.app.tsbuildinfo",
                },
                "include": ["src"],
            }
        ),
        encoding="utf-8",
    )
    source = project / "src/app.ts"
    source.write_text("export const value: number = 1;\n", encoding="utf-8")

    cold = subprocess.run(
        [str(TSC_BIN), "-b"], cwd=project, capture_output=True, text=True, check=False
    )
    assert cold.returncode == 0, cold.stdout + cold.stderr
    assert (project / "tsconfig.app.tsbuildinfo").is_file()

    warm = subprocess.run(
        [str(TSC_BIN), "-b"], cwd=project, capture_output=True, text=True, check=False
    )
    assert warm.returncode == 0, warm.stdout + warm.stderr

    previous_stat = source.stat()
    source.write_text('export const value: number = "wrong";\n', encoding="utf-8")
    os.utime(source, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns + 2_000_000_000))
    mutated = subprocess.run(
        [str(TSC_BIN), "-b"], cwd=project, capture_output=True, text=True, check=False
    )
    assert mutated.returncode != 0
    assert "TS2322" in mutated.stdout + mutated.stderr


def test_r5_tsc_caches_are_gitignored_and_do_not_trip_landing_commit_check(tmp_path: Path) -> None:
    repo = tmp_path / "closeout_repo"
    (repo / "dashboard").mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / ".gitignore", repo / ".gitignore")
    shutil.copyfile(REPO_ROOT / "dashboard/.gitignore", repo / "dashboard/.gitignore")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "l29-proof@example.invalid")
    _git(repo, "config", "user.name", "L29 Proof")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")

    cache_relpaths = (
        "dashboard/tsconfig.app.tsbuildinfo",
        "dashboard/tsconfig.node.tsbuildinfo",
        "dashboard/tsconfig.driver.tsbuildinfo",
    )
    git_dir = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    hook_body = (
        "#!/bin/sh\n"
        'printf "{}" > dashboard/tsconfig.app.tsbuildinfo\n'
        'printf "{}" > dashboard/tsconfig.node.tsbuildinfo\n'
        'printf "{}" > dashboard/tsconfig.driver.tsbuildinfo\n'
        'echo "[pre-commit] result: fast-tier PASS"\n'
        "exit 0\n"
    )
    _write_executable(git_dir / "hooks/pre-commit", hook_body.removeprefix("#!/bin/sh\n"))

    (repo / "change.txt").write_text("candidate\n", encoding="utf-8")
    _git(repo, "add", "-A")
    tree = _git(repo, "write-tree").strip()

    passed = run_landing_commit_check(repo, tree=tree)
    assert passed.tree == tree
    assert _git(repo, "write-tree").strip() == tree
    for relpath in cache_relpaths:
        assert (repo / relpath).is_file()
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", relpath],
            cwd=repo,
            check=False,
        )
        assert ignored.returncode == 0, f"{relpath} must be gitignored"
    _git(repo, "add", "-A")
    assert _git(repo, "write-tree").strip() == tree


def test_r6_gate_exports_pyright_ignore_warnings_and_real_type_error_fails(
    tmp_path: Path,
) -> None:
    env_log = tmp_path / "pyright_env.log"
    py_stub = (
        f'log="{env_log}"\n'
        'case "$*" in\n'
        "  *-m\\ ruff\\ check*)\n"
        '    printf "RUFF:%s\\n" "${PYRIGHT_PYTHON_IGNORE_WARNINGS:-unset}" >> "$log"\n'
        "    ;;\n"
        "  *-m\\ pyright*)\n"
        '    printf "PYRIGHT:%s\\n" "${PYRIGHT_PYTHON_IGNORE_WARNINGS:-unset}" >> "$log"\n'
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    repo, env = _make_hook_repo(tmp_path, py_stub, "exit 0\n")
    env.pop("PYRIGHT_PYTHON_IGNORE_WARNINGS", None)

    result = _run_gate(repo, env)
    assert result.returncode == 0, result.stderr + result.stdout
    assert env_log.read_text(encoding="utf-8").splitlines() == ["RUFF:unset", "PYRIGHT:1"]

    bad_file = tmp_path / "bad_types.py"
    bad_file.write_text('value: int = "not-an-int"\n', encoding="utf-8")
    pyright_env = dict(os.environ, PYRIGHT_PYTHON_IGNORE_WARNINGS="1")
    pyright_run = subprocess.run(
        [sys.executable, "-m", "pyright", "--pythonpath", sys.executable, str(bad_file)],
        cwd=tmp_path,
        env=pyright_env,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert pyright_run.returncode != 0
    combined = pyright_run.stdout + pyright_run.stderr
    assert "reportAssignmentType" in combined or "cannot be assigned to" in combined
