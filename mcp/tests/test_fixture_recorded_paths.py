"""MIK-R82 rule 7: recorded payloads keep the paths they were recorded with.

A test fixture may carry a recorded payload that names a ``/tmp/...`` path from the machine
that recorded it. Those paths stay as recorded: no test rewrites them, and no test opens
them -- they are data under test, not files the suite reads. What the suite does guard is
developer-local paths: a fixture must never name the developer's own home (``/home/...``,
``/Users/...``), because the next checkout would read another machine's directories. This
module is that guard: it flags developer-local paths in fixtures, it lets recorded ``/tmp``
payload paths and the suite's synthetic homes (``/home/test``, ``/home/x``) through, and it
proves no test source references a recorded payload path.
"""

from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / "fixtures"

# A path that only exists on the machine that wrote it: a home directory, or an environment
# variable that expands to one. Recorded ``/tmp`` payload paths are deliberately absent:
# rule 7 keeps them as recorded, and no test opens them.
_DEVELOPER_LOCAL = (
    re.compile(r"/home/[A-Za-z0-9_.-]+"),
    re.compile(r"/Users/[A-Za-z0-9_.-]+"),
    re.compile(r"\$(?:HOME|USERPROFILE|HOMEDRIVE|HOMEPATH)\b"),
    re.compile(r"%(?:HOME|USERPROFILE|HOMEDRIVE|HOMEPATH)%"),
    re.compile(r"~/[A-Za-z0-9_.\-/]+"),
)
# The suite's synthetic-home convention (``test_harness_control_claude.py`` runs with
# ``HOME=/home/test``; conversation fixtures launch with ``/home/x/...``): recorded values,
# not a developer's machine, so the guard lets them through.
_SYNTHETIC_HOMES = frozenset({"/home/test", "/home/x"})
_RECORDED_PAYLOAD = re.compile(r"/tmp/[A-Za-z0-9_.\-/]+")


def fixture_text(path: Path) -> str:
    """Inspect the complete payload; compressed container bytes are not fixture text."""
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def find_developer_local_paths(text: str) -> list[str]:
    """Every developer-local path ``text`` names; recorded ``/tmp`` payload paths never match."""

    found: list[str] = []
    for pattern in _DEVELOPER_LOCAL:
        found.extend(match.group(0) for match in pattern.finditer(text))
    return sorted(set(found) - _SYNTHETIC_HOMES)


def recorded_payload_paths(text: str) -> list[str]:
    """Every recorded ``/tmp`` payload path ``text`` names."""

    return sorted(set(match.group(0) for match in _RECORDED_PAYLOAD.finditer(text)))


def test_the_guard_flags_developer_homes_and_lets_recorded_payloads_through() -> None:
    """The positive control: without these patterns the guard below would prove nothing."""

    assert find_developer_local_paths("wrote /home/alice/tree and /Users/bob/tree") == [
        "/Users/bob",
        "/home/alice",
    ]
    assert find_developer_local_paths("under $HOME/work and %USERPROFILE%\\work") == [
        "$HOME",
        "%USERPROFILE%",
    ]
    assert find_developer_local_paths("synthetic homes stay: /home/test and /home/x/bin") == []
    assert find_developer_local_paths("kept as recorded: /tmp/l5-fixture.py") == []
    assert recorded_payload_paths("kept as recorded: /tmp/l5-fixture.py") == ["/tmp/l5-fixture.py"]


def test_no_fixture_names_a_developer_local_path() -> None:
    assert FIXTURES.is_dir()
    offenders = {
        str(path.relative_to(TESTS)): find_developer_local_paths(fixture_text(path))
        for path in sorted(FIXTURES.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    assert {name: hits for name, hits in offenders.items() if hits} == {}


def test_no_test_opens_a_recorded_payload_path() -> None:
    """Rule 7, both halves: recorded ``/tmp`` paths stay as recorded, and no test opens them."""

    payloads = {
        hit
        for path in sorted(FIXTURES.rglob("*"))
        if path.is_file() and not path.is_symlink()
        for hit in recorded_payload_paths(fixture_text(path))
    }
    references = {}
    for path in sorted(TESTS.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        source = path.read_text(encoding="utf-8")
        references[str(path.relative_to(TESTS))] = sorted(
            {hit for hit in payloads if hit in source}
        )
    assert {name: hits for name, hits in references.items() if hits} == {}


def test_compressed_fixture_scans_the_decoded_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "record.json.gz"
    monkeypatch.setattr(sys.modules[__name__], "FIXTURES", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "TESTS", tmp_path)
    # A real home anywhere in decompressed data still violates the unchanged rule.
    fixture.write_bytes(gzip.compress(b'{"source": "/home/alice/native.jsonl"}'))
    with pytest.raises(AssertionError):
        test_no_fixture_names_a_developer_local_path()
    fixture.write_bytes(gzip.compress(b'{"source": "/tmp/recorded-native.jsonl"}'))
    test_no_fixture_names_a_developer_local_path()
    assert recorded_payload_paths(fixture_text(fixture)) == ["/tmp/recorded-native.jsonl"]


def test_malformed_compressed_fixture_is_not_an_empty_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "record.json.gz"
    fixture.write_bytes(b"not a gzip stream")
    monkeypatch.setattr(sys.modules[__name__], "FIXTURES", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "TESTS", tmp_path)
    with pytest.raises(gzip.BadGzipFile):
        test_no_fixture_names_a_developer_local_path()
