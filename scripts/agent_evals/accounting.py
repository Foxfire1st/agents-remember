"""Identity-bound Curator trial accounting; no measurements or acceptance decisions.

Input attempts are chronological and retained, including unknown attempts and
their causes. A case reaches the loop's threshold at two correct graded trials
out of three. R62 v8 permits infrastructure replacements and one named final-full
step after passing measurements, without arbitrary unchanged-text resampling.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Literal, cast

from agents_remember.errors import AgentsRememberError

MODELS = (
    "gpt-6.1-sol",
    "opencode-go:deepseek-v4.1-flash",
    "opencode-go:kimi-k3.1-pro",
    "openai-codex:gpt-6.1-sol-900k",
)
CASE_THEMES = {f"CE{number:02}": f"CT{(number + 1) // 2}" for number in range(1, 15)}
UNKNOWN_STATUSES = (
    "infrastructure_failure",
    "missing_capture",
    "unanswered_prompt",
    "semantic_unknown",
)
HELPER_DEADLINE_STATE = "helper-liveness-unobservable-at-deadline"
Outcome = Literal[
    "right",
    "wrong",
    "infrastructure_failure",
    "missing_capture",
    "unanswered_prompt",
    "semantic_unknown",
    "actor_only_complete",
    "helper-liveness-unobservable-at-deadline",
]
CaseStatus = Literal[
    "pass", "fail", "unknown", "semantic_unknown", "helper-liveness-unobservable-at-deadline"
]
ComparisonStatus = Literal["improved", "unchanged", "worsened", "unknown"]
BatchKind = Literal["measurement", "final_full", "tooling_check"]


def final_full_repeat_allowed() -> bool:
    """D626 Q15 permits the named step, relayed by Manager at 20261029T210559Z."""
    return True


@dataclass(frozen=True, slots=True)
class Attempt:
    """One observed attempt, including its slot and any replaced unknown attempt."""

    attempt_id: str
    case_id: str
    model: str
    text_signature: str
    batch_id: str
    trial_slot: int
    status: Outcome
    actor_tokens: int | None = None
    judge_tokens: int | None = None
    retry_of: str | None = None
    judge_model: str | None = None
    unknown_cause: str | None = None
    batch_kind: BatchKind = "measurement"

    def __post_init__(self) -> None:
        for name in ("attempt_id", "text_signature", "batch_id"):
            _required_text(getattr(self, name), name)
        if self.case_id not in CASE_THEMES or self.model not in MODELS:
            raise AgentsRememberError(
                "Attempt must name a fixed CE01-CE14 case and admitted model."
            )
        if type(self.trial_slot) is not int or self.trial_slot not in (1, 2, 3):
            raise AgentsRememberError(
                "trial_slot must be 1, 2 or 3; retain replacements in that slot."
            )
        if self.status not in (
            "right",
            "wrong",
            "actor_only_complete",
            HELPER_DEADLINE_STATE,
            *UNKNOWN_STATUSES,
        ):
            raise AgentsRememberError(
                "Unknown attempt status; name the observed outcome explicitly."
            )
        if self.batch_kind not in ("measurement", "final_full", "tooling_check"):
            raise AgentsRememberError(
                "batch_kind must be measurement, final_full or tooling_check."
            )
        _validate_actor_only(self)
        for name in ("actor_tokens", "judge_tokens"):
            _token_count(getattr(self, name), name)
        for name in ("retry_of", "judge_model", "unknown_cause"):
            value = getattr(self, name)
            if value is not None:
                _required_text(value, name)
        if self.status in (*UNKNOWN_STATUSES, HELPER_DEADLINE_STATE):
            _required_text(self.unknown_cause, "unknown_cause")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Attempt:
        """Validate raw JSON fields without guessing missing identity or token usage."""
        slot = value.get("trial_slot")
        if type(slot) is not int:
            raise AgentsRememberError("trial_slot must be an integer in the captured attempt.")
        return cls(
            attempt_id=_required_text(value.get("attempt_id"), "attempt_id"),
            case_id=_required_text(value.get("case_id"), "case_id"),
            model=_required_text(value.get("model"), "model"),
            text_signature=_required_text(value.get("text_signature"), "text_signature"),
            batch_id=_required_text(value.get("batch_id"), "batch_id"),
            trial_slot=slot,
            status=cast(Outcome, _required_text(value.get("status"), "status")),
            actor_tokens=_token_count(value.get("actor_tokens"), "actor_tokens"),
            judge_tokens=_token_count(value.get("judge_tokens"), "judge_tokens"),
            retry_of=_optional_text(value.get("retry_of"), "retry_of"),
            judge_model=_optional_text(value.get("judge_model"), "judge_model"),
            unknown_cause=_optional_text(value.get("unknown_cause"), "unknown_cause"),
            batch_kind=cast(
                BatchKind, _required_text(value.get("batch_kind", "measurement"), "batch_kind")
            ),
        )


def _validate_actor_only(attempt: Attempt) -> None:
    if attempt.status == "actor_only_complete" and (
        attempt.batch_kind != "tooling_check"
        or attempt.judge_model is not None
        or attempt.judge_tokens is not None
    ):
        raise AgentsRememberError("actor_only_complete is ungraded tooling evidence only")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentsRememberError(f"{field} must be nonempty; recover its actual identity first.")
    return value


def _optional_text(value: object, field: str) -> str | None:
    return None if value is None else _required_text(value, field)


def _token_count(value: object, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise AgentsRememberError(
            f"{field} must be a nonnegative integer or null for unknown usage."
        )
    return value


@dataclass(frozen=True, slots=True)
class TokenTotal:
    known_tokens: int
    observed_attempts: int
    unknown_attempts: int


@dataclass(frozen=True, slots=True)
class TokenAccounting:
    actor: TokenTotal
    judge: TokenTotal

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def summarize_usage(attempts: Sequence[Attempt]) -> TokenAccounting:
    """Count unknown separately from observed zero, including replaced attempts."""

    def total(values: Sequence[int | None]) -> TokenTotal:
        known = [value for value in values if value is not None]
        return TokenTotal(sum(known), len(known), len(values) - len(known))

    return TokenAccounting(
        actor=total([attempt.actor_tokens for attempt in attempts]),
        judge=total([attempt.judge_tokens for attempt in attempts]),
    )


@dataclass(frozen=True, slots=True)
class UnknownTrial:
    attempt_id: str
    case_id: str
    trial_slot: int
    status: Outcome
    cause: str
    replacement_attempt_id: str | None
    retry_required: bool
    owner: Literal["Architect"] = "Architect"


def _unknown_trials(attempts: Sequence[Attempt]) -> tuple[UnknownTrial, ...]:
    replacements = {
        attempt.retry_of: attempt.attempt_id for attempt in attempts if attempt.retry_of
    }
    return tuple(
        UnknownTrial(
            attempt.attempt_id,
            attempt.case_id,
            attempt.trial_slot,
            attempt.status,
            _required_text(attempt.unknown_cause, "unknown_cause"),
            replacements.get(attempt.attempt_id),
            attempt.status != "semantic_unknown" and attempt.attempt_id not in replacements,
        )
        for attempt in attempts
        if attempt.status in UNKNOWN_STATUSES
    )


@dataclass(frozen=True, slots=True)
class CaseMetrics:
    case_id: str
    theme_id: str
    attempts: int
    graded_outcomes: int
    right: int
    wrong: int
    unknown_attempts: int
    complete: bool
    status: CaseStatus
    all_three_right: bool | None
    at_least_two_of_three: bool | None
    usage: TokenAccounting
    unknown_trials: tuple[UnknownTrial, ...]
    helper_deadline_attempts: int


@dataclass(frozen=True, slots=True)
class ThemeMetrics:
    theme_id: str
    case_ids: tuple[str, ...]
    complete: bool
    all_three_right_cases: int | None
    at_least_two_of_three_cases: int | None


@dataclass(frozen=True, slots=True)
class ModelReport:
    model: str
    text_signature: str
    batch_id: str
    cases: tuple[CaseMetrics, ...]
    themes: tuple[ThemeMetrics, ...]
    complete: bool
    graded_outcomes: int
    unknown_attempts: int
    all_three_right_cases: int | None
    at_least_two_of_three_cases: int | None
    usage: TokenAccounting
    unknown_trials: tuple[UnknownTrial, ...]
    batch_kind: BatchKind
    tooling_usage: TokenAccounting
    tooling_unknown_trials: tuple[UnknownTrial, ...]
    helper_deadline_trials: tuple[Attempt, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_attempt_sequence(attempts: Sequence[Attempt]) -> None:
    """Validate captured attempts, without suppressing unknowns or sampling failures.

    This validates evidence supplied to it; it cannot establish capture completeness
    or authorize another run. Caller retains the chronological full attempt ledger.
    """
    identities: set[str] = set()
    batch_texts: dict[str, str] = {}
    batch_kinds: dict[str, BatchKind] = {}
    latest: dict[tuple[str, str, str, int], Attempt] = {}
    batches: dict[tuple[str, str], dict[str, list[Attempt]]] = defaultdict(dict)
    for attempt in attempts:
        if attempt.attempt_id in identities:
            raise AgentsRememberError(
                "Duplicate attempt identity; retain each actual attempt once."
            )
        identities.add(attempt.attempt_id)
        if attempt.batch_kind == "final_full" and not final_full_repeat_allowed():
            raise AgentsRememberError(
                "The named final_full step is closed pending the Architect's v8 ruling."
            )
        signature = batch_texts.setdefault(attempt.batch_id, attempt.text_signature)
        if signature != attempt.text_signature:
            raise AgentsRememberError(
                "One batch names different text signatures; split its identities."
            )
        if batch_kinds.setdefault(attempt.batch_id, attempt.batch_kind) != attempt.batch_kind:
            raise AgentsRememberError("One batch names different batch kinds; retain its phase.")
        slot = (attempt.batch_id, attempt.case_id, attempt.model, attempt.trial_slot)
        _validate_replacement(latest.get(slot), attempt)
        latest[slot] = attempt
        if attempt.batch_kind != "tooling_check":
            groups = batches[(attempt.case_id, attempt.model)]
            groups.setdefault(attempt.batch_id, []).append(attempt)
    for groups in batches.values():
        _validate_resampling(groups)


def _validate_replacement(previous: Attempt | None, attempt: Attempt) -> None:
    if previous is None:
        if attempt.retry_of is not None:
            raise AgentsRememberError(
                "Retry has no earlier attempt in its exact batch/case/model/slot."
            )
        return
    if previous.status in ("right", "wrong"):
        raise AgentsRememberError(
            "A graded slot cannot be resampled; only an unknown trial may be replaced."
        )
    if previous.status == "semantic_unknown":
        raise AgentsRememberError(
            "A semantic_unknown criterion needs clarification; do not rerun its actor."
        )
    if attempt.retry_of != previous.attempt_id:
        raise AgentsRememberError(
            "Infrastructure replacement must link the latest unknown in its slot."
        )


def _validate_resampling(groups: Mapping[str, Sequence[Attempt]]) -> None:
    sampled_texts: dict[str, Sequence[Attempt]] = {}
    latest_batch: Sequence[Attempt] | None = None
    for batch in groups.values():
        signature = batch[0].text_signature
        if (
            batch[0].batch_kind == "final_full"
            and latest_batch is not None
            and _case_metrics(latest_batch[0].case_id, latest_batch).status != "pass"
        ):
            raise AgentsRememberError(
                "The named final_full step requires previously measured cases to pass first."
            )
        previous = sampled_texts.get(signature)
        if previous is not None:
            if batch[0].batch_kind != "final_full":
                raise AgentsRememberError(
                    "Unchanged text cannot begin another behavioral batch; replace only the spoiled trial in its original slot."
                )
            prior_result = _case_metrics(previous[0].case_id, previous)
            if previous[0].batch_kind == "final_full" or prior_result.status != "pass":
                raise AgentsRememberError(
                    "A named final_full repeat requires a completed passing measurement batch."
                )
        sampled_texts[signature] = batch
        latest_batch = batch


def _case_metrics(case_id: str, attempts: Sequence[Attempt]) -> CaseMetrics:
    right = sum(attempt.status == "right" for attempt in attempts)
    wrong = sum(attempt.status == "wrong" for attempt in attempts)
    graded = right + wrong
    complete = graded == 3
    status: CaseStatus = "unknown" if not complete else "pass" if right >= 2 else "fail"
    replaced = {attempt.retry_of for attempt in attempts}
    helper_deadlines = [attempt for attempt in attempts if attempt.status == HELPER_DEADLINE_STATE]
    if not complete and any(
        item.status == "semantic_unknown" and item.attempt_id not in replaced for item in attempts
    ):
        status = "semantic_unknown"
    if not complete and any(item.attempt_id not in replaced for item in helper_deadlines):
        status = HELPER_DEADLINE_STATE
    return CaseMetrics(
        case_id,
        CASE_THEMES[case_id],
        len(attempts),
        graded,
        right,
        wrong,
        sum(attempt.status in UNKNOWN_STATUSES for attempt in attempts),
        complete,
        status,
        right == 3 if complete else None,
        right >= 2 if complete else None,
        summarize_usage(attempts),
        _unknown_trials(attempts),
        len(helper_deadlines),
    )


def _theme_metrics(theme_id: str, cases: Sequence[CaseMetrics]) -> ThemeMetrics:
    complete = all(case.complete for case in cases)
    return ThemeMetrics(
        theme_id=theme_id,
        case_ids=tuple(case.case_id for case in cases),
        complete=complete,
        all_three_right_cases=sum(case.all_three_right is True for case in cases)
        if complete
        else None,
        at_least_two_of_three_cases=(
            sum(case.at_least_two_of_three is True for case in cases) if complete else None
        ),
    )


def summarize_attempts(
    attempts: Sequence[Attempt], *, model: str, text_signature: str, batch_id: str
) -> ModelReport:
    """Report all fourteen fixed cases for one exact model/text/batch identity."""
    if model not in MODELS:
        raise AgentsRememberError("Report must name one admitted model, not a harness label.")
    _required_text(text_signature, "text_signature")
    _required_text(batch_id, "batch_id")
    validate_attempt_sequence(attempts)
    if any(
        attempt.batch_id == batch_id and attempt.text_signature != text_signature
        for attempt in attempts
    ):
        raise AgentsRememberError(
            "Report text signature conflicts with the captured batch identity."
        )
    selected = [
        attempt
        for attempt in attempts
        if (attempt.model, attempt.text_signature, attempt.batch_id)
        == (model, text_signature, batch_id)
    ]
    measured = [attempt for attempt in selected if attempt.batch_kind != "tooling_check"]
    tooling = [attempt for attempt in selected if attempt.batch_kind == "tooling_check"]
    grouped: dict[str, list[Attempt]] = defaultdict(list)
    for attempt in measured:
        grouped[attempt.case_id].append(attempt)
    cases = tuple(_case_metrics(case_id, grouped[case_id]) for case_id in CASE_THEMES)
    themes = tuple(
        _theme_metrics(f"CT{number}", [case for case in cases if case.theme_id == f"CT{number}"])
        for number in range(1, 8)
    )
    complete = all(case.complete for case in cases)
    return ModelReport(
        model,
        text_signature,
        batch_id,
        cases,
        themes,
        complete,
        sum(case.graded_outcomes for case in cases),
        sum(case.unknown_attempts for case in cases),
        sum(case.all_three_right is True for case in cases) if complete else None,
        sum(case.at_least_two_of_three is True for case in cases) if complete else None,
        summarize_usage(measured),
        _unknown_trials(measured),
        selected[0].batch_kind if selected else "measurement",
        summarize_usage(tooling),
        _unknown_trials(tooling),
        tuple(attempt for attempt in selected if attempt.status == HELPER_DEADLINE_STATE),
    )


@dataclass(frozen=True, slots=True)
class ThemeComparison:
    theme_id: str
    old_all_three_right_cases: int | None
    final_all_three_right_cases: int | None
    old_at_least_two_of_three_cases: int | None
    final_at_least_two_of_three_cases: int | None
    status: ComparisonStatus


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    model: str
    old_text_signature: str
    final_text_signature: str
    old_batch_id: str
    final_batch_id: str
    complete: bool
    themes: tuple[ThemeComparison, ...]
    reopens_loop: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _comparison(old: ThemeMetrics, final: ThemeMetrics) -> ThemeComparison:
    before, after = old.at_least_two_of_three_cases, final.at_least_two_of_three_cases
    status: ComparisonStatus = "unknown"
    if before is not None and after is not None:
        status = "worsened" if after < before else "improved" if after > before else "unchanged"
    return ThemeComparison(
        old.theme_id,
        old.all_three_right_cases,
        final.all_three_right_cases,
        before,
        after,
        status,
    )


def compare_reports(old: ModelReport, final_new: ModelReport) -> ComparisonReport:
    """Compare the same fixed pairs per model; incomplete pairs remain unknown."""
    if "tooling_check" in (old.batch_kind, final_new.batch_kind):
        raise AgentsRememberError("A tooling_check batch is excluded from measurement comparisons.")
    if old.model != final_new.model:
        raise AgentsRememberError("Compare one model at a time; never combine model reliability.")
    themes = tuple(
        _comparison(before, after)
        for before, after in zip(old.themes, final_new.themes, strict=True)
    )
    return ComparisonReport(
        model=old.model,
        old_text_signature=old.text_signature,
        final_text_signature=final_new.text_signature,
        old_batch_id=old.batch_id,
        final_batch_id=final_new.batch_id,
        complete=old.complete and final_new.complete,
        themes=themes,
        reopens_loop=any(theme.status == "worsened" for theme in themes),
    )
