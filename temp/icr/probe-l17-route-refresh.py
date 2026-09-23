"""ICR-R17 base-defect probe: does a refresh's previous binding identity reach the port?

Runs the REAL route registration (`serving.review.register_review_routes`) over a capturing stub port
and asks it for a review WITH `previousBindingDigest`. The stub records the request the transport
forwarded, so the probe reports exactly what the application layer would receive.

Usage: PYTHONPATH=<tree>/mcp/src python probe-l17-route-refresh.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import KnowledgeReviewResult, ReviewSurfaceRequest
from agents_remember.serving.review import register_review_routes

PREVIOUS = "9" * 64
seen: list[ReviewSurfaceRequest] = []


def port(request: ReviewSurfaceRequest) -> KnowledgeReviewResult:
    seen.append(request)
    return KnowledgeReviewResult(
        state="refused",
        repository_id=request.repository_id,
        refusal={
            "code": "subject_unresolved",
            "detail": "probe: the port answered without composing a review",
            "next_action": "probe only",
        },
    )


def main() -> int:
    app = FastAPI()
    config = McpRuntimeConfig(
        workspace_root=Path(tempfile.mkdtemp(prefix="l17-probe-")),
        coordination_root=Path("/nonexistent-coordination"),
        config_path=Path("/nonexistent-config.json"),
        transcript_root=Path("/nonexistent-transcripts"),
    )
    register_review_routes(app, config, port)
    params = {
        "repo": "agents-remember",
        "master": "260921_complete-code-and-intent-review",
        "leaf": "260921-ICR-L17",
        "selectorKind": "invariant",
        "selectorId": "11111111-1111-1111-1111-111111111111",
        "previousBindingDigest": PREVIOUS,
    }
    with TestClient(app) as client:
        response = client.get("/api/review/intent", params=params)
    forwarded = getattr(seen[-1], "previous_binding_digest", "<field absent from the request model>")
    print(f"status: {response.status_code}")
    print(f"forwarded previous_binding_digest: {forwarded!r}")
    print(f"reaches the port: {forwarded == PREVIOUS}")
    malformed = dict(params, previousBindingDigest="not-a-digest")
    with TestClient(app) as client:
        bad = client.get("/api/review/intent", params=malformed)
    print(f"malformed digest status: {bad.status_code}")
    print(f"malformed body offendingInput: {bad.json().get('offendingInput')!r}")
    print("body:", json.dumps(bad.json(), sort_keys=True)[:240])
    return 0


if __name__ == "__main__":
    sys.exit(main())
