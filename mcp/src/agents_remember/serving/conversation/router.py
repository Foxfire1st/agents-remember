"""Stable root composition for the three independently owned conversation routers."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from agents_remember.serving.conversation.active.api import router as active_router
from agents_remember.serving.conversation.control.api import router as control_router
from agents_remember.serving.conversation.library.api import router as library_router
from agents_remember.serving.conversation.runtime import (
    ConversationRuntime,
    install_conversation_runtime,
)

CONVERSATION_CHILD_ROUTERS = (active_router, library_router, control_router)

router = APIRouter()
for child_router in CONVERSATION_CHILD_ROUTERS:
    router.include_router(child_router)


def register_conversation_routes(app: FastAPI, runtime: ConversationRuntime) -> None:
    """Install the one runtime authority, then mount the stable root exactly once.

    The runtime binding is the single composition seam repaired in L0: child
    leaves add behavior only inside their owned child modules and consume the
    runtime through the request dependencies; they never edit this composition,
    re-bind the runtime, or register again.
    """

    install_conversation_runtime(app, runtime)
    app.include_router(router)


__all__ = ["CONVERSATION_CHILD_ROUTERS", "register_conversation_routes", "router"]
