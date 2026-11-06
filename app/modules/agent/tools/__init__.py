"""The agent's @tool functions (Phase 3), collected in one list.

To add a tool: write it in its own file here (e.g. wiki.py), import it below and add it
to TOOLS. build_agent() passes TOOLS to the agent, so nothing else needs to change.
The first paragraph of its docstring is the short description shown in the test UI.

A run can switch tools off (AGENT_TOOLS, the CLI's --tools, the UI's toggles): select_tools()
keeps only the chosen ones, so a tool that is off is not given to the agent at all.
If the tool is paid or slow, also add its name to NEEDS_APPROVAL: a person is asked
before each call (see ask_approval in agent/hooks.py).
"""

from app.modules.agent.tools.places import find_place, reverse_geocode
from app.modules.agent.tools.wiki import wiki_nearby, wiki_search, wiki_summary

TOOLS = [
    find_place,  # name -> places with coordinates (OpenStreetMap)
    reverse_geocode,  # coordinates -> address (OpenStreetMap)
    wiki_search,  # free text -> Wikipedia articles with intro and coordinates
    wiki_summary,  # one article -> full intro, description, coordinates, other names
    wiki_nearby,  # coordinates -> Wikipedia places around them, with distance
]

# Tool names a person must approve before each call (with AGENT_APPROVAL=listed).
# All tools so far are free, so none yet; paid tools (e.g. web search) go here.
NEEDS_APPROVAL: set[str] = set()


def tool_name(t) -> str:
    return t.tool_name


def select_tools(names: list[str] | None) -> list:
    """The tools to give the agent: all when names is None, else only those named."""
    if names is None:
        return list(TOOLS)
    known = {tool_name(t): t for t in TOOLS}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise RuntimeError(f"unknown tools: {', '.join(unknown)} (known: {', '.join(known)})")
    return [t for t in TOOLS if tool_name(t) in names]


def tool_list() -> list[dict]:
    """Name, short description (first docstring paragraph) and approval flag, for the UI."""
    items = []
    for t in TOOLS:
        doc = (t.tool_spec.get("description") or "").strip()
        short = " ".join(doc.split("\n\n")[0].split())
        items.append(
            {"name": tool_name(t), "description": short, "approval": tool_name(t) in NEEDS_APPROVAL}
        )
    return items
