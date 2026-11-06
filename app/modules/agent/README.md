# Agent (Phase 2: Reason, Phase 3: Search)

The reasoning agent and its tools. Input comes from the [image engine](../engine/README.md). Back to the [main README](../../../README.md).

## Reasoning (Phase 2)

The engine prepares the agent input from the payload (clues, the photo and close-up crops, `app/modules/engine/prepare.py`). A Strands agent with Claude reads it, links the clues, checks places with its map and Wikipedia tools (see [Tools](#tools-phase-3-search)) and returns ranked candidate places with evidence, coordinates when a lookup confirmed them, and the searches it could not run yet. Needs `ANTHROPIC_API_KEY` in `.env`.

```bash
uv run python -m app.modules.engine.prepare out/photo/payload.json                                          # see the clues sent to the LLM (no API call)
uv run python -m app.modules.engine samples/photo.jpeg --reason --out out/photo                          # Phase 1 + Phase 2
uv run python -m app.modules.engine samples/photo.jpeg --reason --from-payload out/photo/payload.json --out out/photo  # Phase 2 only, reuses the saved payload
```

The result prints and, with `--out`, is saved to `out/photo/reason.json`. At the end it prints the run's tokens and cost, e.g. `tokens: input 3000, cache read 40000, cache write 8000, output 2500 ($0.0590)`.

**Prompt caching** is on: the prompt, the tools and the conversation so far (photo and crops included) are cached on Anthropic's side, so each model call after the first in a run reads them at about 10% of the input price. The cache lives 5 minutes after its last use and survives app restarts; changing the prompt, a tool docstring or the model starts a new one.

**Spend:** every run adds one line (model, tokens, USD) to `out/spend.jsonl` (CLI and UI runs). Prices per model are in `PRICES` (`app/modules/agent/spend.py`). The test UI shows it in the top bar.

| Setting | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | (empty) | Required for `--reason` |
| `REASON_MODEL` | `claude-sonnet-5-5` | Claude model; `claude-opus-5-5` for hard photos |
| `REASON_MAX_TOKENS` | `16000` | Max length of the answer |
| `REASON_MAX_CROPS` | `6` | Close-up crops of detected objects (signs, street, shops, vehicles, buildings) sent with the photo; `0` = photo only |
| `REASON_CACHE` | `true` | Prompt caching; `false` to turn it off (e.g. to compare cost) |
| `SPEND_LOG` | `out/spend.jsonl` | One line per reasoning run: tokens and USD |
| `CREDIT_BUDGET` | `0` | USD you topped up; the UI shows budget minus spent. `0` = hidden. An estimate: Anthropic has no balance API, so update it after a top-up |

## Tools (Phase 3: Search)

The agent can look things up while it reasons. Strands runs a loop: Claude picks a tool call, the tool runs, the result goes back to Claude, and Claude decides again (another call, another tool, or the answer). Our code never decides the order. Plan and status: [docs/phase-3-search.md](../../../docs/phase-3-search.md).

| Tool | Source | What it does |
|---|---|---|
| `find_place(name, city, country)` | OpenStreetMap Nominatim (free, no key) | A name read in the photo (shop, square, street, landmark) → real places with coordinates and an openstreetmap.org link, in English plus the local name |
| `reverse_geocode(lat, lon)` | OpenStreetMap Nominatim | Coordinates (e.g. EXIF GPS) → the address there |
| `wiki_search(query, lang)` | Wikipedia (free, no key) | Free text or a loose description ("clock tower downtown Beirut") → top articles with a short intro and coordinates. Handles misread OCR words (see below) |
| `wiki_summary(title, lang)` | Wikipedia | One article → description, full intro, coordinates and its name in other languages (local names help `find_place`) |
| `wiki_nearby(lat, lon, radius, keyword)` | Wikipedia | Coordinates from any tool or EXIF → Wikipedia places around them, closest first, with distance; `keyword` keeps e.g. only churches |

The tools chain: one finds a candidate point, the others check it against the photo.

```
find_place / wiki_search / EXIF GPS ──► candidate point (lat, lon)
                                              │
              ┌───────────────────────────────┼──────────────────────────────┐
              ▼                               ▼                              ▼
   wiki_summary(title)            wiki_nearby(point, "church")      reverse_geocode(point)
   does it look like the photo?   is the church in the photo there? which street and area?
```

Try the tools by hand (no LLM):

```bash
uv run python -m app.modules.agent.tools "Place de l'Etoile" --city Beirut
uv run python -m app.modules.agent.tools --reverse 33.8959 35.5063
uv run python -m app.modules.agent.tools --wiki "Mohammad Al-Amin Mosque Beirut"   # --lang fr / ar
uv run python -m app.modules.agent.tools --wiki "intitle:/ayzeh/ Beirut"           # cut-off OCR word
uv run python -m app.modules.agent.tools --wiki-page "Mohammad Al-Amin Mosque"
uv run python -m app.modules.agent.tools --nearby 33.8955 35.5064 --keyword church --radius 500
```

Misread or cut-off OCR words in `wiki_search` (tested on Wikipedia search):

| Pattern | For | Example |
|---|---|---|
| `word~` | 1-2 wrong letters | `Nejmh~ square` → Nejmeh Square |
| `word*` | missing ending | `Mosq* Beirut downtown` → Mohammad Al-Amin Mosque |
| `intitle:/fragment/` | missing start or middle | `intitle:/ayzeh/ Beirut` → Rue Gouraud (Gemmayzeh) |

`?` and a leading `*` do not work. When a search finds nothing, the hints suggest these retries built from the query.

During a `--reason` run every lookup is printed, e.g. `-> find_place(name="Place Saint-Georges", city="Paris", country="France")`.

How it behaves:

- **Retries:** if nothing is found, `find_place` and the wiki tools return hints (fix the spelling, drop generic words, search the street, widen the area) and the prompt tells the agent to change one thing at a time and move on after 3 misses on one clue.
- **Lookup limit:** at most `AGENT_MAX_LOOKUPS` tool calls per run, enforced in code; after that the agent is told to answer with what it has.
- **Cache:** the same request in one run returns the saved answer (no network call). Failed answers are not cached.
- **Errors:** a timeout or bad answer comes back as `{"error": ...}`, so the agent can retry or move on instead of crashing.

### Sources

Every link a tool returns during the run (Wikipedia articles, OpenStreetMap places) is kept by the `collect_sources` hook and saved as `sources` in `reason.json`: title, url, `wiki` or `map`, the tool, and `used` (its name appears in the answer). The links come only from real tool results, never from the model, so none is made up. The test UI lists them at the end of the Result tab, grouped by Wikipedia and Map, ★ = used, used first.

### Choosing tools per run

A tool that is **on** is available: the agent calls it only if it helps. A tool that is **off** is removed from the run completely: it is not given to the agent and the prompt does not name it (each tool's hint in the prompt comes from `TOOL_TIPS` in `app/modules/agent/constants.py`, only for tools that are on). With no tools, the agent answers from the photo and the clues alone. The run log starts with `tools: ...`.

```bash
uv run python -m app.modules.engine samples/photo.jpeg --reason --tools find_place,wiki_nearby --out out/photo
uv run python -m app.modules.engine samples/photo.jpeg --reason --tools "" --out out/photo   # no tools
```

### Tool approval

A person can approve tool calls before they run. Tools named in `NEEDS_APPROVAL` (`app/modules/agent/tools/__init__.py`) ask every time; all tools so far are free, so the list is empty for now. Answers: **yes** (run it), **no** (skip it, the agent goes on) or **all** (allow this tool for the rest of the run).

- **CLI:** asks in the terminal: `Allow find_place(...)? [y]es / [n]o / [a]ll:`.
- **Test UI:** a box in the Progress card with Yes / No / Allow all. With no answer in `AGENT_APPROVAL_TIMEOUT` seconds it counts as no.

How an approval travels in the UI:

```
Agent (subprocess)            RunManager (server)              Browser
1. hook pauses the agent
2. prints APPROVAL {...}  ──► 3. marks run "waiting",
                                 starts the timeout      ──►  4. shows the box
                                                              5. you click Yes
                              6. POST /approval writes   ◄──
7. reads "yes", resumes  ◄──     "yes" to the agent's stdin
```

### Settings (`.env`, all optional)

| Setting | Default | Meaning |
|---|---|---|
| `AGENT_TOOLS` | (unset) | Tools the agent gets, comma-separated, e.g. `find_place,wiki_nearby`; unset = all, empty = none. CLI: `--tools`; UI: the Tools toggles |
| `AGENT_MAX_LOOKUPS` | `10` | Tool calls per run; more are cancelled |
| `AGENT_APPROVAL` | `listed` | Who asks first: `listed` = tools in `NEEDS_APPROVAL`, `all` = every tool (handy to test), `none` = nothing asks |
| `AGENT_APPROVAL_TIMEOUT` | `300` | UI only: seconds to wait for an answer, then no; `0` = wait forever. Read when the server starts |
| `PLACES_URL` | `https://nominatim.openstreetmap.org` | Map search service |
| `PLACES_TIMEOUT` | `10` | Seconds per map lookup |
| `TOOLS_CONTACT` | (empty) | Email or URL added to the User-Agent. **Needed for Wikipedia** (it answers 403 without a contact); OpenStreetMap asks for it too |
| `WIKI_URL` | `https://{lang}.wikipedia.org/w` | Wikipedia API base; `{lang}` = `en`, `fr`, `ar`... |
| `WIKI_TIMEOUT` | `10` | Seconds per Wikipedia lookup |

### Add a tool

1. Write it in its own file in `app/modules/agent/tools/` with `@tool`; the docstring is what Claude reads to decide when to use it.
2. If it calls a web API, use `ApiClient` (`tools/http.py`): timeout, User-Agent, wait between calls, cache, and errors as `ToolError`.
3. Add it to `TOOLS` in `tools/__init__.py`. If it is paid or slow, add its name to `NEEDS_APPROVAL` too.
