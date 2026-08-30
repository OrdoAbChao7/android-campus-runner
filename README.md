# Android Runner

Windows + Android ADB automation runner for WeCom campus-run tasks.

Navigates the WeCom (企业微信) **智慧体育 → 校园跑** UI automatically,
plays a GPX/KML route through a GPS-mock provider, and switches between
multiple enterprise accounts so every account records a completed run —
all without touching the phone.

---

## Project status

| Layer | Status | Notes |
|---|---|---|
| Core automation (`src/android_runner/`) | **Complete** | ADB, uiautomator2, WeCom UI navigation, GPS provider, multi-account loop |
| CLI (`android-runner` command) | **Complete** | `doctor`, `run-route`, `provider-status`, `campus-run` sub-commands |
| Account config (`config/accounts.yaml`) | **Complete** | YAML schema defined, loader + validator in `accounts.py` |
| Flask backend (`dashboard/app.py`) | **Complete** | All REST + SSE endpoints implemented, background thread, log capture |
| Web frontend (`dashboard/static/`) | **TODO** | Directory exists but is empty — next agent must build the HTML/JS/CSS dashboard |

### What the next agent needs to build

A single-file (or minimal-file) web frontend in `dashboard/static/` that talks to the already-finished Flask backend at `http://localhost:5050`.

**Required pages / panels:**

1. **账号管理** — table of accounts (`enterprise`, `phone`, `password` masked as `****`), add / edit / delete rows, save button (`POST /api/accounts`)
2. **运行配置** — form for `serial`, `route` (dropdown from `GET /api/routes`), `gps_config`, `adb`, `keep_gps`; save button (`POST /api/config`)
3. **今日看板** — progress cards per account (pending / running / done / failed), Start / Stop buttons, real-time log console fed by `GET /api/run/stream` (SSE)

**Backend API already available (all at `http://localhost:5050`):**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/accounts` | List accounts (passwords masked as `****`) |
| `POST` | `/api/accounts` | Save accounts; empty password string = keep existing |
| `GET` | `/api/config` | Get dashboard run config |
| `POST` | `/api/config` | Save dashboard run config |
| `GET` | `/api/routes` | List `.gpx`/`.kml` files in `routes/` |
| `POST` | `/api/run/start` | Start campus-run task (background thread) |
| `POST` | `/api/run/stop` | Request graceful stop after current account |
| `GET` | `/api/run/status` | Snapshot of run state (JSON) |
| `GET` | `/api/run/stream` | SSE stream — emits `{type:"log", line:"..."}` and `{type:"status", event:"...", ...}` |

**SSE event shapes:**

```jsonc
// Log line
{"type": "log", "line": "2025-01-01 12:00:00 INFO android_runner: ..."}

// Status events
{"type": "status", "event": "started",       "started_at": "..."}
{"type": "status", "event": "account_start", "account": "企业A", "index": 1, "total": 3}
{"type": "status", "event": "account_done",  "account": "企业A"}
{"type": "status", "event": "account_failed","account": "企业A", "error": "..."}
{"type": "status", "event": "finished",      "finished_at": "...", "completed": [...], "failed": [...]}
{"type": "status", "event": "idle"}
{"type": "status", "event": "snapshot",      "running": bool, "completed": [...], "failed": [...], "current": "...", "log_lines": [...]}
{"type": "connected"}
```

**Flask serves static files** from `dashboard/static/` automatically — just drop `index.html` (+ optional `.css`/`.js`) there and open `http://localhost:5050/`.

To do this, the next agent should add to `app.py`:

```python
from flask import send_from_directory

@app.route("/")
def index():
    return send_from_directory(Path(__file__).parent / "static", "index.html")

@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(Path(__file__).parent / "static", filename)
```

---

## Requirements

| Dependency | Notes |
|---|---|
| Python 3.11+ | |
| [ADB](https://developer.android.com/tools/releases/platform-tools) | Must be on `PATH` or set via `ANDROID_RUNNER_ADB` |
| [uiautomator2](https://github.com/openatx/uiautomator2) | Installed via `pip` |
| Flask | `pip install flask pyyaml` |
| [gps-locator desktop-cli](https://github.com/example/gps-locator) | Node.js tool that injects mock GPS |
| WeCom (企业微信) | Installed on Android device; all target enterprises already logged in |

---

## Installation

```powershell
pip install -e ".[test]"
pip install flask pyyaml
```

---

## Project layout

```
config/
  accounts.example.yaml     # credential template — copy to accounts.yaml
  accounts.yaml             # real credentials (git-ignored)
  gps-locator.example.yaml  # GPS provider config template
  gps-locator.yaml          # real GPS config (git-ignored if you add it)
  dashboard.yaml            # dashboard run config (auto-created on first save)
dashboard/
  app.py                    # Flask backend — COMPLETE
  static/                   # Web frontend — TO BE BUILT
    index.html              # (not yet created)
logs/                       # runtime logs (git-ignored except .gitkeep)
routes/
  smoke-test.gpx            # sample route for quick testing
src/android_runner/
  accounts.py               # load & validate accounts.yaml
  adb.py                    # ADB client wrapper
  cli.py                    # command-line entry point
  device.py                 # AndroidDevice (uiautomator2 + ADB)
  doctor.py                 # environment health checks
  runner.py                 # high-level MVP & multi-account flows
  workflow.py               # route execution & account-switch logic
  location/
    provider.py             # GpsLocatorProvider (calls gps-locator CLI)
    route.py                # GPX/KML validation
  wecom/
    account.py              # AccountSwitcher state machine
    campus_run.py           # WeCom UI navigation helpers
tests/                      # pytest test suite (all passing)
```

---

## Configuration

### 1. GPS provider — `config/gps-locator.yaml`

Copy the example and fill in your device serial and gps-locator path:

```yaml
serial: "YOUR_DEVICE_SERIAL"
working_directory: "./gps-locator/desktop-cli"
commands:
  prepare: ["node", "gps-lab.mjs", "prepare", "--serial", "{serial}"]
  launch:  ["adb", "-s", "{serial}", "shell", "monkey", "-p", "com.gpsupdater.app", "1"]
  status:  ["node", "gps-lab.mjs", "status",  "--serial", "{serial}"]
  route:   ["node", "gps-lab.mjs", "route",   "--serial", "{serial}", "--file", "{route}"]
  stop:    ["node", "gps-lab.mjs", "stop",    "--serial", "{serial}"]
  report:  ["node", "gps-lab.mjs", "report",  "--output", "logs/gps-report", "--serial", "{serial}"]
```

### 2. Accounts — `config/accounts.yaml`

Copy `config/accounts.example.yaml` to `config/accounts.yaml`.  
**This file is git-ignored — never commit it.**

```yaml
accounts:
  - enterprise: "企业名称A"   # exact name in WeCom account switcher
    phone: "13800000001"
    password: "your_password"
    current: true             # account active on device right now

  - enterprise: "企业名称B"
    phone: "13800000002"
    password: "your_password"
```

| Field | Required | Description |
|---|---|---|
| `enterprise` | yes | Display name in WeCom switcher (must match exactly) |
| `phone` | yes | Mobile number used to log in |
| `password` | yes | Login password |
| `current` | no | `true` for the account currently active on device |

---

## Usage

### Start the dashboard (recommended)

```powershell
cd running
python dashboard/app.py
# Open http://localhost:5050 in a browser
```

### CLI — check environment

```powershell
python -m android_runner doctor
```

### CLI — run campus-run for multiple accounts

```powershell
python -m android_runner campus-run `
  --config  config/gps-locator.yaml `
  --route   routes/smoke-test.gpx `
  --serial  YOUR_DEVICE_SERIAL `
  --accounts-file config/accounts.yaml
```

Or specify enterprise names inline:

```powershell
python -m android_runner campus-run `
  --config  config/gps-locator.yaml `
  --route   routes/smoke-test.gpx `
  --serial  YOUR_DEVICE_SERIAL `
  --accounts "企业名称A" "企业名称B" "企业名称C" `
  --current-account "企业名称A"
```

CLI optional flags:

| Flag | Description |
|---|---|
| `--current-account ENTERPRISE` | Override which account is active at startup |
| `--keep-gps` | Skip stopping GPS provider after all runs |
| `--verbose` / `-v` | Enable debug-level logging |

### CLI — other commands

```powershell
# Run a single route (GPS stops after)
python -m android_runner run-route --config config/gps-locator.yaml --route routes/smoke-test.gpx --serial SERIAL

# Check GPS provider status
python -m android_runner provider-status --config config/gps-locator.yaml --serial SERIAL
```

---

## How it works

```
campus-run flow (multi-account)
──────────────────────────────────────────────────────────────────
1. Load accounts from accounts.yaml
2. provider.prepare()  — start GPS mock session on device    ─┐
3. For each enterprise account:                               │ GPS stays on
   a. WeCom: 工作台 → 智慧体育 → 校园跑 → 开始校园跑         │ between runs
   b. Tap 自由跑                                              │
   c. provider.start_route(route.gpx) — play GPS track       │
   d. If more accounts remain: switch WeCom enterprise       ─┘
4. provider.stop()  — end GPS mock session
```

Account switching uses WeCom's built-in enterprise switcher
(`com.tencent.wework:id/nts`). The device must already be logged in to
every target enterprise — the runner does **not** perform a full login.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANDROID_RUNNER_ADB` | `adb` | Path to the ADB executable |

---

## Running tests

```powershell
python -m pytest -q
```

The test suite uses only stdlib and pytest — no device connection required.

---

## Safety notes

- `run_mvp` stops at the **自由跑** prompt by default (`allow_start=False`).
  Pass `allow_start=True` explicitly to proceed.
- `SafeAccountSwitcher` never switches accounts unless `allow_logout` returns
  `True`, preventing accidental session invalidation.
- `accounts.yaml` is listed in `.gitignore`. Do not override this.
- The dashboard has no authentication — run it on localhost only.
