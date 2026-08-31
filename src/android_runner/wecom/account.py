from __future__ import annotations

from enum import Enum, auto
from pathlib import Path
from typing import Callable

from ..device import UnsafeWeComCheckpoint, WeComCheckpoint, WeComPage


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


class WeComEnterpriseSwitcher(AccountSwitcher):
    """Switch among already-logged-in WeCom enterprises without logout."""

    def __init__(
        self,
        device,
        target: str,
        *,
        current: str | None = None,
        logged_in_enterprises: tuple[str, ...] | list[str],
        checkpoint_directory: Path = Path("logs/checkpoints"),
    ):
        self.device = device
        self.target = target.strip()
        self.current = current.strip() if current else None
        self.logged_in_enterprises = frozenset(
            enterprise.strip() for enterprise in logged_in_enterprises
            if isinstance(enterprise, str) and enterprise.strip()
        )
        self.checkpoint_directory = Path(checkpoint_directory)
        super().__init__(self._open, self._select, self._verify)

    def switch(self) -> AccountSwitchState:
        if (
            not self.target
            or not self.current
            or self.target == self.current
            or self.current not in self.logged_in_enterprises
            or self.target not in self.logged_in_enterprises
        ):
            self.state = AccountSwitchState.ABORT
            return self.state
        return super().switch()

    def _checkpoint(self, expected_page: WeComPage) -> WeComCheckpoint:
        capture = getattr(self.device, "capture_wecom_checkpoint", None)
        if not callable(capture):
            raise UnsafeWeComCheckpoint("device does not support WeCom checkpoints")
        checkpoint = capture(self.checkpoint_directory)
        if not isinstance(checkpoint, WeComCheckpoint):
            raise UnsafeWeComCheckpoint("invalid WeCom checkpoint")
        checkpoint.require_page(expected_page)
        return checkpoint

    def _open(self) -> None:
        self._checkpoint(WeComPage.ACCOUNT_HOME)
        self.device.click(resource_id="com.tencent.wework:id/nts")

    def _select(self) -> None:
        self._checkpoint(WeComPage.ACCOUNT_SWITCHER)
        self.device.click(text=self.target)

    def _verify(self) -> bool:
        self._checkpoint(WeComPage.ACCOUNT_HOME)
        return self.device.wait_text(self.target, timeout=5)


class WeComEnterpriseSwitchCapability:
    """Production-only capability for guarded sequential enterprise switching."""

    def __init__(
        self,
        device,
        *,
        current: str | None,
        logged_in_enterprises: tuple[str, ...] | list[str],
    ):
        self.device = device
        self.current = current
        self.logged_in_enterprises = tuple(logged_in_enterprises)

    def switch_to(self, target: str) -> bool:
        switcher = WeComEnterpriseSwitcher(
            self.device,
            target=target,
            current=self.current,
            logged_in_enterprises=self.logged_in_enterprises,
        )
        if switcher.switch() is not AccountSwitchState.READY:
            return False
        self.current = target
        return True
