"""The gate's scope is the tree, and this file is what makes that true.

``check.derive_scope`` reads scope off ``git ls-files`` rather than off a constant, but
a derivation can still miss a corner of the tree: a directory nobody thought of, or a
language whose rail nobody wired up. So this module recomputes the tracked set
independently, builds the commands the wrapper actually runs, and asserts those
commands reach every tracked file.

Two rails are checked. Python goes through the quality wrapper, so the assertion is made
against the real ``ruff`` and ``pyright`` argument vectors rather than against the
dataclass that produced them -- a scope that is declared but not passed to a tool is not
a scope. TypeScript goes through the frontend rail, which lives in per-directory
``eslint.config.*`` and ``tsconfig*.json`` files; those are read from the tree too.

Anything neither rail reaches fails this module. There is no allowlist to name it in.
Three empty ones stood here -- ``ALLOWED_UNGATED_PYTHON``, ``ALLOWED_UNGATED_TYPESCRIPT``
and ``ALLOWED_UNTYPED_TYPESCRIPT`` -- each shrink-only, each with a comment saying it
should stay empty. They were deleted with the complexity baseline they were shaped like:
an empty exemption list is a place to put the next offender, and every population they
were built for was brought onto a rail instead of recorded. Ruff and pyright reach all
607 tracked Python files; ``.pi/extensions/tsconfig.json`` is the rail for the Pi harness
extension; ``tsconfig.driver.json`` covers the Playwright/perf driver layer and
``panda.config.ts`` joined ``tsconfig.node.json``. A file that genuinely cannot be gated
is a change to a rail, not a line in a list.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from functools import cache
from pathlib import Path, PurePosixPath

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.code_quality import check

REPO_ROOT = Path(__file__).resolve().parents[2]


def git_ls_files(*patterns: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--", *patterns],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return sorted({entry for entry in completed.stdout.split("\0") if entry})


def wrapper_steps() -> dict[str, check.Step]:
    """The commands the wrapper would run against this checkout, by step name."""
    scope = check.derive_scope(REPO_ROOT)
    config = check.CheckConfig(
        project_root=REPO_ROOT,
        scope=scope,
        coverage_json=None,
        threshold=check.crap_calculator.DEFAULT_CRAP_THRESHOLD,
        top=check.crap_calculator.DEFAULT_TOP,
    )
    return {step.name: step for step in check.quality_steps(config, REPO_ROOT / "coverage.json")}


def is_under(path: str, roots: list[Path] | list[PurePosixPath]) -> bool:
    parts = PurePosixPath(path).parts
    for root in roots:
        root_parts = PurePosixPath(root).parts
        if parts[: len(root_parts)] == root_parts:
            return True
    return False


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a tsconfig include/exclude glob to a full-path regex.

    TypeScript's glob vocabulary here is ``**`` (any number of directories), ``*`` (any
    run of characters within one segment) and ``?`` (one character within one segment).
    ``fnmatch`` is not usable because its ``*`` crosses ``/``, which would silently
    widen every pattern and make this test claim coverage it does not have.
    """
    out: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out.append("(?:[^/]+/)*")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(out) + "$")


def tsconfig_patterns(config_path: str, key: str) -> list[str]:
    raw = json.loads((REPO_ROOT / config_path).read_text(encoding="utf-8"))
    entries = raw.get(key, [])
    if not isinstance(entries, list):
        return []
    directory = PurePosixPath(config_path).parent
    return [(directory / str(entry)).as_posix() for entry in entries]


@cache
def typescript_type_roots() -> tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]:
    """Include and exclude matchers from every tracked ``tsconfig*.json``.

    A bare entry (``"src"``, ``"vite.config.ts"``) is matched literally *and* as a
    directory prefix, because TypeScript reads a directory entry as everything beneath
    it and a file entry as itself.
    """
    includes: list[re.Pattern[str]] = []
    excludes: list[re.Pattern[str]] = []
    for config_path in git_ls_files("*tsconfig*.json"):
        for key, sink in (("include", includes), ("exclude", excludes)):
            for pattern in tsconfig_patterns(config_path, key):
                sink.append(glob_to_regex(pattern))
                if not any(character in pattern for character in "*?"):
                    sink.append(glob_to_regex(f"{pattern}/**"))
    return tuple(includes), tuple(excludes)


def typescript_lint_roots() -> list[PurePosixPath]:
    """Directories an eslint flat config governs.

    ESLint's flat config applies to the directory it sits in, so a tracked
    ``eslint.config.*`` marks its own directory as a lint root.
    """
    return [PurePosixPath(path).parent for path in git_ls_files("*eslint.config.*")]


def type_checked(path: str) -> bool:
    includes, excludes = typescript_type_roots()
    if any(pattern.match(path) for pattern in excludes):
        return False
    return any(pattern.match(path) for pattern in includes)


class PythonGateScopeTests(unittest.TestCase):
    def test_every_tracked_python_file_is_linted_and_type_checked(self) -> None:
        steps = wrapper_steps()
        ruff_arguments = set(steps["ruff"].command)
        pyright_arguments = set(steps["pyright"].command)

        ungated = {
            path: [
                rail
                for rail, arguments in (("ruff", ruff_arguments), ("pyright", pyright_arguments))
                if path not in arguments
            ]
            for path in git_ls_files("*.py")
        }
        ungated = {path: rails for path, rails in ungated.items() if rails}

        self.assertEqual(
            ungated,
            {},
            "tracked Python outside the gate. Bring it into scope -- the wrapper derives "
            "scope from `git ls-files`, so a file only escapes if it is untracked. There "
            "is no allowlist to record it in.",
        )

    def test_python_coverage_and_test_rails_reach_their_trees(self) -> None:
        scope = check.derive_scope(REPO_ROOT)

        package_files = [
            path for path in git_ls_files("*.py") if is_under(path, scope.coverage_paths)
        ]
        self.assertEqual(
            sorted(scope.coverage_paths),
            [Path("mcp/src/agents_remember")],
            "the tracked top-level package set changed; coverage and CRAP scope moved with it",
        )
        self.assertGreater(len(package_files), 0)

        test_files = [path for path in git_ls_files("*.py") if is_under(path, scope.test_paths)]
        collected = [path for path in git_ls_files("mcp/tests/*.py", "mcp/tests/**/*.py")]
        self.assertEqual(
            sorted(test_files),
            sorted(collected),
            "pytest's testpaths no longer covers every tracked test file",
        )


class TypeScriptGateScopeTests(unittest.TestCase):
    def test_every_tracked_typescript_file_is_on_a_frontend_rail(self) -> None:
        self.assertEqual(
            sorted(ungated_typescript_paths()),
            [],
            "tracked TypeScript outside every frontend rail. Put it under a directory "
            "with an eslint.config.* or name it in a tsconfig include. There is no "
            "allowlist to record it in.",
        )

    def test_every_linted_typescript_file_is_also_type_checked(self) -> None:
        # "Linted but never type-checked" is the same shape of gap as the Python scope
        # hole 260731-EFA-L2 closed, so the stricter rail is asserted separately: a file
        # satisfying only one of the two is still a file no type error can reach.
        self.assertEqual(
            sorted(untyped_typescript_paths()),
            [],
            "tracked TypeScript that no tsconfig include type-checks. Add it to a "
            "tsconfig project. There is no allowlist to record it in.",
        )


def ungated_typescript_paths() -> set[str]:
    lint_roots = typescript_lint_roots()
    return {
        path
        for path in git_ls_files("*.ts", "*.tsx")
        if not is_under(path, lint_roots) and not type_checked(path)
    }


def untyped_typescript_paths() -> set[str]:
    """Tracked TypeScript a lint root reaches but no tsconfig type-checks.

    Files on no rail at all are excluded: those are what
    ``test_every_tracked_typescript_file_is_on_a_frontend_rail`` reports, and naming them
    in both places would mean two failures for one fix.
    """
    lint_roots = typescript_lint_roots()
    return {
        path
        for path in git_ls_files("*.ts", "*.tsx")
        if is_under(path, lint_roots) and not type_checked(path)
    }


if __name__ == "__main__":
    unittest.main()
