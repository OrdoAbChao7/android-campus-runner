from pathlib import Path
from datetime import datetime, timezone

from android_runner.device import WeComCheckpoint, WeComPage
from android_runner.state import RunState
from android_runner.workflow import (
    _run_multi_account_for_test,
    run_route_with_cleanup,
    run_route_then_switch,
    run_multi_account,
)
from android_runner.wecom.account import AccountSwitchState, AccountSwitcher, WeComEnterpriseSwitcher


class Provider:
    def __init__(self): self.calls = []
    def start_route(self, route): self.calls.append("start"); raise RuntimeError("route failed")
    def stop(self): self.calls.append("stop")
    def stop_verified(self): self.stop(); return type("R", (), {"ok": True})()


def test_route_cleanup_stops_provider_on_failure():
    provider = Provider()
    assert run_route_with_cleanup(provider, Path("route.gpx")) is False
    assert provider.calls == ["start", "stop"]


class SuccessProvider:
    def __init__(self): self.calls = []
    def start_route(self, route): self.calls.append("start")
    def stop(self): self.calls.append("stop")
    def stop_verified(self): self.stop(); return type("R", (), {"ok": True})()


def test_switch_happens_only_after_successful_route():
    provider = SuccessProvider()
    calls = []
    switcher = AccountSwitcher(lambda: calls.append("open"), lambda: calls.append("select"), lambda: True)
    assert run_route_then_switch(provider, Path("route.gpx"), switcher) is AccountSwitchState.ABORT
    assert provider.calls == []
    assert calls == []


def test_switch_requires_explicit_verified_app_result_after_verified_provider_stop():
    provider = SuccessProvider()

    def checkpoint(page, fingerprint):
        return WeComCheckpoint(
            screenshot_path=Path("screen.png"), hierarchy_path=Path("page.xml"),
            captured_at=datetime.now(timezone.utc), foreground_package="com.tencent.wework",
            foreground_activity="com.tencent.wework.launch.WwMainActivity", adb_serial="PHONE",
            device_fingerprint="fingerprint", page_fingerprint=fingerprint, page=page,
        )

    class Device:
        def __init__(self):
            self.calls = []
            self.checkpoints = iter((
                checkpoint(WeComPage.ACCOUNT_HOME, "a" * 64),
                checkpoint(WeComPage.ACCOUNT_SWITCHER, "b" * 64),
                checkpoint(WeComPage.ACCOUNT_HOME, "c" * 64),
            ))

        def capture_wecom_checkpoint(self, _directory): return next(self.checkpoints)
        def click(self, **kwargs): self.calls.append(kwargs)
        def wait_text(self, text, timeout=5): return text == "目标企业"

    device = Device()
    switcher = WeComEnterpriseSwitcher(
        device, "目标企业", current="当前企业",
        logged_in_enterprises=("当前企业", "目标企业"),
    )

    state = run_route_then_switch(
        provider, Path("route.gpx"), switcher, app_result_verified=lambda: True,
    )

    assert state is AccountSwitchState.READY
    assert provider.calls == ["start", "stop"]
    assert device.calls == [{"resource_id": "com.tencent.wework:id/nts"}, {"text": "目标企业"}]


def test_failed_verified_stop_blocks_completion_and_enters_safe_stop():
    class UnsafeProvider:
        def start_route(self, route):
            return type("Result", (), {"ok": True})()

        def stop_verified(self):
            return type("Result", (), {"ok": False})()

    result = _run_multi_account_for_test(
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
        _run_multi_account_for_test(
            provider=Provider(), route=Path("route.gpx"), accounts=["enterprise"],
            open_campus_run_fn=lambda device: None,
            confirm_free_run_fn=lambda device, *, allow_start: None,
            switch_account_fn=lambda account: True, device=object(),
            authorize_start=lambda account: True,
        )
