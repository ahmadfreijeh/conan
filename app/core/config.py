from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "conan"
    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/conan"
    redis_url: str = "redis://localhost:6379/0"

    # Image engine (app/modules/engine)
    engine_device: Literal["auto", "mps", "cpu"] = "auto"  # for DINO; auto = Mac GPU if available
    detect_box_threshold: float = 0.30  # min confidence that a box holds an object
    detect_text_threshold: float = 0.25  # min confidence that the box matches the word
    detect_groups: str = "all"  # label groups to look for, e.g. "text,street,buildings"
    detect_batch_size: int = 25  # labels per model run (prompt length limit)
    ocr_det_model: Literal["mobile", "server"] = "mobile"  # text finder: fast vs more accurate
    # Recognizers, comma-separated; each reads every line and the best reading wins.
    # PP-OCRv6_medium_rec: English/Latin + Chinese. arabic_PP-OCRv5_mobile_rec: Arabic.
    ocr_rec_models: str = "PP-OCRv6_medium_rec,arabic_PP-OCRv5_mobile_rec"
    ocr_min_score: float = 0.5  # drop readings below this confidence
    engine_runs_log: str = "out/runs.jsonl"  # one line per run with --out, to compare runs

    # Reasoning agent (app/modules/agent)
    anthropic_api_key: str = ""
    reason_model: str = "claude-sonnet-5-5"  # switch to claude-opus-5-5 for hard photos
    reason_max_tokens: int = 16000
    reason_max_crops: int = 6  # close-up object crops sent with the photo; 0 = none
    spend_log: str = "out/spend.jsonl"  # one line per reasoning run: tokens and USD
    credit_budget: float = 0  # USD you topped up; the UI shows budget - spent (0 = hide)
    reason_cache: bool = True  # prompt caching: repeat sends of prompt, tools, photo cost ~10%

    # Agent tools (app/modules/agent/tools)
    agent_max_lookups: int = 10  # tool calls per run; more are cancelled (agent/hooks.py)
    # Ask a person before tool calls: listed = tools in NEEDS_APPROVAL, all = every tool, none
    agent_approval: Literal["listed", "all", "none"] = "listed"
    agent_approval_timeout: float = 300  # UI: seconds to wait for an answer, then "no"; 0 = forever
    # Contact (email or URL) sent in the User-Agent; Wikipedia and OSM policies require one
    tools_contact: str = ""
    places_url: str = "https://nominatim.openstreetmap.org"  # OpenStreetMap Nominatim
    places_timeout: float = 10.0  # seconds per map lookup
    wiki_url: str = "https://{lang}.wikipedia.org/w"  # {lang} = en, fr, ar...
    wiki_timeout: float = 10.0  # seconds per Wikipedia lookup

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
