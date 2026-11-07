"""Hooks: small functions Strands calls at moments of the agent's run.

Each hook's type hint says which moment it runs at (e.g. BeforeToolCallEvent = just before
a tool runs). To add a hook: write the function here and add it to HOOKS; build_agent()
passes HOOKS to the agent.

Run state (lookup count, tools allowed for the run) lives in agent.state. start_run() resets
it once per run; not a BeforeInvocationEvent hook, because resuming after an approval pause
is a new invocation and would reset it mid-run.
"""

import json
import sys

from strands import Agent
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent

from app.core.config import get_settings
from app.modules.agent.constants import (
    ALL,
    ALLOWED_KEY,
    LOOKUPS_KEY,
    SOURCES_KEY,
    WAITING_KEY,
    YES,
)
from app.modules.agent.schemas import ReasonResult
from app.modules.agent.tools import NEEDS_APPROVAL


def _is_lookup(event: BeforeToolCallEvent) -> bool:
    # Strands returns the structured answer as a tool call too; that is not a lookup.
    return event.tool_use["name"] != ReasonResult.__name__


def _is_waiting(event: BeforeToolCallEvent) -> bool:
    return event.tool_use["toolUseId"] in event.invocation_state.get(WAITING_KEY, set())


def start_run(agent: Agent) -> None:
    """Call once before each run: no lookups made, no tools allowed yet."""
    agent.state.set(LOOKUPS_KEY, 0)
    agent.state.set(ALLOWED_KEY, [])
    agent.state.set(SOURCES_KEY, [])


def _needs_approval(name: str) -> bool:
    mode = get_settings().agent_approval
    return mode == "all" or (mode == "listed" and name in NEEDS_APPROVAL)


def ask_approval(event: BeforeToolCallEvent) -> None:
    """Before each tool call: if the tool needs approval, pause the agent and ask.

    event.interrupt() pauses the run; reason() collects the answer and resumes it. On resume
    Strands runs this hook again for the same call, and interrupt() returns the answer.
    """
    name = event.tool_use["name"]
    allowed = event.agent.state.get(ALLOWED_KEY) or []
    if not _is_lookup(event) or not _needs_approval(name) or name in allowed:
        return
    waiting = event.invocation_state.setdefault(WAITING_KEY, set())
    waiting.add(event.tool_use["toolUseId"])
    # First time: pauses here. On resume: returns the answer, and the call is not waiting.
    answer = event.interrupt(
        f"approve-{name}", reason={"tool": name, "input": event.tool_use["input"]}
    )
    waiting.discard(event.tool_use["toolUseId"])
    if answer == ALL:
        event.agent.state.set(ALLOWED_KEY, [*allowed, name])
    elif answer != YES:
        event.cancel_tool = f"A person declined this {name} call. Do not retry it; go on."


def limit_lookups(event: BeforeToolCallEvent) -> None:
    """Before each tool call: once AGENT_MAX_LOOKUPS is reached, cancel the call. The agent
    gets the message below as the tool result, so it stops searching and answers."""
    if not _is_lookup(event) or _is_waiting(event) or event.cancel_tool:
        return
    count = event.agent.state.get(LOOKUPS_KEY) or 0
    limit = get_settings().agent_max_lookups
    if count >= limit:
        event.cancel_tool = (
            f"Lookup limit reached ({limit}). Do not call more tools; "
            "answer now with what you have."
        )
        return
    event.agent.state.set(LOOKUPS_KEY, count + 1)


def print_tool_call(event: BeforeToolCallEvent) -> None:
    """Show each lookup in the terminal (stderr), e.g. -> find_place(name="...", city="...")."""
    if not _is_lookup(event) or _is_waiting(event):
        return
    tool_input = event.tool_use["input"]
    args = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in tool_input.items())
    skipped = f"  [skipped: {event.cancel_tool}]" if event.cancel_tool else ""
    print(f"-> {event.tool_use['name']}({args}){skipped}", file=sys.stderr)


def _tool_json(event: AfterToolCallEvent) -> dict | None:
    """The tool's result as a dict, or None (failed, cancelled, not JSON)."""
    if event.exception or event.cancel_message:
        return None
    text = "".join(part.get("text", "") for part in event.result.get("content", []))
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _links(result: dict) -> list[tuple[str, str]]:
    """(title, url) pairs in a tool result: map places, wiki articles, nearby places."""
    items = [*result.get("places", []), *result.get("results", [])]
    if isinstance(result.get("place"), dict):  # reverse_geocode
        items.append(result["place"])
    if "url" in result:  # wiki_summary: the article itself
        items.append(result)
    pairs = []
    for item in items:
        url, title = item.get("url"), item.get("title") or item.get("name")
        if url and title:
            pairs.append((title, url))
    return pairs


def collect_sources(event: AfterToolCallEvent) -> None:
    """After each tool call: keep the links it returned, so the result can list its sources.
    Links come only from real tool results, never from the model, so none is made up."""
    if event.tool_use["name"] == ReasonResult.__name__:
        return
    result = _tool_json(event)
    if result is None:
        return
    sources = event.agent.state.get(SOURCES_KEY) or []
    seen = {s["url"] for s in sources}
    for title, url in _links(result):
        if url not in seen:
            seen.add(url)
            kind = "wiki" if "wikipedia.org" in url else "map"
            sources.append(
                {"title": title, "url": url, "kind": kind, "tool": event.tool_use["name"]}
            )
    event.agent.state.set(SOURCES_KEY, sources)


def run_sources(agent: Agent) -> list[dict]:
    """This run's sources (see collect_sources)."""
    return agent.state.get(SOURCES_KEY) or []


def _summarize(result: dict) -> str:
    """One short line for a tool result: places found, no match with hints, or the error."""
    if "error" in result:
        return f"error: {result['error']}"
    if "places" in result:
        places = result["places"]
        if not places:
            return f"no match (hints: {'; '.join(result.get('hints', []))})"
        top = "; ".join(_place_line(p) for p in places[:3])
        more = f" (+{len(places) - 3} more)" if len(places) > 3 else ""
        return f"{len(places)} places: {top}{more}"
    if "results" in result:  # wiki_search
        found = result["results"]
        if not found:
            return f"no match (hints: {'; '.join(result.get('hints', []))})"
        return f"{len(found)} articles: " + "; ".join(r["title"] for r in found[:3])
    if "place" in result:  # reverse_geocode
        return f"address: {result['place'].get('address', '')}"
    if "intro" in result:  # wiki_summary
        return f"article: {result['title']} ({result.get('description', '')})"
    return json.dumps(result, ensure_ascii=False)[:200]


def _place_line(p: dict) -> str:
    """e.g. 'Nejmeh Square (33.8967, 35.5046)' or 'Martyrs' Square, 125 m' for wiki_nearby."""
    name = p.get("name") or p.get("title", "?")
    if "distance_m" in p:
        return f"{name}, {p['distance_m']} m"
    if p.get("lat") is not None:
        return f"{name} ({p['lat']:.4f}, {p['lon']:.4f})"
    return name


def print_tool_result(event: AfterToolCallEvent) -> None:
    """Show each lookup's result in the terminal (stderr), e.g. <- find_place: 2 places: ..."""
    # Skip the structured answer and cancelled calls (print_tool_call already showed the skip).
    if event.tool_use["name"] == ReasonResult.__name__ or event.cancel_message:
        return
    name = event.tool_use["name"]
    if event.exception:
        print(f"<- {name}: failed: {event.exception}", file=sys.stderr)
        return
    text = "".join(part.get("text", "") for part in event.result.get("content", []))
    try:
        line = _summarize(json.loads(text))
    except (ValueError, TypeError, KeyError, AttributeError):
        line = text[:200]
    print(f"<- {name}: {line}", file=sys.stderr)


# Hooks for the same moment run in this order. Approval goes first: it pauses before the
# limit counts the call, so a resumed call is counted once. Print goes last, to show skips.
HOOKS = [
    ask_approval,  # before each tool call: ask a person if the tool needs it
    limit_lookups,  # before each tool call: stop after AGENT_MAX_LOOKUPS
    print_tool_call,  # before each tool call: print it
    print_tool_result,  # after each tool call: print a short summary of its result
    collect_sources,  # after each tool call: keep the links it returned (Sources in the result)
]
