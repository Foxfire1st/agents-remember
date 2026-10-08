"""The archive hook's receipts: one per attempt that did something, numbered without gaps.

Layout under a task's ``notes/reports``:

* ``review-artifact-cleanup.json`` -- the latest receipt, the name every reader opens;
* ``review-artifact-cleanup.attempt-<n>.json`` -- each earlier receipt, named by its own number.

**A deletion is never unrecorded.** An attempt that is about to delete writes its receipt first,
with the state ``in-progress`` and the list of what it is about to delete, and replaces it with the
outcome when it is done. If the receipt cannot be written, the attempt deletes nothing. If the
process dies in between, the in-progress receipt stays; the next attempt keeps it under its number
and reports each listed artifact that is gone as deleted in that attempt.

**What leaves a receipt.** An attempt that deleted something, failed on something, or found
something newly absent: an artifact an interrupted attempt had listed, or one that failed earlier
and is gone now. A repeated attempt that does none of these writes nothing; the latest receipt
already says it all.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from agents_remember.kernel.atomic_write import atomic_write_bytes, atomic_write_text

CLEANUP_REPORT_NAME: Final = "review-artifact-cleanup.json"
IN_PROGRESS: Final = "in-progress"
# Receipt keys whose entries name one deleted artifact each, by ``ref`` or by ``path``.
_ARTIFACT_KEYS: Final = ("reviewRefs", "retainedCodeRefs", "datasetCopies")
DELETION_KEYS: Final = (*_ARTIFACT_KEYS, "releasedGenerations")
_NUMBERED: Final = re.compile(r"\.attempt-(\d+)$")


def _name(entry: dict[str, Any]) -> str | None:
    return entry.get("ref") or entry.get("path")


def _names(document: dict[str, Any], keys: tuple[str, ...] = _ARTIFACT_KEYS) -> set[str]:
    return {name for key in keys for entry in document.get(key, []) if (name := _name(entry))}


@dataclass
class ReceiptLedger:
    """The receipts one archived task holds, and the one this attempt writes."""

    path: Path
    outside: Callable[[Path], str | None]
    still_there: Callable[[str], bool]
    earlier: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    begun: int | None = None

    @classmethod
    def open(
        cls,
        reports: Path,
        outside: Callable[[Path], str | None],
        still_there: Callable[[str], bool],
    ) -> ReceiptLedger:
        """Read every receipt earlier attempts left, oldest first.

        ``outside`` says why a path is not physically the task's own (then nothing is read from or
        written to it). ``still_there`` says whether a named artifact of the task still exists; it
        answers yes for a name that is no artifact of the task, so such a name is never reported
        as gone.
        """

        path = reports / CLEANUP_REPORT_NAME
        ledger = cls(path, outside, still_there)
        if outside(path) is not None:
            return ledger
        numbered: list[tuple[int, dict[str, Any]]] = []
        for candidate in reports.glob(f"{path.stem}.attempt-*{path.suffix}"):
            match = _NUMBERED.search(candidate.stem)
            document = _read(candidate)
            if match is not None and document is not None:
                numbered.append((int(match.group(1)), document))
        numbered.sort(key=lambda item: item[0])
        if path.exists():
            # A receipt written before receipts were numbered, or one that cannot be read, counts
            # as the one after the last numbered file; a numbered copy of the same attempt is the
            # same receipt.
            latest = _read(path) or {}
            attempt = int(latest.get("attempt", numbered[-1][0] + 1 if numbered else 1))
            numbered = [item for item in numbered if item[0] != attempt]
            numbered.append((attempt, latest))
        ledger.earlier = numbered
        return ledger

    @property
    def latest(self) -> int:
        return self.earlier[-1][0] if self.earlier else 0

    def already_absent(self, document: dict[str, Any]) -> list[dict[str, Any]]:
        """What earlier attempts dealt with and this attempt therefore finds gone.

        Three sources: an artifact an earlier receipt lists as deleted; one an interrupted attempt
        was about to delete and that is gone; one that failed earlier and is gone without this
        hook having deleted it.
        """

        current = _names(document) | {entry.get("target") for entry in document["failures"]}
        absent: dict[str, dict[str, Any]] = {}
        for attempt, earlier in self.earlier:
            interrupted = earlier.get("state") == IN_PROGRESS
            listed = earlier.get("planned", {}) if interrupted else earlier
            for key in _ARTIFACT_KEYS:
                for name in sorted(_names(listed, (key,)) - current - absent.keys()):
                    if interrupted and self.still_there(name):
                        continue
                    absent[name] = {"artifact": name, "kind": key, "deletedInAttempt": attempt}
                    if interrupted:
                        absent[name]["interrupted"] = True
        for attempt, earlier in self.earlier:
            for entry in earlier.get("failures", []):
                name = entry.get("target")
                if not name or name in current or name in absent or self.still_there(name):
                    continue
                absent[name] = {"artifact": name, "kind": "failures", "failedInAttempt": attempt}
        return list(absent.values())

    def preview(self, document: dict[str, Any]) -> None:
        """Say on a dry run what a real attempt would leave: the receipt, its number, its files.

        ``previousAttempt`` says how the attempt before ended, as its receipt records it:
        ``failed`` when it names a failure, ``interrupted`` when it was never replaced by an
        outcome, ``finished`` otherwise, and ``None`` when no attempt left a receipt.
        """

        writes = not self._nothing_to_record(document)
        document["receipt"] = "would-write" if writes else "unchanged"
        document["attempt"] = self.latest + 1 if writes else self.latest
        document["receiptFiles"] = (
            [path.as_posix() for path in self._files_written()] if writes else []
        )
        document["previousAttempt"] = self._previous_attempt()

    def _files_written(self) -> list[Path]:
        """The files an attempt that leaves a receipt writes: the receipt, and the one set aside."""

        if not self.path.exists():
            return [self.path]
        return [self._numbered(self.latest), self.path]

    def _previous_attempt(self) -> str | None:
        if not self.earlier:
            return None
        latest = self.earlier[-1][1]
        if latest.get("state") == IN_PROGRESS:
            return "interrupted"
        failed = latest.get("failures") or latest.get("state") in {"partial", "failed"}
        return "failed" if failed else "finished"

    def _numbered(self, attempt: int) -> Path:
        return self.path.with_name(f"{self.path.stem}.attempt-{attempt}{self.path.suffix}")

    def begin(self, planned: dict[str, Any]) -> str | None:
        """Write this attempt's receipt before it deletes anything; why not, when it cannot be."""

        if (problem := self.outside(self.path)) is not None:
            return problem
        try:
            attempt = self._set_aside() + 1
            receipt = {
                "schema": planned["schema"],
                "state": IN_PROGRESS,
                "taskId": planned["taskId"],
                "reason": planned["reason"],
                "attempt": attempt,
                "planned": {key: planned[key] for key in DELETION_KEYS},
            }
            atomic_write_text(self.path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        except (OSError, ValueError) as error:
            return str(error)
        self.begun = attempt
        return None

    def finish(self, document: dict[str, Any]) -> None:
        """Replace the in-progress receipt by the outcome, or write none when nothing happened."""

        if (problem := self.outside(self.path)) is not None:
            self._unwritten(document, problem)
            return
        try:
            if self.begun is None and self._nothing_to_record(document):
                document["attempt"] = self.latest
                document["reportPath"] = self.path.as_posix()
                document["receipt"] = "unchanged"
                return
            document["attempt"] = self.begun if self.begun is not None else self._set_aside() + 1
            atomic_write_text(self.path, json.dumps(document, indent=2, sort_keys=True) + "\n")
            document["reportPath"] = self.path.as_posix()
        except (OSError, ValueError) as error:
            self._unwritten(document, str(error))

    def _unwritten(self, document: dict[str, Any], problem: str) -> None:
        document["reportPath"] = None
        document["failures"].append({"target": self.path.as_posix(), "detail": problem})
        document["state"] = "partial"

    def _nothing_to_record(self, document: dict[str, Any]) -> bool:
        if any(document[key] for key in (*DELETION_KEYS, "failures")) or not self.earlier:
            return False
        latest = self.earlier[-1][1]
        if latest.get("state") == IN_PROGRESS:
            return False
        recorded = _names(latest) | {entry["artifact"] for entry in latest.get("alreadyAbsent", [])}
        return all(entry["artifact"] in recorded for entry in document["alreadyAbsent"])

    def _set_aside(self) -> int:
        """Keep the latest receipt under its own number; return that number.

        The numbered name comes from the receipt's own attempt number, so setting the same receipt
        aside twice (after a death between this copy and the next write) repeats one file instead
        of numbering a second copy.
        """

        if not self.path.exists():
            return self.latest
        target = self._numbered(self.latest)
        if (problem := self.outside(target)) is not None:
            raise OSError(problem)
        atomic_write_bytes(target, self.path.read_bytes())
        return self.latest


def _read(path: Path) -> dict[str, Any] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None
