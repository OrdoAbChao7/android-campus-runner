"""Tests for the multi-account campus-run flow."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from android_runner import runner
from android_runner.intent import IntentUseRegistry, RunIntent, RunObservation, route_sha256
from android_runner.wecom.campus_run import CampusRunState
from android_runner.workflow import MultiRunResult, run_multi_account
from android_runner.state import RunState


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

class Device:
    def __init__(self):
        self.clicks = []
        self.started_apps = []

    def start_app(self, package):
        self.started_apps.append(package)

    def click(self, **kwargs):
        self.clicks.append(kwargs)

    def wait_text(self, text, timeout=10):
        return True


class Provider:
    def __init__(self, route_ok: bool = True):
        self.calls: list[str] = []
        self._route_ok = route_ok

    def prepare(self):
        self.calls.append("prepare")
        return type("R", (), {"ok": True})()

    def start_route(self, route):
        self.calls.append("route")
        return type("R", (), {"ok": self._route_ok})()

    def stop(self):
        self.calls.append("stop")


# ---------------------------------------------------------------------------
# run_multi_account (workflow layer)
# ---------------------------------------------------------------------------

def _make_fns(open_raises=False, confirm_raises=False):
    """Return stub open/confirm callables."""
    def open_fn(device):
        if open_raises:
            raise RuntimeError("open failed")
        return CampusRunState.START_PROMPT

    def confirm_fn(device, **kwargs):
        if confirm_raises:
            raise RuntimeError("confirm failed")
        return CampusRunState.RUNNING

    return open_fn, confirm_fn


def _start_authorizations(accounts: list[str]):
    now = datetime.now(timezone.utc)
    registry = IntentUseRegistry()
    intents = {}
    for number, account in enumerate(accounts, start=1):
        intent = RunIntent(
            intent_id=f"direct-{number}", adb_serial="PHONE", device_fingerprint="fingerprint",
            current_enterprise=account, target_enterprise=account, route_sha256="0" * 64,
            not_before=now - timedelta(minutes=1), not_after=now + timedelta(minutes=1),
            max_duration=timedelta(minutes=30), allowed_action_ids={"campus_run.start"},
        )
        registry.register(intent)
        intents[account] = (intent, RunObservation("PHONE", "fingerprint", "0" * 64, now))
    return intents, registry


def _run_multi_authorized(**kwargs):
    intents, registry = _start_authorizations(kwargs["accounts"])
    return run_multi_account(**kwargs, intents=intents, intent_registry=registry)


def test_multi_account_single_no_switch():
    """One account: route runs, no switch needed, provider stopped."""
    provider = Provider()
    switches: list[str] = []

    result = _run_multi_authorized(
        provider=provider,
        route=Path("route.gpx"),
        accounts=["企业A"],
        open_campus_run_fn=_make_fns()[0],
        confirm_free_run_fn=_make_fns()[1],
        switch_account_fn=lambda name: switches.append(name) or True,
        device=Device(),
    )

    assert result.completed == ["企业A"]
    assert result.failed == []
    assert switches == []  # no switch for single account
    assert "route" in provider.calls
    assert provider.calls[-1] == "stop"


def test_multi_account_two_accounts_switches_once():
    """Two accounts: route runs twice, switch happens once between them."""
    provider = Provider()
    switches: list[str] = []

    result = _run_multi_authorized(
        provider=provider,
        route=Path("route.gpx"),
        accounts=["企业A", "企业B"],
        open_campus_run_fn=_make_fns()[0],
        confirm_free_run_fn=_make_fns()[1],
        switch_account_fn=lambda name: switches.append(name) or True,
        device=Device(),
    )

    assert result.completed == ["企业A", "企业B"]
    assert result.failed == []
    assert switches == ["企业B"]
    assert provider.calls.count("route") == 2
    assert provider.calls[-1] == "stop"


def test_multi_account_three_accounts_two_switches():
    """Three accounts: route runs three times, switch happens twice."""
    provider = Provider()
    switches: list[str] = []

    result = _run_multi_authorized(
        provider=provider,
        route=Path("route.gpx"),
        accounts=["企业A", "企业B", "企业C"],
        open_campus_run_fn=_make_fns()[0],
        confirm_free_run_fn=_make_fns()[1],
        switch_account_fn=lambda name: switches.append(name) or True,
        device=Device(),
    )

    assert result.completed == ["企业A", "企业B", "企业C"]
    assert switches == ["企业B", "企业C"]
    assert provider.calls.count("route") == 3
    assert provider.calls[-1] == "stop"


def test_multi_account_route_failure_aborts_remaining():
    """If the route fails for account B, remaining accounts are marked failed."""
    call_count = 0

    class FailOnSecond(Provider):
        def start_route(self, route):
            nonlocal call_count
            call_count += 1
            self.calls.append("route")
            ok = call_count != 2  # fail on second call
            return type("R", (), {"ok": ok})()

    provider = FailOnSecond()
    result = _run_multi_authorized(
        provider=provider,
        route=Path("route.gpx"),
        accounts=["企业A", "企业B", "企业C"],
        open_campus_run_fn=_make_fns()[0],
        confirm_free_run_fn=_make_fns()[1],
        switch_account_fn=lambda name: True,
        device=Device(),
    )

    assert result.completed == ["企业A"]
    assert result.failed == ["企业B"]  # C is never attempted after abort
    assert provider.calls[-1] == "stop"


def test_failed_stop_after_first_account_blocks_next_account():
    class StopFailsAfterRoute(Provider):
        def stop_verified(self):
            self.calls.append("verified-stop")
            return type("R", (), {"ok": False})()

    provider = StopFailsAfterRoute()
    confirmations = []
    result = _run_multi_authorized(
        provider=provider,
        route=Path("route.gpx"),
        accounts=["企业A", "企业B"],
        open_campus_run_fn=lambda device: None,
        confirm_free_run_fn=lambda device, **kwargs: confirmations.append(kwargs["intent"].intent_id),
        switch_account_fn=lambda name: True,
        device=Device(),
    )

    assert confirmations == ["direct-1"]
    assert provider.calls.count("route") == 1
    assert result.completed == []
    assert result.state is RunState.SAFE_STOP


def test_multi_account_switch_failure_aborts_remaining():
    """If switch fails, all subsequent accounts are added to failed."""
    provider = Provider()

    result = _run_multi_authorized(
        provider=provider,
        route=Path("route.gpx"),
        accounts=["企业A", "企业B", "企业C"],
        open_campus_run_fn=_make_fns()[0],
        confirm_free_run_fn=_make_fns()[1],
        switch_account_fn=lambda name: False,  # always fails
        device=Device(),
    )

    assert result.completed == ["企业A"]
    assert set(result.failed) == {"企业B", "企业C"}
    assert provider.calls[-1] == "stop"


def test_multi_account_open_failure_aborts():
    """If open_campus_run raises, current account is failed and loop stops."""
    provider = Provider()

    result = _run_multi_authorized(
        provider=provider,
        route=Path("route.gpx"),
        accounts=["企业A", "企业B"],
        open_campus_run_fn=_make_fns(open_raises=True)[0],
        confirm_free_run_fn=_make_fns()[1],
        switch_account_fn=lambda name: True,
        device=Device(),
    )

    assert result.completed == []
    assert "企业A" in result.failed
    assert provider.calls[-1] == "stop"


def test_multi_account_rejects_keep_gps_execution_path():
    """A production flow cannot opt out of verified provider shutdown."""
    provider = Provider()

    with pytest.raises(TypeError):
        run_multi_account(
            provider=provider,
            route=Path("route.gpx"),
            accounts=["企业A"],
            open_campus_run_fn=_make_fns()[0],
            confirm_free_run_fn=_make_fns()[1],
            switch_account_fn=lambda name: True,
            device=Device(),
            stop_provider_on_finish=False,
        )


def test_multi_account_empty_accounts_returns_empty():
    provider = Provider()
    result = _run_multi_authorized(
        provider=provider,
        route=Path("route.gpx"),
        accounts=[],
        open_campus_run_fn=_make_fns()[0],
        confirm_free_run_fn=_make_fns()[1],
        switch_account_fn=lambda name: True,
        device=Device(),
    )
    assert result.completed == []
    assert result.failed == []


# ---------------------------------------------------------------------------
# run_multi_account_mvp (runner layer)
# ---------------------------------------------------------------------------

def test_mvp_multi_account_prepare_failure_marks_all_failed(monkeypatch):
    class BadProvider(Provider):
        def prepare(self):
            self.calls.append("prepare")
            return type("R", (), {"ok": False})()

    provider = BadProvider()
    result = runner.run_multi_account_mvp(
        device=Device(),
        provider=provider,
        route=Path("route.gpx"),
        accounts=["企业A", "企业B"],
    )
    assert result.failed == ["企业A", "企业B"]
    assert result.completed == []


def test_mvp_multi_account_cleanup_failure_returns_safe_stop():
    class BadProvider(Provider):
        def prepare(self):
            return type("R", (), {"ok": False})()

        def stop_verified(self):
            return type("R", (), {"ok": False})()

    result = runner.run_multi_account_mvp(
        device=Device(), provider=BadProvider(), route=Path("route.gpx"), accounts=["企业A"],
    )

    assert result.state is RunState.SAFE_STOP


def test_mvp_multi_account_ready_check_failure_marks_all_failed(monkeypatch):
    class NotReadyProvider(Provider):
        def ready(self):
            return False

    provider = NotReadyProvider()
    result = runner.run_multi_account_mvp(
        device=Device(),
        provider=provider,
        route=Path("route.gpx"),
        accounts=["企业A"],
    )
    assert result.failed == ["企业A"]
    assert result.completed == []


def test_mvp_multi_account_full_flow(monkeypatch, tmp_path):
    """Integration: two accounts complete successfully via run_multi_account_mvp."""
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, **kwargs: CampusRunState.RUNNING)

    switched_to: list[str] = []

    class FakeSwitcher:
        from android_runner.wecom.account import AccountSwitchState as _S

        def __init__(self, device, target, current=None):
            self.target = target

        def switch(self):
            switched_to.append(self.target)
            from android_runner.wecom.account import AccountSwitchState
            return AccountSwitchState.READY

    monkeypatch.setattr(runner, "WeComEnterpriseSwitcher", FakeSwitcher)

    provider = Provider()
    route = tmp_path / "route.gpx"
    route.write_text("route", encoding="utf-8")
    now = datetime.now(timezone.utc)
    registry = IntentUseRegistry()
    intents = {}
    for number, account in enumerate(["企业A", "企业B"], start=1):
        intent = RunIntent(
            intent_id=f"intent-{number}", adb_serial="PHONE", device_fingerprint="fingerprint",
            current_enterprise=account, target_enterprise=account, route_sha256=route_sha256(route),
            not_before=now - timedelta(minutes=1), not_after=now + timedelta(minutes=1),
            max_duration=timedelta(minutes=30), allowed_action_ids={"campus_run.start"},
        )
        registry.register(intent)
        intents[account] = (intent, RunObservation("PHONE", "fingerprint", route_sha256(route), now))
    result = runner.run_multi_account_mvp(
        device=Device(),
        provider=provider,
        route=route,
        accounts=["企业A", "企业B"],
        current_account="企业A",
        intents=intents,
        intent_registry=registry,
    )

    assert result.completed == ["企业A", "企业B"]
    assert result.failed == []
    assert switched_to == ["企业B"]
    assert provider.calls.count("route") == 2
    assert provider.calls[-1] == "stop"
