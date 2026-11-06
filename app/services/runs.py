"""Test runs started from the UI: run scripts/full_run.sh on an uploaded photo.

Local testing only. Each run lives in out/uploads/<id>/: the photo, run.json (status),
log.txt and everything the script writes (payload.json, clues.json, sent/, reason.json).
That folder is the history; it survives restarts. One run goes at a time so the models
are not loaded twice. Later this can move to the Arq worker and the database.

Tool approval: when the agent needs a yes/no, the run prints one APPROVAL_PREFIX line and
waits on stdin. The run is then "waiting"; answer() writes the person's reply to stdin.
If nobody answers within AGENT_APPROVAL_TIMEOUT seconds, the server answers "no" itself.
"""

import asyncio
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.modules.agent.constants import APPROVAL_PREFIX

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "full_run.sh"
UPLOADS = ROOT / "out" / "uploads"
RUN_ID = re.compile(r"^[0-9a-f]{12}$")


class RunBusyError(AppError):
    status_code = 409
    code = "run_busy"


class NotWaitingError(AppError):
    status_code = 409
    code = "not_waiting"


@dataclass
class LiveRun:
    """A run that is going right now; its log is kept in memory until it ends."""

    id: str
    dir: Path
    lines: list[str] = field(default_factory=list)
    finished: bool = False
    proc: asyncio.subprocess.Process | None = None
    waiting: dict | None = None  # the approval request ({"tool", "input"}) it waits on
    approvals: int = 0  # requests so far; the event stream uses it to spot a new one


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _call_text(request: dict) -> str:
    """e.g. find_place(name="Place Saint-Georges", city="Paris")"""
    args = ", ".join(
        f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in request.get("input", {}).items()
    )
    return f"{request.get('tool')}({args})"


class RunManager:
    def __init__(self) -> None:
        self._live: dict[str, LiveRun] = {}
        self._tasks: set[asyncio.Task] = set()

    # ---- start ---------------------------------------------------------------

    async def start(
        self, data: bytes, suffix: str, filename: str, tools: list[str] | None = None
    ) -> str:
        """Start a run. tools = the tool names the agent gets (None = all, [] = none)."""
        if any(not r.finished for r in self._live.values()):
            raise RunBusyError("Another run is still going; wait for it to finish")

        run_id = uuid.uuid4().hex[:12]
        run_dir = UPLOADS / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        image = run_dir / f"photo{suffix}"
        image.write_bytes(data)
        self._write_meta(
            run_dir,
            id=run_id,
            filename=filename,
            image=image.name,
            created_at=datetime.now(UTC).isoformat(),
            status="running",
            tools=tools,
        )

        run = LiveRun(id=run_id, dir=run_dir)
        self._live[run_id] = run
        task = asyncio.create_task(self._execute(run, image, tools))
        self._tasks.add(task)  # keep a reference so the task is not garbage-collected
        task.add_done_callback(self._tasks.discard)
        return run_id

    async def _execute(self, run: LiveRun, image: Path, tools: list[str] | None) -> None:
        status = "failed"
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}  # print lines as they happen
        if tools is not None:
            env["AGENT_TOOLS"] = ",".join(tools)  # read by build_agent(); "" = no tools
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                str(SCRIPT),
                str(image),
                str(run.dir),
                cwd=ROOT,
                stdin=asyncio.subprocess.PIPE,  # approval answers go here
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
            run.proc = proc
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip("\n")
                if line.startswith(APPROVAL_PREFIX):
                    request = json.loads(line[len(APPROVAL_PREFIX) :])
                    run.waiting = request
                    run.approvals += 1
                    line = f"[approval] waiting: {_call_text(request)}"
                    self._start_approval_timer(run)
                run.lines.append(line)
            status = "done" if await proc.wait() == 0 else "failed"
        except Exception as exc:  # report any launch error in the log instead of hanging
            run.lines.append(f"error: {exc}")
        finally:
            run.waiting = None
            (run.dir / "log.txt").write_text("\n".join(run.lines) + "\n")
            self._write_meta(run.dir, status=status, finished_at=datetime.now(UTC).isoformat())
            run.finished = True
            self._live.pop(run.id, None)  # from now on it is read from disk

    def _start_approval_timer(self, run: LiveRun) -> None:
        """After AGENT_APPROVAL_TIMEOUT seconds, answer "no" if this request still waits."""
        timeout = get_settings().agent_approval_timeout
        if timeout <= 0:
            return  # 0 = wait forever
        request_no = run.approvals

        async def expire() -> None:
            await asyncio.sleep(timeout)
            if run.waiting is not None and run.approvals == request_no:  # not answered yet
                note = f"no answer in {timeout:g}s, counted as no"
                await self.answer(run.id, "no", note=note)

        task = asyncio.create_task(expire())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def answer(self, run_id: str, answer: str, note: str | None = None) -> None:
        """Send the person's reply (yes / no / all) to the run that waits for approval."""
        run = self._live.get(run_id)
        if run is None or run.waiting is None or run.proc is None or run.proc.stdin is None:
            raise NotWaitingError("This run is not waiting for an approval")
        run.lines.append(f"[approval] {note or answer}: {_call_text(run.waiting)}")
        run.waiting = None
        run.proc.stdin.write(f"{answer}\n".encode())
        await run.proc.stdin.drain()

    # ---- read ----------------------------------------------------------------

    def _dir(self, run_id: str) -> Path:
        run_dir = UPLOADS / run_id
        if not RUN_ID.match(run_id) or not self._ensure_meta(run_dir):
            raise NotFoundError(f"Run {run_id} not found")
        return run_dir

    def _ensure_meta(self, run_dir: Path) -> bool:
        """True if the folder is a run. Older runs (before run.json) get one made from files."""
        if (run_dir / "run.json").exists():
            return True
        photos = sorted(run_dir.glob("photo.*")) if run_dir.is_dir() else []
        if not RUN_ID.match(run_dir.name) or not photos:
            return False
        created = datetime.fromtimestamp(photos[0].stat().st_mtime, UTC).isoformat()
        self._write_meta(
            run_dir,
            id=run_dir.name,
            filename=photos[0].name,
            image=photos[0].name,
            created_at=created,
            status="done" if (run_dir / "reason.json").exists() else "failed",
        )
        return True

    def _write_meta(self, run_dir: Path, **fields: Any) -> None:
        path = run_dir / "run.json"
        meta = (_read_json(path) or {}) | fields
        path.write_text(json.dumps(meta, indent=2))

    def _meta(self, run_dir: Path) -> dict:
        meta = _read_json(run_dir / "run.json")
        if meta["status"] == "running" and meta["id"] not in self._live:
            meta["status"] = "interrupted"  # the server stopped while it was running
        return meta

    def live(self, run_id: str) -> LiveRun | None:
        return self._live.get(run_id)

    def log_lines(self, run_id: str) -> list[str]:
        live = self._live.get(run_id)
        if live is not None:
            return live.lines
        log = self._dir(run_id) / "log.txt"
        return log.read_text().splitlines() if log.exists() else []

    def status(self, run_id: str) -> str:
        return self._meta(self._dir(run_id))["status"]

    def history(self) -> list[dict]:
        """All runs, newest first, with the top guess for the list view."""
        items = []
        if UPLOADS.exists():
            for run_dir in UPLOADS.iterdir():
                if not self._ensure_meta(run_dir):
                    continue
                meta = self._meta(run_dir)
                result = _read_json(run_dir / "reason.json") or {}
                top = (result.get("candidates") or [None])[0]
                items.append(
                    {
                        **meta,
                        "top_guess": ", ".join(
                            x for x in (top.get("city"), top.get("country")) if x
                        )
                        if top
                        else None,
                        "confidence": top.get("confidence") if top else None,
                    }
                )
        return sorted(items, key=lambda m: m["created_at"], reverse=True)

    def detail(self, run_id: str) -> dict:
        run_dir = self._dir(run_id)
        live = self._live.get(run_id)
        return {
            **self._meta(run_dir),
            "waiting": live.waiting if live else None,
            "log": self.log_lines(run_id),
            "result": _read_json(run_dir / "reason.json"),
            "clues": _read_json(run_dir / "clues.json"),
            "sent": _read_json(run_dir / "sent" / "crops.json") or [],
        }

    def extraction_log(self) -> list[dict]:
        """Every Phase 1 run from runs.jsonl (CLI and UI), newest first.

        Lines whose photo is a UI upload that still exists get its run_id, so the UI can open it.
        """
        path = Path(get_settings().engine_runs_log)
        path = path if path.is_absolute() else ROOT / path
        if not path.exists():
            return []
        entries = []
        for raw in path.read_text().splitlines():
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue  # skip a broken line instead of failing the whole log
            image = Path(entry.get("image", ""))
            run_id = image.parent.name
            on_file = image.parent.parent == UPLOADS and (image.parent / "run.json").exists()
            from_ui = image.parent.parent == UPLOADS
            entry["run_id"] = run_id if on_file and RUN_ID.match(run_id) else None
            entry["source"] = "ui" if from_ui else "cli"
            if from_ui:
                entry["name"] = f"CASE-{run_id[:6].upper()}"
            elif image.is_relative_to(ROOT):
                entry["name"] = str(image.relative_to(ROOT))
            else:
                entry["name"] = str(image)  # CLI paths are saved as typed, e.g. samples/photo.jpeg
            entries.append(entry)
        return sorted(entries, key=lambda e: e.get("started_at", ""), reverse=True)

    def delete(self, run_id: str) -> None:
        run_dir = self._dir(run_id)
        if run_id in self._live:
            raise RunBusyError("This run is still going; wait for it to finish")
        shutil.rmtree(run_dir)

    def delete_all(self) -> int:
        """Delete every finished run; a run that is going is kept. Returns how many."""
        count = 0
        if UPLOADS.exists():
            for run_dir in UPLOADS.iterdir():
                if run_dir.name in self._live or not self._ensure_meta(run_dir):
                    continue
                shutil.rmtree(run_dir)
                count += 1
        return count

    def file(self, run_id: str, name: str) -> Path:
        """The photo or a sent crop. Only plain file names inside the run folder."""
        run_dir = self._dir(run_id)
        meta = self._meta(run_dir)
        if name == "photo":
            return run_dir / meta["image"]
        if re.fullmatch(r"[0-9]+_[a-z0-9_]+\.jpg", name) and (run_dir / "sent" / name).exists():
            return run_dir / "sent" / name
        raise NotFoundError(f"File {name} not found")


run_manager = RunManager()
