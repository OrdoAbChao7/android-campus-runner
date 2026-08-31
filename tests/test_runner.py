from datetime import datetime, timedelta, timezone
from pathlib import Path

from android_runner import runner
from android_runner.device import WeComCheckpoint, WeComPage
from android_runner.intent import IntentReplayError, IntentUseRegistry, RunIntent, RunObservation, route_sha256
from android_runner.state import RunState
from android_runner.wecom.account import AccountSwitchState, AccountSwitcher, WeComEnterpriseSwitcher
from android_runner.wecom.campus_run import CampusRunState


class Device:
    def __init__(self): self.clicks = []
    def start_app(self, package): pass
    def click(self, **kwargs): self.clicks.append(kwargs)
    def wait_text(self, text, timeout=10): return True
    def capture_wecom_checkpoint(self, _directory):
        return WeComCheckpoint(
            screenshot_path=Path("screen.png"), hierarchy_path=Path("page.xml"),
            captured_at=datetime.now(timezone.utc), foreground_package="com.tencent.wework",
            foreground_activity="com.tencent.wework.launch.WwMainActivity", adb_serial="PHONE",
            device_fingerprint="fingerprint", page_fingerprint="a" * 64,
            page=WeComPage.START_PROMPT, enterprise_identity="target",
        )


class Provider:
    def __init__(self): self.calls = []
    def prepare(self): self.calls.append("prepare"); return type("R", (), {"ok": True})()
    def start_route(self, route): self.calls.append("route"); return type("R", (), {"ok": True})()
    def stop(self): self.calls.append("stop")
    def stop_verified(self): self.stop(); return type("R", (), {"ok": True})()


def test_mvp_rejects_generic_switcher_without_authorization_before_provider_or_ui(monkeypatch):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    provider = Provider()
    result = runner.run_mvp(Device(), provider, Path("route.gpx"), AccountSwitcher(lambda: None, lambda: None, lambda: True))
    assert result.campus_state is CampusRunState.INIT
    assert result.account_state is AccountSwitchState.ABORT
    assert result.state is RunState.SAFE_STOP
    assert provider.calls == []


def test_mvp_rejects_generic_callback_switcher_before_provider_or_ui_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, **kwargs: CampusRunState.RUNNING)
    provider = Provider()
    switcher = AccountSwitcher(lambda: None, lambda: None, lambda: True)
    route = tmp_path / "route.gpx"
    route.write_text("route", encoding="utf-8")
    now = datetime.now(timezone.utc)
    intent = RunIntent(
        intent_id="one", adb_serial="PHONE", device_fingerprint="fingerprint",
        current_enterprise="current", target_enterprise="target", route_sha256=route_sha256(route),
        not_before=now - timedelta(minutes=1), not_after=now + timedelta(minutes=1),
        max_duration=timedelta(minutes=30), allowed_action_ids={"campus_run.start"},
    )
    observation = RunObservation("PHONE", "fingerprint", route_sha256(route), now)
    registry = IntentUseRegistry()
    registry.register(intent)
    result = runner.run_mvp(Device(), provider, route, switcher, intent=intent,
                            observation=observation, intent_registry=registry,
                            app_result_verified=lambda: True)
    assert result.campus_state is CampusRunState.INIT
    assert result.account_state is AccountSwitchState.ABORT
    assert result.state is RunState.SAFE_STOP
    assert provider.calls == []


def test_mvp_authorized_run_reserves_before_ui_and_releases_on_ui_failure(monkeypatch, tmp_path):
    route = tmp_path / "route.gpx"
    route.write_text("route", encoding="utf-8")
    now = datetime.now(timezone.utc)
    intent = RunIntent(
        intent_id="single-reservation", adb_serial="PHONE", device_fingerprint="fingerprint",
        current_enterprise="current", target_enterprise="target", route_sha256=route_sha256(route),
        not_before=now - timedelta(minutes=1), not_after=now + timedelta(minutes=1),
        max_duration=timedelta(minutes=30), allowed_action_ids={"campus_run.start"},
    )
    observation = RunObservation("PHONE", "fingerprint", route_sha256(route), now)
    registry = IntentUseRegistry()
    registry.register(intent)
    reservation_seen = []

    def open_campus_run(_device):
        try:
            registry.reserve_batch([intent])
        except IntentReplayError:
            reservation_seen.append(True)
        else:
            reservation_seen.append(False)
        return CampusRunState.START_PROMPT

    monkeypatch.setattr(runner, "open_campus_run", open_campus_run)
    monkeypatch.setattr(runner, "confirm_free_run", lambda _device, **_kwargs: (_ for _ in ()).throw(RuntimeError("UI fingerprint mismatch")))
    result = runner.run_mvp(
        Device(), Provider(), route,
        WeComEnterpriseSwitcher(
            Device(), "target", current="current",
            logged_in_enterprises=("current", "target"),
        ),
        intent=intent, observation=observation, intent_registry=registry,
    )

    assert result.state is RunState.IDLE
    assert reservation_seen == [True]
    retry = registry.reserve_batch([intent])
    registry.release_reservation(retry)


def test_mvp_cleans_up_when_readiness_fails(monkeypatch):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, **kwargs: CampusRunState.RUNNING)
    provider = Provider()
    provider.ready = lambda: False
    result = runner.run_mvp(Device(), provider, Path("route.gpx"), AccountSwitcher(lambda: None, lambda: None, lambda: True))
    assert result.account_state is AccountSwitchState.ABORT
    assert result.state is RunState.SAFE_STOP
    assert provider.calls == []


def test_mvp_checks_provider_before_opening_start_prompt(monkeypatch):
    observed = []
    monkeypatch.setattr(runner, "open_campus_run", lambda device: observed.append("open"))
    provider = Provider()
    provider.ready = lambda: observed.append("ready") or False

    runner.run_mvp(Device(), provider, Path("route.gpx"), AccountSwitcher(lambda: None, lambda: None, lambda: True))

    assert observed == []
    assert provider.calls == []


def test_mvp_never_confirms_free_run_without_single_use_intent(monkeypatch):
    clicks = []
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, **kwargs: clicks.append(kwargs))
    provider = Provider()
    provider.ready = lambda: True

    result = runner.run_mvp(Device(), provider, Path("route.gpx"), AccountSwitcher(lambda: None, lambda: None, lambda: True))

    assert result.campus_state is CampusRunState.INIT
    assert result.state is RunState.SAFE_STOP
    assert clicks == []
    assert provider.calls == []


def test_mvp_rejects_removed_allow_start_bypass():
    with __import__("pytest").raises(TypeError):
        runner.run_mvp(
            Device(), Provider(), Path("route.gpx"),
            AccountSwitcher(lambda: None, lambda: None, lambda: True), allow_start=True,
        )


def test_mvp_cleanup_verification_failure_returns_safe_stop(monkeypatch):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)

    class UnsafeProvider(Provider):
        def stop_verified(self):
            return type("Result", (), {"ok": False})()

    result = runner.run_mvp(
        Device(), UnsafeProvider(), Path("route.gpx"),
        AccountSwitcher(lambda: None, lambda: None, lambda: True),
    )

    assert result.state is RunState.SAFE_STOP


def test_mvp_verified_stop_failure_after_authorized_route_returns_safe_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, **kwargs: CampusRunState.RUNNING)

    class UnsafeProvider(Provider):
        def stop_verified(self):
            return type("Result", (), {"ok": False})()

    route = tmp_path / "route.gpx"
    route.write_text("route", encoding="utf-8")
    now = datetime.now(timezone.utc)
    intent = RunIntent(
        intent_id="stop-fails", adb_serial="PHONE", device_fingerprint="fingerprint",
        current_enterprise="current", target_enterprise="target", route_sha256=route_sha256(route),
        not_before=now - timedelta(minutes=1), not_after=now + timedelta(minutes=1),
        max_duration=timedelta(minutes=30), allowed_action_ids={"campus_run.start"},
    )
    registry = IntentUseRegistry()
    registry.register(intent)
    result = runner.run_mvp(
        Device(), UnsafeProvider(), route,
        WeComEnterpriseSwitcher(
            Device(), "target", current="current",
            logged_in_enterprises=("current", "target"),
        ),
        intent=intent,
        observation=RunObservation("PHONE", "fingerprint", route_sha256(route), now),
        intent_registry=registry,
    )

    assert result.campus_state is CampusRunState.RUNNING
    assert result.state is RunState.SAFE_STOP
