from android_runner.doctor import DoctorResult, format_report


def test_format_report_includes_failure_reason():
    report = format_report(
        DoctorResult(
            checks={"adb": (False, "not found"), "Android device": (True, "SERIAL")},
            device={"serial": "SERIAL"},
        )
    )
    assert "[FAIL] adb: not found" in report
    assert "[PASS] Android device: SERIAL" in report
