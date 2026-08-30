# Android Runner

Minimal Windows + Android ADB automation runner.

## Quick start

```powershell
$env:PYTHONPATH = "src"
python -m android_runner doctor
python -m pytest -q
```

Set `ANDROID_RUNNER_ADB` to override the ADB executable path.

The guarded Python MVP entry point is `android_runner.runner.run_mvp(...)`.
It stops at the WeCom "自由跑" prompt unless `allow_start=True`; when enabled,
it prepares the configured GPS Locator provider, runs the validated GPX/KML
route, always stops the provider, and then invokes the account-switch state
machine.
