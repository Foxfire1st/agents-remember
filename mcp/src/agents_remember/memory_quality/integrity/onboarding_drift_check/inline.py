"""Inline onboarding block extraction and classification for drift detection."""

from __future__ import annotations

import hashlib
from pathlib import Path

from agents_remember.kernel.coordination_context_resolver import (
    StorageSettings,
    clean_scalar,
    normalize_rel_path,
    resolve_storage_for_source,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    list_repo_sources,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    COMMON_BLOCK_DELIMITERS,
    INLINE_END_MARKER,
    INLINE_START_MARKER,
    DriftRow,
    InlineBlock,
)


def line_bounds(text: str, index: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    return start, end


def expand_inline_bounds(source_text: str, start_index: int, end_index: int) -> tuple[int, int]:
    start_line_start, _ = line_bounds(source_text, start_index)
    previous_line_end = start_line_start - 1
    previous_line_start = source_text.rfind("\n", 0, max(previous_line_end, 0)) + 1
    previous_line = (
        source_text[previous_line_start:previous_line_end].strip() if start_line_start > 0 else ""
    )
    block_start = start_line_start
    expected_end = COMMON_BLOCK_DELIMITERS.get(previous_line)
    if expected_end:
        block_start = previous_line_start

    _, end_line_end = line_bounds(source_text, end_index)
    block_end = end_line_end
    if expected_end and end_line_end < len(source_text):
        next_line_start = end_line_end + 1
        next_line_end = source_text.find("\n", next_line_start)
        if next_line_end == -1:
            next_line_end = len(source_text)
        next_line = source_text[next_line_start:next_line_end].strip()
        if next_line == expected_end:
            block_end = next_line_end
    if block_end < len(source_text) and source_text[block_end : block_end + 1] == "\n":
        block_end += 1
    return block_start, block_end


def extract_inline_onboarding_block(source_text: str) -> InlineBlock | None:
    start_index = source_text.find(INLINE_START_MARKER)
    end_index = source_text.find(INLINE_END_MARKER)
    if start_index == -1 or end_index == -1 or end_index < start_index:
        return None

    block_start, block_end = expand_inline_bounds(source_text, start_index, end_index)
    raw_text = source_text[block_start:block_end]
    metadata: dict[str, str] = {}
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in {INLINE_START_MARKER, INLINE_END_MARKER}:
            continue
        if stripped.startswith(
            ("/*", "*/", "<!--", "-->", '"""', "'''", "=begin", "=end", "{-", "-}", "(*", "*)")
        ):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = clean_scalar(value)
    return InlineBlock(raw_text=raw_text, metadata=metadata)


def compute_inline_source_digest(source_text: str, block: InlineBlock) -> str:
    source_without_block = source_text.replace(block.raw_text, "", 1)
    normalized_source = source_without_block.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()


def classify_inline_source(source_file: str, repo_root: Path) -> DriftRow:
    source_path = repo_root / normalize_rel_path(source_file)
    if not source_path.exists():
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date="",
            classification="orphaned",
            trust="low",
            affected_sections="all; source missing",
            note="Inline onboarding source file no longer exists.",
        )

    try:
        source_text = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date="",
            classification="unsupported",
            trust="low",
            affected_sections="verification; encoding",
            note="Source file is not UTF-8 text, so inline onboarding cannot be parsed safely.",
        )

    block = extract_inline_onboarding_block(source_text)
    if block is None:
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date="",
            classification="missing",
            trust="low",
            affected_sections="all; onboarding missing",
            note="Inline onboarding block is missing.",
        )

    source_digest = block.metadata.get("sourceDigest", "")
    verified_at = block.metadata.get("verifiedAt", "")
    if source_digest.startswith("sha256:"):
        source_digest = source_digest.split(":", 1)[1]
    if not source_digest:
        return DriftRow(
            onboarding_file=f"inline:{normalize_rel_path(source_file)}",
            source_file=normalize_rel_path(source_file),
            repository=repo_root.name,
            storage_mode="inline",
            last_verified_hash="",
            last_verified_date=verified_at,
            classification="missing verification",
            trust="medium",
            affected_sections="metadata; verification",
            note="Inline onboarding block is missing sourceDigest metadata.",
        )

    computed_digest = compute_inline_source_digest(source_text, block)
    classification = "up to date" if computed_digest == source_digest else "drifted"
    return DriftRow(
        onboarding_file=f"inline:{normalize_rel_path(source_file)}",
        source_file=normalize_rel_path(source_file),
        repository=repo_root.name,
        storage_mode="inline",
        last_verified_hash=source_digest,
        last_verified_date=verified_at,
        classification=classification,
        trust="high" if classification == "up to date" else "medium",
        affected_sections="none"
        if classification == "up to date"
        else "logic; invariants; metadata",
        note=(
            "Inline source digest matches the current source body."
            if classification == "up to date"
            else "Source body changed since the recorded inline sourceDigest was computed."
        ),
    )


def discover_inline_onboarding_sources(repo_root: Path, settings: StorageSettings) -> list[str]:
    inline_sources: list[str] = []
    for source_file in list_repo_sources(repo_root):
        if resolve_storage_for_source(source_file, settings, repo_root.name) != "inline":
            continue

        source_path = repo_root / source_file
        if not source_path.exists():
            continue

        try:
            source_text = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        if extract_inline_onboarding_block(source_text) is not None:
            inline_sources.append(source_file)

    return sorted(inline_sources)
