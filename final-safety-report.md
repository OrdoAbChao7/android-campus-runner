# Final safety report

Date: 2026-08-31
Branch: `codex/ide-wip-preserve`

## Result

The audited direct-start path is now fail-closed. The current public CLI and
dashboard have no RunIntent bridge and intentionally cannot start Campus Run.
They do not tap **自由跑**, perform login/logout, handle OTP, or bypass a
CAPTCHA.

## Closed findings

| Finding | Implemented control | Verification |
|---|---|---|
| Route substitution | The selected route is re-hashed from its actual bytes immediately before authorization and compared to both `RunIntent` and `RunObservation`. | `tests/test_safety_audit_closure.py` rejects an authorized route A when route B is selected. |
| Enterprise confusion | WeCom checkpoints capture and require the expected enterprise identity; confirmation checks the target enterprise again before consuming an intent or clicking. | `tests/test_enterprise_identity.py` and `tests/test_campus_flow.py`. |
| Dashboard single-account illusion | Dashboard submits one guarded multi-account session; the protected capability calls `ensure_active` before each account, switching or verifying the actual enterprise. | `tests/test_dashboard_security_static.py` and `tests/test_multi_account.py`. |
| Route duration overrun | Route execution receives the authorization's maximum duration, detects timeout with an injectable clock, verifies shutdown, enters `SAFE_STOP`, and does not switch accounts. | `tests/test_duration_watchdog.py`. |
| Unsafe GPS readiness/shutdown | `simulationActive: true` is not ready; only a provider with successful `stop_verified()` qualifies as a safe shutdown. | `tests/test_safety_audit_closure.py` and `tests/test_workflow_cleanup.py`. |
| Replay after restart | `IntentUseRegistry` accepts an injected `IntentUseStore`; production uses a SQLite store at `logs/intent-use.sqlite3` or `ANDROID_RUNNER_INTENT_STORE`. Consumption is atomic and durable. Store failure or a volatile registry is rejected before provider/UI work. | `tests/test_intent_store.py`. |
| Missing execution evidence | Both runner entrypoints create a unique run-id `EvidenceWriter`/`StateMachine` session before provider/UI work and return paths to a sanitized `summary.json`. Evidence failure is fail-closed. | `tests/test_runner_evidence.py`. |
| Misleading public status | README, CLI help/output, dashboard start response, and dashboard status now say that direct Campus Run start is intentionally unavailable without a bridge. | `tests/test_cli_route.py` and `tests/test_dashboard_security_static.py`. |

## Artifacts and operating rules

- Durable intent consumption: `logs/intent-use.sqlite3` by default.
- Per-run evidence: `logs/runs/<run-id>/events.jsonl` and `summary.json`.
- Dashboard status includes `intent_bridge_available: false` and
  `direct_campus_run_start_available: false`.
- A future bridge must issue/register the full immutable intent in the durable
  store; it must not replace a persistence failure with an in-memory registry.

## Verification record

Focused commits:

1. `8833e72` — route binding and verified provider shutdown
2. `21eb2b3` — enterprise-identity checkpoints
3. `d2266bf` — duration watchdog
4. `379c66b` — guarded dashboard multi-account transitions
5. `fc1aba1` — durable intent replay protection
6. `95e39f4` — runner state/evidence summaries

Final verification: `pytest -q` — `130 passed, 3 skipped`.

## Remaining boundaries

- Flask is not installed in this verification environment, so the Flask-backed
  dashboard integration tests are skipped; static dashboard security checks do
  run.
- No real device, WeCom session, GPS provider, login/logout, OTP, or CAPTCHA
  flow was exercised. The repository intentionally does not provide those as
  an unauthorised direct-start path.
- The public dashboard and CLI remain non-starting until a separately reviewed
  external RunIntent bridge is supplied.
