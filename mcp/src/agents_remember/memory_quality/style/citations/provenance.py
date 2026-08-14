"""Historical source and exact dependency-version provenance for citation claims.

Code and memory are separate Git repositories, so a code verification stamp cannot be used
to read a memory source. The external-memory ledger supplies that mapping, and each mapped
commit must be reachable from its repository's current history rather than merely present in
the object database. Dependency source is not in either repository; its reproducible identity
is a concrete PEP 440 equality on the Python surface or the exact npm package-lock version on
the JavaScript surface. Names never pool across those ecosystems. A permissive range is useful
installation policy but is not historical evidence of what was reviewed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from agents_remember.kernel.git_command import GIT_METADATA_TIMEOUT_SECONDS, run_git
from agents_remember.kernel.memory_ledger import LedgerError, find_mapping, load_ledger

REQUIREMENTS_PATH = "mcp/requirements.txt"
PACKAGE_LOCK_PATH = "dashboard/package-lock.json"
REQUIREMENT_NAME = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)")
PYTHON_ECOSYSTEM = "python"
NPM_ECOSYSTEM = "npm"
PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
NPM_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"})


@dataclass(frozen=True)
class Read:
    text: str | None
    error: str | None = None


@dataclass(frozen=True)
class LockedVersion:
    package: str
    version: str
    surface: str


@dataclass
class GitHistory:
    root: Path
    name: str
    _commits: dict[str, Read] = field(default_factory=dict)
    _files: dict[tuple[str, str], Read] = field(default_factory=dict)

    def commit(self, stamp: str) -> Read:
        if stamp not in self._commits:
            resolved = run_git(
                self.root,
                ["rev-parse", "--verify", f"{stamp}^{{commit}}"],
                timeout=GIT_METADATA_TIMEOUT_SECONDS,
            )
            if resolved.returncode != 0:
                self._commits[stamp] = Read(
                    None,
                    _git_error(
                        resolved.stderr,
                        f"{self.name} history stamp {stamp} does not name a commit",
                    ),
                )
                return self._commits[stamp]
            commit = resolved.stdout.strip()
            reachable = run_git(
                self.root,
                ["merge-base", "--is-ancestor", commit, "HEAD"],
                timeout=GIT_METADATA_TIMEOUT_SECONDS,
            )
            if reachable.returncode != 0:
                self._commits[stamp] = Read(
                    None,
                    _git_error(
                        reachable.stderr,
                        f"{self.name} history stamp {stamp} resolves to {commit}, but that commit "
                        f"is not reachable from the current {self.name} history",
                    ),
                )
                return self._commits[stamp]
            self._commits[stamp] = Read(commit)
        return self._commits[stamp]

    def file(self, commit: str, path: str) -> Read:
        key = (commit, path)
        if key not in self._files:
            completed = run_git(
                self.root,
                ["show", f"{commit}:{path}"],
                timeout=GIT_METADATA_TIMEOUT_SECONDS,
            )
            self._files[key] = (
                Read(completed.stdout)
                if completed.returncode == 0
                else Read(None, _git_error(completed.stderr, f"{path} did not exist at {commit}"))
            )
        return self._files[key]


@dataclass
class Histories:
    code_root: Path
    memory_root: Path
    code: GitHistory = field(init=False)
    memory: GitHistory = field(init=False)

    def __post_init__(self) -> None:
        self.code = GitHistory(self.code_root, "code")
        self.memory = GitHistory(self.memory_root, "memory")

    def memory_commit(self, code_commit: str) -> Read:
        try:
            ledger = load_ledger(self.memory_root / "memory.md")
        except (LedgerError, OSError, UnicodeError) as error:
            return Read(None, f"memory ledger is unavailable or invalid: {error}")
        mapped = find_mapping(ledger, code_commit)
        if mapped is None:
            return Read(None, f"memory.md has no ledger mapping for code commit {code_commit}")
        return self.memory.commit(mapped.memory_commit)

    def dependency_versions(
        self, package: str, ecosystem: str, code_commit: str
    ) -> tuple[LockedVersion | None, LockedVersion | None, str | None]:
        historical = self._locked_version(package, ecosystem, code_commit)
        current = self._locked_version(package, ecosystem, None)
        errors = [read.error for read in (historical, current) if read.error]
        return historical.version, current.version, "; ".join(errors) or None

    def _locked_version(self, package: str, ecosystem: str, commit: str | None) -> VersionRead:
        if ecosystem == PYTHON_ECOSYSTEM:
            requirement = self._manifest(REQUIREMENTS_PATH, commit)
            candidate, permissive, parse_error = requirement_candidate_for(package, requirement)
            surface = REQUIREMENTS_PATH
        elif ecosystem == NPM_ECOSYSTEM:
            package_lock = self._manifest(PACKAGE_LOCK_PATH, commit)
            candidate, parse_error = package_candidate_for(package, package_lock)
            permissive = False
            surface = PACKAGE_LOCK_PATH
        else:
            return VersionRead(None, f"{package} has unsupported dependency ecosystem {ecosystem}")
        if candidate is not None:
            return VersionRead(candidate)
        where = "working tree" if commit is None else f"commit {commit}"
        if permissive:
            return VersionRead(
                None,
                f"{package} has only a permissive requirement in {where}; it has no resolved version",
            )
        if parse_error is not None:
            return VersionRead(None, parse_error)
        return VersionRead(
            None,
            f"{package} has no resolved {ecosystem} version in {surface} at {where}",
        )

    def _manifest(self, path: str, commit: str | None) -> Read:
        if commit is not None:
            return self.code.file(commit, path)
        target = self.code_root / path
        if not target.is_file():
            return Read(None, f"{path} did not exist in the working tree")
        try:
            return Read(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as error:
            return Read(None, f"could not read {path}: {error}")


@dataclass(frozen=True)
class VersionRead:
    version: LockedVersion | None
    error: str | None = None


def requirement_candidate_for(
    package: str, read: Read
) -> tuple[LockedVersion | None, bool, str | None]:
    if read.text is None:
        return None, False, manifest_error(read)
    versions, ranged = requirement_versions(read.text)
    normal = normalised_package(package)
    candidate = (
        LockedVersion(package, versions[normal], REQUIREMENTS_PATH) if normal in versions else None
    )
    return candidate, normal in ranged, None


def package_candidate_for(package: str, read: Read) -> tuple[LockedVersion | None, str | None]:
    if read.text is None:
        return None, manifest_error(read)
    try:
        versions = package_lock_versions(read.text)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        return None, f"{PACKAGE_LOCK_PATH} is invalid: {error}"
    candidate = (
        LockedVersion(package, versions[package], PACKAGE_LOCK_PATH)
        if package in versions
        else None
    )
    return candidate, None


def manifest_error(read: Read) -> str | None:
    if read.error is None:
        return None
    absent_markers = ("did not exist", "does not exist", "exists on disk, but not in")
    return None if any(marker in read.error for marker in absent_markers) else read.error


def requirement_versions(text: str) -> tuple[dict[str, str], set[str]]:
    exact: dict[str, str] = {}
    permissive: set[str] = set()
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", maxsplit=1)[0].strip()
        if not line or line.startswith(("-", "http://", "https://")):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            named = REQUIREMENT_NAME.match(line)
            if named is not None:
                name = normalised_package(named.group("name"))
                exact.pop(name, None)
                permissive.add(name)
                seen.add(name)
            continue
        name = normalised_package(requirement.name)
        if name in seen:
            exact.pop(name, None)
            permissive.add(name)
            continue
        seen.add(name)
        specifiers = list(requirement.specifier)
        if (
            requirement.url is not None
            or requirement.marker is not None
            or requirement.extras
            or len(specifiers) != 1
            or specifiers[0].operator != "=="
            or "*" in specifiers[0].version
        ):
            permissive.add(name)
            continue
        try:
            version = Version(specifiers[0].version)
        except InvalidVersion:
            permissive.add(name)
            continue
        exact[name] = str(version)
    return exact, permissive


def package_lock_versions(text: str) -> dict[str, str]:
    loaded = json.loads(text)
    packages = loaded.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("packages must be an object")
    found: dict[str, str] = {}
    for key, value in packages.items():
        if not isinstance(key, str) or not key.startswith("node_modules/"):
            continue
        package = key.removeprefix("node_modules/")
        if "/node_modules/" in package or not isinstance(value, dict):
            continue
        version = value.get("version")
        if isinstance(version, str) and version:
            found[package] = version
    return found


def package_from_path(path: str) -> str:
    parts = path.split("/")
    if path.startswith("@") and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def ecosystem_from_path(path: str) -> str | None:
    """The one resolved-version namespace capable of proving ``path``'s identity."""
    pure = PurePosixPath(path)
    suffix = pure.suffix.lower()
    if suffix in PYTHON_SUFFIXES:
        return PYTHON_ECOSYSTEM
    if suffix in NPM_SUFFIXES or pure.name == "package.json":
        return NPM_ECOSYSTEM
    return None


def normalised_package(package: str) -> str:
    return re.sub(r"[-_.]+", "-", package).lower()


def _git_error(stderr: str, fallback: str) -> str:
    first = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
    return first or fallback
