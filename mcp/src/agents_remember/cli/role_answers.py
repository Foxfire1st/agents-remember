"""Expose the canonical role and one notice for an earlier-ID API request."""

from __future__ import annotations

import json

from fastapi.responses import JSONResponse

from agents_remember.models.role_identity import ROLE_ALIAS_NOTICE
from agents_remember.models.role_launcher import RoleSelection


def alias_response(request: RoleSelection, response: JSONResponse) -> JSONResponse:
    if not request.used_role_alias:
        return response
    payload = json.loads(bytes(response.body))
    payload["role"] = request.role
    prior = payload.get("warning")
    payload["warning"] = f"{prior} {ROLE_ALIAS_NOTICE}" if prior else ROLE_ALIAS_NOTICE
    return JSONResponse(payload, status_code=response.status_code)
