"""Content-addressed evidence files shared by resets and reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError
from agents_remember.kernel.sidecar_pairing import confine_non_symlink_rel, confine_rel


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AuthorityError(f"{path}: expected a JSON object; repair the named evidence file")
    return value


def write_object(path: Path, value: object) -> None:
    """Write caller-owned evidence, never a task document or a runtime record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class EvidenceFile:
    path: str
    sha256: str

    @classmethod
    def capture(cls, root: Path, relative: str) -> EvidenceFile:
        name = confine_non_symlink_rel(root, relative)
        return cls(name, sha256_file(root / name))

    def verify(self, root: Path) -> Path:
        name = confine_non_symlink_rel(root, self.path)
        path = root / name
        if sha256_file(path) != self.sha256:
            raise AuthorityError(
                f"evidence changed: {path}; recover its declared bytes before running"
            )
        return path

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def manifest_files(root: Path) -> tuple[EvidenceFile, ...]:
    """Inventory every file once; private cases are finite, explicitly owned bundles."""
    return tuple(
        EvidenceFile.capture(root, path.relative_to(root).as_posix())
        for path in sorted(root.rglob("*"))
        if path.is_file() or path.is_symlink()
    )


def child_path(root: Path, relative: str) -> Path:
    """Use the product's path authority for outputs as well as existing inputs."""
    return root / confine_rel(root, relative)
