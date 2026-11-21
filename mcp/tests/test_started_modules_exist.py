"""Every module the product starts exists at the path that names it.

The failure this exists to make impossible: a launcher keeps naming a module that a later
commit deleted, and nothing notices because nothing imports it.  ``173bb01e1`` deleted the
detached lifecycle worker and left ``launch_detached_worker`` still naming
``agents_remember.application.lifecycle.lifecycle_operation_worker``; the only thing that
would have run the launch is a journal record from an older build, so the break sat in the
tree for two months with a green suite.

Two populations are checked, both by file path and never by import, so a module whose import
has side effects or whose dependencies are absent is still measured:

* ``mcp/src/agents_remember`` -- the module name after ``-m`` in a python invocation built by
  the product, whether written as a string literal or held in a module constant. Stdio server
  definitions declare the executable and its arguments separately.
* ``mcp/pyproject.toml`` ``[project.scripts]`` -- each entry's module, and the function that
  entry names inside it.

What is deliberately out of scope, and why:

* ``git``'s own ``-m``.  Those arguments are told apart by shape, not by a list of call
  sites: a python invocation's elements before ``-m`` are interpreter flags or one executable
  expression, while a git invocation's are its subcommand (``commit``, ``commit-tree``,
  ``read-tree``), so the classification needs no knowledge of which function is calling.
* ``scripts/`` and ``mcp/test_support/``.  Those are run by a developer or imported by a
  test; the product never starts them.
* The ``python -m`` lines of the documents.  A prose command is not a thing the product
  starts, and a check over it would go red on a documentation edit rather than a deletion.

A form this scan cannot resolve fails the test unless ``UNRESOLVED_FORMS`` lists its file and
reason.  Declaring one is a statement about the scan's reach, never an exemption from it: the
module it names is still checked, and a declared entry whose form no longer appears fails, so
the list cannot outlive its reason.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

pytestmark = pytest.mark.fitness

MCP_ROOT: Final = Path(__file__).resolve().parents[1]
PACKAGE_ROOT: Final = MCP_ROOT / "src" / "agents_remember"
SOURCE_ROOT: Final = MCP_ROOT / "src"
PYPROJECT_PATH: Final = MCP_ROOT / "pyproject.toml"

# The interpreter flags that may precede ``-m``.  Everything before the ``-m`` in a python
# invocation is one of these or a single expression naming the interpreter, which is what
# separates it from a git subcommand.
PYTHON_FLAGS: Final[frozenset[str]] = frozenset(
    {"-P", "-B", "-E", "-I", "-O", "-OO", "-s", "-u", "-X", "-W"}
)

# Interpreter executables a launch list may put ahead of ``-m``.  A list that starts with
# ``git`` or any other program is not a python invocation; the shape is what tells them apart.
INTERPRETER_NAMES: Final[frozenset[str]] = frozenset({"python", "python3", "py"})


@dataclass(frozen=True)
class UnresolvedDeclaration:
    """One site the scan cannot resolve, and why it cannot.

    A declaration is a statement about the scan's reach, never an exemption from it: the reason is
    required, and a declared form that no longer appears fails, so the list cannot outlive its
    reason.
    """

    path: str
    form: str
    reason: str


# Empty while every site in the tree is resolvable; a new unresolvable site fails the test until
# it lands here with its reason.
UNRESOLVED_FORMS: Final[tuple[UnresolvedDeclaration, ...]] = ()

_GENERATED_TOML_QUOTES: Final = str.maketrans("", "", '" \t,')


@dataclass(frozen=True)
class LaunchSite:
    """One module name a python invocation in the product would start."""

    path: Path
    line: int
    module: str
    form: str


@dataclass(frozen=True)
class UnresolvedSite:
    """One ``-m`` the scan met but could not turn into a module name."""

    path: Path
    line: int
    form: str


def _modules() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _module_file(module: str) -> Path | None:
    """Resolve a dotted module name to its file below ``mcp/src``, if it exists."""

    relative = Path(*module.split("."))
    for candidate in (
        SOURCE_ROOT / relative.with_suffix(".py"),
        SOURCE_ROOT / relative / "__init__.py",
    ):
        if candidate.is_file():
            return candidate
    return None


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level string constants, which is how a launcher usually holds its target."""

    constants: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.expr]
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def _is_interpreter_prefix(elements: list[ast.expr], *, before: int) -> bool:
    """Tell a python invocation's argument list from a git subcommand's.

    A python invocation carries interpreter flags and at most one executable expression
    ahead of ``-m``.  A git invocation carries its subcommand as the first element, and a
    subcommand is a string constant that is not an interpreter flag.
    """

    leading = elements[:before]
    if not leading:
        return False
    flags = 0
    interpreter = 0
    for element in leading:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            if element.value in PYTHON_FLAGS:
                flags += 1
                continue
            if _is_interpreter_name(element.value):
                interpreter += 1
                continue
            return False
        interpreter += 1
    return interpreter == 1 and (interpreter + flags) == len(leading)


def _is_interpreter_name(value: str) -> bool:
    """Whether an executable string names a python interpreter rather than a program."""

    name = value.rsplit("/", 1)[-1]
    return name in INTERPRETER_NAMES or name.startswith("python3.")


def _separate_commands(tree: ast.Module) -> dict[ast.List | ast.Tuple, ast.expr]:
    """Find command/args pairs such as a stdio MCP server definition."""

    commands: dict[ast.List | ast.Tuple, ast.expr] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        fields = {
            key.value: value
            for key, value in zip(node.keys, node.values, strict=True)
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        command, args = fields.get("command"), fields.get("args")
        if command is not None and isinstance(args, (ast.List, ast.Tuple)):
            commands[args] = command
    return commands


def _launch_sites(path: Path, tree: ast.Module) -> tuple[list[LaunchSite], list[UnresolvedSite]]:
    constants = _module_constants(tree)
    commands = _separate_commands(tree)
    starts: list[LaunchSite] = []
    unresolved: list[UnresolvedSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        command = commands.get(node)
        elements = [command, *node.elts] if command is not None else node.elts
        for index, element in enumerate(elements):
            if not (isinstance(element, ast.Constant) and element.value == "-m"):
                continue
            if not _is_interpreter_prefix(elements, before=index):
                continue
            target = elements[index + 1] if index + 1 < len(elements) else None
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                starts.append(LaunchSite(path, element.lineno, target.value, "literal"))
            elif isinstance(target, ast.Name) and target.id in constants:
                value = constants[target.id]
                starts.append(LaunchSite(path, element.lineno, value, f"constant {target.id}"))
            else:
                unresolved.append(
                    UnresolvedSite(path, element.lineno, ast.unparse(target) if target else "-m")
                )
        generated, generated_unresolved = _generated_text_sites(path, elements)
        starts.extend(generated)
        unresolved.extend(generated_unresolved)
    return starts, unresolved


def _generated_text_sites(
    path: Path, elements: list[ast.expr]
) -> tuple[list[LaunchSite], list[UnresolvedSite]]:
    """A ``-m`` written into generated configuration text, e.g. an argv list of TOML lines.

    The shape is a string constant that is a ``-m`` fragment with a dotted name beside it in
    the same sequence.  Reading it here is what keeps the generated registration honest
    without exempting it.
    """

    sites: list[LaunchSite] = []
    unresolved: list[UnresolvedSite] = []
    for index, element in enumerate(elements[:-1]):
        if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
            continue
        if element.value.strip() != '"-m",':
            continue
        following = elements[index + 1]
        if not (isinstance(following, ast.Constant) and isinstance(following.value, str)):
            # A generated fragment whose successor is computed is a launch this scan cannot
            # read; dropping it silently is what let a deleted module survive here.
            unresolved.append(
                UnresolvedSite(path, element.lineno, f"generated text: {ast.unparse(following)}")
            )
            continue
        module = following.value.translate(_GENERATED_TOML_QUOTES)
        if "." not in module:
            continue
        sites.append(LaunchSite(path, element.lineno, module, "generated text"))
    return sites, unresolved


def _declared_unresolved(path: Path, form: str) -> bool:
    """Whether a declaration names this site *and* carries a reason for it."""

    relative = path.relative_to(MCP_ROOT.parent).as_posix()
    return any(
        entry.path == relative and entry.form == form and entry.reason.strip()
        for entry in UNRESOLVED_FORMS
    )


def _relative(path: Path) -> str:
    return path.relative_to(MCP_ROOT.parent).as_posix()


def _all_sites() -> list[LaunchSite]:
    sites: list[LaunchSite] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found, _ = _launch_sites(path, tree)
        sites.extend(found)
    return sites


def test_every_started_module_exists() -> None:
    missing: list[str] = []
    unresolved: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found, undecidable = _launch_sites(path, tree)
        for site in found:
            if _module_file(site.module) is None:
                missing.append(
                    f"{_relative(site.path)}:{site.line} names {site.module} ({site.form})"
                )
        for site in undecidable:
            if not _declared_unresolved(site.path, site.form):
                unresolved.append(f"{_relative(site.path)}:{site.line} passes {site.form} to -m")
    assert not unresolved, (
        "these launch sites name a module this scan cannot resolve; write the module as a "
        "literal or a module constant, or declare the form and its reason in UNRESOLVED_FORMS:\n"
        + "\n".join(unresolved)
    )
    assert not missing, (
        "these launch sites name a module that does not exist; either the module was deleted "
        "and its launcher must go with it, or the name is wrong:\n" + "\n".join(missing)
    )


def test_the_scan_finds_the_launches_it_claims_to() -> None:
    """A scan that silently matches nothing would pass every site above."""

    sites = _all_sites()
    assert len(sites) >= 5, [site.module for site in sites]
    assert len({site.path for site in sites}) >= 4
    assert any(site.form.startswith("constant ") for site in sites), (
        "no launch site holds its module in a module constant; the constant path is untested"
    )


def _package_of(file: Path) -> str:
    """The dotted package a file inside ``mcp/src`` belongs to."""

    parts = file.relative_to(SOURCE_ROOT).parts
    return ".".join(parts[:-1])


def _resolve_relative(package: str, module: str | None, level: int) -> str | None:
    """Turn ``from ..x import y`` into the absolute dotted module it names."""

    parts = package.split(".") if package else []
    if level - 1 > len(parts):
        return None
    base = parts[: len(parts) - (level - 1)] if level > 1 else parts
    if module:
        base = base + module.split(".")
    return ".".join(base) if base else None


def _import_origin(file: Path, function: str) -> tuple[str, str] | None:
    """The absolute module and original symbol bound by a top-level import."""

    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    package = _package_of(file)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == function:
                    origin = (
                        _resolve_relative(package, node.module, node.level)
                        if node.level
                        else node.module
                    )
                    return (origin, alias.name) if origin is not None else None
    return None


def resolves_to_definition(
    file: Path, function: str, *, seen: set[tuple[Path, str]] | None = None
) -> bool:
    """Whether the module *defines* the function, following a re-export to its definition.

    A console entry point is usually a two-line module that re-exports its ``main`` from a
    sibling.  Counting the import as the function is what would let a deleted definition pass:
    the name is still bound, and the shipped script is broken.  Only a ``def`` counts, and a
    re-export counts only when the module it names defines one.
    """

    visited = seen if seen is not None else set()
    binding = (file, function)
    if binding in visited:
        return False
    visited.add(binding)
    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return True
    origin = _import_origin(file, function)
    if origin is None:
        return False
    module, imported_name = origin
    target = _module_file(module)
    if target is None:
        return False
    return resolves_to_definition(target, imported_name, seen=visited)


def test_console_scripts_name_an_existing_module_and_function() -> None:
    declared = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]["scripts"]
    assert declared, "mcp/pyproject.toml declares no console script"
    failures: list[str] = []
    for name, target in sorted(declared.items()):
        module, _, function = target.partition(":")
        file = _module_file(module)
        if file is None:
            failures.append(f"{name} names module {module}, which does not exist")
            continue
        if not resolves_to_definition(file, function):
            failures.append(
                f"{name} names {module}:{function}, which no module reachable from it defines"
            )
    assert not failures, "\n".join(failures)


def test_declared_unresolved_forms_still_appear() -> None:
    """A declaration whose form is gone is a claim about code that no longer exists."""

    seen: set[tuple[str, str]] = set()
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _, undecidable = _launch_sites(path, tree)
        for site in undecidable:
            seen.add((_relative(site.path), site.form))
    for entry in UNRESOLVED_FORMS:
        assert entry.reason.strip(), f"declared unresolved form {entry.path} carries no reason"
        assert (entry.path, entry.form) in seen, (
            f"declared unresolved form {entry.path} {entry.form} no longer appears"
        )


def _parse(source: str) -> ast.Module:
    return ast.parse(source)


def test_a_literal_interpreter_list_launching_an_absent_module_is_found() -> None:
    """Both standalone commands and stdio server definitions can launch a missing module."""

    tree = _parse(
        "import subprocess\n"
        "MODULE = 'agents_remember.deleted_module'\n"
        "subprocess.run(['python', '-m', MODULE])\n"
        "subprocess.run(['git', 'commit', '-m', 'message'])\n"
        "server = {'command': 'python3', 'args': ['-P', '-m', MODULE]}\n"
    )
    found, undecidable = _launch_sites(Path("mcp/src/agents_remember/probe.py"), tree)
    assert [(site.line, site.module, site.form) for site in found] == [
        (3, "agents_remember.deleted_module", "constant MODULE"),
        (5, "agents_remember.deleted_module", "constant MODULE"),
    ]
    assert _module_file("agents_remember.deleted_module") is None
    assert undecidable == []


def test_an_undecidable_literal_target_is_reported_rather_than_dropped() -> None:
    """A ``-m`` whose module is computed must fail the scan until it is declared."""

    tree = _parse(
        "import subprocess, sys\nsys.executable\nsubprocess.run(['python', '-m', unknown()])\n"
    )
    found, undecidable = _launch_sites(Path("mcp/src/agents_remember/probe.py"), tree)
    assert found == []
    assert [site.form for site in undecidable] == ["unknown()"]


def test_a_generated_fragment_with_a_computed_successor_is_reported() -> None:
    """The generated registration is read, and a computed successor is not silently skipped."""

    tree = _parse("fragment = ['\"-m\",', NAMED]\n")
    assignment = tree.body[0]
    assert isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.List)
    found, undecidable = _generated_text_sites(
        Path("mcp/src/agents_remember/probe.py"), assignment.value.elts
    )
    assert found == []
    assert [site.form for site in undecidable] == ["generated text: NAMED"]


def test_a_dangling_reexport_does_not_satisfy_a_console_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a function exported by the sibling can satisfy the console entry point."""

    monkeypatch.setattr(sys.modules[__name__], "SOURCE_ROOT", tmp_path)
    entry = tmp_path / "entry.py"
    sibling = tmp_path / "sibling.py"
    sibling.write_text("VALUE = 1\n", encoding="utf-8")
    entry.write_text("from sibling import main\n", encoding="utf-8")
    assert not resolves_to_definition(entry, "main")

    sibling.write_text("def main(): pass\n", encoding="utf-8")
    assert resolves_to_definition(entry, "main")
    entry.write_text("import sibling as main\n", encoding="utf-8")
    assert not resolves_to_definition(entry, "main")


def test_a_reexport_to_a_real_definition_still_passes() -> None:
    """The shipped entry point's relative re-export must keep resolving."""

    file = _module_file("agents_remember.mcp.__main__")
    assert file is not None
    assert resolves_to_definition(file, "main")


def test_an_aliased_reexport_resolves_the_original_symbol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A console entry must reach the symbol that its import statement names."""

    monkeypatch.setattr(sys.modules[__name__], "SOURCE_ROOT", tmp_path)
    package = tmp_path / "example"
    package.mkdir()
    entry = package / "__main__.py"
    server = package / "server.py"
    entry.write_text("from .server import launch as main\n", encoding="utf-8")
    server.write_text("def main(): pass\n", encoding="utf-8")
    assert not resolves_to_definition(entry, "main")

    server.write_text("def launch(): pass\n", encoding="utf-8")
    assert resolves_to_definition(entry, "main")

    entry.write_text("def run(): pass\nfrom .server import launch as main\n", encoding="utf-8")
    server.write_text("from .__main__ import run as launch\n", encoding="utf-8")
    assert resolves_to_definition(entry, "main")
