<div align="center">
  <h1>Android Campus Runner</h1>
  <a href="./README.md"><b>English</b></a> | <b>中文</b>
</div>
<br>

# Android Runner

基于 Windows + Android ADB 的企业微信校园跑受控自动化组件。Runner 具备路线哈希、企业切换、一次性 RunIntent 消耗等防护控制；本地面板提供两步授权桥接，CLI 有意保持不可直接启动。

---

## 项目状态

| 层级 | 状态 | 说明 |
|---|---|---|
| 核心自动化（`src/android_runner/`） | **受防护的内部 API** | 路线哈希、企业检查点、持久化一次性 intent 消耗、Provider 关停、多账号切换 |
| CLI（`android-runner` 命令） | **安全门控** | `doctor`、`run-route`、`provider-status` 可用；`campus-run` 有意拒绝直接启动 |
| 账号配置（`config/accounts.yaml`） | **校验过的 Schema** | 已定义 YAML Schema，`accounts.py` 提供加载与校验 |
| Flask 后端（`dashboard/app.py`） | **安全门控** | `/api/run/authorize` 捕获实时启动检查点；`/api/run/start` 消费其一次性 intent |
| Web 前端（`dashboard/static/`） | **MVP** | 本地控制页将授权与启动分为两个受保护操作 |

### 前端范围

内置 MVP 前端与 `http://localhost:5050` 的 Flask 后端通信，绝不绕过受保护的授权桥接。

**MVP 面板：**

1. **运行设置** — token、串号、已配置的企业与路线选择器
2. **双步骤控制** — 先捕获检查点/授权，再启动返回的 intent
3. **今日看板** — 进度、停止请求、实时状态/日志流

**后端 API（均位于 `http://localhost:5050`）：**

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/accounts` | 列出账号元数据与凭据引用（绝不含明文秘密） |
| `POST` | `/api/accounts` | 保存账号；拒绝明文凭据字段 |
| `GET` | `/api/config` | 获取面板运行配置 |
| `POST` | `/api/config` | 保存面板运行配置 |
| `GET` | `/api/routes` | 列出 `routes/` 下的 `.gpx`/`.kml` 文件 |
| `POST` | `/api/run/authorize` | 捕获实时企业微信启动提示并签发持久化 RunIntent（需要 token + 确认口令） |
| `POST` | `/api/run/start` | 仅启动此前已授权的 intent；路线与串号必须与捕获时一致 |
| `POST` | `/api/run/stop` | 请求在当前账号结束后优雅停止 |
| `GET` | `/api/run/status` | 运行状态快照（JSON） |
| `GET` | `/api/run/stream` | SSE 流 — 发送 `{type:"log", line:"..."}` 与 `{type:"status", event:"...", ...}` |

**受保护桥接的 SSE 事件格式：**

```jsonc
// 日志行
{"type": "log", "line": "2025-01-01 12:00:00 INFO android_runner: ..."}

// 状态事件
{"type": "status", "event": "started",       "started_at": "..."}
{"type": "status", "event": "account_start", "account": "企业A", "index": 1, "total": 3}
{"type": "status", "event": "account_done",  "account": "企业A"}
{"type": "status", "event": "account_failed","account": "企业A", "error": "..."}
{"type": "status", "event": "finished",      "finished_at": "...", "completed": [...], "failed": [...]}
{"type": "status", "event": "idle"}
{"type": "status", "event": "snapshot",      "running": bool, "completed": [...], "failed": [...], "current": "...", "log_lines": [...]}
{"type": "connected"}
```

Flask 自动从 `dashboard/static/` 提供静态文件 — 把 `index.html`（可选 `.css`/`.js`）放进去，然后访问 `http://localhost:5050/` 即可。

---

## 环境要求

| 依赖 | 说明 |
|---|---|
| Python 3.11+ | |
| [ADB](https://developer.android.com/tools/releases/platform-tools) | 必须在 `PATH` 上，或通过 `ANDROID_RUNNER_ADB` 设置 |
| [uiautomator2](https://github.com/openatx/uiautomator2) | 通过 `pip` 安装 |
| Flask | `pip install flask pyyaml` |
| [gps-locator desktop-cli](https://github.com/example/gps-locator) | 注入模拟 GPS 的 Node.js 工具 |
| 企业微信 | 安装在 Android 设备上；所有目标企业均已登录 |

---

## 安装

```powershell
pip install -e ".[test]"
pip install flask pyyaml
```

---

## 项目结构

```
config/
  accounts.example.yaml     # 凭据模板 — 复制为 accounts.yaml
  accounts.yaml             # 真实凭据（已被 git 忽略）
  gps-locator.example.yaml  # GPS Provider 配置模板
  gps-locator.yaml          # 真实 GPS 配置（如有则被 git 忽略）
  dashboard.yaml            # 面板运行配置（首次保存时自动创建）
dashboard/
  app.py                    # 安全门控的 Flask 后端与本地 RunIntent 桥接
  static/                   # 可选的状态 UI
logs/                       # 运行日志（除 .gitkeep 外被 git 忽略）
routes/
  smoke-test.gpx            # 快速测试用的示例路线
src/android_runner/
  accounts.py               # 加载并校验 accounts.yaml
  adb.py                    # ADB 客户端封装
  cli.py                    # 命令行入口
  device.py                 # AndroidDevice（uiautomator2 + ADB）
  doctor.py                 # 环境健康检查
  runner.py                 # 高层 MVP 与多账号流程
  workflow.py               # 路线执行与账号切换逻辑
  location/
    provider.py             # GpsLocatorProvider（调用 gps-locator CLI）
    route.py                # GPX/KML 校验
  wecom/
    account.py              # AccountSwitcher 状态机
    campus_run.py           # 企业微信 UI 导航辅助
tests/                      # pytest 测试套件（全部通过）
```

---

## 配置

### 1. GPS Provider — `config/gps-locator.yaml`

复制示例文件并填入设备串号与 gps-locator 路径：

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

### 2. 账号 — `config/accounts.yaml`

将 `config/accounts.example.yaml` 复制为 `config/accounts.yaml`。
**该文件已被 git 忽略 — 切勿提交。**

```yaml
accounts:
  - enterprise: "企业名称A"   # 企业微信切换器中的准确名称
    phone: "13800000001"
    credential_ref: "env:WECOM_ACCOUNT_A"
    current: true             # 设备上当前激活的账号

  - enterprise: "企业名称B"
    phone: "13800000002"
    credential_ref: "env:WECOM_ACCOUNT_B"
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `enterprise` | 是 | 企业微信切换器中的显示名称（必须完全一致） |
| `phone` | 是 | 登录手机号 |
| `credential_ref` | 否 | 外部秘密存储引用；切勿在 YAML 中放明文秘密 |
| `current` | 否 | `true` 表示设备上当前激活的账号 |

---

## 使用

### 启动面板状态/配置服务

```powershell
cd running
python dashboard/app.py
# 在浏览器中打开 http://localhost:5050
```

面板通过 `GET /api/run/status` 暴露 `intent_bridge_available: true`。在目标设备已连接时先调用 `/api/run/authorize` 并使用显式确认口令 `START_CAMPUS_RUN`；然后携带返回的 `intent_id` 调用 `/api/run/start`。桥接会从实时屏幕捕获设备指纹与企业微信企业信息，因此这些值绝不信任请求体。

### CLI — 检查环境

```powershell
python -m android_runner doctor
```

### CLI — campus-run 有意保持不可直接启动

CLI 不签发授权令牌，也不会点击**自由跑**。受监督的运行请使用本地面板的两步桥接。

```powershell
python -m android_runner campus-run `
  --config  config/gps-locator.yaml `
  --route   routes/smoke-test.gpx `
  --serial  YOUR_DEVICE_SERIAL `
  --accounts-file config/accounts.yaml
```

或直接内联指定企业名称：

```powershell
python -m android_runner campus-run `
  --config  config/gps-locator.yaml `
  --route   routes/smoke-test.gpx `
  --serial  YOUR_DEVICE_SERIAL `
  --accounts "企业名称A" "企业名称B" "企业名称C" `
  --current-account "企业名称A"
```

CLI 可选参数：

| 参数 | 说明 |
|---|---|
| `--current-account ENTERPRISE` | 覆盖启动时激活的账号 |
| `--verbose` / `-v` | 启用 debug 级日志 |

### CLI — 其他命令

```powershell
# 运行单条路线（结束后 GPS 自动停止）
python -m android_runner run-route --config config/gps-locator.yaml --route routes/smoke-test.gpx --serial SERIAL

# 检查 GPS Provider 状态
python -m android_runner provider-status --config config/gps-locator.yaml --serial SERIAL
```

---

## 受防护的运行路径

面板桥接在 Runner 启动前注册持久化、一次性消耗的 `RunIntent`。

```
campus-run 流程（多账号）
──────────────────────────────────────────────────────────────────
1. 从 accounts.yaml 加载账号
2. 对每个企业账号：
   a. provider.prepare() 与 provider.ready() — 校验 GPS 会话
   b. 校验并消耗该账号的一次性 RunIntent
   c. 企业微信: 工作台 → 智慧体育 → 校园跑 → 开始校园跑
   d. 之后才点击 自由跑
   e. provider.start_route(route.gpx) — 回放 GPS 轨迹
   f. provider.stop_verified() — 要求 simulationActive: false
   g. 如还有剩余账号：切换企业微信企业
```

账号切换使用企业微信内置的企业切换器
（`com.tencent.wework:id/nts`）。设备必须已登录所有目标企业 — Runner **不会**执行完整登录。

---

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ANDROID_RUNNER_ADB` | `adb` | ADB 可执行文件路径 |
| `ANDROID_RUNNER_INTENT_STORE` | `logs/intent-use.sqlite3` | 已签发/已消耗 RunIntent 绑定的持久化 SQLite 存储 |

---

## 运行测试

```powershell
python -m pytest -q
```

测试套件仅使用标准库和 pytest — 无需连接设备。

### 无线 ADB 排障

配对前，电脑与手机必须位于同一可达局域网。例如电脑在 `192.168.3.x/24` 无法直接访问 `192.168.1.x` 的手机；请将其中一台设备换到另一网络，并从**无线调试**中读取手机当前地址。Mihomo 或 Clash 等 VPN/TUN 客户端也可能抑制 mDNS 发现；如果发现列表为空，先记录当前适配器状态，仅在 ADB 测试期间临时挂起相关隧道，结束后恢复。

---

## 安全说明

- CLI 保持不可直接启动。面板执行必须经过 `/api/run/authorize` 后再 `/api/run/start` 的两步流程，没有实时检查点与持久化 intent 就无法启动。
- `run_mvp` 在**自由跑**提示处停止，除非它消耗了一个已注册、持久化、一次性且观测值与实际路线字节均与授权动作匹配的 `RunIntent`。
- 每个账号都有独立的 RunIntent 与经验证的 GPS 关停；停止校验或最大时长失败会进入 `SAFE_STOP`，阻止下一次账号切换。
- 持久化 SQLite 记录会在进程重启后拒绝已被消耗的 intent。如果持久化存储无法打开，受保护的 Runner 入口会以失败关闭（fail closed）。
- 每次 `run_mvp` 或 `run_multi_account_mvp` 调用都会在 `logs/runs/`（或其注入的证据根目录）下写入唯一的状态/证据摘要。
- `accounts.yaml` 已列入 `.gitignore`。不要覆盖这一设置。
- 面板写操作端点需要 `ANDROID_RUNNER_DASHBOARD_TOKEN` 并仅绑定本机；缺失 token 时以失败关闭。
