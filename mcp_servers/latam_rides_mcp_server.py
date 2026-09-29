#!/usr/bin/env python3
"""
LATAM Ride-Hailing MCP Server (Uber, DiDi, inDrive, Cabify, 99)

No ride-hailing company publishes a consumer fare API, and inDrive prices are negotiated, so live fares
cannot be fetched. This server provides:

  - route_info            Geocodes two places (OpenStreetMap Nominatim) and returns road distance and
                          duration (OSRM public demo server). Real data.
  - estimate_fares        Per-service fare ESTIMATE = base + per_km*km + per_min*min (min fare applied),
                          from the rate table below. The default rates are rough placeholders, not
                          published tariffs: calibrate them with real quotes by editing latam_rates.json
                          (created next to this file on first use) . Output is labelled an estimate.
  - ride_app_links        Deep link that pre-fills Uber (documented universal link) and web entry points
                          for the other apps so you can read the real quote yourself.

Nothing here books a ride.
"""

import os
import json
from urllib.parse import quote

import httpx
from fastmcp import FastMCP

mcp = FastMCP("latam-rides")

HERE = os.path.dirname(os.path.abspath(__file__))
RATES_FILE = os.path.join(HERE, "latam_rates.json")
UA = "ai-orchestration-latam-rides/1.0 (personal use)"

# Rough placeholder rates in local currency (base, per_km, per_min, minimum). NOT official tariffs.
DEFAULT_RATES = {
    "MX": {"currency": "MXN", "services": {
        "Uber": [15, 6.5, 1.2, 40], "DiDi": [13, 6.0, 1.1, 38], "inDrive": [12, 5.5, 1.0, 35], "Cabify": [20, 8.0, 1.5, 55]}},
    "CO": {"currency": "COP", "services": {
        "Uber": [4000, 1300, 250, 8000], "DiDi": [3500, 1200, 230, 7500], "inDrive": [3000, 1100, 200, 7000], "Cabify": [5000, 1600, 300, 10000]}},
    "PE": {"currency": "PEN", "services": {
        "Uber": [3.0, 1.2, 0.20, 7], "DiDi": [2.5, 1.1, 0.18, 6.5], "inDrive": [2.0, 1.0, 0.15, 6], "Cabify": [4.0, 1.5, 0.25, 9]}},
}

APP_LINKS = {
    "DiDi": "https://web.didiglobal.com/",
    "inDrive": "https://indrive.com/",
    "Cabify": "https://cabify.com/",
    "99 (Brazil)": "https://99app.com/",
}


def _rates() -> dict:
    if not os.path.exists(RATES_FILE):
        with open(RATES_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_RATES, f, indent=2)
    with open(RATES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _geocode(place: str, country: str):
    with httpx.Client(timeout=20.0, headers={"User-Agent": UA}) as c:
        r = c.get("https://nominatim.openstreetmap.org/search",
                  params={"q": place, "format": "json", "limit": 1, "countrycodes": country.lower()})
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"could not geocode '{place}'")
    return float(data[0]["lat"]), float(data[0]["lon"]), data[0]["display_name"]


def _route(a, b):
    url = f"https://router.project-osrm.org/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}"
    with httpx.Client(timeout=20.0, headers={"User-Agent": UA}) as c:
        r = c.get(url, params={"overview": "false"})
    r.raise_for_status()
    route = r.json()["routes"][0]
    return route["distance"] / 1000.0, route["duration"] / 60.0


def _trip(pickup: str, dropoff: str, country: str):
    a, b = _geocode(pickup, country), _geocode(dropoff, country)
    km, minutes = _route(a, b)
    return a, b, km, minutes


@mcp.tool()
def route_info(pickup: str, dropoff: str, country: str = "MX") -> str:
    """Road distance and duration between two places (country: MX, CO or PE)."""
    try:
        a, b, km, minutes = _trip(pickup, dropoff, country)
    except Exception as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"pickup": a[2], "dropoff": b[2], "distance_km": round(km, 1),
                       "duration_min": round(minutes)}, ensure_ascii=False, indent=2)


@mcp.tool()
def estimate_fares(pickup: str, dropoff: str, country: str = "MX") -> str:
    """ESTIMATE fares for Uber, DiDi, inDrive and Cabify from road distance and a rate table.

    These are model estimates using placeholder rates, not live quotes. Edit latam_rides rates in
    mcp_servers/latam_rates.json to calibrate. inDrive is negotiated, so treat its number as a starting offer.
    """
    country = country.upper()
    rates = _rates()
    if country not in rates:
        return json.dumps({"error": f"country must be one of {list(rates)}"})
    try:
        a, b, km, minutes = _trip(pickup, dropoff, country)
    except Exception as e:
        return json.dumps({"error": str(e)})
    cfg = rates[country]
    fares = {}
    for name, (base, per_km, per_min, minimum) in cfg["services"].items():
        fares[name] = round(max(minimum, base + per_km * km + per_min * minutes), 2)
    cheapest = min(fares, key=fares.get)
    return json.dumps({
        "pickup": a[2], "dropoff": b[2], "distance_km": round(km, 1), "duration_min": round(minutes),
        "currency": cfg["currency"], "estimated_fares": fares, "cheapest_estimate": cheapest,
        "warning": "ESTIMATES from placeholder rates, not live quotes. Surge, tolls and local tariffs are not modelled.",
        "check_real_price": ride_links_dict(a, b),
    }, ensure_ascii=False, indent=2)


def ride_links_dict(a, b) -> dict:
    uber = ("https://m.uber.com/ul/?action=setPickup"
            f"&pickup[latitude]={a[0]}&pickup[longitude]={a[1]}&pickup[nickname]={quote(a[2][:40])}"
            f"&dropoff[latitude]={b[0]}&dropoff[longitude]={b[1]}&dropoff[nickname]={quote(b[2][:40])}")
    return {"Uber (pre-filled)": uber, **APP_LINKS}


@mcp.tool()
def ride_app_links(pickup: str, dropoff: str, country: str = "MX") -> str:
    """Uber deep link pre-filled with the trip, plus web entry points for DiDi, inDrive, Cabify and 99."""
    try:
        a, b = _geocode(pickup, country), _geocode(dropoff, country)
    except Exception as e:
        return json.dumps({"error": str(e)})
    return json.dumps(ride_links_dict(a, b), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
