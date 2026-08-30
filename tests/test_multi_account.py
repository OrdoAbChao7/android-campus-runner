"""Tests for the multi-account campus-run flow."""
from __future__ import annotations

from pathlib import Path

import pytest

from android_runner import runner
from android_runner.wecom.campus_run import CampusRunState
from android_runner.workflow import MultiRunResult, run_multi_account


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

    def confirm_fn(device, *, allow_start=False):
        if confirm_raises:
            raise RuntimeError("confirm failed")
        return CampusRunState.RUNNING

    return open_fn, confirm_fn


def test_multi_account_single_no_switch():
    """One account: route runs, no switch needed, provider stopped."""
    provider = Provider()
    switches: list[str] = []

    result = run_multi_account(
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

    result = run_multi_account(
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

    result = run_multi_account(
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
    result = run_multi_account(
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


def test_multi_account_switch_failure_aborts_remaining():
    """If switch fails, all subsequent accounts are added to failed."""
    provider = Provider()

    result = run_multi_account(
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

    result = run_multi_account(
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


def test_multi_account_gps_not_stopped_when_keep_gps():
    """Provider.stop is NOT called when stop_provider_on_finish=False."""
    provider = Provider()

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

    assert "stop" not in provider.calls


def test_multi_account_empty_accounts_returns_empty():
    provider = Provider()
    result = run_multi_account(
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


def test_mvp_multi_account_full_flow(monkeypatch):
    """Integration: two accounts complete successfully via run_multi_account_mvp."""
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, allow_start: CampusRunState.RUNNING)

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
    result = runner.run_multi_account_mvp(
        device=Device(),
        provider=provider,
        route=Path("route.gpx"),
        accounts=["企业A", "企业B"],
        current_account="企业A",
    )

    assert result.completed == ["企业A", "企业B"]
    assert result.failed == []
    assert switched_to == ["企业B"]
    assert provider.calls.count("route") == 2
    assert provider.calls[-1] == "stop"
