"""Tool approval for worker jobs, through Redis.

When the agent wants a tool that needs approval, `approve` saves the request in
`job:{id}:approval`, sends an `approval` event on the live log (the UI shows Yes / No / All)
and waits for the answer the API pushes to `job:{id}:answer`. No answer in
AGENT_APPROVAL_TIMEOUT seconds counts as no.
"""

import json

from redis import Redis

from app.core.config import get_settings
from app.modules.agent.constants import ANSWERS, NO, Approve
from app.modules.agent.helpers import format_call
from app.services.jobs import job_key
from app.worker.live_log import LiveLog


def redis_approve(redis: Redis, job_id: str, live: LiveLog) -> Approve:
    """An approve function for reason(): {"tool", "input"} -> "yes", "no" or "all"."""
    request_key, answer_key = job_key(job_id, "approval"), job_key(job_id, "answer")

    def approve(request: dict) -> str:
        call = format_call(request["tool"], request["input"])
        timeout = get_settings().agent_approval_timeout
        redis.delete(answer_key)  # drop a late answer to an earlier request
        redis.set(request_key, json.dumps(request))
        live.add(f"[approval] waiting: {call}")
        live.event("approval", json.dumps(request))
        got = redis.blpop([answer_key], timeout=timeout)  # timeout 0 = wait forever
        redis.delete(request_key)
        answer = ANSWERS.get(got[1], NO) if got else NO
        note = answer if got else f"no answer in {timeout:g}s, counted as no"
        live.add(f"[approval] {note}: {call}")
        live.event("approval_done")
        return answer

    return approve
