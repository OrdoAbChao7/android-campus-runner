from pathlib import Path

from android_runner import runner
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


def test_mvp_runs_route_then_switches_when_authorized(monkeypatch):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, allow_start: CampusRunState.RUNNING)
    provider = Provider()
    switcher = SafeAccountSwitcher(lambda: None, lambda: None, lambda: True, allow_logout=lambda: True)
    result = runner.run_mvp(Device(), provider, Path("route.gpx"), switcher, allow_start=True)
    assert result.campus_state is CampusRunState.RUNNING
    assert result.account_state is AccountSwitchState.READY
    assert provider.calls == ["prepare", "route", "stop"]


def test_mvp_cleans_up_when_readiness_fails(monkeypatch):
    monkeypatch.setattr(runner, "open_campus_run", lambda device: CampusRunState.START_PROMPT)
    monkeypatch.setattr(runner, "confirm_free_run", lambda device, allow_start: CampusRunState.RUNNING)
    provider = Provider()
    provider.ready = lambda: False
    result = runner.run_mvp(Device(), provider, Path("route.gpx"), SafeAccountSwitcher(lambda: None, lambda: None, lambda: True), allow_start=True)
    assert result.account_state is None
    assert provider.calls == ["prepare", "stop"]
