# Plan: move runs to Arq workers

Status: steps 1–6 built. Open points below are for later.

## Why

Before this plan, `app/services/runs.py` started `scripts/full_run.sh` as a subprocess
from the API, kept run state in memory and in `out/uploads/`, and allowed one run at a
time. The Arq worker (`app/worker/main.py`) had no jobs.

Goal: the API only queues work; workers do the heavy runs; run state lives in Postgres
and Redis, so we can run many runs and add more workers.

## Keep it simple

Applies to every step in this branch:

- New code: smallest version that works. No extra layers, options or helpers "for later".
- Code we touch: simplify it while we are there (remove dead code, merge duplicates,
  shorten long functions), e.g. `app/services/runs.py` once the subprocess is gone.
- After each step, run `/simplify` on the diff before moving on.

## Steps

1. **Jobs table**: `Job` model + migration. A generic table, so later it can hold other
   kinds of work, not only geolocation runs. Columns: id, type (broad kind of work: `agent`, `upload`, ...),
   status, metadata (JSON: what the job does, e.g. geolocation, plus filename, tools and
   other job-specific data), result (JSON),
   created/finished times. No behavior change yet.
2. **Worker job**: one general `agent` job in the worker (not tied to geolocation). It
   reads the job row, sees from metadata what to do (e.g. geolocation), calls the engine
   and agent in Python (no bash script) and updates the row.
3. **API queues jobs**: keep the `/runs` endpoints as they are for the UI. `POST /runs`
   saves a job row and calls `enqueue_job`. The read endpoints (list, detail) get data
   from a `JobRepository`, filtered to agent geolocation jobs. Drop the
   one-run-at-a-time limit.
4. **Live log via Redis**: the worker writes log lines (the terminal lines: steps, tool
   calls, approvals, errors) to a Redis stream; the SSE endpoint reads from it. When the
   job ends, the full log is saved on the job row and the Redis key gets a short expiry
   (a few minutes), so a connected UI can still read the last lines.
5. **Approvals via Redis**: the worker publishes the approval request; the API writes the
   answer to Redis; the worker waits on it. The timeout moves to the worker.
6. **Models loaded once**: load detection/OCR in the worker `startup`, reuse per job.

## Open points

- Files (photo, crops): keep on local disk for now (API and worker on the same machine);
  move to S3 or similar when deployed.
- Concurrency: start with a low `max_jobs` (1–2), since the models use a lot of memory.
- Old runs in `out/uploads/`: decided, left on disk (see Decisions).
- Remove `log.txt` later: keep it during the move for debugging, then drop it once the
  DB log works in the UI (avoids two copies getting out of sync, and the file won't exist
  when the worker runs on another machine).

## Decisions

- Table is `jobs` (not `runs`), so it can be reused for other kinds of work.
- Run-specific data (filename, tools, ...) goes in a JSON `metadata` column, not in
  separate columns.
- `type` is a broad kind (`agent`, `upload`, ...). The specific task (e.g. geolocation)
  lives in metadata.
- `type` can also take a sub-kind after a colon: the task, not a tool, e.g.
  `agent:geolocation`. Tools the agent calls (wiki_search, find_place, ...) are steps
  inside that job. Filter a whole kind with a prefix match (`type LIKE 'agent:%'`).
- Worker jobs are general too: one `agent` job handles all agent tasks; the task comes
  from the job's metadata.
- Keep the `/runs` APIs. They read from `JobRepository`, filtering for agent jobs, so a
  "run" is just a filtered view of jobs.
- Live log is temporary in Redis: on finish, save it to the job row, then expire the
  Redis key.
- One job = one full run (extract, then reason, with all its tool calls). Tool calls are
  steps inside the job, not separate jobs, since the agent picks each next tool from the
  last result.
- Everything for a run is linked by the job id: run id in the UI, Arq `_job_id`, and Redis
  keys (`job:{id}:log`, `job:{id}:approval`, `job:{id}:answer`).
- Approvals go through Redis, not stdin: the worker saves the request, the API sends the
  `approval` SSE event, the answer comes back through Redis, and the worker counts no
  answer as "no" after the timeout. The CLI terminal prompt stays for manual runs.
- The full log is stored on the job row (text, a few KB per run); the API reads it from
  there. `log.txt` is still written in the job folder as an extra copy on disk.
- `status` is a plain string (`queued`, `running`, `done`, `failed`), not a DB enum, so
  it is easy to change. The `log` column is added in step 1 with the rest.
- The worker picks the task from the job type (`agent:geolocation` -> `geolocation`), so
  the task is not stored twice. Metadata holds the inputs: `image`, `out_dir`, `tools`.
- A job's files go to `out/jobs/<job id>/`. Until step 5, tools that need approval get "no".
- Layout: `app/worker/main.py` is the entry (`WorkerSettings`, list of jobs). Each job kind
  has one file in `app/worker/jobs/` (e.g. `agent.py`). Inside it, the job picks the task
  with if/else and calls a private function in the same file (e.g. `_geolocate`).
- Step 3: only new jobs show in the UI; old runs in `out/uploads/` stay on disk, unread.
- `queue_job()` (`app/services/jobs.py`) saves the row in its own session and commits it
  before queueing, so the worker always finds it. The API has its own Arq pool (its Redis client decodes text; Arq
  needs bytes).
- A failed job keeps `result` empty and puts the error in `log`.
- Step 4: the worker catches the job's prints (stdout and stderr) with `LiveLog`
  (`app/worker/live_log.py`) and sends each line to `job:{id}:log`; engine and agent code
  is unchanged. Only safe with one job per worker (`max_jobs = 1`); pass a `log` function
  instead if we ever run jobs side by side. The `end` entry is written after the row is
  committed; the key then expires after 5 minutes and the API reads the log from the row.
- Step 5: approval events (`approval`, `approval_done`) go on the same `job:{id}:log`
  stream as the lines, so the events endpoint needs nothing extra. The worker waits for
  the answer with BLPOP on `job:{id}:answer` (timeout = AGENT_APPROVAL_TIMEOUT).
- Step 6: the models were already cached per process (`lru_cache`); the worker now loads
  them in `startup`, so the first job is not slow. The worker takes longer to start.
