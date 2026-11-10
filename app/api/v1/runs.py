"""Test-run endpoints for the UI: upload a photo, follow its status, browse past runs.

A run is an `agent:geolocation` job; the Arq worker runs it (app/services/runs.py).
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.api.deps import RedisDep, SessionDep
from app.core.errors import AppError
from app.modules.agent import spend
from app.modules.agent.tools import select_tools, tool_list
from app.modules.engine.helpers import split_csv
from app.services import runs
from app.services.rate_limit import check_run_limit, client_ip, record_run

router = APIRouter(prefix="/runs", tags=["runs"])


class ApprovalAnswer(BaseModel):
    answer: Literal["yes", "no", "all"]  # all = allow this tool for the rest of the run


ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # same limit as the engine loader


@router.post("", status_code=201)
async def start_run(
    request: Request,
    redis: RedisDep,
    file: UploadFile,
    tools: Annotated[str | None, Form()] = None,
) -> dict:
    """Upload a photo and queue a run. tools: comma-separated names the agent gets
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
    names = None if tools is None else split_csv(tools)
    if names is not None:
        try:
            select_tools(names)  # 400 on an unknown name, before the run starts
        except RuntimeError as exc:
            raise AppError(str(exc)) from None
    run_id = await runs.start(data, suffix, file.filename or "photo", names)
    await record_run(redis, ip)
    return {"id": run_id}


@router.get("")
async def list_runs(
    session: SessionDep, page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=100)
) -> dict:
    """History: runs, newest first, one page at a time."""
    return await runs.history(session, page, size)


@router.get("/log")
async def extraction_log(page: int = Query(1, ge=1), size: int = Query(15, ge=1, le=100)) -> dict:
    """Phase 1 run stats from runs.jsonl (time, size, objects, timings, memory), newest first."""
    return runs.extraction_log(page, size)


@router.get("/tools")
async def list_tools() -> list[dict]:
    """The agent's tools for the UI toggles: name, short description, needs approval."""
    return tool_list()


@router.get("/spend")
async def spend_summary() -> dict:
    """LLM spend: last run (tokens, USD), total spent, CREDIT_BUDGET and what is left."""
    return spend.summary()


@router.delete("")
async def delete_all_runs(session: SessionDep) -> dict:
    """Clear the history (queued and running runs are kept)."""
    return {"deleted": await runs.delete_all(session)}


@router.get("/{run_id}")
async def get_run(run_id: str, session: SessionDep) -> dict:
    """One run: status, log, result, the clues and crops that were sent to the LLM."""
    return await runs.detail(session, run_id)


@router.get("/{run_id}/events")
async def run_events(
    run_id: str, redis: RedisDep, last_event_id: Annotated[str | None, Header()] = None
) -> StreamingResponse:
    """Live log as server-sent events; ends with an `end` event holding the status.

    When the agent waits for a tool approval, an `approval` event holds {"tool", "input"};
    `approval_done` follows once it is answered (by the person or by the timeout).

    Each line carries its Redis stream id as the event id. When the browser reconnects it
    sends the last id it got (Last-Event-ID header), so the stream goes on from there.
    """
    await runs.check(run_id)  # 404 before the stream starts
    start = last_event_id if last_event_id and "-" in last_event_id else "0"

    async def stream() -> AsyncIterator[str]:
        async for entry_id, fields in runs.follow(redis, run_id, start):
            if "end" in fields:
                yield f"event: end\ndata: {json.dumps({'status': fields['end']})}\n\n"
            elif "event" in fields:  # approval: {"tool", "input"}; approval_done: answered
                yield f"event: {fields['event']}\ndata: {fields['data']}\n\n"
            else:
                yield f"id: {entry_id}\ndata: {json.dumps({'line': fields['line']})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/{run_id}/approval", status_code=204)
async def answer_approval(run_id: str, body: ApprovalAnswer, redis: RedisDep) -> None:
    """Answer the tool call the run is waiting on: yes, no or all."""
    await runs.answer(redis, run_id, body.answer)


@router.get("/{run_id}/files/{name}")
async def run_file(run_id: str, name: str, session: SessionDep) -> FileResponse:
    """`photo` or a sent crop file name, e.g. `1_sign.jpg`."""
    return FileResponse(await runs.file(session, run_id, name))


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str, session: SessionDep) -> None:
    await runs.delete(session, run_id)
