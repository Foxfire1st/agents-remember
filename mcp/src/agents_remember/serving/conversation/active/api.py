"""Behavior-empty active conversation child router owned by FEUI SC2."""

from fastapi import APIRouter

router = APIRouter(
    prefix="/api/terminal/{ar_session_id}/conversation",
    tags=["structured-conversation-active"],
)
