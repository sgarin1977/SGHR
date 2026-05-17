from logger import log
import aiohttp
import math

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
HEADERS = {"User-Agent": "myapp/1.0"}

async def get_location_by_coords(latitude: float, longitude: float):
    url = f"{NOMINATIM_URL}/reverse"
    params = {
        "lat": latitude,
        "lon": longitude,
        "format": "json"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=HEADERS) as response:
                data = await response.json()
                address = data.get("address", {})
                country = address.get("country")
                city = address.get("city") or address.get("town") or address.get("village")
                return country, city
    except Exception as e:
        log.error(f"[GEO] Ошибка геолокации: {e}")
        return None, None

async def get_coords_by_city(city_name: str, country: str = None):
    url = f"{NOMINATIM_URL}/search"
    params = {
        "q": f"{city_name}, {country}" if country else city_name,
        "format": "json",
        "limit": 1
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=HEADERS) as response:
                data = await response.json()
                if data:
                    lat = float(data[0]["lat"])
                    lon = float(data[0]["lon"])
                    found_country = data[0].get("display_name", "").split(",")[-1].strip()
                    return lat, lon, found_country
                return None, None, None
    except Exception as e:
        log.error(f"[GEO] Ошибка геолокации: {e}")
        return None, None, None

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # радиус Земли в км
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c

