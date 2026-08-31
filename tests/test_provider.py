from android_runner.location.provider import GpsLocatorProvider, ProviderResult, command_for_route


def test_command_for_route_substitutes_route_path():
    assert command_for_route(["gps", "route", "{route}", "--serial", "{serial}"], "routes/smoke.gpx", "PHONE") == ["gps", "route", "routes/smoke.gpx", "--serial", "PHONE"]


def test_provider_result_records_process_output():
    result = ProviderResult(command=["gps"], returncode=0, stdout="ok", stderr="")
    assert result.ok and result.stdout == "ok"


def test_report_uses_dedicated_report_command():
    provider = GpsLocatorProvider({"report": ["gps", "report", "{serial}"]}, serial="PHONE")
    provider._run = lambda command: ProviderResult(command, 0, "report", "")
    result = provider.report()
    assert result is not None and result.command == ["gps", "report", "PHONE"]


def test_ready_requires_all_provider_flags():
    provider = GpsLocatorProvider({"status": ["gps", "status"]})
    provider._run = lambda command: ProviderResult(command, 0, '{"available":true,"mockLocationReady":true,"commandReady":true,"simulationActive":false}', "")
    assert provider.ready() is True
    provider._run = lambda command: ProviderResult(command, 0, '{"available":true,"mockLocationReady":false,"commandReady":true,"simulationActive":false}', "")
    assert provider.ready() is False


def test_prepare_launches_provider_app_when_configured():
    provider = GpsLocatorProvider({"prepare": ["prep"], "launch": ["launch", "{serial}"]}, serial="PHONE")
    calls = []
    provider._run = lambda command: (calls.append(command) or ProviderResult(command, 0, "", ""))
    assert provider.prepare().ok
    assert calls == [["prep"], ["launch", "PHONE"]]


def test_start_route_with_timeout_passes_the_authorized_duration_to_the_provider(tmp_path):
    route = tmp_path / "route.gpx"
    route.write_text(
        '<gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="3" lon="4"/></trkseg></trk></gpx>',
        encoding="utf-8",
    )
    provider = GpsLocatorProvider({"route": ["gps", "route", "{route}"]})
    calls = []

    def run(command, *, timeout=None):
        calls.append((command, timeout))
        return ProviderResult(command, 0, "", "")

    provider._run = run

    assert provider.start_route_with_timeout(route, timeout=12.5).ok is True
    assert calls == [(["gps", "route", str(route)], 12.5)]


def test_stop_verified_waits_for_inactive_simulation():
    provider = GpsLocatorProvider({"stop": ["gps", "stop"], "status": ["gps", "status"]}, poll_interval=0)
    responses = iter([
        ProviderResult(["gps", "stop"], 0, "", ""),
        ProviderResult(["gps", "status"], 0, '{"simulationActive": true}', ""),
        ProviderResult(["gps", "status"], 0, '{"simulationActive": false}', ""),
    ])
    provider._run = lambda command: next(responses)

    assert provider.stop_verified(timeout=1).ok is True


def test_stop_verified_fails_when_simulation_never_stops():
    provider = GpsLocatorProvider({"stop": ["gps", "stop"], "status": ["gps", "status"]}, poll_interval=0)
    provider._run = lambda command: ProviderResult(command, 0, '{"simulationActive": true}', "")

    result = provider.stop_verified(timeout=0)

    assert result.ok is False
    assert "simulationActive" in result.stderr


def test_failed_stop_latches_provider_until_inactive_status_is_confirmed():
    provider = GpsLocatorProvider(
        {"prepare": ["gps", "prepare"], "stop": ["gps", "stop"], "status": ["gps", "status"]},
        poll_interval=0,
    )
    calls = []
    provider._run = lambda command: (
        calls.append(command)
        or ProviderResult(command, 0, '{"simulationActive": true}', "")
    )

    assert provider.stop_verified(timeout=0).ok is False
    blocked_calls = len(calls)
    assert provider.prepare().ok is False
    assert provider.ready() is False
    assert len(calls) == blocked_calls

    provider._run = lambda command: ProviderResult(command, 0, '{"simulationActive": false}', "")
    assert provider.stop_verified(timeout=0).ok is True
    assert provider.prepare().ok is True


def test_ready_fails_closed_for_non_object_json_status_payloads():
    provider = GpsLocatorProvider({"status": ["gps", "status"]})
    for payload in ("[]", "null", '"ready"'):
        provider._run = lambda command, payload=payload: ProviderResult(command, 0, payload, "")
        assert provider.ready() is False


def test_status_returns_result_for_non_object_json_status_payload():
    provider = GpsLocatorProvider({"status": ["gps", "status"]})
    provider._run = lambda command: ProviderResult(command, 0, "[]", "")
    result = provider.status()
    assert isinstance(result, ProviderResult)
    assert result.ok is True
