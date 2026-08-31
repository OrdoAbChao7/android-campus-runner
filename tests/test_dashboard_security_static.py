"""Static security checks kept runnable when Flask is not installed."""

from __future__ import annotations

from pathlib import Path


DASHBOARD = Path(__file__).parents[1] / "dashboard" / "app.py"


def test_dashboard_has_no_permissive_network_or_cors_defaults():
    source = DASHBOARD.read_text(encoding="utf-8")

    assert 'host="127.0.0.1"' in source
    assert 'debug=False' in source
    assert 'Access-Control-Allow-Origin"' not in source
    assert '"*"' not in source[source.find("def _add_cors_headers"):source.find("def _options_handler")]


def test_dashboard_has_control_token_and_route_confinement_guards():
    source = DASHBOARD.read_text(encoding="utf-8")

    assert "compare_digest" in source
    assert "ANDROID_RUNNER_DASHBOARD_TOKEN" in source
    assert "def _resolve_route" in source
    assert "validate_route" in source
    assert "route must be a basename" in source


def test_dashboard_statically_guards_writes_and_authorization_order():
    source = DASHBOARD.read_text(encoding="utf-8")

    for endpoint in ("post_accounts", "post_config", "run_start", "run_stop"):
        handler = source.split(f"def {endpoint}", 1)[1].split("\n\n@app.route", 1)[0]
        assert "_require_control_token()" in handler
    run_task = source.split("def _run_task", 1)[1]
    assert run_task.index("_validate_run_intents") < run_task.index("GpsLocatorProvider")
    assert "provider and executable configuration is not accepted" in source


def test_dashboard_static_intent_validation_matches_runner_preconditions():
    source = DASHBOARD.read_text(encoding="utf-8")
    validator = source.split("def _validate_run_intents", 1)[1].split("\n\n# ---------------------------------------------------------------------------\n# Background", 1)[0]

    assert "if not enterprises" in validator
    assert "intent.current_enterprise != enterprise" in validator
    assert "intent.target_enterprise != enterprise" in validator
    assert 'validate_route_binding(route, intent, observation, "campus_run.start")' in validator
    assert "intent_registry.validate_registered(intent)" in validator


def test_dashboard_uses_one_guarded_multi_account_runner_session():
    source = DASHBOARD.read_text(encoding="utf-8")
    run_task = source.split("def _run_task", 1)[1].split("\n\n# ---------------------------------------------------------------------------\n# API", 1)[0]

    assert "accounts=enterprise_list" in run_task
    assert "accounts=[enterprise]" not in run_task


def test_dashboard_exposes_the_two_step_intent_bridge():
    source = DASHBOARD.read_text(encoding="utf-8")
    run_start = source.split("def run_start", 1)[1].split("\n\n@app.route", 1)[0]
    run_status = source.split("def run_status", 1)[1].split("\n\n# ---------------------------------------------------------------------------\n# API", 1)[0]

    assert "INTENT_BRIDGE_AVAILABLE" in source
    assert "previously captured, durable single-use authorization" in run_start
    assert '"intent_bridge_available"' in run_status
    assert '"direct_campus_run_start_available"' in run_status
    assert "/api/run/authorize" in source


def test_dashboard_authorization_bridge_requires_live_checkpoint_and_confirmation():
    source = DASHBOARD.read_text(encoding="utf-8")
    authorize = source.split("def run_authorize", 1)[1].split("\n\n@app.route", 1)[0]

    assert "_require_control_token()" in authorize
    assert "_AUTH_CONFIRMATION" in authorize
    assert "open_campus_run(device)" in authorize
    assert "capture_wecom_checkpoint" in authorize
    assert "IntentUseRegistry.production" in authorize
    assert "route_sha256(route)" in authorize
