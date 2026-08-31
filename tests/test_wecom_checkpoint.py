from __future__ import annotations

from datetime import UTC

import pytest

from android_runner.device import (
    AndroidDevice,
    UnsafeWeComCheckpoint,
    WeComPage,
    classify_wecom_page,
)


def test_checkpoint_captures_wecom_start_prompt_evidence_and_stable_fingerprint(tmp_path):
    class Adb:
        def shell(self, *args):
            assert args == ("getprop", "ro.build.fingerprint")
            return "vendor/device/build:fingerprint"

    class Ui:
        def app_current(self):
            return {"package": "com.tencent.wework", "activity": "com.tencent.wework.launch.WwMainActivity"}

        def dump_hierarchy(self):
            return '<hierarchy><node class="android.widget.TextView" text="自由跑" resource-id="run.start" /></hierarchy>'

    device = AndroidDevice.__new__(AndroidDevice)
    device.serial = "PHONE-1"
    device.adb = Adb()
    device.ui = Ui()
    device.screenshot = lambda path: path.write_bytes(b"png")
    device.hierarchy = lambda path: path.write_text(device.ui.dump_hierarchy(), encoding="utf-8")

    checkpoint = device.capture_wecom_checkpoint(tmp_path)

    assert checkpoint.screenshot_path.read_bytes() == b"png"
    assert checkpoint.hierarchy_path.exists()
    assert checkpoint.captured_at.tzinfo is UTC
    assert checkpoint.foreground_package == "com.tencent.wework"
    assert checkpoint.foreground_activity == "com.tencent.wework.launch.WwMainActivity"
    assert checkpoint.adb_serial == "PHONE-1"
    assert checkpoint.device_fingerprint == "vendor/device/build:fingerprint"
    assert checkpoint.page is WeComPage.START_PROMPT
    assert len(checkpoint.page_fingerprint) == 64


@pytest.mark.parametrize(
    "hierarchy",
    [
        '<hierarchy><node text="登录" /></hierarchy>',
        '<hierarchy><node text="退出登录" /></hierarchy>',
        '<hierarchy><node text="请输入验证码" /></hierarchy>',
        '<hierarchy><node text="CAPTCHA" /></hierarchy>',
        '<hierarchy><node text="unrecognized page" /></hierarchy>',
    ],
)
def test_checkpoint_classifier_fails_closed_for_authentication_or_unknown_pages(hierarchy):
    with pytest.raises(UnsafeWeComCheckpoint):
        classify_wecom_page(
            package="com.tencent.wework",
            activity="com.tencent.wework.launch.WwMainActivity",
            hierarchy=hierarchy,
        )


def test_checkpoint_classifier_rejects_non_wecom_foreground_package():
    with pytest.raises(UnsafeWeComCheckpoint, match="foreground package"):
        classify_wecom_page(
            package="com.android.settings",
            activity="com.android.settings.Settings",
            hierarchy='<hierarchy><node text="自由跑" /></hierarchy>',
        )
