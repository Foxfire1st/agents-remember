"""Serialized Pi model/thinking mutation with correlated state readback."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

from agents_remember.errors import (
    HarnessAdapterBusyError,
    HarnessAdapterDisconnectedError,
    HarnessControlError,
)
from agents_remember.serving.harness_capabilities import CapabilitySnapshot, SetResult
from agents_remember.serving.pi_rpc_process import PiRpcTransport, WriteGuard
from agents_remember.serving.pi_rpc_protocol import (
    PiSessionState,
    parse_pi_response,
    pi_response_error,
)

TransportGetter = Callable[[], PiRpcTransport]
StateReader = Callable[[], Awaitable[PiSessionState]]
CapabilitiesReader = Callable[[PiSessionState], Awaitable[CapabilitySnapshot]]
CapabilitiesGetter = Callable[[], CapabilitySnapshot]
StateCommitter = Callable[[PiSessionState, CapabilitySnapshot], None]
RequestIdFactory = Callable[[str], str]

DEFAULT_PI_MUTATION_TIMEOUT_SECONDS = 5.0


class PiRpcConfiguration:
    """Make one mutation response and its get_state evidence an atomic set transaction."""

    def __init__(
        self,
        *,
        transport: TransportGetter,
        read_state: StateReader,
        read_capabilities: CapabilitiesReader,
        capabilities: CapabilitiesGetter,
        commit: StateCommitter,
        request_id: RequestIdFactory,
        timeout_seconds: float = DEFAULT_PI_MUTATION_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise HarnessControlError("Pi configuration timeout must be positive")
        self._transport = transport
        self._read_state = read_state
        self._read_capabilities = read_capabilities
        self._capabilities = capabilities
        self._commit = commit
        self._request_id = request_id
        self._timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    async def set_model(
        self, model_key: str, *, before_write: WriteGuard | None = None
    ) -> SetResult:
        identity = _provider_model(model_key)
        if identity is None:
            return _unsupported(
                model_key,
                "Pi model must use the exact provider/model-id identity from the dynamic catalog",
            )
        provider, model_id = identity
        async with self._lock:
            transaction = await self._transaction(
                requested=model_key,
                purpose="model",
                command="set_model",
                values={"provider": provider, "modelId": model_id},
                before_write=before_write,
            )
        if isinstance(transaction, SetResult):
            return transaction
        state, capabilities = transaction
        if state.model_key != model_key:
            return _unknown(
                model_key,
                f"Pi acknowledged set_model but get_state reported {state.model_key!r}",
            )
        self._commit(state, capabilities)
        return SetResult(
            ok=True,
            acceptance="echo-verified",
            requested_value=model_key,
            effective_value=model_key,
            detail="Pi correlated set_model success with exact get_state model readback",
        )

    async def set_effort(
        self, effort: str, *, before_write: WriteGuard | None = None
    ) -> SetResult:
        capabilities = self._capabilities()
        selected = next(
            (
                model
                for model in capabilities.models
                if model.key == capabilities.selected_model_key
            ),
            None,
        )
        vocabulary = (
            {option.key for option in selected.effort_options if option.session_settable}
            if selected is not None
            else set()
        )
        if effort not in vocabulary:
            return _unsupported(
                effort,
                "Pi thinking level is absent from the selected model's dynamic vocabulary",
            )
        selected_model = capabilities.selected_model_key
        async with self._lock:
            transaction = await self._transaction(
                requested=effort,
                purpose="thinking",
                command="set_thinking_level",
                values={"level": effort},
                before_write=before_write,
            )
        if isinstance(transaction, SetResult):
            return transaction
        state, refreshed = transaction
        if state.model_key != selected_model:
            return _unknown(
                effort,
                "Pi model changed while reading back the thinking-level mutation",
            )
        self._commit(state, refreshed)
        effective = state.thinking_level
        detail = "Pi correlated set_thinking_level success with exact get_state readback"
        if effective != effort:
            detail = f"Pi clamped requested thinking level {effort!r} to {effective!r}"
        return SetResult(
            ok=True,
            acceptance="echo-verified",
            requested_value=effort,
            effective_value=effective,
            detail=detail,
        )

    async def _transaction(
        self,
        *,
        requested: str,
        purpose: str,
        command: str,
        values: Mapping[str, object],
        before_write: WriteGuard | None,
    ) -> tuple[PiSessionState, CapabilitySnapshot] | SetResult:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                request_id = self._request_id(command)
                frame = await self._transport().request(
                    {"id": request_id, "type": command, **dict(values)},
                    before_write=before_write,
                )
                response = parse_pi_response(frame, request_id=request_id, command=command)
                if response["success"] is False:
                    return _unsupported(requested, pi_response_error(response))
                state = await self._read_state()
                capabilities = await self._read_capabilities(state)
                return state, capabilities
        except TimeoutError:
            return _unknown(
                requested,
                f"Pi {purpose} mutation/readback exceeded the bounded timeout",
            )
        except HarnessAdapterDisconnectedError:
            return _unknown(
                requested,
                f"Pi {purpose} mutation lost its correlated response or readback",
            )
        except HarnessAdapterBusyError:
            raise
        except HarnessControlError as exc:
            return _unknown(
                requested,
                f"Pi {purpose} mutation produced incoherent catalog evidence: {exc}",
            )


def _provider_model(model_key: str) -> tuple[str, str] | None:
    if "/" not in model_key:
        return None
    provider, model_id = model_key.split("/", 1)
    if not provider or not model_id or provider != provider.strip() or model_id != model_id.strip():
        return None
    return provider, model_id


def _unsupported(requested: str, detail: str) -> SetResult:
    return SetResult(
        ok=False,
        acceptance="unsupported",
        requested_value=requested,
        detail=detail,
    )


def _unknown(requested: str, detail: str) -> SetResult:
    return SetResult(
        ok=False,
        acceptance="unknown",
        requested_value=requested,
        detail=detail,
    )
