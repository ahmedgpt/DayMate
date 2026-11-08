"""Open-Meteo client: current/forecast/historical weather by place name. Never fabricates data."""
from __future__ import annotations

from datetime import date, timedelta

import httpx

from app.errors import DayMateError, ValidationError
from app.utils.datetime_utils import parse_date

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_HORIZON_DAYS = 15

WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "violent rain showers",
       95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail"}
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum"


class WeatherError(DayMateError):
    code = "weather_unavailable"


class WeatherService:
    def __init__(self, timeout: float = 10.0, transport: httpx.BaseTransport | None = None):
        self.timeout, self.transport = timeout, transport

    def _get(self, url: str, params: dict) -> dict:
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException:
            raise WeatherError("The weather service timed out.")
        except (httpx.HTTPError, ValueError):
            raise WeatherError("The weather service is currently unavailable.")

    def _geocode(self, location: str) -> dict:
        data = self._get(GEOCODE_URL, {"name": location, "count": 1, "language": "en", "format": "json"})
        results = data.get("results") or []
        if not results:
            raise ValidationError(f"I couldn't find a place called {location!r}.", code="location_not_found")
        r = results[0]
        return {"name": r["name"], "country": r.get("country"), "latitude": r["latitude"], "longitude": r["longitude"],
                "timezone": r.get("timezone", "auto")}

    def get_weather(self, location: str, on_date: str | None = None, today: date | None = None) -> dict:
        """`today` is the user's local date (used to choose current / forecast / archive)."""
        location = (location or "").strip()
        if not location:
            raise ValidationError("A location is required.", code="location_required")
        today = today or date.today()
        target = parse_date(on_date, "date") if on_date else today
        place = self._geocode(location)
        base = {"latitude": place["latitude"], "longitude": place["longitude"], "timezone": place["timezone"]}
        result = {"location": {k: place[k] for k in ("name", "country", "latitude", "longitude")},
                  "date": target.isoformat(), "source": "Open-Meteo (open-meteo.com)"}

        if target == today:
            data = self._get(FORECAST_URL, {**base, "current": "temperature_2m,weather_code,wind_speed_10m",
                                            "daily": DAILY, "start_date": target.isoformat(),
                                            "end_date": target.isoformat()})
            cur = data.get("current") or {}
            result.update(kind="current_and_today", current={
                "temperature_c": cur.get("temperature_2m"), "wind_speed_kmh": cur.get("wind_speed_10m"),
                "conditions": WMO.get(cur.get("weather_code"), "unknown")})
            result["day"] = self._day(data)
        elif today < target <= today + timedelta(days=FORECAST_HORIZON_DAYS):
            data = self._get(FORECAST_URL, {**base, "daily": DAILY, "start_date": target.isoformat(),
                                            "end_date": target.isoformat()})
            result.update(kind="forecast", day=self._day(data))
        elif target < today:
            data = self._get(ARCHIVE_URL, {**base, "daily": DAILY, "start_date": target.isoformat(),
                                           "end_date": target.isoformat()})
            result.update(kind="historical", day=self._day(data))
        else:
            raise ValidationError(f"Forecasts only cover the next {FORECAST_HORIZON_DAYS} days.", code="date_out_of_range")
        if not result.get("day"):
            raise WeatherError("The weather service returned no data for that date.")
        return result

    @staticmethod
    def _day(data: dict) -> dict | None:
        d = data.get("daily") or {}
        try:
            return {"conditions": WMO.get(d["weather_code"][0], "unknown"), "max_temp_c": d["temperature_2m_max"][0],
                    "min_temp_c": d["temperature_2m_min"][0], "precipitation_mm": d["precipitation_sum"][0]}
        except (KeyError, IndexError, TypeError):
            return None
