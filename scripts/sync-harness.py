#!/usr/bin/env python3
"""Generate the self-hosted harness starter packages from one source.

The repository dogfoods its own harness configuration: ``.claude/``, ``.codex/``,
``.cursor/``, ``.github-vscode/`` (with ``.vscode/``), ``.hermes/``, ``.openclaw/``,
``.pi/`` and ``.agents/`` are real starter packages a user can copy into a workspace.
That is legitimate, but it used to mean eight independent copies of the same programs:
``render-starter.py`` existed eight times at 96 to 143 lines, and the session-start hook
four times, so a fix landed in one copy and not the others.

A starter package is copied out of this repository and run from wherever it lands, so
each program has to stay a single self-contained file. Sharing therefore happens here,
at generation time: ``scripts/harness/`` holds one definition of every shared artifact
and this script fans them out three ways --

* ``shared``   verbatim copies,
* ``composed`` one shared body plus per-harness framing, and
* the two fragment libraries, assembled per harness into standalone programs.

    python3 scripts/sync-harness.py            # write every generated file
    python3 scripts/sync-harness.py --check    # verify, write nothing, non-zero on drift
    python3 scripts/sync-harness.py --list-targets

Only the files listed in ``HARNESSES`` are managed. Everything else in a starter package
-- ``config.toml``, ``settings.json``, ``mcp.json``, ``hooks.json`` and so on -- is a
genuine single-copy per-harness file and is left alone. ``scripts/harness/README.md``
records that classification and the evidence for it.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = REPO_ROOT / "scripts" / "harness"
SHARED_ROOT = HARNESS_ROOT / "shared"
STARTER_LIBRARY = HARNESS_ROOT / "render_starter.py"
HOOK_LIBRARY = HARNESS_ROOT / "session_start_hook.py"

# Generated starter files are plain data files invoked through an interpreter
# (`python .claude/render-starter.py`, `sh .claude/render-starter.sh`, and the hook
# commands the harness configuration renders). None is executed as `./file`, so the
# executable bit is noise -- and it had already drifted onto two of the four hook
# scripts and not the other two.
GENERATED_MODE = 0o644

STARTER_NAME = "render-starter.py"
HOOK_NAME = "hooks/agents-remember-session-start.py"

# Two blank lines between top-level definitions, which is where Ruff's formatter puts
# them; generated files are meant to survive `ruff format --check` unchanged.
FRAGMENT_SEPARATOR = "\n\n\n"
BLANK_LINES_BEFORE_TOP_LEVEL = ("", "", "")

# Emission order for starter fragments. Declaring it once here keeps every generated
# file in the same order no matter what order a target lists its fragments in.
STARTER_FRAGMENT_ORDER = (
    "Renderer",
    "command_string",
    "toml_basic_string_content",
    "infer_workspace_root",
    "repository_ids",
    "replace_text",
    "write_context_file",
    "render_settings",
    "hook_script_path",
    "vscode_root",
    "render_claude_settings",
    "render_cursor_hooks",
    "render_vscode_hooks",
    "validate",
    "render_claude",
    "render_codex",
    "render_cursor",
    "render_vscode",
    "render_hermes",
    "render_openclaw",
    "render_pi",
    "render_antigravity",
    "main",
)

HOOK_FRAGMENT_ORDER = (
    "Payload",
    "hook_specific_output",
    "additional_context",
    "started_inside_workspace",
    "emit",
)

# Modules a generated file may import. Which of them a given file needs is derived from
# the fragments it actually emits (see `required_imports`), not maintained by hand.
KNOWN_IMPORTS = ("argparse", "json", "os", "platform", "shlex", "subprocess", "sys")
KNOWN_FROM_IMPORTS = (
    ("collections.abc", "Callable"),
    ("pathlib", "Path"),
)


@dataclass(frozen=True)
class StarterSpec:
    """Everything about one harness's ``render-starter.py`` that is not shared."""

    label: str
    placeholders: tuple[str, ...]
    target_files: tuple[str, ...]
    fragments: tuple[str, ...]
    entry: str
    extra_files: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    """Second validation root, as (constant name, explaining comment, relative paths)."""


@dataclass(frozen=True)
class HookSpec:
    """Everything about one harness's session-start hook that is not shared."""

    subject: str
    fragments: tuple[str, ...]
    trailer: tuple[str, ...]


@dataclass(frozen=True)
class Composed:
    """A text file made of one shared body plus per-harness framing.

    The nine files carrying the session-start directive are not byte-identical, but
    every difference is framing a harness requires around an identical body: front
    matter, a title heading, an `@`-include line, a note about where the path resolves.
    Splitting body from framing is what lets a wording change land once.
    """

    body: str
    destination: str
    prologue: tuple[str, ...] = ()
    epilogue: tuple[str, ...] = ()


@dataclass(frozen=True)
class Harness:
    key: str
    directory: str
    starter: StarterSpec
    hook: HookSpec | None = None
    shared: tuple[tuple[str, str], ...] = ()
    """Verbatim copies, as (name under scripts/harness/shared, path under `directory`)."""

    composed: tuple[Composed, ...] = ()
    extra_shared: tuple[tuple[str, str], ...] = ()
    """Verbatim copies whose destination is a repo-relative path, not under `directory`."""


STARTER_SHARED = (
    ("render-starter.sh", "render-starter.sh"),
    ("render-starter.ps1", "render-starter.ps1"),
)
MCP_SETTINGS_SHARED = ("agents-remember-settings.json", "mcp/agents-remember-settings.json")

# Two directive bodies, and the difference between them is not cosmetic. The hook and
# rules files are read from inside the workspace, so they name `ar-coordination/AGENTS.md`
# relatively. The context files Hermes, Antigravity and OpenClaw read are mirrored to the
# workspace root, so they carry the rendered absolute path and say the rules are workspace
# instructions. Which body a harness takes is a per-harness fact; the body itself is not.
SESSION_DIRECTIVE = "session-start-directive.md"
WORKSPACE_DIRECTIVE = "workspace-directive.md"

AGENTS_MD_INCLUDE = ("", "@<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md")

HOOK_DIRECTIVE = Composed(
    body=SESSION_DIRECTIVE,
    destination="hooks/agents-remember-session-start.md",
)

PLACEHOLDER_VALUES = {
    "PATH_PLACEHOLDER": "<PATH/TO/YOUR/PROJECTS_FOLDER>",
    "REPO_PLACEHOLDER": "<YOUR_REPOSITORY_FOLDER_NAME>",
    "PYTHON_PLACEHOLDER": "<PYTHON_EXECUTABLE>",
    "HOOK_SCRIPT_PLACEHOLDER": "<CLAUDE_HOOK_SCRIPT>",
    "HOOK_COMMAND_PLACEHOLDER": "<PYTHON_HOOK_COMMAND>",
}

SHARED_STARTER_FRAGMENTS = (
    "infer_workspace_root",
    "repository_ids",
    "replace_text",
    "render_settings",
    "validate",
    "main",
    "Renderer",
)

HOOK_SPECIFIC_OUTPUT_HOOK = HookSpec(
    subject="SessionStart",
    fragments=("Payload", "hook_specific_output", "emit"),
    trailer=("    emit(hook_specific_output)",),
)

HARNESSES = (
    Harness(
        key="claude",
        directory=".claude",
        starter=StarterSpec(
            label="Claude Code",
            placeholders=(
                "PATH_PLACEHOLDER",
                "REPO_PLACEHOLDER",
                "PYTHON_PLACEHOLDER",
                "HOOK_SCRIPT_PLACEHOLDER",
            ),
            target_files=("settings.json", "mcp/mcp.json", "mcp/agents-remember-settings.json"),
            fragments=(*SHARED_STARTER_FRAGMENTS, "render_claude_settings"),
            entry="render_claude",
        ),
        hook=HOOK_SPECIFIC_OUTPUT_HOOK,
        shared=(*STARTER_SHARED, MCP_SETTINGS_SHARED),
        composed=(HOOK_DIRECTIVE,),
    ),
    Harness(
        key="codex",
        directory=".codex",
        starter=StarterSpec(
            label="Codex",
            placeholders=("PATH_PLACEHOLDER", "REPO_PLACEHOLDER", "HOOK_COMMAND_PLACEHOLDER"),
            target_files=("config.toml", "mcp/agents-remember-settings.json"),
            fragments=(*SHARED_STARTER_FRAGMENTS, "command_string", "toml_basic_string_content"),
            entry="render_codex",
        ),
        hook=HookSpec(
            subject="SessionStart",
            fragments=("Payload", "hook_specific_output", "started_inside_workspace", "emit"),
            trailer=(
                "    if started_inside_workspace():",
                "        emit(hook_specific_output)",
            ),
        ),
        shared=(*STARTER_SHARED, MCP_SETTINGS_SHARED),
        composed=(HOOK_DIRECTIVE,),
    ),
    Harness(
        key="cursor",
        directory=".cursor",
        starter=StarterSpec(
            label="Cursor",
            placeholders=("PATH_PLACEHOLDER", "REPO_PLACEHOLDER", "HOOK_COMMAND_PLACEHOLDER"),
            target_files=(
                "hooks.json",
                "mcp.json",
                "mcp/agents-remember-settings.json",
                "rules/agents-remember.mdc",
            ),
            fragments=(*SHARED_STARTER_FRAGMENTS, "command_string", "render_cursor_hooks"),
            entry="render_cursor",
        ),
        hook=HookSpec(
            subject="sessionStart",
            fragments=("Payload", "additional_context", "emit"),
            trailer=("    emit(additional_context)",),
        ),
        shared=(*STARTER_SHARED, MCP_SETTINGS_SHARED),
        composed=(
            HOOK_DIRECTIVE,
            Composed(
                body=SESSION_DIRECTIVE,
                destination="rules/agents-remember.mdc",
                prologue=("---", "alwaysApply: true", "---", ""),
                epilogue=AGENTS_MD_INCLUDE,
            ),
        ),
    ),
    Harness(
        key="vscode",
        directory=".github-vscode",
        starter=StarterSpec(
            label="VS Code + Copilot",
            placeholders=("PATH_PLACEHOLDER", "REPO_PLACEHOLDER"),
            target_files=(
                "copilot-instructions.md",
                "hooks/agents-remember-session-start.json",
                "hooks/agents-remember-session-start.py",
            ),
            fragments=(
                *SHARED_STARTER_FRAGMENTS,
                "command_string",
                "hook_script_path",
                "vscode_root",
                "render_vscode_hooks",
            ),
            entry="render_vscode",
            extra_files=(
                (
                    "VSCODE_TARGET_FILES",
                    "# Rendered into the sibling .vscode/ folder, not into this one.",
                    ("mcp.json", "mcp/agents-remember-settings.json"),
                ),
            ),
        ),
        hook=HOOK_SPECIFIC_OUTPUT_HOOK,
        shared=STARTER_SHARED,
        composed=(
            HOOK_DIRECTIVE,
            Composed(
                body=SESSION_DIRECTIVE,
                destination="copilot-instructions.md",
                epilogue=(
                    "",
                    "`ar-coordination/AGENTS.md` resolves under the workspace root:",
                    "`<PATH/TO/YOUR/PROJECTS_FOLDER>/ar-coordination/AGENTS.md`.",
                ),
            ),
        ),
        # VS Code reads MCP settings from the workspace's .vscode/ folder, so the
        # settings file for this starter lives outside the starter folder.
        extra_shared=(
            ("agents-remember-settings.json", ".vscode/mcp/agents-remember-settings.json"),
        ),
    ),
    Harness(
        key="hermes",
        directory=".hermes",
        starter=StarterSpec(
            label="Hermes",
            placeholders=("PATH_PLACEHOLDER", "REPO_PLACEHOLDER"),
            target_files=("HERMES.md", "config.yaml", "mcp/agents-remember-settings.json"),
            fragments=(*SHARED_STARTER_FRAGMENTS, "write_context_file"),
            entry="render_hermes",
            extra_files=(
                (
                    "WORKSPACE_TARGET_FILES",
                    "# Mirrored to the workspace root, where Hermes reads it from.",
                    ("HERMES.md",),
                ),
            ),
        ),
        shared=(*STARTER_SHARED, MCP_SETTINGS_SHARED),
        composed=(Composed(body=WORKSPACE_DIRECTIVE, destination="HERMES.md"),),
    ),
    Harness(
        key="openclaw",
        directory=".openclaw",
        starter=StarterSpec(
            label="OpenClaw",
            placeholders=("PATH_PLACEHOLDER", "REPO_PLACEHOLDER"),
            target_files=(
                "openclaw.merge.json",
                "workspace/AGENTS.md",
                "mcp/agents-remember-settings.json",
            ),
            fragments=SHARED_STARTER_FRAGMENTS,
            entry="render_openclaw",
        ),
        shared=(*STARTER_SHARED, MCP_SETTINGS_SHARED),
        composed=(
            Composed(
                body=WORKSPACE_DIRECTIVE,
                destination="workspace/AGENTS.md",
                prologue=("# OpenClaw Workspace Instructions", ""),
                epilogue=AGENTS_MD_INCLUDE,
            ),
        ),
    ),
    Harness(
        key="pi",
        directory=".pi",
        starter=StarterSpec(
            label="Pi.dev",
            placeholders=("PATH_PLACEHOLDER", "REPO_PLACEHOLDER"),
            target_files=(
                "mcp.json",
                "mcp/agents-remember-settings.json",
                "extensions/agents-remember-start.ts",
            ),
            fragments=SHARED_STARTER_FRAGMENTS,
            entry="render_pi",
        ),
        shared=(*STARTER_SHARED, MCP_SETTINGS_SHARED),
    ),
    Harness(
        key="antigravity",
        directory=".agents",
        starter=StarterSpec(
            label="Antigravity/Gemini",
            placeholders=("PATH_PLACEHOLDER", "REPO_PLACEHOLDER"),
            target_files=("GEMINI.md", "mcp_config.json", "mcp/agents-remember-settings.json"),
            fragments=(*SHARED_STARTER_FRAGMENTS, "write_context_file"),
            entry="render_antigravity",
            extra_files=(
                (
                    "WORKSPACE_TARGET_FILES",
                    "# Mirrored to the workspace root, where Antigravity reads it from.",
                    ("GEMINI.md",),
                ),
            ),
        ),
        shared=(*STARTER_SHARED, MCP_SETTINGS_SHARED),
        composed=(
            Composed(
                body=WORKSPACE_DIRECTIVE,
                destination="GEMINI.md",
                prologue=("# Antigravity Workspace Instructions", ""),
                epilogue=AGENTS_MD_INCLUDE,
            ),
        ),
    ),
)


@dataclass(frozen=True)
class GeneratedFile:
    label: str
    path: Path
    text: str


# --- fragment extraction -----------------------------------------------------------


def fragment_sources(library: Path) -> dict[str, str]:
    """Every top-level definition in `library`, by name, as verbatim source text."""
    text = library.read_text(encoding="utf-8")
    lines = text.splitlines()
    module = ast.parse(text, filename=str(library))
    sources: dict[str, str] = {}
    for node in module.body:
        names = top_level_names(node)
        if not names:
            continue
        end_lineno = node.end_lineno
        if end_lineno is None:
            raise SystemExit(f"{library.name}: no end line for {names[0]!r}")
        source = "\n".join(lines[node.lineno - 1 : end_lineno])
        for name in names:
            sources[name] = source
    return sources


def top_level_names(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def ordered_fragments(
    requested: tuple[str, ...], order: tuple[str, ...], sources: dict[str, str], library: Path
) -> list[str]:
    """The requested fragments in emission order, refusing anything undefined.

    A fragment name that is missing from the library, or missing from the declared
    order, would otherwise silently drop code out of a generated program.
    """
    wanted = set(requested)
    undefined = sorted(wanted - set(sources))
    if undefined:
        raise SystemExit(f"{library.name}: no such fragment(s): {undefined}")
    unordered = sorted(wanted - set(order))
    if unordered:
        raise SystemExit(f"{library.name}: fragments not in the declared order: {unordered}")
    return [name for name in order if name in wanted]


def required_imports(body: str) -> list[str]:
    """Import lines for exactly the names `body` uses.

    Deriving this from the assembled text is what keeps a generated file's imports from
    drifting away from its fragments: adding a fragment that reaches for a new module
    cannot leave the import behind.
    """
    used = {node.id for node in ast.walk(ast.parse(body)) if isinstance(node, ast.Name)}
    lines = [f"import {module}" for module in KNOWN_IMPORTS if module in used]
    lines += [f"from {module} import {name}" for module, name in KNOWN_FROM_IMPORTS if name in used]
    return lines


# --- rendering ---------------------------------------------------------------------


def generated_notice(source: str) -> list[str]:
    return [
        "# Generated file -- do not edit.",
        f"# Source: {source}",
        "# Regenerate: python3 scripts/sync-harness.py",
    ]


def tuple_literal(name: str, elements: tuple[str, ...]) -> list[str]:
    """A tuple assignment written the way Ruff's formatter would leave it.

    Two or more elements keep the trailing comma Ruff reads as "stay exploded"; a
    single element's comma is syntax rather than a formatting hint, so Ruff collapses
    that form and the generator has to emit it collapsed too.
    """
    if not elements:
        return [f"{name}: tuple[str, ...] = ()"]
    if len(elements) == 1:
        return [f"{name} = ({elements[0]},)"]
    return [f"{name} = (", *(f"    {element}," for element in elements), ")"]


def starter_constants(spec: StarterSpec) -> list[str]:
    lines = [f'HARNESS_LABEL = "{spec.label}"']
    for placeholder in spec.placeholders:
        lines.append(f'{placeholder} = "{PLACEHOLDER_VALUES[placeholder]}"')
    lines += tuple_literal("PLACEHOLDERS", spec.placeholders)
    lines += tuple_literal("TARGET_FILES", quoted(spec.target_files))
    for name, comment, values in spec.extra_files:
        lines.append(comment)
        lines += tuple_literal(name, quoted(values))
    return lines


def quoted(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f'"{value}"' for value in values)


def render_starter_program(harness: Harness, sources: dict[str, str]) -> str:
    spec = harness.starter
    fragments = ordered_fragments(
        (*spec.fragments, spec.entry), STARTER_FRAGMENT_ORDER, sources, STARTER_LIBRARY
    )
    body = FRAGMENT_SEPARATOR.join(sources[name] for name in fragments)
    constants = "\n".join(starter_constants(spec))
    header = [
        "#!/usr/bin/env python3",
        f'"""Render the {spec.label} Agents Remember starter package for one workspace."""',
        "",
        *generated_notice("scripts/harness/render_starter.py"),
        "",
        "from __future__ import annotations",
        "",
        *required_imports(f"{constants}\n\n{body}"),
        "",
        constants,
        *BLANK_LINES_BEFORE_TOP_LEVEL,
    ]
    trailer = [
        *BLANK_LINES_BEFORE_TOP_LEVEL,
        'if __name__ == "__main__":',
        f"    main({spec.entry})",
        "",
    ]
    return "\n".join(header) + body + "\n".join(trailer)


def render_hook_program(label: str, hook: HookSpec, sources: dict[str, str]) -> str:
    fragments = ordered_fragments(hook.fragments, HOOK_FRAGMENT_ORDER, sources, HOOK_LIBRARY)
    body = FRAGMENT_SEPARATOR.join(sources[name] for name in fragments)
    constants = ['DIRECTIVE_PATH = Path(__file__).resolve().with_suffix(".md")']
    if "started_inside_workspace" in hook.fragments:
        constants.insert(0, "WORKSPACE_ROOT = Path(__file__).resolve().parents[2]")
    constants_text = "\n".join(constants)
    subject = f"{label} {hook.subject}"
    header = [
        "#!/usr/bin/env python3",
        f'"""Emit Agents Remember workspace startup context for {subject}."""',
        "",
        *generated_notice("scripts/harness/session_start_hook.py"),
        "",
        "from __future__ import annotations",
        "",
        *required_imports(f"{constants_text}\n\n{body}"),
        "",
        constants_text,
        *BLANK_LINES_BEFORE_TOP_LEVEL,
    ]
    trailer = [*BLANK_LINES_BEFORE_TOP_LEVEL, 'if __name__ == "__main__":', *hook.trailer, ""]
    return "\n".join(header) + body + "\n".join(trailer)


def generated_files() -> list[GeneratedFile]:
    starter_sources = fragment_sources(STARTER_LIBRARY)
    hook_sources = fragment_sources(HOOK_LIBRARY)
    files: list[GeneratedFile] = []
    for harness in HARNESSES:
        directory = REPO_ROOT / harness.directory
        files.append(
            GeneratedFile(
                label=f"{harness.starter.label} starter renderer",
                path=directory / STARTER_NAME,
                text=render_starter_program(harness, starter_sources),
            )
        )
        if harness.hook is not None:
            files.append(
                GeneratedFile(
                    label=f"{harness.starter.label} session-start hook",
                    path=directory / HOOK_NAME,
                    text=render_hook_program(harness.starter.label, harness.hook, hook_sources),
                )
            )
        for shared_name, relative in harness.shared:
            files.append(
                GeneratedFile(
                    label=f"{harness.starter.label} {shared_name}",
                    path=directory / relative,
                    text=read_shared(shared_name),
                )
            )
        for composed in harness.composed:
            files.append(
                GeneratedFile(
                    label=f"{harness.starter.label} {composed.body}",
                    path=directory / composed.destination,
                    text=compose(composed),
                )
            )
        for shared_name, repo_relative_path in harness.extra_shared:
            files.append(
                GeneratedFile(
                    label=f"{harness.starter.label} {shared_name}",
                    path=REPO_ROOT / repo_relative_path,
                    text=read_shared(shared_name),
                )
            )
    return files


def read_shared(name: str) -> str:
    path = SHARED_ROOT / name
    if not path.is_file():
        raise SystemExit(f"[sync-harness] missing canonical source: {repo_relative(path)}")
    return path.read_text(encoding="utf-8")


def compose(composed: Composed) -> str:
    body = read_shared(composed.body).rstrip("\n")
    return "\n".join([*composed.prologue, body, *composed.epilogue]) + "\n"


# --- commands ----------------------------------------------------------------------


def repo_relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def mode_of(path: Path) -> int:
    return path.stat().st_mode & 0o777


def describe_drift(generated: GeneratedFile) -> list[str]:
    """Empty when the file on disk matches; otherwise the reasons it does not."""
    if not generated.path.is_file():
        return ["file is missing"]
    problems: list[str] = []
    current = generated.path.read_text(encoding="utf-8")
    if current != generated.text:
        diff = difflib.unified_diff(
            current.splitlines(),
            generated.text.splitlines(),
            fromfile="on disk",
            tofile="generated",
            lineterm="",
        )
        problems.append("content differs:")
        problems += [f"  {line}" for line in diff]
    current_mode = mode_of(generated.path)
    if current_mode != GENERATED_MODE:
        problems.append(f"mode is {current_mode:04o}, expected {GENERATED_MODE:04o}")
    return problems


def check_targets() -> int:
    drifted = 0
    for generated in generated_files():
        problems = describe_drift(generated)
        if not problems:
            print(f"[sync-harness] ok: {repo_relative(generated.path)}")
            continue
        drifted += 1
        print(f"[sync-harness] out of sync: {repo_relative(generated.path)}")
        for line in problems:
            print(f"  {line}")
    if drifted:
        print(
            f"[sync-harness] {drifted} generated file(s) drifted; "
            "run: python3 scripts/sync-harness.py",
            file=sys.stderr,
        )
        return 1
    return 0


def sync_targets() -> int:
    for generated in generated_files():
        generated.path.parent.mkdir(parents=True, exist_ok=True)
        generated.path.write_text(generated.text, encoding="utf-8", newline="\n")
        generated.path.chmod(GENERATED_MODE)
        print(f"[sync-harness] wrote {repo_relative(generated.path)}")
    return check_targets()


def list_targets() -> int:
    print(f"starter fragments: {repo_relative(STARTER_LIBRARY)}")
    print(f"hook fragments:    {repo_relative(HOOK_LIBRARY)}")
    print(f"verbatim sources:  {repo_relative(SHARED_ROOT)}")
    for generated in generated_files():
        print(f"{repo_relative(generated.path)}  <- {generated.label}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the self-hosted harness starter packages from one source."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify that every generated file matches its source; do not write files.",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="Print the generated files and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_targets:
        return list_targets()
    if args.check:
        return check_targets()
    return sync_targets()


if __name__ == "__main__":
    raise SystemExit(main())
