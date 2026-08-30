"""Flask dashboard backend for android-runner.

Exposes REST endpoints and an SSE stream so the frontend can:
  - Manage accounts (config/accounts.yaml)
  - Manage run configuration (config/dashboard.yaml)
  - Start / stop a campus-run task in a background thread
  - Receive real-time log lines via Server-Sent Events

Run from the ``running/`` directory (or anywhere — paths are anchored to
the directory that contains this file's parent):

    python dashboard/app.py

The server listens on http://0.0.0.0:5050 by default.
"""
from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml
from flask import Flask, Response, jsonify, request, stream_with_context

# ---------------------------------------------------------------------------
# Path anchoring
# ---------------------------------------------------------------------------

# dashboard/app.py  →  parent  →  running/
BASE_DIR = Path(__file__).parent.parent

# Make sure the package installed in editable / src layout is importable when
# this script is run directly (e.g. ``python dashboard/app.py``).
_src = BASE_DIR / "src"
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from android_runner.accounts import Account, load_accounts, ordered_enterprises  # noqa: E402
from android_runner.cli import DEFAULT_ADB, load_provider_config  # noqa: E402
from android_runner.device import AndroidDevice  # noqa: E402
from android_runner.location.provider import GpsLocatorProvider  # noqa: E402
from android_runner.runner import run_multi_account_mvp  # noqa: E402

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)

# ---------------------------------------------------------------------------
# CORS — allow every origin (local dev tool, no auth needed)
# ---------------------------------------------------------------------------

@app.after_request
def _add_cors_headers(response: Response) -> Response:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def _options_handler(path: str) -> Response:
    return Response(status=204)


# ---------------------------------------------------------------------------
# Shared run-state
# ---------------------------------------------------------------------------

@dataclass
class RunState:
    """Mutable singleton that tracks the active campus-run session."""

    running: bool = False
    stop_requested: bool = False
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    current: str | None = None
    # Ring-buffer: keep at most MAX_LOG_LINES lines
    log_lines: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None


MAX_LOG_LINES = 500

# Module-level singletons
_state = RunState()
_state_lock = threading.Lock()

# ---------------------------------------------------------------------------
# SSE subscriber queues
# ---------------------------------------------------------------------------

_sse_queues: list[queue.Queue] = []
_sse_lock = threading.Lock()

_SENTINEL = object()  # pushed into a queue to signal "close the connection"


def _broadcast(message: dict) -> None:
    """Push *message* (a dict) as a JSON-encoded SSE event to every subscriber."""
    payload = f"data: {json.dumps(message, ensure_ascii=False)}\n\n"
    with _sse_lock:
        dead: list[queue.Queue] = []
        for q in _sse_queues:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_queues.remove(q)


# ---------------------------------------------------------------------------
# Custom logging handler — captures android_runner log lines
# ---------------------------------------------------------------------------

class _SSELogHandler(logging.Handler):
    """Appends log records to RunState and broadcasts them via SSE."""

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        with _state_lock:
            _state.log_lines.append(line)
            if len(_state.log_lines) > MAX_LOG_LINES:
                _state.log_lines = _state.log_lines[-MAX_LOG_LINES:]
        _broadcast({"type": "log", "line": line})


_sse_handler: _SSELogHandler | None = None


def _attach_log_handler() -> _SSELogHandler:
    handler = _SSELogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.DEBUG)
    logging.getLogger("android_runner").addHandler(handler)
    return handler


def _detach_log_handler(handler: _SSELogHandler) -> None:
    logging.getLogger("android_runner").removeHandler(handler)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _ok(**data) -> tuple[dict, int]:
    """Return a success envelope with HTTP 200."""
    return jsonify({"ok": True, **data}), 200


def _err(message: str, status: int = 400) -> tuple[dict, int]:
    """Return an error envelope with the given HTTP status."""
    return jsonify({"ok": False, "error": message}), status


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

ACCOUNTS_PATH = BASE_DIR / "config" / "accounts.yaml"
GPS_CONFIG_PATH = BASE_DIR / "config" / "gps-locator.yaml"
DASHBOARD_CONFIG_PATH = BASE_DIR / "config" / "dashboard.yaml"
ROUTES_DIR = BASE_DIR / "routes"

_DEFAULT_DASHBOARD_CONFIG: dict = {
    "gps_config": str(GPS_CONFIG_PATH),
    "route": "",
    "serial": "",
    "adb": DEFAULT_ADB,
    "accounts_file": str(ACCOUNTS_PATH),
}


def _load_dashboard_config() -> dict:
    if not DASHBOARD_CONFIG_PATH.exists():
        return dict(_DEFAULT_DASHBOARD_CONFIG)
    raw = yaml.safe_load(DASHBOARD_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    cfg = dict(_DEFAULT_DASHBOARD_CONFIG)
    cfg.update({key: value for key, value in raw.items() if key in cfg})
    return cfg


def _save_dashboard_config(cfg: dict) -> None:
    DASHBOARD_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_CONFIG_PATH.write_text(
        yaml.dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _accounts_to_yaml_dict(accounts: list[dict]) -> dict:
    return {"accounts": accounts}


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

def _run_task(
    *,
    serial: str,
    adb: str,
    gps_config_path: str,
    route: str,
    accounts_file: str,
) -> None:
    """Target function for the background campus-run thread.

    Updates *_state* throughout and broadcasts SSE events.
    """
    global _sse_handler

    handler = _attach_log_handler()
    _sse_handler = handler

    log = logging.getLogger("android_runner.dashboard")

    try:
        with _state_lock:
            _state.running = True
            _state.stop_requested = False
            _state.completed = []
            _state.failed = []
            _state.current = None
            _state.log_lines = []
            _state.started_at = _now_iso()
            _state.finished_at = None

        _broadcast({"type": "status", "event": "started", "started_at": _state.started_at})

        # --- Load provider config ---
        try:
            config = load_provider_config(gps_config_path)
        except Exception as exc:
            log.error("failed to load GPS provider config: %s", exc)
            with _state_lock:
                _state.failed = ["(setup)"]
            return

        effective_serial = serial or str(config.get("serial", ""))
        provider = GpsLocatorProvider(
            config["commands"],
            serial=effective_serial,
            cwd=config.get("working_directory"),
        )

        # --- Connect to device ---
        try:
            device = AndroidDevice(adb, effective_serial)
        except Exception as exc:
            log.error("failed to connect to device: %s", exc)
            with _state_lock:
                _state.failed = ["(device)"]
            return

        # --- Load accounts ---
        try:
            loaded_accounts = load_accounts(accounts_file)
        except Exception as exc:
            log.error("failed to load accounts: %s", exc)
            with _state_lock:
                _state.failed = ["(accounts)"]
            return

        current_account = next((a.enterprise for a in loaded_accounts if a.current), None)
        enterprise_list = ordered_enterprises(loaded_accounts)

        log.info(
            "starting campus-run for %d account(s): %s",
            len(enterprise_list),
            ", ".join(enterprise_list),
        )

        # Intercept each account transition to keep _state.current up-to-date.
        # We wrap run_multi_account_mvp by patching provider.start_route so we
        # can track which account is running.  The simplest approach: iterate
        # accounts ourselves via a wrapping loop that checks stop_requested.
        #
        # Because run_multi_account_mvp is synchronous and long-running we
        # cannot easily inject stop checks mid-run. We honour stop_requested
        # between accounts by using a thin shim: run one account at a time via
        # run_multi_account_mvp with a single-element list, repeating until
        # done or stop is requested.

        from android_runner.workflow import MultiRunResult  # local import for clarity

        for i, enterprise in enumerate(enterprise_list):
            with _state_lock:
                if _state.stop_requested:
                    log.info("stop requested — aborting before account: %s", enterprise)
                    remaining = enterprise_list[i:]
                    _state.failed.extend(remaining)
                    break
                _state.current = enterprise

            _broadcast({"type": "status", "event": "account_start", "account": enterprise,
                        "index": i + 1, "total": len(enterprise_list)})
            log.info("[%d/%d] running for: %s", i + 1, len(enterprise_list), enterprise)

            # Determine which account is currently active on the device for
            # this sub-run (carry over from previous iteration).
            sub_current = current_account if i == 0 else enterprise_list[i - 1]

            try:
                sub_result = run_multi_account_mvp(
                    device=device,
                    provider=provider,
                    route=Path(route),
                    accounts=[enterprise],
                    current_account=sub_current,
                )
            except Exception as exc:
                log.error("unexpected error for account %s: %s", enterprise, exc, exc_info=True)
                with _state_lock:
                    _state.failed.append(enterprise)
                _broadcast({"type": "status", "event": "account_failed", "account": enterprise,
                            "error": str(exc)})
                break

            with _state_lock:
                _state.completed.extend(sub_result.completed)
                _state.failed.extend(sub_result.failed)

            if sub_result.failed:
                _broadcast({"type": "status", "event": "account_failed", "account": enterprise})
                log.error("run failed for: %s — aborting remaining accounts", enterprise)
                with _state_lock:
                    remaining = enterprise_list[i + 1:]
                    _state.failed.extend(remaining)
                break

            _broadcast({"type": "status", "event": "account_done", "account": enterprise})

    except Exception as exc:
        log.error("unhandled error in run task: %s", exc, exc_info=True)

    finally:
        finished = _now_iso()
        with _state_lock:
            _state.running = False
            _state.current = None
            _state.finished_at = finished

        _broadcast({
            "type": "status",
            "event": "finished",
            "finished_at": finished,
            "completed": _state.completed,
            "failed": _state.failed,
        })

        _detach_log_handler(handler)
        _sse_handler = None

        # Signal all SSE connections that the run is over (they will send a
        # final status push and keep the connection alive for future runs).
        _broadcast({"type": "status", "event": "idle"})


# ---------------------------------------------------------------------------
# API — Accounts
# ---------------------------------------------------------------------------

@app.route("/api/accounts", methods=["GET"])
def get_accounts() -> Response:
    """Return accounts from config/accounts.yaml as JSON."""
    if not ACCOUNTS_PATH.exists():
        return _ok(accounts=[])
    try:
        raw = yaml.safe_load(ACCOUNTS_PATH.read_text(encoding="utf-8")) or {}
        accounts = raw.get("accounts", [])
        # Return only the non-secret account metadata and credential reference.
        masked = [
            {
                "enterprise": a.get("enterprise", ""),
                "phone": a.get("phone", ""),
                "credential_ref": a.get("credential_ref"),
                "current": bool(a.get("current", False)),
            }
            for a in accounts
        ]
        return _ok(accounts=masked)
    except Exception as exc:
        return _err(f"failed to read accounts file: {exc}", 500)


@app.route("/api/accounts", methods=["POST"])
def post_accounts() -> Response:
    """Write accounts to config/accounts.yaml.

    Only credential references are accepted. Secret values must remain in an
    external credential store and are never written by this endpoint.
    """
    body = request.get_json(silent=True)
    if not body or not isinstance(body.get("accounts"), list):
        return _err("request body must contain an 'accounts' list")

    incoming: list[dict] = body["accounts"]

    merged: list[dict] = []
    for entry in incoming:
        if not isinstance(entry, dict):
            return _err("each account must be an object")
        if "password" in entry or "passwd" in entry:
            return _err("plaintext credential fields are not accepted; use credential_ref")
        enterprise = str(entry.get("enterprise", "")).strip()
        phone = str(entry.get("phone", "")).strip()
        credential_ref = entry.get("credential_ref")
        if credential_ref is not None:
            credential_ref = str(credential_ref).strip()
        current = bool(entry.get("current", False))

        if not enterprise or not phone:
            return _err("each account must have 'enterprise' and 'phone'")

        merged.append({
            "enterprise": enterprise,
            "phone": phone,
            "credential_ref": credential_ref,
            "current": current,
        })

    try:
        ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ACCOUNTS_PATH.write_text(
            yaml.dump({"accounts": merged}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except Exception as exc:
        return _err(f"failed to write accounts file: {exc}", 500)

    return _ok(saved=len(merged))


# ---------------------------------------------------------------------------
# API — Dashboard config
# ---------------------------------------------------------------------------

@app.route("/api/config", methods=["GET"])
def get_config() -> Response:
    """Return the current dashboard run configuration."""
    try:
        cfg = _load_dashboard_config()
        return _ok(config=cfg)
    except Exception as exc:
        return _err(f"failed to read config: {exc}", 500)


@app.route("/api/config", methods=["POST"])
def post_config() -> Response:
    """Persist dashboard run configuration to config/dashboard.yaml."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("request body must be a JSON object")

    # Only allow known keys to avoid arbitrary data in the file.
    allowed = {"gps_config", "route", "serial", "adb", "accounts_file"}
    cfg = _load_dashboard_config()
    for key in allowed:
        if key in body:
            cfg[key] = body[key]

    try:
        _save_dashboard_config(cfg)
    except Exception as exc:
        return _err(f"failed to write config: {exc}", 500)

    return _ok(config=cfg)


# ---------------------------------------------------------------------------
# API — Routes listing
# ---------------------------------------------------------------------------

@app.route("/api/routes", methods=["GET"])
def get_routes() -> Response:
    """List all .gpx and .kml files in the routes/ directory."""
    if not ROUTES_DIR.is_dir():
        return _ok(routes=[])
    files = sorted(
        p.name
        for p in ROUTES_DIR.iterdir()
        if p.suffix.lower() in {".gpx", ".kml"}
    )
    return _ok(routes=files)


# ---------------------------------------------------------------------------
# API — Task control
# ---------------------------------------------------------------------------

@app.route("/api/run/start", methods=["POST"])
def run_start() -> Response:
    """Start a campus-run task in a background thread.

    Accepts optional JSON overrides::

        {
            "serial":        "...",
            "route":         "filename.gpx",   # relative to routes/ or absolute
            "accounts_file": "/path/to/accounts.yaml"
        }

    Returns 409 if a run is already in progress.
    """
    with _state_lock:
        if _state.running:
            return _err("a run is already in progress", 409)

    body = request.get_json(silent=True) or {}
    cfg = _load_dashboard_config()

    # Resolve overrides (request body takes precedence over dashboard.yaml).
    serial: str = body.get("serial", cfg.get("serial", ""))
    adb: str = body.get("adb", cfg.get("adb", DEFAULT_ADB))
    gps_config: str = body.get("gps_config", cfg.get("gps_config", str(GPS_CONFIG_PATH)))

    accounts_file: str = body.get("accounts_file", cfg.get("accounts_file", str(ACCOUNTS_PATH)))

    route_raw: str = body.get("route", cfg.get("route", ""))
    if not route_raw:
        return _err("'route' is required — set it in config or pass it in the request")

    # Resolve route path: if it's not absolute, look first in routes/ then BASE_DIR.
    route_path = Path(route_raw)
    if not route_path.is_absolute():
        candidate = ROUTES_DIR / route_raw
        if candidate.exists():
            route_path = candidate
        else:
            route_path = BASE_DIR / route_raw

    if not route_path.exists():
        return _err(f"route file not found: {route_path}")

    # Validate accounts file exists before spinning up the thread.
    if not Path(accounts_file).exists():
        return _err(f"accounts file not found: {accounts_file}")

    thread = threading.Thread(
        target=_run_task,
        kwargs={
            "serial": serial,
            "adb": adb,
            "gps_config_path": gps_config,
            "route": str(route_path),
            "accounts_file": accounts_file,
        },
        daemon=True,
        name="campus-run",
    )
    thread.start()

    return _ok(message="run started")


@app.route("/api/run/stop", methods=["POST"])
def run_stop() -> Response:
    """Request a graceful stop after the current account's run finishes."""
    with _state_lock:
        if not _state.running:
            return _err("no run is currently in progress", 409)
        _state.stop_requested = True

    return _ok(message="stop requested — will halt after the current account finishes")


@app.route("/api/run/status", methods=["GET"])
def run_status() -> Response:
    """Return a snapshot of the current run state."""
    with _state_lock:
        data = {
            "running": _state.running,
            "stop_requested": _state.stop_requested,
            "completed": list(_state.completed),
            "failed": list(_state.failed),
            "current": _state.current,
            "total": len(_state.completed) + len(_state.failed),
            "log_lines": list(_state.log_lines),
            "started_at": _state.started_at,
            "finished_at": _state.finished_at,
        }
    return _ok(**data)


# ---------------------------------------------------------------------------
# API — SSE stream
# ---------------------------------------------------------------------------

@app.route("/api/run/stream", methods=["GET"])
def run_stream() -> Response:
    """Server-Sent Events endpoint.

    Each event is a JSON object sent as::

        data: {"type": "log"|"status", ...}\\n\\n

    A heartbeat comment ``": ping\\n\\n"`` is sent every 15 seconds to prevent
    proxy and browser timeouts.
    """
    client_queue: queue.Queue = queue.Queue(maxsize=256)

    with _sse_lock:
        _sse_queues.append(client_queue)

    def _generate() -> Iterator[str]:
        # Send a synthetic "connected" event so the client knows the stream is live.
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"

        # Send the current state immediately so the client can sync on reconnect.
        with _state_lock:
            snapshot = {
                "type": "status",
                "event": "snapshot",
                "running": _state.running,
                "completed": list(_state.completed),
                "failed": list(_state.failed),
                "current": _state.current,
                "log_lines": list(_state.log_lines),
            }
        yield f"data: {json.dumps(snapshot)}\n\n"

        try:
            while True:
                try:
                    # Block with a timeout so we can send heartbeats even when
                    # no events are produced.
                    payload = client_queue.get(timeout=15)
                    if payload is _SENTINEL:
                        break
                    yield payload
                except queue.Empty:
                    # Send a heartbeat comment to keep the connection alive.
                    yield ": ping\n\n"
        finally:
            # Always clean up the queue reference when the client disconnects.
            with _sse_lock:
                try:
                    _sse_queues.remove(client_queue)
                except ValueError:
                    pass

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if behind a reverse proxy
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Configure a basic handler for the root logger so android_runner messages
    # are visible in the terminal as well as in the SSE stream.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
