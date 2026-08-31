from pathlib import Path

from android_runner.state import RunState
from android_runner.workflow import run_route_with_cleanup, run_route_then_switch, run_multi_account
from android_runner.wecom.account import AccountSwitchState, AccountSwitcher


class Provider:
    def __init__(self): self.calls = []
    def start_route(self, route): self.calls.append("start"); raise RuntimeError("route failed")
    def stop(self): self.calls.append("stop")


def test_route_cleanup_stops_provider_on_failure():
    provider = Provider()
    assert run_route_with_cleanup(provider, Path("route.gpx")) is False
    assert provider.calls == ["start", "stop"]


class SuccessProvider:
    def __init__(self): self.calls = []
    def start_route(self, route): self.calls.append("start")
    def stop(self): self.calls.append("stop")


def test_switch_happens_only_after_successful_route():
    provider = SuccessProvider()
    switcher = AccountSwitcher(lambda: None, lambda: None, lambda: True)
    assert run_route_then_switch(provider, Path("route.gpx"), switcher) is AccountSwitchState.ABORT
    assert provider.calls == ["start", "stop"]


def test_switch_requires_explicit_verified_app_result_after_verified_provider_stop():
    provider = SuccessProvider()
    calls = []
    switcher = AccountSwitcher(lambda: calls.append("open"), lambda: calls.append("select"), lambda: True)

    state = run_route_then_switch(
        provider, Path("route.gpx"), switcher, app_result_verified=lambda: True,
    )

    assert state is AccountSwitchState.READY
    assert provider.calls == ["start", "stop"]
    assert calls == ["open", "select"]


def test_failed_verified_stop_blocks_completion_and_enters_safe_stop():
    class UnsafeProvider:
        def start_route(self, route):
            return type("Result", (), {"ok": True})()

        def stop_verified(self):
            return type("Result", (), {"ok": False})()

    result = run_multi_account(
        provider=UnsafeProvider(),
        route=Path("route.gpx"),
        accounts=["enterprise"],
        open_campus_run_fn=lambda device: None,
        confirm_free_run_fn=lambda device, *, allow_start: None,
        switch_account_fn=lambda account: True,
        device=object(),
    )

    assert result.completed == []
    assert result.failed == ["enterprise"]
    assert result.state is RunState.SAFE_STOP


def test_workflow_rejects_authorize_callback_bypass():
    with __import__("pytest").raises(TypeError):
        run_multi_account(
            provider=Provider(), route=Path("route.gpx"), accounts=["enterprise"],
            open_campus_run_fn=lambda device: None,
            confirm_free_run_fn=lambda device, *, allow_start: None,
            switch_account_fn=lambda account: True, device=object(),
            authorize_start=lambda account: True,
        )
