from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import yaml

from .accounts import load_accounts, ordered_enterprises
from .device import AndroidDevice
from .doctor import format_report, run_doctor
from .location.provider import GpsLocatorProvider
from .runner import run_multi_account_mvp
from .workflow import run_route_with_cleanup


DEFAULT_ADB = os.environ.get("ANDROID_RUNNER_ADB", "E:\\edge download\\scrcpy-win64-v4.1\\adb.exe")


def load_provider_config(path: str | Path) -> dict:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("commands"), dict):
        raise ValueError("provider config must contain a commands mapping")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(prog="android-runner")
    parser.add_argument("--verbose", "-v", action="store_true", help="enable debug logging")
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

    campus = sub.add_parser(
        "campus-run",
        help="validate campus-run inputs; an external single-use RunIntent is required to start",
    )
    campus.add_argument("--config", required=True, help="GPS Locator provider config YAML")
    campus.add_argument("--route", required=True, help="GPX or KML route file")
    campus.add_argument("--serial", required=True, help="ADB device serial")
    campus.add_argument("--adb", default=DEFAULT_ADB, help="path to adb executable")
    accounts_group = campus.add_mutually_exclusive_group(required=True)
    accounts_group.add_argument(
        "--accounts",
        nargs="+",
        metavar="ENTERPRISE",
        help="WeCom enterprise display names to run in order (inline)",
    )
    accounts_group.add_argument(
        "--accounts-file",
        metavar="FILE",
        help="path to accounts.yaml with enterprise/phone and optional credential_ref entries",
    )
    campus.add_argument(
        "--current-account",
        default=None,
        metavar="ENTERPRISE",
        help="enterprise currently active on the device; overrides the 'current' flag in accounts.yaml",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

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

    if args.command == "campus-run":
        print("campus-run requires an externally provided single-use RunIntent; no Campus Run action was started")
        return 1
        config = load_provider_config(args.config)
        serial = args.serial or str(config.get("serial", ""))
        provider = GpsLocatorProvider(config["commands"], serial=serial, cwd=config.get("working_directory"))
        device = AndroidDevice(args.adb, serial)

        if args.accounts_file:
            loaded = load_accounts(args.accounts_file)
            enterprise_list = ordered_enterprises(loaded, start=args.current_account)
            current = args.current_account or next((a.enterprise for a in loaded if a.current), None)
        else:
            enterprise_list = args.accounts
            current = args.current_account

        multi_result = run_multi_account_mvp(
            device=device,
            provider=provider,
            route=Path(args.route),
            accounts=enterprise_list,
            current_account=current,
        )
        print(f"completed: {multi_result.completed}")
        if multi_result.failed:
            print(f"failed:    {multi_result.failed}")
        return 0 if not multi_result.failed else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
