# Android Runner

Windows + Android ADB automation components for supervised WeCom campus-run
tasks. The runner has guarded route, enterprise, and provider controls. The
local dashboard exposes a two-step RunIntent bridge; the CLI remains
intentionally non-starting.

---

## Project status

| Layer | Status | Notes |
|---|---|---|
| Core automation (`src/android_runner/`) | **Guarded internal API** | Route hash, enterprise checkpoints, durable single-use intent consumption, provider shutdown, and multi-account switching |
| CLI (`android-runner` command) | **Safety-gated** | `doctor`, `run-route`, and `provider-status` work; `campus-run` deliberately refuses direct start |
| Account config (`config/accounts.yaml`) | **Validated schema** | YAML schema defined, loader + validator in `accounts.py` |
| Flask backend (`dashboard/app.py`) | **Safety-gated** | `/api/run/authorize` captures a live start checkpoint; `/api/run/start` consumes its one-shot intent |
| Web frontend (`dashboard/static/`) | **MVP** | Local control page keeps authorization and start as separate protected actions |

### Future frontend scope

The included MVP frontend talks to the Flask backend at `http://localhost:5050`; it never bypasses the protected authorization bridge.

**MVP panels:**

1. **运行设置** — token, serial, configured enterprise and route selectors
2. **双步骤控制** — capture checkpoint/authorize, then start the returned intent
3. **今日看板** — progress, stop request, and live status/log stream

**Backend API already available (all at `http://localhost:5050`):**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/accounts` | List account metadata and credential references (never secrets) |
| `POST` | `/api/accounts` | Save accounts; plaintext credential fields are rejected |
| `GET` | `/api/config` | Get dashboard run config |
| `POST` | `/api/config` | Save dashboard run config |
| `GET` | `/api/routes` | List `.gpx`/`.kml` files in `routes/` |
| `POST` | `/api/run/authorize` | Capture the live WeCom start prompt and issue one durable RunIntent (requires token + confirmation phrase) |
| `POST` | `/api/run/start` | Start only the previously authorized intent; route and serial must match the capture |
| `POST` | `/api/run/stop` | Request graceful stop after current account |
| `GET` | `/api/run/status` | Snapshot of run state (JSON) |
| `GET` | `/api/run/stream` | SSE stream — emits `{type:"log", line:"..."}` and `{type:"status", event:"...", ...}` |

**SSE event shapes for the protected bridge:**

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
  app.py                    # Safety-gated Flask backend and local RunIntent bridge
  static/                   # Optional future status UI
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
    credential_ref: "env:WECOM_ACCOUNT_A"
    current: true             # account active on device right now

  - enterprise: "企业名称B"
    phone: "13800000002"
    credential_ref: "env:WECOM_ACCOUNT_B"
```

| Field | Required | Description |
|---|---|---|
| `enterprise` | yes | Display name in WeCom switcher (must match exactly) |
| `phone` | yes | Mobile number used to log in |
| `credential_ref` | no | External secret-store reference; never put a plaintext secret in YAML |
| `current` | no | `true` for the account currently active on device |

---

## Usage

### Start the dashboard status/config service

```powershell
cd running
python dashboard/app.py
# Open http://localhost:5050 in a browser
```

The dashboard exposes `intent_bridge_available: true` through
`GET /api/run/status`. First call `/api/run/authorize` while the intended
device is connected and use the explicit confirmation phrase
`START_CAMPUS_RUN`; then call `/api/run/start` with the returned `intent_id`.
The bridge captures the device fingerprint and WeCom enterprise from the live
screen, so these values are never trusted from the request body.

### CLI — check environment

```powershell
python -m android_runner doctor
```

### CLI — campus-run remains intentionally non-starting

The CLI does not issue authorization tokens or tap **自由跑**. Use the local
dashboard's two-step bridge for a supervised run.

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
| `--verbose` / `-v` | Enable debug-level logging |

### CLI — other commands

```powershell
# Run a single route (GPS stops after)
python -m android_runner run-route --config config/gps-locator.yaml --route routes/smoke-test.gpx --serial SERIAL

# Check GPS provider status
python -m android_runner provider-status --config config/gps-locator.yaml --serial SERIAL
```

---

## Guarded runner path

The dashboard bridge registers durable, single-use `RunIntent` values before
the runner is started.

```
campus-run flow (multi-account)
──────────────────────────────────────────────────────────────────
1. Load accounts from accounts.yaml
2. For each enterprise account:
   a. provider.prepare() and provider.ready() — verify the GPS session
   b. Validate and consume that account's single-use RunIntent
   c. WeCom: 工作台 → 智慧体育 → 校园跑 → 开始校园跑
   d. Only then tap 自由跑
   e. provider.start_route(route.gpx) — play GPS track
   f. provider.stop_verified() — require simulationActive: false
   g. If more accounts remain: switch WeCom enterprise
```

Account switching uses WeCom's built-in enterprise switcher
(`com.tencent.wework:id/nts`). The device must already be logged in to
every target enterprise — the runner does **not** perform a full login.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ANDROID_RUNNER_ADB` | `adb` | Path to the ADB executable |
| `ANDROID_RUNNER_INTENT_STORE` | `logs/intent-use.sqlite3` | Durable SQLite store for issued/consumed RunIntent bindings |

---

## Running tests

```powershell
python -m pytest -q
```

The test suite uses only stdlib and pytest — no device connection required.

---

## Safety notes

- The CLI remains non-starting. Dashboard execution requires the two-step
  `/api/run/authorize` then `/api/run/start` flow and cannot start without a
  live checkpoint and durable intent.
- `run_mvp` stops at the **自由跑** prompt unless it consumes a registered,
  durable, single-use `RunIntent` whose observation and actual route bytes
  match the authorized action.
- Each account has its own RunIntent and verified GPS shutdown; a stop
  verification or max-duration failure enters `SAFE_STOP` and prevents the
  next account switch.
- A durable SQLite record rejects an already-consumed intent after process
  restart. If the durable store cannot be opened, protected runner entrypoints
  fail closed.
- Each `run_mvp` or `run_multi_account_mvp` invocation writes a unique
  state/evidence summary beneath `logs/runs/` (or its injected evidence root).
- `accounts.yaml` is listed in `.gitignore`. Do not override this.
- Dashboard write endpoints require `ANDROID_RUNNER_DASHBOARD_TOKEN` and bind
  to localhost; an absent token fails closed.
