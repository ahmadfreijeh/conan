"""The reasoning agent (Strands + Claude) with its Phase 3 tools.

Takes the AgentInput the engine prepared (clues, photo, crops), links the clues, checks
candidates with map lookups and returns a ReasonResult: ranked candidate places with
evidence (and coordinates when a lookup confirmed them), plus searches it could not run yet.
"""

import json
import sys
from collections.abc import Callable

from strands import Agent
from strands.models.anthropic import AnthropicModel

from app.core.config import get_settings
from app.modules.agent.constants import APPROVAL_PREFIX, SYSTEM_PROMPT
from app.modules.agent.helpers import image_bytes
from app.modules.agent.hooks import ALL, HOOKS, NO, YES, start_run
from app.modules.agent.schemas import ReasonResult
from app.modules.agent.tools import TOOLS
from app.modules.engine.schemas import AgentInput


def build_agent() -> Agent:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    model = AnthropicModel(
        client_args={"api_key": settings.anthropic_api_key},
        model_id=settings.reason_model,
        max_tokens=settings.reason_max_tokens,
    )
    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
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
    agent_input: AgentInput, agent: Agent | None = None, approve: Approve | None = None
) -> ReasonResult:
    """Send the prepared input to the agent and return its structured answer.

    If a tool needs approval, the agent pauses (stop_reason "interrupt"). Each waiting call
    is passed to `approve`, then the agent resumes with the answers. No `approve` = "no".
    """
    agent = agent or build_agent()
    start_run(agent)
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
    if not isinstance(result.structured_output, ReasonResult):
        raise RuntimeError("The model did not return a structured result")
    return result.structured_output


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
