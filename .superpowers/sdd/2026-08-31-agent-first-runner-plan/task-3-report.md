# Task 3 report — verified GPS lifecycle and guarded start

## Delivered

- Added `GpsLocatorProvider.stop_verified()`: it issues the configured official GPS Locator `stop` command, then polls `status` until the JSON response reports `simulationActive: false`. A stop command error, malformed/failed status, or timeout is returned as a failed `ProviderResult`.
- Moved provider preparation/readiness ahead of Campus Run navigation in `run_mvp`; readiness failure prevents the start prompt and invokes safe cleanup.
- Removed the production `keep_gps` path from the CLI and workflow APIs. Completion now always performs verified shutdown.
- Default flows do not tap `自由跑`. `run_mvp` consumes a registered, single-use `RunIntent` with matching `RunObservation` before confirmation; multi-account runner consumes one intent per account.
- A verified-stop failure does not mark the failed account completed, prevents the next account, and exposes `RunState.SAFE_STOP` in `MultiRunResult`.

## Regression coverage

- Provider stop verification: success only after inactive simulation; timeout/failure is unsuccessful.
- Provider readiness occurs before opening the start prompt.
- No `自由跑` confirmation occurs without a valid single-use intent, including when obsolete `allow_start=True` is supplied.
- A failed verified stop prevents completion and results in `SAFE_STOP`.
- CLI rejects the removed `--keep-gps` argument.

## Verification

`pytest -q` completed with `70 passed, 1 skipped`.

## Compatibility note

The cleanup adapter calls `stop_verified()` when a provider supplies it; simple existing test/dry-run doubles without that method continue to use `stop()`. Production `GpsLocatorProvider` always supplies verified shutdown.

## Fix round 1

- Removed the workflow `authorize_start` callback and the runner `allow_start` parameter. Multi-account execution now receives and consumes a registered `RunIntent` plus matching `RunObservation` for every account; missing or replayed authorization reaches `SAFE_STOP` without confirming the UI.
- Added a provider-session unsafe latch. Any failed `stop_verified()` blocks `prepare`, `ready`, and `start_route` until a later status confirmation reports `simulationActive: false`.
- Provider shutdown is verified after every account. A failed stop clears completion for that account and prevents opening or confirming the next account; the following account is re-prepared and checked for readiness.
- Updated stale CLI/README language that advertised keeping GPS active or `allow_start` authorization.

Verification after this fix round: `pytest -q` completed with `76 passed, 1 skipped`.

## Fix round 2

- `confirm_free_run()` is now an atomic authorization boundary: it accepts only a `RunIntent`, matching `RunObservation`, and `IntentUseRegistry`, consumes the intent, and then clicks the UI. The public `allow_start` boolean has been removed.
- Dashboard execution and persisted configuration no longer accept or pass `keep_gps` / `stop_provider_on_finish`; each delegated account flow uses verified shutdown.
- `MvpRunResult.state` now returns `SAFE_STOP` when verified cleanup fails after an authorized route, rather than reporting an idle state after a failed stop.
- Rewrote the README flow so it documents per-account intent consumption, readiness checks, and verified shutdown before account switching.

Verification after this fix round: `python -m py_compile dashboard/app.py` and `pytest -q` completed with `78 passed, 1 skipped`.

## Fix round 3

- CLI and dashboard have no safe RunIntent input or issuance path, so their campus-run controls now explicitly refuse execution with a readable external-RunIntent requirement instead of silently attempting a run.
- `run_multi_account_mvp()` similarly returns `SAFE_STOP` before provider preparation when intents are absent; no prompt or confirmation can occur.

Verification after this fix round: `python -m py_compile dashboard/app.py` and `pytest -q` completed with `80 passed, 1 skipped`.

## Fix round 4 — multi-account authorization preflight

- Added a runner-layer preflight before `provider.prepare()`/`ready()` or any WeCom UI action.
- Every requested account must have a two-item `(RunIntent, RunObservation)` tuple with the expected types, matching `current_enterprise` and `target_enterprise`, and a valid action binding.
- Missing accounts, empty/`None` pairs, malformed or wrong-typed values, unusable registries, and enterprise mismatches now return `SAFE_STOP` with a clear authorization message and leave provider/device state untouched.
- Added parametrized regressions covering each invalid binding and asserting zero provider/UI side effects.

Verification: `pytest -q` completed with `85 passed, 1 skipped`.
