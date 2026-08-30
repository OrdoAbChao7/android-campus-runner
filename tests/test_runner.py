from datetime import datetime, timedelta, timezone
from pathlib import Path

from android_runner import runner
from android_runner.intent import IntentUseRegistry, RunIntent, RunObservation, route_sha256
from android_runner.state import RunState
from android_runner.wecom.account import AccountSwitchState, SafeAccountSwitcher
from android_runner.wecom.campus_run import CampusRunState


class Device:
    def __init__(self): self.clicks = []
    def start_app(self, package): pass
    def click(self, **kwargs): self.clicks.append(kwargs)
    def wait_text(self, text, timeout=10): return True


class Provider:
    def __init__(self): self.calls = []
    def prepare(self): self.calls.append("prepare"); return type("R", (), {"ok": True})()
    def start_route(self, route): self.calls.append("route"); return type("R", (), {"ok": True})()
    def stop(self): self.calls.append("stop")


def test_mvp_stops_at_prompt_by_default(monkeypatch):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    result = runner.run_mvp(Device(), Provider(), Path("route.gpx"), SafeAccountSwitcher(lambda: None, lambda: None, lambda: True))
    assert result.campus_state is CampusRunState.START_PROMPT
    assert result.account_state is None


def test_mvp_runs_route_then_switches_when_authorized(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, allow_start: CampusRunState.RUNNING)
    provider = Provider()
    switcher = SafeAccountSwitcher(lambda: None, lambda: None, lambda: True, allow_logout=lambda: True)
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
                            observation=observation, intent_registry=registry)
    assert result.campus_state is CampusRunState.RUNNING
    assert result.account_state is AccountSwitchState.READY
    assert provider.calls == ["prepare", "route", "stop"]


def test_mvp_cleans_up_when_readiness_fails(monkeypatch):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, allow_start: CampusRunState.RUNNING)
    provider = Provider()
    provider.ready = lambda: False
    result = runner.run_mvp(Device(), provider, Path("route.gpx"), SafeAccountSwitcher(lambda: None, lambda: None, lambda: True))
    assert result.account_state is None
    assert provider.calls == ["prepare", "stop"]


def test_mvp_checks_provider_before_opening_start_prompt(monkeypatch):
    observed = []
    monkeypatch.setattr(runner, "open_campus_run", lambda device: observed.append("open"))
    provider = Provider()
    provider.ready = lambda: observed.append("ready") or False

    runner.run_mvp(Device(), provider, Path("route.gpx"), SafeAccountSwitcher(lambda: None, lambda: None, lambda: True))

    assert observed == ["ready"]
    assert provider.calls == ["prepare", "stop"]


def test_mvp_never_confirms_free_run_without_single_use_intent(monkeypatch):
    clicks = []
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, allow_start: clicks.append(allow_start))
    provider = Provider()
    provider.ready = lambda: True

    result = runner.run_mvp(Device(), provider, Path("route.gpx"), SafeAccountSwitcher(lambda: None, lambda: None, lambda: True))

    assert result.campus_state is CampusRunState.START_PROMPT
    assert clicks == []
    assert provider.calls == ["prepare", "stop"]


def test_mvp_rejects_removed_allow_start_bypass():
    with __import__("pytest").raises(TypeError):
        runner.run_mvp(
            Device(), Provider(), Path("route.gpx"),
            SafeAccountSwitcher(lambda: None, lambda: None, lambda: True), allow_start=True,
        )


def test_mvp_cleanup_verification_failure_returns_safe_stop(monkeypatch):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)

    class UnsafeProvider(Provider):
        def stop_verified(self):
            return type("Result", (), {"ok": False})()

    result = runner.run_mvp(
        Device(), UnsafeProvider(), Path("route.gpx"),
        SafeAccountSwitcher(lambda: None, lambda: None, lambda: True),
    )

    assert result.state is RunState.SAFE_STOP
