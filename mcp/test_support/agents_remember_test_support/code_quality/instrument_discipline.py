"""Instrument discipline: the checks that catch a measurement taken by an instrument that could not see.

Four faults recurred across `260915_role-capsules-and-native-eve`, and each is a property of the
*instrument* rather than of the product it measured:

* a **text probe** whose pattern could not match the thing it looked for, so its zero was read as a
  fact (`n/a`-link family; an exact-phrase grep against a claim class; a regex missing `re.M`);
* a **capture** that ran past the unit it was counting, so one row's comma count became a wider
  blob's (the "57 comma items" figure);
* a **run** that crashed and was read as a pass, because the pass line was printed either way (a
  comparison that printed `IDENTICAL` after two `awk` syntax errors; a harness that raised
  `FileNotFoundError` and never reported the property at all);
* a **record** that named a producer which did not produce it, or was produced by an earlier
  revision than the one it was compared against (a delta table whose producer was in no package; a
  before/after pair captured with two different revisions of the same check).

The shared remedy is not "look more carefully". It is that a **zero, a count, a pass or a producer
identity is only admissible when the instrument carries proof it could have produced the other
answer**. Each function below enforces one of those admissibility conditions and refuses — loudly,
with the reason — when the proof is absent.

These are helper functions, not a gate: they are called by the tests and by verification work, and
they never touch the filesystem except in :func:`producer_identity_refusal`, which is handed the
paths it may read.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass

REFUSAL_PREFIX = "instrument discipline:"

# Text that means a command failed, in the shapes these transcripts actually carry. Kept narrow on
# purpose: a broad marker list would flag every transcript that merely *discusses* a failure.
CRASH_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*Traceback \(most recent call last\)"),
    re.compile(r"^\s*[A-Za-z_.]*(?:Error|Exception|Exit):\s"),
    re.compile(r"^[a-z][\w.+-]*:\s+.*\b(?:error|Error|cannot|Cannot|not found|No such file)\b"),
    re.compile(r"^\s*\^+\s*(?:syntax error|backslash not last character on line)"),
    re.compile(
        r"^\s*[A-Za-z][\w .+-]*:\s+(?:syntax error|unsupported|invalid option|unknown option)"
    ),
)

# Text that claims the thing the crash would have prevented. A crash is only a fault when one of
# these follows it: a transcript that fails and says so is a working instrument.
PASS_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bIDENTICAL\b"),
    re.compile(r"\b\d+\s*/\s*\d+\b"),
    re.compile(r"\b(?:PASS|PASSED|OK)\b"),
    re.compile(r"\b(?:exit|status)\s*[:=]?\s*0\b", re.I),
)

EXIT_CODE = re.compile(r"\bexit(?:\s+code)?\s*[:=]?\s*(\d+)\b", re.I)

# An exit status is *evidence of a run* only where the artifact records it as one: on a command line
# that carries its status, or on a standalone status line. `EXIT_CODE` alone also matches prose that
# describes the convention ("it will exit 0 on success, exit 1 on a mismatch"), which is how a
# document with no run in it at all came to satisfy the no-run refusal.
TRANSCRIPT_EXIT = re.compile(
    r"^(?:\s*(?:\$|>)\s.*\bexit(?:\s+code)?\s*[:=]?\s*(\d+)\b"
    r"|\s*(?:exit\s+code|exit|status)\s*[:=]?\s*(\d+)\s*$)",
    re.I | re.M,
)

# A result that the check could have failed to produce, as this corpus records one: a line carrying a
# **non-zero** count of findings, errors or violations. The quantity is the whole point — "0 errors
# found; no finding was produced" is a disclaimer, and a disclaimer is not evidence that the check can
# fail. This is a named heuristic, not a proof: it under-admits (a result phrased otherwise must be
# stated through ``can_fail_evidence``) and it does not admit the disclaimers that defeated the
# earlier singular-word search.
NONZERO_FINDINGS = re.compile(r"^[\W]*([1-9]\d*)\s*(?:findings?|errors?|violations?)\b", re.M)


@dataclass(frozen=True)
class Witness:
    """One known answer a pattern must reproduce before its count over real data means anything."""

    kind: str
    pattern: str
    sample: str
    describes: str


@dataclass(frozen=True)
class Probe:
    """A positive and a negative witness: what the probe must find, and what it must not."""

    positive: Witness
    negative: Witness


@dataclass(frozen=True)
class PatternCount:
    """A count over real text, carrying the witnesses that licensed it."""

    count: int
    pattern: str
    witnesses: tuple[Witness, ...]


@dataclass(frozen=True)
class CapturedWindow:
    """The lines a unit actually occupies, and how the window was closed."""

    text: str
    start_line: int
    end_line: int
    items: int
    closed_by: str


@dataclass(frozen=True)
class CrashScan:
    """What a captured transcript shows about whether its producer ran to completion."""

    crash_lines: tuple[int, ...]
    pass_lines: tuple[int, ...]
    crashed_before_pass: bool
    detail: str


@dataclass(frozen=True)
class ProducerIdentity:
    """The three facts a reproducible record must carry, and its bytes for the comparison."""

    script: pathlib.Path
    command: str
    revision: str


@dataclass(frozen=True)
class ProducerReproduction:
    """The outcome of re-running a recorded producer against the recorded artifact."""

    matches: bool
    recorded_digest: str
    produced_digest: str | None
    output: str


def _refuse(reason: str) -> ValueError:
    return ValueError(f"{REFUSAL_PREFIX} {reason}")


def _scan(pattern: str, text: str) -> int:
    return sum(1 for _ in re.finditer(pattern, text, re.M))


def _prove_probe(probe: Probe) -> None:
    """Refuse a probe that cannot see its own positive or cannot exclude its own negative."""
    for witness in probe.positive, probe.negative:
        re.compile(witness.pattern)
    if _scan(probe.positive.pattern, probe.positive.sample) < 1:
        raise _refuse(
            f"positive witness {probe.positive.describes!r} did not match its own sample, so the "
            f"probe cannot see the shape it is looking for: {probe.positive.pattern!r}"
        )
    if _scan(probe.negative.pattern, probe.negative.sample) > 0:
        raise _refuse(
            f"negative witness {probe.negative.describes!r} matched its own sample, so the probe is "
            f"not discriminating: {probe.negative.pattern!r}"
        )


def counted_pattern(pattern: str, text: str, *, probe: Probe) -> PatternCount:
    """Count ``pattern`` over ``text`` only after the probe has been proved able to see both answers.

    The positive witness must match, so a zero cannot come from a pattern that matches nothing; the
    negative witness must not match, so a count cannot come from a pattern loose enough to match the
    thing it was supposed to exclude. Both failures are refusals, not zeroes.

    The licence the probe carries belongs to **the pattern it proved**, not to whoever calls with it:
    ``pattern`` must be the probe's own positive pattern. A proved probe for one shape licenses no
    zero for another, or the count would be returned under a proof taken for a different instrument.
    """
    _prove_probe(probe)
    if pattern != probe.positive.pattern:
        raise _refuse(
            f"the counted pattern is not the one the probe proved: counted {pattern!r} against "
            f"positive witness {probe.positive.pattern!r}, so the count carries no licence"
        )
    return PatternCount(
        count=_scan(pattern, text), pattern=pattern, witnesses=(probe.positive, probe.negative)
    )


def _matching_line(lines: Sequence[str], pattern: str) -> int | None:
    for position, line in enumerate(lines):
        if re.search(pattern, line):
            return position
    return None


def capture_bounded_window(
    text: str,
    *,
    start_pattern: str = r"^\| memory-refresh-attestations",
    boundary_pattern: str = r"^\|",
    max_lines: int = 200,
) -> CapturedWindow:
    """Capture the unit that starts at ``start_pattern`` and no further.

    The window opens on the first line matching ``start_pattern`` and closes at the first of: a
    blank line, a line matching ``boundary_pattern``, or the line cap. A table with no blank lines
    between its rows therefore ends at the next row rather than swallowing it — the fault that
    turned one row's 21 commas into a measurement of everything below it.

    ``closed_by`` names the boundary that actually closed the window, the cap included: a window cut
    off at ``max_lines`` says so, because a capped capture and a complete one are the same shape
    otherwise and the lines left unread are exactly what a reader cannot see.
    """
    lines = text.splitlines()
    start = _matching_line(lines, start_pattern)
    if start is None:
        raise _refuse(
            f"no line matches the start pattern {start_pattern!r}, so no unit was captured"
        )
    end = start
    closed_by = "end of evidence"
    while end + 1 < len(lines):
        if (end + 1 - start) >= max_lines:
            closed_by = "line cap"
            break
        following = lines[end + 1]
        if not following.strip():
            closed_by = "blank line"
            break
        if end + 1 > start and re.search(boundary_pattern, following):
            closed_by = "boundary line"
            break
        end += 1
    captured = "\n".join(lines[start : end + 1])
    return CapturedWindow(
        text=captured,
        start_line=start + 1,
        end_line=end + 1,
        items=len(captured.split(",")),
        closed_by=closed_by,
    )


def crash_scan(text: str) -> CrashScan:
    """Report crash markers, and whether any of them precedes a claim the crash would invalidate.

    A transcript whose producer failed and whose next claim is healthy is the shape that reads as a
    pass: the failure is visible but the claim is not scoped to the run that produced it.
    """
    crash_lines: list[int] = []
    pass_lines: list[int] = []
    for number, line in enumerate(text.splitlines(), 1):
        for marker in CRASH_MARKERS:
            if marker.search(line):
                crash_lines.append(number)
                break
    for number, line in enumerate(text.splitlines(), 1):
        for marker in PASS_MARKERS:
            if marker.search(line):
                pass_lines.append(number)
                break
    first_crash = crash_lines[0] if crash_lines else None
    crashed_before_pass = bool(
        crash_lines and pass_lines and first_crash is not None and pass_lines[-1] > first_crash
    )
    if not crash_lines:
        detail = "no crash marker in the captured output"
    elif not crashed_before_pass:
        detail = f"crash marker(s) at line(s) {crash_lines} with no pass claim after them"
    else:
        detail = (
            f"crash marker(s) at line(s) {crash_lines} precede the pass claim at line "
            f"{pass_lines[-1]}, so the claim is not attributable to a run that completed"
        )
    return CrashScan(
        crash_lines=tuple(crash_lines),
        pass_lines=tuple(pass_lines),
        crashed_before_pass=crashed_before_pass,
        detail=detail,
    )


def _recorded_exit(text: str) -> int | None:
    """The exit status the artifact records as a run, or ``None`` when it only describes one."""
    statuses = [int(match.group(1) or match.group(2)) for match in TRANSCRIPT_EXIT.finditer(text)]
    return statuses[-1] if statuses else None


def _reports_findings(text: str) -> bool:
    """Whether the artifact reports a non-zero result of its own — see :data:`NONZERO_FINDINGS`."""
    return NONZERO_FINDINGS.search(text) is not None


def check_artifact_refusal(
    *,
    text: str,
    script_name: str,
    probe: Probe,
    producer_ran: bool = False,
    can_fail_evidence: str = "",
) -> str | None:
    """Refuse a check's recorded artifact unless the run is evidenced and could have failed.

    Four refusals, each named:

    * the transcript **crashed and then claimed a result**, so the claim is not attributable to a run
      that completed;
    * the artifact evidences **no run at all** — no exit status and no producer transcript;
    * the recorded run **exited non-zero and printed no result**, so a reader sees no failures: a
      status line with nothing after it is the shape a crash takes when it leaves no traceback;
    * nothing states **how the check can fail**. A producer that exits non-zero on failure satisfies
      this without saying so, so the caller passes ``can_fail_evidence``: the mutation, seed, control
      or failing input it exercised. An artifact that reports findings is its own evidence.

    The ``probe`` is required so a caller cannot reach an admissibility verdict without having
    licensed its own pattern first. Returns ``None`` when the artifact is admissible, otherwise the
    refusal reason.
    """
    _prove_probe(probe)
    scan = crash_scan(text)
    if scan.crashed_before_pass:
        return f"{script_name}: {scan.detail}"
    recorded_exit = _recorded_exit(text)
    if recorded_exit is None and not producer_ran:
        return (
            f"{script_name}: the artifact carries no exit status and no producer transcript, so no "
            f"run is evidenced at all"
        )
    if (
        recorded_exit not in (None, 0)
        and not scan.pass_lines
        and not _reports_findings(text)
        and not can_fail_evidence.strip()
    ):
        return (
            f"{script_name}: the recorded run exited {recorded_exit} and reported no result at all, "
            f"so a reader sees no failures; an exit status with no claim after it is a crash, not a "
            f"pass"
        )
    if not _reports_findings(text) and not can_fail_evidence.strip():
        return (
            f"{script_name}: the artifact shows a run but not that the check can fail — no finding "
            f"and no stated way for the check to fail — so its clean result is vacuous"
        )
    return None


def producer_identity_refusal(record: ProducerIdentity, *, worktree: pathlib.Path) -> str | None:
    """Refuse a record whose named producer, command or revision is not the one that produced it.

    The named script must be a file in ``worktree`` — a producer named in prose but absent from the
    package cannot have produced the artifact, which is exactly how a delta table came to cite a
    script that never wrote it.
    """
    if not record.script.name:
        return "the record names no producer script"
    resolved = worktree / record.script
    if not resolved.is_file():
        return f"the named producer {record.script} is not in the package at {worktree}"
    if not record.command.strip():
        return f"the record names no command, so {record.script} cannot be re-run"
    if not record.revision.strip():
        return "the record names no revision, so its artifact cannot be pinned to a source tree"
    return None


def reproduce_recorded_producer(
    *,
    producer: pathlib.Path,
    arguments: Sequence[str],
    recorded: pathlib.Path,
    output_argument: bool,
    timeout_seconds: int = 120,
) -> ProducerReproduction:
    """Re-run a recorded producer and compare its output to the artifact it is said to produce.

    ``output_argument`` writes the producer's output to a scratch file that is appended to
    ``arguments``; otherwise the producer's stdout is the artifact. The comparison is byte-exact:
    "regenerated from its own producer" is a claim about bytes, and a near-match is a different
    producer or a different input.

    The scratch file never lands beside the recorded artifact, so re-running a producer cannot write
    into — or temporarily overwrite — the evidence it is being checked against. A producer that
    exits non-zero, or that exits 0 without writing its output argument, is reported as
    ``matches=False`` rather than raising: the helper's answer to "did this reproduce?" is False.
    """
    recorded_bytes = recorded.read_bytes()
    recorded_digest = hashlib.sha256(recorded_bytes).hexdigest()
    with tempfile.TemporaryDirectory(prefix="ar-instrument-reproduction-") as scratch:
        produced_path: pathlib.Path | None = None
        if output_argument:
            produced_path = pathlib.Path(scratch) / f"{recorded.name}.reproduced"
            command = [sys.executable, str(producer), *arguments, str(produced_path)]
        else:
            command = [sys.executable, str(producer), *arguments]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_seconds, check=False
        )
        if completed.returncode != 0:
            return ProducerReproduction(
                matches=False,
                recorded_digest=recorded_digest,
                produced_digest=None,
                output=f"exit {completed.returncode}: {completed.stderr.strip() or completed.stdout.strip()}",
            )
        if produced_path is not None:
            if not produced_path.is_file():
                return ProducerReproduction(
                    matches=False,
                    recorded_digest=recorded_digest,
                    produced_digest=None,
                    output=(
                        f"exit 0 but the producer wrote no artifact to {produced_path.name!r}: "
                        f"{completed.stdout.strip()}"
                    ),
                )
            produced_bytes = produced_path.read_bytes()
        else:
            produced_bytes = completed.stdout.encode("utf-8")
    produced_digest = hashlib.sha256(produced_bytes).hexdigest()
    return ProducerReproduction(
        matches=produced_digest == recorded_digest,
        recorded_digest=recorded_digest,
        produced_digest=produced_digest,
        output=completed.stdout.strip(),
    )
