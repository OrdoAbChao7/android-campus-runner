# Task 4 report — WeCom checkpoint and guarded switching

## Delivered

- Added immutable `WeComCheckpoint` capture in `android_runner.device` with
  screenshot and hierarchy evidence paths, UTC timestamp, foreground package
  and activity, ADB serial, device build fingerprint, and a normalized UI page
  SHA-256 fingerprint.
- The classifier accepts only narrowly recognized WeCom start-prompt,
  account-home, and account-switcher pages. It fails closed for unknown pages,
  another foreground package/activity, login/logout, OTP/verification-code,
  CAPTCHA, and re-authentication markers.
- `confirm_free_run` now requires an expected start-prompt checkpoint, samples
  it again before touching the reservation, checks the page and device
  fingerprint, and only then consumes the intent and taps `自由跑`.
- Removed the logout opt-in switcher. Enterprise selection requires a distinct
  target in an explicit `logged_in_enterprises` list and safe checkpoints before
  opening, selecting, and accepting the resulting account home page.
- Switching is fail-closed unless independent application-result verification
  and verified provider shutdown both succeed. The multi-account runner also
  requires the explicit already-logged-in enterprise list.

## Verification

`pytest -q` completed with `106 passed, 1 skipped`.

No login, logout, OTP, CAPTCHA, or authentication action was automated.

## Fix round 1

- Replaced the broad `com.tencent.wework.*` activity check and trigger-phrase
  matching with an exact activity allowlist plus required text/resource-id page
  signatures. Unknown activities and phrase-only pages now remain unknown.
- `run_mvp` rejects an authorized generic callback switcher before provider or
  UI actions, and `run_route_then_switch` rejects it before route execution.
  Production switching therefore uses the protected `WeComEnterpriseSwitcher`
  boundary only.
- Added regression coverage for unknown activity, phrase-only controls,
  callback-switcher preflight rejection, and login/logout/out-of-list switch
  rejection with zero clicks.

## Fix round 2

- `run_mvp` now performs the protected-switcher type check unconditionally,
  including its no-authorization/manual-stop path, before provider preparation
  or UI navigation.
- Replaced the public multi-account callback parameter with the concrete
  `WeComEnterpriseSwitchCapability`. The callback-based execution helper is
  private and used only by unit tests; production construction in `runner`
  receives its guarded capability from explicit device/current/enterprise data.
- Added zero-side-effect regressions for generic switchers on both public
  single- and multi-account entry points.
