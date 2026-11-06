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
from strands.hooks import BeforeToolCallEvent

from app.core.config import get_settings
from app.modules.agent.schemas import ReasonResult
from app.modules.agent.tools import NEEDS_APPROVAL

# Keys in agent.state
LOOKUPS_KEY = "lookups"  # tool calls made this run
ALLOWED_KEY = "allowed_tools"  # tools the person answered "all" for, this run

# Key in event.invocation_state: ids of tool calls paused for approval. Strands still runs
# the other hooks when one pauses, so they skip these calls (no double count or print).
WAITING_KEY = "waiting_for_approval"

# Answers a person can give to an approval request
YES, NO, ALL = "yes", "no", "all"


def _is_lookup(event: BeforeToolCallEvent) -> bool:
    # Strands returns the structured answer as a tool call too; that is not a lookup.
    return event.tool_use["name"] != ReasonResult.__name__


def _is_waiting(event: BeforeToolCallEvent) -> bool:
    return event.tool_use["toolUseId"] in event.invocation_state.get(WAITING_KEY, set())


def start_run(agent: Agent) -> None:
    """Call once before each run: no lookups made, no tools allowed yet."""
    agent.state.set(LOOKUPS_KEY, 0)
    agent.state.set(ALLOWED_KEY, [])


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


# Hooks for the same moment run in this order. Approval goes first: it pauses before the
# limit counts the call, so a resumed call is counted once. Print goes last, to show skips.
HOOKS = [
    ask_approval,  # before each tool call: ask a person if the tool needs it
    limit_lookups,  # before each tool call: stop after AGENT_MAX_LOOKUPS
    print_tool_call,  # before each tool call: print it
]
