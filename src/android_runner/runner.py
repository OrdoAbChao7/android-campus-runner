from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
import time

from .intent import IntentReservation, IntentUseRegistry, RunIntent, RunObservation, validate_route_binding
from .state import RunState
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
    ):
        return "invalid RunIntent authorization: intent_registry is not usable"
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
    ):
        return "invalid RunIntent authorization: intent, observation, and registry are required"
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
) -> MvpRunResult:
    """Execute the authorized MVP flow, stopping safely at the start prompt by default."""
    reservation: IntentReservation | None = None
    has_authorization = intent is not None or observation is not None or intent_registry is not None
    if not isinstance(switcher, WeComEnterpriseSwitcher):
        return MvpRunResult(
            CampusRunState.INIT,
            account_state=AccountSwitchState.ABORT,
            state=RunState.SAFE_STOP,
        )
    if has_authorization:
        authorization_error = _validate_single_authorization(
            route, intent, observation, intent_registry, action_id=action_id,
        )
        if authorization_error is not None:
            return MvpRunResult(CampusRunState.INIT, state=RunState.SAFE_STOP)
        try:
            reservation = intent_registry.reserve_batch([intent])
        except Exception:
            return MvpRunResult(CampusRunState.INIT, state=RunState.SAFE_STOP)

    try:
        prepared = provider.prepare()
        if not getattr(prepared, "ok", True):
            return MvpRunResult(CampusRunState.INIT, state=_cleanup_state(provider))
        if hasattr(provider, "ready") and not provider.ready():
            return MvpRunResult(CampusRunState.INIT, state=_cleanup_state(provider))
        state = open_campus_run(device)
        if reservation is None:
            return MvpRunResult(state, state=_cleanup_state(provider))
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
            return MvpRunResult(state, state=_cleanup_state(provider))
        account_state = run_route_then_switch(
            provider,
            route,
            switcher,
            app_result_verified=app_result_verified,
            max_duration=intent.max_duration,
            clock=clock,
        )
        run_state = RunState.SAFE_STOP if account_state is AccountSwitchState.ABORT else RunState.IDLE
        return MvpRunResult(CampusRunState.RUNNING, account_state, run_state)
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
    if not accounts:
        return MultiRunResult()
    authorization_error = _validate_multi_account_authorization(
        accounts, route, intents, intent_registry,
    )
    if authorization_error is not None:
        return MultiRunResult(
            failed=list(accounts),
            state=RunState.SAFE_STOP,
            message=authorization_error,
        )

    try:
        reservation = intent_registry.reserve_batch(
            [intents[account][0] for account in accounts],
        )
    except Exception as exc:
        return MultiRunResult(
            failed=list(accounts),
            state=RunState.SAFE_STOP,
            message=f"invalid RunIntent authorization reservation: {exc}",
        )

    try:
        # Prepare the GPS provider once before any runs start.
        prepared = provider.prepare()
        if not getattr(prepared, "ok", True):
            _stop_safely(provider)
            return MultiRunResult(failed=list(accounts), state=RunState.SAFE_STOP)

        if hasattr(provider, "ready") and not provider.ready():
            _stop_safely(provider)
            return MultiRunResult(failed=list(accounts), state=RunState.SAFE_STOP)

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

        return run_multi_account(
            provider=provider,
            route=route,
            accounts=accounts,
            open_campus_run_fn=open_campus_run,
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
    finally:
        intent_registry.release_reservation(reservation)
