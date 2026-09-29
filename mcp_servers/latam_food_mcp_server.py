#!/usr/bin/env python3
"""
LATAM Food Delivery MCP Server (Rappi, PedidosYa, iFood, Uber Eats, DiDi Food)

None of these apps offers a public consumer API, and PedidosYa/iFood return HTTP 403 to plain
requests, so live menu prices cannot be fetched directly. This server therefore offers:

  - food_app_links         Per-country list of delivery apps with their web entry points.
  - apify_food_search      Runs a hosted Apify scraper actor for Rappi / PedidosYa and returns the
                           dataset. REQUIRES APIFY_TOKEN in .env (pay-per-use on Apify's side).
                           The actor input schema is NOT verified: pass the fields the actor's page
                           documents via `run_input` (a JSON object).
  - compare_dishes_plan    Returns the exact steps/URLs an agent with a browser MCP should follow to
                           compare one dish across the apps in a city.

Nothing here places orders.
"""

import os
import json
from urllib.parse import quote_plus

import httpx
from fastmcp import FastMCP

mcp = FastMCP("latam-food")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT, ".env")
if os.path.exists(ENV_FILE):
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

APPS = {
    "MX": {"Rappi": "https://www.rappi.com.mx/", "Uber Eats": "https://www.ubereats.com/mx",
           "DiDi Food": "https://www.didi-food.com/es-MX/", "Justo (grocery)": "https://justo.mx/"},
    "CO": {"Rappi": "https://www.rappi.com.co/", "Uber Eats": "https://www.ubereats.com/co",
           "DiDi Food": "https://www.didi-food.com/es-CO/"},
    "PE": {"Rappi": "https://www.rappi.com.pe/", "PedidosYa": "https://www.pedidosya.com.pe/",
           "Uber Eats": "https://www.ubereats.com/pe"},
    "CL": {"Rappi": "https://www.rappi.cl/", "PedidosYa": "https://www.pedidosya.cl/",
           "Uber Eats": "https://www.ubereats.com/cl"},
    "AR": {"Rappi": "https://www.rappi.com.ar/", "PedidosYa": "https://www.pedidosya.com.ar/"},
    "BR": {"iFood": "https://www.ifood.com.br/", "Rappi": "https://www.rappi.com.br/",
           "Uber Eats": "https://www.ubereats.com/br"},
}

ACTORS = {
    "rappi": "parseforge~rappi-scraper",
    "pedidosya": "scrapers_lat~pedidosya-scraper",
}


@mcp.tool()
def food_app_links(country: str = "MX") -> str:
    """List the food delivery apps active in a country (MX, CO, PE, CL, AR, BR) with web entry points."""
    country = country.upper()
    if country not in APPS:
        return json.dumps({"error": f"country must be one of {list(APPS)}"})
    return json.dumps({"country": country, "apps": APPS[country]}, ensure_ascii=False, indent=2)


@mcp.tool()
def apify_food_search(platform: str, run_input: dict, max_items: int = 20) -> str:
    """Run an Apify scraper for Rappi or PedidosYa and return its dataset items.

    Args:
        platform: 'rappi' or 'pedidosya'
        run_input: JSON input for the actor, per its Apify page (e.g. a search URL or city/query fields).
                   The schema is not verified here; if the actor rejects the input the error is returned.
        max_items: cap on returned items
    """
    token = os.getenv("APIFY_TOKEN", "")
    if not token:
        return json.dumps({"error": "APIFY_TOKEN not set. Add it to .env (https://console.apify.com/account/integrations)."})
    actor = ACTORS.get(platform.lower())
    if not actor:
        return json.dumps({"error": f"platform must be one of {list(ACTORS)}"})
    try:
        with httpx.Client(timeout=300.0) as c:
            resp = c.post(f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                          params={"token": token, "limit": max(1, min(max_items, 100))}, json=run_input)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"request failed: {e}"})
    if resp.status_code not in (200, 201):
        return json.dumps({"error": f"Apify HTTP {resp.status_code}: {resp.text[:400]}", "actor": actor})
    return json.dumps({"actor": actor, "items": resp.json()}, ensure_ascii=False, indent=2)


@mcp.tool()
def compare_dishes_plan(dish: str, country: str = "MX", city: str = "") -> str:
    """Plan for comparing one dish across the delivery apps of a country using a browser MCP.

    Returns each app's URL and the fields to record (item price, delivery fee, service fee, ETA).
    """
    country = country.upper()
    if country not in APPS:
        return json.dumps({"error": f"country must be one of {list(APPS)}"})
    return json.dumps({
        "dish": dish, "country": country, "city": city,
        "search_hint": quote_plus(f"{dish} {city}".strip()),
        "steps": [f"Open {name} ({url}), set the delivery address to a point in {city or 'the city'}, search '{dish}', "
                  f"record restaurant, item price, delivery fee, service fee, ETA, and the restaurant URL."
                  for name, url in APPS[country].items() if "grocery" not in name],
        "note": "Apps may require login or an address before showing menus. Report what could not be read instead of estimating.",
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
