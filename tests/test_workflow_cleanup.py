from pathlib import Path

from android_runner.workflow import run_route_with_cleanup, run_route_then_switch
from android_runner.wecom.account import AccountSwitchState, SafeAccountSwitcher


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
    switcher = SafeAccountSwitcher(lambda: None, lambda: None, lambda: True)
    assert run_route_then_switch(provider, Path("route.gpx"), switcher) is AccountSwitchState.ABORT
    assert provider.calls == ["start", "stop"]
