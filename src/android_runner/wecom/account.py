from __future__ import annotations

from enum import Enum, auto
from typing import Callable, Optional


class AccountSwitchState(Enum):
    INIT = auto()
    OPEN_SWITCHER = auto()
    SELECT_TARGET = auto()
    VERIFY_ACCOUNT = auto()
    READY = auto()
    ABORT = auto()


class AccountSwitcher:
    def __init__(self, open_switcher: Callable[[], None], select_target: Callable[[], None], verify: Callable[[], bool]):
        self.open_switcher = open_switcher
        self.select_target = select_target
        self.verify = verify
        self.state = AccountSwitchState.INIT

    def switch(self) -> AccountSwitchState:
        try:
            self.state = AccountSwitchState.OPEN_SWITCHER
            self.open_switcher()
            self.state = AccountSwitchState.SELECT_TARGET
            self.select_target()
            self.state = AccountSwitchState.VERIFY_ACCOUNT
            self.state = AccountSwitchState.READY if self.verify() else AccountSwitchState.ABORT
        except Exception:
            self.state = AccountSwitchState.ABORT
        return self.state


class SafeAccountSwitcher(AccountSwitcher):
    """Config-driven switch hook that never logs out without explicit opt-in.

    WeCom exposes account switching through its login/logout UI.  Since a logout
    can invalidate the current session, the runner defaults to an ABORT state
    until the caller supplies an explicit target selector and an opt-in
    ``allow_logout`` callback.
    """

    def __init__(
        self,
        open_switcher: Callable[[], None],
        select_target: Callable[[], None],
        verify: Callable[[], bool],
        *,
        allow_logout: Optional[Callable[[], bool]] = None,
    ):
        super().__init__(open_switcher, select_target, verify)
        self.allow_logout = allow_logout

    def switch(self) -> AccountSwitchState:
        if self.allow_logout is None or not self.allow_logout():
            self.state = AccountSwitchState.ABORT
            return self.state
        return super().switch()


class WeComEnterpriseSwitcher(SafeAccountSwitcher):
    """Switch among already-logged-in WeCom enterprises without logout."""

    def __init__(self, device, target: str, *, current: str | None = None):
        self.device = device
        self.target = target.strip()
        self.current = current.strip() if current else None
        super().__init__(self._open, self._select, self._verify, allow_logout=lambda: True)

    def _open(self) -> None:
        self.device.click(resource_id="com.tencent.wework:id/nts")

    def _select(self) -> None:
        if not self.target or self.target == self.current:
            raise ValueError("target enterprise must differ from current enterprise")
        self.device.click(text=self.target)

    def _verify(self) -> bool:
        return self.device.wait_text(self.target, timeout=5)
