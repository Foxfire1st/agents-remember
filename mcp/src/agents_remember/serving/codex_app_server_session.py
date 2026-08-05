"""Connection, capability, and thread-open ownership for Codex app-server."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

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
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    LaunchKnobs,
    ModelCapability,
)
from agents_remember.serving.harness_control_models import AdapterSnapshot, LaunchSpec

TransportFactory = Callable[[], CodexAppServerTransport]


def codex_launch_knobs(*, model_key: str, effort: str | None) -> LaunchKnobs:
    """Carry native Codex launch state through thread/start settings, never CODEX_CONFIG.

    Reads nothing but its arguments: the selection is spelled into the session settings before
    any adapter or transport exists, which is why it is a launch fact rather than an adapter
    method. The result feeds ``CodexAppServerSettings.config``.
    """

    if not model_key or model_key != model_key.strip():
        raise CodexAppServerError("Codex launch model must be non-empty with no outer whitespace")
    if effort is None or not effort or effort != effort.strip():
        raise CodexAppServerError("Codex launch effort must be non-empty with no outer whitespace")
    return LaunchKnobs(
        session_config={
            "model": model_key,
            "model_reasoning_effort": effort,
        },
        owned_argv_options=("--model", "-m"),
        owned_config_keys=("model", "model_reasoning_effort"),
    )


@dataclass(frozen=True)
class CodexAppServerSettings:
    """Settings-owned desired state sent through app-server, never an effort argv mapping."""

    reasoning_effort: str | None = None
    model: str | None = None
    resume_thread_id: str | None = None
    approval_policy: object | None = None
    approvals_reviewer: str | None = None
    sandbox: str | None = None
    turn_sandbox_policy: Mapping[str, object] | None = None
    config: Mapping[str, object] = field(default_factory=dict)
    submission_limit: int = 256
    model_page_limit: int = 32
    ephemeral: bool = False
    client_name: str = "agents_remember"
    client_title: str = "Agents Remember"
    client_version: str = "3.0.0"

    def __post_init__(self) -> None:
        for name, value in (
            ("client_name", self.client_name),
            ("client_title", self.client_title),
            ("client_version", self.client_version),
        ):
            if not value or value != value.strip():
                raise CodexAppServerError(
                    f"Codex {name} must be non-empty with no outer whitespace"
                )
        if self.reasoning_effort is not None and (
            not self.reasoning_effort or self.reasoning_effort != self.reasoning_effort.strip()
        ):
            raise CodexAppServerError(
                "Codex reasoning_effort must be non-empty with no outer whitespace"
            )
        if self.model is not None and (not self.model or self.model != self.model.strip()):
            raise CodexAppServerError("Codex model must be non-empty with no outer whitespace")
        for name, value in (
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
        self.desired_model: CodexModelCapability | None = None
        self.models: tuple[CodexModelCapability, ...] = ()
        self.desired_effort: str | None = None
        self.effective_effort: str | None = None
        self._settings_overridden = False

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
            cli_version, initialize_evidence = await self._initialize(transport)
            models = await self._read_models(transport)
            requested_model = (
                self.desired_model.model
                if self._settings_overridden and self.desired_model is not None
                else self.settings.model
            )
            selected = select_model(models, requested_model)
            desired_effort = (
                self.desired_effort
                if self._settings_overridden and self.desired_effort is not None
                else self.settings.reasoning_effort or selected.default_effort
            )
            validate_reasoning_effort(selected, desired_effort)
            self.desired_model = selected
            self.desired_effort = desired_effort
            method = "thread/resume" if resume_thread_id else "thread/start"
            response = await transport.request(
                method,
                self._thread_params(
                    resume_thread_id=resume_thread_id,
                    selected=selected,
                    desired_effort=desired_effort,
                ),
            )
            thread = parse_thread_open_response(
                response,
                method=method,
                desired_effort=desired_effort,
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
            self.desired_model = selected
            self.models = models
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

    async def discover(self, launch: LaunchSpec) -> CapabilitySnapshot:
        """Enumerate through initialize/model-list only; do not create a Codex thread."""

        transport = self._transport_factory()
        await transport.start(launch)
        try:
            await self._initialize(transport)
            models = await self._read_models(transport)
            return self._normalized_capabilities(models, selected=None, effort=None)
        finally:
            await transport.stop("forced")

    def set_desired_model(self, model_key: str) -> str | None:
        target = next((model for model in self.models if model.model == model_key), None)
        if target is None:
            advertised = ", ".join(model.model for model in self.models)
            raise CodexAppServerError(
                f"Codex model {model_key!r} is not dynamically advertised; options: {advertised}"
            )
        current = self.require_desired_model()
        if target.model == current.model:
            return None
        self.desired_model = target
        self._settings_overridden = True
        desired_effort = self.require_desired_effort()
        if desired_effort in target.supported_efforts:
            return None
        self.desired_effort = target.default_effort
        return (
            f"reasoning effort {desired_effort!r} is unavailable for {target.model!r}; "
            f"rebased to its dynamic default {target.default_effort!r}"
        )

    def set_desired_effort(self, effort: str) -> None:
        model = self.require_desired_model()
        validate_reasoning_effort(model, effort)
        if effort == self.desired_effort:
            return
        self.desired_effort = effort
        self._settings_overridden = True

    @property
    def has_pending_settings(self) -> bool:
        desired = self.desired_model
        effective = self.model
        return bool(
            desired is not None
            and effective is not None
            and (desired.model != effective.model or self.desired_effort != self.effective_effort)
        )

    def accept_settings_selection(
        self,
        *,
        model: CodexModelCapability,
        effort: str,
    ) -> None:
        """Promote only the selection carried by one accepted turn submission."""

        catalog_model = next(
            (candidate for candidate in self.models if candidate.model == model.model),
            None,
        )
        if catalog_model is None or catalog_model != model:
            raise CodexAppServerError(
                "Codex accepted turn selection is absent from the negotiated model catalog"
            )
        validate_reasoning_effort(catalog_model, effort)
        self.model = catalog_model
        self.effective_effort = effort

    def accept_settings_update(self, *, model: str, effort: str) -> bool:
        desired = self.require_desired_model()
        desired_effort = self.require_desired_effort()
        if model == desired.model and effort == desired_effort:
            changed = self.has_pending_settings
            self.accept_settings_selection(model=desired, effort=desired_effort)
            return changed
        effective = self.model
        if (
            self.has_pending_settings
            and effective is not None
            and model == effective.model
            and effort == self.effective_effort
        ):
            return False
        raise CodexAppServerError(
            "Codex thread/settings/updated changed model or reasoning effort outside the "
            "deliberate adapter setter"
        )

    def capability_snapshot(self) -> JsonObject:
        model = self.model
        protocol = CODEX_APP_SERVER_PROTOCOL
        if self.cli_version is not None:
            protocol = f"{protocol}/{self.cli_version}"
        return {
            "protocol": protocol,
            "codexCliVersion": self.cli_version,
            "experimentalApi": True,
            "model": model.model if model else None,
            "desiredModel": self.desired_model.model if self.desired_model else None,
            "advertisedReasoningEfforts": list(model.supported_efforts) if model else [],
            "defaultReasoningEffort": model.default_effort if model else None,
            "desiredReasoningEffort": self.desired_effort,
            "effectiveReasoningEffort": self.effective_effort,
            "settingsPending": self.has_pending_settings,
        }

    def advertise(self) -> CapabilitySnapshot:
        model = self.model
        if model is None or not self.models:
            raise CodexAppServerError("Codex model catalog is not available")
        return self._normalized_capabilities(
            self.models,
            selected=model,
            effort=self.effective_effort,
        )

    def _normalized_capabilities(
        self,
        models: tuple[CodexModelCapability, ...],
        *,
        selected: CodexModelCapability | None,
        effort: str | None,
    ) -> CapabilitySnapshot:
        return CapabilitySnapshot(
            models=tuple(
                ModelCapability(
                    key=item.model,
                    display_name=item.display_name,
                    resolved_model=item.model,
                    description=item.description,
                    supports_effort=bool(item.effort_options),
                    effort_options=item.effort_options,
                    default_effort=item.default_effort,
                    is_default=item.is_default,
                    hidden=item.hidden,
                )
                for item in models
            ),
            selected_model_key=selected.model if selected is not None else None,
            selected_effort=effort,
        )

    async def _initialize(
        self,
        transport: CodexAppServerTransport,
    ) -> tuple[str, JsonObject]:
        initialize = await transport.request(
            "initialize",
            {
                "clientInfo": {
                    "name": self.settings.client_name,
                    "title": self.settings.client_title,
                    "version": self.settings.client_version,
                },
                # History capability is contract-probed after initialization. Opting in makes
                # bounded history RPCs callable where present; no version string is authority.
                "capabilities": {"experimentalApi": True},
            },
        )
        cli_version, evidence = validate_initialize_response(
            initialize,
            client_name=self.settings.client_name,
        )
        await transport.notify("initialized", {})
        return cli_version, evidence

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
        desired_effort: str,
    ) -> JsonObject:
        assert self.launch is not None
        config = dict(self.settings.config)
        configured_model = config.get("model")
        if (
            not self._settings_overridden
            and configured_model is not None
            and configured_model != selected.model
        ):
            raise CodexAppServerError(
                "Codex thread config model conflicts with the dynamically selected model"
            )
        configured_effort = config.get("model_reasoning_effort")
        if (
            not self._settings_overridden
            and configured_effort is not None
            and configured_effort != desired_effort
        ):
            raise CodexAppServerError(
                "Codex thread config model_reasoning_effort conflicts with the settings desired effort"
            )
        config["model"] = selected.model
        config["model_reasoning_effort"] = desired_effort
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

    def require_desired_effort(self) -> str:
        if self.desired_effort is None:
            raise CodexAppServerError("Codex session has not resolved a reasoning effort")
        return self.desired_effort

    def require_desired_model(self) -> CodexModelCapability:
        if self.desired_model is None:
            raise CodexAppServerError("Codex session has not resolved a desired model")
        return self.desired_model
