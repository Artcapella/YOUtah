import math

from backend.models import Coordinate

EARTH_RADIUS_KM = 6371.0


def haversine_km(a: Coordinate, b: Coordinate) -> float:
    d_lat = math.radians(b.lat - a.lat)
    d_lng = math.radians(b.lng - a.lng)
    h = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(a.lat)) * math.cos(math.radians(b.lat)) * math.sin(d_lng / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def bounding_box(center: Coordinate, radius_km: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lng, max_lng) of a square that contains the circle. Not for use near the poles."""
    d_lat = math.degrees(radius_km / EARTH_RADIUS_KM)
    d_lng = d_lat / max(math.cos(math.radians(center.lat)), 1e-6)
    return center.lat - d_lat, center.lat + d_lat, center.lng - d_lng, center.lng + d_lng
