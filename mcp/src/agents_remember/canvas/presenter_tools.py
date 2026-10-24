"""The presenter's entire authoring tool authority, started only for its prepared folder.

No AR tool is registered. Inputs are immutable digests, output paths are descriptor anchored,
documentation is a separate read-only surface, and draft render runs a model-free browser.
"""

from __future__ import annotations

import base64
import json
import tempfile
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import urlopen

from agents_remember.canvas.kit import kit_assets
from agents_remember.canvas.presenter_assets import collect_assets
from agents_remember.canvas.presenter_files import (
    directory_fd,
    ensure_child,
    scan_files,
    write_output,
)
from agents_remember.canvas.presenter_store import MAX_RECORD_BYTES, digest_bytes, read_bounded
from agents_remember.canvas.prospective import BrowserProspectiveLoad
from agents_remember.models.knowledge.review_evidence import EvidencePacket
from agents_remember.models.knowledge.review_frame import MAX_DRAFT_BYTES, FrameLimits

DOCUMENTATION_HOSTS = frozenset(
    {
        "developer.mozilla.org",
        "shiki.style",
        "motioncanvas.io",
        "www.typescriptlang.org",
        "raw.githubusercontent.com",
        "github.com",
    }
)


class AuthoringTools:
    """The kept authoring tools, aimed at one working folder and one input folder.

    The presenter's attempt passes only its working folder: the grants lie in ``host/`` beneath
    it. The explainer's environment keeps its grants and packet in ``instructions/`` and its
    drafts in ``drafts/`` (MIK-R186 rule 2), so it hands both folders and the grants it read
    itself -- the same behaviour, one layout apart.
    """

    def __init__(
        self,
        working: Path,
        *,
        host: Path | None = None,
        grants: Mapping[str, Any] | None = None,
    ):
        self.working = working
        self.host = working / "host" if host is None else host
        manifest: Mapping[str, Any]
        if grants is None:
            loaded = json.loads(
                read_bounded(
                    self.host / "grants.json",
                    MAX_RECORD_BYTES,
                    reason="grants-too-large",
                    detail="input grant manifest",
                )
            )
            if not isinstance(loaded, Mapping):
                raise ValueError("the grant manifest is not an object of admitted input names")
            manifest = loaded
        else:
            manifest = grants
        self.grants: dict[str, Any] = dict(manifest)
        if self.grants["working"] != str(working):
            raise ValueError("the grant does not name this prepared folder")
        ensure_child(working, "assets")

    def read(self, name: str) -> str:
        expected = self.grants["inputs"].get(name)
        if expected is None:
            raise ValueError("foreign-scope-read: not an exact admitted input name")
        body = read_bounded(
            self.host / name, MAX_RECORD_BYTES, reason="input-too-large", detail=name
        )
        if digest_bytes(body) != expected:
            raise ValueError("input-changed: the input does not match its admitted digest")
        return body.decode()

    def write(self, relative: str, payload: bytes) -> None:
        if len(payload) > 1024 * 1024:
            raise ValueError("one authoring write exceeds 1 MiB")
        with directory_fd(self.working) as held:
            total = sum(
                size
                for _path, _parent, _name, size in scan_files(held, depth_limit=8, entry_limit=1024)
            )
        if total + len(payload) > MAX_DRAFT_BYTES:
            raise ValueError("the presentation working quota is exhausted")
        write_output(self.working / "assets", relative, payload)

    def carry(self, paths: list[str]) -> dict:
        if len(paths) > 64:
            raise ValueError("too many kit requests")
        carried = kit_assets(paths)
        for path, body in carried.items():
            self.write(path, body)
        return {"files": list(carried), "bytes": sum(map(len, carried.values()))}

    def assets(self, relative: str | None = None, offset: int = 0) -> dict:
        """List/read this attempt's own retained or authored assets; never a review-evidence read.

        Reads are limited by the artifact/working quotas and return at most 12000 characters.
        Binary chunks are base64; the digest names the whole actual asset, not just the chunk.
        """
        if not 0 <= offset <= MAX_DRAFT_BYTES:
            raise ValueError("asset offset is outside its read bound")
        carried = collect_assets(self.working, FrameLimits())
        if relative is None:
            return {
                path: {"bytes": len(body), "sha256": digest_bytes(body)}
                for path, body in carried.items()
            }
        if relative not in carried:
            raise ValueError("foreign-output-read: not an asset of this exact attempt")
        body = carried[relative]
        try:
            content, encoding = body.decode(), "utf-8"
        except UnicodeDecodeError:
            content, encoding = base64.b64encode(body).decode(), "base64"
        return {
            "path": relative,
            "sha256": digest_bytes(body),
            "bytes": len(body),
            "encoding": encoding,
            "offset": offset,
            "content": content[offset : offset + 12000],
            "nextOffset": offset + 12000 if offset + 12000 < len(content) else None,
            "reviewEvidence": False,
        }

    def render(self):
        packet = EvidencePacket.model_validate_json(self.read("evidence.json"))
        assets = collect_assets(self.working, FrameLimits())
        with tempfile.TemporaryDirectory(prefix="ar-presenter-picture-") as temporary:
            picture = Path(temporary) / "draft.png"
            result = BrowserProspectiveLoad(screenshot=picture).load(
                assets=assets, entrypoint="index.html", packet=packet
            )
            body = None
            if picture.is_file():
                body = read_bounded(
                    picture, 2 * 1024 * 1024, reason="picture-too-large", detail="draft picture"
                )
                write_output(self.working, "host/draft.png", body)
        report = {
            **asdict(result),
            "assetDigests": {path: digest_bytes(body) for path, body in assets.items()},
            "viewport": [1280, 720],
        }
        return report, body

    @staticmethod
    def documentation(url: str, offset: int = 0) -> dict:
        if not 0 <= offset <= 256 * 1024:
            raise ValueError("documentation offset is outside its read bound")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in DOCUMENTATION_HOSTS
            or parsed.username
        ):
            raise ValueError("the address is not an admitted documentation host")
        with urlopen(url, timeout=20) as response:
            if urlsplit(response.url).hostname not in DOCUMENTATION_HOSTS:
                raise ValueError("documentation redirected outside admitted hosts")
            body = response.read(256 * 1024 + 1)
        if len(body) > 256 * 1024:
            raise ValueError("documentation exceeds its read bound")
        text = body.decode(errors="replace")
        return {
            "offset": offset,
            "totalCharacters": len(text),
            "nextOffset": offset + 12000 if offset + 12000 < len(text) else None,
            "surface": "documentation",
            "reviewEvidence": False,
            "url": url,
            "text": text[offset : offset + 12000],
        }


# The standalone `review-presenter` server that used to be built here is gone (MIK-R186 @ v2 rule 3):
# the normal AR task server serves an explainer seat, and `AuthoringTools` above is the implementation
# it registers under `_register_authoring`. Nothing else remains to launch.
