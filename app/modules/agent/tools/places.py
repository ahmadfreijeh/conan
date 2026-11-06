"""Phase 3 step 1: map tools on OpenStreetMap Nominatim (free, no API key).

- find_place: a name (shop, square, street, landmark), optionally near a city -> places with
  coordinates.
- reverse_geocode: coordinates (e.g. EXIF GPS) -> the address at that spot.

HTTP, the 1-request-per-second wait and errors are handled by ApiClient (tools/http.py).
Errors come back as {"error": ...} instead of raising, so the agent can try another query.

Try it (CLI in tools/__main__.py):
    uv run python -m app.modules.agent.tools "Place de l'Etoile" --city Beirut
    uv run python -m app.modules.agent.tools --reverse 33.8959 35.5063
"""

from functools import lru_cache

from strands import tool

from app.core.config import get_settings
from app.modules.agent.tools.http import ApiClient, ToolError


@lru_cache
def _client() -> ApiClient:
    """One Nominatim client for all calls, so the 1-per-second wait is shared."""
    settings = get_settings()
    return ApiClient(
        settings.places_url,
        timeout=settings.places_timeout,
        min_interval=1.0,  # Nominatim usage policy
        params={
            "format": "jsonv2",
            "addressdetails": 1,
            "namedetails": 1,  # gives the local-language name too
            "accept-language": "en",  # answers in English where OSM has it
        },
    )


def _place(item: dict) -> dict:
    """Keep only what the agent needs from a Nominatim result (English, plus the local name)."""
    name = item.get("name") or item.get("display_name", "").split(",")[0]
    local = (item.get("namedetails") or {}).get("name", "")
    return {
        "name": name,
        "local_name": local if local and local != name else "",
        "lat": float(item["lat"]),
        "lon": float(item["lon"]),
        "type": f"{item.get('category', '')}/{item.get('type', '')}",
        "address": item.get("display_name", ""),
    }


GENERIC_WORDS = {"brasserie", "cafe", "café", "restaurant", "hotel", "bar", "shop", "le", "la"}


def _no_match_hints(name: str, city: str | None, country: str | None) -> list[str]:
    """What to try next when a search finds nothing, tailored to this query."""
    hints = ["check the spelling (OCR letters may be wrong) or try the local-language name"]
    words = name.split()
    generic = [w for w in words if w.casefold() in GENERIC_WORDS]
    if generic and len(words) > len(generic):
        rest = " ".join(w for w in words if w.casefold() not in GENERIC_WORDS)
        hints.append(f"drop generic words: try {rest!r}")
    elif len(words) > 3:
        hints.append("try fewer words, e.g. only the distinctive part of the name")
    hints.append("search the street or square name instead of the business")
    if city:
        hints.append(
            f"try without city={city!r}" + (f", keeping country={country!r}" if country else "")
        )
    elif not country:
        hints.append("add a city or country guess to narrow it down")
    return hints


@tool
def find_place(
    name: str, city: str | None = None, country: str | None = None, limit: int = 5
) -> dict:
    """Find real places by name on OpenStreetMap and get their coordinates.

    Use it for shop, café or hotel names, streets, squares and landmarks read in the photo.
    Add city and country when you have a guess, to avoid matches in other countries.

    Args:
        name: What to look for, e.g. "Brasserie Place St Georges" or "Place de l'Etoile".
        city: Optional city to search in, e.g. "Beirut".
        country: Optional country, e.g. "Lebanon".
        limit: Max results (1 to 10).
    """
    query = ", ".join(part for part in (name, city, country) if part)
    try:
        items = _client().get_json("search", {"q": query, "limit": max(1, min(limit, 10))})
    except ToolError as exc:
        return {
            "query": query,
            "error": str(exc),
            "hint": "temporary problem: retry once or move on",
        }
    places = [_place(item) for item in items]
    if not places:
        return {"query": query, "places": [], "hints": _no_match_hints(name, city, country)}
    return {"query": query, "places": places}


@tool
def reverse_geocode(lat: float, lon: float) -> dict:
    """Get the address (street, area, city, country) at given coordinates.

    Use it when the photo metadata has GPS, or to name the spot of a candidate place.

    Args:
        lat: Latitude, e.g. 33.8959.
        lon: Longitude, e.g. 35.5063.
    """
    try:
        item = _client().get_json("reverse", {"lat": lat, "lon": lon})
    except ToolError as exc:
        return {"error": str(exc)}
    if not isinstance(item, dict) or "error" in item:
        return {"error": "no address found at these coordinates"}
    return {"place": _place(item), "parts": item.get("address", {})}
