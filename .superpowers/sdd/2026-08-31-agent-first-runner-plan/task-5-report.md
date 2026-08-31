# Task 5 report — dashboard local-control hardening

Implemented local-only dashboard controls in `dashboard/app.py`:

- Server binds to `127.0.0.1` with debug disabled; permissive CORS headers and catch-all OPTIONS routes were removed.
- `POST /api/accounts`, `POST /api/config`, `POST /api/run/start`, and `POST /api/run/stop` require `ANDROID_RUNNER_DASHBOARD_TOKEN` through a constant-time comparison. The token is neither persisted nor returned.
- Account writes reject plaintext credential fields, malformed identifiers, malformed credential references, and duplicate enterprises.
- Dashboard configuration accepts only a validated route basename and serial. Routes are confined to `routes/`, must exist, have a supported extension, and pass `validate_route`. Executable/provider/working-directory request settings are refused.
- `_run_task` now validates all account RunIntent bindings before creating the provider or device adapter, and it uses fixed deployment-time adapters rather than request-provided command/path values.

Tests added or extended:

- Flask endpoint tests cover authenticated mutations, CORS/localhost behavior, identifier validation, route confinement, and no adapter construction without intents.
- `tests/test_dashboard_security_static.py` keeps core security checks runnable when Flask is unavailable locally.

Verification:

```text
python -m compileall -q dashboard/app.py
pytest -q
113 passed, 1 skipped in 0.47s
```

The skipped test module is Flask-dependent; the static dashboard security tests ran in this environment.

## Fix round 1 — complete RunIntent validation

The dashboard pre-adapter guard now mirrors the runner's authorization checks:

- it rejects an empty enterprise list;
- every account must supply a `(RunIntent, RunObservation)` pair whose current and target enterprise both match that account;
- `RunIntent.validate(observation, "campus_run.start")` verifies route SHA-256, ADB serial, device fingerprint, time window, and action ID;
- `IntentUseRegistry.validate_registered` verifies the exact issued intent has not been consumed or reserved.

All failures happen before construction of `GpsLocatorProvider` or `AndroidDevice`. Regression cases cover registered intents with invalid route hash, serial, fingerprint, time, current enterprise, and target enterprise while asserting zero adapter constructions.

Verification for this fix round:

```text
python -m compileall -q dashboard/app.py
pytest -q
114 passed, 1 skipped in 0.46s
```
