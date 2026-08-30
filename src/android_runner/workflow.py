from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .wecom.account import AccountSwitchState


class RouteProvider(Protocol):
    def start_route(self, route: Path): ...
    def stop(self): ...


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
