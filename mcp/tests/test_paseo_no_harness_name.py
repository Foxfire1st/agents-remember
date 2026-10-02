"""The code a launch runs through names no harness: the scan, and what it reads."""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "agents_remember"
# The code a launch runs through: every module and package of the seam, found by name, every
# script beside the bridge, every module of the package that imports one of the seam's modules,
# and every module one of the seam's modules imports, one step away, so that new code is scanned
# without anyone listing it. It serves every harness alike, so it names none.
LAUNCH_CODE_PATTERNS = (
    "cli/paseo_*.py",
    "cli/paseo_*/**/*.py",
    "cli/orca_*.py",
    "cli/orca_*/**/*.py",
    "cli/leaf_enclosure_start.py",
    "cli/*.mjs",
    "application/agent_binding.py",
    "application/orca_task_context.py",
)
# Modules the seam imports that are core modules of the line this build was copied from. Each
# names a harness for a purpose of its own, and no launch decision of this build reads the name.
INHERITED_MODULES = {
    "application/role_capsules/launch.py": (
        "the capsule compiler's launch step: it knows the two carriers of the base line by the "
        "harness they were built for; this build compiles a capsule for no carrier"
    ),
    "serving/launch_capsule.py": (
        "the launch capsule's model of the base line, which lists the harnesses that had a "
        "carrier; the launch imports its request type only"
    ),
    "errors.py": (
        "the package's error types, among them those of the base line's app-server client; the "
        "runtime commands import the common base class only"
    ),
}
# The one place in the scanned code where a harness's name stays: the dashboard command's help
# text for --config names the folder in which its settings discovery looks. It is a folder name
# the discovery (cli/discovery.py) reads; no launch decision depends on it, and the word cannot
# go without changing that help text.
NAMED_EXCEPTIONS = {"cli/dashboard.py": ".claude/mcp/agents-remember-settings.json"}
HARNESS_NAMES = ("codex", "claude", "pi", "hermes", "eve", "opencode", "copilot", "omp")
# A word ends at anything that is not a letter, at an underscore and at a change of case, so a
# harness's name is found inside an identifier too.
WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")
# The one identifier of the capsule compiler that carries a harness's name and selects nothing.
INHERITED_IDENTIFIER = "codex_delivery"


def harness_names_in(text: str, excepted: str = "") -> list[str]:
    scanned = text.replace(INHERITED_IDENTIFIER, "")
    if excepted:
        scanned = scanned.replace(excepted, "")
    return [word for word in WORD.findall(scanned) if word.lower() in HARNESS_NAMES]


def module_name(package: Path, path: Path) -> str:
    return ".".join((package.name, *path.relative_to(package).with_suffix("").parts))


def imported_names(package: Path, path: Path) -> set[str]:
    """Every dotted name a module imports, relative imports resolved against its own place."""

    own = module_name(package, path).split(".")
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = own[: len(own) - node.level] if node.level else []
            module = ".".join([*base, *(node.module.split(".") if node.module else [])])
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def module_file(package: Path, name: str) -> Path | None:
    """The file of the package that a dotted name stands for, when it stands for one."""

    parts = name.split(".")
    if parts[0] != package.name:
        return None
    base = package.joinpath(*parts[1:])
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def launch_code(package: Path) -> list[Path]:
    """The files the harness-name scan reads: the seam, what imports it and what it imports."""

    seam = {path for pattern in LAUNCH_CODE_PATTERNS for path in package.glob(pattern)}
    sources = {path for path in seam if path.suffix == ".py"}
    modules = {module_name(package, path) for path in sources}
    importers = {
        path
        for path in package.rglob("*.py")
        if path not in seam and modules & imported_names(package, path)
    }
    imported = {
        file
        for path in sources
        for name in imported_names(package, path)
        if (file := module_file(package, name)) is not None
    }
    inherited = {package / name for name in INHERITED_MODULES}
    return sorted((seam | importers | imported) - inherited)


class NoHarnessNameTests(unittest.TestCase):
    def test_the_launch_code_names_no_harness(self) -> None:
        scanned = launch_code(PACKAGE)
        names = [path.relative_to(PACKAGE).as_posix() for path in scanned]
        # The patterns find the seam's modules, the ones of this leaf among them; the import rules
        # add the modules that use them (the tool server's tools, the dashboard command) and the
        # modules they use (the launcher's models, the runtime's settings).
        for expected in (
            "cli/paseo_launch.py",
            "cli/paseo_catalog.py",
            "cli/paseo_bridge.mjs",
            "cli/orca_task_routes.py",
            "cli/orca_task_receipts.py",
            "cli/orca_task_preparation.py",
            "cli/orca_handover_artifacts.py",
            "cli/leaf_enclosure_start.py",
            "application/agent_binding.py",
            "mcp/tools/core.py",
            "cli/dashboard.py",
            "models/orca_launcher.py",
            "kernel/primitives/paseo_runtime_settings.py",
        ):
            self.assertIn(expected, names)
        # The modules excepted whole are exactly three, each still imported by the seam and each
        # still naming a harness: an exception that no longer applies has to go.
        self.assertEqual(
            sorted(INHERITED_MODULES),
            ["application/role_capsules/launch.py", "errors.py", "serving/launch_capsule.py"],
        )
        imported_by_the_seam = {
            name
            for pattern in LAUNCH_CODE_PATTERNS
            for path in PACKAGE.glob(pattern)
            if path.suffix == ".py"
            for name in imported_names(PACKAGE, path)
        }
        for name, reason in INHERITED_MODULES.items():
            with self.subTest(inherited=name):
                path = PACKAGE / name
                self.assertNotIn(name, names)
                self.assertIn(module_name(PACKAGE, path), imported_by_the_seam)
                self.assertNotEqual(harness_names_in(path.read_text(encoding="utf-8")), [])
                self.assertGreater(len(reason), 40)
        for path, name in zip(scanned, names, strict=True):
            with self.subTest(name):
                text = path.read_text(encoding="utf-8")
                found = sorted(set(harness_names_in(text, NAMED_EXCEPTIONS.get(name, ""))))
                self.assertEqual(found, [], f"{name} names a harness: {found}")
        # The scan sees what it is meant to catch, inside an identifier too, and passes the one
        # inherited identifier and words that merely contain a harness's letters.
        caught: dict[str, list[str]] = {
            'if provider == "eve":': ["eve"],
            "HARNESSES = ('Codex', 'pi')": ["Codex", "pi"],
            'WITHOUT_TOOL_SERVERS = ("eve", "hermes")': ["eve", "hermes"],
            "if is_eve_provider(provider):": ["eve"],
            "EVE_ID = provider": ["EVE"],
            "isClaudeProvider(entry)": ["Claude"],
            "capsule.codex_delivery.trusted_instructions": [],
            "an api key, several steps, an event and every option": [],
        }
        for text, harnesses in caught.items():
            with self.subTest(text):
                self.assertEqual(harness_names_in(text), harnesses)
        # The named exception covers its one phrase and nothing else in that file.
        phrase = NAMED_EXCEPTIONS["cli/dashboard.py"]
        self.assertEqual(harness_names_in(f"the nearest {phrase}", phrase), [])
        self.assertEqual(harness_names_in(f"{phrase} for claude", phrase), ["claude"])

    def test_the_scan_finds_code_the_seam_uses_or_is_used_by_without_being_listed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "agents_remember"
            tuple_of_names = 'WITHOUT_TOOL_SERVERS = ("eve", "hermes")\n'
            files = {
                "cli/paseo_launch.py": (
                    "from agents_remember.cli import provider_rules\n"
                    "from agents_remember.application.launch_rules import WITHOUT_TOOL_SERVERS\n"
                    "from ..models import launcher_models\n"
                    "from agents_remember.errors import AgentsRememberError\n"
                ),
                # Seam code by its use, not by its name: each imports a scanned module.
                "cli/role_tool_rules.py": "from agents_remember.cli import paseo_launch\n",
                "mcp/tools/role_agents.py": "from ...cli.paseo_launch import build\n",
                "cli/sibling_rules.py": "from . import paseo_launch\n",
                # Helper modules that import nothing of the seam: the launch imports them.
                "cli/provider_rules.py": "import json\n",
                "application/launch_rules.py": "import json\n",
                "models/launcher_models.py": "from agents_remember.kernel import second_step\n",
                # Scripts beside the bridge, whatever their names, and a package of the seam.
                "cli/paseo_bridge_rules.mjs": "export const rules = {}\n",
                "cli/bridge_rules.mjs": "export const rules = {}\n",
                "cli/paseo_rules/__init__.py": "import json\n",
                "cli/orca_rules/tables.py": "import json\n",
            }
            not_launch_code = {
                # It imports nothing of the seam and the seam does not import it.
                "kernel/harness_table.py": "import json\n",
                # Two steps from the seam: a module the launch imports imports it.
                "kernel/second_step.py": "import json\n",
                # A module excepted by name, although the launch imports it.
                "errors.py": "class AgentsRememberError(Exception): ...\n",
            }
            for name, text in {**files, **not_launch_code}.items():
                path = package / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text + tuple_of_names, encoding="utf-8")
            scanned = [path.relative_to(package).as_posix() for path in launch_code(package)]
            self.assertEqual(scanned, sorted(files))
            for path in launch_code(package):
                self.assertEqual(
                    harness_names_in(path.read_text(encoding="utf-8")), ["eve", "hermes"]
                )


if __name__ == "__main__":
    unittest.main()
