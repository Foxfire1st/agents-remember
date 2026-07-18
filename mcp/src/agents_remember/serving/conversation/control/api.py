"""Behavior-empty conversation control child router owned by FEUI SC4."""

from fastapi import APIRouter

router = APIRouter(
    prefix="/api/terminal/{ar_session_id}",
    tags=["structured-conversation-control"],
)
