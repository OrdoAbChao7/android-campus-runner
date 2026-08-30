from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .intent import IntentUseRegistry, RunIntent, RunObservation
from .wecom.campus_run import CampusRunState, confirm_free_run, open_campus_run
from .wecom.account import WeComEnterpriseSwitcher
from .workflow import MultiRunResult, run_multi_account, run_route_then_switch


@dataclass(frozen=True)
class MvpRunResult:
    campus_state: CampusRunState
    account_state: object | None = None


def _stop_safely(provider) -> bool:
    try:
        result = provider.stop_verified() if hasattr(provider, "stop_verified") else provider.stop()
    except Exception:
        return False
    return bool(getattr(result, "ok", True))


def _consume_start_intent(
    intent: RunIntent | None,
    observation: RunObservation | None,
    registry: IntentUseRegistry | None,
    action_id: str,
) -> bool:
    if intent is None or observation is None or registry is None:
        return False
    try:
        registry.consume(intent, observation, action_id)
    except Exception:
        return False
    return True


def run_mvp(
    device,
    provider,
    route: Path,
    switcher,
    *,
    allow_start: bool = False,
    intent: RunIntent | None = None,
    observation: RunObservation | None = None,
    intent_registry: IntentUseRegistry | None = None,
    action_id: str = "campus_run.start",
) -> MvpRunResult:
    """Execute the authorized MVP flow, stopping safely at the start prompt by default."""
    prepared = provider.prepare()
    if not getattr(prepared, "ok", True):
        _stop_safely(provider)
        return MvpRunResult(CampusRunState.INIT)
    if hasattr(provider, "ready") and not provider.ready():
        _stop_safely(provider)
        return MvpRunResult(CampusRunState.INIT)
    state = open_campus_run(device)
    if not _consume_start_intent(intent, observation, intent_registry, action_id):
        _stop_safely(provider)
        return MvpRunResult(state)
    confirm_free_run(device, allow_start=True)
    account_state = run_route_then_switch(provider, route, switcher)
    return MvpRunResult(CampusRunState.RUNNING, account_state)


def run_multi_account_mvp(
    device,
    provider,
    route: Path,
    accounts: list[str],
    *,
    current_account: str | None = None,
    intents: dict[str, tuple[RunIntent, RunObservation]] | None = None,
    intent_registry: IntentUseRegistry | None = None,
) -> MultiRunResult:
    """Run campus-run sequentially for every account in *accounts*.

    The GPS provider is prepared once before the first run and kept alive
    between runs. It is stopped only after all accounts finish (or on error),
    unless *stop_provider_on_finish* is False.

    *accounts* is a list of enterprise (or account) display names that WeCom
    shows in its account-switcher. The device must already be logged in to all
    of them. Switching is done via :class:`WeComEnterpriseSwitcher`.

    *current_account* is the enterprise name currently active on the device.
    Omit it when there is only one account or you do not need the guard.
    """
    if not accounts:
        return MultiRunResult()

    # Prepare the GPS provider once before any runs start.
    prepared = provider.prepare()
    if not getattr(prepared, "ok", True):
        _stop_safely(provider)
        return MultiRunResult(failed=list(accounts))

    if hasattr(provider, "ready") and not provider.ready():
        _stop_safely(provider)
        return MultiRunResult(failed=list(accounts))

    # Build a switch function: given the next enterprise name, perform the switch.
    # Track the current enterprise in a mutable container so the closure can update it.
    _current_ref: list[str | None] = [current_account]

    def switch_to(next_enterprise: str) -> bool:
        switcher = WeComEnterpriseSwitcher(device, target=next_enterprise, current=_current_ref[0])
        state = switcher.switch()
        from .wecom.account import AccountSwitchState
        ok = state is AccountSwitchState.READY
        if ok:
            _current_ref[0] = next_enterprise
        return ok

    return run_multi_account(
        provider=provider,
        route=route,
        accounts=accounts,
        open_campus_run_fn=open_campus_run,
        confirm_free_run_fn=confirm_free_run,
        switch_account_fn=switch_to,
        device=device,
        authorize_start=lambda account: _consume_start_intent(
            *(intents or {}).get(account, (None, None)), intent_registry, "campus_run.start"
        ),
    )
