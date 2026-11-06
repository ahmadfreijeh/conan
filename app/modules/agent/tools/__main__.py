"""Try the agent's tools by hand, without the agent:
uv run python -m app.modules.agent.tools "Place de l'Etoile" --city Beirut
uv run python -m app.modules.agent.tools --reverse 33.8959 35.5063
"""

import argparse
import json

from app.modules.agent.tools.places import find_place, reverse_geocode

parser = argparse.ArgumentParser(description="Try the map tools by hand.")
parser.add_argument("name", nargs="?", help="place name to search")
parser.add_argument("--city")
parser.add_argument("--country")
parser.add_argument("--limit", type=int, default=5)
parser.add_argument("--reverse", nargs=2, type=float, metavar=("LAT", "LON"))
args = parser.parse_args()

if args.reverse:
    result = reverse_geocode(*args.reverse)
elif args.name:
    result = find_place(args.name, args.city, args.country, args.limit)
else:
    parser.error("give a place name or --reverse LAT LON")
print(json.dumps(result, indent=2, ensure_ascii=False))
