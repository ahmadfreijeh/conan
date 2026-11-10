"""A job's printed lines, sent live to Redis.

While a job runs, the worker points print() (stdout and stderr) at a LiveLog. Each line goes
to the Redis stream `job:{id}:log` (the API streams it to the UI) and is kept for the job row.
This swaps stdout for the whole process, so it is only safe with one job at a time (max_jobs=1).
"""

import io

from redis import Redis

from app.services.jobs import job_key

KEEP_SECONDS = 300  # after the job ends, the Redis copy stays this long, then the row has it


class LiveLog(io.TextIOBase):
    def __init__(self, redis: Redis, job_id: str) -> None:
        self.redis, self.key = redis, job_key(job_id, "log")
        self.lines: list[str] = []
        self._part = ""  # text after the last newline

    def write(self, text: str) -> int:
        self._part += text
        *done, self._part = self._part.split("\n")
        for line in done:
            self.add(line.rsplit("\r", 1)[-1])  # progress bars redraw with \r: keep the last
        return len(text)

    def add(self, line: str) -> None:
        self.lines.append(line)
        self.redis.xadd(self.key, {"line": line})

    def event(self, name: str, data: str = "{}") -> None:
        """An event for the UI (e.g. `approval`), sent in order with the lines."""
        self.redis.xadd(self.key, {"event": name, "data": data})

    def text(self) -> str:
        """The full log (call when the job is over)."""
        if self._part:
            self.add(self._part)
            self._part = ""
        return "\n".join(self.lines)

    def end(self, status: str) -> None:
        """Tell readers the job is over, and let the Redis copy expire."""
        self.redis.xadd(self.key, {"end": status})
        self.redis.expire(self.key, KEEP_SECONDS)
