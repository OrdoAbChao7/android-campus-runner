from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml

from .doctor import format_report, run_doctor
from .location.provider import GpsLocatorProvider
from .workflow import run_route_with_cleanup


DEFAULT_ADB = os.environ.get("ANDROID_RUNNER_ADB", "E:\\edge download\\scrcpy-win64-v4.1\\adb.exe")


def load_provider_config(path: str | Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("commands"), dict):
        raise ValueError("provider config must contain a commands mapping")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(prog="android-runner")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--adb", default=DEFAULT_ADB)
    route = sub.add_parser("run-route", help="prepare GPS Locator, run a GPX/KML route, and always stop")
    route.add_argument("--config", required=True)
    route.add_argument("--route", required=True)
    route.add_argument("--serial", default="")
    status = sub.add_parser("provider-status", help="query configured GPS Locator readiness")
    status.add_argument("--config", required=True)
    status.add_argument("--serial", default="")
    args = parser.parse_args()
    if args.command == "doctor":
        result = run_doctor(args.adb)
        print(format_report(result))
        return 0 if all(ok for ok, _ in result.checks.values()) else 1
    if args.command == "run-route":
        config = load_provider_config(args.config)
        serial = args.serial or str(config.get("serial", ""))
        provider = GpsLocatorProvider(config["commands"], serial=serial, cwd=config.get("working_directory"))
        prepared = provider.prepare()
        if not prepared.ok:
            print(prepared.stderr or prepared.stdout)
            return prepared.returncode or 1
        result = run_route_with_cleanup(provider, Path(args.route))
        print("route completed" if result else "route failed")
        return 0 if result else 1
    if args.command == "provider-status":
        config = load_provider_config(args.config)
        serial = args.serial or str(config.get("serial", ""))
        provider = GpsLocatorProvider(config["commands"], serial=serial, cwd=config.get("working_directory"))
        result = provider.status()
        print(result.stdout or result.stderr)
        return 0 if result.ok else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
