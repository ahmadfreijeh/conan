"""The agent's @tool functions (Phase 3), collected in one list.

To add a tool: write it in its own file here (e.g. wiki.py), import it below and add it
to TOOLS. build_agent() passes TOOLS to the agent, so nothing else needs to change.
If the tool is paid or slow, also add its name to NEEDS_APPROVAL: a person is asked
before each call (see ask_approval in agent/hooks.py).
"""

from app.modules.agent.tools.places import find_place, reverse_geocode

TOOLS = [
    find_place,  # name -> places with coordinates (OpenStreetMap)
    reverse_geocode,  # coordinates -> address (OpenStreetMap)
]

# Tool names a person must approve before each call (with AGENT_APPROVAL=listed).
# Both map tools are free, so none yet; paid tools (e.g. web search) go here.
NEEDS_APPROVAL: set[str] = set()
