"""The presenter's entire authoring tool authority, started only for its prepared folder.

No AR tool is registered. Inputs are immutable digests, output paths are descriptor anchored,
documentation is a separate read-only surface, and draft render runs a model-free browser.
"""

from __future__ import annotations

import argparse
import base64
import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit
from urllib.request import urlopen

from mcp.server.fastmcp import FastMCP, Image
from pydantic import Field

from agents_remember.canvas.kit import kit_assets
from agents_remember.canvas.presenter_assets import collect_assets
from agents_remember.canvas.presenter_evidence import source as read_source
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
    def __init__(self, working: Path):
        self.working = working
        self.host = working / "host"
        self.grants = json.loads(
            read_bounded(
                self.host / "grants.json",
                MAX_RECORD_BYTES,
                reason="grants-too-large",
                detail="input grant manifest",
            )
        )
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


def create_server(working: Path) -> FastMCP:
    tools = AuthoringTools(working)
    server = FastMCP("review-presenter")

    @server.tool()
    def inputs() -> dict:
        """List exact admitted input names and digests. There is no arbitrary filesystem reader."""
        return tools.grants["inputs"]

    @server.tool()
    def read_input(name: str) -> str:
        """Read evidence.json or assignment.txt, with its admitted digest checked."""
        return tools.read(name)

    @server.tool()
    def source(side: str, path: str, start: int = 1, end: int = 100) -> dict:
        """Read a relative path at one of the packet's four exact trees, through bounded R102 IO.

        No repository, HEAD or revision argument exists. Refusals and unavailable history are
        receipts, not permission to read today's checkout. Never writes or executes compared code.
        """
        packet = EvidencePacket.model_validate_json(tools.read("evidence.json"))
        return read_source(packet, side, path, start=start, end=end)

    @server.tool()
    def assets(relative: str | None = None, offset: int = 0) -> dict:
        """List/read this attempt's own assets; exact read chunks are bounded to 12000 characters."""
        return tools.assets(relative, offset)

    @server.tool()
    def write_asset(relative: str, text: Annotated[str, Field(max_length=1048576)]) -> dict:
        """Write UTF-8 presentation output relative to assets/, never an absolute/protected path."""
        body = text.encode()
        tools.write(relative, body)
        return {"path": relative, "sha256": digest_bytes(body), "bytes": len(body)}

    @server.tool()
    def carry_kit(paths: list[str]) -> dict:
        """Carry kit-relative files (e.g. shiki/4.5.0/shiki-kit.js) and their required notices."""
        return tools.carry(paths)

    @server.tool()
    def documentation(url: str, offset: int = 0) -> dict:
        """Fetch bounded read-only authoring documentation. This surface is NEVER review evidence."""
        return tools.documentation(url, offset)

    @server.tool()
    def render() -> list:
        """Load the exact draft in Chromium with reduced motion; return diagnostics and its picture."""
        report, picture = tools.render()
        result: list[Any] = [json.dumps(report)]
        if picture is not None:
            result.append(Image(data=picture, format="png"))
        return result

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working", type=Path, required=True)
    create_server(parser.parse_args().working).run()


if __name__ == "__main__":
    main()
