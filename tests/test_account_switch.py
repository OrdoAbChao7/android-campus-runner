from android_runner.wecom.account import AccountSwitchState, AccountSwitcher, SafeAccountSwitcher, WeComEnterpriseSwitcher


def test_account_switch_runs_after_route_completion():
    calls = []
    switcher = AccountSwitcher(lambda: calls.append("open"), lambda: calls.append("select"), lambda: True)
    assert switcher.switch() is AccountSwitchState.READY
    assert calls == ["open", "select"]


def test_safe_switch_requires_explicit_logout_opt_in():
    calls = []
    switcher = SafeAccountSwitcher(lambda: calls.append("open"), lambda: calls.append("select"), lambda: True)
    assert switcher.switch() is AccountSwitchState.ABORT
    assert calls == []


def test_safe_switch_runs_when_explicitly_enabled():
    switcher = SafeAccountSwitcher(lambda: None, lambda: None, lambda: True, allow_logout=lambda: True)
    assert switcher.switch() is AccountSwitchState.READY


def test_enterprise_switcher_uses_stable_menu_selector():
    class Device:
        def __init__(self): self.calls = []
        def click(self, **kwargs): self.calls.append(kwargs)
        def wait_text(self, text, timeout=5): return text == "目标企业"
    device = Device()
    assert WeComEnterpriseSwitcher(device, "目标企业").switch() is AccountSwitchState.READY
    assert device.calls == [{"resource_id": "com.tencent.wework:id/nts"}, {"text": "目标企业"}]


def test_enterprise_switcher_rejects_current_enterprise():
    class Device:
        def click(self, **kwargs): raise AssertionError("must not click")
        def wait_text(self, text, timeout=5): return True
    switcher = WeComEnterpriseSwitcher(Device(), "武汉理工大学", current="武汉理工大学")
    assert switcher.switch() is AccountSwitchState.ABORT
