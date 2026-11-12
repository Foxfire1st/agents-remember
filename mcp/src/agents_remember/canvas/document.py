"""Deliver a binding to the admitted document, never to a navigated WindowProxy.

The host installs this bootstrap before generated scripts. Binding delivery is local to that
document, after its listeners exist. Leaving it announces revocation even when navigation occurs
while parsing, before the iframe's first load event. Navigation remains a browser capability.
"""

from __future__ import annotations

import json
import re

from agents_remember.canvas.kit import kit_protocol_helper_js
from agents_remember.canvas.sessions import CanvasSession

_DOCTYPE = re.compile(rb"\A(?:\xef\xbb\xbf)?(?:\s|<!--.*?-->)*<!doctype\s[^>]*>", re.I | re.S)


def bound_document(payload: bytes, session: CanvasSession) -> bytes:
    """The document a page receives: the admitted bytes plus the binding bootstrap.

    The bootstrap is installed before the page's own scripts, on every served document: the page
    runs inside the isolation the policy serves, so there is no document that runs without it.
    """
    binding = json.dumps(
        {
            "schema": "ar-canvas-binding/v1",
            "protocolVersion": 1,
            "session": session.session,
            "revision": session.revision,
            "comparison": session.comparison,
        },
        ensure_ascii=True,
    ).replace("<", "\\u003c")
    bootstrap = (
        "<script>(()=>{const binding=" + binding + ";"
        "const host=parent;const notify=host.postMessage.bind(host);"
        "const channel=new MessageChannel();"
        "channel.port1.onmessage=event=>channel.port1.postMessage(event.data);"
        "notify({schema:'ar-canvas-document/v1',session:binding.session,phase:'entered'},"
        "'*',[channel.port2]);"
        "addEventListener('pagehide',()=>notify({schema:'ar-canvas-document/v1',"
        "session:binding.session,phase:'departed'},'*'),{once:true});"
        + kit_protocol_helper_js()
        + "addEventListener('DOMContentLoaded',()=>dispatchEvent(new MessageEvent('message',"
        "{data:binding,source:host})),{once:true});"
        "})();</script>"
    ).encode("ascii")
    doctype = _DOCTYPE.match(payload)
    offset = doctype.end() if doctype else 0
    return payload[:offset] + bootstrap + payload[offset:]
