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

    # Image engine (app/engine)
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

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
