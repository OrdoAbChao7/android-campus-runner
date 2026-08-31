from __future__ import annotations

import yaml
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from android_runner.intent import IntentUseRegistry, RunIntent, RunObservation, route_sha256

pytest.importorskip("flask")

_SPEC = importlib.util.spec_from_file_location("dashboard_app", Path(__file__).parents[1] / "dashboard" / "app.py")
dashboard = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = dashboard
_SPEC.loader.exec_module(dashboard)


@pytest.fixture
def control_token(monkeypatch):
    monkeypatch.setenv("ANDROID_RUNNER_DASHBOARD_TOKEN", "local-test-token")
    return {"X-Local-Control-Token": "local-test-token"}


def test_accounts_api_exposes_only_credential_ref(monkeypatch, tmp_path):
    path = tmp_path / "accounts.yaml"
    path.write_text(yaml.safe_dump({"accounts": [{
        "enterprise": "企业A", "phone": "1", "credential_ref": "env:A",
    }]}), encoding="utf-8")
    monkeypatch.setattr(dashboard, "ACCOUNTS_PATH", path)

    response = dashboard.app.test_client().get("/api/accounts")

    assert response.status_code == 200
    account = response.get_json()["accounts"][0]
    assert "password" not in account
    assert account["credential_ref"] == "env:A"


def test_dashboard_serves_minimal_control_page():
    response = dashboard.app.test_client().get("/")
    assert response.status_code == 200
    assert "采集检查点并授权" in response.get_data(as_text=True)


def test_accounts_api_rejects_password_without_writing(monkeypatch, tmp_path, control_token):
    path = tmp_path / "accounts.yaml"
    monkeypatch.setattr(dashboard, "ACCOUNTS_PATH", path)

    response = dashboard.app.test_client().post("/api/accounts", json={
        "accounts": [{"enterprise": "企业A", "phone": "1", "password": "secret"}],
    }, headers=control_token)

    assert response.status_code == 400
    assert "password" not in response.get_json()["error"].lower()
    assert not path.exists()


def test_dashboard_config_does_not_persist_keep_gps(monkeypatch, tmp_path, control_token):
    config_path = tmp_path / "dashboard.yaml"
    monkeypatch.setattr(dashboard, "DASHBOARD_CONFIG_PATH", config_path)

    response = dashboard.app.test_client().post(
        "/api/config", json={"keep_gps": True}, headers=control_token,
    )

    assert response.status_code == 200
    assert "keep_gps" not in response.get_json()["config"]
    assert "keep_gps" not in config_path.read_text(encoding="utf-8")


def test_dashboard_run_start_requires_captured_runintent(control_token):
    response = dashboard.app.test_client().post("/api/run/start", json={}, headers=control_token)

    assert response.status_code == 409
    assert "intent_id" in response.get_json()["error"]


@pytest.mark.parametrize("endpoint, payload", [
    ("/api/accounts", {"accounts": []}),
    ("/api/config", {}),
    ("/api/run/start", {}),
    ("/api/run/stop", {}),
])
def test_mutating_endpoints_require_a_control_token(endpoint, payload, control_token):
    response = dashboard.app.test_client().post(endpoint, json=payload)

    assert response.status_code == 401


def test_dashboard_uses_localhost_and_emits_no_permissive_cors_header():
    response = dashboard.app.test_client().get("/api/routes")

    assert response.headers.get("Access-Control-Allow-Origin") is None
    assert "127.0.0.1" in Path(dashboard.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("route_name", ["../outside.gpx", "nested/route.gpx", "C:\\route.gpx", "missing.gpx", "route.txt"])
def test_config_rejects_nonlocal_or_unsupported_routes_without_writing(
    monkeypatch, tmp_path, control_token, route_name,
):
    config_path = tmp_path / "dashboard.yaml"
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir()
    monkeypatch.setattr(dashboard, "DASHBOARD_CONFIG_PATH", config_path)
    monkeypatch.setattr(dashboard, "ROUTES_DIR", routes_dir)

    response = dashboard.app.test_client().post(
        "/api/config", json={"route": route_name}, headers=control_token,
    )

    assert response.status_code == 400
    assert not config_path.exists()


def test_config_accepts_only_a_valid_route_basename(monkeypatch, tmp_path, control_token):
    config_path = tmp_path / "dashboard.yaml"
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir()
    (routes_dir / "safe.gpx").write_text(
        '<gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="3" lon="4"/></trkseg></trk></gpx>',
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "DASHBOARD_CONFIG_PATH", config_path)
    monkeypatch.setattr(dashboard, "ROUTES_DIR", routes_dir)

    response = dashboard.app.test_client().post(
        "/api/config", json={"route": "safe.gpx"}, headers=control_token,
    )

    assert response.status_code == 200
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved == {"route": "safe.gpx", "serial": ""}


@pytest.mark.parametrize("account", [
    {"enterprise": "bad/name", "phone": "13800138000", "credential_ref": "env:one"},
    {"enterprise": "企业A", "phone": "not a phone!", "credential_ref": "env:one"},
    {"enterprise": "企业A", "phone": "13800138000", "credential_ref": "bad ref"},
])
def test_accounts_reject_invalid_identifiers_without_writing(monkeypatch, tmp_path, control_token, account):
    path = tmp_path / "accounts.yaml"
    monkeypatch.setattr(dashboard, "ACCOUNTS_PATH", path)

    response = dashboard.app.test_client().post(
        "/api/accounts", json={"accounts": [account]}, headers=control_token,
    )

    assert response.status_code == 400
    assert not path.exists()


def test_accounts_reject_duplicate_enterprises_without_writing(monkeypatch, tmp_path, control_token):
    path = tmp_path / "accounts.yaml"
    monkeypatch.setattr(dashboard, "ACCOUNTS_PATH", path)

    response = dashboard.app.test_client().post("/api/accounts", json={"accounts": [
        {"enterprise": "企业A", "phone": "13800138000", "credential_ref": "env:one"},
        {"enterprise": "企业A", "phone": "13900139000", "credential_ref": "env:two"},
    ]}, headers=control_token)

    assert response.status_code == 400
    assert not path.exists()


def test_missing_run_intents_never_constructs_provider_or_device(monkeypatch, tmp_path):
    accounts = tmp_path / "accounts.yaml"
    accounts.write_text(yaml.safe_dump({"accounts": [{
        "enterprise": "企业A", "phone": "13800138000",
    }]}), encoding="utf-8")
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir()
    (routes_dir / "safe.gpx").write_text(
        '<gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="3" lon="4"/></trkseg></trk></gpx>',
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(dashboard, "ACCOUNTS_PATH", accounts)
    monkeypatch.setattr(dashboard, "ROUTES_DIR", routes_dir)
    monkeypatch.setattr(dashboard, "GpsLocatorProvider", lambda *args, **kwargs: calls.append("provider"))
    monkeypatch.setattr(dashboard, "AndroidDevice", lambda *args, **kwargs: calls.append("device"))

    dashboard._run_task(serial="", route="safe.gpx", intents=None, intent_registry=None)

    assert calls == []


def _dashboard_run_inputs(monkeypatch, tmp_path, *, intent_changes=None, observation_changes=None):
    accounts = tmp_path / "accounts.yaml"
    accounts.write_text(yaml.safe_dump({"accounts": [{
        "enterprise": "企业A", "phone": "13800138000",
    }]}), encoding="utf-8")
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir()
    route = routes_dir / "safe.gpx"
    route.write_text(
        '<gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="3" lon="4"/></trkseg></trk></gpx>',
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "ACCOUNTS_PATH", accounts)
    monkeypatch.setattr(dashboard, "ROUTES_DIR", routes_dir)

    now = datetime.now(timezone.utc)
    intent_values = {
        "intent_id": "dashboard-intent",
        "adb_serial": "SERIAL",
        "device_fingerprint": "fingerprint",
        "current_enterprise": "企业A",
        "target_enterprise": "企业A",
        "route_sha256": route_sha256(route),
        "not_before": now - timedelta(minutes=1),
        "not_after": now + timedelta(minutes=1),
        "max_duration": timedelta(minutes=30),
        "allowed_action_ids": {"campus_run.start"},
    }
    intent_values.update(intent_changes or {})
    intent = RunIntent(**intent_values)
    observation_values = {
        "adb_serial": "SERIAL",
        "device_fingerprint": "fingerprint",
        "route_sha256": route_sha256(route),
        "observed_at": now,
    }
    observation_values.update(observation_changes or {})
    observation = RunObservation(**observation_values)
    registry = IntentUseRegistry()
    registry.register(intent)
    return intent, observation, registry


@pytest.mark.parametrize("intent_changes, observation_changes", [
    ({}, {"route_sha256": "f" * 64}),
    ({}, {"adb_serial": "OTHER"}),
    ({}, {"device_fingerprint": "other-fingerprint"}),
    ({}, {"observed_at": datetime.now(timezone.utc) + timedelta(hours=1)}),
    ({"current_enterprise": "企业B"}, {}),
    ({"target_enterprise": "企业B"}, {}),
])
def test_invalid_registered_run_intent_never_constructs_provider_or_device(
    monkeypatch, tmp_path, intent_changes, observation_changes,
):
    intent, observation, registry = _dashboard_run_inputs(
        monkeypatch, tmp_path,
        intent_changes=intent_changes,
        observation_changes=observation_changes,
    )
    calls = []
    monkeypatch.setattr(dashboard, "GpsLocatorProvider", lambda *args, **kwargs: calls.append("provider"))
    monkeypatch.setattr(dashboard, "AndroidDevice", lambda *args, **kwargs: calls.append("device"))

    dashboard._run_task(
        serial="SERIAL",
        route="safe.gpx",
        intents={"企业A": (intent, observation)},
        intent_registry=registry,
    )

    assert calls == []


def test_dashboard_run_intents_require_at_least_one_account():
    with pytest.raises(dashboard.IntentValidationError, match="at least one"):
        dashboard._validate_run_intents([], Path("route.gpx"), {}, IntentUseRegistry())
