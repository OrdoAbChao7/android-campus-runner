from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree


class RouteError(ValueError):
    pass


def validate_route(path: Path) -> int:
    if path.suffix.lower() not in {".gpx", ".kml"}:
        raise RouteError("route must be GPX or KML")
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError) as exc:
        raise RouteError(f"invalid route XML: {exc}") from exc
    points = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "trkpt":
            lat, lon = element.get("lat"), element.get("lon")
        elif tag == "coordinates":
            for value in (element.text or "").split():
                fields = value.split(",")
                if len(fields) >= 2:
                    points.append((float(fields[1]), float(fields[0])))
            continue
        else:
            continue
        if lat is None or lon is None:
            raise RouteError("route point missing latitude or longitude")
        points.append((float(lat), float(lon)))
    if len(points) < 2:
        raise RouteError("route must contain at least two points")
    for lat, lon in points:
        if not -90 <= lat <= 90:
            raise RouteError(f"latitude out of range: {lat}")
        if not -180 <= lon <= 180:
            raise RouteError(f"longitude out of range: {lon}")
    return len(points)
