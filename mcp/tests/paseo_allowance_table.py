"""The vocabulary and the per-file allowance table of the Paseo single-path scan (PNT-R02).

``test_paseo_single_path.py`` holds the scan itself: the boundary files, the rule regexes and the
planted cases. This module holds what the scan is judged against -- the path prefixes a use can
live under, the names of the rules, and ``ALLOWED``: for one rule and one file, the number of
uses that are AR's own and the reason each is. A legitimate second surface therefore needs an
entry here, with its reason, and an entry whose file is gone or trips the rule less often than
its number says is stale.

A file may trip a rule exactly as often as its entry says. One use more is a second path until it
is counted here, so a new caller inside an already allowed file still fails the checkout scan
until someone writes down why it is the same path and not another one.
"""

from __future__ import annotations

CLI = "mcp/src/agents_remember/cli/"
RUNTIME = "mcp/src/agents_remember/serving/paseo/"
SANDBOX = "scripts/pnt_sandbox/"  # PNT-R11's tooling, merged from another leaf

PACKAGE = "names a Paseo package"
PROGRAM = "starts the Paseo command line"
LITERAL = "has paseo as a bare string literal"
COMMAND_LINE = "has a string that begins a paseo command line"
RUNNER = "names the Paseo command-line runner"
SCRIPT = "names the bridge script"
ADDRESS = "reads the runtime's address or install prefix"
SHELL = "runs paseo from a shell script"
MANIFEST = "names paseo in a manifest"

# Per rule and per file: the uses of one of these names that are AR's own, as their number and
# their reason. A file may trip a rule exactly as often as its entry says: one use more is a
# second path until it is counted here, and an entry whose file is gone, or trips the rule less
# often, is stale. Both fail the scan of the checkout.
RUNTIME_COMMANDS = "PNT-R01: "
ALLOWED: dict[str, dict[str, tuple[int, str]]] = {
    PACKAGE: {
        "scripts/check-host-contract.py": (
            1,
            "checks the shipped plugin SDK pin against the build host",
        ),
        "mcp/src/agents_remember/package_data/paseo_host/package.json": (
            1,
            "the exact host dependency of the shipped whole-tree lock",
        ),
        # The dev-only fake Paseo host names the pinned server its navigation follows
        # (L123); it reaches Paseo only through the genuine bridge code it fakes.
        "dashboard/src/dev/FakePaseoHost.tsx": (
            2,
            "dev-only fake for the MIK-R121 browser test: two comments naming the pinned server truth it follows; the page itself drives the genuine bridge code",
        ),
    },
    PROGRAM: {},
    LITERAL: {
        CLI + "__main__.py": (1, "the `paseo` sub-command of AR's own command line (PNT-R01)"),
        "mcp/src/agents_remember/kernel/primitives/fresh_host.py": (
            1,
            "the directory name of the provisioned runtime under the XDG homes",
        ),
        SANDBOX + "operations.py": (
            1,
            "starts AR's own `agents_remember.cli paseo <command>`; `paseo` key of its own record",
        ),
        SANDBOX + "commands.py": (3, "the `paseo` key of the sandbox's own process record"),
        SANDBOX + "layout.py": (
            2,
            "the sandbox directory named `paseo` that holds the home and prefix",
        ),
    },
    COMMAND_LINE: {
        RUNTIME + "paseo_daemon.py": (
            3,
            RUNTIME_COMMANDS + "its docstring quotes the command line the runner builds, and "
            "one failure text of stop begins with the command's name",
        ),
        RUNTIME + "paseo_daemon_config.py": (
            1,
            RUNTIME_COMMANDS + "its docstring quotes the command it does not use for a provider "
            "entry",
        ),
        RUNTIME + "paseo_provision.py": (
            2,
            RUNTIME_COMMANDS + "provision and outstanding-stop failure texts name the command",
        ),
        SANDBOX + "commands.py": (
            10,
            "step names and output lines about AR's own `paseo` sub-commands",
        ),
        SANDBOX + "operations.py": (
            1,
            "the step name of AR's remaining `paseo <command>` status/stop path; install is public",
        ),
    },
    RUNNER: {
        RUNTIME + "paseo_install.py": (3, "install and preview use the single runtime runner"),
        RUNTIME + "paseo_node.py": (1, "Node/npm validation uses the same process runner"),
        RUNTIME + "paseo_run.py": (3, "run context and read-only install admission"),
        RUNTIME + "paseo_start.py": (
            6,
            "start/status and their typed locked helper use the same runner without install/reload",
        ),
        RUNTIME + "paseo_daemon.py": (
            8,
            RUNTIME_COMMANDS + "status and stop build the runner and pass it to their helpers",
        ),
        RUNTIME + "paseo_daemon_config.py": (
            1,
            RUNTIME_COMMANDS + "imports the failure class from the runner's module",
        ),
        RUNTIME + "paseo_provision.py": (
            7,
            RUNTIME_COMMANDS
            + "explicit provision and install build the same runner for their steps",
        ),
        RUNTIME + "paseo_settings.py": (
            3,
            RUNTIME_COMMANDS + "reads pending settings through the one runtime runner",
        ),
        CLI + "paseo_runtime.py": (
            5,
            RUNTIME_COMMANDS + "imports the failure class from the runner's module; "
            "`paseo_command` is also the name under which it parses its own sub-command",
        ),
        RUNTIME + "paseo_process_record.py": (
            1,
            RUNTIME_COMMANDS + "imports the failure class from the runner's module; it starts "
            "nothing",
        ),
    },
    ADDRESS: {
        RUNTIME + "paseo_install.py": (4, "install result and read-only preview of selected host"),
        RUNTIME + "paseo_run.py": (1, "checks the selected prefix before replacement"),
        RUNTIME + "paseo_start.py": (3, "reports/comparisons for the configured listen address"),
        RUNTIME + "paseo_start_outcome.py": (
            2,
            "hashes prefix/listen to match an in-flight start outcome; executes no host command",
        ),
        SANDBOX + "settings_expectation.py": (
            1,
            "compares the sandbox's own host block with the product's fresh block; starts no host",
        ),
        RUNTIME + "paseo_provision.py": (
            12,
            RUNTIME_COMMANDS + "provision installs into the prefix and binds the listen address",
        ),
        RUNTIME + "paseo_settings.py": (
            4,
            RUNTIME_COMMANDS + "owns the listen setting and its restart comparison",
        ),
        CLI + "paseo_catalog.py": (2, "the catalog cache is keyed by the runtime it was read from"),
        CLI + "role_launch_first_run.py": (
            3,
            "run start reads the prefix and the home of the host it provisioned, to say which "
            "host the start will use",
        ),
        "mcp/src/agents_remember/kernel/primitives/fresh_host.py": (
            1,
            "writes the `paseoRuntime` block of a host that has never been provisioned (c-13 step 3)",
        ),
        "mcp/src/agents_remember/kernel/primitives/paseo_runtime_settings.py": (
            6,
            "the settings model that defines these fields",
        ),
        SANDBOX + "layout.py": (1, "writes the `paseoRuntime` settings block of the sandbox"),
        SANDBOX + "build_tool_calls.py": (
            2,
            "compares the sandbox's home/listen with the build's resolution under the server "
            "environment and refuses a foreign host; starts no host",
        ),
        SANDBOX + "build_roots.py": (
            5,
            "reports current shared host paths and values for the sandbox check",
        ),
        SANDBOX + "safety.py": (
            2,
            "names the settings keys whose values must lie inside the sandbox",
        ),
    },
}
