"""Test-run endpoints for the UI: upload a photo, stream progress, browse past runs."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.api.deps import RedisDep
from app.core.errors import AppError
from app.modules.agent import spend
from app.modules.agent.tools import select_tools, tool_list
from app.services.rate_limit import check_run_limit, client_ip, record_run
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
async def start_run(
    request: Request,
    redis: RedisDep,
    file: UploadFile,
    tools: Annotated[str | None, Form()] = None,
) -> dict:
    """Upload a photo and start a run. tools: comma-separated names the agent gets
    (left out = all, "" = none); a tool that is off is not given to the agent at all.

    Limited to RUN_LIMIT_PER_HOUR runs per IP per rolling hour (429 + Retry-After),
    except when APP_ENV=local. Only runs that start count."""
    ip = client_ip(request)
    await check_run_limit(redis, ip)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise AppError("Upload a JPEG, PNG or WebP photo")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError("Photo is larger than 30 MB")
    names = None if tools is None else [n.strip() for n in tools.split(",") if n.strip()]
    if names is not None:
        try:
            select_tools(names)  # 400 on an unknown name, before the run starts
        except RuntimeError as exc:
            raise AppError(str(exc)) from None
    run_id = await run_manager.start(data, suffix, file.filename or "photo", names)
    await record_run(redis, ip)
    return {"id": run_id}


@router.get("")
async def list_runs(page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=100)) -> dict:
    """History: uploads, newest first, one page at a time."""
    return _page(run_manager.history(), page, size)


@router.get("/log")
async def extraction_log(page: int = Query(1, ge=1), size: int = Query(15, ge=1, le=100)) -> dict:
    """Phase 1 run stats from runs.jsonl (time, size, objects, timings, memory), newest first."""
    return _page(run_manager.extraction_log(), page, size)


@router.get("/tools")
async def list_tools() -> list[dict]:
    """The agent's tools for the UI toggles: name, short description, needs approval."""
    return tool_list()


@router.get("/spend")
async def spend_summary() -> dict:
    """LLM spend: last run (tokens, USD), total spent, CREDIT_BUDGET and what is left."""
    return spend.summary()


@router.delete("")
async def delete_all_runs() -> dict:
    """Clear the history (a run that is still going is kept)."""
    return {"deleted": run_manager.delete_all()}


@router.get("/{run_id}")
async def get_run(run_id: str) -> dict:
    """One run: status, log, result, the clues and crops that were sent to the LLM."""
    return run_manager.detail(run_id)


@router.get("/{run_id}/events")
async def run_events(
    run_id: str, last_event_id: Annotated[str | None, Header()] = None
) -> StreamingResponse:
    """Live log as server-sent events; ends with an `end` event holding the status.

    Each line carries its index as the event id. When the browser reconnects it sends the
    last id it got (Last-Event-ID header), so the stream goes on from the next line instead
    of sending the whole log again.

    When the agent waits for a tool approval, an `approval` event holds {"tool", "input"};
    `approval_done` follows once it is answered (by the person or by the timeout).
    """
    run_manager.status(run_id)  # 404 if unknown

    async def stream() -> AsyncIterator[str]:
        sent = int(last_event_id) + 1 if last_event_id and last_event_id.isdigit() else 0
        approvals = 0
        asking = False  # an approval box is open in the UI
        while True:
            live = run_manager.live(run_id)
            finished = live is None  # check first so no last line is missed
            lines = run_manager.log_lines(run_id)
            while sent < len(lines):
                yield f"id: {sent}\ndata: {json.dumps({'line': lines[sent]})}\n\n"
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
