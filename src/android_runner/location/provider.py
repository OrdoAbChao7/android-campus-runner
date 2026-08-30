from __future__ import annotations

import subprocess
import json
from dataclasses import dataclass
from pathlib import Path

from .route import validate_route


@dataclass
class ProviderResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def command_for_route(template: list[str], route: str | Path, serial: str = "") -> list[str]:
    return [str(item).replace("{route}", str(route)).replace("{serial}", serial) for item in template]


class GpsLocatorProvider:
    def __init__(self, command_template: list[str] | dict[str, list[str]], serial: str = "", cwd: str | Path | None = None, timeout: float = 300.0):
        self.commands = command_template if isinstance(command_template, dict) else {"prepare": command_template, "status": command_template, "route": command_template, "stop": command_template}
        self.serial = serial
        self.cwd = str(cwd) if cwd else None
        self.timeout = timeout
        self.last_result: ProviderResult | None = None

    def prepare(self) -> ProviderResult:
        result = self._run(command_for_route(self.commands["prepare"], "", self.serial))
        if result.ok and self.commands.get("launch"):
            result = self._run(command_for_route(self.commands["launch"], "", self.serial))
        return result

    def status(self) -> ProviderResult:
        return self._run(command_for_route(self.commands["status"], "", self.serial))

    def ready(self) -> bool:
        """Return true only when the provider reports a usable mock location."""
        result = self.status()
        if not result.ok:
            return False
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return False
        return bool(payload.get("available") and payload.get("mockLocationReady")
                    and payload.get("commandReady"))

    def start_route(self, route: Path) -> ProviderResult:
        validate_route(route)
        return self._run(command_for_route(self.commands["route"], route, self.serial))

    def stop(self) -> ProviderResult:
        return self._run(command_for_route(self.commands["stop"], "", self.serial))

    def report(self) -> ProviderResult | None:
        template = self.commands.get("report")
        if template:
            return self._run(command_for_route(template, "", self.serial))
        return self.last_result

    def _run(self, command: list[str]) -> ProviderResult:
        try:
            completed = subprocess.run(command, cwd=self.cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=self.timeout, check=False)
            result = ProviderResult(command, completed.returncode, completed.stdout, completed.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = ProviderResult(command, 1, "", str(exc))
        self.last_result = result
        return result
