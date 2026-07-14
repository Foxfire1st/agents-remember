"""Connection, capability, and thread-open ownership for Codex app-server."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agents_remember.errors import CodexAppServerError
from agents_remember.serving.codex_app_server_protocol import (
    CODEX_APP_SERVER_PROTOCOL,
    CodexAppServerTransport,
    CodexStdioTransport,
    JsonObject,
)
from agents_remember.serving.codex_app_server_state import (
    CodexModelCapability,
    activity_from_thread_status,
    parse_model_page,
    parse_thread_open_response,
    select_model,
    validate_initialize_response,
    validate_reasoning_effort,
)
from agents_remember.serving.harness_control_models import AdapterSnapshot, LaunchSpec

BusyPolicy = Literal["steer", "queue"]
TransportFactory = Callable[[], CodexAppServerTransport]


@dataclass(frozen=True)
class CodexAppServerSettings:
    """Settings-owned desired state sent through app-server, never an effort argv mapping."""

    reasoning_effort: str
    model: str | None = None
    resume_thread_id: str | None = None
    approval_policy: object | None = None
    approvals_reviewer: str | None = None
    sandbox: str | None = None
    turn_sandbox_policy: Mapping[str, object] | None = None
    config: Mapping[str, object] = field(default_factory=dict)
    busy_policy: BusyPolicy = "steer"
    busy_queue_limit: int = 64
    submission_limit: int = 256
    model_page_limit: int = 32
    ephemeral: bool = False
    client_name: str = "agents_remember"
    client_title: str = "Agents Remember"
    client_version: str = "3.0.0"

    def __post_init__(self) -> None:
        for name, value in (
            ("reasoning_effort", self.reasoning_effort),
            ("client_name", self.client_name),
            ("client_title", self.client_title),
            ("client_version", self.client_version),
        ):
            if not value or value != value.strip():
                raise CodexAppServerError(
                    f"Codex {name} must be non-empty with no outer whitespace"
                )
        if self.model is not None and (not self.model or self.model != self.model.strip()):
            raise CodexAppServerError("Codex model must be non-empty with no outer whitespace")
        if self.busy_policy not in {"steer", "queue"}:
            raise CodexAppServerError("Codex busy policy must be 'steer' or 'queue'")
        for name, value in (
            ("busy_queue_limit", self.busy_queue_limit),
            ("submission_limit", self.submission_limit),
            ("model_page_limit", self.model_page_limit),
        ):
            if value < 1:
                raise CodexAppServerError(f"Codex {name} must be positive")


class CodexAppServerSession:
    """One transport plus the exact selected model/thread/effective-effort evidence."""

    def __init__(
        self,
        settings: CodexAppServerSettings,
        *,
        transport_factory: TransportFactory = CodexStdioTransport,
    ) -> None:
        self.settings = settings
        self._transport_factory = transport_factory
        self.transport: CodexAppServerTransport | None = None
        self.launch: LaunchSpec | None = None
        self.thread_id: str | None = None
        self.cli_version: str | None = None
        self.model: CodexModelCapability | None = None
        self.effective_effort: str | None = None

    async def connect(
        self,
        launch: LaunchSpec,
        *,
        resume_thread_id: str | None,
    ) -> AdapterSnapshot:
        transport = self._transport_factory()
        self.transport = transport
        self.launch = launch
        connected = False
        try:
            await transport.start(launch)
            initialize = await transport.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": self.settings.client_name,
                        "title": self.settings.client_title,
                        "version": self.settings.client_version,
                    },
                    "capabilities": {"experimentalApi": False},
                },
            )
            cli_version, initialize_evidence = validate_initialize_response(
                initialize,
                client_name=self.settings.client_name,
            )
            await transport.notify("initialized", {})
            models = await self._read_models(transport)
            selected = select_model(models, self.settings.model)
            validate_reasoning_effort(selected, self.settings.reasoning_effort)
            method = "thread/resume" if resume_thread_id else "thread/start"
            response = await transport.request(
                method,
                self._thread_params(resume_thread_id=resume_thread_id, selected=selected),
            )
            thread = parse_thread_open_response(
                response,
                method=method,
                desired_effort=self.settings.reasoning_effort,
            )
            if resume_thread_id is not None and thread.thread_id != resume_thread_id:
                raise CodexAppServerError("thread/resume returned a different Codex thread id")
            if thread.model != selected.model:
                raise CodexAppServerError(
                    f"Codex {method} echoed model {thread.model!r}; "
                    f"selected model was {selected.model!r}"
                )
            if thread.cli_version != cli_version:
                raise CodexAppServerError(
                    f"Codex {method} cliVersion {thread.cli_version!r} differs from negotiated "
                    f"initialize version {cli_version!r}"
                )
            if Path(thread.cwd) != launch.cwd:
                raise CodexAppServerError(
                    f"Codex {method} echoed cwd {thread.cwd!r}; "
                    f"requested cwd was {str(launch.cwd)!r}"
                )
            self.thread_id = thread.thread_id
            self.cli_version = cli_version
            self.model = selected
            self.effective_effort = thread.effective_effort
            activity, acceptance = activity_from_thread_status(thread.status)
            snapshot = AdapterSnapshot(
                identity=launch.identity,
                control="ready",
                activity=activity,
                acceptance=acceptance,
                vendor_session_id=thread.thread_id,
                raw={
                    **self.capability_snapshot(),
                    "initialize": initialize_evidence,
                    "threadOpenMethod": method,
                    "threadCliVersion": thread.cli_version,
                    "modelProvider": thread.model_provider,
                },
            )
            connected = True
            return snapshot
        finally:
            if not connected:
                await transport.stop("forced")

    async def stop(self) -> None:
        if self.transport is not None:
            await self.transport.stop("forced")

    def record_effective_effort(self, effort: str) -> None:
        self.effective_effort = effort

    def capability_snapshot(self) -> JsonObject:
        model = self.model
        protocol = CODEX_APP_SERVER_PROTOCOL
        if self.cli_version is not None:
            protocol = f"{protocol}/{self.cli_version}"
        return {
            "protocol": protocol,
            "codexCliVersion": self.cli_version,
            "experimentalApi": False,
            "model": model.model if model else None,
            "advertisedReasoningEfforts": list(model.supported_efforts) if model else [],
            "defaultReasoningEffort": model.default_effort if model else None,
            "desiredReasoningEffort": self.settings.reasoning_effort,
            "effectiveReasoningEffort": self.effective_effort,
            "busyPolicy": self.settings.busy_policy,
        }

    async def _read_models(
        self,
        transport: CodexAppServerTransport,
    ) -> tuple[CodexModelCapability, ...]:
        models: list[CodexModelCapability] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(self.settings.model_page_limit):
            params: JsonObject = {"includeHidden": True}
            if cursor is not None:
                params["cursor"] = cursor
            page, cursor = parse_model_page(await transport.request("model/list", params))
            models.extend(page)
            if cursor is None:
                return tuple(models)
            if cursor in seen_cursors:
                raise CodexAppServerError("Codex model/list repeated a pagination cursor")
            seen_cursors.add(cursor)
        raise CodexAppServerError("Codex model/list exceeded the pagination limit")

    def _thread_params(
        self,
        *,
        resume_thread_id: str | None,
        selected: CodexModelCapability,
    ) -> JsonObject:
        assert self.launch is not None
        config = dict(self.settings.config)
        configured_effort = config.get("model_reasoning_effort")
        if configured_effort is not None and configured_effort != self.settings.reasoning_effort:
            raise CodexAppServerError(
                "Codex thread config model_reasoning_effort conflicts with the settings desired effort"
            )
        config["model_reasoning_effort"] = self.settings.reasoning_effort
        params: JsonObject = {
            "model": selected.model,
            "cwd": str(self.launch.cwd),
            "config": config,
        }
        if resume_thread_id is None:
            params["ephemeral"] = self.settings.ephemeral
        else:
            params["threadId"] = resume_thread_id
        for key, value in (
            ("approvalPolicy", self.settings.approval_policy),
            ("approvalsReviewer", self.settings.approvals_reviewer),
            ("sandbox", self.settings.sandbox),
        ):
            if value is not None:
                params[key] = value
        return params
