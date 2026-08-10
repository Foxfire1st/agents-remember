"""The cold-start guard: the server must import and start with no network egress.

This is a regression guard for a class of defect, not for one library. The class is
"something on the server's import path reaches the network", and its cost is that the
server cannot start where there is none -- a fresh container, an offline machine, a
hermetic CI job. The instance that motivated the guard was ``models/tokens.py`` building
its default counter at module scope, which made ``tiktoken.get_encoding("o200k_base")``
download the vocabulary from openaipublic.blob.core.windows.net while
``mcp/tools/base.py`` was still importing.

The test is a subprocess for two reasons, both of which decide whether it can fail at all:

* tiktoken memoizes loaded encodings in ``tiktoken.registry.ENCODINGS``, and any earlier
  test in the session has already populated it. In-process, the load under test would be
  a dictionary hit that never touches the disk, let alone the network, and the assertion
  would pass against a package that ships no vocabulary at all.
* the caches must be cold. The child gets ``TIKTOKEN_CACHE_DIR``, ``DATA_GYM_CACHE_DIR``,
  ``TMPDIR`` and ``HOME`` pointed at an empty directory, so every place tiktoken looks --
  including the default ``<tmp>/data-gym-cache`` -- is empty. Pointing the operator
  variable at a *cold* directory is deliberate: it also proves the package's own vendored
  copy wins over an exported one that would send the load back to the network.

Nothing here imports ``agents_remember.models.tokens`` at module scope, and nothing may.
That module builds ``DEFAULT_TOKEN_COUNTER`` at import, so a module-scope import runs the
load under test inside the *parent* pytest process, during collection, before any
assertion exists to see what it did -- and the parent has network and a writable checkout,
which is precisely the pair of conditions the guard is asserting the absence of. It cost
this file its teeth once already: with the load happening at collection,
``tiktoken.load.read_file_cached`` met the mismatching bytes first, deleted the vendored
file, re-downloaded it into the package directory and let all eight tests report green.
Everything below imports the module inside the test that needs it, via ``tokens_module``.

The child blocks egress before importing anything, and proves the block took effect before
trusting the run. Verified to bite, on a scratch copy of the tree with the vendored
vocabulary deleted: the child dies while importing the server, with
``TokenizerVocabularyError`` raised from ``DEFAULT_TOKEN_COUNTER = TiktokenTokenCounter()``
in ``models/tokens.py`` -- the refusal, not the ``requests.exceptions.ConnectionError`` an
unguarded download would raise, because ``vendored_vocabulary_cache`` never lets the load
reach the network at all. The probe exits 1 and both ``ColdStartTests`` fail on its return
code with that traceback as the message; the rest of this file fails alongside them,
because ``tokens_module()`` raises the same refusal in this process. Thirteen failures, no
collection error, nothing skipped.

Removal is only the loudest way to break the vendored file. The likelier one is a copy that
is present and wrong, and no child can observe that one -- tiktoken never gets far enough
to try the network. That case is ``CorruptVendoredVocabularyTests``.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from unittest import mock

from tiktoken import load as tiktoken_load
from tiktoken_ext import openai_public

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import TokenizerVocabularyError

# tiktoken's own cache variable. Spelled out rather than imported from the module under
# test, because importing that module up here is the defect this file guards against. Its
# siblings -- DATA_GYM_CACHE_DIR, TMPDIR, HOME -- are literals where the probe sets them;
# this one is a constant only because two places need it.
TIKTOKEN_CACHE_DIR_ENV = "TIKTOKEN_CACHE_DIR"

# The repository file that keeps the vendored copy's bytes intact through a checkout, and
# the directory its entry has to name a file in. That entry is a literal path rather than a
# glob, so it is found by the directory it lives in and then checked against the name of
# the file actually shipped.
GITATTRIBUTES = REPO_ROOT / ".gitattributes"
VENDORED_VOCABULARY_ATTRIBUTE_PREFIX = "mcp/src/agents_remember/package_data/tiktoken/"

# How long a re-entrant load may take before the deadlock it is checking for is the only
# remaining explanation. Generous against a cold first tiktoken load (seconds), and finite
# because the failure mode under test is an unbounded wait: a regression must report the
# deadlock, not reproduce it inside the suite.
REENTRANT_LOAD_TIMEOUT_SECONDS = 60.0

# The payload the child counts. Fixed so the parent can count the same thing with its own
# already-loaded counter and compare: equal counts are the proof that the vendored
# vocabulary is the real one and that this change moved no numbers.
PROBE_PAYLOAD = {"ok": True, "operation": "ping"}

PROBE = '''\
"""Start the MCP server with every network path closed, and report what it counted."""

import json
import socket
import sys
import tempfile
from pathlib import Path


class BlockedNetwork(OSError):
    """Raised in place of any outbound connection this process attempts."""


def _blocked(*args, **kwargs):
    raise BlockedNetwork("network egress is blocked by the cold-start guard")


# Patch the connection calls rather than the `socket.socket` class itself: replacing the
# class breaks unrelated imports that subclass it, which turns a clean "no egress" run
# into a confusing failure that proves nothing.
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
socket.gethostbyname = _blocked

# A block that silently did not take would make every assertion below vacuous.
try:
    socket.create_connection(("example.invalid", 80))
except BlockedNetwork:
    pass
else:
    raise SystemExit("the network block did not take effect; the run proves nothing")

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.server import create_server
from agents_remember.models.tokens import DEFAULT_TOKEN_COUNTER

root = Path(tempfile.mkdtemp())
server = create_server(
    McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )
)
payload = json.loads(sys.argv[1])
print(
    json.dumps(
        {
            "server": server.name,
            "tokenizer": DEFAULT_TOKEN_COUNTER.name,
            "exact": DEFAULT_TOKEN_COUNTER.exact,
            "tokens": DEFAULT_TOKEN_COUNTER.count(payload),
        }
    )
)
'''


def tokens_module() -> ModuleType:
    """Import the module under test, from inside the test that needs it.

    Deliberately not a module-scope import -- see this module's docstring for what a
    collection-time load costs. ``importlib.import_module`` rather than a function-level
    ``import`` statement because PLC0415 forbids the latter and there is no per-file ignore
    for it; the module object is the same either way.
    """

    return importlib.import_module("agents_remember.models.tokens")


def run_cold_start_probe(workspace: Path) -> subprocess.CompletedProcess[str]:
    """Run the probe in a child with cold tiktoken caches and this checkout on the path."""

    cold = workspace / "cold-cache"
    cold.mkdir()
    script = workspace / "cold_start_probe.py"
    script.write_text(PROBE, encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONPATH": str(MCP_SRC),
            TIKTOKEN_CACHE_DIR_ENV: str(cold),
            "DATA_GYM_CACHE_DIR": str(cold),
            "TMPDIR": str(cold),
            "HOME": str(cold),
        }
    )
    return subprocess.run(
        [sys.executable, str(script), json.dumps(PROBE_PAYLOAD)],
        capture_output=True,
        text=True,
        timeout=300,
        env=environment,
        check=False,
    )


class ColdStartTests(unittest.TestCase):
    def test_the_server_starts_with_cold_caches_and_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            result = run_cold_start_probe(Path(workspace))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["server"], "Agents Remember")

    def test_the_cold_start_counts_exactly_what_a_warm_one_does(self) -> None:
        # The behaviour that must NOT have changed: same tokenizer, same exactness, same
        # number. A lazy load with an approximate fallback would pass the start test above
        # and fail this one, which is the whole reason the vocabulary is vendored.
        warm = tokens_module().DEFAULT_TOKEN_COUNTER
        with tempfile.TemporaryDirectory() as workspace:
            result = run_cold_start_probe(Path(workspace))
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["tokenizer"], "tiktoken:o200k_base")
        self.assertIs(report["exact"], True)
        self.assertEqual(report["tokens"], warm.count(PROBE_PAYLOAD))


class VendoredVocabularyTests(unittest.TestCase):
    def test_the_shipped_file_is_the_one_tiktoken_asks_for(self) -> None:
        # Every hash here is re-derived from the installed tiktoken rather than restated, so
        # a version bump that moves the URL or changes the expected content fails here
        # instead of silently sending the load back to the network on the next cold machine.
        # That includes VENDORED_VOCABULARY_SHA256: the loader has to check the digest
        # before tiktoken sees the file, which means holding a copy of it, and this is what
        # keeps that copy from becoming a second source of truth.
        requested: dict[str, str] = {}

        def record(url: str, expected_hash: str | None = None) -> dict[bytes, int]:
            requested["url"] = url
            requested["hash"] = expected_hash or ""
            return {}

        with mock.patch.object(openai_public, "load_tiktoken_bpe", record):
            openai_public.o200k_base()

        tokens = tokens_module()
        path = tokens.vendored_vocabulary_path()
        self.assertEqual(requested["url"], tokens.VENDORED_VOCABULARY_URL)
        self.assertEqual(path.name, hashlib.sha1(requested["url"].encode()).hexdigest())
        self.assertEqual(tokens.VENDORED_VOCABULARY_SHA256, requested["hash"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), requested["hash"])

    def test_the_gitattributes_entry_names_the_shipped_file(self) -> None:
        # The entry is a literal path and the path is sha1(URL), so a tiktoken release that
        # moves the URL renames the file and leaves the rule naming a file that no longer
        # exists. Nothing else notices: an orphaned `-text` rule protects nothing, silently,
        # and only on `core.autocrlf=true` clones -- where the checkout then rewrites the
        # line endings of a file whose bytes are its identity, which is exactly the
        # corruption CorruptVendoredVocabularyTests refuses at load time.
        entries = [
            line.split()
            for line in GITATTRIBUTES.read_text(encoding="utf-8").splitlines()
            if line.startswith(VENDORED_VOCABULARY_ATTRIBUTE_PREFIX)
        ]
        name = tokens_module().vendored_vocabulary_path().name
        self.assertEqual(entries, [[f"{VENDORED_VOCABULARY_ATTRIBUTE_PREFIX}{name}", "-text"]])

    def test_a_missing_vocabulary_fails_loudly(self) -> None:
        tokens = tokens_module()
        with (
            tempfile.TemporaryDirectory() as empty,
            mock.patch.object(tokens, "VENDORED_VOCABULARY_DIR", Path(empty)),
            self.assertRaises(TokenizerVocabularyError),
            tokens.vendored_vocabulary_cache(tokens.VENDORED_ENCODING_NAME),
        ):
            self.fail("an absent vocabulary must never fall through to a download")

    def test_an_encoding_this_package_does_not_ship_is_refused(self) -> None:
        tokens = tokens_module()
        with (
            self.assertRaises(TokenizerVocabularyError),
            tokens.vendored_vocabulary_cache("cl100k_base"),
        ):
            self.fail("only the vendored encoding may be loaded")

    def test_the_cache_override_does_not_outlive_the_load(self) -> None:
        tokens = tokens_module()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(TIKTOKEN_CACHE_DIR_ENV, None)
            with tokens.vendored_vocabulary_cache(tokens.VENDORED_ENCODING_NAME):
                cache_dir = os.environ[TIKTOKEN_CACHE_DIR_ENV]
                self.assertEqual(cache_dir, str(tokens.VENDORED_VOCABULARY_DIR))
            self.assertNotIn(TIKTOKEN_CACHE_DIR_ENV, os.environ)

    def test_an_operator_cache_dir_is_overridden_then_restored(self) -> None:
        # Overridden because an operator's cache may be cold, and honouring a cold one is
        # the download this exists to remove; restored because the process may hold other
        # tiktoken users whose cache choice is theirs to make.
        tokens = tokens_module()
        with mock.patch.dict(os.environ, {TIKTOKEN_CACHE_DIR_ENV: "/operator/cache"}):
            with tokens.vendored_vocabulary_cache(tokens.VENDORED_ENCODING_NAME):
                cache_dir = os.environ[TIKTOKEN_CACHE_DIR_ENV]
                self.assertEqual(cache_dir, str(tokens.VENDORED_VOCABULARY_DIR))
            self.assertEqual(os.environ[TIKTOKEN_CACHE_DIR_ENV], "/operator/cache")

    def test_building_a_counter_leaves_no_cache_dir_behind(self) -> None:
        tokens = tokens_module()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(TIKTOKEN_CACHE_DIR_ENV, None)
            counter = tokens.TiktokenTokenCounter()
            self.assertEqual(counter.name, "tiktoken:o200k_base")
            self.assertNotIn(TIKTOKEN_CACHE_DIR_ENV, os.environ)

    def test_holding_the_context_open_around_a_counter_does_not_deadlock(self) -> None:
        """The exported context manager may be re-entered by the thread already inside it.

        ``with vendored_vocabulary_cache(name): TiktokenTokenCounter()`` is the obvious use
        of a context manager this package exports, and the counter's own load re-enters it
        on the same thread. Against a non-reentrant lock held across the ``yield`` that is a
        permanent hang: no timeout, no traceback, a gate that never returns rather than one
        that fails.

        On a worker with a bounded join for exactly that reason -- a regression here has to
        report the deadlock rather than reproduce it inside the suite.
        """

        tokens = tokens_module()
        built: list[str] = []

        def build_inside_the_open_context() -> None:
            with tokens.vendored_vocabulary_cache(tokens.VENDORED_ENCODING_NAME):
                built.append(tokens.TiktokenTokenCounter().name)

        worker = threading.Thread(target=build_inside_the_open_context, daemon=True)
        worker.start()
        worker.join(timeout=REENTRANT_LOAD_TIMEOUT_SECONDS)
        self.assertFalse(worker.is_alive(), "re-entering the vendored vocabulary deadlocked")
        self.assertEqual(built, ["tiktoken:o200k_base"])


class CorruptVendoredVocabularyTests(unittest.TestCase):
    """A vendored file that is present but wrong must be refused, never repaired.

    This is the case the cold-start probe structurally cannot see. tiktoken's
    ``read_file_cached`` answers a SHA-256 mismatch by deleting the cached file and
    downloading a replacement over it, so if the digest check is left to tiktoken every
    corruption below ends as a silent fetch that rewrites the installed package directory
    and a suite that reports green -- measured: CRLF-mangling the vendored file, and
    truncating it to half its bytes, both passed while the file was quietly restored.

    Every case corrupts a *copy* in a temporary directory and points the loader at that.
    The shipped file is the subject of these tests; mutating it would leave the checkout
    damaged whenever one of them failed part-way, and the suite would then be asserting
    against its own debris.
    """

    def assert_corruption_is_refused(self, corrupt: Callable[[bytes], bytes]) -> None:
        """Point the loader at a corrupted copy of the vocabulary and require a refusal."""

        tokens = tokens_module()
        shipped = tokens.vendored_vocabulary_path()
        with tempfile.TemporaryDirectory() as directory:
            corrupt_dir = Path(directory)
            copy = corrupt_dir / shipped.name
            copy.write_bytes(corrupt(shipped.read_bytes()))
            digest = hashlib.sha256(copy.read_bytes()).hexdigest()
            self.assertNotEqual(digest, tokens.VENDORED_VOCABULARY_SHA256)
            with (
                mock.patch.object(tokens, "VENDORED_VOCABULARY_DIR", corrupt_dir),
                self.assertRaises(TokenizerVocabularyError) as refusal,
                tokens.vendored_vocabulary_cache(tokens.VENDORED_ENCODING_NAME),
            ):
                self.fail("a vocabulary whose bytes are wrong must never reach tiktoken")
            # The file, the digest expected and the digest found: the operator's next move
            # is to compare their copy against the one tiktoken asks for, and a message that
            # says only "corrupt" does not say against what.
            message = str(refusal.exception)
            self.assertIn(str(copy), message)
            self.assertIn(tokens.VENDORED_VOCABULARY_SHA256, message)
            self.assertIn(digest, message)
            # Still there. tiktoken was never handed the directory, so nothing deleted the
            # copy and nothing downloaded a replacement over it.
            self.assertTrue(copy.is_file())

    def test_a_line_ending_mangled_copy_is_refused(self) -> None:
        # What a `core.autocrlf=true` checkout does to a file git thinks is text, and the
        # reason `.gitattributes` marks this one `-text`. It opens fine and looks right.
        self.assert_corruption_is_refused(lambda data: data.replace(b"\n", b"\r\n"))

    def test_a_truncated_copy_is_refused(self) -> None:
        # An interrupted download or a partial write. The prefix is byte-identical, so
        # anything short of hashing the whole file accepts it.
        self.assert_corruption_is_refused(lambda data: data[: len(data) // 2])

    def test_a_counter_will_not_build_on_a_corrupt_copy(self) -> None:
        """The production entry point refuses, and refuses before tiktoken reads anything.

        ``TiktokenTokenCounter()`` is the statement that runs while ``mcp/tools/base.py`` is
        importing, so it is the one that has to fail. ``unreachable`` stands in for
        tiktoken's own fetch and earns its place twice: it is the assertion that no read was
        attempted at all, and it is what stops a regression here from actually downloading
        3.6 MB over the corrupt copy the way the unguarded code does.

        The corruption is a single flipped byte -- same length, same line endings, same
        everything a cheaper check than a digest could look at.
        """

        tokens = tokens_module()
        shipped = tokens.vendored_vocabulary_path()

        def unreachable(blobpath: str) -> bytes:
            raise AssertionError(f"the refusal must come before tiktoken reads {blobpath}")

        with tempfile.TemporaryDirectory() as directory:
            corrupt_dir = Path(directory)
            data = bytearray(shipped.read_bytes())
            data[0] ^= 0x01
            (corrupt_dir / shipped.name).write_bytes(data)
            with (
                mock.patch.object(tokens, "VENDORED_VOCABULARY_DIR", corrupt_dir),
                mock.patch.object(tiktoken_load, "read_file", unreachable),
                self.assertRaises(TokenizerVocabularyError),
            ):
                tokens.TiktokenTokenCounter()


if __name__ == "__main__":
    unittest.main()
