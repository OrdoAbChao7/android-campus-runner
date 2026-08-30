from __future__ import annotations

from enum import Enum, auto

from ..device import AndroidDevice


class CampusRunState(Enum):
    INIT = auto()
    WORKBENCH = auto()
    SMART_SPORTS = auto()
    CAMPUS_RUN = auto()
    START_PROMPT = auto()
    RUNNING = auto()


def next_state(state: CampusRunState) -> CampusRunState:
    order = [CampusRunState.INIT, CampusRunState.WORKBENCH,
             CampusRunState.SMART_SPORTS, CampusRunState.CAMPUS_RUN,
             CampusRunState.START_PROMPT, CampusRunState.RUNNING]
    index = order.index(state)
    return order[min(index + 1, len(order) - 1)]


def open_campus_run(device: AndroidDevice, timeout: float = 15.0) -> CampusRunState:
    """Navigate to the campus-run start prompt using visible text only."""
    device.start_app("com.tencent.wework")
    device.click(text="工作台", timeout=timeout)
    try:
        device.click(text="智慧体育", timeout=timeout)
    except LookupError:
        # Workbench apps may be below the fold; reveal them using a container swipe.
        device.ui.swipe(630, 2300, 630, 700, duration=0.5)
        device.click(text="智慧体育", timeout=timeout)
    device.click(text="校园跑", timeout=timeout)
    device.click(text="开始校园跑", timeout=timeout)
    return CampusRunState.START_PROMPT


def confirm_free_run(device: AndroidDevice, *, allow_start: bool = False,
                     timeout: float = 10.0) -> CampusRunState:
    """Confirm the WeCom free-run prompt only with explicit authorization."""
    if not allow_start:
        raise PermissionError("free-run confirmation requires explicit allow_start")
    device.click(text="自由跑", timeout=timeout)
    return CampusRunState.RUNNING
