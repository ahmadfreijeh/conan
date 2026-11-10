"""Runs for the test UI. A run is an `agent:geolocation` job (app/services/jobs.py) that
the Arq worker runs. Status, result and log are on the job row; the files (photo,
payload.json, clues.json, sent/, reason.json) are in out/jobs/<job id>/.
"""

import asyncio
import json
import re
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.db.session import SessionLocal
from app.models.job import Job
from app.repositories.job import FINISHED, JobRepository
from app.services.jobs import JOBS, ROOT, job_key, queue_job

RUN_TYPE = "agent:geolocation"
KIND = RUN_TYPE.split(":")[0]  # the run list shows every agent job


class RunBusyError(AppError):
    status_code = 409
    code = "run_busy"


class NotWaitingError(AppError):
    status_code = 409
    code = "not_waiting"


def page_info(total: int, page: int, size: int) -> dict:
    """Pager fields for a newest-first list; `page` is clamped to the last page."""
    pages = max(1, -(-total // size))  # ceil, at least 1 so an empty list has page 1
    return {"total": total, "page": min(page, pages), "size": size, "pages": pages}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _save_photo(run_dir: Path, suffix: str, data: bytes) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    image = run_dir / f"photo{suffix}"
    image.write_bytes(data)
    return image


async def start(data: bytes, suffix: str, filename: str, tools: list[str] | None) -> str:
    """Save the photo and queue the run. tools: names the agent gets (None = all, [] = none)."""
    job_id = uuid.uuid4()
    run_dir = JOBS / str(job_id)
    image = await asyncio.to_thread(_save_photo, run_dir, suffix, data)
    meta = {"filename": filename, "image": str(image), "out_dir": str(run_dir), "tools": tools}
    await queue_job(RUN_TYPE, meta, job_id)
    return str(job_id)


def _id(run_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(run_id)
    except ValueError:
        raise NotFoundError(f"Run {run_id} not found") from None


async def _get(session: AsyncSession, run_id: str) -> Job:
    job = await JobRepository(session).get(_id(run_id))
    if job is None or job.kind != KIND:
        raise NotFoundError(f"Run {run_id} not found")
    return job


def _summary(job: Job) -> dict:
    """The fields the history list needs, with the top guess."""
    top = ((job.result or {}).get("candidates") or [None])[0]
    return {
        "id": str(job.id),
        "filename": job.meta.get("filename") or Path(job.meta.get("image", "")).name,
        "tools": job.meta.get("tools"),
        "status": job.status,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "top_guess": ", ".join(x for x in (top.get("city"), top.get("country")) if x)
        if top
        else None,
        "confidence": top.get("confidence") if top else None,
    }


async def history(session: AsyncSession, page: int, size: int) -> dict:
    """One page of runs, newest first, plus totals for the pager."""
    jobs = JobRepository(session)
    info = page_info(await jobs.count_by_kind(KIND), page, size)
    items = await jobs.list_by_kind(KIND, offset=(info["page"] - 1) * size, limit=size)
    return {"items": [_summary(j) for j in items], **info}


async def detail(session: AsyncSession, run_id: str) -> dict:
    job = await _get(session, run_id)
    out = Path(job.meta["out_dir"])
    return {
        **_summary(job),
        "log": (job.log or "").splitlines(),
        "result": job.result,
        "clues": _read_json(out / "clues.json"),
        "sent": _read_json(out / "sent" / "crops.json") or [],
    }


async def check(run_id: str) -> None:
    """404 if the run is unknown (own session: the event stream outlives the request)."""
    async with SessionLocal() as session:
        await _get(session, run_id)


async def _saved_log(redis: Redis, run_id: str) -> list[tuple[str, dict]] | None:
    """The finished run's log from its row, once the live copy in Redis is gone; else None."""
    if await redis.exists(job_key(run_id, "log")):
        return None
    async with SessionLocal() as session:
        job = await _get(session, run_id)
    if job.status not in FINISHED:
        return None
    lines = (job.log or "").splitlines()
    return [(str(i), {"line": line}) for i, line in enumerate(lines)] + [
        ("end", {"end": job.status})
    ]


async def follow(redis: Redis, run_id: str, last_id: str = "0") -> AsyncIterator[tuple[str, dict]]:
    """The run's log as (id, fields): {"line": ...} lines and {"event", "data"} events (tool
    approvals), then one {"end": status}.

    Live from the Redis stream the worker writes; from the job row once that has expired.
    last_id: go on after this stream entry (the browser's Last-Event-ID on reconnect).
    """
    key = job_key(run_id, "log")
    got = None  # check the row first, then only when 5 s pass with no new lines
    while True:
        if not got:
            saved = await _saved_log(redis, run_id)
            if saved is not None:
                for entry in saved:
                    yield entry
                return
        got = await redis.xread({key: last_id}, count=100, block=5000)
        for _, entries in got:
            for last_id, fields in entries:
                yield last_id, fields
                if "end" in fields:
                    return


async def answer(redis: Redis, run_id: str, answer: str) -> None:
    """Pass the person's reply (yes / no / all) to the worker waiting on an approval."""
    if not await redis.exists(job_key(run_id, "approval")):
        raise NotWaitingError("This run is not waiting for an approval")
    key = job_key(run_id, "answer")
    await redis.rpush(key, answer)
    await redis.expire(key, 60)  # the worker takes it at once; drop it if it is too late


async def _remove(repo: JobRepository, job: Job) -> None:
    """Delete the run's folder (off the event loop) and its row."""
    await asyncio.to_thread(shutil.rmtree, job.meta["out_dir"], True)  # True = ignore errors
    await repo.delete(job)


async def delete(session: AsyncSession, run_id: str) -> None:
    job = await _get(session, run_id)
    if job.status not in FINISHED:
        raise RunBusyError("This run is still going; wait for it to finish")
    await _remove(JobRepository(session), job)


async def delete_all(session: AsyncSession) -> int:
    """Delete every finished run; queued and running ones are kept. Returns how many."""
    repo = JobRepository(session)
    jobs = await repo.finished_by_kind(KIND)
    for job in jobs:
        await _remove(repo, job)
    return len(jobs)


async def file(session: AsyncSession, run_id: str, name: str) -> Path:
    """The photo or a sent crop. Only plain file names inside the run folder."""
    job = await _get(session, run_id)
    if name == "photo":
        return Path(job.meta["image"])
    sent = Path(job.meta["out_dir"]) / "sent" / name
    if re.fullmatch(r"[0-9]+_[a-z0-9_]+\.jpg", name) and sent.exists():
        return sent
    raise NotFoundError(f"File {name} not found")


def extraction_log(page: int, size: int) -> dict:
    """One page of Phase 1 runs from runs.jsonl (CLI and jobs), newest first.

    Lines whose photo is in a job folder that still exists get its run_id, so the UI can open it.
    """
    path = Path(get_settings().engine_runs_log)
    path = path if path.is_absolute() else ROOT / path
    entries = []
    for raw in path.read_text().splitlines() if path.exists() else []:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue  # skip a broken line instead of failing the whole log
        image = Path(entry.get("image", ""))
        from_job = image.parent.parent == JOBS
        entry["run_id"] = image.parent.name if from_job and image.parent.is_dir() else None
        entry["source"] = "ui" if from_job else "cli"
        if from_job:
            entry["name"] = f"CASE-{image.parent.name[:6].upper()}"
        elif image.is_relative_to(ROOT):
            entry["name"] = str(image.relative_to(ROOT))
        else:
            entry["name"] = str(image)  # CLI paths are saved as typed, e.g. samples/photo.jpeg
        entries.append(entry)
    entries.sort(key=lambda e: e.get("started_at", ""), reverse=True)
    info = page_info(len(entries), page, size)
    start = (info["page"] - 1) * size
    return {"items": entries[start : start + size], **info}
