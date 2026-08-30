from pathlib import Path

import pytest

from android_runner.location.route import RouteError, validate_route


def test_validate_route_accepts_two_valid_points(tmp_path: Path):
    route = tmp_path / "ok.gpx"
    route.write_text('<gpx><trk><trkseg><trkpt lat="30" lon="120"/><trkpt lat="30.001" lon="120.001"/></trkseg></trk></gpx>')
    assert validate_route(route) == 2


def test_validate_route_rejects_invalid_coordinates(tmp_path: Path):
    route = tmp_path / "bad.gpx"
    route.write_text('<gpx><trk><trkseg><trkpt lat="95" lon="120"/><trkpt lat="30" lon="120"/></trkseg></trk></gpx>')
    with pytest.raises(RouteError, match="latitude"):
        validate_route(route)
