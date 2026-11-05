"""Performance and environment info recorded with every run.

Shows how long each step took, CPU/GPU/memory use, the machine, library versions and
the settings used, so runs can be compared. GPU utilization % isn't readable on macOS
without sudo (powermetrics), so for the GPU we record the device and memory used.
"""

import json
import os
import platform
import resource
import subprocess
import sys
import time
from datetime import datetime
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.modules.engine.constants import TRACKED_PACKAGES
from app.modules.engine.helpers import split_csv


class RunMeter:
    """Start at the beginning of a run; call finish() at the end to get the run section."""

    def __init__(self) -> None:
        self.started_at = datetime.now().astimezone()
        self._wall = time.perf_counter()
        self._cpu = time.process_time()  # user + system CPU time of this process, all threads

    def finish(self, timings: dict[str, float], gpu_device: str | None) -> dict[str, Any]:
        wall_s = time.perf_counter() - self._wall
        cpu_s = time.process_time() - self._cpu
        return {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "total_ms": round(wall_s * 1000, 2),
            "timings": timings,
            "cpu": {
                "cpu_time_s": round(cpu_s, 2),
                # >100% means several cores worked at once
                "cpu_usage_pct": round(cpu_s / wall_s * 100, 1) if wall_s > 0 else None,
            },
            "gpu": _gpu_info(gpu_device),
            "memory": {"peak_mb": _peak_memory_mb()},
            "machine": machine_info(),
            "versions": versions(),
            "settings": engine_settings(),
        }


def append_history(log_path: Path, payload_dict: dict[str, Any]) -> None:
    """Add one compact line per run to a JSONL file, to compare runs over time."""
    run = payload_dict["run"]
    line = {
        "started_at": run["started_at"],
        "image": payload_dict["image"]["path"],
        "sha256": payload_dict["image"]["sha256"][:12],
        "size": [payload_dict["image"]["width"], payload_dict["image"]["height"]],
        "objects": len(payload_dict["objects"]),
        "text_lines": len(payload_dict["text"]),
        "total_ms": run["total_ms"],
        "timings": run["timings"],
        "cpu": run["cpu"],
        "gpu": run["gpu"],
        "peak_mb": run["memory"]["peak_mb"],
        "settings": run["settings"],
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _gpu_info(device: str | None) -> dict[str, Any]:
    info: dict[str, Any] = {"device": device or "none"}
    torch = sys.modules.get("torch")  # only if a model already imported it
    if device == "mps" and torch is not None:
        try:
            info["memory_mb"] = round(torch.mps.driver_allocated_memory() / 1e6, 1)
        except Exception:  # older torch builds
            info["memory_mb"] = None
    info["ocr_device"] = "cpu"  # PaddlePaddle has no Mac GPU support
    return info


def _peak_memory_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kilobytes
    return round(peak / 1e6 if sys.platform == "darwin" else peak / 1e3, 1)


@lru_cache(maxsize=1)
def machine_info() -> dict[str, Any]:
    chip = platform.processor() or platform.machine()
    ram_gb = None
    if sys.platform == "darwin":
        chip = _sysctl("machdep.cpu.brand_string") or chip
        mem = _sysctl("hw.memsize")
        ram_gb = round(int(mem) / 1024**3, 1) if mem else None
        os_name = f"macOS {platform.mac_ver()[0]}"
    else:
        try:
            ram_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
        except (ValueError, OSError, AttributeError):
            pass
        os_name = platform.platform()
    return {"chip": chip, "cores": os.cpu_count(), "ram_gb": ram_gb, "os": os_name}


@lru_cache(maxsize=1)
def versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {"python": platform.python_version()}
    for pkg in TRACKED_PACKAGES:
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def engine_settings() -> dict[str, Any]:
    s = get_settings()
    return {
        "engine_device": s.engine_device,
        "detect_box_threshold": s.detect_box_threshold,
        "detect_text_threshold": s.detect_text_threshold,
        "detect_groups": s.detect_groups,
        "detect_batch_size": s.detect_batch_size,
        "ocr_det_model": s.ocr_det_model,
        "ocr_rec_models": split_csv(s.ocr_rec_models),
        "ocr_min_score": s.ocr_min_score,
    }


def _sysctl(name: str) -> str | None:
    try:
        return subprocess.run(
            ["sysctl", "-n", name], capture_output=True, text=True, timeout=2, check=True
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
