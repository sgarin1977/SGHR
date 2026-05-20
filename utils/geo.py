import math


EARTH_RADIUS_KM = 6371.0


def haversine_distance_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    lat1_rad = math.radians(float(lat1))
    lon1_rad = math.radians(float(lon1))
    lat2_rad = math.radians(float(lat2))
    lon2_rad = math.radians(float(lon2))

    delta_lat = lat2_rad - lat1_rad
    delta_lon = lon2_rad - lon1_rad

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))

    return EARTH_RADIUS_KM * c


def is_within_radius_km(
    *,
    origin_lat: float,
    origin_lon: float,
    target_lat: float,
    target_lon: float,
    radius_km: float,
) -> bool:
    return (
        haversine_distance_km(origin_lat, origin_lon, target_lat, target_lon)
        <= float(radius_km)
    )