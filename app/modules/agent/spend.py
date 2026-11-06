"""What each reasoning run cost: tokens -> USD, one line per run in SPEND_LOG (JSONL).

reason() calls record() after every run (CLI and UI), so the file is the spend history.
The UI shows the last run, the total and CREDIT_BUDGET minus the total. Anthropic has no
API for the credit balance, so "left" is an estimate: it misses use of the same key
elsewhere, and CREDIT_BUDGET must be updated by hand after a top-up.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings

ROOT = Path(__file__).resolve().parents[3]

# USD per million tokens: input, output, cache read. A cache write (5 min) is 1.25x input.
# Check https://www.anthropic.com/pricing when adding a model; unknown models cost None.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),  # prompts up to 100K tokens
}
CACHE_WRITE = 1.25


def _log_path() -> Path:
    path = Path(get_settings().spend_log)
    return path if path.is_absolute() else ROOT / path


def cost(model: str, usage: dict) -> float | None:
    """USD for one run's token counts (Strands usage keys), or None for an unknown model."""
    price = PRICES.get(model)
    if price is None:
        return None
    inp, out, read = price
    return (
        usage.get("inputTokens", 0) * inp
        + usage.get("cacheWriteInputTokens", 0) * inp * CACHE_WRITE
        + usage.get("cacheReadInputTokens", 0) * read
        + usage.get("outputTokens", 0) * out
    ) / 1_000_000


def record(model: str, usage: dict) -> dict:
    """Append one run to the spend log and return the line."""
    line = {
        "at": datetime.now(UTC).isoformat(),
        "model": model,
        "input": usage.get("inputTokens", 0),
        "cache_read": usage.get("cacheReadInputTokens", 0),
        "cache_write": usage.get("cacheWriteInputTokens", 0),
        "output": usage.get("outputTokens", 0),
        "usd": cost(model, usage),
    }
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(line) + "\n")
    return line


def _tokens(line: dict) -> int:
    """All tokens of one run: input, cache read, cache write and output."""
    return sum(line.get(k, 0) for k in ("input", "cache_read", "cache_write", "output"))


def summary() -> dict:
    """Last run, run count, total tokens and USD, budget and what is left (None = no budget)."""
    lines = []
    path = _log_path()
    if path.exists():
        for raw in path.read_text().splitlines():
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                continue  # skip a broken line
    spent = sum(line.get("usd") or 0 for line in lines)
    total_tokens = sum(_tokens(line) for line in lines)
    budget = get_settings().credit_budget or None
    last = lines[-1] if lines else None
    if last:
        last = {
            **last,
            "tokens": _tokens(last),
        }
    return {
        "last": last,
        "runs": len(lines),
        "total_tokens": total_tokens,
        "spent": round(spent, 4),
        "budget": budget,
        "left": round(budget - spent, 4) if budget else None,
    }
