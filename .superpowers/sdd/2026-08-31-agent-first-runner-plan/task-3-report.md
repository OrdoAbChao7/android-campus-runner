# Task 3 report — verified GPS lifecycle and guarded start

## Delivered

- Added `GpsLocatorProvider.stop_verified()`: it issues the configured official GPS Locator `stop` command, then polls `status` until the JSON response reports `simulationActive: false`. A stop command error, malformed/failed status, or timeout is returned as a failed `ProviderResult`.
- Moved provider preparation/readiness ahead of Campus Run navigation in `run_mvp`; readiness failure prevents the start prompt and invokes safe cleanup.
- Removed the production `keep_gps` path from the CLI and workflow APIs. Completion now always performs verified shutdown.
- Default flows do not tap `自由跑`. `run_mvp` consumes a registered, single-use `RunIntent` with matching `RunObservation` before confirmation; multi-account runner consumes one intent per account.
- A verified-stop failure clears prior completion, marks remaining accounts failed, and exposes `RunState.SAFE_STOP` in `MultiRunResult`.

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
