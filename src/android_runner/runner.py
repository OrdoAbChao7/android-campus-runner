from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
import time
from uuid import uuid4

from .intent import (
    IntentPersistenceError,
    IntentReservation,
    IntentUseRegistry,
    RunIntent,
    RunObservation,
    validate_route_binding,
)
from .evidence import EvidenceWriter
from .state import RunState, StateMachine
from .wecom.campus_run import (
    CampusRunState,
    capture_start_prompt_checkpoint,
    confirm_free_run,
    open_campus_run,
)
from .wecom.account import AccountSwitchState, WeComEnterpriseSwitcher, WeComEnterpriseSwitchCapability
from .workflow import MultiRunResult, run_multi_account, run_route_then_switch


@dataclass(frozen=True)
class MvpRunResult:
    campus_state: CampusRunState
    account_state: object | None = None
    state: RunState = RunState.IDLE
    evidence_dir: Path | None = None
    evidence_summary: Path | None = None


_DEFAULT_EVIDENCE_ROOT = Path("logs") / "runs"
_SUCCESS_STATES = (
    RunState.ACCOUNT_VERIFIED,
    RunState.PAGE_READY,
    RunState.PROVIDER_READY,
    RunState.START_AUTHORIZED,
    RunState.RUNNING_VERIFIED,
    RunState.ROUTE_RUNNING,
    RunState.ROUTE_COMPLETE,
    RunState.APP_RESULT_VERIFIED,
    RunState.PROVIDER_STOPPED,
    RunState.EVIDENCE_CAPTURED,
    RunState.ACCOUNT_SWITCHED,
    RunState.DONE,
)


@dataclass
class _RunnerEvidence:
    """Small adapter that makes existing runner outcomes auditable."""

    writer: EvidenceWriter
    machine: StateMachine
    run_id: str

    @property
    def run_dir(self) -> Path:
        return self.writer.run_dir

    def preflight_ok(self) -> None:
        if self.machine.state is RunState.DEVICE_LOCKED:
            self.machine.transition(RunState.PREFLIGHT_OK)

    def safe_stop(self, reason: str) -> None:
        self.machine.safe_stop(reason)

    def complete(self) -> None:
        if self.machine.state is RunState.SAFE_STOP:
            return
        self.preflight_ok()
        for state in _SUCCESS_STATES:
            if self.machine.state is state:
                continue
            self.machine.transition(state)

    def finalize(
        self,
        *,
        runner_state: RunState,
        outcome: str,
        reason: str,
        authorized: bool,
        account_count: int,
        completed_count: int = 0,
        failed_count: int = 0,
    ) -> Path:
        if outcome == "completed":
            self.complete()
        else:
            self.safe_stop(reason)
        self.writer.append_event(
            "runner_outcome",
            {
                "outcome": outcome,
                "runner_state": runner_state.name,
                "final_state": self.machine.state.name,
            },
        )
        return self.writer.write_snapshot(
            "summary",
            {
                "run_id": self.run_id,
                "outcome": outcome,
                "reason": reason,
                "runner_state": runner_state.name,
                "final_state": self.machine.state.name,
                "authorized": authorized,
                "account_count": account_count,
                "completed_count": completed_count,
                "failed_count": failed_count,
            },
        )


def _begin_evidence(
    *,
    evidence_root: Path | None,
    run_id: str | None,
    account_count: int,
    authorized: bool,
) -> _RunnerEvidence | None:
    """Create a unique evidence directory before any provider or UI call."""
    evidence_id = run_id or f"run-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:12]}"
    try:
        writer = EvidenceWriter(Path(evidence_root or _DEFAULT_EVIDENCE_ROOT), evidence_id)
        machine = StateMachine(journal=writer)
        machine.transition(RunState.DEVICE_LOCKED)
        writer.append_event(
            "runner_started",
            {"account_count": account_count, "authorized": authorized},
        )
    except Exception:
        return None
    return _RunnerEvidence(writer=writer, machine=machine, run_id=evidence_id)


def _finish_mvp(
    evidence: _RunnerEvidence | None,
    result: MvpRunResult,
    *,
    outcome: str,
    reason: str,
    authorized: bool,
) -> MvpRunResult:
    if evidence is None:
        return result
    try:
        summary = evidence.finalize(
            runner_state=result.state,
            outcome=outcome,
            reason=reason,
            authorized=authorized,
            account_count=1,
            completed_count=1 if outcome == "completed" else 0,
            failed_count=1 if outcome != "completed" else 0,
        )
    except Exception:
        return replace(result, state=RunState.SAFE_STOP, evidence_dir=evidence.run_dir)
    return replace(result, evidence_dir=evidence.run_dir, evidence_summary=summary)


def _finish_multi(
    evidence: _RunnerEvidence | None,
    result: MultiRunResult,
    *,
    outcome: str,
    reason: str,
    authorized: bool,
    account_count: int,
) -> MultiRunResult:
    if evidence is None:
        return result
    try:
        summary = evidence.finalize(
            runner_state=result.state,
            outcome=outcome,
            reason=reason,
            authorized=authorized,
            account_count=account_count,
            completed_count=len(result.completed),
            failed_count=len(result.failed),
        )
    except Exception:
        result.state = RunState.SAFE_STOP
        result.evidence_dir = evidence.run_dir
        return result
    result.evidence_dir = evidence.run_dir
    result.evidence_summary = summary
    return result


def _stop_safely(provider) -> bool:
    try:
        stop_verified = getattr(provider, "stop_verified", None)
        if not callable(stop_verified):
            return False
        result = stop_verified()
    except Exception:
        return False
    return bool(getattr(result, "ok", False))


def _cleanup_state(provider) -> RunState:
    return RunState.IDLE if _stop_safely(provider) else RunState.SAFE_STOP


def _validate_multi_account_authorization(
    accounts: list[str],
    route: Path,
    intents: dict[str, tuple[RunIntent, RunObservation]] | None,
    intent_registry: IntentUseRegistry | None,
    *,
    action_id: str = "campus_run.start",
) -> str | None:
    """Validate every account binding before touching the provider or device UI."""
    if (
        not isinstance(intent_registry, IntentUseRegistry)
        or not callable(getattr(intent_registry, "validate_registered", None))
        or not callable(getattr(intent_registry, "consume_reserved", None))
        or not callable(getattr(intent_registry, "require_durable", None))
    ):
        return "invalid RunIntent authorization: intent_registry is not usable"
    try:
        intent_registry.require_durable()
    except IntentPersistenceError as exc:
        return f"invalid RunIntent authorization: {exc}"
    if not isinstance(intents, dict):
        return "invalid RunIntent authorization: account intent mapping is missing"

    invalid: list[str] = []
    for account in accounts:
        binding = intents.get(account)
        if not isinstance(binding, tuple) or len(binding) != 2:
            invalid.append(f"{account} (missing or malformed binding)")
            continue
        intent, observation = binding
        if not isinstance(intent, RunIntent) or not isinstance(observation, RunObservation):
            invalid.append(f"{account} (RunIntent/RunObservation required)")
            continue
        account_errors: list[str] = []
        if intent.current_enterprise != account or intent.target_enterprise != account:
            account_errors.append("enterprise binding mismatch")
        try:
            validate_route_binding(route, intent, observation, action_id)
        except Exception as exc:
            account_errors.append(str(exc))
        try:
            intent_registry.validate_registered(intent)
        except Exception as exc:
            account_errors.append(str(exc))
        if account_errors:
            invalid.append(f"{account} ({'; '.join(account_errors)})")

    if invalid:
        return "invalid RunIntent authorization before provider/UI actions: " + "; ".join(invalid)
    return None


def _validate_single_authorization(
    route: Path,
    intent: RunIntent | None,
    observation: RunObservation | None,
    intent_registry: IntentUseRegistry | None,
    *,
    action_id: str = "campus_run.start",
) -> str | None:
    """Validate a single authorization before touching provider or device UI."""
    if (
        not isinstance(intent, RunIntent)
        or not isinstance(observation, RunObservation)
        or not isinstance(intent_registry, IntentUseRegistry)
        or not callable(getattr(intent_registry, "validate_registered", None))
        or not callable(getattr(intent_registry, "consume_reserved", None))
        or not callable(getattr(intent_registry, "require_durable", None))
    ):
        return "invalid RunIntent authorization: intent, observation, and registry are required"
    try:
        intent_registry.require_durable()
    except IntentPersistenceError as exc:
        return f"invalid RunIntent authorization: {exc}"
    errors: list[str] = []
    try:
        validate_route_binding(route, intent, observation, action_id)
    except Exception as exc:
        errors.append(str(exc))
    try:
        intent_registry.validate_registered(intent)
    except Exception as exc:
        errors.append(str(exc))
    if errors:
        return "invalid RunIntent authorization before provider/UI actions: " + "; ".join(errors)
    return None


def run_mvp(
    device,
    provider,
    route: Path,
    switcher,
    *,
    intent: RunIntent | None = None,
    observation: RunObservation | None = None,
    intent_registry: IntentUseRegistry | None = None,
    action_id: str = "campus_run.start",
    app_result_verified: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    evidence_root: Path | None = None,
    run_id: str | None = None,
) -> MvpRunResult:
    """Execute the authorized MVP flow, stopping safely at the start prompt by default."""
    reservation: IntentReservation | None = None
    has_authorization = intent is not None or observation is not None or intent_registry is not None
    evidence = _begin_evidence(
        evidence_root=evidence_root,
        run_id=run_id,
        account_count=1,
        authorized=has_authorization,
    )
    if evidence is None:
        return MvpRunResult(CampusRunState.INIT, state=RunState.SAFE_STOP)
    if not isinstance(switcher, WeComEnterpriseSwitcher):
        return _finish_mvp(
            evidence,
            MvpRunResult(
                CampusRunState.INIT,
                account_state=AccountSwitchState.ABORT,
                state=RunState.SAFE_STOP,
            ),
            outcome="refused",
            reason="WeComEnterpriseSwitcher is required",
            authorized=has_authorization,
        )
    if has_authorization:
        if intent_registry is None:
            try:
                intent_registry = IntentUseRegistry.production()
            except IntentPersistenceError:
                return _finish_mvp(
                    evidence,
                    MvpRunResult(CampusRunState.INIT, state=RunState.SAFE_STOP),
                    outcome="refused",
                    reason="durable intent-use store is unavailable",
                    authorized=True,
                )
        authorization_error = _validate_single_authorization(
            route, intent, observation, intent_registry, action_id=action_id,
        )
        if authorization_error is not None:
            return _finish_mvp(
                evidence,
                MvpRunResult(CampusRunState.INIT, state=RunState.SAFE_STOP),
                outcome="refused",
                reason="RunIntent authorization validation failed",
                authorized=True,
            )
        try:
            reservation = intent_registry.reserve_batch([intent])
        except Exception:
            return _finish_mvp(
                evidence,
                MvpRunResult(CampusRunState.INIT, state=RunState.SAFE_STOP),
                outcome="refused",
                reason="RunIntent reservation failed",
                authorized=True,
            )
        evidence.preflight_ok()

    try:
        prepared = provider.prepare()
        if not getattr(prepared, "ok", True):
            return _finish_mvp(
                evidence,
                MvpRunResult(CampusRunState.INIT, state=_cleanup_state(provider)),
                outcome="failed",
                reason="provider preparation failed",
                authorized=has_authorization,
            )
        try:
            provider_ready = not hasattr(provider, "ready") or provider.ready()
        except Exception:
            _stop_safely(provider)
            return _finish_mvp(
                evidence,
                MvpRunResult(CampusRunState.INIT, state=RunState.SAFE_STOP),
                outcome="failed",
                reason="provider readiness check failed",
                authorized=has_authorization,
            )
        if not provider_ready:
            return _finish_mvp(
                evidence,
                MvpRunResult(CampusRunState.INIT, state=_cleanup_state(provider)),
                outcome="failed",
                reason="provider readiness failed",
                authorized=has_authorization,
            )
        state = open_campus_run(device)
        if reservation is None:
            return _finish_mvp(
                evidence,
                MvpRunResult(state, state=_cleanup_state(provider)),
                outcome="refused",
                reason="no single-use RunIntent was consumed",
                authorized=False,
            )
        try:
            start_checkpoint = capture_start_prompt_checkpoint(
                device,
                expected_enterprise=intent.target_enterprise,
            )
            confirm_free_run(
                device,
                intent=intent,
                observation=observation,
                intent_registry=intent_registry,
                reservation=reservation,
                route=route,
                action_id=action_id,
                start_checkpoint=start_checkpoint,
            )
        except Exception:
            return _finish_mvp(
                evidence,
                MvpRunResult(state, state=_cleanup_state(provider)),
                outcome="failed",
                reason="start prompt verification or authorization failed",
                authorized=True,
            )
        account_state = run_route_then_switch(
            provider,
            route,
            switcher,
            app_result_verified=app_result_verified,
            max_duration=intent.max_duration,
            clock=clock,
        )
        run_state = RunState.SAFE_STOP if account_state is AccountSwitchState.ABORT else RunState.IDLE
        outcome = "completed" if account_state is AccountSwitchState.READY else "failed"
        return _finish_mvp(
            evidence,
            MvpRunResult(CampusRunState.RUNNING, account_state, run_state),
            outcome=outcome,
            reason="guarded route and account workflow completed" if outcome == "completed" else "guarded route or account workflow failed",
            authorized=True,
        )
    finally:
        if reservation is not None:
            intent_registry.release_reservation(reservation)


def run_multi_account_mvp(
    device,
    provider,
    route: Path,
    accounts: list[str],
    *,
    current_account: str | None = None,
    logged_in_enterprises: tuple[str, ...] | list[str] | None = None,
    intents: dict[str, tuple[RunIntent, RunObservation]] | None = None,
    intent_registry: IntentUseRegistry | None = None,
    app_result_verified_fn: Callable[[str], bool] | None = None,
    before_account_fn: Callable[[str, int, int], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    evidence_root: Path | None = None,
    run_id: str | None = None,
    start_prompt_verified: bool = False,
) -> MultiRunResult:
    """Run campus-run sequentially for every account in *accounts*.

    The GPS provider is prepared once before the first run and is always
    stopped with verification after completion or an error.

    *accounts* is a list of enterprise (or account) display names that WeCom
    shows in its account-switcher. The device must already be logged in to all
    of them. Switching is done via :class:`WeComEnterpriseSwitcher`.

    *current_account* is the enterprise name currently active on the device.
    Omit it when there is only one account or you do not need the guard.

    *logged_in_enterprises* is the explicit, operator-provided list of
    enterprises already logged in on the device. Without it, switching aborts.
    """
    has_authorization = intents is not None or intent_registry is not None
    evidence = _begin_evidence(
        evidence_root=evidence_root,
        run_id=run_id,
        account_count=len(accounts),
        authorized=has_authorization,
    )
    if evidence is None:
        return MultiRunResult(failed=list(accounts), state=RunState.SAFE_STOP)
    if not accounts:
        return _finish_multi(
            evidence,
            MultiRunResult(),
            outcome="refused",
            reason="no accounts were requested",
            authorized=has_authorization,
            account_count=0,
        )
    if intents is not None and intent_registry is None:
        try:
            intent_registry = IntentUseRegistry.production()
        except IntentPersistenceError as exc:
            return _finish_multi(
                evidence,
                MultiRunResult(
                    failed=list(accounts),
                    state=RunState.SAFE_STOP,
                    message=f"durable RunIntent authorization store is unavailable: {exc}",
                ),
                outcome="refused",
                reason="durable intent-use store is unavailable",
                authorized=True,
                account_count=len(accounts),
            )
    authorization_error = _validate_multi_account_authorization(
        accounts, route, intents, intent_registry,
    )
    if authorization_error is not None:
        return _finish_multi(
            evidence,
            MultiRunResult(
                failed=list(accounts),
                state=RunState.SAFE_STOP,
                message=authorization_error,
            ),
            outcome="refused",
            reason="RunIntent authorization validation failed",
            authorized=has_authorization,
            account_count=len(accounts),
        )

    try:
        reservation = intent_registry.reserve_batch(
            [intents[account][0] for account in accounts],
        )
    except Exception as exc:
        return _finish_multi(
            evidence,
            MultiRunResult(
                failed=list(accounts),
                state=RunState.SAFE_STOP,
                message=f"invalid RunIntent authorization reservation: {exc}",
            ),
            outcome="refused",
            reason="RunIntent reservation failed",
            authorized=True,
            account_count=len(accounts),
        )
    evidence.preflight_ok()

    try:
        # Prepare the GPS provider once before any runs start.
        prepared = provider.prepare()
        if not getattr(prepared, "ok", True):
            _stop_safely(provider)
            return _finish_multi(
                evidence,
                MultiRunResult(failed=list(accounts), state=RunState.SAFE_STOP),
                outcome="failed",
                reason="provider preparation failed",
                authorized=True,
                account_count=len(accounts),
            )

        try:
            provider_ready = not hasattr(provider, "ready") or provider.ready()
        except Exception:
            _stop_safely(provider)
            return _finish_multi(
                evidence,
                MultiRunResult(failed=list(accounts), state=RunState.SAFE_STOP),
                outcome="failed",
                reason="provider readiness check failed",
                authorized=True,
                account_count=len(accounts),
            )
        if not provider_ready:
            _stop_safely(provider)
            return _finish_multi(
                evidence,
                MultiRunResult(failed=list(accounts), state=RunState.SAFE_STOP),
                outcome="failed",
                reason="provider readiness failed",
                authorized=True,
                account_count=len(accounts),
            )

        switcher_capability = WeComEnterpriseSwitchCapability(
            device,
            current=current_account,
            logged_in_enterprises=tuple(logged_in_enterprises or ()),
        )

        def confirm_with_checkpoint(_device, **kwargs):
            intent = kwargs["intent"]
            return confirm_free_run(
                _device,
                start_checkpoint=capture_start_prompt_checkpoint(
                    _device,
                    expected_enterprise=intent.target_enterprise,
                ),
                **kwargs,
            )

        result = run_multi_account(
            provider=provider,
            route=route,
            accounts=accounts,
            open_campus_run_fn=(
                (lambda _device: CampusRunState.START_PROMPT)
                if start_prompt_verified else open_campus_run
            ),
            confirm_free_run_fn=confirm_with_checkpoint,
            switcher_capability=switcher_capability,
            device=device,
            intents=intents,
            intent_registry=intent_registry,
            reservation=reservation,
            app_result_verified_fn=app_result_verified_fn,
            before_account_fn=before_account_fn,
            clock=clock,
        )
        outcome = "completed" if not result.failed and len(result.completed) == len(accounts) else "failed"
        return _finish_multi(
            evidence,
            result,
            outcome=outcome,
            reason="guarded multi-account workflow completed" if outcome == "completed" else "guarded multi-account workflow failed",
            authorized=True,
            account_count=len(accounts),
        )
    finally:
        intent_registry.release_reservation(reservation)
