from __future__ import annotations

from pathlib import Path

import uiautomator2 as u2

from .adb import ADBClient


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
