# Task 3 final review remediation

- `workflow.run_multi_account` now rejects absent, stale, or mismatched active intent reservations before it touches the provider or device UI. The reservation-to-intent batch binding is checked under the registry lock.
- `wecom.campus_run.confirm_free_run` now requires concrete `IntentUseRegistry` and `IntentReservation` instances before it calls `consume_reserved` or clicks the free-run control.
- `consume_reserved` remains the atomic consume-before-click boundary; callers retain the existing `release_reservation` cleanup in `runner.run_multi_account_mvp`.

Regression coverage includes a missing-reservation custom callback bypass and a duck-typed no-op registry bypass. Verification: `pytest -q` — 96 passed, 1 skipped.
