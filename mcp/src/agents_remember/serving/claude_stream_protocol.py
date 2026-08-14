"""Strict Claude Code stream-json framing and capability parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    InteractionQuestion,
    InteractionQuestionOption,
    PendingInteraction,
)
from agents_remember.serving.harness_control_models import (
    TerminalOutcome,
)

CLAUDE_STREAM_PROTOCOL = "claude-stream-json"
MAX_TRANSCRIPT_TEXT_CHARS = 128 * 1024
_DISCOVERY_MCP_CONFIG = '{"mcpServers":{}}'
_IDENTITY_CHANGING_COMMANDS = frozenset(
    {"clear", "continue", "exit", "fork", "login", "logout", "quit", "resume"}
)
_NATIVE_CAPABILITY_COMMANDS = frozenset({"effort", "model"})
# Claude's native cancel enums on a result frame's ``terminal_reason``: an error frame carrying
# one of these is an unambiguous user cut on its own, with no accepted-interrupt correlation.
_NATIVE_CANCEL_TERMINAL_REASONS = frozenset({"cancelled", "interrupted", "user_cancelled"})
# The abort shape Claude emits when a native interrupt tears the stream down (probe-locked on the
# claude 2.1.217 interrupt fixture: ``error_during_execution`` / ``is_error`` with this reason).
# It is NOT a standalone cancel reason — an unprovoked ``aborted_streaming`` stays ``failed`` — but
# it is the frame-shape discriminator that, conjoined with an accepted interrupt, proves a clean cut.
_ABORT_TERMINAL_REASON = "aborted_streaming"


@dataclass(frozen=True)
class ClaudeControlInitialization:
    commands: frozenset[str]
    pending_requests: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class ClaudeSystemInitialization:
    session_id: str
    version: str
    cwd: str
    model: str
    permission_mode: str
    commands: frozenset[str]


# ``--forward-subagent-text``:
# sub-agent text/thinking blocks cross the live stream instead of tool_use/
# tool_result blocks only. Smoke-verified on the installed claude 2.1.220
# (``claude --help`` lists it; the 2026-07-26 live probes streamed sidechain
# text blocks with it).
FORWARD_SUBAGENT_TEXT_FLAG = "--forward-subagent-text"
# The capability floor: 2.1.220 is the only installed version
# the flag was ever probed against, so it is the floor. NO version seam exists at argv-build
# time — the installed version is captured only by ``system/init`` AFTER launch — so emission
# stays behind that capture: the adapter launches WITHOUT the flag (fail-closed default) and
# re-launches WITH it only when the captured version meets this floor; below it the flag is
# omitted and the capability is marked unverified with the exact reason. A version gate is
# justified HERE and nowhere else: the contract is argv-only, so it cannot be probed without
# launching the flag itself.
FORWARD_SUBAGENT_TEXT_FLOOR = (2, 1, 220)


def claude_version_tuple(version: str) -> tuple[int, ...] | None:
    """Parse ``major.minor.patch``; anything else is unproven (None), never guessed."""

    parts = version.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def forward_subagent_text_supported(version: str | None) -> bool:
    """The fail-closed floor verdict: unproven/unparseable versions never get the flag."""

    if version is None:
        return False
    parsed = claude_version_tuple(version)
    if parsed is None:
        return False
    return parsed >= FORWARD_SUBAGENT_TEXT_FLOOR


def build_claude_stream_argv(
    argv: Sequence[str], *, forward_subagent_text: bool = False
) -> tuple[str, ...]:
    """Preserve caller arguments and add only the documented stream transport flags.

    ``forward_subagent_text`` is fail-closed: the flag is
    emitted only when the caller proved the installed CLI meets
    ``FORWARD_SUBAGENT_TEXT_FLOOR`` — never by default. A caller-supplied flag in
    ``argv`` is preserved verbatim exactly like every other caller argument.
    """

    if not argv or not argv[0]:
        raise HarnessControlError("Claude launch argv requires an executable")
    result = list(argv)
    _require_option_value(result, "--input-format", "stream-json")
    _require_option_value(result, "--output-format", "stream-json")
    _require_option_value(result, "--permission-prompt-tool", "stdio")
    if "-p" not in result and "--print" not in result:
        result.append("-p")
    flags = ["--verbose", "--replay-user-messages"]
    if forward_subagent_text:
        flags.append(FORWARD_SUBAGENT_TEXT_FLAG)
    for flag in flags:
        if flag not in result:
            result.append(flag)
    return tuple(result)


def build_claude_discovery_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Replace Claude 2.1.210's accumulated MCP selectors with one strict empty set."""

    if not argv:
        return tuple(argv)
    discovery_options = ("--mcp-config", _DISCOVERY_MCP_CONFIG, "--strict-mcp-config")
    kept = [argv[0]]
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            return (*kept, *discovery_options, *argv[index:])
        if argument == "--mcp-config":
            index += 1
            # The installed CLI declares ``<configs...>``: every following non-option token is
            # another JSON string or file until the next option (or ``--`` separator).
            while index < len(argv) and not argv[index].startswith("-"):
                index += 1
            continue
        if argument.startswith("--mcp-config="):
            # The attached spelling owns only its attached value. A following positional token
            # is unrelated argv and must remain byte-for-byte intact.
            index += 1
            continue
        if argument == "--strict-mcp-config":
            index += 1
            continue
        kept.append(argument)
        index += 1
    return (*kept, *discovery_options)


def initialization_request(request_id: str) -> dict[str, object]:
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {"subtype": "initialize"},
    }


def list_models_request(request_id: str) -> dict[str, object]:
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {"subtype": "list_models"},
    }


def interrupt_frame(request_id: str) -> dict[str, object]:
    """The native mid-turn interrupt control request (probe-locked on claude 2.1.217).

    A raw ``control_request`` with ``request.subtype == "interrupt"`` is natively accepted while
    a turn is running: the harness answers a ``control_response`` success carrying
    ``{"still_queued": []}`` and the turn terminates with an ``error_during_execution`` /
    ``is_error`` result (there is no dedicated native ``interrupted`` subtype).
    """

    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {"subtype": "interrupt"},
    }


def interrupt_response_outcome(
    frame: Mapping[str, object], request_id: str
) -> tuple[bool, str | None] | None:
    """Correlate one control_response to the interrupt request; ``None`` when unrelated.

    Returns ``(True, None)`` for the native success answer and ``(False, detail)`` for a native
    refusal. A malformed control_response envelope raises; an unrelated request id stays
    untouched so another caller's pending control request is never consumed.
    """

    if frame.get("type") != "control_response":
        return None
    response = _mapping(frame.get("response"), "Claude control response")
    if response.get("request_id") != request_id:
        return None
    if response.get("subtype") == "success":
        return True, None
    error = response.get("error")
    detail = (
        error
        if isinstance(error, str) and error
        else "Claude refused the interrupt control request"
    )
    return False, detail


def bootstrap_message() -> dict[str, object]:
    """Trigger system/init without a model query or visible user message."""

    return {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": [{"type": "text", "text": ""}]},
        "parent_tool_use_id": None,
        "isSynthetic": True,
        "shouldQuery": False,
    }


def parse_control_initialization(
    frame: Mapping[str, object], request_id: str
) -> ClaudeControlInitialization | None:
    if frame.get("type") != "control_response":
        return None
    response = _mapping(frame.get("response"), "Claude initialize response")
    if response.get("request_id") != request_id:
        return None
    if response.get("subtype") != "success":
        raise HarnessControlError("Claude initialize control request was rejected")
    payload = _mapping(response.get("response"), "Claude initialize capability payload")
    commands = _commands_from_control(payload.get("commands"))
    pending = _pending_requests(response)
    return ClaudeControlInitialization(commands=frozenset(commands), pending_requests=pending)


def parse_system_initialization(
    frame: Mapping[str, object],
    *,
    expected_cwd: Path,
) -> ClaudeSystemInitialization | None:
    if frame.get("type") != "system" or frame.get("subtype") != "init":
        return None
    session_id = _required_text(frame, "session_id", "Claude system/init")
    version = _required_text(frame, "claude_code_version", "Claude system/init")
    cwd = _required_text(frame, "cwd", "Claude system/init")
    model = _required_text(frame, "model", "Claude system/init")
    permission_mode = _required_text(frame, "permissionMode", "Claude system/init")
    if not isinstance(frame.get("tools"), list):
        raise HarnessControlError("Claude system/init lacks tool capabilities")
    slash_commands = frame.get("slash_commands")
    if not isinstance(slash_commands, list) or not all(
        isinstance(item, str) for item in slash_commands
    ):
        raise HarnessControlError("Claude system/init lacks slash-command capabilities")
    if Path(cwd).resolve() != expected_cwd.resolve():
        raise HarnessControlError("Claude system/init cwd does not match the launch cwd")
    return ClaudeSystemInitialization(
        session_id=session_id,
        version=version,
        cwd=cwd,
        model=model,
        permission_mode=permission_mode,
        commands=frozenset(_normalize_command(item) for item in slash_commands),
    )


def is_successful_bootstrap_result(frame: Mapping[str, object], session_id: str) -> bool:
    if frame.get("type") != "result":
        return False
    if frame.get("session_id") != session_id:
        raise HarnessControlError("Claude bootstrap result changed session identity")
    if frame.get("subtype") != "success" or frame.get("is_error") is not False:
        raise HarnessControlError("Claude bootstrap message did not complete successfully")
    return True


def correlation_envelope(request_id: str, token: str, text: str) -> str:
    correlation = json.dumps(
        {"requestId": request_id, "token": token},
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"[agents-remember-correlation] {correlation}\n\n{text}"


def prompt_frame(
    *,
    session_id: str,
    correlation_id: str,
    text: str,
) -> dict[str, object]:
    return {
        "type": "user",
        "session_id": session_id,
        "uuid": correlation_id,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "parent_tool_use_id": None,
    }


def raw_message_text(frame: Mapping[str, object]) -> str:
    message = frame.get("message")
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts = [
        block["text"]
        for block in content
        if isinstance(block, Mapping)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "\n".join(texts)


def message_text(frame: Mapping[str, object]) -> str:
    return clip_transcript_text(raw_message_text(frame))


def session_command(text: str) -> str | None:
    stripped = text.lstrip()
    if not stripped.startswith("/"):
        return None
    return _normalize_command(stripped[1:].split(maxsplit=1)[0])


def session_command_replay_text(text: str) -> str:
    """Return Claude's replay body for one native slash command.

    Claude accepts the ordinary ``/command args`` user frame but replays it under the original
    UUID as a canonical command body. Correlation therefore remains exact without pretending the
    replay body is byte-identical to the submitted wire text.
    """

    command = session_command(text)
    if command is None:
        return text
    command_text = text.lstrip()[1:]
    _, separator, arguments = command_text.partition(" ")
    if not separator:
        arguments = ""
    return (
        f"<command-name>/{command}</command-name>\n"
        f"            <command-message>{command}</command-message>\n"
        f"            <command-args>{arguments}</command-args>"
    )


def command_unsupported_detail(command: str, advertised: frozenset[str]) -> str | None:
    if command in _IDENTITY_CHANGING_COMMANDS:
        return (
            f"Claude session command /{command} changes process/session identity and is unsupported "
            "inside one long-lived control adapter"
        )
    if command not in advertised and command not in _NATIVE_CAPABILITY_COMMANDS:
        return f"Claude Code did not advertise /{command}; the command was not sent"
    return None


def pending_interaction(
    frame: Mapping[str, object], *, created_at: str
) -> PendingInteraction | None:
    if frame.get("type") != "control_request":
        return None
    request_id = _required_text(frame, "request_id", "Claude control request")
    request = _mapping(frame.get("request"), "Claude control request")
    if request.get("subtype") != "can_use_tool":
        return None
    tool_name = _required_text(request, "tool_name", "Claude permission request")
    tool_input = _mapping(request.get("input"), "Claude permission input")
    tool_use_id = _required_text(request, "tool_use_id", "Claude permission request")
    if tool_name == "AskUserQuestion":
        questions = _question_pages(tool_input)
        prompt, choices = _question_prompt(questions)
        kind = "user-input"
    else:
        title = request.get("title")
        prompt = title if isinstance(title, str) else f"Allow Claude tool {tool_name}?"
        choices = ("allow", "deny")
        kind = "permission"
        questions = ()
    return PendingInteraction(
        interaction_id=request_id,
        kind=kind,
        prompt=prompt,
        created_at=created_at,
        choices=choices,
        raw={"toolName": tool_name, "toolUseId": tool_use_id, "input": dict(tool_input)},
        questions=questions,
    )


def interaction_response_frame(
    vendor_frame: Mapping[str, object], response_text: str
) -> dict[str, object]:
    request_id = _required_text(vendor_frame, "request_id", "Claude control request")
    request = _mapping(vendor_frame.get("request"), "Claude control request")
    tool_name = _required_text(request, "tool_name", "Claude permission request")
    tool_use_id = _required_text(request, "tool_use_id", "Claude permission request")
    tool_input = _mapping(request.get("input"), "Claude permission input")
    if tool_name == "AskUserQuestion":
        answers = _question_answers(tool_input, response_text)
        result: dict[str, object] = {
            "behavior": "allow",
            "updatedInput": {**tool_input, "answers": answers},
            "toolUseID": tool_use_id,
        }
    elif response_text.strip() == "allow":
        result = {"behavior": "allow", "updatedInput": dict(tool_input), "toolUseID": tool_use_id}
    elif response_text.strip() == "deny":
        result = {
            "behavior": "deny",
            "message": "Denied through the Agents Remember durable operator response",
            "toolUseID": tool_use_id,
        }
    else:
        raise HarnessControlError("Claude permission response must be exactly 'allow' or 'deny'")
    return {
        "type": "control_response",
        "response": {"subtype": "success", "request_id": request_id, "response": result},
    }


def clip_transcript_text(text: str) -> str:
    if len(text) <= MAX_TRANSCRIPT_TEXT_CHARS:
        return text
    omitted = len(text) - MAX_TRANSCRIPT_TEXT_CHARS
    return f"{text[:MAX_TRANSCRIPT_TEXT_CHARS]}\n… [{omitted} characters omitted]"


def restore_pending_interaction(
    pending_requests: tuple[dict[str, object], ...], *, created_at: str
) -> tuple[PendingInteraction | None, dict[str, object] | None]:
    if not pending_requests:
        return None, None
    frame = pending_requests[0]
    interaction = pending_interaction(frame, created_at=created_at)
    if interaction is None:
        raise HarnessControlError(
            "Claude resumed with a pending control request this adapter cannot route"
        )
    return interaction, frame


def optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def terminal_outcome(
    frame: Mapping[str, object], *, interrupt_accepted: bool = False
) -> TerminalOutcome:
    """Classify one result frame; an accepted interrupt remaps ONLY the abort shape.

    Claude answers an accepted interrupt with an ``error_during_execution`` / ``is_error``
    result carrying ``terminal_reason == "aborted_streaming"`` (probe-locked on 2.1.217) —
    natively indistinguishable from a real failure except for that reason plus the adapter's
    accepted-interrupt correlation. The remap is therefore CONJUNCTIVE: only an accepted
    interrupt whose result is genuinely abort-shaped settles ``cancelled``. A real error that
    RACES the accept window — a 429/``api_error`` or a tool failure — carries its own
    ``terminal_reason`` (never ``aborted_streaming``) and keeps its ``failed`` meaning; an
    accepted interrupt is not licence to relabel an unrelated failure as a clean user cut. A
    native success stays ``completed`` even then (the interrupt raced a natural completion), and
    an unprovoked ``aborted_streaming`` with no accepted interrupt stays ``failed``.
    """

    if frame.get("subtype") == "success" and frame.get("is_error") is False:
        return "completed"
    if frame.get("terminal_reason") in _NATIVE_CANCEL_TERMINAL_REASONS:
        return "cancelled"
    if interrupt_accepted and frame.get("terminal_reason") == _ABORT_TERMINAL_REASON:
        return "cancelled"
    return "failed"


def terminal_metadata(frame: Mapping[str, object]) -> dict[str, object]:
    """Retain non-content terminal evidence that is safe to surface for diagnosis."""

    return {
        "subtype": frame.get("subtype"),
        "isError": frame.get("is_error"),
        "terminalReason": frame.get("terminal_reason"),
        "stopReason": frame.get("stop_reason"),
        "apiErrorStatus": frame.get("api_error_status"),
    }


def result_detail(frame: Mapping[str, object]) -> str:
    result = frame.get("result")
    if isinstance(result, str):
        return clip_transcript_text(result)
    errors = frame.get("errors")
    if isinstance(errors, list):
        return clip_transcript_text("\n".join(item for item in errors if isinstance(item, str)))
    subtype = frame.get("subtype")
    return str(subtype) if subtype is not None else ""


def _require_option_value(argv: list[str], flag: str, expected: str) -> None:
    found = False
    for index, item in enumerate(argv):
        value: str | None = None
        if item == flag:
            if index + 1 >= len(argv):
                raise HarnessControlError(f"Claude launch flag {flag} requires a value")
            value = argv[index + 1]
        elif item.startswith(f"{flag}="):
            value = item.split("=", 1)[1]
        if value is not None:
            found = True
            if value != expected:
                raise HarnessControlError(
                    f"Claude launch requires {flag} {expected}, got {value!r}"
                )
    if not found:
        argv.extend((flag, expected))


def _commands_from_control(raw: object) -> set[str]:
    if not isinstance(raw, list):
        raise HarnessControlError("Claude initialize response lacks command capabilities")
    commands: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise HarnessControlError("Claude initialize command entry must be an object")
        commands.add(_normalize_command(_required_text(entry, "name", "Claude command")))
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise HarnessControlError("Claude command aliases must be strings")
        commands.update(_normalize_command(alias) for alias in aliases)
    return commands


def _pending_requests(response: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    pending: list[dict[str, object]] = []
    for key in ("pending_permission_requests", "pending_user_dialog_requests"):
        raw = response.get(key, [])
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise HarnessControlError(f"Claude initialize {key} must be an array of objects")
        pending.extend(dict(item) for item in raw)
    if len(pending) > 1:
        raise HarnessControlError(
            "Claude resumed with multiple pending interactions; the normalized contract exposes one"
        )
    return tuple(pending)


def _question_pages(tool_input: Mapping[str, object]) -> tuple[InteractionQuestion, ...]:
    """Parse the full per-page AskUserQuestion structure; the one validation authority."""

    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        raise HarnessControlError("Claude AskUserQuestion input requires questions")
    pages: list[InteractionQuestion] = []
    for question in questions:
        item = _mapping(question, "Claude AskUserQuestion question")
        header = _required_text(item, "header", "Claude AskUserQuestion question")
        text = _required_text(item, "question", "Claude AskUserQuestion question")
        options = item.get("options")
        if not isinstance(options, list):
            raise HarnessControlError("Claude AskUserQuestion options must be an array")
        multi_select = item.get("multiSelect", False)
        if not isinstance(multi_select, bool):
            raise HarnessControlError("Claude AskUserQuestion multiSelect must be a boolean")
        pages.append(
            InteractionQuestion(
                text=text,
                header=header,
                options=tuple(_question_option(option) for option in options),
                multi_select=multi_select,
            )
        )
    return tuple(pages)


def _question_option(raw: object) -> InteractionQuestionOption:
    option = _mapping(raw, "Claude question option")
    label = _required_text(option, "label", "Claude option")
    description = option.get("description")
    if description is not None and not isinstance(description, str):
        raise HarnessControlError("Claude question option description must be text")
    return InteractionQuestionOption(label=label, description=description)


def _question_prompt(pages: tuple[InteractionQuestion, ...]) -> tuple[str, tuple[str, ...]]:
    """Derive the legacy flattened rendering from the parsed pages (kept for old surfaces)."""

    prompt_lines = [f"{page.header}: {page.text}" for page in pages]
    choices = tuple(option.label for page in pages for option in page.options)
    return "\n".join(prompt_lines), choices


def _question_answers(tool_input: Mapping[str, object], response_text: str) -> dict[str, str]:
    try:
        decoded = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise HarnessControlError(
            "Claude AskUserQuestion response must be a JSON object of question text to answer"
        ) from exc
    if not isinstance(decoded, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in decoded.items()
    ):
        raise HarnessControlError(
            "Claude AskUserQuestion response must map question text to string answers"
        )
    questions = tool_input.get("questions")
    if not isinstance(questions, list):
        raise HarnessControlError("Claude AskUserQuestion input requires questions")
    expected = {
        _required_text(
            _mapping(question, "Claude AskUserQuestion question"),
            "question",
            "Claude AskUserQuestion question",
        )
        for question in questions
    }
    if set(decoded) != expected:
        raise HarnessControlError(
            "Claude AskUserQuestion response must answer every question by its exact text"
        )
    return {str(key): str(value) for key, value in decoded.items()}


def _normalize_command(value: str) -> str:
    return value.strip().lstrip("/")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise HarnessControlError(f"{label} must be an object")
    return value


def _required_text(raw: Mapping[str, object], key: str, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"{label} requires non-empty {key}")
    return value
