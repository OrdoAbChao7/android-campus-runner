from __future__ import annotations

import yaml
import importlib.util
from pathlib import Path
import pytest

pytest.importorskip("flask")

_SPEC = importlib.util.spec_from_file_location("dashboard_app", Path(__file__).parents[1] / "dashboard" / "app.py")
dashboard = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
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


def test_dashboard_run_start_requires_external_runintent(control_token):
    response = dashboard.app.test_client().post("/api/run/start", json={}, headers=control_token)

    assert response.status_code == 409
    assert "RunIntent" in response.get_json()["error"]


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
