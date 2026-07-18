"""Stable root composition for the three independently owned conversation routers."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from agents_remember.serving.conversation.active.api import router as active_router
from agents_remember.serving.conversation.control.api import router as control_router
from agents_remember.serving.conversation.library.api import router as library_router

CONVERSATION_CHILD_ROUTERS = (active_router, library_router, control_router)

router = APIRouter()
for child_router in CONVERSATION_CHILD_ROUTERS:
    router.include_router(child_router)


def register_conversation_routes(app: FastAPI) -> None:
    """Mount the stable root once; child leaves add only to their owned router module."""

    app.include_router(router)


__all__ = ["CONVERSATION_CHILD_ROUTERS", "register_conversation_routes", "router"]
