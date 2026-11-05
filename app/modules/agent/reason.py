"""Phase 2: the reasoning agent (Strands + Claude), no tools yet.

Takes the AgentInput the engine prepared (clues, photo, crops), links the clues and returns
a ReasonResult: ranked candidate places with evidence, plus search queries for Phase 3.
Phase 3 adds @tool functions to the same agent (tools=[...]); nothing else changes.
"""

from strands import Agent
from strands.models.anthropic import AnthropicModel

from app.core.config import get_settings
from app.modules.agent.constants import SYSTEM_PROMPT
from app.modules.agent.helpers import image_bytes
from app.modules.agent.schemas import ReasonResult
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
    # No tools yet (Phase 2). Phase 3 passes tools=[...] here.
    return Agent(model=model, system_prompt=SYSTEM_PROMPT, callback_handler=None)


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


def reason(agent_input: AgentInput, agent: Agent | None = None) -> ReasonResult:
    """Send the prepared input to the agent and return its structured answer."""
    agent = agent or build_agent()
    result = agent(_message(agent_input), structured_output_model=ReasonResult)
    if not isinstance(result.structured_output, ReasonResult):
        raise RuntimeError("The model did not return a structured result")
    return result.structured_output
