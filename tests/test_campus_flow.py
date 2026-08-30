import pytest

from android_runner.wecom.campus_run import CampusRunState, confirm_free_run, next_state


def test_campus_flow_states_are_ordered():
    state = CampusRunState.INIT
    for expected in [CampusRunState.WORKBENCH, CampusRunState.SMART_SPORTS,
                     CampusRunState.CAMPUS_RUN, CampusRunState.START_PROMPT]:
        state = next_state(state)
        assert state is expected


def test_free_run_requires_explicit_authorization():
    class Device:
        def click(self, **kwargs): raise AssertionError("must not click")
    with pytest.raises(PermissionError):
        confirm_free_run(Device())


def test_free_run_confirmation_enters_running_state():
    calls = []
    class Device:
        def click(self, **kwargs): calls.append(kwargs)
    assert confirm_free_run(Device(), allow_start=True) is CampusRunState.RUNNING
    assert calls == [{"text": "自由跑", "timeout": 10.0}]
