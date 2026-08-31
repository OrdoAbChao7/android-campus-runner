from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from android_runner import runner
from android_runner.intent import IntentUseRegistry, RunIntent, RunObservation, route_sha256
from android_runner.state import RunState
from android_runner.wecom.account import WeComEnterpriseSwitcher


def _intent(route, *, intent_id: str = "evidence-intent") -> tuple[RunIntent, RunObservation]:
    now = datetime.now(UTC)
    intent = RunIntent(
        intent_id=intent_id,
        adb_serial="PHONE",
        device_fingerprint="fingerprint",
        current_enterprise="企业A",
        target_enterprise="企业A",
        route_sha256=route_sha256(route),
        not_before=now - timedelta(minutes=1),
        not_after=now + timedelta(minutes=1),
        max_duration=timedelta(minutes=30),
        allowed_action_ids={"campus_run.start"},
    )
    return intent, RunObservation("PHONE", "fingerprint", route_sha256(route), now)


def test_single_runner_writes_state_and_evidence_summary_on_preflight_safe_stop(tmp_path):
    route = tmp_path / "route.gpx"
    route.write_text("route", encoding="utf-8")
    intent, observation = _intent(route)
    registry = IntentUseRegistry.production(tmp_path / "intent-use.sqlite3")

    class Provider:
        def prepare(self):
            raise AssertionError("preflight rejection must precede provider work")

    device = object()
    result = runner.run_mvp(
        device,
        Provider(),
        route,
        WeComEnterpriseSwitcher(
            device,
            "企业A",
            current="企业A",
            logged_in_enterprises=("企业A",),
        ),
        intent=intent,
        observation=observation,
        intent_registry=registry,
        evidence_root=tmp_path / "evidence",
    )

    assert result.state is RunState.SAFE_STOP
    assert result.evidence_summary is not None and result.evidence_summary.is_file()
    summary = json.loads(result.evidence_summary.read_text(encoding="utf-8"))["payload"]
    assert summary["final_state"] == "SAFE_STOP"
    assert summary["outcome"] == "refused"
    events = (result.evidence_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"state_transition"' in events
    assert '"safe_stop"' in events


def test_multi_runner_writes_state_and_evidence_summary_when_intent_is_missing(tmp_path):
    class Provider:
        def prepare(self):
            raise AssertionError("missing intent must precede provider work")

    result = runner.run_multi_account_mvp(
        device=object(),
        provider=Provider(),
        route=tmp_path / "route.gpx",
        accounts=["企业A"],
        evidence_root=tmp_path / "evidence",
    )

    assert result.state is RunState.SAFE_STOP
    assert result.evidence_summary is not None and result.evidence_summary.is_file()
    summary = json.loads(result.evidence_summary.read_text(encoding="utf-8"))["payload"]
    assert summary["final_state"] == "SAFE_STOP"
    assert summary["outcome"] == "refused"


def test_single_runner_ready_exception_verifies_cleanup_and_finalizes_safe_stop(tmp_path):
    route = tmp_path / "route.gpx"
    route.write_text("route", encoding="utf-8")
    intent, observation = _intent(route, intent_id="ready-exception")
    registry = IntentUseRegistry.production(tmp_path / "intent-use.sqlite3")
    registry.register(intent)

    class Provider:
        def __init__(self):
            self.calls = []

        def prepare(self):
            self.calls.append("prepare")
            return type("Result", (), {"ok": True})()

        def ready(self):
            self.calls.append("ready")
            raise RuntimeError("status payload failure")

        def stop_verified(self):
            self.calls.append("verified-stop")
            return type("Result", (), {"ok": True})()

    provider = Provider()
    result = runner.run_mvp(
        object(), provider, route,
        WeComEnterpriseSwitcher(object(), "企业A", current="企业A", logged_in_enterprises=("企业A",)),
        intent=intent, observation=observation, intent_registry=registry,
        evidence_root=tmp_path / "evidence",
    )

    assert result.state is RunState.SAFE_STOP
    assert provider.calls == ["prepare", "ready", "verified-stop"]
    assert result.evidence_summary is not None and result.evidence_summary.is_file()
    summary = json.loads(result.evidence_summary.read_text(encoding="utf-8"))["payload"]
    assert summary["final_state"] == "SAFE_STOP"
