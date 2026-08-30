from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .wecom.campus_run import CampusRunState, confirm_free_run, open_campus_run
from .workflow import run_route_then_switch


@dataclass(frozen=True)
class MvpRunResult:
    campus_state: CampusRunState
    account_state: object | None = None


def run_mvp(device, provider, route: Path, switcher, *, allow_start: bool = False) -> MvpRunResult:
    """Execute the authorized MVP flow, stopping safely at the start prompt by default."""
    state = open_campus_run(device)
    if not allow_start:
        return MvpRunResult(state)
    confirm_free_run(device, allow_start=True)
    prepared = provider.prepare()
    if not getattr(prepared, "ok", True):
        try:
            provider.stop()
        except Exception:
            pass
        return MvpRunResult(CampusRunState.RUNNING)
    if hasattr(provider, "ready") and not provider.ready():
        try:
            provider.stop()
        except Exception:
            pass
        return MvpRunResult(CampusRunState.RUNNING)
    account_state = run_route_then_switch(provider, route, switcher)
    return MvpRunResult(CampusRunState.RUNNING, account_state)
