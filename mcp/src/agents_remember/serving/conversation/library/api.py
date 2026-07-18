"""Behavior-empty native conversation library child router owned by FEUI SC3."""

from fastapi import APIRouter

router = APIRouter(
    prefix="/api/harnesses/{harness_id}/conversations",
    tags=["structured-conversation-library"],
)
