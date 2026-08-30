# Plan: Agent-first safe MVP hardening and execution kernel

> **For the implementing agent:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to execute this plan task-by-task.

## Global Constraints

- Work only in the isolated worktree `E:\Projects\school\running\.worktrees\agent-first-runner`.
- Preserve the official GPS Locator provider; do not add or reference 标枪定位.
- Default behavior must not tap the Campus Run “自由跑” button.
- Never store or expose plaintext account passwords; use `credential_ref`.
- Provider shutdown is mandatory and verified; no `--keep-gps` escape hatch in production paths.
- Use typed adapters and existing ADB path conventions; do not add arbitrary shell execution.
- Run `pytest -q` after every task and keep changes small and reviewable.

## Task 1 — Secure account model

**Files:** `src/android_runner/accounts.py`, `config/accounts.example.yaml`, `tests/test_accounts.py`, `tests/test_multi_account.py`.

Replace password fields with `credential_ref: str | None`; reject plaintext `password`/`passwd` keys in production loader, while allowing test-only injected resolver values. Keep ordering/current-account behavior. Add tests proving secrets are absent from serialized accounts and error messages.

## Task 2 — Typed RunIntent and state/evidence kernel

**Files:** new `src/android_runner/intent.py`, `src/android_runner/state.py`, `src/android_runner/evidence.py`; tests under `tests/`.

Implement immutable intent validation, route hashing, single-use consumption, exact serial/fingerprint checks, and an append-only JSONL journal. Add explicit states listed in the design spec and reject illegal transitions. Test replay, duplicate intent, device drift, and SAFE_STOP transitions.

## Task 3 — Verified provider lifecycle and safe workflow ordering

**Files:** `src/android_runner/location/provider.py`, `src/android_runner/workflow.py`, `src/android_runner/runner.py`, tests.

Move provider readiness before any start prompt/action. Add `stop_verified()` polling GPS Locator status until `simulationActive == false`; failure blocks completion and the next run. Remove/disable `keep_gps` in production APIs. Make the default Campus Run flow stop at the prompt unless a single-use `RunIntent` explicitly authorizes start.

## Task 4 — WeCom checkpoint and account switching guard

**Files:** `src/android_runner/wecom/campus_run.py`, `src/android_runner/wecom/account.py`, new checkpoint helper, tests.

Add package/activity/page fingerprint checks and a manual checkpoint that captures screenshot, hierarchy, foreground package/activity, and timestamp. Permit switching only to a distinct already-logged-in enterprise after verified app completion and provider stop. Unknown UI, logout, login, OTP, and CAPTCHA must produce SAFE_STOP.

## Task 5 — Dashboard safety boundary

**Files:** `dashboard/app.py`, new dashboard tests, `.gitignore`, `README.md`.

Bind only to `127.0.0.1`; require a local control token for mutating endpoints; never return passwords; accept only named route/account IDs and validated paths; remove arbitrary command execution. Dashboard must invoke the same RunIntent/policy path as CLI.

## Task 6 — Verification and release gate

Run unit tests, static secret scan, CLI dry-run, and (when the authorized phone is connected) one supervised route followed by two additional runs. Verify `adb devices -l`, provider stop status, evidence files, and no accidental “自由跑” tap. Review public diff before any future push.

