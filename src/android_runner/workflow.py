from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from .intent import IntentReservation, IntentUseRegistry, IntentValidationError, RunIntent, RunObservation
from .wecom.account import (
    AccountSwitchState,
    WeComEnterpriseSwitcher,
    WeComEnterpriseSwitchCapability,
)
from .state import RunState

log = logging.getLogger(__name__)


class RouteProvider(Protocol):
    def start_route(self, route: Path): ...
    def stop(self): ...
    def stop_verified(self): ...


@dataclass
class MultiRunResult:
    """Summary of a multi-account campus run session."""
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    state: RunState = RunState.IDLE
    message: str | None = None

    @property
    def total(self) -> int:
        return len(self.completed) + len(self.failed)


def stop_provider_verified(provider: RouteProvider) -> bool:
    """Use the verified GPS Locator shutdown when available for production."""
    try:
        result = provider.stop_verified() if hasattr(provider, "stop_verified") else provider.stop()
    except Exception:
        log.warning("provider stop failed", exc_info=True)
        return False
    return bool(getattr(result, "ok", True))


def run_route_with_cleanup(provider: RouteProvider, route: Path) -> bool:
    route_ok = False
    try:
        result = provider.start_route(route)
        route_ok = bool(getattr(result, "ok", True))
    except Exception:
        log.warning("route failed", exc_info=True)
    stopped = stop_provider_verified(provider)
    return route_ok and stopped


def run_route_then_switch(
    provider: RouteProvider,
    route: Path,
    switcher,
    *,
    app_result_verified: Callable[[], bool] | None = None,
) -> AccountSwitchState:
    """Switch only after independent app-result proof and verified provider shutdown."""
    if not isinstance(switcher, WeComEnterpriseSwitcher):
        return AccountSwitchState.ABORT
    if not run_route_with_cleanup(provider, route):
        return AccountSwitchState.ABORT
    if app_result_verified is None:
        return AccountSwitchState.ABORT
    try:
        if not app_result_verified():
            return AccountSwitchState.ABORT
    except Exception:
        log.warning("app result verification failed", exc_info=True)
        return AccountSwitchState.ABORT
    return switcher.switch()


def _validate_active_multi_account_reservation(
    accounts: list[str],
    intents: dict[str, tuple[RunIntent, RunObservation]] | None,
    intent_registry: IntentUseRegistry | None,
    reservation: IntentReservation | None,
    *,
    action_id: str,
) -> str | None:
    """Reject before provider/UI actions unless this run owns every authorization."""
    if not isinstance(intent_registry, IntentUseRegistry):
        return "IntentUseRegistry is required"
    if not isinstance(reservation, IntentReservation):
        return "active IntentReservation is required"
    if not isinstance(intents, dict):
        return "account intent mapping is required"

    intent_batch: list[RunIntent] = []
    for account in accounts:
        binding = intents.get(account)
        if not isinstance(binding, tuple) or len(binding) != 2:
            return f"valid authorization is required for account: {account}"
        intent, observation = binding
        if not isinstance(intent, RunIntent) or not isinstance(observation, RunObservation):
            return f"valid authorization is required for account: {account}"
        try:
            intent.validate(observation, action_id)
        except IntentValidationError as exc:
            return f"invalid authorization for account {account}: {exc}"
        intent_batch.append(intent)

    try:
        intent_registry.validate_active_reservation(reservation, intent_batch)
    except Exception as exc:
        return f"invalid active IntentReservation: {exc}"
    return None


def _run_multi_account_for_test(
    provider: RouteProvider,
    route: Path,
    accounts: list[str],
    open_campus_run_fn: Callable[..., object],
    confirm_free_run_fn: Callable[..., object],
    switch_account_fn: Callable[[str], bool],
    device,
    *,
    intents: dict[str, tuple[RunIntent, RunObservation]] | None = None,
    intent_registry: IntentUseRegistry | None = None,
    reservation: IntentReservation | None = None,
    action_id: str = "campus_run.start",
    app_result_verified_fn: Callable[[str], bool] | None = None,
) -> MultiRunResult:
    """Run each account only after consuming its registered start authorization."""
    if not accounts:
        return MultiRunResult()
    authorization_error = _validate_active_multi_account_reservation(
        accounts,
        intents,
        intent_registry,
        reservation,
        action_id=action_id,
    )
    if authorization_error is not None:
        return MultiRunResult(
            failed=list(accounts),
            state=RunState.SAFE_STOP,
            message=authorization_error,
        )

    result = MultiRunResult()
    needs_cleanup = True
    try:
        for i, account in enumerate(accounts):
            log.info("[%d/%d] starting run for account: %s", i + 1, len(accounts), account)
            if i and hasattr(provider, "prepare"):
                prepared = provider.prepare()
                if not getattr(prepared, "ok", True):
                    result.failed.append(account)
                    result.state = RunState.SAFE_STOP
                    break
            if hasattr(provider, "ready") and not provider.ready():
                result.failed.append(account)
                result.state = RunState.SAFE_STOP
                break
            intent, observation = intents[account]
            try:
                open_campus_run_fn(device)
                confirm_kwargs = {
                    "intent": intent,
                    "observation": observation,
                    "intent_registry": intent_registry,
                    "action_id": action_id,
                }
                confirm_kwargs["reservation"] = reservation
                confirm_free_run_fn(device, **confirm_kwargs)
            except Exception as exc:
                log.error("failed to open campus run for %s: %s", account, exc)
                result.failed.append(account)
                break

            try:
                route_result = provider.start_route(route)
                ok = bool(getattr(route_result, "ok", True))
            except Exception:
                log.warning("route failed", exc_info=True)
                ok = False
            if not ok:
                log.error("route failed for account: %s", account)
                result.failed.append(account)
                break

            stopped = stop_provider_verified(provider)
            needs_cleanup = False
            if not stopped:
                log.error("provider stop verification failed for account: %s", account)
                result.failed.append(account)
                result.state = RunState.SAFE_STOP
                break

            log.info("run completed for account: %s", account)
            result.completed.append(account)

            # Switch to next account if there are more runs to do.
            if i < len(accounts) - 1:
                next_account = accounts[i + 1]
                if app_result_verified_fn is None:
                    log.error("missing app-result proof; refusing account switch")
                    result.failed.extend(accounts[i + 1:])
                    result.state = RunState.SAFE_STOP
                    break
                try:
                    app_result_verified = bool(app_result_verified_fn(account))
                except Exception:
                    log.warning("app result verification failed for account: %s", account, exc_info=True)
                    app_result_verified = False
                if not app_result_verified:
                    log.error("app result verification failed; refusing account switch")
                    result.failed.extend(accounts[i + 1:])
                    result.state = RunState.SAFE_STOP
                    break
                log.info("switching account: %s -> %s", account, next_account)
                switched = switch_account_fn(next_account)
                if not switched:
                    log.error("account switch failed, aborting remaining runs")
                    result.failed.extend(accounts[i + 1:])
                    break
                needs_cleanup = True
    finally:
        if needs_cleanup and not stop_provider_verified(provider):
            result.completed.clear()
            if accounts:
                result.failed = list(dict.fromkeys(result.failed + accounts))
            result.state = RunState.SAFE_STOP
    return result


def run_multi_account(
    provider: RouteProvider,
    route: Path,
    accounts: list[str],
    open_campus_run_fn: Callable[..., object],
    confirm_free_run_fn: Callable[..., object],
    device,
    *,
    switcher_capability: WeComEnterpriseSwitchCapability | None = None,
    intents: dict[str, tuple[RunIntent, RunObservation]] | None = None,
    intent_registry: IntentUseRegistry | None = None,
    reservation: IntentReservation | None = None,
    action_id: str = "campus_run.start",
    app_result_verified_fn: Callable[[str], bool] | None = None,
) -> MultiRunResult:
    """Production multi-account flow using only a guarded WeCom capability."""
    if not isinstance(switcher_capability, WeComEnterpriseSwitchCapability):
        return MultiRunResult(
            failed=list(accounts),
            state=RunState.SAFE_STOP,
            message="WeComEnterpriseSwitchCapability is required",
        )
    return _run_multi_account_for_test(
        provider=provider,
        route=route,
        accounts=accounts,
        open_campus_run_fn=open_campus_run_fn,
        confirm_free_run_fn=confirm_free_run_fn,
        switch_account_fn=switcher_capability.switch_to,
        device=device,
        intents=intents,
        intent_registry=intent_registry,
        reservation=reservation,
        action_id=action_id,
        app_result_verified_fn=app_result_verified_fn,
    )
