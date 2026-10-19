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
    "cli/role_*.py",
    "cli/role_*/**/*.py",
    "cli/leaf_enclosure_start.py",
    "cli/*.mjs",
    "application/agent_binding.py",
    "application/role_launch_context.py",
)
# The one place in the scanned code where a harness's name stays: the dashboard command's help
# text for --config names the folder in which its settings discovery looks. It is a folder name
# the discovery (cli/discovery.py) reads; no launch decision depends on it, and the word cannot
# go without changing that help text.
NAMED_EXCEPTIONS = {"cli/dashboard.py": ".claude/mcp/agents-remember-settings.json"}
# Modules where an agent kind's name IS this build's launch decision, required by MIK-R71:
# rule 21 names the least permissive mode for each agent kind (Claude `default`, Codex
# `auto`), and the launch grants a new agent the read of its own assignment through the
# host mechanism of its provider. Like RULE6_MODULES these are scanned but their agent
# names are admitted: the decision cannot name the agent kind it decides for without
# naming it.
RULE21_MODULES = {
    "cli/paseo_catalog.py": (
        "the rule-21 least-permissive-mode table (least permissive supported mode for "
        "this agent kind): the reference names the mode for each agent kind, Claude "
        "`default` and Codex `auto`; MIK-R71 rule 21"
    ),
    "cli/paseo_launch.py": (
        "the provider's own grant mechanism for rule 22 (the installed host grants by "
        "exact tool name for Claude; Codex carries a side effect that prevents the same "
        "grant); MIK-R71 rules 21 and 22"
    ),
}
# The exact harness words each R71 file may name: an unrelated new provider decision in
# either file fails the guard. Unlike RULE6's whole-module allowance, the admission is
# bound to these occurrences; a file that stops naming them, or names more, is refused.
RULE21_ADMITTED_NAMES = {
    "cli/paseo_catalog.py": {"claude", "codex"},
    "cli/paseo_launch.py": {"Claude", "Codex", "claude"},
}


# MIK-R104/105: the qualified presenter is a confined capability, not a generic role
# launch. These two owners name the only provider whose native tool boundary was
# qualified (D341); another provider name must fail until separately qualified.
PRESENTER_MODULES = {
    "canvas/presenter_native.py": "the qualified presenter start refuses providers "
    "without the measured native tool boundary; it is not a generic launch default",
    "cli/paseo_bridge.mjs": "the host-side presenter create applies the qualified "
    "provider's native empty built-in tools and strict MCP config on every resume",
}
PRESENTER_ADMITTED_NAMES = {name: {"Claude", "claude"} for name in PRESENTER_MODULES}
ADMITTED_NAMES = RULE21_ADMITTED_NAMES | PRESENTER_ADMITTED_NAMES


def guard_passes(name: str, found: list[str]) -> bool:
    """Whether the harness-name guard accepts ``found`` in the named scanned file."""

    if name in RULE6_MODULES:
        return found != []
    if name in ADMITTED_NAMES:
        return set(found) == ADMITTED_NAMES[name]
    return found == []


# Modules where a harness's name IS this build's launch decision, ruled by the architect
# (MIK-R112 rule 6, choice b; MIK-R132): what the role start hands the host so that one
# agent does not load the product's other server. Their harness names are admitted: the
# mapping cannot say for which harness the host offers a setting without naming it.
RULE6_MODULES = {
    "application/agent_binding.py": (
        "the exclusion record of MIK-R132 (launch_server_exclusion, exclusion_agent_options, "
        "exclusion_keeps_copy_out): the one provider for which the pinned host reads a setting "
        "of one launch, and the record the first message and the role-start answer derive from"
    ),
}
HARNESS_NAMES = ("codex", "claude", "pi", "hermes", "eve", "opencode", "copilot", "omp")
# A word ends at anything that is not a letter, at an underscore and at a change of case, so a
# harness's name is found inside an identifier too.
WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")


def harness_names_in(text: str, excepted: str = "") -> list[str]:
    scanned = text.replace(excepted, "") if excepted else text
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
    return sorted(seam | importers | imported)


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
            "cli/role_launch_routes.py",
            "cli/role_launch_receipts.py",
            "cli/role_launch_preparation.py",
            "cli/role_handover_artifacts.py",
            "cli/leaf_enclosure_start.py",
            "application/agent_binding.py",
            "mcp/tools/core.py",
            "cli/dashboard.py",
            "models/role_launcher.py",
            "kernel/primitives/paseo_runtime_settings.py",
        ):
            self.assertIn(expected, names)
        # No module is excepted whole: the capsule compiler's launch step, the launch capsule's
        # model and the package's error types are scanned like the rest.
        for scanned_too in (
            "application/role_capsules/launch.py",
            "serving/launch_capsule.py",
            "errors.py",
        ):
            self.assertIn(scanned_too, names)
        # The ruled exception is exactly one module: an entry that no longer applies has to go.
        self.assertEqual(sorted(RULE6_MODULES), ["application/agent_binding.py"])
        for _name, reason in RULE6_MODULES.items():
            self.assertGreater(len(reason), 40)
        # The R71 exception is exactly two modules: an entry that no longer applies has to go.
        self.assertEqual(sorted(RULE21_MODULES), ["cli/paseo_catalog.py", "cli/paseo_launch.py"])
        for _name, reason in RULE21_MODULES.items():
            self.assertGreater(len(reason), 40)
        self.assertEqual(
            sorted(PRESENTER_MODULES), ["canvas/presenter_native.py", "cli/paseo_bridge.mjs"]
        )
        for reason in PRESENTER_MODULES.values():
            self.assertGreater(len(reason), 40)
        for path, name in zip(scanned, names, strict=True):
            with self.subTest(name):
                text = path.read_text(encoding="utf-8")
                found = sorted(set(harness_names_in(text, NAMED_EXCEPTIONS.get(name, ""))))
                self.assertTrue(
                    guard_passes(name, found), f"{name} names outside its admission: {found}"
                )
        # The R71 admission is exact: the admitted words pass, an unrelated new
        # provider word fails, and silence fails (the mechanism must stay named).
        for name, admitted in ADMITTED_NAMES.items():
            with self.subTest(witness=name):
                self.assertTrue(guard_passes(name, sorted(admitted)))
                self.assertFalse(guard_passes(name, sorted(admitted | {"eve"})))
                self.assertFalse(guard_passes(name, []))
        # A mutant probe on the real files: an unrelated provider decision added to
        # either R71 file's actual text fails the guard, so the admission cannot
        # silently widen to new harness/provider branches.
        for name in ADMITTED_NAMES:
            with self.subTest(mutant=name):
                text = (PACKAGE / name).read_text(encoding="utf-8")
                mutated = (
                    text + '\n_DEFAULT_EVE_MODE = "auto"  # an unrelated new provider decision\n'
                )
                found = sorted(set(harness_names_in(mutated)))
                self.assertIn("eve", [word.lower() for word in found])
                self.assertFalse(guard_passes(name, found), f"{name} admits {found}")
        # The scan sees what it is meant to catch, inside an identifier too, and passes words
        # that merely contain a harness's letters.
        caught: dict[str, list[str]] = {
            'if provider == "eve":': ["eve"],
            "HARNESSES = ('Codex', 'pi')": ["Codex", "pi"],
            'WITHOUT_TOOL_SERVERS = ("eve", "hermes")': ["eve", "hermes"],
            "if is_eve_provider(provider):": ["eve"],
            "EVE_ID = provider": ["EVE"],
            "isClaudeProvider(entry)": ["Claude"],
            "capsule.codex_delivery.trusted_instructions": ["codex"],
            "capsule.delivery.trusted_instructions": [],
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
                "cli/start_tool_rules.py": "from agents_remember.cli import paseo_launch\n",
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
                "cli/role_rules/tables.py": "import json\n",
                # The package's error types: the launch imports them, so they are scanned.
                "errors.py": "class AgentsRememberError(Exception): ...\n",
            }
            not_launch_code = {
                # It imports nothing of the seam and the seam does not import it.
                "kernel/harness_table.py": "import json\n",
                # Two steps from the seam: a module the launch imports imports it.
                "kernel/second_step.py": "import json\n",
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
