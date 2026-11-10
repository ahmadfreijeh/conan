"""The `agent` job: runs any `agent:<task>` job row. The task picks a private function
in this file (e.g. `agent:geolocation` -> `_geolocate`)."""

import asyncio
import logging
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.session import SessionLocal
from app.modules.agent.constants import Approve
from app.repositories.job import JobRepository
from app.worker.live.approval import redis_approve
from app.worker.live.log import LiveLog

logger = logging.getLogger(__name__)


async def agent(ctx: dict[str, Any], job_id: str) -> None:
    """Run the job row `job_id`; its prints go live to Redis, then status, result and log
    are saved on the row."""
    async with SessionLocal() as session:
        jobs = JobRepository(session)
        job = await jobs.get(uuid.UUID(job_id))
        if job is None:
            logger.error("job %s not found", job_id)
            return
        await jobs.update(job, status="running", started_at=datetime.now(UTC))
        await session.commit()

        live = LiveLog(ctx["log_redis"], job_id)
        approve = redis_approve(ctx["log_redis"], job_id, live)
        task, meta = job.task, job.meta

        def run() -> dict[str, Any]:
            with redirect_stdout(live), redirect_stderr(live):
                return _run(task, meta, approve)

        try:
            # The engine and agent are blocking code: run them off the event loop.
            fields = {"status": "done", "result": await asyncio.to_thread(run)}
        except Exception as exc:
            logger.exception("job %s failed", job_id)
            live.add(f"error: {exc}")
            fields = {"status": "failed"}  # result stays empty; the error is in the log
        log = live.text()
        (Path(meta["out_dir"]) / "log.txt").write_text(log + "\n")  # extra copy on disk
        await jobs.update(job, **fields, log=log, finished_at=datetime.now(UTC))
        await session.commit()
        live.end(fields["status"])  # after the commit, so the UI reads the final row


def _run(task: str, meta: dict[str, Any], approve: Approve) -> dict[str, Any]:
    if task == "geolocation":
        return _geolocate(meta, approve)
    raise ValueError(f"unknown agent task: {task}")


def _geolocate(meta: dict[str, Any], approve: Approve) -> dict[str, Any]:
    """Full run on one photo: extract clues, then reason. Files go to meta["out_dir"].
    The `==> n/5` lines move the step bar in the UI."""
    from app.modules.agent.reason import reason  # heavy imports, only when a job runs
    from app.modules.engine.pipeline import run_pipeline
    from app.modules.engine.prepare import prepare_for_agent, save_agent_input

    out = Path(meta["out_dir"])
    print("==> 3/5 Extract clues")
    payload = run_pipeline(meta["image"], out_dir=out)
    print("==> 4/5 Prepare clues for the LLM")
    agent_input = prepare_for_agent(payload)
    save_agent_input(agent_input, out)
    print("==> 5/5 Reason")
    result = reason(agent_input, approve=approve, tool_names=meta.get("tools"))
    (out / "reason.json").write_text(result.model_dump_json(indent=2))
    return result.model_dump(mode="json")
