from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET

import uiautomator2 as u2

from .adb import ADBClient


WECOM_PACKAGE = "com.tencent.wework"


class UnsafeWeComCheckpoint(RuntimeError):
    """The foreground UI is not a recognized, safe WeCom page."""


class WeComPage(str, Enum):
    START_PROMPT = "start_prompt"
    ACCOUNT_HOME = "account_home"
    ACCOUNT_SWITCHER = "account_switcher"


@dataclass(frozen=True, slots=True)
class WeComCheckpoint:
    """Evidence captured before a guarded WeCom action."""

    screenshot_path: Path
    hierarchy_path: Path
    captured_at: datetime
    foreground_package: str
    foreground_activity: str
    adb_serial: str
    device_fingerprint: str
    page_fingerprint: str
    page: WeComPage
    enterprise_identity: str | None = None

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise UnsafeWeComCheckpoint("checkpoint timestamp must be timezone-aware")
        if self.captured_at.utcoffset() != UTC.utcoffset(self.captured_at):
            raise UnsafeWeComCheckpoint("checkpoint timestamp must be UTC")
        if self.foreground_package != WECOM_PACKAGE:
            raise UnsafeWeComCheckpoint("foreground package is not WeCom")
        if self.foreground_activity not in _KNOWN_WECOM_ACTIVITIES:
            raise UnsafeWeComCheckpoint("foreground activity is not recognized as WeCom")
        if not self.adb_serial or not self.device_fingerprint:
            raise UnsafeWeComCheckpoint("checkpoint device identity is incomplete")
        if len(self.page_fingerprint) != 64 or any(char not in "0123456789abcdef" for char in self.page_fingerprint):
            raise UnsafeWeComCheckpoint("checkpoint page fingerprint is invalid")
        if self.enterprise_identity is not None and not self.enterprise_identity.strip():
            raise UnsafeWeComCheckpoint("checkpoint enterprise identity is invalid")

    def require_page(self, page: WeComPage) -> None:
        if self.page is not page:
            raise UnsafeWeComCheckpoint(f"expected {page.value}, found {self.page.value}")

    def require_enterprise(self, enterprise: str) -> None:
        """Require a captured enterprise identity to match the protected target."""
        expected = enterprise.strip() if isinstance(enterprise, str) else ""
        actual = self.enterprise_identity.strip() if isinstance(self.enterprise_identity, str) else ""
        if not expected or not actual:
            raise UnsafeWeComCheckpoint("checkpoint enterprise identity is incomplete")
        if actual.casefold() != expected.casefold():
            raise UnsafeWeComCheckpoint("checkpoint enterprise identity does not match expected enterprise")


_UNSAFE_PAGE_MARKERS = (
    "login", "log in", "sign in", "logout", "log out", "otp", "one-time password",
    "verification code", "captcha", "re-auth", "authenticate", "登录", "退出登录",
    "验证码", "人机验证", "安全验证", "身份验证", "重新登录",
)

_KNOWN_WECOM_ACTIVITIES = frozenset({
    "com.tencent.wework.launch.WwMainActivity",
    "com.tencent.wework.common.webview.WwWebActivity",
})
_SAFE_PAGE_SIGNATURES = {
    WeComPage.START_PROMPT: (
        "自由跑",
        "com.tencent.wework:id/campus_run_free_run",
    ),
    WeComPage.ACCOUNT_HOME: (
        "工作台",
        "com.tencent.wework:id/nts",
    ),
    WeComPage.ACCOUNT_SWITCHER: (
        "切换企业",
        "com.tencent.wework:id/enterprise_switcher",
    ),
}
_ENTERPRISE_ID_RESOURCE_IDS = frozenset({
    "com.tencent.wework:id/enterprise_name",
    "com.tencent.wework:id/enterprise_name_text",
    "com.tencent.wework:id/enterprise_name_tv",
    "com.tencent.wework:id/corp_name",
    "com.tencent.wework:id/org_name",
})


def _hierarchy_metadata(hierarchy: str) -> tuple[str, frozenset[str], frozenset[str], str | None]:
    """Extract only stable UI data used by the narrow page allowlist."""
    try:
        root = ET.fromstring(hierarchy)
    except ET.ParseError as exc:
        raise UnsafeWeComCheckpoint("unable to parse UI hierarchy") from exc
    nodes = []
    texts: set[str] = set()
    resource_ids: set[str] = set()
    enterprise_identities: set[str] = set()
    for node in root.iter():
        attributes = tuple(
            (name, node.attrib.get(name, ""))
            for name in ("class", "resource-id", "text", "content-desc")
            if node.attrib.get(name, "")
        )
        if attributes:
            nodes.append(attributes)
        text = node.attrib.get("text", "")
        resource_id = node.attrib.get("resource-id", "")
        if text:
            texts.add(text)
        if resource_id:
            resource_ids.add(resource_id)
            if resource_id in _ENTERPRISE_ID_RESOURCE_IDS and text.strip():
                enterprise_identities.add(text.strip())
    if not nodes:
        raise UnsafeWeComCheckpoint("UI hierarchy has no semantic nodes")
    canonical = repr(tuple(nodes)).encode("utf-8")
    if len(enterprise_identities) > 1:
        raise UnsafeWeComCheckpoint("checkpoint enterprise identity is ambiguous")
    enterprise_identity = next(iter(enterprise_identities), None)
    return (
        hashlib.sha256(canonical).hexdigest(),
        frozenset(texts),
        frozenset(resource_ids),
        enterprise_identity,
    )


def classify_wecom_page(*, package: str, activity: str, hierarchy: str) -> tuple[WeComPage, str]:
    """Recognize only pages where the runner has a narrowly defined safe action."""
    if package != WECOM_PACKAGE:
        raise UnsafeWeComCheckpoint("foreground package is not WeCom")
    if activity not in _KNOWN_WECOM_ACTIVITIES:
        raise UnsafeWeComCheckpoint("foreground activity is not recognized as WeCom")
    fingerprint, texts, resource_ids, _enterprise_identity = _hierarchy_metadata(hierarchy)
    normalized = hierarchy.casefold()
    if any(marker in normalized for marker in _UNSAFE_PAGE_MARKERS):
        raise UnsafeWeComCheckpoint("authentication or re-authentication screen is unsafe")
    for page, (required_text, required_resource_id) in _SAFE_PAGE_SIGNATURES.items():
        if required_text in texts and required_resource_id in resource_ids:
            return page, fingerprint
    raise UnsafeWeComCheckpoint("unknown WeCom page")


def selector_candidates(*, resource_id: str | None = None, text: str | None = None,
                        content_desc: str | None = None, xpath: str | None = None):
    values = [("resource_id", resource_id), ("text", text),
              ("content_desc", content_desc), ("xpath", xpath)]
    return [(kind, value) for kind, value in values if value]


class AndroidDevice:
    def __init__(self, adb_path: str, serial: str):
        self.adb = ADBClient(adb_path, serial)
        self.serial = serial
        self.ui = u2.connect(serial)

    def start_app(self, package: str) -> None:
        result = self.adb.run("shell", "monkey", "-p", package, "1")
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"failed to start {package}")

    def stop_app(self, package: str) -> None:
        self.ui.app_stop(package)

    def back(self) -> None:
        self.ui.press("back")

    def home(self) -> None:
        self.ui.press("home")

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ui.screenshot(str(path))

    def hierarchy(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.ui.dump_hierarchy(), encoding="utf-8")

    def capture_wecom_checkpoint(self, directory: Path) -> WeComCheckpoint:
        """Capture auditable UI evidence and reject every unrecognized foreground page."""
        captured_at = datetime.now(UTC)
        current = self.ui.app_current()
        package = str(current.get("package") or "")
        activity = str(current.get("activity") or "")
        stamp = captured_at.strftime("%Y%m%dT%H%M%S%fZ")
        screenshot_path = Path(directory) / f"wecom-{stamp}.png"
        hierarchy_path = Path(directory) / f"wecom-{stamp}.xml"
        self.screenshot(screenshot_path)
        self.hierarchy(hierarchy_path)
        hierarchy = hierarchy_path.read_text(encoding="utf-8")
        page, fingerprint = classify_wecom_page(
            package=package,
            activity=activity,
            hierarchy=hierarchy,
        )
        _, _, _, enterprise_identity = _hierarchy_metadata(hierarchy)
        if enterprise_identity is None:
            raise UnsafeWeComCheckpoint("checkpoint enterprise identity is unavailable")
        device_fingerprint = self.adb.shell("getprop", "ro.build.fingerprint")
        return WeComCheckpoint(
            screenshot_path=screenshot_path,
            hierarchy_path=hierarchy_path,
            captured_at=captured_at,
            foreground_package=package,
            foreground_activity=activity,
            adb_serial=self.serial,
            device_fingerprint=device_fingerprint,
            page_fingerprint=fingerprint,
            page=page,
            enterprise_identity=enterprise_identity,
        )

    def wait_text(self, text: str, timeout: float = 10.0) -> bool:
        return self.ui(text=text).wait(timeout=timeout)

    def click(self, *, resource_id: str | None = None, text: str | None = None,
              content_desc: str | None = None, xpath: str | None = None,
              timeout: float = 10.0) -> None:
        for kind, value in selector_candidates(resource_id=resource_id, text=text,
                                               content_desc=content_desc, xpath=xpath):
            selector = self.ui(resourceId=value) if kind == "resource_id" else \
                self.ui(text=value) if kind == "text" else \
                self.ui(description=value) if kind == "content_desc" else self.ui.xpath(value)
            if selector.wait(timeout=timeout):
                selector.click()
                return
        raise LookupError("element not found")
