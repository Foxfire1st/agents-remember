"""Copy packaged Agents Remember skills into harness skill roots."""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.install.assets import long_path, packaged_source_root

IGNORED_COPY_PATTERNS = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


@dataclass
class SkillsInstallSummary:
    installed: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "archived": self.archived,
            "removed": self.removed,
            "planned": self.planned,
        }


@dataclass(frozen=True)
class SkillsInstallRequest:
    """What one skills install is asked to do.

    Where the packaged skills land, whether it writes at all, and how it treats
    a skill directory that is already there — the collision rule is what every
    per-skill copy has to consult, so it rides with the destination.
    """

    install_root: Path
    dry_run: bool = False
    overwrite: bool = False
    archive_existing: bool = False

    def validated(self) -> SkillsInstallRequest:
        if not self.install_root.is_absolute():
            raise ValueError("install_root must be an absolute path")
        if self.overwrite and self.archive_existing:
            raise ValueError("overwrite and archive_existing are mutually exclusive")
        return self


def install_skills(
    *,
    install_root: Path,
    dry_run: bool = False,
    overwrite: bool = False,
    archive_existing: bool = False,
) -> dict[str, Any]:
    request = SkillsInstallRequest(
        install_root=install_root,
        dry_run=dry_run,
        overwrite=overwrite,
        archive_existing=archive_existing,
    ).validated()

    with packaged_source_root() as source_root:
        skills_root = source_root / "runtime" / "skills"
        if not skills_root.is_dir():
            raise FileNotFoundError(f"packaged skills root does not exist: {skills_root}")
        source_root_text = skills_root.as_posix()

        summary = SkillsInstallSummary()
        if not dry_run:
            install_root.mkdir(parents=True, exist_ok=True)

        # The packaged skills tree is flat: every skill sits directly under skills/.
        # Copy each skill into the harness root under its frontmatter name so every
        # harness discovers them reliably; there is no nesting to flatten.
        for skill_file in sorted(skills_root.rglob("SKILL.md")):
            skill_dir = skill_file.parent
            name = _read_skill_name(skill_file)
            if not name:
                raise ValueError(f"missing frontmatter name in {skill_file}")
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
                raise ValueError(f"unsupported skill name in {skill_file}: {name}")
            _copy_skill_tree(
                request,
                summary,
                source=skill_dir,
                destination=install_root / name,
            )

    return {
        "ok": True,
        "operation": "skills_install",
        "dryRun": dry_run,
        "sourceRoot": source_root_text,
        "installRoot": install_root.as_posix(),
        **summary.to_dict(),
    }


def _copy_skill_tree(
    request: SkillsInstallRequest,
    summary: SkillsInstallSummary,
    *,
    source: Path,
    destination: Path,
) -> None:
    dry_run = request.dry_run
    if _exists_or_link(destination):
        if request.archive_existing:
            archived = _archive_path(destination, request.install_root, dry_run)
            summary.archived.append(archived.as_posix())
        elif request.overwrite:
            if not dry_run:
                if destination.is_dir() and not _is_link(destination):
                    shutil.rmtree(destination)
                else:
                    _unlink_path(destination)
            summary.removed.append(destination.as_posix())
        else:
            raise FileExistsError(
                f"skill install target already exists; set overwrite or archive_existing: {destination}"
            )

    if dry_run:
        summary.planned.append(destination.as_posix())
        return

    shutil.copytree(long_path(source), long_path(destination), ignore=IGNORED_COPY_PATTERNS)
    summary.installed.append(destination.as_posix())


def _exists_or_link(path: Path) -> bool:
    return path.exists() or path.is_symlink() or os.path.lexists(path)


def _is_link(path: Path) -> bool:
    if path.is_symlink() or os.path.islink(path):
        return True
    if sys.platform != "win32":
        return False
    try:
        attrs = path.lstat().st_file_attributes
    except FileNotFoundError:
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _unlink_path(path: Path) -> None:
    try:
        path.unlink()
    except IsADirectoryError:
        path.rmdir()


def _archive_path(path: Path, install_root: Path, dry_run: bool) -> Path:
    archive_root = install_root / ".archived-local-copies" / time.strftime("%Y%m%d-%H%M%S")
    archived = archive_root / path.name
    if not dry_run:
        archive_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(archived))
    return archived


def _read_skill_name(skill_file: Path) -> str:
    in_frontmatter = False
    for raw_line in skill_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line == "---" and not in_frontmatter:
            in_frontmatter = True
            continue
        if line == "---" and in_frontmatter:
            break
        if in_frontmatter and line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return ""
