from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .intent import IntentUseRegistry, RunIntent, RunObservation
from .state import RunState
from .wecom.campus_run import CampusRunState, confirm_free_run, open_campus_run
from .wecom.account import AccountSwitchState, WeComEnterpriseSwitcher
from .workflow import MultiRunResult, run_multi_account, run_route_then_switch


@dataclass(frozen=True)
class MvpRunResult:
    campus_state: CampusRunState
    account_state: object | None = None
    state: RunState = RunState.IDLE


def _stop_safely(provider) -> bool:
    try:
        result = provider.stop_verified() if hasattr(provider, "stop_verified") else provider.stop()
    except Exception:
        return False
    return bool(getattr(result, "ok", True))


def _cleanup_state(provider) -> RunState:
    return RunState.IDLE if _stop_safely(provider) else RunState.SAFE_STOP


def _validate_multi_account_authorization(
    accounts: list[str],
    intents: dict[str, tuple[RunIntent, RunObservation]] | None,
    intent_registry: IntentUseRegistry | None,
    *,
    action_id: str = "campus_run.start",
) -> str | None:
    """Validate every account binding before touching the provider or device UI."""
    if not isinstance(intent_registry, IntentUseRegistry) or not callable(
        getattr(intent_registry, "consume", None)
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
        if intent.current_enterprise != account or intent.target_enterprise != account:
            invalid.append(f"{account} (enterprise binding mismatch)")
            continue
        try:
            intent.validate(observation, action_id)
        except Exception as exc:
            invalid.append(f"{account} ({exc})")

    if invalid:
        return "invalid RunIntent authorization before provider/UI actions: " + "; ".join(invalid)
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
) -> MvpRunResult:
    """Execute the authorized MVP flow, stopping safely at the start prompt by default."""
    prepared = provider.prepare()
    if not getattr(prepared, "ok", True):
        return MvpRunResult(CampusRunState.INIT, state=_cleanup_state(provider))
    if hasattr(provider, "ready") and not provider.ready():
        return MvpRunResult(CampusRunState.INIT, state=_cleanup_state(provider))
    state = open_campus_run(device)
    if intent is None or observation is None or intent_registry is None:
        return MvpRunResult(state, state=_cleanup_state(provider))
    try:
        confirm_free_run(
            device,
            intent=intent,
            observation=observation,
            intent_registry=intent_registry,
            action_id=action_id,
        )
    except Exception:
        return MvpRunResult(state, state=_cleanup_state(provider))
    account_state = run_route_then_switch(provider, route, switcher)
    run_state = RunState.SAFE_STOP if account_state is AccountSwitchState.ABORT else RunState.IDLE
    return MvpRunResult(CampusRunState.RUNNING, account_state, run_state)


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

    The GPS provider is prepared once before the first run and is always
    stopped with verification after completion or an error.

    *accounts* is a list of enterprise (or account) display names that WeCom
    shows in its account-switcher. The device must already be logged in to all
    of them. Switching is done via :class:`WeComEnterpriseSwitcher`.

    *current_account* is the enterprise name currently active on the device.
    Omit it when there is only one account or you do not need the guard.
    """
    if not accounts:
        return MultiRunResult()
    authorization_error = _validate_multi_account_authorization(
        accounts, intents, intent_registry,
    )
    if authorization_error is not None:
        return MultiRunResult(
            failed=list(accounts),
            state=RunState.SAFE_STOP,
            message=authorization_error,
        )

    # Prepare the GPS provider once before any runs start.
    prepared = provider.prepare()
    if not getattr(prepared, "ok", True):
        _stop_safely(provider)
        return MultiRunResult(failed=list(accounts), state=RunState.SAFE_STOP)

    if hasattr(provider, "ready") and not provider.ready():
        _stop_safely(provider)
        return MultiRunResult(failed=list(accounts), state=RunState.SAFE_STOP)

    # Build a switch function: given the next enterprise name, perform the switch.
    # Track the current enterprise in a mutable container so the closure can update it.
    _current_ref: list[str | None] = [current_account]

    def switch_to(next_enterprise: str) -> bool:
        switcher = WeComEnterpriseSwitcher(device, target=next_enterprise, current=_current_ref[0])
        state = switcher.switch()
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
        intents=intents,
        intent_registry=intent_registry,
    )
