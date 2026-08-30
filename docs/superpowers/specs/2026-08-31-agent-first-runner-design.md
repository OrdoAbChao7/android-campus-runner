# Agent-first Android Campus Runner — Design Specification

## Goal

Provide a safe MVP for an authorized Android test device: observe ADB/uiautomator2, navigate Enterprise WeChat to Campus Run, prepare the official GPS Locator provider, execute only an explicitly authorized test run, verify completion, stop simulation, capture evidence, and optionally switch among enterprises already logged in on the phone.

## Safety boundaries

- No mock-location detection bypass, root, hooks, APK modification, or anti-cheat evasion.
- No automated logout/login, OTP, CAPTCHA, or password submission.
- No real Campus Run start without a single-use human authorization; default stops before “自由跑”.
- Provider stop must be verified before completion or another run.
- Credentials use Windows-protected references; plaintext passwords are never committed, returned, or logged.

## Architecture and states

Supervisor Agent plans and diagnoses; Policy Engine validates a typed `RunIntent`; deterministic runner owns adapters for ADB, uiautomator2, WeCom, and GPS Locator; a read-only verifier checks evidence. Actions are observe → precondition → at-most-one action → postcondition.

`IDLE → DEVICE_LOCKED → PREFLIGHT_OK → ACCOUNT_VERIFIED → PAGE_READY → PROVIDER_READY → START_AUTHORIZED → RUNNING_VERIFIED → ROUTE_RUNNING → ROUTE_COMPLETE → APP_RESULT_VERIFIED → PROVIDER_STOPPED → EVIDENCE_CAPTURED → ACCOUNT_SWITCHED → DONE`; ambiguity enters `SAFE_STOP`.

`RunIntent` binds single-use id, exact serial/device fingerprint, enterprises, route SHA-256, time window, max duration, and allowed action IDs. Evidence is append-only under `logs/<run-id>/` with state/events, sanitized screenshots/UI hierarchy, ADB output, provider status, route report, and summary.

