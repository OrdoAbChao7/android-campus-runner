from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from android_runner.device import WeComCheckpoint, WeComPage
from android_runner.intent import IntentUseRegistry, IntentValidationError, RunIntent, RunObservation, route_sha256
from android_runner.wecom.account import AccountSwitchState, WeComEnterpriseSwitcher
from android_runner.wecom.campus_run import confirm_free_run


def _checkpoint(page: WeComPage, enterprise: str) -> WeComCheckpoint:
    return WeComCheckpoint(
        screenshot_path=Path("screen.png"),
        hierarchy_path=Path("page.xml"),
        captured_at=datetime.now(timezone.utc),
        foreground_package="com.tencent.wework",
        foreground_activity="com.tencent.wework.launch.WwMainActivity",
        adb_serial="PHONE",
        device_fingerprint="fingerprint",
        page_fingerprint="a" * 64,
        page=page,
        enterprise_identity=enterprise,
    )


def _intent_and_observation(route: Path, enterprise: str):
    now = datetime.now(timezone.utc)
    intent = RunIntent(
        intent_id=f"enterprise-{enterprise}",
        adb_serial="PHONE",
        device_fingerprint="fingerprint",
        current_enterprise=enterprise,
        target_enterprise=enterprise,
        route_sha256=route_sha256(route),
        not_before=now - timedelta(minutes=1),
        not_after=now + timedelta(minutes=1),
        max_duration=timedelta(minutes=30),
        allowed_action_ids={"campus_run.start"},
    )
    return intent, RunObservation("PHONE", "fingerprint", route_sha256(route), now)


def test_confirm_free_run_rejects_checkpoint_for_a_different_enterprise_before_clicking(tmp_path):
    route = tmp_path / "route.gpx"
    route.write_text("route", encoding="utf-8")
    intent, observation = _intent_and_observation(route, "企业B")
    registry = IntentUseRegistry()
    registry.register(intent)
    reservation = registry.reserve_batch([intent])
    clicks: list[dict] = []

    class Device:
        def capture_wecom_checkpoint(self, _directory):
            return _checkpoint(WeComPage.START_PROMPT, "企业A")

        def click(self, **kwargs):
            clicks.append(kwargs)

    try:
        with pytest.raises(IntentValidationError, match="enterprise"):
            confirm_free_run(
                Device(),
                intent=intent,
                observation=observation,
                intent_registry=registry,
                reservation=reservation,
                route=route,
                start_checkpoint=_checkpoint(WeComPage.START_PROMPT, "企业A"),
            )
    finally:
        registry.release_reservation(reservation)

    assert clicks == []


def test_enterprise_switcher_rejects_a_post_switch_checkpoint_for_the_wrong_enterprise():
    class Device:
        def __init__(self) -> None:
            self.clicks: list[dict] = []
            self.checkpoints = iter((
                _checkpoint(WeComPage.ACCOUNT_HOME, "企业A"),
                _checkpoint(WeComPage.ACCOUNT_SWITCHER, "企业A"),
                _checkpoint(WeComPage.ACCOUNT_HOME, "企业A"),
            ))

        def capture_wecom_checkpoint(self, _directory):
            return next(self.checkpoints)

        def click(self, **kwargs):
            self.clicks.append(kwargs)

        def wait_text(self, text, timeout=5):
            return text == "企业B"

    device = Device()
    switcher = WeComEnterpriseSwitcher(
        device,
        target="企业B",
        current="企业A",
        logged_in_enterprises=("企业A", "企业B"),
    )

    assert switcher.switch() is AccountSwitchState.ABORT
    assert device.clicks == [
        {"resource_id": "com.tencent.wework:id/nts"},
        {"text": "企业B"},
    ]
