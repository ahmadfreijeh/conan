"""Test-run endpoints for the UI: upload a photo, stream progress, browse past runs."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.core.errors import AppError
from app.services.runs import run_manager

router = APIRouter(prefix="/runs", tags=["runs"])

ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # same limit as the engine loader


class ApprovalAnswer(BaseModel):
    answer: Literal["yes", "no", "all"]  # all = allow this tool for the rest of the run


def _page(items: list[dict], page: int, size: int) -> dict:
    """One page of a newest-first list, plus totals for the pager."""
    pages = max(1, -(-len(items) // size))  # ceil, at least 1 so an empty list has page 1
    page = min(page, pages)
    start = (page - 1) * size
    return {
        "items": items[start : start + size],
        "total": len(items),
        "page": page,
        "size": size,
        "pages": pages,
    }


@router.post("", status_code=201)
async def start_run(file: UploadFile) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise AppError("Upload a JPEG, PNG or WebP photo")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError("Photo is larger than 30 MB")
    run_id = await run_manager.start(data, suffix, file.filename or "photo")
    return {"id": run_id}


@router.get("")
async def list_runs(page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=100)) -> dict:
    """History: uploads, newest first, one page at a time."""
    return _page(run_manager.history(), page, size)


@router.get("/log")
async def extraction_log(page: int = Query(1, ge=1), size: int = Query(15, ge=1, le=100)) -> dict:
    """Phase 1 run stats from runs.jsonl (time, size, objects, timings, memory), newest first."""
    return _page(run_manager.extraction_log(), page, size)


@router.delete("")
async def delete_all_runs() -> dict:
    """Clear the history (a run that is still going is kept)."""
    return {"deleted": run_manager.delete_all()}


@router.get("/{run_id}")
async def get_run(run_id: str) -> dict:
    """One run: status, log, result, the clues and crops that were sent to the LLM."""
    return run_manager.detail(run_id)


@router.get("/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    """Live log as server-sent events; ends with an `end` event holding the status.

    When the agent waits for a tool approval, an `approval` event holds {"tool", "input"};
    `approval_done` follows once it is answered (by the person or by the timeout).
    """
    run_manager.status(run_id)  # 404 if unknown

    async def stream() -> AsyncIterator[str]:
        sent = 0
        approvals = 0
        asking = False  # an approval box is open in the UI
        while True:
            live = run_manager.live(run_id)
            finished = live is None  # check first so no last line is missed
            lines = run_manager.log_lines(run_id)
            while sent < len(lines):
                yield f"data: {json.dumps({'line': lines[sent]})}\n\n"
                sent += 1
            if live and live.waiting and live.approvals > approvals:
                approvals = live.approvals
                asking = True
                yield f"event: approval\ndata: {json.dumps(live.waiting)}\n\n"
            elif asking and not (live and live.waiting):
                asking = False
                yield "event: approval_done\ndata: {}\n\n"
            if finished:
                yield f"event: end\ndata: {json.dumps({'status': run_manager.status(run_id)})}\n\n"
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/{run_id}/approval", status_code=204)
async def answer_approval(run_id: str, body: ApprovalAnswer) -> None:
    """Answer the tool call the run is waiting on: yes, no or all."""
    await run_manager.answer(run_id, body.answer)


@router.get("/{run_id}/files/{name}")
async def run_file(run_id: str, name: str) -> FileResponse:
    """`photo` or a sent crop file name, e.g. `1_sign.jpg`."""
    return FileResponse(run_manager.file(run_id, name))


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str) -> None:
    run_manager.delete(run_id)
