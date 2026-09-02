"""The weather, from somewhere that answers in numbers.

Asked for the weather in Sana'a, Alfred searched, got AccuWeather back,
failed to read the page, and said it could not find it - while holding
a snippet that said what the weather was. Asked for Bangkok it did read
a page, and the text it got was:

    Bangkok Thailand °C°F Bangkok ForecastDaily forecastTodayHour by
    hourTomorrowPlan aheadSeptemberMonthly weather

which is the navigation, because the numbers arrive by JavaScript that
a plain fetch never runs.

Scraping weather sites is the wrong shape for this. They are built for
eyes, they render late, and the good ones turn away robots. Open-Meteo
answers in JSON, wants no API key, and knows where Sana'a is - so the
question stops being "can this page be read" and starts being "what is
the temperature", which has an answer.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from src.tools.base import AlfredTool

_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"
_AGENT = "Alfred (personal assistant)"

# WMO weather interpretation codes. The numbers are exact and useless to
# say out loud; these are what a person would call the sky.
_SKY = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail",
    99: "thunderstorms with heavy hail",
}


def _get(url: str, params: dict[str, Any], timeout: float = 15.0) -> dict:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": _AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _candidates(place: str) -> list[str]:
    """Ways to ask for one place, most specific first.

    Whole string, then each comma-separated part, then the string with
    leading words removed - "yemen sana'a" becomes "sana'a" - and only
    then with trailing words removed. Leading first because English
    puts the big place first when it qualifies: the city is usually the
    last word, not the first.
    """
    place = " ".join((place or "").split())
    if not place:
        return []

    tries = [place]
    for part in place.split(","):
        part = part.strip()
        if part and part not in tries:
            tries.append(part)

    words = place.replace(",", " ").split()
    for i in range(1, len(words)):
        tail = " ".join(words[i:])
        if tail not in tries:
            tries.append(tail)
    for i in range(len(words) - 1, 0, -1):
        head = " ".join(words[:i])
        if head not in tries:
            tries.append(head)

    return tries[:6]


class WeatherTool(AlfredTool):
    name = "weather"

    description = (
        "The weather anywhere, by place name. actions: now (current "
        "conditions), forecast (the next few days, 'days' up to 7). "
        "Needs 'place' - a town, city or region, e.g. 'Sana'a', "
        "'Bangkok', 'Leeds'. Use this rather than searching the web: "
        "weather sites render their numbers in JavaScript and a fetch "
        "returns their menus."
    )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["now", "forecast"]},
                "place": {"type": "string"},
                "days": {"type": "integer"},
            },
            "required": ["action", "place"],
        }

    def __init__(self, fetch=None) -> None:
        self._get = fetch or _get

    # ----------------------------------------------------------------

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "now").strip().lower()
        place = str(
            arguments.get("place") or arguments.get("city")
            or arguments.get("location") or arguments.get("query") or ""
        ).strip()

        if not place:
            return {"status": "error",
                    "error": "'place' is needed - which town or city?"}
        if action not in ("now", "forecast"):
            return {"status": "error",
                    "error": "action must be 'now' or 'forecast'"}

        try:
            found = self._where(place)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"could not look up {place}: {exc}"}
        if found is None:
            return {
                "status": "not_found",
                "error": f"nowhere called {place!r} - try the nearest city",
            }

        try:
            if action == "now":
                return self._now(found)
            return self._forecast(found, int(arguments.get("days") or 3))
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"could not get the weather: {exc}"}

    # ----------------------------------------------------------------

    def _where(self, place: str) -> dict[str, Any] | None:
        """The place, however it was said.

        People give a country as well as a city - "yemen sana'a",
        "Sana'a, Yemen" - and the geocoder wants the city on its own.
        Asked the way it was typed, it returns nothing, and Alfred
        reports that a real capital does not exist.
        """
        top = None
        for candidate in _candidates(place):
            data = self._get(_GEOCODE, {"name": candidate, "count": 1})
            results = data.get("results") or []
            if results:
                top = results[0]
                break
        if top is None:
            return None
        return {
            "name": top.get("name", place),
            "country": top.get("country", ""),
            "latitude": top["latitude"],
            "longitude": top["longitude"],
        }

    def _now(self, place: dict[str, Any]) -> dict[str, Any]:
        data = self._get(_FORECAST, {
            "latitude": place["latitude"], "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,"
                       "relative_humidity_2m,wind_speed_10m,weather_code",
            "timezone": "auto",
        })
        current = data.get("current") or {}
        sky = _SKY.get(int(current.get("weather_code", -1)), "unsettled")
        temperature = round(float(current.get("temperature_2m", 0)))
        feels = round(float(current.get("apparent_temperature", temperature)))

        where = ", ".join(x for x in (place["name"], place["country"]) if x)
        said = f"{where}: {temperature}°C and {sky}"
        if abs(feels - temperature) >= 2:
            said += f", feels like {feels}°C"

        return {
            "status": "success",
            "place": where,
            "temperature_c": temperature,
            "feels_like_c": feels,
            "sky": sky,
            "humidity_pct": current.get("relative_humidity_2m"),
            "wind_kph": current.get("wind_speed_10m"),
            # The whole answer in one line, because this is usually
            # read out or sent to a phone.
            "said": said + ".",
        }

    def _forecast(self, place: dict[str, Any], days: int) -> dict[str, Any]:
        days = max(1, min(days, 7))
        data = self._get(_FORECAST, {
            "latitude": place["latitude"], "longitude": place["longitude"],
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,"
                     "precipitation_probability_max",
            "forecast_days": days, "timezone": "auto",
        })
        daily = data.get("daily") or {}
        dates = daily.get("time") or []

        out = []
        for i, date in enumerate(dates):
            out.append({
                "date": date,
                "high_c": round(float(daily["temperature_2m_max"][i])),
                "low_c": round(float(daily["temperature_2m_min"][i])),
                "sky": _SKY.get(int(daily["weather_code"][i]), "unsettled"),
                "rain_chance_pct": (
                    daily.get("precipitation_probability_max") or [None] * len(dates)
                )[i],
            })

        where = ", ".join(x for x in (place["name"], place["country"]) if x)
        summary = "; ".join(
            f"{d['date'][5:]} {d['sky']} {d['low_c']}-{d['high_c']}°C"
            for d in out
        )
        return {"status": "success", "place": where, "days": out,
                "said": f"{where}: {summary}."}
