import pytest
from datetime import datetime, timedelta, timezone

from android_runner.intent import IntentReservation, IntentUseRegistry, IntentValidationError, RunIntent, RunObservation
from android_runner.device import WeComCheckpoint, WeComPage
from android_runner.wecom.campus_run import CampusRunState, confirm_free_run, next_state


def _start_checkpoint(*, fingerprint: str = "a" * 64) -> WeComCheckpoint:
    return WeComCheckpoint(
        screenshot_path=__import__("pathlib").Path("screen.png"),
        hierarchy_path=__import__("pathlib").Path("page.xml"),
        captured_at=datetime.now(timezone.utc),
        foreground_package="com.tencent.wework",
        foreground_activity="com.tencent.wework.launch.WwMainActivity",
        adb_serial="PHONE",
        device_fingerprint="fingerprint",
        page_fingerprint=fingerprint,
        page=WeComPage.START_PROMPT,
    )


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
        def capture_wecom_checkpoint(self, _directory): return _start_checkpoint()
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
        reservation=reservation, start_checkpoint=_start_checkpoint(),
    ) is CampusRunState.RUNNING
    assert calls == [{"text": "自由跑", "timeout": 10.0}]


def test_free_run_rejects_changed_start_prompt_fingerprint_before_consuming_or_clicking():
    calls = []

    class Device:
        def click(self, **kwargs):
            calls.append(kwargs)

        def capture_wecom_checkpoint(self, _directory):
            return _start_checkpoint(fingerprint="b" * 64)

    now = datetime.now(timezone.utc)
    intent = RunIntent(
        intent_id="changed-page", adb_serial="PHONE", device_fingerprint="fingerprint",
        current_enterprise="current", target_enterprise="target", route_sha256="0" * 64,
        not_before=now - timedelta(minutes=1), not_after=now + timedelta(minutes=1),
        max_duration=timedelta(minutes=30), allowed_action_ids={"campus_run.start"},
    )
    registry = IntentUseRegistry()
    registry.register(intent)
    reservation = registry.reserve_batch([intent])

    with pytest.raises(IntentValidationError, match="checkpoint"):
        confirm_free_run(
            Device(), intent=intent,
            observation=RunObservation("PHONE", "fingerprint", "0" * 64, now),
            intent_registry=registry, reservation=reservation, start_checkpoint=_start_checkpoint(),
        )

    assert calls == []
    registry.release_reservation(reservation)
    retry = registry.reserve_batch([intent])
    registry.release_reservation(retry)


def test_free_run_rejects_checkpoint_on_a_different_authorized_device_before_clicking():
    calls = []

    class Device:
        def click(self, **kwargs): calls.append(kwargs)
        def capture_wecom_checkpoint(self, _directory): return _start_checkpoint()

    now = datetime.now(timezone.utc)
    intent = RunIntent(
        intent_id="checkpoint-device", adb_serial="OTHER", device_fingerprint="fingerprint",
        current_enterprise="current", target_enterprise="target", route_sha256="0" * 64,
        not_before=now - timedelta(minutes=1), not_after=now + timedelta(minutes=1),
        max_duration=timedelta(minutes=30), allowed_action_ids={"campus_run.start"},
    )
    registry = IntentUseRegistry()
    registry.register(intent)
    reservation = registry.reserve_batch([intent])

    with pytest.raises(IntentValidationError, match="checkpoint device"):
        confirm_free_run(
            Device(), intent=intent,
            observation=RunObservation("OTHER", "fingerprint", "0" * 64, now),
            intent_registry=registry, reservation=reservation, start_checkpoint=_start_checkpoint(),
        )

    assert calls == []


def test_free_run_rejects_duck_typed_registry_without_clicking():
    """A lookalike registry cannot bypass the atomic reservation consumption."""
    calls = []

    class Device:
        def click(self, **kwargs):
            calls.append(kwargs)

    class NoOpRegistry:
        def consume_reserved(self, *args):
            return None

    now = datetime.now(timezone.utc)
    intent = RunIntent(
        intent_id="no-op-registry", adb_serial="PHONE", device_fingerprint="fingerprint",
        current_enterprise="current", target_enterprise="target", route_sha256="0" * 64,
        not_before=now - timedelta(minutes=1), not_after=now + timedelta(minutes=1),
        max_duration=timedelta(minutes=30), allowed_action_ids={"campus_run.start"},
    )
    observation = RunObservation("PHONE", "fingerprint", "0" * 64, now)
    reservation = IntentReservation("fake", "owner", (intent.intent_id,))

    with pytest.raises(IntentValidationError, match="IntentUseRegistry"):
        confirm_free_run(
            Device(), intent=intent, observation=observation,
            intent_registry=NoOpRegistry(), reservation=reservation,
        )

    assert calls == []


def test_free_run_rejects_removed_boolean_bypass():
    with pytest.raises(TypeError):
        confirm_free_run(object(), allow_start=True)
