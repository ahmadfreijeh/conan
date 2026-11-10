"""Queue a job by hand, without the API (job types: see app/services/jobs.py):

uv run python -m app.worker agent:geolocation --image samples/photo.jpeg [--tools find_place]
"""

import argparse
import asyncio
import uuid
from pathlib import Path
from typing import Any

from app.db.session import engine
from app.modules.engine.helpers import split_csv
from app.services.jobs import JOBS, close_arq_pool, queue_job


async def queue(job_id: uuid.UUID, job_type: str, meta: dict[str, Any]) -> None:
    await queue_job(job_type, meta, job_id)
    await close_arq_pool()
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description=__doc__)
    parser.add_argument("type", help="job type, e.g. agent:geolocation")
    parser.add_argument("--image", help="path to a JPEG, PNG or WebP file")
    parser.add_argument("--tools", help='comma-separated tool names; "" = none (default: all)')
    args = parser.parse_args()

    job_id = uuid.uuid4()
    meta: dict[str, Any] = {"out_dir": str(JOBS / str(job_id))}
    if args.image:
        meta["image"] = str(Path(args.image).resolve())
    if args.tools is not None:
        meta["tools"] = split_csv(args.tools)
    asyncio.run(queue(job_id, args.type, meta))
    print(f"queued {args.type} job {job_id}")


if __name__ == "__main__":
    main()
