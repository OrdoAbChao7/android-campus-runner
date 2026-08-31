"""Flask dashboard backend for android-runner.

Exposes REST endpoints and an SSE stream so the frontend can:
  - Manage accounts (config/accounts.yaml)
  - Manage run configuration (config/dashboard.yaml)
  - Report that direct campus-run start is unavailable without an intent bridge
  - Receive real-time log lines via Server-Sent Events

Run from the ``running/`` directory (or anywhere — paths are anchored to
the directory that contains this file's parent):

    python dashboard/app.py

The server listens on http://127.0.0.1:5050 by default.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterator
from uuid import uuid4

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
from android_runner.cli import (  # noqa: E402
    CAMPUS_RUN_DIRECT_START_AVAILABLE,
    CAMPUS_RUN_DISABLED_MESSAGE,
    DEFAULT_ADB,
    INTENT_BRIDGE_AVAILABLE,
    load_provider_config,
)
from android_runner.device import AndroidDevice  # noqa: E402
from android_runner.wecom.campus_run import open_campus_run  # noqa: E402
from android_runner.location.provider import GpsLocatorProvider  # noqa: E402
from android_runner.location.route import RouteError, validate_route  # noqa: E402
from android_runner.intent import (  # noqa: E402
    IntentPersistenceError,
    IntentUseRegistry,
    IntentValidationError,
    RunIntent,
    RunObservation,
    validate_route_binding,
    route_sha256,
)
from android_runner.runner import run_multi_account_mvp  # noqa: E402

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)

# The bridge is deliberately local and two-step: authorization captures the
# live device checkpoint first, while the start endpoint can only consume the
# resulting one-shot binding.  It never accepts caller-supplied fingerprints.
INTENT_BRIDGE_AVAILABLE = True
CAMPUS_RUN_DIRECT_START_AVAILABLE = True
_pending_authorizations: dict[str, tuple[dict[str, tuple[RunIntent, RunObservation]], IntentUseRegistry, str, str, float]] = {}
_pending_lock = threading.Lock()
_AUTH_CONFIRMATION = "START_CAMPUS_RUN"

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


CONTROL_TOKEN_ENV = "ANDROID_RUNNER_DASHBOARD_TOKEN"


def _require_control_token() -> tuple[dict, int] | None:
    """Fail closed unless a local control token authorizes a write request."""
    expected = os.environ.get(CONTROL_TOKEN_ENV, "")
    if not expected:
        return _err("dashboard control token is not configured", 503)

    provided = request.headers.get("X-Local-Control-Token", "")
    if not provided:
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            provided = authorization.removeprefix("Bearer ")

    # Deliberately compare even an empty candidate once a token is configured.
    matches = hmac.compare_digest(provided, expected)
    if not provided or not matches:
        return _err("a valid local control token is required", 401)
    return None


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

ACCOUNTS_PATH = BASE_DIR / "config" / "accounts.yaml"
GPS_CONFIG_PATH = BASE_DIR / "config" / "gps-locator.yaml"
DASHBOARD_CONFIG_PATH = BASE_DIR / "config" / "dashboard.yaml"
ROUTES_DIR = BASE_DIR / "routes"

_DEFAULT_DASHBOARD_CONFIG: dict = {
    "route": "",
    "serial": "",
}


def _load_dashboard_config() -> dict:
    if not DASHBOARD_CONFIG_PATH.exists():
        return dict(_DEFAULT_DASHBOARD_CONFIG)
    raw = yaml.safe_load(DASHBOARD_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    cfg = dict(_DEFAULT_DASHBOARD_CONFIG)
    if not isinstance(raw, dict):
        return cfg
    route_name = raw.get("route", "")
    if isinstance(route_name, str) and route_name:
        try:
            cfg["route"] = _resolve_route(route_name).name
        except ValueError:
            pass
    serial = raw.get("serial", "")
    if isinstance(serial, str) and _is_valid_serial(serial):
        cfg["serial"] = serial
    return cfg


def _save_dashboard_config(cfg: dict) -> None:
    DASHBOARD_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_CONFIG_PATH.write_text(
        yaml.dump({"route": cfg["route"], "serial": cfg["serial"]}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _accounts_to_yaml_dict(accounts: list[dict]) -> dict:
    return {"accounts": accounts}


_SERIAL_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_ENTERPRISE_RE = re.compile(r"[\w .&()（）-]{1,80}", re.UNICODE)
_PHONE_RE = re.compile(r"\+?[0-9][0-9 -]{4,31}")
_CREDENTIAL_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}")


def _is_valid_serial(value: str) -> bool:
    return value == "" or bool(_SERIAL_RE.fullmatch(value))


def _resolve_route(route_name: object) -> Path:
    """Resolve one validated route basename below ``ROUTES_DIR`` only."""
    if not isinstance(route_name, str) or not route_name or len(route_name) > 128:
        raise ValueError("route must be a non-empty filename")
    if "/" in route_name or "\\" in route_name or Path(route_name).name != route_name:
        raise ValueError("route must be a basename")
    if Path(route_name).suffix.lower() not in {".gpx", ".kml"}:
        raise ValueError("route must be a GPX or KML file")

    routes_root = ROUTES_DIR.resolve()
    candidate = (routes_root / route_name).resolve()
    if candidate.parent != routes_root:
        raise ValueError("route is outside the configured routes directory")
    if not candidate.is_file():
        raise ValueError("route does not exist")
    try:
        validate_route(candidate)
    except (RouteError, ValueError, OSError) as exc:
        raise ValueError("route is invalid") from exc
    return candidate


def _validated_account(entry: object) -> dict:
    """Return a storage-safe account record or raise ``ValueError``."""
    if not isinstance(entry, dict):
        raise ValueError("each account must be an object")
    if "password" in entry or "passwd" in entry:
        raise ValueError("plaintext credential fields are not accepted; use credential_ref")

    enterprise = entry.get("enterprise")
    phone = entry.get("phone")
    credential_ref = entry.get("credential_ref")
    if not isinstance(enterprise, str) or not _ENTERPRISE_RE.fullmatch(enterprise.strip()):
        raise ValueError("enterprise contains unsupported characters")
    if not isinstance(phone, str) or not _PHONE_RE.fullmatch(phone.strip()):
        raise ValueError("phone contains unsupported characters")
    if credential_ref is not None:
        if not isinstance(credential_ref, str) or not _CREDENTIAL_REF_RE.fullmatch(credential_ref.strip()):
            raise ValueError("credential_ref contains unsupported characters")
        credential_ref = credential_ref.strip()

    return {
        "enterprise": enterprise.strip(),
        "phone": phone.strip(),
        "credential_ref": credential_ref,
        "current": bool(entry.get("current", False)),
    }


def _validate_run_intents(
    enterprises: list[str],
    route: Path,
    intents: object,
    intent_registry: object,
) -> dict[str, tuple[RunIntent, RunObservation]]:
    """Reject before adapters are constructed unless every account is authorized."""
    if not enterprises:
        raise IntentValidationError("at least one account requires authorization")
    if (
        not isinstance(intent_registry, IntentUseRegistry)
        or not callable(getattr(intent_registry, "validate_registered", None))
        or not callable(getattr(intent_registry, "consume_reserved", None))
        or not callable(getattr(intent_registry, "require_durable", None))
        or not isinstance(intents, dict)
    ):
        raise IntentValidationError("valid per-account RunIntent authorization is required")
    try:
        intent_registry.require_durable()
    except IntentPersistenceError as exc:
        raise IntentValidationError("durable per-account RunIntent authorization is required") from exc

    validated: dict[str, tuple[RunIntent, RunObservation]] = {}
    for enterprise in enterprises:
        binding = intents.get(enterprise)
        if not isinstance(binding, tuple) or len(binding) != 2:
            raise IntentValidationError("valid per-account RunIntent authorization is required")
        intent, observation = binding
        if not isinstance(intent, RunIntent) or not isinstance(observation, RunObservation):
            raise IntentValidationError("valid per-account RunIntent authorization is required")
        if intent.current_enterprise != enterprise or intent.target_enterprise != enterprise:
            raise IntentValidationError("RunIntent enterprise binding does not match account")
        try:
            validate_route_binding(route, intent, observation, "campus_run.start")
            # This verifies the exact issued binding has not been consumed or
            # reserved by another run before any adapter is created.
            intent_registry.validate_registered(intent)
        except Exception as exc:
            raise IntentValidationError("valid per-account RunIntent authorization is required") from exc
        validated[enterprise] = (intent, observation)
    return validated


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

def _run_task(
    *,
    serial: str,
    route: str,
    intents: object,
    intent_registry: object,
    start_prompt_verified: bool = False,
) -> None:
    """Target function for the background campus-run thread.

    Updates *_state* throughout and broadcasts SSE events.
    """
    log = logging.getLogger("android_runner.dashboard")

    # This check intentionally precedes construction of provider/device
    # adapters. A missing or unregistered RunIntent cannot reach subprocesses
    # or device UI, even through a future caller of this internal function.
    try:
        if intent_registry is None:
            intent_registry = IntentUseRegistry.production()
        route_path = _resolve_route(route)
        loaded_accounts = load_accounts(ACCOUNTS_PATH)
        enterprise_list = ordered_enterprises(loaded_accounts)
        validated_intents = _validate_run_intents(
            enterprise_list,
            route_path,
            intents,
            intent_registry,
        )
    except (Exception,) as exc:
        log.info("refusing dashboard run before provider/UI work: %s", exc)
        return

    global _sse_handler
    handler = _attach_log_handler()
    _sse_handler = handler

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
            config = load_provider_config(GPS_CONFIG_PATH)
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
            device = AndroidDevice(DEFAULT_ADB, effective_serial)
        except Exception as exc:
            log.error("failed to connect to device: %s", exc)
            with _state_lock:
                _state.failed = ["(device)"]
            return

        current_account = next((a.enterprise for a in loaded_accounts if a.current), None)
        if current_account is None and len(enterprise_list) == 1:
            current_account = enterprise_list[0]

        log.info(
            "starting campus-run for %d account(s): %s",
            len(enterprise_list),
            ", ".join(enterprise_list),
        )

        def before_account(enterprise: str, index: int, total: int) -> bool:
            with _state_lock:
                if _state.stop_requested:
                    log.info("stop requested — aborting before account: %s", enterprise)
                    return False
                _state.current = enterprise
            _broadcast({
                "type": "status",
                "event": "account_start",
                "account": enterprise,
                "index": index,
                "total": total,
            })
            log.info("[%d/%d] running for: %s", index, total, enterprise)
            return True

        try:
            multi_result = run_multi_account_mvp(
                device=device,
                provider=provider,
                route=route_path,
                accounts=enterprise_list,
                current_account=current_account,
                logged_in_enterprises=tuple(enterprise_list),
                intents=validated_intents,
                intent_registry=intent_registry,
                start_prompt_verified=start_prompt_verified,
                before_account_fn=before_account,
            )
        except Exception as exc:
            log.error("unexpected error in guarded multi-account run: %s", exc, exc_info=True)
            with _state_lock:
                _state.failed = list(enterprise_list)
            _broadcast({"type": "status", "event": "account_failed", "error": str(exc)})
            return

        with _state_lock:
            _state.completed.extend(multi_result.completed)
            _state.failed.extend(multi_result.failed)
        for enterprise in multi_result.completed:
            _broadcast({"type": "status", "event": "account_done", "account": enterprise})
        for enterprise in multi_result.failed:
            _broadcast({"type": "status", "event": "account_failed", "account": enterprise})

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
    denied = _require_control_token()
    if denied is not None:
        return denied
    body = request.get_json(silent=True)
    if not body or not isinstance(body.get("accounts"), list):
        return _err("request body must contain an 'accounts' list")

    incoming: list[dict] = body["accounts"]

    merged: list[dict] = []
    enterprises: set[str] = set()
    for entry in incoming:
        try:
            account = _validated_account(entry)
        except ValueError as exc:
            return _err(str(exc))
        enterprise_key = account["enterprise"].casefold()
        if enterprise_key in enterprises:
            return _err("duplicate enterprise")
        enterprises.add(enterprise_key)
        merged.append(account)

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
    denied = _require_control_token()
    if denied is not None:
        return denied
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err("request body must be a JSON object")

    # Provider commands, working directory, ADB executable, and account file
    # are deployment-time adapters. This endpoint may only select a route by
    # basename and a device serial; it cannot configure executable behavior.
    forbidden = {"gps_config", "adb", "accounts_file", "commands", "command", "working_directory"}
    if forbidden.intersection(body):
        return _err("provider and executable configuration is not accepted")

    cfg = _load_dashboard_config()
    if "route" in body:
        try:
            cfg["route"] = _resolve_route(body["route"]).name
        except ValueError as exc:
            return _err(str(exc))
    if "serial" in body:
        serial = body["serial"]
        if not isinstance(serial, str) or not _is_valid_serial(serial):
            return _err("serial contains unsupported characters")
        cfg["serial"] = serial

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

@app.route("/api/run/authorize", methods=["POST"])
def run_authorize() -> Response:
    """Capture a live start prompt and issue one durable RunIntent.

    This is intentionally a separate action from ``/api/run/start``.  The
    operator must explicitly provide the confirmation phrase while the phone
    is already connected; device fingerprint and enterprise identity are read
    from ADB/WeCom rather than accepted from the request.
    """
    denied = _require_control_token()
    if denied is not None:
        return denied
    with _state_lock:
        if _state.running:
            return _err("a run is already in progress", 409)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("confirm") != _AUTH_CONFIRMATION:
        return _err("explicit confirmation is required", 400)
    route_name = body.get("route")
    enterprise = body.get("enterprise")
    serial = body.get("serial", "")
    if not isinstance(enterprise, str) or not enterprise.strip():
        return _err("enterprise is required")
    if not isinstance(serial, str) or not _is_valid_serial(serial) or not serial:
        return _err("a connected device serial is required")
    try:
        route = _resolve_route(route_name)
        accounts = load_accounts(ACCOUNTS_PATH)
        names = ordered_enterprises(accounts)
        if names != [enterprise.strip()]:
            return _err("authorization bridge currently supports exactly one configured enterprise", 409)
        device = AndroidDevice(DEFAULT_ADB, serial)
        open_campus_run(device)
        checkpoint = device.capture_wecom_checkpoint(BASE_DIR / "logs" / "checkpoints")
        checkpoint.require_enterprise(enterprise.strip())
        digest = route_sha256(route)
        now = datetime.now(timezone.utc)
        minutes = body.get("max_duration_minutes", 30)
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= float(minutes) <= 120:
            return _err("max_duration_minutes must be between 1 and 120")
        intent = RunIntent(
            intent_id=uuid4().hex,
            adb_serial=serial,
            device_fingerprint=checkpoint.device_fingerprint,
            current_enterprise=enterprise.strip(),
            target_enterprise=enterprise.strip(),
            route_sha256=digest,
            not_before=now - timedelta(seconds=30),
            not_after=now + timedelta(minutes=10),
            max_duration=timedelta(minutes=float(minutes)),
            allowed_action_ids=frozenset({"campus_run.start"}),
        )
        registry = IntentUseRegistry.production(BASE_DIR / "logs" / "intent-use.sqlite3")
        registry.register(intent)
        observation = RunObservation(
            adb_serial=serial,
            device_fingerprint=checkpoint.device_fingerprint,
            route_sha256=digest,
            observed_at=now,
        )
    except Exception as exc:
        logging.getLogger("android_runner.dashboard").info("authorization refused: %s", exc)
        return _err("could not capture a safe WeCom start checkpoint", 409)

    with _pending_lock:
        _pending_authorizations[intent.intent_id] = (
            {enterprise.strip(): (intent, observation)}, registry, route.name, serial,
            time.monotonic() + 600,
        )
    return _ok(intent_id=intent.intent_id, enterprise=enterprise.strip(), route=route.name,
               message="authorization captured; call /api/run/start with this intent_id")


@app.route("/api/run/start", methods=["POST"])
def run_start() -> Response:
    """Start only a previously captured, durable single-use authorization."""
    denied = _require_control_token()
    if denied is not None:
        return denied
    with _state_lock:
        if _state.running:
            return _err("a run is already in progress", 409)
    body = request.get_json(silent=True) or {}
    intent_id = body.get("intent_id") if isinstance(body, dict) else None
    if not isinstance(intent_id, str) or not intent_id:
        return _err("intent_id from /api/run/authorize is required", 409)
    with _pending_lock:
        pending = _pending_authorizations.pop(intent_id, None)
    if pending is None or pending[4] < time.monotonic():
        return _err("authorization is missing or expired", 409)
    intents, registry, route_name, serial, _expires = pending
    if isinstance(body, dict) and (body.get("route", route_name) != route_name or body.get("serial", serial) != serial):
        return _err("route and serial must match the captured authorization", 409)
    with _state_lock:
        thread = threading.Thread(
            target=_run_task,
            kwargs={"serial": serial, "route": route_name, "intents": intents, "intent_registry": registry},
            daemon=True,
        )
        thread.start()
    return _ok(message="guarded campus-run started", intent_id=intent_id)


@app.route("/api/run/stop", methods=["POST"])
def run_stop() -> Response:
    """Request a graceful stop after the current account's run finishes."""
    denied = _require_control_token()
    if denied is not None:
        return denied
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
            "intent_bridge_available": INTENT_BRIDGE_AVAILABLE,
            "direct_campus_run_start_available": CAMPUS_RUN_DIRECT_START_AVAILABLE,
            "campus_run_start_message": CAMPUS_RUN_DISABLED_MESSAGE,
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
                "intent_bridge_available": INTENT_BRIDGE_AVAILABLE,
                "direct_campus_run_start_available": CAMPUS_RUN_DIRECT_START_AVAILABLE,
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
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
