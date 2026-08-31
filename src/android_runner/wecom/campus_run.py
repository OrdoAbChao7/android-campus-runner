from __future__ import annotations

from enum import Enum, auto
from pathlib import Path

from ..device import AndroidDevice, UnsafeWeComCheckpoint, WeComCheckpoint, WeComPage
from ..intent import (
    IntentReservation,
    IntentUseRegistry,
    IntentValidationError,
    RunIntent,
    RunObservation,
    validate_route_binding,
)


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


def capture_start_prompt_checkpoint(
    device: AndroidDevice,
    directory: Path = Path("logs/checkpoints"),
    *,
    expected_enterprise: str | None = None,
) -> WeComCheckpoint:
    """Capture the manual start boundary; no authorization is consumed here."""
    capture = getattr(device, "capture_wecom_checkpoint", None)
    if not callable(capture):
        raise UnsafeWeComCheckpoint("device does not support WeCom checkpoints")
    checkpoint = capture(directory)
    if not isinstance(checkpoint, WeComCheckpoint):
        raise UnsafeWeComCheckpoint("invalid WeCom checkpoint")
    checkpoint.require_page(WeComPage.START_PROMPT)
    if expected_enterprise is not None:
        checkpoint.require_enterprise(expected_enterprise)
    return checkpoint


def confirm_free_run(
    device: AndroidDevice,
    *,
    intent: RunIntent,
    observation: RunObservation,
    intent_registry: IntentUseRegistry,
    reservation: IntentReservation,
    route: Path,
    start_checkpoint: WeComCheckpoint | None = None,
    action_id: str = "campus_run.start",
    timeout: float = 10.0,
) -> CampusRunState:
    """Finalize this run's reservation immediately before confirming free-run."""
    if not isinstance(intent_registry, IntentUseRegistry):
        raise IntentValidationError("IntentUseRegistry is required")
    if not isinstance(reservation, IntentReservation):
        raise IntentValidationError("IntentReservation is required")
    _verify_start_prompt_checkpoint(device, start_checkpoint, intent)
    validate_route_binding(route, intent, observation, action_id)
    intent_registry.consume_reserved(reservation, intent, observation, action_id)
    device.click(text="自由跑", timeout=timeout)
    return CampusRunState.RUNNING


def _verify_start_prompt_checkpoint(
    device: AndroidDevice,
    expected: WeComCheckpoint,
    intent: RunIntent,
) -> None:
    """Re-observe the start prompt before consuming the one-shot authorization."""
    if not isinstance(expected, WeComCheckpoint):
        raise IntentValidationError("start checkpoint is required")
    if (
        expected.adb_serial != intent.adb_serial
        or expected.device_fingerprint != intent.device_fingerprint
    ):
        raise IntentValidationError("start checkpoint device does not match intent")
    try:
        expected.require_page(WeComPage.START_PROMPT)
        expected.require_enterprise(intent.target_enterprise)
        capture = getattr(device, "capture_wecom_checkpoint", None)
        if not callable(capture):
            raise UnsafeWeComCheckpoint("device does not support WeCom checkpoints")
        observed = capture(Path(expected.hierarchy_path).parent)
        if not isinstance(observed, WeComCheckpoint):
            raise UnsafeWeComCheckpoint("invalid WeCom checkpoint")
        observed.require_page(WeComPage.START_PROMPT)
        observed.require_enterprise(intent.target_enterprise)
    except UnsafeWeComCheckpoint as exc:
        raise IntentValidationError(f"unsafe start checkpoint: {exc}") from exc
    if (
        observed.foreground_package != expected.foreground_package
        or observed.foreground_activity != expected.foreground_activity
        or observed.adb_serial != expected.adb_serial
        or observed.device_fingerprint != expected.device_fingerprint
        or observed.page_fingerprint != expected.page_fingerprint
        or observed.enterprise_identity != expected.enterprise_identity
    ):
        raise IntentValidationError("start checkpoint no longer matches expected fingerprint")
