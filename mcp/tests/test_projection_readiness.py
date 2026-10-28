"""A serving process may answer while its one real projection is being prepared."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.observer.projection import WorkspaceProjection
from agents_remember.serving import _app_routes as routes
from agents_remember.serving._app_common import _ServingRuntime
from agents_remember.serving.projector import Projector, ProjectorClosed
from fastapi import FastAPI
from fastapi.responses import JSONResponse


def projector(tmp_path: Path) -> Projector:
    return Projector(
        McpRuntimeConfig(
            config_path=tmp_path / "settings.json",
            coordination_root=tmp_path,
            workspace_root=tmp_path,
            transcript_root=tmp_path / "logs",
        )
    )


def snapshot() -> WorkspaceProjection:
    return WorkspaceProjection(generatedAt="2026-10-25T12:00:00Z")


def test_early_subscriber_and_waiters_receive_actual_prime_once(tmp_path):
    async def scenario():
        owner = projector(tmp_path)
        started, release = asyncio.Event(), asyncio.Event()
        value = snapshot()

        async def tick(*_args):
            started.set()
            await release.wait()
            return value

        stream = owner.subscribe()
        first = asyncio.create_task(anext(stream))
        waiters = [asyncio.create_task(owner.wait_ready()) for _ in range(3)]
        with patch.object(owner, "_tick", side_effect=tick) as produce:
            prime = asyncio.create_task(owner.prime())
            await started.wait()
            await asyncio.sleep(0)
            assert owner.current() == (0, None)
            assert not first.done() and all(not item.done() for item in waiters)
            waiters[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiters[0]
            release.set()
            await prime
            await asyncio.gather(*waiters[1:])
            seq, event = await first
            assert seq == 0 and event.event == "snapshot" and event.data is value
            assert produce.call_count == 1
            late = owner.subscribe()
            assert (await anext(late))[0] == 0
            await late.aclose()
            assert all(queue.empty() for queue in owner._subscribers)
        await stream.aclose()

    asyncio.run(scenario())


def test_failed_prime_waits_for_real_recovery_publication(tmp_path):
    async def scenario():
        owner = projector(tmp_path)
        stream = owner.subscribe()
        first = asyncio.create_task(anext(stream))
        waiter = asyncio.create_task(owner.wait_ready())
        with patch.object(owner, "_tick", new=AsyncMock(side_effect=OSError("read failed"))):
            await owner.prime()
        await asyncio.sleep(0)
        assert not waiter.done() and not first.done()
        value = snapshot()
        owner._publish_projection(value)
        await waiter
        seq, event = await first
        assert seq == 1 and event.event == "snapshot" and event.data is value
        await stream.aclose()

    asyncio.run(scenario())


def test_shutdown_releases_waiters_and_refuses_new_waits(tmp_path):
    async def scenario():
        owner = projector(tmp_path)
        waiters = [asyncio.create_task(owner.wait_ready()) for _ in range(2)]
        await asyncio.sleep(0)
        owner.close_readiness()
        for waiter in waiters:
            with pytest.raises(ProjectorClosed):
                await waiter
        with pytest.raises(ProjectorClosed):
            await owner.wait_ready()

    asyncio.run(scenario())


def test_state_and_document_routes_join_one_preparation(tmp_path):
    async def scenario():
        owner = projector(tmp_path)
        runtime = cast(
            _ServingRuntime, SimpleNamespace(projector=owner, explainer_application=None)
        )
        app = FastAPI()
        routes._register_projection_routes(app, runtime)
        routes._register_action_routes(app, runtime)
        with (
            patch.object(
                routes, "_action_response", return_value=JSONResponse({"action": "ready"})
            ) as action,
            patch.object(
                routes, "_state_response", return_value=JSONResponse({"state": "ready"})
            ) as state,
            patch.object(
                routes,
                "_task_document_response",
                return_value=JSONResponse({"document": "ready"}),
            ) as document,
            patch.object(owner, "_tick", new=AsyncMock(return_value=snapshot())) as produce,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                reads = [
                    asyncio.create_task(client.get(path))
                    for path in ("/api/state", "/api/task-document?path=task")
                ]
                reads.append(asyncio.create_task(client.post("/api/actions/start", json={})))
                # load-independent: parks the task on the real condition before the assertion; nothing reads the clock
                await asyncio.sleep(0.02)
                assert not state.called and not document.called and not action.called
                assert all(not item.done() for item in reads)
                await owner.prime()
                responses = await asyncio.gather(*reads)
                assert [response.status_code for response in responses] == [200, 200, 200]
                assert produce.call_count == 1
                assert state.call_count == document.call_count == action.call_count == 1
        owner.close_readiness()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/state")).status_code == 503

    asyncio.run(scenario())
