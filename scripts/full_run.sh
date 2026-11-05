#!/usr/bin/env bash
# Full cycle on one photo: install, Phase 1 (extract), show clues, Phase 2 (reason).
# Usage: scripts/full_run.sh [image] [out_dir]
#   scripts/full_run.sh                              -> samples/photo.jpeg, out/photo
#   scripts/full_run.sh samples/cafe.jpg out/cafe
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${1:-samples/photo.jpeg}"
OUT="${2:-out/$(basename "${IMAGE%.*}")}"

step() { printf '\n==> %s\n' "$1"; }

step "1/5 Install dependencies"
uv sync --quiet

step "2/5 Check config"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "created .env from .env.example"
fi
if ! grep -qE '^ANTHROPIC_API_KEY=.+' .env; then
  echo "error: set ANTHROPIC_API_KEY in .env, then run again" >&2
  exit 1
fi

step "3/5 Phase 1: extract clues from $IMAGE"
uv run python -m app.modules.engine "$IMAGE" --out "$OUT" > /dev/null

step "4/5 Clues sent to the LLM"
uv run python -m app.modules.agent.clues "$OUT/payload.json"

step "5/5 Phase 2: reason"
uv run python -m app.modules.engine "$IMAGE" --reason --from-payload "$OUT/payload.json" --out "$OUT"

printf '\nDone. Files in %s: payload.json, reason.json, crops/, text/\n' "$OUT"
