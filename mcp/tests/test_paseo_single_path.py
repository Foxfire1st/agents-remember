"""Only the bridge and the PNT-R01 runtime commands reach Paseo (PNT-R02 item 1).

A text scan of every source file in the checkout. It fails when a file outside the boundary files
names a Paseo package, starts Paseo's command line, names the command-line runner or the bridge
script, or reads the runtime's address or install prefix, which is what a second path into the
daemon needs first. A legitimate use of one of those names outside the boundary gets one entry in
``ALLOWED``, for one rule and one file, with its reason.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = "mcp/src/agents_remember/cli/"
SANDBOX = "scripts/pnt_sandbox/"  # PNT-R11's tooling, merged from another leaf

# The boundary itself: no rule applies inside these files. An entry for a file that a checkout
# does not have is not an error, so leaves that are merged later can be listed here.
BOUNDARY_FILES: dict[str, str] = {
    CLI + "paseo_bridge.py": "the bridge: the one caller of the bridge script",
    CLI + "paseo_bridge.mjs": "the bridge script: the one importer of Paseo's client package",
    CLI + "paseo_command.py": "PNT-R01: the one runner of Paseo's own command line",
    CLI + "paseo_daemon.py": "PNT-R01: status and stop through that runner",
    CLI + "paseo_daemon_config.py": "PNT-R01: the daemon settings provision writes",
    CLI + "paseo_plugin_files.py": "PNT-R01: the files AR owns under the daemon home",
    CLI + "paseo_provision.py": "PNT-R01: provision through that runner",
    CLI + "paseo_runtime.py": "PNT-R01: the provision, status and stop commands",
}
BOUNDARY_DIRECTORIES: dict[str, str] = {
    "mcp/src/agents_remember/package_data/paseo_plugin/": "the AR plugin: Paseo's plugin interface",
}

PACKAGE = "names a Paseo package"
PROGRAM = "starts the Paseo command line"
LITERAL = "has paseo as a bare string literal"
COMMAND_LINE = "has a string that begins a paseo command line"
RUNNER = "names the Paseo command-line runner"
SCRIPT = "names the bridge script"
ADDRESS = "reads the runtime's address or install prefix"
SHELL = "runs paseo from a shell script"
MANIFEST = "names paseo in a manifest"

RULES: dict[str, re.Pattern[str]] = {
    PACKAGE: re.compile(r"getpaseo"),
    # The program by a path that ends in it, by a look-up, or through a package runner.
    PROGRAM: re.compile(
        r"\.bin[/\\\"', ]+paseo\b"
        r"|which\(\s*[\"']paseo\b"
        r"|\b(?:npx|bunx|npm exec|pnpm dlx)\s+(?:-\S+\s+)*paseo\b"
        r"|\bbin[/\\]paseo\b|\}[/\\]paseo\b|[\"'`][/\\]paseo[\"'`]"
        r"|[\"'`]paseo\.(?:cmd|exe)[\"'`]"
    ),
    LITERAL: re.compile(r"""["'`]paseo["'`]"""),
    COMMAND_LINE: re.compile(r"""["'`]paseo\s"""),
    RUNNER: re.compile(r"\bpaseo_command\b|\bPaseoCli\b"),
    SCRIPT: re.compile(r"paseo_bridge\.mjs|paseo_bridge\.__file__"),
    # AR_PASEO_AGENT_ID is the agent binding of design contract C5, not an address of the runtime.
    ADDRESS: re.compile(
        r"\.install_prefix\b|\binstallPrefix\b|\bAR_PASEO_(?!AGENT_ID\b)"
        r"|\.listen(?:_host|_port)?\b(?!\()"
    ),
    # A command word that is paseo or ends in /paseo, at the start of a command or after leading
    # NAME=value words; or a variable that is given the program.
    SHELL: re.compile(
        r"(?m)(?:^|[;|&(`{!]|\$\("
        r"|\b(?:if|elif|while|until|then|do|else|exec|env|nohup|sudo|time|command|xargs)\s"
        r"|\btimeout\s+\S+\s)"
        r"\s*(?:[A-Za-z_]\w*=\S*\s+)*[\"']?(?:[^\s\"';|&]*/)?paseo[\"']?(?:\s|$)"
        r"|\b[A-Za-z_]\w*=[\"']?(?:[^\s\"';|&]*/)?paseo[\"']?(?:\s|$)"
    ),
    # In a manifest: the word paseo on its own, as in a script entry or a dependency name.
    MANIFEST: re.compile(r"(?<![\w@/-])paseo(?![\w-])"),
}

# Per rule and per file: a use of one of these names that is AR's own. An entry for a file that a
# checkout does not have is not an error.
ALLOWED: dict[str, dict[str, str]] = {
    LITERAL: {
        CLI + "__main__.py": "the `paseo` sub-command of AR's own command line (PNT-R01)",
        SANDBOX + "operations.py": (
            "starts AR's own `agents_remember.cli paseo <command>`; `paseo` key of its own record"
        ),
        SANDBOX + "commands.py": "the `paseo` key of the sandbox's own process record",
        SANDBOX + "layout.py": "the sandbox directory named `paseo` that holds the home and prefix",
    },
    COMMAND_LINE: {
        SANDBOX + "commands.py": "step names and output lines about AR's own `paseo` sub-commands",
        SANDBOX + "operations.py": "the step name of AR's own `paseo <command>` sub-command",
    },
    RUNNER: {
        CLI + "paseo_process_record.py": (
            "PNT-R01: imports the failure class from the runner's module; it starts nothing"
        ),
    },
    ADDRESS: {
        CLI + "paseo_catalog.py": "the catalog cache is keyed by the runtime it was read from",
        "mcp/src/agents_remember/kernel/primitives/paseo_runtime_settings.py": (
            "the settings model that defines these fields"
        ),
        SANDBOX + "layout.py": "writes the `paseoRuntime` settings block of the sandbox",
        SANDBOX
        + "build_roots.py": "reports the configured values so the sandbox check can compare",
        SANDBOX + "safety.py": "names the settings keys whose values must lie inside the sandbox",
    },
}

CODE_SUFFIXES = {".py", ".mjs", ".cjs", ".js", ".jsx", ".ts", ".tsx", ".mts", ".cts"}
SHELL_SUFFIXES = {".sh", ".bash"}
MANIFEST_NAMES = {"package.json", "pyproject.toml", "requirements.txt"}
SHELL_INTERPRETER = re.compile(r"\A#!.*\b(?:sh|bash|dash|ksh|zsh)\b")
# Tests are not shipped source. A directory named `tests` under `mcp/src` is.
TEST_PATHS = re.compile(r"^mcp/tests/|^dashboard/e2e/|\.test\.[cm]?[jt]sx?$")


def may_be_scanned(path: str) -> bool:
    """By its path alone: outside the boundary and the tests, and of a kind the scan reads."""

    if path in BOUNDARY_FILES or path.startswith(tuple(BOUNDARY_DIRECTORIES)):
        return False
    if TEST_PATHS.search(path):
        return False
    return (
        Path(path).suffix in CODE_SUFFIXES | SHELL_SUFFIXES | {""}
        or Path(path).name in MANIFEST_NAMES
    )


def is_scanned(path: str, text: str) -> bool:
    """Whether the scan reads this file: code, a manifest, or a script without a suffix."""

    return may_be_scanned(path) and (Path(path).suffix != "" or text.startswith("#!"))


def second_paths_to_paseo(path: str, text: str) -> list[str]:
    """The rules a scanned file trips and is not allowed to trip."""

    if Path(path).name in MANIFEST_NAMES:
        names = [PACKAGE, MANIFEST]
    else:
        names = [name for name in RULES if name not in (SHELL, MANIFEST)]
        if Path(path).suffix in SHELL_SUFFIXES or SHELL_INTERPRETER.search(text):
            names.append(SHELL)
            text = "\n".join(
                line for line in text.splitlines() if not line.lstrip().startswith("#")
            )
    return [
        name for name in names if RULES[name].search(text) and path not in ALLOWED.get(name, {})
    ]


def scan_tree(root: Path) -> tuple[int, dict[str, list[str]]]:
    """Scan every tracked or untracked, not ignored file of a checkout."""

    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    scanned = 0
    offenders: dict[str, list[str]] = {}
    for path in listed:
        file = root / path
        # Only the path decides which files are opened; is_scanned then decides on the content.
        if not path or not may_be_scanned(path) or not file.is_file():
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        if not is_scanned(path, text):
            continue
        scanned += 1
        reasons = second_paths_to_paseo(path, text)
        if reasons:
            offenders[path] = reasons
    return scanned, offenders


PY = CLI + "zz_models.py"
TS = "dashboard/src/cockpit/zzPaseo.ts"
MJS = "dashboard/scripts/zz-paseo.mjs"

# The second paths an independent review planted (PNT-L3 review R1, P00 to P26), each as the file
# it was planted in. The value is the rules that must catch it; an empty tuple is a plant that a
# text scan does not catch.
PLANTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "P00 the command line by its path": (
        PY,
        'subprocess.run([prefix / "node_modules" / ".bin" / "paseo", "provider", "models"])',
        (PROGRAM, LITERAL),
    ),
    "P01 the program name in a constant": (
        PY,
        'PROGRAM = "paseo"\nexecutable = settings.install_prefix / "node_modules" / ".bin" / PROGRAM',
        (LITERAL, ADDRESS),
    ),
    "P02 the path joined from parts": (
        PY,
        'PARTS = ("node_modules", ".bin")\n'
        'executable = settings.install_prefix.joinpath(*PARTS, "paseo")',
        (LITERAL, ADDRESS),
    ),
    "P03 shlex.split of a command line": (
        PY,
        'subprocess.run(shlex.split(f"paseo provider models {provider} --json"))',
        (COMMAND_LINE,),
    ),
    "P04 a tuple of arguments": (
        PY,
        'argv = ("paseo", "provider", "ls", "--json")\nsubprocess.run(argv)',
        (LITERAL,),
    ),
    "P05 a shell string": (
        PY,
        'command = "paseo provider ls --json"\nsubprocess.run(command, shell=True)',
        (COMMAND_LINE,),
    ),
    "P06 asyncio.create_subprocess_exec": (
        PY,
        'await asyncio.create_subprocess_exec("paseo", "provider", "ls", "--json")',
        (LITERAL,),
    ),
    "P07 os.execvp": (PY, 'os.execvp("paseo", ["paseo", "attach", agent_id])', (LITERAL,)),
    "P08 the runner through importlib": (
        PY,
        'runner = importlib.import_module("agents_remember.cli.paseo_command")',
        (RUNNER,),
    ),
    "P09 the runner by a relative import": (PY, "from .paseo_command import PaseoCli", (RUNNER,)),
    "P10 the runner through an allowed module": (
        PY,
        "from agents_remember.cli.paseo_provision import PaseoCli",
        (RUNNER,),
    ),
    "P11 the runner module in a parenthesised import": (
        PY,
        "from agents_remember.cli import (\n    paseo_command,\n)",
        (RUNNER,),
    ),
    "P12 the runner by attribute access": (
        PY,
        "import agents_remember.cli as cli\ncli.paseo_command.PaseoCli(settings)",
        (RUNNER,),
    ),
    "P13 a node -e string that imports the client by a built path": (
        PY,
        'SCOPE = "@" + "getpaseo"\n'
        "code = f\"await import('{settings.install_prefix}/node_modules/{SCOPE}/client/dist/index.js')\"\n"
        'subprocess.run(["node", "--input-type=module", "-e", code])',
        (PACKAGE, ADDRESS),
    ),
    "P14 a node -e string that speaks to the daemon's socket": (
        PY,
        "code = f\"const ws = new WebSocket('ws://{settings.listen}/ws')\"\n"
        'subprocess.run(["node", "-e", code])',
        (ADDRESS,),
    ),
    "P15 Python speaks the daemon's wire protocol": (
        PY,
        "from websockets.sync.client import connect\n"
        'with connect(f"ws://{settings.listen}/ws") as daemon:\n    daemon.send("{}")',
        (ADDRESS,),
    ),
    "P16 a second caller of the bridge script": (
        PY,
        'script = Path(paseo_bridge.__file__).with_suffix(".mjs")\n'
        'subprocess.run(["node", str(script), "catalog"])',
        (SCRIPT,),
    ),
    "P17 the dashboard imports the client": (
        TS,
        'import { createPaseoClient } from "@getpaseo/client";',
        (PACKAGE,),
    ),
    "P18 the dashboard opens a socket to an address it was handed": (
        TS,
        'const socket = new WebSocket("ws://" + listen + "/ws");\n'
        'socket.onopen = () => socket.send(JSON.stringify({ type: "get_providers_snapshot_request" }));',
        (),
    ),
    "P19 the dashboard imports a specifier built from pieces": (
        TS,
        'const scope = "@" + "getpaseo";\nconst sdk = await import(scope + "/client");',
        (PACKAGE,),
    ),
    "P20 spawn with the program in a variable": (
        MJS,
        'const program = "paseo";\nspawnSync(program, ["provider", "ls", "--json"]);',
        (LITERAL,),
    ),
    "P21 execa": (MJS, 'execa("paseo", ["provider", "ls", "--json"]);', (LITERAL,)),
    "P22 a shell script without a suffix": (
        "scripts/zz-paseo",
        '#!/usr/bin/env bash\nexec "$1/node_modules/.bin/paseo" provider ls --json\n',
        (PROGRAM, SHELL),
    ),
    "P23 a .bash script": (
        "scripts/zz-paseo.bash",
        "#!/usr/bin/env bash\npaseo provider ls --json\n",
        (SHELL,),
    ),
    "P24 a .sh script": ("scripts/zz-paseo.sh", "#!/bin/sh\npaseo provider ls --json\n", (SHELL,)),
    "P25 the executable taken from the runner object": (
        PY,
        'def models(cli: "paseo_daemon.PaseoCli", provider):\n'
        '    return subprocess.run([cli.executable, "provider", "models", provider])',
        (RUNNER,),
    ),
    "P26 shipped source in a directory named tests": (
        CLI + "tests/zz_models.py",
        'subprocess.run(["paseo", "provider", "ls", "--json"])',
        (LITERAL,),
    ),
    "a name split below the package scope": (
        TS,
        'const sdk = await import("@get" + "paseo/client");',
        (),
    ),
    "a program handed in from outside": (PY, "subprocess.run([program, *arguments])", ()),
    "a manifest that depends on the client": (
        "dashboard/package.json",
        '"dependencies": { "@getpaseo/client": "0.11.0-beta.2" }',
        (PACKAGE,),
    ),
    # Review R2, plants Q04 to Q08, Q10, Q11, Q14, Q31 to Q35 and Q38.
    "Q04 a path below a bin directory variable": (
        PY,
        'bin_dir = prefix / "node_modules" / ".bin"\n'
        'subprocess.run([f"{bin_dir}/paseo", "provider", "models", provider])',
        (PROGRAM,),
    ),
    "Q05 a command line with the sub-command in a variable": (
        PY,
        'subprocess.run(shlex.split(f"paseo {command} --json"))',
        (COMMAND_LINE,),
    ),
    "Q06 a percent-formatted command line": (
        PY,
        'subprocess.run("paseo %s --json" % command, shell=True)',
        (COMMAND_LINE,),
    ),
    "Q07 a str.format command line": (
        PY,
        'subprocess.run("paseo {} --json".format(command), shell=True)',
        (COMMAND_LINE,),
    ),
    "Q08 a concatenated command line": (
        PY,
        'subprocess.run("paseo " + " ".join(arguments), shell=True)',
        (COMMAND_LINE,),
    ),
    "Q10 an absolute path to the program": (
        PY,
        'subprocess.run(["/usr/local/bin/paseo", "provider", "ls", "--json"])',
        (PROGRAM,),
    ),
    "Q11 the Windows program name": (
        PY,
        'subprocess.run([shutil.which("paseo.cmd"), "provider", "ls", "--json"])',
        (PROGRAM,),
    ),
    "Q14 a package.json script": (
        "dashboard/zz/package.json",
        '{"name": "zz", "private": true, "scripts": {"providers": "paseo provider ls --json"}}',
        (MANIFEST,),
    ),
    "Q31 shell: if": (
        "scripts/zz-paseo.sh",
        "#!/bin/sh\nif paseo daemon status --json >/dev/null; then\n  echo up\nfi\n",
        (SHELL,),
    ),
    "Q32 shell: an environment assignment before the command": (
        "scripts/zz-paseo.sh",
        '#!/bin/sh\nPASEO_HOME="$1" paseo provider ls --json\n',
        (SHELL,),
    ),
    "Q33 shell: a timeout wrapper": (
        "scripts/zz-paseo.sh",
        "#!/bin/sh\ntimeout 30 paseo provider ls --json\n",
        (SHELL,),
    ),
    "Q34 shell: the program name in a variable": (
        "scripts/zz-paseo.sh",
        "#!/bin/sh\nPASEO=paseo\n$PASEO provider ls --json\n",
        (SHELL,),
    ),
    "Q35 shell: a while condition": (
        "scripts/zz-paseo.sh",
        "#!/bin/sh\nwhile ! paseo daemon status >/dev/null 2>&1; do sleep 1; done\n",
        (SHELL,),
    ),
    "Q38 a template literal command line": (
        MJS,
        "export const run = (command) => execSync(`paseo ${command} --json`);",
        (COMMAND_LINE,),
    ),
    "the program name with a Windows suffix": (
        PY,
        'subprocess.run(["paseo.exe", "provider", "ls", "--json"])',
        (PROGRAM,),
    ),
    "the program looked up under another suffix": (
        PY,
        'program = shutil.which("paseo.bat")',
        (PROGRAM,),
    ),
    "the program appended to a directory": (
        PY,
        'subprocess.run([str(bin_dir) + "/paseo", "provider", "ls", "--json"])',
        (PROGRAM,),
    ),
    **{
        f"shell: {line}": ("scripts/zz-paseo.sh", f"#!/bin/sh\n{line}\n", (SHELL,))
        for line in (
            "{ paseo daemon status; } >/dev/null",
            "if false; then :; elif paseo daemon status; then echo up; fi",
            "until paseo daemon status; do sleep 1; done",
            "command paseo provider ls --json",
            "echo codex | xargs paseo provider models",
        )
    },
    "a manifest dependency named paseo": (
        "tools/zz/requirements.txt",
        "paseo==0.11.0\n",
        (MANIFEST,),
    ),
    "a variable that only looks like the agent binding": (
        PY,
        'ids = os.environ["AR_PASEO_AGENT_IDS"]',
        (ADDRESS,),
    ),
    "a shell pipeline": (
        "scripts/zz-paseo.sh",
        "#!/bin/sh\n# paseo provider ls\nmodels=$(cd /tmp && paseo provider models codex | head -1)\n",
        (SHELL,),
    ),
    "the command line through npx": (PY, 'os.system("npx --yes paseo daemon status")', (PROGRAM,)),
    "the bridge script by name": (
        PY,
        'script = Path(__file__).with_name("paseo_bridge.mjs")',
        (SCRIPT,),
    ),
    "a bridge variable of its own": (PY, 'url = os.environ["AR_PASEO_URL"]', (ADDRESS,)),
    "a file allowed for one rule trips another": (
        CLI + "__main__.py",
        'paseo = sub.add_parser("paseo")\nprefix = settings.install_prefix',
        (ADDRESS,),
    ),
}

# Uses of the same names that are AR's own and must not trip the scan.
OWN_USES: dict[str, tuple[str, str]] = {
    "the bridge's public function": (
        PY,
        "from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call\n"
        'reply = bridge_call(config, "catalog", {})',
    ),
    "the PNT-R01 commands as functions": (
        PY,
        "from agents_remember.cli.paseo_provision import provision_runtime\n"
        "from agents_remember.cli.paseo_daemon import runtime_status, stop_runtime",
    ),
    "AR's own sub-command, where it is defined": (
        CLI + "__main__.py",
        'paseo = sub.add_parser(\n    "paseo",\n    help="Paseo runtime")',
    ),
    "the host fields of an execution": (
        PY,
        'receipt["execution"] = {"kind": "paseo-agent", "agentId": agent_id}',
    ),
    "the agent binding of contract C5": (PY, 'agent_id = os.environ["AR_PASEO_AGENT_ID"]'),
    "the settings block as a whole": (PY, "settings = config.paseo_runtime"),
    "a server that listens": (MJS, "server.listen(port);"),
    "AR's own sub-command in a shell script": (
        "scripts/zz-run.sh",
        '#!/bin/sh\n# the paseo runtime\nagents-remember paseo status --config "$1"\n',
    ),
    "a script in another language": (
        "scripts/zz-tool",
        "#!/usr/bin/env python3\npaseo = load()\n",
    ),
}


class SinglePathTests(unittest.TestCase):
    def test_only_the_bridge_and_the_runtime_commands_reach_paseo(self) -> None:
        scanned, offenders = scan_tree(REPO_ROOT)

        self.assertGreater(scanned, 500, "the scan found too few source files to mean anything")
        self.assertTrue((REPO_ROOT / CLI / "paseo_bridge.py").is_file())
        self.assertTrue((REPO_ROOT / CLI / "paseo_bridge.mjs").is_file())
        self.assertEqual(offenders, {})

    def test_the_scan_catches_each_kind_of_second_path(self) -> None:
        """Each planted second path trips the rules named for it, and AR's own uses trip none.

        What a text scan cannot catch, and a reviewer has to: a browser socket opened to an
        address the page was handed (P18); a package, program or script name assembled at run
        time from pieces smaller than the names the rules look for; a program path or address
        that arrives as an argument or in the environment; and a boundary file that hands its
        runner or the settings' address out under another name.

        Also open: file kinds the scan does not read (data files such as other `.json` and
        `.toml` files, a `Makefile` or `justfile`, `.ps1` scripts, workflow files, `.html`
        pages); a file that is allowed for a rule using that rule again for a second path; and
        an address read from the raw settings mapping, or held under one of AR's own names (the
        frame URL of the embed list, the sandbox layout's listen address).
        """

        for label, (path, text, rules) in PLANTS.items():
            with self.subTest(label):
                self.assertTrue(is_scanned(path, text), "the scan does not read this file")
                found = second_paths_to_paseo(path, text)
                self.assertEqual(sorted(found), sorted(rules))
        for label, (path, text) in OWN_USES.items():
            with self.subTest(label):
                self.assertTrue(is_scanned(path, text), "the scan does not read this file")
                self.assertEqual(second_paths_to_paseo(path, text), [])
        for path in ("mcp/tests/test_x.py", "dashboard/e2e/x.spec.ts", "dashboard/src/x.test.tsx"):
            with self.subTest(path):
                self.assertFalse(is_scanned(path, ""))
        for rule, files in ALLOWED.items():
            self.assertIn(rule, RULES)
            for path, reason in files.items():
                self.assertNotIn(path, BOUNDARY_FILES)
                self.assertGreater(len(reason), 20, f"{path} is allowed without a reason")

    def test_the_tree_scan_reports_exactly_the_planted_files(self) -> None:
        planted: dict[str, tuple[str, list[str]]] = {
            CLI + "zz_models.py": ('subprocess.run(["paseo", "provider", "ls"])\n', [LITERAL]),
            "dashboard/src/cockpit/zzPaseo.tsx": (
                'import { createPaseoClient } from "@getpaseo/client";\n',
                [PACKAGE],
            ),
            "dashboard/scripts/zz-paseo.mjs": (
                "export const run = (command) => execSync(`paseo ${command} --json`);\n",
                [COMMAND_LINE],
            ),
            "scripts/zz-paseo.sh": (
                "#!/bin/sh\nif paseo daemon status; then echo up; fi\n",
                [SHELL],
            ),
            "scripts/zz-paseo": (
                '#!/usr/bin/env bash\nPASEO_HOME="$1" paseo provider ls\n',
                [SHELL],
            ),
            "dashboard/zz/package.json": (
                '{"scripts": {"providers": "paseo provider ls"}}\n',
                [MANIFEST],
            ),
            "tools/zz/pyproject.toml": ('dependencies = ["paseo"]\n', [MANIFEST]),
            "tools/zz/requirements.txt": ("getpaseo-client==0.11.0\n", [PACKAGE]),
            CLI + "tests/zz_models.py": ("cli = PaseoCli(settings)\n", [RUNNER]),
            "mcp/src/agents_remember/package_data/other/zz.mjs": (
                'import "@getpaseo/client";\n',
                [PACKAGE],
            ),
            "scripts/zz_untracked.py": ('url = f"ws://{settings.listen}/ws"\n', [ADDRESS]),
        }
        untracked = {"scripts/zz_untracked.py"}
        clean = {
            CLI + "zz_catalog.py": 'reply = bridge_call(config, "catalog", {})\n',
            CLI + "__main__.py": 'paseo = sub.add_parser("paseo")\n',
        }
        unread = {
            CLI + "paseo_command.py": 'class PaseoCli:\n    program = "paseo"\n',
            "mcp/src/agents_remember/package_data/paseo_plugin/index.server.ts": (
                'import { definePlugin } from "@getpaseo/plugin";\n'
            ),
            "mcp/tests/test_zz.py": 'ARGV = ["paseo", "provider", "ls"]\n',
            "node_modules/@getpaseo/client/index.js": "export const name = '@getpaseo/client'\n",
            "docs/zz.md": "Run `paseo provider ls --json`.\n",
            ".gitignore": "node_modules/\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", root.as_posix()], check=True)
            files = {**{path: text for path, (text, _rules) in planted.items()}, **clean, **unread}
            for path, text in files.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(text, encoding="utf-8")
            tracked = sorted(set(files) - untracked - {"node_modules/@getpaseo/client/index.js"})
            subprocess.run(["git", "-C", root.as_posix(), "add", "--", *tracked], check=True)

            scanned, offenders = scan_tree(root)

        self.assertEqual(offenders, {path: rules for path, (_text, rules) in planted.items()})
        self.assertEqual(scanned, len(planted) + len(clean))


if __name__ == "__main__":
    unittest.main()
