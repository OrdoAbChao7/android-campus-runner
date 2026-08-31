from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from android_runner.device import WeComCheckpoint, WeComPage, classify_wecom_page
from android_runner.wecom.account import AccountSwitchState, AccountSwitcher, WeComEnterpriseSwitcher


def _checkpoint(page: WeComPage, fingerprint: str) -> WeComCheckpoint:
    return WeComCheckpoint(
        screenshot_path=Path("screen.png"), hierarchy_path=Path("page.xml"),
        captured_at=datetime.now(timezone.utc), foreground_package="com.tencent.wework",
        foreground_activity="com.tencent.wework.launch.WwMainActivity", adb_serial="PHONE",
        device_fingerprint="fingerprint", page_fingerprint=fingerprint, page=page,
    )


def test_generic_account_switch_runs_after_route_completion():
    calls = []
    switcher = AccountSwitcher(lambda: calls.append("open"), lambda: calls.append("select"), lambda: True)
    assert switcher.switch() is AccountSwitchState.READY
    assert calls == ["open", "select"]


def test_enterprise_switcher_only_selects_distinct_explicitly_logged_in_enterprise():
    class Device:
        def __init__(self):
            self.calls = []
            self.checkpoints = iter((
                _checkpoint(WeComPage.ACCOUNT_HOME, "a" * 64),
                _checkpoint(WeComPage.ACCOUNT_SWITCHER, "b" * 64),
                _checkpoint(WeComPage.ACCOUNT_HOME, "c" * 64),
            ))

        def capture_wecom_checkpoint(self, _directory):
            return next(self.checkpoints)

        def click(self, **kwargs):
            self.calls.append(kwargs)

        def wait_text(self, text, timeout=5):
            return text == "目标企业"

    device = Device()
    switcher = WeComEnterpriseSwitcher(
        device, "目标企业", current="当前企业",
        logged_in_enterprises=("当前企业", "目标企业"),
    )

    assert switcher.switch() is AccountSwitchState.READY
    assert device.calls == [{"resource_id": "com.tencent.wework:id/nts"}, {"text": "目标企业"}]


def test_enterprise_switcher_refuses_unknown_target_without_clicking():
    class Device:
        def capture_wecom_checkpoint(self, _directory):
            raise AssertionError("must not capture or click")

        def click(self, **kwargs):
            raise AssertionError("must not click")

    switcher = WeComEnterpriseSwitcher(
        Device(), "未登录企业", current="当前企业", logged_in_enterprises=("当前企业",),
    )
    assert switcher.switch() is AccountSwitchState.ABORT


def test_enterprise_switcher_refuses_unsafe_checkpoint_before_opening():
    class Device:
        def __init__(self): self.calls = []

        def capture_wecom_checkpoint(self, _directory):
            return _checkpoint(WeComPage.START_PROMPT, "a" * 64)

        def click(self, **kwargs): self.calls.append(kwargs)

    device = Device()
    switcher = WeComEnterpriseSwitcher(
        device, "目标企业", current="当前企业",
        logged_in_enterprises=("当前企业", "目标企业"),
    )
    assert switcher.switch() is AccountSwitchState.ABORT
    assert device.calls == []


def test_enterprise_switcher_rejects_login_or_logout_checkpoint_without_clicking():
    class Device:
        def __init__(self): self.calls = []

        def capture_wecom_checkpoint(self, _directory):
            return classify_wecom_page(
                package="com.tencent.wework",
                activity="com.tencent.wework.launch.WwMainActivity",
                hierarchy='<hierarchy><node text="退出登录" /></hierarchy>',
            )

        def click(self, **kwargs): self.calls.append(kwargs)

    device = Device()
    switcher = WeComEnterpriseSwitcher(
        device, "目标企业", current="当前企业",
        logged_in_enterprises=("当前企业", "目标企业"),
    )
    assert switcher.switch() is AccountSwitchState.ABORT
    assert device.calls == []
