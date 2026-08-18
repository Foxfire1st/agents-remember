"""Enforce single owners for git, atomic publish, and task-document publication.

``kernel/git_command.py`` owns every git subprocess. The sweep reports statically
resolvable git spawns and argv constructions, including aliases, asyncio entry points,
shell program words, and path-qualified executables.

``kernel/atomic_write.py`` owns every temp-write/replace publish. The sweep reports
``os.replace`` (including imported aliases) and one-argument ``Path.replace`` outside the
owner.

The reviewed task-document writer set owns calls to ``write_task_doc``,
``write_task_docs``, and ``write_task_doc_batch``. The census follows direct imports, import
aliases, module aliases, and relative imports so a new production publisher cannot appear outside
that set unnoticed.

Known false-positive boundaries:

1. A list of version-control-system names beginning with `"git"` is argv-shaped.
2. A one-argument ``dataclasses.replace`` attribute call is replace-shaped; the package
   currently uses the imported-name form, which is not reported.
3. Computed executable names, f-string programs, and git hidden inside a general shell
   payload are not resolved.
4. ``Path.rename``, ``os.rename``, and ``shutil.move`` are outside the atomic-publish
   rule; review new single-file replacement uses separately.

Scope is ``mcp/src/agents_remember`` excluding ``package_data`` and tests. Failures list
every offender and name the owner API that replaces it.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

GIT_RUNNER_OWNER = "kernel/git_command.py"
ATOMIC_WRITE_OWNER = "kernel/atomic_write.py"
TASK_DOCUMENT_STORE_OWNER = "tasks/store.py"
TASK_DOCUMENT_WRITER_AUTHORITIES = frozenset(
    {
        "application/task_doc_tools.py",
        "application/task_execution_topology.py",
        TASK_DOCUMENT_STORE_OWNER,
        "worktrees/modules/finalize.py",
        "worktrees/modules/start.py",
        "worktrees/organizational_completion.py",
        "worktrees/reopen.py",
    }
)
TASK_DOCUMENT_WRITE_APIS = frozenset({"write_task_doc", "write_task_docs", "write_task_doc_batch"})
TASK_DOCUMENT_WRITE_MODULES = frozenset({"agents_remember.tasks", "agents_remember.tasks.store"})

GIT_REMEDIATION = (
    "route the command through agents_remember.kernel.git_command.run_git "
    "(it takes repo_root, args, and optional work_dir/timeout) and delete the local argv"
)
ATOMIC_WRITE_REMEDIATION = (
    "route the write through agents_remember.kernel.atomic_write -- atomic_write_text / "
    "atomic_write_bytes for new content, atomic_replace to move an existing file into place"
)
TASK_DOCUMENT_WRITE_REMEDIATION = (
    "route the candidate through a reviewed task-document authority and its readiness rule; "
    "expand TASK_DOCUMENT_WRITER_AUTHORITIES only after reviewing the new publication boundary"
)

# The stdlib spawn entry points. A module reaches one either through the module object
# (``subprocess.run``) or through a name it imported from it (``from subprocess import run``).
SUBPROCESS_SPAWNS = frozenset({"run", "Popen", "check_output", "check_call", "call"})
ASYNCIO_PROGRAM_SPAWN = "create_subprocess_exec"
ASYNCIO_SHELL_SPAWN = "create_subprocess_shell"

# Program tokens that mean the git binary once the directory part is stripped.
GIT_PROGRAM_NAMES = frozenset({"git", "git.exe"})


@dataclass(frozen=True)
class Offender:
    """One place the primitive is reached outside its owner."""

    module: str
    line: int
    form: str
    detail: str

    def __str__(self) -> str:
        return f"{self.module}:{self.line}  [{self.form}] {self.detail}"


# One rule, applied to one parsed module: the sweep below is the same for both primitives.
ModuleRule = Callable[[ast.AST, str], list[Offender]]


def package_modules(package_root: Path) -> list[Path]:
    """Every module the rules apply to, in a stable order."""
    return [path for path in sorted(package_root.rglob("*.py")) if "package_data" not in path.parts]


def names_git(token: str) -> bool:
    """Whether a program token names the git binary.

    Normalize path separators and inspect the basename so absolute ``git``/``git.exe`` paths
    match. ``gitk``, ``git-lfs``, and ``github`` are different programs and do not match.
    """
    return PurePosixPath(token.replace("\\", "/")).name in GIT_PROGRAM_NAMES


def string_constants(tree: ast.AST) -> dict[str, str]:
    """Names bound to a string literal, so ``BINARY = "git"`` cannot launder the program word.

    Plain, annotated and chained assignments all bind, and the scope is the whole module
    rather than the enclosing function: over-approximating here can only ever *report* more,
    and a name that means "git" in one function and something else in another is not a shape
    this package has.
    """
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        targets: list[ast.expr]
        if isinstance(node, ast.Assign):
            targets = node.targets  # `A = B = "git"` binds every name on the left
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value.value
    return constants


def imported_names(tree: ast.AST, module_name: str, wanted: frozenset[str]) -> set[str]:
    """Bare names this module bound via ``from <module_name> import <wanted>``.

    Binding the name is the whole bypass: ``from subprocess import run`` leaves nothing for
    an attribute match to find. Resolved per module so an unrelated local ``run`` or
    ``replace`` is never mistaken for the stdlib one.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            names.update(alias.asname or alias.name for alias in node.names if alias.name in wanted)
    return names


def _module_package(module: str) -> list[str]:
    """The dotted package containing a module path relative to ``agents_remember``."""
    path = PurePosixPath(module)
    return ["agents_remember", *path.parts[:-1]]


def _import_from_origin(node: ast.ImportFrom, module: str) -> str:
    """Resolve one absolute or relative ``from`` import to its dotted module."""
    if node.level == 0:
        return node.module or ""
    package = _module_package(module)
    keep = len(package) - (node.level - 1)
    base = package[: max(keep, 0)]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _task_writer_bindings(tree: ast.AST, module: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return bare writer aliases and module aliases bound by imports."""
    writers: dict[str, str] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in TASK_DOCUMENT_WRITE_MODULES:
                    continue
                bound = alias.asname or alias.name.split(".", 1)[0]
                modules[bound] = alias.name if alias.asname else bound
        elif isinstance(node, ast.ImportFrom):
            origin = _import_from_origin(node, module)
            for alias in node.names:
                bound = alias.asname or alias.name
                if origin in TASK_DOCUMENT_WRITE_MODULES and alias.name == "*":
                    writers.update({name: name for name in TASK_DOCUMENT_WRITE_APIS})
                elif (
                    origin in TASK_DOCUMENT_WRITE_MODULES and alias.name in TASK_DOCUMENT_WRITE_APIS
                ):
                    writers[bound] = alias.name
                imported_module = f"{origin}.{alias.name}" if origin else alias.name
                if imported_module in TASK_DOCUMENT_WRITE_MODULES:
                    modules[bound] = imported_module
    return writers, modules


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _task_writer_call(
    call: ast.Call, writers: Mapping[str, str], modules: Mapping[str, str]
) -> str | None:
    if isinstance(call.func, ast.Name):
        return writers.get(call.func.id)
    dotted = _dotted_name(call.func)
    if dotted is None:
        return None
    head, _, tail = dotted.partition(".")
    imported = modules.get(head)
    if imported is None or not tail:
        return None
    canonical = f"{imported}.{tail}"
    owner, _, api = canonical.rpartition(".")
    return api if owner in TASK_DOCUMENT_WRITE_MODULES and api in TASK_DOCUMENT_WRITE_APIS else None


def module_task_document_writer_sites(tree: ast.AST, module: str) -> list[Offender]:
    """Every canonical task-document writer definition/call in one production module."""
    sites: list[Offender] = []
    if module == TASK_DOCUMENT_STORE_OWNER:
        sites.extend(
            Offender(module, node.lineno, "TaskDocument store", f"defines {node.name}")
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name in TASK_DOCUMENT_WRITE_APIS
        )
    writers, modules = _task_writer_bindings(tree, module)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        api = _task_writer_call(node, writers, modules)
        if api is not None:
            sites.append(Offender(module, node.lineno, "TaskDocument writer", f"calls {api}(...)"))
    return sorted(sites, key=lambda site: (site.line, site.form, site.detail))


def _token(node: ast.expr | None, constants: Mapping[str, str]) -> str | None:
    """The string this expression is statically known to be, or ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _argv_head(node: ast.expr | None, constants: Mapping[str, str]) -> str | None:
    """The program word of an argv display, or ``None`` when this is not one."""
    if not isinstance(node, ast.List | ast.Tuple) or not node.elts:
        return None
    return _token(node.elts[0], constants)


def _spawn_kind(call: ast.Call, aliases: set[str]) -> str | None:
    """``"program"``, ``"shell"``, ``"argv"`` -- how this spawn names what it runs."""
    func = call.func
    if isinstance(func, ast.Name):
        return "argv" if func.id in aliases else None
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr == ASYNCIO_PROGRAM_SPAWN:
        return "program"
    if func.attr == ASYNCIO_SHELL_SPAWN:
        return "shell"
    if func.attr in SUBPROCESS_SPAWNS and isinstance(func.value, ast.Name):
        return "argv" if func.value.id == "subprocess" else None
    return None


def _program_token(call: ast.Call, kind: str, constants: Mapping[str, str]) -> str | None:
    """What this spawn will execute, when that can be read off the syntax tree."""
    first = call.args[0] if call.args else None
    if kind == "program":
        return _token(first, constants)
    if kind == "argv":
        head = _argv_head(first, constants)
        if head is not None:
            return head
    # A ``shell`` spawn, or an ``argv`` spawn handed a command string (``shell=True``, or a
    # single program name): the program is the first word of that string.
    text = _token(first, constants)
    if text is None:
        return None
    words = text.split()
    return words[0] if words else None


def _git_spawn_offenders(
    tree: ast.AST, module: str, constants: Mapping[str, str]
) -> tuple[list[Offender], set[int]]:
    """Spawns of git, plus the argv nodes those spawns already account for."""
    aliases = imported_names(tree, "subprocess", SUBPROCESS_SPAWNS)
    offenders: list[Offender] = []
    consumed: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _spawn_kind(node, aliases)
        if kind is None:
            continue
        if node.args:
            consumed.add(id(node.args[0]))
        token = _program_token(node, kind, constants)
        if token is not None and names_git(token):
            offenders.append(
                Offender(module, node.lineno, "git spawn", f"spawns {token!r} directly")
            )
    return offenders, consumed


def _git_argv_offenders(
    tree: ast.AST, module: str, constants: Mapping[str, str], consumed: set[int]
) -> list[Offender]:
    """Git argv under construction, wherever it is later spawned."""
    offenders: list[Offender] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.List | ast.Tuple) or id(node) in consumed:
            continue
        head = _argv_head(node, constants)
        if head is not None and names_git(head):
            offenders.append(
                Offender(module, node.lineno, "git argv", f"builds a {head!r} argv here")
            )
    return offenders


def module_git_offenders(tree: ast.AST, module: str) -> list[Offender]:
    """Every git-program reference in one parsed module, ordered by line."""
    constants = string_constants(tree)
    spawns, consumed = _git_spawn_offenders(tree, module, constants)
    found = [*spawns, *_git_argv_offenders(tree, module, constants, consumed)]
    return sorted(found, key=lambda offender: (offender.line, offender.form))


def module_replace_offenders(tree: ast.AST, module: str) -> list[Offender]:
    """Every reach for the replace syscall in one parsed module, ordered by line."""
    aliases = imported_names(tree, "os", frozenset({"replace"}))
    offenders: list[Offender] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            offender = _replace_offender(node, module, aliases)
            if offender is not None:
                offenders.append(offender)
    return sorted(offenders, key=lambda offender: offender.line)


def _replace_offender(node: ast.Call, module: str, aliases: set[str]) -> Offender | None:
    func = node.func
    if isinstance(func, ast.Name):
        if func.id in aliases:
            return Offender(module, node.lineno, "os.replace", f"{func.id}(...) imported from os")
        return None
    if not isinstance(func, ast.Attribute) or func.attr != "replace":
        return None
    if isinstance(func.value, ast.Name) and func.value.id == "os":
        return Offender(module, node.lineno, "os.replace", "os.replace(...)")
    if len(node.args) == 1 and not node.keywords:
        return Offender(
            module,
            node.lineno,
            "Path.replace",
            f"{ast.unparse(func)}(...) renames over a destination",
        )
    return None


def _sweep(package_root: Path, owner: str, rule: ModuleRule) -> list[Offender]:
    """Apply one per-module rule to every module except the primitive's owner."""
    offenders: list[Offender] = []
    for path in package_modules(package_root):
        module = path.relative_to(package_root).as_posix()
        if module == owner:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(rule(tree, module))
    return offenders


def git_program_offenders(package_root: Path) -> list[Offender]:
    """Every place outside :data:`GIT_RUNNER_OWNER` that names the git program."""
    return _sweep(package_root, GIT_RUNNER_OWNER, module_git_offenders)


def os_replace_offenders(package_root: Path) -> list[Offender]:
    """Every place outside :data:`ATOMIC_WRITE_OWNER` that reaches the replace syscall."""
    return _sweep(package_root, ATOMIC_WRITE_OWNER, module_replace_offenders)


def task_document_writer_sites(package_root: Path) -> list[Offender]:
    """The executable census of production task-document publication authorities."""
    sites: list[Offender] = []
    for path in package_modules(package_root):
        module = path.relative_to(package_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        sites.extend(module_task_document_writer_sites(tree, module))
    return sites


def task_document_writer_offenders(package_root: Path) -> list[Offender]:
    """Task-document writer sites outside the reviewed authority set."""
    return [
        site
        for site in task_document_writer_sites(package_root)
        if site.module not in TASK_DOCUMENT_WRITER_AUTHORITIES
    ]


def report(offenders: list[Offender], *, headline: str, remediation: str) -> str:
    """The whole offender list with the fix named -- never just the first failure.

    A shape check that stops at the first offender turns one sweep into N commits of
    whack-a-mole, and each of those commits is a chance to decide the rule is not worth it.
    """
    if not offenders:
        return ""
    body = "\n".join(f"  {offender}" for offender in offenders)
    return f"{headline} ({len(offenders)} found)\n{body}\nremediation: {remediation}"
