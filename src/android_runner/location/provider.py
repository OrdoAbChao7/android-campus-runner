from __future__ import annotations

import subprocess
import json
import time
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
    def __init__(self, command_template: list[str] | dict[str, list[str]], serial: str = "", cwd: str | Path | None = None, timeout: float = 300.0, poll_interval: float = 0.5):
        self.commands = command_template if isinstance(command_template, dict) else {"prepare": command_template, "status": command_template, "route": command_template, "stop": command_template}
        self.serial = serial
        self.cwd = str(cwd) if cwd else None
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.last_result: ProviderResult | None = None
        self._unsafe_latched = False

    def _blocked_result(self, operation: str) -> ProviderResult:
        result = ProviderResult([], 1, "", f"GPS Locator is unsafe; {operation} is blocked until simulationActive == false")
        self.last_result = result
        return result

    def prepare(self) -> ProviderResult:
        if self._unsafe_latched:
            return self._blocked_result("prepare")
        result = self._run(command_for_route(self.commands["prepare"], "", self.serial))
        if result.ok and self.commands.get("launch"):
            result = self._run(command_for_route(self.commands["launch"], "", self.serial))
        return result

    def status(self) -> ProviderResult:
        result = self._run(command_for_route(self.commands["status"], "", self.serial))
        if result.ok:
            try:
                payload = json.loads(result.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                payload = None
            if isinstance(payload, dict) and payload.get("simulationActive") is False:
                self._unsafe_latched = False
        return result

    def ready(self) -> bool:
        """Return true only when the provider reports a usable mock location."""
        if self._unsafe_latched:
            return False
        result = self.status()
        if not result.ok:
            return False
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return False
        if not isinstance(payload, dict):
            return False
        return bool(
            payload.get("available")
            and payload.get("mockLocationReady")
            and payload.get("commandReady")
            and payload.get("simulationActive") is False
        )

    def start_route(self, route: Path) -> ProviderResult:
        if self._unsafe_latched:
            return self._blocked_result("start_route")
        validate_route(route)
        return self._run(command_for_route(self.commands["route"], route, self.serial))

    def start_route_with_timeout(self, route: Path, *, timeout: float) -> ProviderResult:
        """Run one route command with the caller's safety duration cap."""
        if timeout <= 0:
            return ProviderResult([], 1, "", "route duration deadline has already expired")
        if self._unsafe_latched:
            return self._blocked_result("start_route")
        validate_route(route)
        return self._run(
            command_for_route(self.commands["route"], route, self.serial),
            timeout=timeout,
        )

    def stop(self) -> ProviderResult:
        return self._run(command_for_route(self.commands["stop"], "", self.serial))

    def stop_verified(self, *, timeout: float | None = None) -> ProviderResult:
        """Stop GPS Locator and confirm its simulation is no longer active."""
        stopped = self.stop()
        if not stopped.ok:
            self._unsafe_latched = True
            return stopped

        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        last_status: ProviderResult | None = None
        while True:
            last_status = self.status()
            if last_status.ok:
                try:
                    payload = json.loads(last_status.stdout.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    payload = None
                if isinstance(payload, dict) and payload.get("simulationActive") is False:
                    self._unsafe_latched = False
                    self.last_result = stopped
                    return stopped
            if time.monotonic() >= deadline:
                stdout = last_status.stdout if last_status is not None else ""
                failure = ProviderResult(
                    stopped.command,
                    1,
                    stdout,
                    "GPS Locator stop could not verify simulationActive == false",
                )
                self._unsafe_latched = True
                self.last_result = failure
                return failure
            time.sleep(self.poll_interval)

    def report(self) -> ProviderResult | None:
        template = self.commands.get("report")
        if template:
            return self._run(command_for_route(template, "", self.serial))
        return self.last_result

    def _run(self, command: list[str], *, timeout: float | None = None) -> ProviderResult:
        try:
            effective_timeout = self.timeout if timeout is None else min(self.timeout, timeout)
            completed = subprocess.run(command, cwd=self.cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=effective_timeout, check=False)
            result = ProviderResult(command, completed.returncode, completed.stdout, completed.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = ProviderResult(command, 1, "", str(exc))
        self.last_result = result
        return result
