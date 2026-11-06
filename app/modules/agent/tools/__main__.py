"""Try the agent's tools by hand, without the agent:
uv run python -m app.modules.agent.tools "Place de l'Etoile" --city Beirut
uv run python -m app.modules.agent.tools --reverse 33.8959 35.5063
uv run python -m app.modules.agent.tools --wiki "Mohammad Al-Amin Mosque Beirut" [--lang fr]
uv run python -m app.modules.agent.tools --wiki-page "Mohammad Al-Amin Mosque"
uv run python -m app.modules.agent.tools --nearby 33.8955 35.5064 [--radius 500] [--keyword church]
"""

import argparse
import json

from app.modules.agent.tools.places import find_place, reverse_geocode
from app.modules.agent.tools.wiki import wiki_nearby, wiki_search, wiki_summary

parser = argparse.ArgumentParser(description="Try the agent tools by hand.")
parser.add_argument("name", nargs="?", help="place name to search")
parser.add_argument("--city")
parser.add_argument("--country")
parser.add_argument("--limit", type=int, default=5)
parser.add_argument("--wiki", metavar="QUERY", help="search Wikipedia")
parser.add_argument("--wiki-page", metavar="TITLE", help="read one Wikipedia article")
parser.add_argument(
    "--lang", default="en", help="Wikipedia language for --wiki, --wiki-page and --nearby"
)
parser.add_argument("--nearby", nargs=2, type=float, metavar=("LAT", "LON"))
parser.add_argument("--radius", type=int, default=500, help="metres, for --nearby")
parser.add_argument("--keyword", help="only places matching this word, for --nearby")
parser.add_argument("--reverse", nargs=2, type=float, metavar=("LAT", "LON"))
args = parser.parse_args()

if args.nearby:
    result = wiki_nearby(*args.nearby, args.radius, args.keyword, args.lang)
elif args.wiki_page:
    result = wiki_summary(args.wiki_page, args.lang)
elif args.wiki:
    result = wiki_search(args.wiki, args.lang, args.limit)
elif args.reverse:
    result = reverse_geocode(*args.reverse)
elif args.name:
    result = find_place(args.name, args.city, args.country, args.limit)
else:
    parser.error(
        "give a place name, --wiki QUERY, --wiki-page TITLE, --nearby LAT LON or --reverse LAT LON"
    )
print(json.dumps(result, indent=2, ensure_ascii=False))
