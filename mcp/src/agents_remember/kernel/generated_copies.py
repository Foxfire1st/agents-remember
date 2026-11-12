"""Repository-owned byte-copy declarations, shared by generators and tree readers.

The declaration is data in the repository being judged, never an installed product's
inventory. Parsing is dependency-free so source-checkout generators can use this owner
without installing the product. A caller owns acquisition and domain error reporting.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, NamedTuple

DECLARATION_PATH = "generated-copies.json"


class CopyTarget(NamedTuple):
    label: str
    path: str


class CopyProjection(NamedTuple):
    source: str
    targets: tuple[CopyTarget, ...]
    ignore_names: tuple[str, ...]
    ignore_suffixes: tuple[str, ...]

    def ignores(self, relative: str) -> bool:
        return any(
            part in self.ignore_names or part.endswith(self.ignore_suffixes)
            for part in PurePosixPath(relative).parts
        )


def _path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("copy paths must be nonempty repository-relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value or value == ".":
        raise ValueError(f"copy path {value!r} is not a literal repository-relative path")
    return value


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(one, str) or not one for one in value):
        raise ValueError(f"{name} must be a list of nonempty strings")
    return tuple(value)


def _projection(value: Any) -> CopyProjection:
    if not isinstance(value, dict) or set(value) != {
        "source",
        "targets",
        "ignoreNames",
        "ignoreSuffixes",
    }:
        raise ValueError("each copy declares source, targets, ignoreNames and ignoreSuffixes")
    source = _path(value["source"])
    targets = value["targets"]
    if not isinstance(targets, list) or not targets:
        raise ValueError(f"{source}: targets must be a nonempty list")
    parsed = []
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"label", "path"}:
            raise ValueError(f"{source}: each target declares label and path")
        label = target["label"]
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"{source}: each target needs a nonblank label")
        parsed.append(CopyTarget(label, _path(target["path"])))
    return CopyProjection(
        source,
        tuple(parsed),
        _strings(value["ignoreNames"], "ignoreNames"),
        _strings(value["ignoreSuffixes"], "ignoreSuffixes"),
    )


def parse_generated_copies(data: bytes) -> tuple[CopyProjection, ...]:
    """Validate a present declaration; absence is decided by the repository reader."""
    document = json.loads(data.decode("utf-8"))
    if not isinstance(document, dict) or set(document) != {"version", "copies"}:
        raise ValueError("generated-copies.json must declare version and copies")
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("generated-copies.json version must be 1")
    if not isinstance(document["copies"], list):
        raise ValueError("generated-copies.json copies must be a list")
    projections = tuple(_projection(one) for one in document["copies"])
    sources = [one.source for one in projections]
    targets = [target.path for one in projections for target in one.targets]
    if len(set(sources)) != len(sources) or len(set(targets)) != len(targets):
        raise ValueError("generated copy sources and targets must be unique")
    for target in targets:
        if any(
            PurePosixPath(target).is_relative_to(source)
            or PurePosixPath(source).is_relative_to(target)
            for source in sources
        ):
            raise ValueError(f"generated target {target!r} overlaps a canonical source")
        if any(
            other != target and PurePosixPath(target).is_relative_to(other) for other in targets
        ):
            raise ValueError(f"generated target {target!r} overlaps another target")
    return projections
