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
