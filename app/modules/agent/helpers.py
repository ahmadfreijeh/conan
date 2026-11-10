"""Small helpers used only by the agent. Generic box and image helpers live in
app/modules/engine/helpers.py."""

import io
import json

from PIL import Image

from app.modules.agent.constants import IMAGE_MAX_SIDE


def format_call(tool: str, args: dict) -> str:
    """e.g. find_place(name="Place Saint-Georges", city="Paris")"""
    text = ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())
    return f"{tool}({text})"


def image_bytes(image: Image.Image) -> bytes:
    """JPEG bytes of the image, downscaled to IMAGE_MAX_SIDE, ready to send to the LLM."""
    copy = image.convert("RGB")
    copy.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    copy.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
