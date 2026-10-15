#!/usr/bin/env python3
"""Sync the root skill tree into package and harness skill copies."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SKILLS = REPO_ROOT / "skills"


def _skill_projection(root: Path = REPO_ROOT):
    """Read this checkout's declaration through the dependency-free parser owner."""
    parser_path = REPO_ROOT / "mcp/src/agents_remember/kernel/generated_copies.py"
    spec = importlib.util.spec_from_file_location("_ar_copy_declaration", parser_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load copy declaration parser: {parser_path}")
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    projections = parser.parse_generated_copies((root / parser.DECLARATION_PATH).read_bytes())
    matches = [one for one in projections if one.source == "skills"]
    if len(matches) != 1:
        raise ValueError("generated-copies.json must declare exactly one skills source")
    return matches[0]


IGNORED_NAMES = frozenset(_skill_projection().ignore_names)
IGNORED_SUFFIXES = _skill_projection().ignore_suffixes


@dataclass(frozen=True)
class SkillTarget:
    label: str
    path: Path


@dataclass(frozen=True)
class SkillDiff:
    missing: tuple[Path, ...]
    extra: tuple[Path, ...]
    changed: tuple[Path, ...]

    @property
    def in_sync(self) -> bool:
        return not self.missing and not self.extra and not self.changed


def skill_targets(root: Path = REPO_ROOT) -> tuple[SkillTarget, ...]:
    return tuple(
        SkillTarget(target.label, root / target.path) for target in _skill_projection(root).targets
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy the canonical root skills/ tree into the MCP package data "
            "and every harness starter package."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify that every target matches skills/; do not write files.",
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="Print sync targets and exit.",
    )
    return parser.parse_args()


def repo_relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def ignored(rel_path: Path) -> bool:
    return any(part in IGNORED_NAMES or part.endswith(IGNORED_SUFFIXES) for part in rel_path.parts)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    hasher.update(path.read_bytes())
    return hasher.hexdigest()


def extended_length(path: Path) -> Path:
    """Windows extended-length (\\\\?\\) form so file operations work past the
    legacy 260-char MAX_PATH even when LongPathsEnabled is off; identity on
    other platforms."""
    text = str(path)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return Path(f"\\\\?\\{os.path.abspath(text)}")
    return path


def file_digests(root: Path) -> dict[Path, str]:
    walk_root = extended_length(root)
    if not walk_root.exists():
        return {}

    files: dict[Path, str] = {}
    for path in walk_root.rglob("*"):
        rel_path = path.relative_to(walk_root)
        if ignored(rel_path) or not path.is_file():
            continue
        files[rel_path] = digest(path)
    return files


def diff_target(target: SkillTarget) -> SkillDiff:
    source = file_digests(CANONICAL_SKILLS)
    current = file_digests(target.path)

    source_paths = set(source)
    current_paths = set(current)
    shared_paths = source_paths & current_paths

    return SkillDiff(
        missing=tuple(sorted(source_paths - current_paths)),
        extra=tuple(sorted(current_paths - source_paths)),
        changed=tuple(sorted(path for path in shared_paths if source[path] != current[path])),
    )


def copy_ignore(_directory: str, names: list[str]) -> list[str]:
    return [name for name in names if name in IGNORED_NAMES or name.endswith(IGNORED_SUFFIXES)]


def sync_target(target: SkillTarget) -> None:
    if target.path.resolve() == CANONICAL_SKILLS.resolve():
        raise RuntimeError("refusing to sync canonical skills/ onto itself")
    replace_tree(CANONICAL_SKILLS, target.path)


def replace_tree(source: Path, target: Path) -> None:
    """Build a complete staged copy, then swap it in with two separate renames.

    The previous delete-then-copy left the target gutted when the copy (or the
    delete itself) failed mid-way — a real incident on a Windows host without
    long-path support. Staging first closes that window: a copy that fails before
    the first rename leaves the live target exactly as it was, and a re-run prunes
    leftovers from an earlier crash and rebuilds the staging copy from source.

    The window between the two renames remains. The live target is renamed to
    ``<target>.ar-sync-old`` before ``<target>.ar-sync-new`` is renamed onto the
    live path, and those renames are separate operations: a failure after the
    first leaves the live path absent, with the previous copy and the complete
    replacement both still on disk. A re-run deletes both leftovers before it
    copies again, so a second failure can consume the last copy of the old bytes.
    Nothing here is atomic and nothing rolls back; only a later successful run
    reconstructs the target from source."""
    staging = extended_length(target.parent / f"{target.name}.ar-sync-new")
    retired = extended_length(target.parent / f"{target.name}.ar-sync-old")
    target_ext = extended_length(target)
    for leftover in (staging, retired):
        if leftover.exists():
            shutil.rmtree(leftover)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(extended_length(source), staging, ignore=copy_ignore)
    if target_ext.exists():
        os.rename(target_ext, retired)
    os.rename(staging, target_ext)
    if retired.exists():
        shutil.rmtree(retired)


def print_diff(target: SkillTarget, diff: SkillDiff) -> None:
    print(f"[sync-skills] out of sync: {target.label} ({repo_relative(target.path)})")
    for label, paths in (
        ("missing", diff.missing),
        ("extra", diff.extra),
        ("changed", diff.changed),
    ):
        if not paths:
            continue
        print(f"  {label}:")
        for path in paths:
            print(f"    {path.as_posix()}")


def projected_profile_bytes(root: Path = REPO_ROOT) -> bytes:
    """Generate only the skill row through the certification profile's owner."""
    sys.path.insert(0, str(REPO_ROOT / "mcp/src"))
    try:
        owner = importlib.import_module(
            "agents_remember.certification.repository_profiles.canonical"
        )
    finally:
        sys.path.pop(0)
    path = root / "mcp/certification-profile-v1.json"
    document = json.loads(path.read_bytes())
    profile = owner.RepositoryCertificationProfile.model_validate(document)
    projected = owner.project_generated_skills(
        profile, (root / "generated-copies.json").read_bytes()
    )
    row = next(one for one in projected.generatedInputs if one.inputId == "generated-skills")
    stored = next(one for one in document["generatedInputs"] if one["inputId"] == row.inputId)
    stored.update(generatedScopes=list(row.generatedScopes), sourceScopes=list(row.sourceScopes))
    document["profileDigest"] = projected.profileDigest
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def check_targets() -> int:
    failed = False
    for target in skill_targets():
        diff = diff_target(target)
        if diff.in_sync:
            print(f"[sync-skills] ok: {target.label} ({repo_relative(target.path)})")
            continue
        print_diff(target, diff)
        failed = True

    profile = REPO_ROOT / "mcp/certification-profile-v1.json"
    if profile.read_bytes() != projected_profile_bytes():
        print("[sync-skills] out of sync: generated-skills profile row", file=sys.stderr)
        failed = True
    if failed:
        print("[sync-skills] run: python3 scripts/sync-skills.py", file=sys.stderr)
        return 1
    return 0


def sync_targets() -> int:
    if not CANONICAL_SKILLS.is_dir():
        print("[sync-skills] missing canonical skills/ directory", file=sys.stderr)
        return 1

    targets = skill_targets()
    profile_bytes = projected_profile_bytes()
    for target in targets:
        sync_target(target)
        print(f"[sync-skills] synced {repo_relative(target.path)}")
    (REPO_ROOT / "mcp/certification-profile-v1.json").write_bytes(profile_bytes)
    return check_targets()


def list_targets() -> int:
    print(f"canonical: {repo_relative(CANONICAL_SKILLS)}")
    for target in skill_targets():
        print(f"{target.label}: {repo_relative(target.path)}")
    return 0


def main() -> int:
    args = parse_args()
    if args.list_targets:
        return list_targets()
    if args.check:
        return check_targets()
    return sync_targets()


if __name__ == "__main__":
    raise SystemExit(main())
