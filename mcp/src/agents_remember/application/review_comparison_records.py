"""Read a comparison's native record inputs with exact consumed-byte observations."""

from pathlib import Path
from typing import Final

from pydantic import ValidationError

from agents_remember.kernel.read_cancellation import raise_if_read_cancelled
from agents_remember.kernel.recorded_reads import (
    ABSENT,
    bytes_identity,
    observed_json_files,
    record_read,
)
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.tasks.task_paths import slugify

REVIEW_COMPARISONS_DIRECTORY: Final = "review-comparisons"


def comparison_directory(task_root: Path, leaf_id: str) -> Path:
    """Where one leaf's comparison records live, beside the task's other durable reports."""

    return durable_reports_root(task_root) / REVIEW_COMPARISONS_DIRECTORY / slugify(leaf_id)


def record_bytes(path: Path) -> bytes:
    """Keep the bytes actually parsed, including failed candidates in record selection."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        record_read(path, ABSENT)
        raise
    except OSError as error:
        record_read(path, f"unreadable ({type(error).__name__})")
        raise
    record_read(path, bytes_identity(raw))
    return raw


def records_at(paths: tuple[Path, ...]) -> tuple[ReviewTreeComparisonRecord, ...]:
    records = []
    for path in paths:
        raise_if_read_cancelled()  # a long history: a read that left stops between two records
        try:
            records.append(ReviewTreeComparisonRecord.model_validate_json(record_bytes(path)))
        except (OSError, ValidationError):
            continue
    return tuple(sorted(records, key=lambda record: record.number))


def comparison_records(task_root: Path, leaf_id: str) -> tuple[ReviewTreeComparisonRecord, ...]:
    """Read the exact record listing and bytes, including an empty record directory."""
    return records_at(observed_json_files(comparison_directory(task_root, leaf_id)))


# A record is written once, as ``<number>.json`` with the number one above the highest, so its file
# name orders the history: a lookup reads the listing's names and only the records it selects, and
# names nothing else -- the answers kept on it stay flat in the history's length (D570).


def _named_record(path: Path, number: int) -> ReviewTreeComparisonRecord | None:
    """The record ``path`` holds when it is readable and carries its own file name's number."""

    try:
        record = ReviewTreeComparisonRecord.model_validate_json(record_bytes(path))
    except (OSError, ValidationError):
        return None
    return record if record.number == number else None


def latest_record(task_root: Path, leaf_id: str) -> ReviewTreeComparisonRecord | None:
    """The leaf's highest-numbered readable record, or ``None`` when it recorded none.

    The listing's names are read, then the records from the highest number down to the first
    readable one: an unreadable file is passed over, as the full read skips it, and so is one whose
    content names another number than its file name.
    """

    return latest_record_at(observed_json_files(comparison_directory(task_root, leaf_id)))


def latest_record_at(paths: tuple[Path, ...]) -> ReviewTreeComparisonRecord | None:
    """:func:`latest_record` of a listing already read: its highest-numbered readable record."""

    numbered = sorted(
        ((int(path.stem), path) for path in paths if path.stem.isdigit()), reverse=True
    )
    for number, path in numbered:
        record = _named_record(path, number)
        if record is not None:
            return record
    return None


def numbered_record(
    task_root: Path, leaf_id: str, number: int
) -> ReviewTreeComparisonRecord | None:
    """The leaf's record ``number``, reading only its own file; ``None`` when it has none."""

    return _named_record(comparison_directory(task_root, leaf_id) / f"{number}.json", number)
