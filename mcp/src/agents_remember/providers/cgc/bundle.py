"""CodeGraphContext seed bundle path rewriting helpers."""

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any


def path_replacements(source_root: Path, target_root: Path) -> list[tuple[str, str]]:
    source_resolved = source_root.resolve()
    target_resolved = target_root.resolve()
    pairs = [
        (str(source_resolved), str(target_resolved)),
        (source_resolved.as_posix(), target_resolved.as_posix()),
        (str(source_resolved).replace("/", "\\"), str(target_resolved).replace("/", "\\")),
        (
            source_resolved.as_posix().replace("/", "\\"),
            target_resolved.as_posix().replace("/", "\\"),
        ),
    ]
    unique: dict[str, str] = {}
    for source, target in pairs:
        if source and source not in unique:
            unique[source] = target
    return sorted(unique.items(), key=lambda item: len(item[0]), reverse=True)


def rewrite_string(value: str, replacements: list[tuple[str, str]]) -> tuple[str, int]:
    rewritten = value
    count = 0
    for source, target in replacements:
        occurrences = rewritten.count(source)
        if occurrences:
            rewritten = rewritten.replace(source, target)
            count += occurrences
    return rewritten, count


def rewrite_json_value(value: Any, replacements: list[tuple[str, str]]) -> tuple[Any, int]:
    if isinstance(value, str):
        return rewrite_string(value, replacements)
    if isinstance(value, list):
        return _rewrite_json_list(value, replacements)
    if isinstance(value, dict):
        return _rewrite_json_dict(value, replacements)
    return value, 0


def _rewrite_json_list(
    value: list[Any],
    replacements: list[tuple[str, str]],
) -> tuple[list[Any], int]:
    total = 0
    rewritten_items = []
    for item in value:
        rewritten, count = rewrite_json_value(item, replacements)
        rewritten_items.append(rewritten)
        total += count
    return rewritten_items, total


def _rewrite_json_dict(
    value: dict[str, Any],
    replacements: list[tuple[str, str]],
) -> tuple[dict[str, Any], int]:
    total = 0
    rewritten_dict: dict[str, Any] = {}
    for key, item in value.items():
        rewritten_key, key_count = rewrite_string(key, replacements)
        rewritten_item, item_count = rewrite_json_value(item, replacements)
        rewritten_dict[rewritten_key] = rewritten_item
        total += key_count + item_count
    return rewritten_dict, total


def rewrite_cgc_bundle_paths(
    source_bundle: Path,
    target_bundle: Path,
    source_root: Path,
    target_root: Path,
) -> dict[str, Any]:
    replacements = path_replacements(source_root, target_root)
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        _extract_safe_zip(source_bundle, temp_root)
        rewrite = _rewrite_extracted_bundle(temp_root, replacements)
        _write_zip_from_tree(target_bundle, temp_root)

    return {
        "sourceBundle": source_bundle.as_posix(),
        "targetBundle": target_bundle.as_posix(),
        "sourceRoot": source_root.resolve().as_posix(),
        "targetRoot": target_root.resolve().as_posix(),
        "replacementCount": rewrite["replacementCount"],
        "filesRewritten": rewrite["filesRewritten"],
    }


def _extract_safe_zip(source_bundle: Path, temp_root: Path) -> None:
    with zipfile.ZipFile(source_bundle, "r") as zip_ref:
        for entry in zip_ref.namelist():
            resolved = (temp_root / entry).resolve()
            if not str(resolved).startswith(str(temp_root.resolve())):
                raise RuntimeError(f"unsafe CGC bundle entry escapes extraction root: {entry}")
        zip_ref.extractall(temp_root)


def _rewrite_extracted_bundle(
    temp_root: Path,
    replacements: list[tuple[str, str]],
) -> dict[str, Any]:
    total_replacements = 0
    files_rewritten: list[str] = []
    for path in sorted(temp_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(temp_root).as_posix()
        count = _rewrite_bundle_file(path, replacements)
        if count:
            files_rewritten.append(relative)
            total_replacements += count
    return {"replacementCount": total_replacements, "filesRewritten": files_rewritten}


def _rewrite_bundle_file(path: Path, replacements: list[tuple[str, str]]) -> int:
    if path.suffix == ".json":
        return _rewrite_json_file(path, replacements)
    if path.suffix == ".jsonl":
        return _rewrite_jsonl_file(path, replacements)
    if path.suffix.lower() in {".md", ".txt"}:
        return _rewrite_text_file(path, replacements)
    return 0


def _rewrite_json_file(path: Path, replacements: list[tuple[str, str]]) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    rewritten, count = rewrite_json_value(data, replacements)
    if count:
        path.write_text(json.dumps(rewritten, indent=2) + "\n", encoding="utf-8")
    return count


def _rewrite_jsonl_file(path: Path, replacements: list[tuple[str, str]]) -> int:
    output_lines: list[str] = []
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        rewritten_line, line_count = _rewrite_jsonl_line(line, replacements)
        output_lines.append(rewritten_line)
        count += line_count
    if count:
        path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    return count


def _rewrite_jsonl_line(
    line: str,
    replacements: list[tuple[str, str]],
) -> tuple[str, int]:
    if not line.strip():
        return line, 0
    data = json.loads(line)
    rewritten, count = rewrite_json_value(data, replacements)
    return json.dumps(rewritten, separators=(",", ":")), count


def _rewrite_text_file(path: Path, replacements: list[tuple[str, str]]) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    rewritten, count = rewrite_string(text, replacements)
    if count:
        path.write_text(rewritten, encoding="utf-8")
    return count


def _write_zip_from_tree(target_bundle: Path, temp_root: Path) -> None:
    target_bundle.parent.mkdir(parents=True, exist_ok=True)
    if target_bundle.exists():
        target_bundle.unlink()
    with zipfile.ZipFile(target_bundle, "w", compression=zipfile.ZIP_DEFLATED) as zip_ref:
        for path in sorted(temp_root.rglob("*")):
            if path.is_file():
                zip_ref.write(path, path.relative_to(temp_root).as_posix())
