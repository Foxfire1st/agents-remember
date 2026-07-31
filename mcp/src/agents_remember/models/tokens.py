"""Token accounting helpers for modeled tool responses."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import tiktoken

from agents_remember.errors import TokenizerVocabularyError
from agents_remember.models.base import ResponseModel

# The one encoding this package counts tokens with, and the vocabulary file tiktoken would
# otherwise fetch the first time it is loaded.
#
# Vendoring that file is what lets the server start with no network egress. This module is
# on the import path of every MCP tool (mcp/tools/base.py imports it, and the counter below
# is built at module scope), so before the vocabulary shipped inside the package,
# `tiktoken.get_encoding` opened an HTTPS connection to openaipublic.blob.core.windows.net
# while the server was still importing: a fresh container, an offline machine and a
# hermetic CI job could not start the server at all, and nothing in the tree set
# TIKTOKEN_CACHE_DIR to give the download a warm cache to hit.
#
# It is vendored rather than lazily loaded behind an approximate fallback on purpose. A
# fallback makes the reported count depend on whether the machine that produced it had
# network, which silently mixes exact o200k_base counts with estimates inside one dashboard
# aggregate. The shipped file is byte-identical to the download -- this module verifies its
# SHA-256 before handing it to tiktoken -- so counts and the reported `tiktoken:o200k_base`
# name are unchanged and there is no second code path to keep honest.
VENDORED_ENCODING_NAME = "o200k_base"
VENDORED_VOCABULARY_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
# The digest tiktoken itself asserts for that URL (`expected_hash` in
# `tiktoken_ext/openai_public.py::o200k_base`). It is restated here because the check has to
# happen *before* tiktoken is pointed at the file: `tiktoken.load.read_file_cached` answers a
# hash mismatch by deleting the cached copy and downloading a replacement over it, so leaving
# the verification to tiktoken means a damaged vendored file is silently repaired -- from the
# network, into the installed package directory, on the server's startup path. Restating it is
# not a second source of truth: `mcp/tests/test_cold_start.py` re-derives this value from the
# installed tiktoken, so a release that changes what it asks for fails there.
VENDORED_VOCABULARY_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
VENDORED_VOCABULARY_DIR = Path(__file__).resolve().parent.parent / "package_data" / "tiktoken"
TIKTOKEN_CACHE_DIR_ENV = "TIKTOKEN_CACHE_DIR"
# Reentrant because the guarded region spans a `yield` in a public context manager. The
# obvious misuse -- `with vendored_vocabulary_cache(name): TiktokenTokenCounter()` -- has the
# counter's own load re-enter the manager on the same thread, which on a plain Lock is a
# permanent hang with no timeout and no diagnostic rather than a wrong answer.
_CACHE_DIR_LOCK = threading.RLock()


def vendored_vocabulary_path() -> Path:
    """The shipped copy of the ``o200k_base`` vocabulary.

    The file name is not a choice. ``tiktoken.load.read_file_cached`` looks a download up in
    its cache directory under the SHA-1 of the source URL, so that digest is the only name a
    cache hit can have. Deriving it from the URL here, rather than hard-coding the digest,
    keeps the shipped file and the download it stands in for provably the same thing.
    """

    digest = hashlib.sha1(VENDORED_VOCABULARY_URL.encode()).hexdigest()
    return VENDORED_VOCABULARY_DIR / digest


def _verify_vendored_vocabulary(encoding_name: str) -> Path:
    """Return the vendored vocabulary for ``encoding_name``, or refuse to name one.

    Absent and present-but-wrong are both refusals, and the second is the one that needs
    saying. tiktoken does check the SHA-256, but it does not fail closed on a mismatch:
    ``read_file_cached`` removes the offending file and downloads a replacement into its
    place. Pointed at this package's directory, that turns a corrupt vendored copy into a
    silent network fetch on the startup path *and* a rewrite of the installed tree -- or,
    on the read-only install the caller below is written for, into a ``PermissionError``
    from the write-back instead of the refusal designed here. Checking the digest before
    tiktoken is told where to look is what makes corruption behave like absence.

    The whole file is read to hash it. That is 3.6 MB once per counter construction, which
    in the server is once per process, and it buys the only signal that separates the
    vendored copy from a plausible-looking wrong one.
    """

    path = vendored_vocabulary_path()
    if encoding_name != VENDORED_ENCODING_NAME or not path.is_file():
        raise TokenizerVocabularyError(
            f"no vendored tiktoken vocabulary for {encoding_name!r}: this package ships "
            f"{VENDORED_ENCODING_NAME} only, at {path}. Loading anything else would mean "
            "downloading it, which the server must never do while starting."
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != VENDORED_VOCABULARY_SHA256:
        # Both digests, because the operator's next move is to compare the file they have
        # against the one tiktoken asks for, and a message that reports only "corrupt" does
        # not say against what. A checkout that rewrote the line endings and a truncated or
        # partially written copy both land here, and both open cleanly.
        raise TokenizerVocabularyError(
            f"the vendored tiktoken vocabulary at {path} is not the file it stands in for: "
            f"expected SHA-256 {VENDORED_VOCABULARY_SHA256}, found {digest}. Re-fetch it as "
            f"{VENDORED_VOCABULARY_DIR}/README.md describes; the server must not download it "
            "while starting, so this refuses rather than repairing itself."
        )
    return path


@contextmanager
def vendored_vocabulary_cache(encoding_name: str) -> Iterator[None]:
    """Resolve one tiktoken load against the vocabulary vendored into this package.

    The override wins over any ``TIKTOKEN_CACHE_DIR`` the operator exported: theirs may be
    cold, and honouring a cold one is exactly the download this exists to remove. Nothing is
    lost by ignoring it, since the shipped file is the same bytes.

    It is scoped to the load instead of exported process-wide because the vendored directory
    sits inside the installed package, which is routinely read-only. Left set, any *other*
    tiktoken encoding loaded later in this process would try to write its download there,
    and ``read_file_cached`` re-raises write failures for a caller-specified cache directory
    (it only swallows them for its own default).

    A vocabulary that is absent, or present with the wrong bytes, raises instead of letting
    tiktoken reach for the network -- see ``_verify_vendored_vocabulary``. Only the verified
    file's own directory is ever handed over, so tiktoken cannot be pointed at a directory
    whose contents were not checked.

    The lock is what keeps two *participating* callers from restoring each other's value
    part-way through a load, and no more than that. ``TIKTOKEN_CACHE_DIR`` is process-global
    and belongs to tiktoken, not to this module: a thread that reads or writes it without
    coming through here can still observe the override or overwrite it, and nothing
    available at this layer can prevent that. The honest scope is "the counters this package
    builds", which is what it is for.
    """

    path = _verify_vendored_vocabulary(encoding_name)
    with _CACHE_DIR_LOCK:
        previous = os.environ.get(TIKTOKEN_CACHE_DIR_ENV)
        os.environ[TIKTOKEN_CACHE_DIR_ENV] = str(path.parent)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(TIKTOKEN_CACHE_DIR_ENV, None)
            else:
                os.environ[TIKTOKEN_CACHE_DIR_ENV] = previous


class ResponseTokenCounter(Protocol):
    """Counts tokens for the canonical JSON form of a tool response."""

    @property
    def name(self) -> str: ...

    @property
    def exact(self) -> bool: ...

    def count(self, payload: Mapping[str, Any]) -> int:
        """Return the token count for payload."""
        ...


def _payload_json(payload: Mapping[str, Any]) -> str:
    """Return the canonical JSON form used for response token accounting."""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class ApproximateTokenCounter:
    """Deterministic fallback counter based on compact JSON character length."""

    name: str = "approx:json_chars_div_4"
    exact: bool = False

    def count(self, payload: Mapping[str, Any]) -> int:
        text = _payload_json(payload)
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class TiktokenTokenCounter:
    """Token counter for a named tiktoken encoding."""

    encodingName: str = VENDORED_ENCODING_NAME
    exact: bool = True
    _encoding: tiktoken.Encoding = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # This is where the vocabulary is read (once per process, tiktoken caches the
        # Encoding), so this is the call that has to find it on disk rather than online.
        with vendored_vocabulary_cache(self.encodingName):
            object.__setattr__(self, "_encoding", tiktoken.get_encoding(self.encodingName))

    @property
    def name(self) -> str:
        return f"tiktoken:{self.encodingName}"

    def count(self, payload: Mapping[str, Any]) -> int:
        return len(self._encoding.encode(_payload_json(payload)))


DEFAULT_TOKEN_COUNTER = TiktokenTokenCounter()


def count_response_tokens(
    payload: Mapping[str, Any],
    *,
    token_counter: ResponseTokenCounter = DEFAULT_TOKEN_COUNTER,
) -> int:
    """Return the token count for a JSON payload using the configured counter."""

    return token_counter.count(payload)


def _finalize_token_count(
    payload: dict[str, Any],
    *,
    token_counter: ResponseTokenCounter,
) -> int:
    """Set a stable token count that includes the token metadata fields."""

    previous = None
    while previous != payload["tokens"]:
        previous = payload["tokens"]
        payload["tokens"] = token_counter.count(payload)
    return payload["tokens"]


def finalize_payload_tokens(
    payload: dict[str, Any],
    *,
    token_counter: ResponseTokenCounter = DEFAULT_TOKEN_COUNTER,
) -> dict[str, Any]:
    """Stamp token accounting metadata onto an already-serialized response dict.

    Sets ``tokenizer`` and ``tokenCountExact`` from the counter, then resolves
    ``tokens`` to a stable count that includes the token metadata fields. Mutates
    and returns ``payload`` so callers that already hold a serialized dict (such
    as the MCP dispatch path) do not need a model instance.
    """

    payload["tokens"] = 0
    payload["tokenizer"] = token_counter.name
    payload["tokenCountExact"] = token_counter.exact
    _finalize_token_count(payload, token_counter=token_counter)
    return payload


def response_payload(
    response: ResponseModel,
    *,
    token_counter: ResponseTokenCounter = DEFAULT_TOKEN_COUNTER,
) -> dict[str, Any]:
    """Serialize a response model and add token accounting metadata.

    Accepts any ``ResponseModel`` (not only operation-bearing ``ToolResponse``
    envelopes) because the token fields live on the shared base and the dispatch
    path stamps tokens onto responses such as ``ping`` that carry no operation.
    """

    payload = response.model_dump(mode="json", exclude_none=True)
    return finalize_payload_tokens(payload, token_counter=token_counter)


def dump_with_token_count(
    response: ResponseModel,
    *,
    counter: ResponseTokenCounter = DEFAULT_TOKEN_COUNTER,
) -> dict[str, Any]:
    """Backward-compatible alias for response_payload."""

    return response_payload(response, token_counter=counter)
