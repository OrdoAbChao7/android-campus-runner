from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from .intent import IntentUseRegistry, RunIntent, RunObservation
from .wecom.account import AccountSwitchState
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


def run_route_then_switch(provider: RouteProvider, route: Path, switcher) -> AccountSwitchState:
    """Run and clean up a route, then switch accounts only after success."""
    if not run_route_with_cleanup(provider, route):
        return AccountSwitchState.ABORT
    return switcher.switch()


def run_multi_account(
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
    action_id: str = "campus_run.start",
) -> MultiRunResult:
    """Run each account only after consuming its registered start authorization."""
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
            intent, observation = (intents or {}).get(account, (None, None))
            if intent is None or observation is None or intent_registry is None:
                log.error("no valid start authorization for account: %s", account)
                result.failed.append(account)
                result.state = RunState.SAFE_STOP
                break
            try:
                open_campus_run_fn(device)
                confirm_free_run_fn(
                    device,
                    intent=intent,
                    observation=observation,
                    intent_registry=intent_registry,
                    action_id=action_id,
                )
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
