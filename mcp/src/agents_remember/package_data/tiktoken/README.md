# Vendored tiktoken vocabulary

This directory is a tiktoken cache directory, shipped inside the package. It holds one
file, the `o200k_base` vocabulary that `agents_remember.models.tokens` counts response
tokens with.

It is here so the server starts with no network egress. `mcp/tools/base.py` imports
`models/tokens.py`, which builds the default counter at module scope, so
`tiktoken.get_encoding("o200k_base")` runs while the server is still importing. Without a
warm cache that call downloads the vocabulary, and a fresh container, an offline machine
or a hermetic CI job cannot start the server at all.

## Why the file name is a hash

`tiktoken.load.read_file_cached` looks a download up under the SHA-1 of its source URL, so
the name below is the only one a cache hit can have:

    url    https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken
    name   sha1(url)   = fb374d419588a4632f3f557e76b4b70aebbca790
    bytes  sha256(file) = 446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d

The content hash is the one tiktoken itself asserts on load
(`tiktoken_ext/openai_public.py::o200k_base`), so the shipped file is byte-identical to
the download it replaces and token counts are unchanged.

`models/tokens.py` checks that hash itself, before pointing tiktoken at this directory,
and raises `TokenizerVocabularyError` on a mismatch. It has to: tiktoken checks the same
hash but does not fail closed on it. `read_file_cached` answers a mismatch by deleting the
cached file and downloading a replacement over it, which here means a startup download that
also rewrites the installed package -- or, on a read-only install, a `PermissionError` from
the write-back. So a copy that is present but wrong -- line endings rewritten by a
`core.autocrlf=true` checkout, a truncated or partial write, one flipped byte -- is refused
rather than silently repaired.

`mcp/tests/test_cold_start.py` holds all of that shut. It re-derives the URL's SHA-1 and the
expected SHA-256 from the installed tiktoken and fails if either stops matching the shipped
file, so a version bump that moves the URL is caught. It corrupts *copies* of this file in a
temporary directory -- CRLF-mangled, truncated to half its bytes, one byte flipped -- and
requires the refusal each time, never touching the file here. And it fails if the
`.gitattributes` entry stops naming the file that is actually shipped.

## Refreshing it

Only needed if that test reports a new URL. Fetch the URL tiktoken names, keep the SHA-1
naming, and verify the SHA-256 tiktoken expects. The new digest also replaces
`VENDORED_VOCABULARY_SHA256` in `models/tokens.py`; the test above re-derives it from
tiktoken, so the two cannot drift apart silently:

    python - <<'PY'
    import hashlib, pathlib, urllib.request
    url = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
    data = urllib.request.urlopen(url).read()
    here = pathlib.Path("mcp/src/agents_remember/package_data/tiktoken")
    (here / hashlib.sha1(url.encode()).hexdigest()).write_bytes(data)
    print(hashlib.sha256(data).hexdigest())
    PY

Then rename the `-text` entry in the repository's `.gitattributes` to match --
`test_the_gitattributes_entry_names_the_shipped_file` stays red until you do. That entry is
what stops a `core.autocrlf=true` checkout from rewriting the line endings of a file whose
bytes are its identity, which would leave that clone -- and only that clone -- unable to
start the server at all.

Unlike the cockpit bundle next door, this file is committed. It is third-party data
addressed by its own hash, written once and changed only when tiktoken changes what it
asks for -- not a build product regenerated on every release, so it carries none of the
churn that kept `package_data/dashboard/` out of version control.
