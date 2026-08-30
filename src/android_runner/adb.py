from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


def parse_devices(output: str) -> list[dict[str, str]]:
    devices = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, rest = parts[0], parts[1:]
        if rest[0] != "device":
            continue
        item = {"serial": serial, "state": rest[0]}
        for field in rest[1:]:
            if field.startswith("model:"):
                item["model"] = field.removeprefix("model:")
        devices.append(item)
    return devices


@dataclass
class ADBClient:
    executable: str = "adb"
    serial: str | None = None
    timeout: float = 30.0

    def command(self, *args: str) -> list[str]:
        cmd = [self.executable]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd + list(args)

    def run(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command(*args), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout or self.timeout, check=False,
        )

    def devices(self) -> list[dict[str, str]]:
        return parse_devices(self.run("devices", "-l").stdout)

    def shell(self, *args: str) -> str:
        result = self.run("shell", *args)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"adb shell failed: {result.returncode}")
        return result.stdout.strip()

    def screenshot(self, path: Path) -> None:
        result = subprocess.run(self.command("exec-out", "screencap", "-p"), capture_output=True, timeout=self.timeout)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace").strip() or "screenshot failed")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result.stdout)

    def dump_hierarchy(self, path: Path) -> None:
        result = self.run("exec-out", "uiautomator", "dump", "/dev/tty")
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "hierarchy dump failed")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.stdout, encoding="utf-8")
