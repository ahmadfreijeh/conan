"""The shape of what the reasoning agent returns.

The LLM must fill ReasonResult (structured output), so the result is checked and easy to
store. Phase 3 reads `search_queries` as its first tool calls.
"""

from pydantic import BaseModel, Field

# --- Reason result (output) ---------------------------------------------------------


class Evidence(BaseModel):
    clue: str = Field(description="The clue as seen, e.g. 'SALON DE THE (sign text)'")
    reading: str = Field(description="What it means, e.g. 'French, used in Lebanon and France'")


class Candidate(BaseModel):
    country: str
    city: str | None = None
    area: str | None = Field(None, description="Neighbourhood, street or square, if known")
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(description="Clues that support this candidate")
    against: list[str] = Field([], description="Clues that argue against it")


class SearchQuery(BaseModel):
    query: str = Field(description="What to look up, e.g. 'Brasserie Place St Georges'")
    kind: str = Field(description="places, web, wiki or map")
    why: str


class ReasonResult(BaseModel):
    summary: str = Field(description="One or two sentences: best guess and why")
    corrected_text: list[str] = Field(
        [], description="OCR text fixed by the model, e.g. 'DOCKTAILS -> COCKTAILS'"
    )
    seen_in_image: list[str] = Field(
        [], description="Location clues the model saw in the photo that Phase 1 missed"
    )
    candidates: list[Candidate] = Field(description="Ranked, most likely first")
    search_queries: list[SearchQuery] = Field(description="What Phase 3 should look up first")
