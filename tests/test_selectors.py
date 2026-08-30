from android_runner.device import selector_candidates


def test_selector_candidates_prioritize_stable_attributes():
    assert selector_candidates(resource_id="pkg:id/action", text="继续", xpath="//node") == [
        ("resource_id", "pkg:id/action"), ("text", "继续"), ("xpath", "//node")
    ]
