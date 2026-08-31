import pytest
from datetime import datetime, timedelta, timezone

from android_runner.intent import IntentUseRegistry, RunIntent, RunObservation
from android_runner.wecom.campus_run import CampusRunState, confirm_free_run, next_state


def test_campus_flow_states_are_ordered():
    state = CampusRunState.INIT
    for expected in [CampusRunState.WORKBENCH, CampusRunState.SMART_SPORTS,
                     CampusRunState.CAMPUS_RUN, CampusRunState.START_PROMPT]:
        state = next_state(state)
        assert state is expected


def test_free_run_requires_explicit_authorization():
    class Device:
        def click(self, **kwargs): raise AssertionError("must not click")
    with pytest.raises(TypeError):
        confirm_free_run(Device())


def test_free_run_confirmation_finalizes_run_reservation_before_clicking():
    calls = []
    class Device:
        def click(self, **kwargs): calls.append(kwargs)
    now = datetime.now(timezone.utc)
    intent = RunIntent(
        intent_id="start", adb_serial="PHONE", device_fingerprint="fingerprint",
        current_enterprise="current", target_enterprise="target", route_sha256="0" * 64,
        not_before=now - timedelta(minutes=1), not_after=now + timedelta(minutes=1),
        max_duration=timedelta(minutes=30), allowed_action_ids={"campus_run.start"},
    )
    registry = IntentUseRegistry()
    registry.register(intent)
    observation = RunObservation("PHONE", "fingerprint", "0" * 64, now)
    reservation = registry.reserve_batch([intent])

    assert confirm_free_run(
        Device(), intent=intent, observation=observation, intent_registry=registry,
        reservation=reservation,
    ) is CampusRunState.RUNNING
    assert calls == [{"text": "自由跑", "timeout": 10.0}]


def test_free_run_rejects_removed_boolean_bypass():
    with pytest.raises(TypeError):
        confirm_free_run(object(), allow_start=True)
