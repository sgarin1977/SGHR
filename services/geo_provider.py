import os
from dataclasses import dataclass
from typing import Any

import httpx


class GeoProviderError(Exception):
    pass


@dataclass(frozen=True)
class GeoPlaceCandidate:
    name: str
    country_name: str
    country_code: str
    latitude: float
    longitude: float
    display_name: str
    provider: str = "nominatim"
    place_id: str | None = None
    osm_type: str | None = None
    osm_id: str | None = None
    place_type: str | None = None

    def to_state(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "country_name": self.country_name,
            "country_code": self.country_code,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "display_name": self.display_name,
            "provider": self.provider,
            "place_id": self.place_id,
            "osm_type": self.osm_type,
            "osm_id": self.osm_id,
            "place_type": self.place_type,
        }

    @classmethod
    def from_state(cls, data: "GeoPlaceCandidate | dict[str, Any]") -> "GeoPlaceCandidate":
        if isinstance(data, cls):
            return data

        return cls(
            name=str(data.get("name") or "").strip(),
            country_name=str(data.get("country_name") or "").strip(),
            country_code=str(data.get("country_code") or "").strip().upper()[:2],
            latitude=float(data["latitude"]),
            longitude=float(data["longitude"]),
            display_name=str(data.get("display_name") or "").strip(),
            provider=str(data.get("provider") or "nominatim"),
            place_id=str(data["place_id"]) if data.get("place_id") is not None else None,
            osm_type=str(data["osm_type"]) if data.get("osm_type") is not None else None,
            osm_id=str(data["osm_id"]) if data.get("osm_id") is not None else None,
            place_type=str(data["place_type"]) if data.get("place_type") is not None else None,
        )


class NominatimGeoProvider:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        user_agent: str | None = None,
        timeout_seconds: float | None = None,
    ):
        self.base_url = (
            base_url
            or os.getenv("NOMINATIM_BASE_URL")
            or "https://nominatim.openstreetmap.org"
        ).rstrip("/")
        self.user_agent = (
            user_agent
            or os.getenv("NOMINATIM_USER_AGENT")
            or "SGHR Beta Bot/0.1 OpenStreetMap geo-provider"
        )
        self.timeout_seconds = float(
            timeout_seconds
            or os.getenv("NOMINATIM_TIMEOUT_SECONDS")
            or 10
        )

    async def search(
        self,
        *,
        query: str,
        language: str = "ru",
        limit: int = 5,
    ) -> list[GeoPlaceCandidate]:
        normalized_query = (query or "").strip()
        if len(normalized_query) < 2:
            return []

        payload = await self._get_json(
            "/search",
            params={
                "q": normalized_query,
                "format": "jsonv2",
                "addressdetails": 1,
                "dedupe": 1,
                "limit": max(1, min(int(limit), 10)),
                "featuretype": "settlement",
                "accept-language": language,
            },
        )

        if not isinstance(payload, list):
            raise GeoProviderError("Unexpected Nominatim search response.")

        candidates = []
        for item in payload:
            candidate = self._candidate_from_payload(item)
            if candidate:
                candidates.append(candidate)

        return candidates

    async def reverse(
        self,
        *,
        latitude: float,
        longitude: float,
        language: str = "ru",
    ) -> GeoPlaceCandidate | None:
        payload = await self._get_json(
            "/reverse",
            params={
                "lat": latitude,
                "lon": longitude,
                "format": "jsonv2",
                "addressdetails": 1,
                "zoom": 10,
                "accept-language": language,
            },
        )

        if not isinstance(payload, dict):
            raise GeoProviderError("Unexpected Nominatim reverse response.")

        return self._candidate_from_payload(payload)

    async def _get_json(self, path: str, *, params: dict[str, Any]) -> Any:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                headers=headers,
            ) as client:
                response = await client.get(f"{self.base_url}{path}", params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise GeoProviderError(f"Nominatim request failed: {exc}") from exc

    def _candidate_from_payload(self, payload: dict[str, Any]) -> GeoPlaceCandidate | None:
        address = payload.get("address") or {}
        display_name = str(payload.get("display_name") or "").strip()
        name = self._address_name(address) or str(payload.get("name") or "").strip()

        if not name and display_name:
            name = display_name.split(",", 1)[0].strip()

        country_name = str(address.get("country") or "").strip()
        country_code = str(address.get("country_code") or "").strip().upper()[:2]

        try:
            latitude = float(payload["lat"])
            longitude = float(payload["lon"])
        except (KeyError, TypeError, ValueError):
            return None

        if not name or not country_name or len(country_code) != 2:
            return None

        return GeoPlaceCandidate(
            name=name,
            country_name=country_name,
            country_code=country_code,
            latitude=latitude,
            longitude=longitude,
            display_name=display_name or f"{name}, {country_name}",
            provider="nominatim",
            place_id=str(payload["place_id"]) if payload.get("place_id") is not None else None,
            osm_type=str(payload["osm_type"]) if payload.get("osm_type") is not None else None,
            osm_id=str(payload["osm_id"]) if payload.get("osm_id") is not None else None,
            place_type=str(payload.get("type") or payload.get("category") or ""),
        )

    def _address_name(self, address: dict[str, Any]) -> str | None:
        for key in (
            "city",
            "town",
            "village",
            "hamlet",
            "suburb",
            "municipality",
            "county",
            "state_district",
        ):
            value = str(address.get(key) or "").strip()
            if value:
                return value

        return None