#!/usr/bin/env python3
"""
LATAM Shopping MCP Server (Mexico, Colombia, Peru, Chile, Argentina, Brazil)

Tools:
  - falabella_search        Falabella (CO / PE / CL) via the search page's embedded JSON. No login.
  - amazon_mx_search        Amazon Mexico search results (HTML parse; may break if Amazon changes markup
                            or shows a captcha).
  - mercadolibre_search     Mercado Libre official API. REQUIRES an access token: api.mercadolibre.com
  - mercadolibre_item       returns 403 without one. Set ML_ACCESS_TOKEN in .env (see
                            https://developers.mercadolibre.com.ar/).
  - store_search_links      Ready-made search URLs for stores with no scrapable structure
                            (Liverpool, Coppel, Exito, Ripley, Alkosto, ...).
  - compare_prices          Runs the working sources above and sorts by price.

Prices are returned as shown by the store, in local currency. Nothing here places orders.
"""

import os
import re
import json
import html
from urllib.parse import quote_plus
from typing import Optional

import httpx
from fastmcp import FastMCP

mcp = FastMCP("latam-shop")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT, ".env")
if os.path.exists(ENV_FILE):
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "es-419,es;q=0.9,en;q=0.5"}

FALABELLA = {
    "CO": ("https://www.falabella.com.co/falabella-co/search?Ntt={q}", "COP"),
    "PE": ("https://www.falabella.com.pe/falabella-pe/search?Ntt={q}", "PEN"),
    "CL": ("https://www.falabella.com/falabella-cl/search?Ntt={q}", "CLP"),
}

ML_SITES = {"MX": "MLM", "CO": "MCO", "PE": "MPE", "CL": "MLC", "AR": "MLA", "BR": "MLB"}
ML_DOMAINS = {"MX": "mercadolibre.com.mx", "CO": "mercadolibre.com.co", "PE": "mercadolibre.com.pe",
              "CL": "mercadolibre.cl", "AR": "mercadolibre.com.ar", "BR": "mercadolivre.com.br"}

STORE_LINKS = {
    "MX": {
        "Liverpool": "https://www.liverpool.com.mx/tienda?s={q}",
        "Coppel": "https://www.coppel.com/SearchDisplay?searchTerm={q}",
        "Walmart MX": "https://www.walmart.com.mx/search?q={q}",
        "Elektra": "https://www.elektra.mx/{q}?_q={q}&map=ft",
        "Mercado Libre": "https://listado.mercadolibre.com.mx/{q}",
        "Amazon MX": "https://www.amazon.com.mx/s?k={q}",
    },
    "CO": {
        "Exito": "https://www.exito.com/s?q={q}",
        "Alkosto": "https://www.alkosto.com/search?text={q}",
        "Ktronix": "https://www.ktronix.com/search?text={q}",
        "Homecenter": "https://www.homecenter.com.co/homecenter-co/search?Ntt={q}",
        "Mercado Libre": "https://listado.mercadolibre.com.co/{q}",
        "Falabella": "https://www.falabella.com.co/falabella-co/search?Ntt={q}",
    },
    "PE": {
        "Ripley": "https://simple.ripley.com.pe/search/{q}",
        "Oechsle": "https://www.oechsle.pe/{q}?_q={q}&map=ft",
        "Promart": "https://www.promart.pe/search?_q={q}&map=ft",
        "PlazaVea": "https://www.plazavea.com.pe/{q}?_q={q}&map=ft",
        "Mercado Libre": "https://listado.mercadolibre.com.pe/{q}",
        "Falabella": "https://www.falabella.com.pe/falabella-pe/search?Ntt={q}",
    },
}


def _client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=25.0, follow_redirects=True)


def _to_number(text: str) -> Optional[float]:
    """Parse '229.900', '1,299.00', '$ 1.299,50' into a float; None if no digits."""
    s = re.sub(r"[^\d.,]", "", text or "")
    if not re.search(r"\d", s):
        return None
    m = re.search(r"[.,](\d{1,2})$", s)
    if m:
        whole = re.sub(r"[.,]", "", s[: m.start()])
        return float(f"{whole}.{m.group(1)}")
    return float(re.sub(r"[.,]", "", s))


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


@mcp.tool()
def falabella_search(query: str, country: str = "CO", limit: int = 10) -> str:
    """Search Falabella (also covers Sodimac/Tottus catalog items sold there).

    Args:
        query: product search text
        country: CO (Colombia), PE (Peru) or CL (Chile)
        limit: max results (1-30)
    """
    country = country.upper()
    if country not in FALABELLA:
        return json.dumps({"error": f"country must be one of {list(FALABELLA)}"})
    url_tpl, currency = FALABELLA[country]
    url = url_tpl.format(q=quote_plus(query))
    try:
        with _client() as c:
            resp = c.get(url)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"request failed: {e}", "search_url": url})
    if resp.status_code != 200:
        return json.dumps({"error": f"HTTP {resp.status_code}", "search_url": url})
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
    if not m:
        return json.dumps({"error": "no embedded product data found (markup changed or bot check)", "search_url": url})
    try:
        results = json.loads(m.group(1))["props"]["pageProps"].get("results", [])
    except (KeyError, ValueError) as e:
        return json.dumps({"error": f"unexpected data shape: {e}", "search_url": url})

    items = []
    for r in results:
        if r.get("isSponsored"):
            continue
        prices = {p.get("type"): _to_number((p.get("price") or [""])[0]) for p in r.get("prices", [])}
        current = next((prices[k] for k in ("internetPrice", "eventPrice", "cmrPrice", "normalPrice") if prices.get(k)), None)
        items.append({
            "store": f"Falabella {country}",
            "title": r.get("displayName"),
            "brand": r.get("brand"),
            "price": current,
            "currency": currency,
            "normal_price": prices.get("normalPrice"),
            "discount": (r.get("discountBadge") or {}).get("label"),
            "rating": r.get("rating"),
            "seller": r.get("sellerName"),
            "url": r.get("url"),
        })
        if len(items) >= max(1, min(limit, 30)):
            break
    return json.dumps({"query": query, "country": country, "count": len(items), "search_url": url, "results": items},
                      ensure_ascii=False, indent=2)


@mcp.tool()
def amazon_mx_search(query: str, limit: int = 10) -> str:
    """Search Amazon Mexico (amazon.com.mx). HTML parse; may hit a captcha.

    Args:
        query: product search text
        limit: max results (1-20)
    """
    url = f"https://www.amazon.com.mx/s?k={quote_plus(query)}"
    try:
        with _client() as c:
            resp = c.get(url)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"request failed: {e}", "search_url": url})
    if resp.status_code != 200 or "captcha" in resp.text[:200000].lower():
        return json.dumps({"error": f"blocked or captcha (HTTP {resp.status_code})", "search_url": url})

    parts = re.split(r'(?=<div[^>]+data-asin="B[0-9A-Z]{9}")', resp.text)
    items, seen = [], set()
    for part in parts:
        a = re.match(r'<div[^>]+data-asin="(B[0-9A-Z]{9})"', part)
        if not a or a.group(1) in seen:
            continue
        block = part[:12000]
        title = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
        price = re.search(r'class="a-price"[^>]*>.*?<span class="a-offscreen">(.*?)</span>', block, re.S)
        rating = re.search(r"([\d.,]+) de 5 estrellas", block)
        if not title or not price:
            continue
        seen.add(a.group(1))
        items.append({
            "store": "Amazon MX",
            "title": _clean(title.group(1)),
            "price": _to_number(_clean(price.group(1))),
            "currency": "MXN",
            "rating": rating.group(1) if rating else None,
            "url": f"https://www.amazon.com.mx/dp/{a.group(1)}",
        })
        if len(items) >= max(1, min(limit, 20)):
            break
    return json.dumps({"query": query, "count": len(items), "search_url": url, "results": items},
                      ensure_ascii=False, indent=2)


def _ml_headers() -> Optional[dict]:
    token = os.getenv("ML_ACCESS_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "User-Agent": UA} if token else None


@mcp.tool()
def mercadolibre_search(query: str, country: str = "MX", limit: int = 10) -> str:
    """Search Mercado Libre via the official API. Needs ML_ACCESS_TOKEN in .env.

    Args:
        query: product search text
        country: MX, CO, PE, CL, AR or BR
        limit: max results (1-50)
    """
    country = country.upper()
    if country not in ML_SITES:
        return json.dumps({"error": f"country must be one of {list(ML_SITES)}"})
    web_url = f"https://listado.{ML_DOMAINS[country]}/{quote_plus(query).replace('+', '-')}"
    headers = _ml_headers()
    if not headers:
        return json.dumps({"error": "ML_ACCESS_TOKEN not set. api.mercadolibre.com rejects anonymous requests (403). "
                                    "Create an app at developers.mercadolibre.com.ar and put the token in .env.",
                           "search_url": web_url})
    try:
        with httpx.Client(timeout=25.0) as c:
            resp = c.get(f"https://api.mercadolibre.com/sites/{ML_SITES[country]}/search",
                         params={"q": query, "limit": max(1, min(limit, 50))}, headers=headers)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"request failed: {e}", "search_url": web_url})
    if resp.status_code != 200:
        return json.dumps({"error": f"HTTP {resp.status_code}: {resp.text[:200]}", "search_url": web_url})
    items = [{
        "store": f"Mercado Libre {country}",
        "id": r.get("id"),
        "title": r.get("title"),
        "price": r.get("price"),
        "currency": r.get("currency_id"),
        "condition": r.get("condition"),
        "sold": r.get("sold_quantity"),
        "free_shipping": (r.get("shipping") or {}).get("free_shipping"),
        "seller": (r.get("seller") or {}).get("nickname"),
        "url": r.get("permalink"),
    } for r in resp.json().get("results", [])]
    return json.dumps({"query": query, "country": country, "count": len(items), "search_url": web_url, "results": items},
                      ensure_ascii=False, indent=2)


@mcp.tool()
def mercadolibre_item(item_id: str) -> str:
    """Get one Mercado Libre listing by id (e.g. MLM123456789). Needs ML_ACCESS_TOKEN in .env."""
    headers = _ml_headers()
    if not headers:
        return json.dumps({"error": "ML_ACCESS_TOKEN not set (see mercadolibre_search)."})
    try:
        with httpx.Client(timeout=25.0) as c:
            resp = c.get(f"https://api.mercadolibre.com/items/{item_id}", headers=headers)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"request failed: {e}"})
    if resp.status_code != 200:
        return json.dumps({"error": f"HTTP {resp.status_code}: {resp.text[:200]}"})
    d = resp.json()
    keep = ("id", "title", "price", "original_price", "currency_id", "condition", "available_quantity",
            "sold_quantity", "permalink", "warranty", "listing_type_id")
    out = {k: d.get(k) for k in keep}
    out["attributes"] = {a.get("name"): a.get("value_name") for a in d.get("attributes", [])[:20]}
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def store_search_links(query: str, country: str = "MX") -> str:
    """Search URLs for LATAM stores that have no structured feed here (Liverpool, Coppel, Exito, Ripley,
    Alkosto, Ktronix, Oechsle, Promart, PlazaVea, Walmart MX, Elektra...). Open them in a browser MCP.

    Args:
        query: product search text
        country: MX, CO or PE
    """
    country = country.upper()
    if country not in STORE_LINKS:
        return json.dumps({"error": f"country must be one of {list(STORE_LINKS)}"})
    q = quote_plus(query)
    return json.dumps({"query": query, "country": country,
                       "links": {name: tpl.format(q=q) for name, tpl in STORE_LINKS[country].items()}},
                      ensure_ascii=False, indent=2)


@mcp.tool()
def compare_prices(query: str, country: str = "CO", limit: int = 5) -> str:
    """Query every source that works for the country and merge results sorted by price.

    Sources: Falabella (CO/PE/CL), Amazon MX (MX), Mercado Libre (all, only if ML_ACCESS_TOKEN is set).
    Prices are in each store's local currency, so only compare within one country.
    """
    country = country.upper()
    merged, notes = [], []

    def collect(raw: str, label: str):
        data = json.loads(raw)
        if "error" in data:
            notes.append(f"{label}: {data['error']}")
        else:
            merged.extend(data["results"])

    if country in FALABELLA:
        collect(falabella_search(query, country, limit), "Falabella")
    if country == "MX":
        collect(amazon_mx_search(query, limit), "Amazon MX")
    if country in ML_SITES:
        collect(mercadolibre_search(query, country, limit), "Mercado Libre")
    merged.sort(key=lambda r: (r.get("price") is None, r.get("price") or 0))
    return json.dumps({"query": query, "country": country, "results": merged, "notes": notes,
                       "more_stores": json.loads(store_search_links(query, country)).get("links", {})},
                      ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
