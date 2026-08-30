from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .adb import ADBClient


@dataclass
class DoctorResult:
    checks: dict[str, tuple[bool, str]]
    device: dict[str, str]


def format_report(result: DoctorResult) -> str:
    lines = ["Android Runner Doctor", ""]
    for name, (ok, detail) in result.checks.items():
        lines.append(f"[{ 'PASS' if ok else 'FAIL' }] {name}: {detail}")
    if result.device:
        lines += ["", "Device:"]
        lines += [f"{key}: {value}" for key, value in result.device.items()]
    return "\n".join(lines)


def run_doctor(adb_path: str) -> DoctorResult:
    checks: dict[str, tuple[bool, str]] = {}
    adb = ADBClient(adb_path)
    try:
        version = adb.run("version")
        checks["adb"] = (version.returncode == 0, version.stdout.splitlines()[0] if version.stdout else version.stderr.strip())
        devices = adb.devices()
    except (OSError, TimeoutError) as exc:
        checks["adb"] = (False, str(exc))
        return DoctorResult(checks, {})
    if not devices:
        checks["Android device"] = (False, "no device (check adb devices -l)")
        return DoctorResult(checks, {})
    device = devices[0]
    checks["Android device"] = (True, device["serial"])
    client = ADBClient(adb_path, device["serial"])
    for name, command in {
        "model": ("getprop", "ro.product.model"),
        "Android": ("getprop", "ro.build.version.release"),
        "SDK": ("getprop", "ro.build.version.sdk"),
        "resolution": ("wm", "size"),
    }.items():
        try:
            checks[name] = (True, client.shell(*command))
        except RuntimeError as exc:
            checks[name] = (False, str(exc))
    packages = client.shell("pm", "list", "packages")
    checks["WeCom installed"] = ("com.tencent.wework" in packages, "com.tencent.wework" if "com.tencent.wework" in packages else "not installed")
    return DoctorResult(checks, device)
