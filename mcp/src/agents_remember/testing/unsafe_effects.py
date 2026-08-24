"""Closed positive effect model for bounded direct-test diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.testing.selection_contract import UnsafeEffectFamily


@dataclass(frozen=True)
class UnsafeEffectRule:
    """One stable family with public reason text and structural module anchors."""

    family: UnsafeEffectFamily
    reason: str
    module_prefixes: tuple[str, ...]


UNSAFE_EFFECT_RULES = (
    UnsafeEffectRule(
        UnsafeEffectFamily.GIT_WORKTREE,
        "Git, repository, and worktree behavior remains Dagger-only",
        (
            "git",
            "dulwich",
            "agents_remember.kernel.git_command",
            "agents_remember.worktrees",
        ),
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.PROCESS_CONTROL,
        "subprocesses, PTYs, signals, and process control remain Dagger-only",
        ("subprocess", "multiprocessing", "pty", "signal", "termios", "resource"),
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.SOCKET_SERVICE,
        "sockets, ports, services, and network clients remain Dagger-only",
        (
            "socket",
            "ssl",
            "http",
            "urllib",
            "requests",
            "httpx",
            "fastapi",
            "uvicorn",
            "websockets",
        ),
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.PROVIDER_CONTAINER,
        "providers, containers, Docker, and Dagger behavior remains Dagger-only",
        ("dagger", "docker", "agents_remember.providers"),
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.BROWSER_EXTERNAL,
        "browser, UI, and external-environment behavior remains Dagger-only",
        (
            "playwright",
            "selenium",
            "webbrowser",
            "agents_remember.serving",
        ),
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.MACHINE_STATE,
        "machine configuration, credentials, home state, and persistent files remain Dagger-only",
        ("keyring", "getpass", "pwd", "grp", "tempfile", "shutil"),
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.MUTABLE_GLOBAL_STATE,
        "unguarded process-global mutation remains Dagger-only",
        ("atexit", "locale"),
    ),
    UnsafeEffectRule(
        UnsafeEffectFamily.DURABILITY_INTEGRATION,
        "durability, recovery, lifecycle, and integration behavior remains Dagger-only",
        (
            "sqlite3",
            "agents_remember.controlplane",
            "agents_remember.memory",
            "agents_remember.tasks",
            "agents_remember.application.lifecycle",
        ),
    ),
)


# Imports on this list are known to have no prohibited import-time effect. Calls are checked
# separately; importing ``os`` or ``sys`` does not authorize environment or path mutation.
ALLOWED_EXTERNAL_MODULE_PREFIXES = (
    "__future__",
    "base64",
    "binascii",
    "builtins",
    "collections",
    "contextlib",
    "copy",
    "dataclasses",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "hashlib",
    "heapq",
    "hmac",
    "inspect",
    "io",
    "itertools",
    "json",
    "math",
    "operator",
    "os",
    "pathlib",
    "pydantic",
    "pytest",
    "re",
    "statistics",
    "string",
    "sys",
    "textwrap",
    "types",
    "typing",
    "unittest",
    "uuid",
)

SAFE_BUILTIN_CALLS = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "bytes",
        "dict",
        "enumerate",
        "float",
        "format",
        "frozenset",
        "hash",
        "int",
        "isinstance",
        "issubclass",
        "len",
        "list",
        "max",
        "min",
        "next",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
    }
)

SAFE_QUALIFIED_CALL_PREFIXES = (
    "collections.",
    "dataclasses.asdict",
    "dataclasses.astuple",
    "dataclasses.dataclass",
    "dataclasses.fields",
    "dataclasses.is_dataclass",
    "dataclasses.replace",
    "decimal.Decimal",
    "enum.auto",
    "fractions.Fraction",
    "functools.cache",
    "functools.cached_property",
    "functools.lru_cache",
    "hashlib.",
    "hmac.",
    "itertools.",
    "json.dumps",
    "json.loads",
    "math.",
    "operator.",
    "os.fspath",
    "os.path.",
    "pathlib.PurePath",
    "pathlib.PurePosixPath",
    "pathlib.PureWindowsPath",
    "pytest.fixture",
    "pytest.mark.",
    "pytest.param",
    "pytest.raises",
    "re.",
    "statistics.",
    "textwrap.",
    "typing.cast",
    "unittest.skip",
    "unittest.skipIf",
    "unittest.skipUnless",
    "uuid.UUID",
)

SAFE_VALUE_METHODS = frozenset(
    {
        "casefold",
        "copy",
        "count",
        "decode",
        "encode",
        "endswith",
        "format",
        "get",
        "hexdigest",
        "index",
        "isalnum",
        "isalpha",
        "isascii",
        "isdecimal",
        "isdigit",
        "islower",
        "isnumeric",
        "isspace",
        "istitle",
        "isupper",
        "items",
        "join",
        "keys",
        "lower",
        "lstrip",
        "partition",
        "removeprefix",
        "removesuffix",
        "replace",
        "rpartition",
        "rsplit",
        "rstrip",
        "split",
        "splitlines",
        "startswith",
        "strip",
        "swapcase",
        "title",
        "upper",
        "values",
        "zfill",
    }
)

UNSAFE_QUALIFIED_CALLS = {
    "builtins.open": UnsafeEffectFamily.MACHINE_STATE,
    "os.chdir": UnsafeEffectFamily.MACHINE_STATE,
    "os.chmod": UnsafeEffectFamily.MACHINE_STATE,
    "os.chown": UnsafeEffectFamily.MACHINE_STATE,
    "os.execv": UnsafeEffectFamily.PROCESS_CONTROL,
    "os.execve": UnsafeEffectFamily.PROCESS_CONTROL,
    "os.fork": UnsafeEffectFamily.PROCESS_CONTROL,
    "os.kill": UnsafeEffectFamily.PROCESS_CONTROL,
    "os.makedirs": UnsafeEffectFamily.MACHINE_STATE,
    "os.mkdir": UnsafeEffectFamily.MACHINE_STATE,
    "os.putenv": UnsafeEffectFamily.MUTABLE_GLOBAL_STATE,
    "os.remove": UnsafeEffectFamily.MACHINE_STATE,
    "os.rename": UnsafeEffectFamily.MACHINE_STATE,
    "os.replace": UnsafeEffectFamily.MACHINE_STATE,
    "os.rmdir": UnsafeEffectFamily.MACHINE_STATE,
    "os.system": UnsafeEffectFamily.PROCESS_CONTROL,
    "os.unlink": UnsafeEffectFamily.MACHINE_STATE,
    "os.unsetenv": UnsafeEffectFamily.MUTABLE_GLOBAL_STATE,
    "sys.exit": UnsafeEffectFamily.PROCESS_CONTROL,
}

DYNAMIC_CALLS = frozenset(
    {"__import__", "breakpoint", "compile", "delattr", "eval", "exec", "getattr", "setattr"}
)


def unsafe_import_family(module: str) -> UnsafeEffectFamily | None:
    """Return the first closed unsafe family that owns an imported module."""

    for rule in UNSAFE_EFFECT_RULES:
        if any(
            module == prefix or module.startswith(f"{prefix}.") for prefix in rule.module_prefixes
        ):
            return rule.family
    return None


def is_allowed_external_import(module: str) -> bool:
    """Whether a non-candidate module is positively known at import altitude."""

    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in ALLOWED_EXTERNAL_MODULE_PREFIXES
    )


def unsafe_family_reason(family: UnsafeEffectFamily) -> str:
    """Return the stable developer-facing explanation for one family."""

    return next(rule.reason for rule in UNSAFE_EFFECT_RULES if rule.family is family)


def is_safe_call(qualified: str, *, method_name: str | None = None) -> bool:
    """Whether a call is positively inside the deterministic in-memory model."""

    if qualified in SAFE_BUILTIN_CALLS:
        return True
    if qualified.startswith("self.assert") or qualified.startswith("cls.assert"):
        return True
    if any(
        qualified == prefix or qualified.startswith(prefix)
        for prefix in SAFE_QUALIFIED_CALL_PREFIXES
    ):
        return True
    return method_name in SAFE_VALUE_METHODS
