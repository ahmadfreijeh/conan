"""The reasoning agent (Strands + Claude) with its Phase 3 tools.

Takes the AgentInput the engine prepared (clues, photo, crops), links the clues, checks
candidates with map lookups and returns a ReasonResult: ranked candidate places with
evidence (and coordinates when a lookup confirmed them), plus searches it could not run yet.
"""

import json
import sys
from collections.abc import Callable

from strands import Agent
from strands.models import CacheConfig
from strands.models.anthropic import AnthropicModel

from app.core.config import get_settings
from app.modules.agent import spend
from app.modules.agent.constants import (
    APPROVAL_PREFIX,
    NO_TOOLS_TIP,
    SYSTEM_PROMPT,
    TOOL_TIPS,
)
from app.modules.agent.helpers import image_bytes
from app.modules.agent.hooks import ALL, HOOKS, NO, YES, start_run
from app.modules.agent.schemas import ReasonResult
from app.modules.agent.tools import select_tools, tool_name
from app.modules.engine.schemas import AgentInput


def tool_names_from_settings() -> list[str] | None:
    """AGENT_TOOLS as a list: None when unset (all tools), [] when empty (no tools)."""
    raw = get_settings().agent_tools
    return None if raw is None else [n.strip() for n in raw.split(",") if n.strip()]


def system_prompt(tools: list) -> str:
    """The prompt with one hint per tool that is on; a tool that is off is not named."""
    tips = [f"   - {TOOL_TIPS[tool_name(t)]}" for t in tools if tool_name(t) in TOOL_TIPS]
    return SYSTEM_PROMPT.replace("{tool_tips}", "\n".join(tips) or f"   - {NO_TOOLS_TIP}")


def build_agent(tool_names: list[str] | None = None) -> Agent:
    """The agent with the chosen tools: tool_names, else AGENT_TOOLS, else all of them."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    model = AnthropicModel(
        client_args={"api_key": settings.anthropic_api_key},
        model_id=settings.reason_model,
        max_tokens=settings.reason_max_tokens,
        # Cache the system prompt, the tools and the conversation so far (photo included):
        # every model call after the first in a run reads them at ~10% of the input price.
        cache_config=CacheConfig(strategy="anthropic", system_prompt_ttl=True, tools_ttl=True)
        if settings.reason_cache
        else None,
    )
    tools = select_tools(tool_names if tool_names is not None else tool_names_from_settings())
    print(f"tools: {', '.join(tool_name(t) for t in tools) or 'none'}", file=sys.stderr)
    return Agent(
        model=model,
        system_prompt=system_prompt(tools),
        tools=tools,
        hooks=HOOKS,
        callback_handler=None,
    )


def _image(image) -> dict:
    return {"image": {"format": "jpeg", "source": {"bytes": image_bytes(image)}}}


def _message(agent_input: AgentInput) -> list[dict]:
    """The user message: clues as JSON, the full photo, then each crop with a short caption."""
    content: list[dict] = [
        {"text": "Clues extracted from the photo (JSON):\n" + agent_input.clues.model_dump_json()},
        {"text": "Full photo:"},
        _image(agent_input.photo),
    ]
    for i, crop in enumerate(agent_input.crops, 1):
        content.append(
            {
                "text": f"Crop {i}: {crop.label} ({crop.group}, confidence {crop.confidence}), "
                f"{crop.where} of the photo"
            }
        )
        content.append(_image(crop.image))
    content.append({"text": "Where was this photo taken?"})
    return content


# Gets what a tool wants to do ({"tool": ..., "input": {...}}), returns "yes", "no" or "all".
Approve = Callable[[dict], str]


def reason(
    agent_input: AgentInput,
    agent: Agent | None = None,
    approve: Approve | None = None,
    tool_names: list[str] | None = None,
) -> ReasonResult:
    """Send the prepared input to the agent and return its structured answer.

    If a tool needs approval, the agent pauses (stop_reason "interrupt"). Each waiting call
    is passed to `approve`, then the agent resumes with the answers. No `approve` = "no".
    tool_names picks the tools (None = AGENT_TOOLS, else all); see build_agent().
    """
    agent = agent or build_agent(tool_names)
    start_run(agent)
    before = dict(agent.event_loop_metrics.accumulated_usage)
    result = agent(_message(agent_input), structured_output_model=ReasonResult)
    while result.stop_reason == "interrupt":
        answers = [
            {
                "interruptResponse": {
                    "interruptId": i.id,
                    "response": approve(i.reason) if approve else NO,
                }
            }
            for i in result.interrupts or []
        ]
        result = agent(answers, structured_output_model=ReasonResult)
    _record_usage(
        agent.model.config["model_id"], before, agent.event_loop_metrics.accumulated_usage
    )
    if not isinstance(result.structured_output, ReasonResult):
        raise RuntimeError("The model did not return a structured result")
    return result.structured_output


def _record_usage(model: str, before: dict, after: dict) -> None:
    """Save this run's tokens and cost to the spend log, and print one line on stderr."""
    keys = ("inputTokens", "cacheReadInputTokens", "cacheWriteInputTokens", "outputTokens")
    used = {k: after.get(k, 0) - before.get(k, 0) for k in keys}
    line = spend.record(model, used)
    usd = f"${line['usd']:.4f}" if line["usd"] is not None else "unknown price"
    print(
        f"tokens: input {line['input']}, cache read {line['cache_read']}, "
        f"cache write {line['cache_write']}, output {line['output']} ({usd})",
        file=sys.stderr,
    )


ANSWERS = {"y": YES, "yes": YES, "n": NO, "no": NO, "a": ALL, "all": ALL}


def ask_in_terminal(request: dict) -> str:
    """Approve function for the CLI: ask on the terminal. Enter or anything else = no."""
    args = ", ".join(f"{k}={v!r}" for k, v in request["input"].items())
    answer = input(f"Allow {request['tool']}({args})? [y]es / [n]o / [a]ll: ").strip().lower()
    return ANSWERS.get(answer, NO)


def ask_over_pipe(request: dict) -> str:
    """Approve function when there is no terminal (the UI): one line out, one line back.

    Prints APPROVAL_PREFIX + the request as JSON, then waits for yes / no / all on stdin.
    If stdin closes, the answer is no.
    """
    print(APPROVAL_PREFIX + json.dumps(request, ensure_ascii=False), flush=True)
    return ANSWERS.get(sys.stdin.readline().strip().lower(), NO)


def default_approve() -> Approve:
    """Ask on the terminal if there is one, else over the pipe (the UI)."""
    return ask_in_terminal if sys.stdin.isatty() else ask_over_pipe
