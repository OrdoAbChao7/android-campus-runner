# Task 4 brief — WeCom checkpoint and account-switching guards

Read this first; it is the complete requirements for Task 4.

Implement a deterministic manual checkpoint and fail-closed Enterprise WeChat navigation/switch boundary.

1. Add a checkpoint value object/helper that captures screenshot path, hierarchy path, timezone-aware UTC timestamp, foreground package/activity, ADB serial/device fingerprint, and a stable page fingerprint. It must reject non-WeCom foreground packages and unknown/unsafe pages before any irreversible action.
2. Classify login, logout, OTP/verification-code, CAPTCHA, and other authentication/re-authentication screens as unsafe. Unknown package/activity/page fingerprints must fail closed without additional clicks.
3. `confirm_free_run` must verify the expected start-prompt checkpoint/fingerprint before consuming a RunIntent and clicking “自由跑”. A mismatch must leave the intent unconsumed and perform zero clicks.
4. Enterprise switching is allowed only to a distinct enterprise from an explicit list of enterprises already logged in on the device. Remove the misleading logout capability. Verify package/activity/page fingerprint before opening/selecting and after switching.
5. Workflow account switching must require both explicit app-result verification and provider-stop verification. Route process success alone is insufficient. Missing app-result proof must abort without switching.

Update `src/android_runner/device.py`, `src/android_runner/wecom/campus_run.py`, `src/android_runner/wecom/account.py`, `src/android_runner/workflow.py`, `src/android_runner/runner.py`, and focused tests. Keep default behavior safe and do not automate login/logout/OTP/CAPTCHA. Run `pytest -q`, commit focused changes, and append `task-4-report.md`. Do not push.

