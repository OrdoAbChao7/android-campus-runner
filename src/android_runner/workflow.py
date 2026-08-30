from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from .wecom.account import AccountSwitchState

log = logging.getLogger(__name__)


class RouteProvider(Protocol):
    def start_route(self, route: Path): ...
    def stop(self): ...


@dataclass
class MultiRunResult:
    """Summary of a multi-account campus run session."""
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.completed) + len(self.failed)


def run_route_with_cleanup(provider: RouteProvider, route: Path) -> bool:
    try:
        result = provider.start_route(route)
        return bool(getattr(result, "ok", True))
    except Exception:
        return False
    finally:
        try:
            provider.stop()
        except Exception:
            pass


def run_route_then_switch(provider: RouteProvider, route: Path, switcher) -> AccountSwitchState:
    """Run and clean up a route, then switch accounts only after success."""
    if not run_route_with_cleanup(provider, route):
        return AccountSwitchState.ABORT
    return switcher.switch()


def run_route_no_stop(provider: RouteProvider, route: Path) -> bool:
    """Start a route without stopping the provider afterwards.

    GPS stays active so the next run can reuse the same mock-location session.
    """
    try:
        result = provider.start_route(route)
        return bool(getattr(result, "ok", True))
    except Exception:
        log.warning("route failed", exc_info=True)
        return False


def run_multi_account(
    provider: RouteProvider,
    route: Path,
    accounts: list[str],
    open_campus_run_fn: Callable[..., object],
    confirm_free_run_fn: Callable[..., object],
    switch_account_fn: Callable[[str], bool],
    device,
    *,
    stop_provider_on_finish: bool = True,
) -> MultiRunResult:
    """Run campus-run once per account, keeping GPS active between runs.

    For each account in *accounts*:
    1. Navigate to the WeCom campus-run start prompt.
    2. Play the GPX/KML route (GPS provider is NOT stopped between iterations).
    3. Switch to the next account (if any remain).

    The provider is stopped once after all accounts finish (or on abort),
    unless *stop_provider_on_finish* is False.
    """
    result = MultiRunResult()
    try:
        for i, account in enumerate(accounts):
            log.info("[%d/%d] starting run for account: %s", i + 1, len(accounts), account)
            try:
                open_campus_run_fn(device)
                confirm_free_run_fn(device, allow_start=True)
            except Exception as exc:
                log.error("failed to open campus run for %s: %s", account, exc)
                result.failed.append(account)
                break

            ok = run_route_no_stop(provider, route)
            if not ok:
                log.error("route failed for account: %s", account)
                result.failed.append(account)
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
    finally:
        if stop_provider_on_finish:
            try:
                provider.stop()
            except Exception:
                log.warning("provider stop failed", exc_info=True)
    return result
