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


def test_accounts_api_rejects_password_without_writing(monkeypatch, tmp_path):
    path = tmp_path / "accounts.yaml"
    monkeypatch.setattr(dashboard, "ACCOUNTS_PATH", path)

    response = dashboard.app.test_client().post("/api/accounts", json={
        "accounts": [{"enterprise": "企业A", "phone": "1", "password": "secret"}],
    })

    assert response.status_code == 400
    assert "password" not in response.get_json()["error"].lower()
    assert not path.exists()


def test_dashboard_config_does_not_persist_keep_gps(monkeypatch, tmp_path):
    config_path = tmp_path / "dashboard.yaml"
    monkeypatch.setattr(dashboard, "DASHBOARD_CONFIG_PATH", config_path)

    response = dashboard.app.test_client().post("/api/config", json={"keep_gps": True})

    assert response.status_code == 200
    assert "keep_gps" not in response.get_json()["config"]
    assert "keep_gps" not in config_path.read_text(encoding="utf-8")


def test_dashboard_run_start_requires_external_runintent():
    response = dashboard.app.test_client().post("/api/run/start", json={})

    assert response.status_code == 409
    assert "RunIntent" in response.get_json()["error"]
