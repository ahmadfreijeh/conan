"""Phase 3 step 2: Wikipedia tools (free, no API key).

- wiki_search: free text (a landmark, a square, "clock tower downtown Beirut") -> the top
  Wikipedia articles, each with a short intro and coordinates when the article has them.
- wiki_summary: one article title -> its full intro, short description, coordinates and
  its name in other languages, to check a candidate against the photo.
- wiki_nearby: coordinates (from any tool or EXIF) -> the Wikipedia places around them with
  their distance, optionally only those matching a keyword ("church", "tower").

Works where find_place needs an exact name: loose descriptions, famous buildings, areas.
HTTP, errors and the cache are handled by ApiClient (tools/http.py); errors come back as
{"error": ...} instead of raising.

Try it (CLI in tools/__main__.py):
    uv run python -m app.modules.agent.tools --wiki "Mohammad Al-Amin Mosque Beirut"
    uv run python -m app.modules.agent.tools --wiki-page "Mohammad Al-Amin Mosque"
    uv run python -m app.modules.agent.tools --nearby 33.8955 35.5064 --keyword church
"""

from functools import lru_cache

from strands import tool

from app.core.config import get_settings
from app.modules.agent.constants import MAX_INTRO, NAME_LANGS
from app.modules.agent.tools.http import ApiClient, ToolError


@lru_cache
def _client(lang: str) -> ApiClient:
    """One client per Wikipedia language (en, fr, ar...), each with its own base URL."""
    settings = get_settings()
    return ApiClient(
        settings.wiki_url.format(lang=lang),
        timeout=settings.wiki_timeout,
        params={"action": "query", "format": "json", "formatversion": 2},
    )


def _article(page: dict, lang: str) -> dict:
    """Keep only what the agent needs from one search result."""
    coords = (page.get("coordinates") or [{}])[0]
    return {
        "title": page["title"],
        "summary": page.get("extract", "").strip(),
        "lat": coords.get("lat"),
        "lon": coords.get("lon"),
        "url": page.get("fullurl") or f"https://{lang}.wikipedia.org/wiki/{page['title']}",
    }


@tool
def wiki_search(query: str, lang: str = "en", limit: int = 5) -> dict:
    """Search Wikipedia for landmarks, buildings, squares, streets or areas.

    Use it when you recognise a landmark or have a loose description, not an exact name
    (find_place needs exact names). Results include a short intro to check against the
    photo, and coordinates when the article has them.

    For OCR words that are misread or cut off (signs, plates), the query accepts:
    - word~ : allows 1-2 wrong letters, e.g. "Gemayze~" or "Nejmh~ square".
    - word* : missing ending, e.g. "Gemm*" or "Mosq* Beirut". Not at the start of a word.
    - intitle:/fragment/ : the title contains the letters seen, wherever they are, e.g.
      "intitle:/mmayz/" or "intitle:/ayzeh/ Beirut" (missing start or middle). Slower: add
      a place word to narrow it. ? and a leading * do not work.

    Args:
        query: Free text, e.g. "Mohammad Al-Amin Mosque" or "clock tower downtown Beirut".
        lang: Wikipedia language code, e.g. "en", "fr", "ar". Use the language of text in
            the photo when English finds nothing.
        limit: Max results (1 to 10).
    """
    lang = (lang or "en").strip().lower()
    params = {
        "generator": "search",
        "gsrsearch": query,
        "gsrlimit": max(1, min(limit, 10)),
        "prop": "coordinates|extracts|info",
        "exintro": 1,  # intro only
        "explaintext": 1,  # plain text, no HTML
        "exsentences": 2,
        "exlimit": "max",
        "colimit": "max",
        "inprop": "url",
    }
    try:
        data = _client(lang).get_json("api.php", params)
    except ToolError as exc:
        return {"query": query, "error": str(exc), "hint": "retry once or move on"}
    pages = sorted((data.get("query") or {}).get("pages", []), key=lambda p: p.get("index", 0))
    results = [_article(page, lang) for page in pages]
    if not results:
        hints = ["try fewer or different words, or the local-language name"]
        if not any(op in query for op in ("~", "*", "intitle:")):
            word = max(query.split(), key=len, default=query)
            hints.append(
                f"if OCR may be wrong or cut off: try {word}~ (wrong letters), "
                f"{word[:4]}* (missing ending) or intitle:/{word[-4:]}/ (missing start)"
            )
        if lang == "en":
            hints.append('try lang="fr" or lang="ar" (or the language of text in the photo)')
        return {"query": query, "lang": lang, "results": [], "hints": hints}
    return {"query": query, "lang": lang, "results": results}


@tool
def wiki_summary(title: str, lang: str = "en") -> dict:
    """Read one Wikipedia article: its intro, short description, coordinates and its name
    in other languages.

    Use it to check a candidate (from wiki_search or your own guess) against the photo:
    what it looks like, what is around it, when it was built. The local-language names
    can be passed to find_place.

    Args:
        title: Exact article title, e.g. "Mohammad Al-Amin Mosque" (old names redirect).
        lang: Wikipedia language code of that title, e.g. "en", "fr", "ar".
    """
    lang = (lang or "en").strip().lower()
    params = {
        "titles": title,
        "redirects": 1,  # follow old or alternative names
        "prop": "extracts|description|coordinates|langlinks|info",
        "exintro": 1,
        "explaintext": 1,
        "lllimit": "max",
        "inprop": "url",
    }
    try:
        data = _client(lang).get_json("api.php", params)
    except ToolError as exc:
        return {"title": title, "error": str(exc), "hint": "retry once or move on"}
    pages = (data.get("query") or {}).get("pages") or [{}]
    page = pages[0]
    if page.get("missing") or page.get("invalid") or "title" not in page:
        return {
            "title": title,
            "error": "no article with this title",
            "hint": "use wiki_search to find the exact title",
        }
    intro = page.get("extract", "").strip()
    if len(intro) > MAX_INTRO:
        intro = intro[:MAX_INTRO].rsplit(" ", 1)[0] + " ..."
    coords = (page.get("coordinates") or [{}])[0]
    links = {link["lang"]: link["title"] for link in page.get("langlinks", [])}
    return {
        "title": page["title"],
        "description": page.get("description", ""),
        "intro": intro,
        "lat": coords.get("lat"),
        "lon": coords.get("lon"),
        "names": {code: links[code] for code in NAME_LANGS if code in links and code != lang},
        "url": page.get("fullurl") or f"https://{lang}.wikipedia.org/wiki/{page['title']}",
    }


@tool
def wiki_nearby(
    lat: float, lon: float, radius: int = 500, keyword: str | None = None, lang: str = "en"
) -> dict:
    """List the Wikipedia places (landmarks, churches, squares, streets...) near a point,
    closest first, with their distance in metres.

    Use it to check a candidate: take coordinates from find_place, wiki_search,
    wiki_summary or the photo's GPS, and see if what is around matches the photo (e.g. the
    church or tower you see). Matches near the point support the guess; none weakens it.

    Args:
        lat: Latitude of the point, e.g. 33.8955.
        lon: Longitude of the point, e.g. 35.5064.
        radius: Search radius in metres (10 to 10000). 300-1000 for a street view photo.
        keyword: Optional word to keep only matching places, e.g. "church", "mosque",
            "tower", "museum" (checked in the title and short description).
        lang: Wikipedia language code, e.g. "en", "fr", "ar".
    """
    lang = (lang or "en").strip().lower()
    radius = max(10, min(int(radius), 10000))
    point = f"{lat}|{lon}"
    params = {
        "generator": "geosearch",
        "ggscoord": point,
        "ggsradius": radius,
        "ggslimit": 50,
        "prop": "coordinates|description|info",
        "codistancefrompoint": point,  # adds "dist" (metres) to each result
        "colimit": "max",
        "inprop": "url",
    }
    try:
        data = _client(lang).get_json("api.php", params)
    except ToolError as exc:
        return {"error": str(exc), "hint": "retry once or move on"}
    places = []
    for page in (data.get("query") or {}).get("pages", []):
        coords = (page.get("coordinates") or [{}])[0]
        places.append(
            {
                "title": page["title"],
                "description": page.get("description", ""),
                "distance_m": round(coords.get("dist", 0)),
                "lat": coords.get("lat"),
                "lon": coords.get("lon"),
                "url": page.get("fullurl", ""),
            }
        )
    found = len(places)
    if keyword:
        word = keyword.casefold()
        places = [
            p
            for p in places
            if word in p["title"].casefold() or word in p["description"].casefold()
        ]
    places.sort(key=lambda p: p["distance_m"])
    result = {"point": [lat, lon], "radius_m": radius, "keyword": keyword, "places": places[:15]}
    if not places:
        hints = [f"try a larger radius (now {radius} m)"]
        if keyword:
            hints.append(f"{found} places found without the keyword: try another word or none")
        if lang == "en":
            hints.append('try lang="fr" or lang="ar": local Wikipedias list more small places')
        result["hints"] = hints
    return result
