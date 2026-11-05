"""CLI: uv run python -m app.modules.engine samples/photo.jpeg [--out out/photo] [--no-detect]
[--no-ocr] [--reason] [--from-payload out/photo/payload.json]"""

import argparse
import sys
from pathlib import Path

from app.modules.engine.loader import ImageLoadError
from app.modules.engine.payload import ExtractionPayload
from app.modules.engine.pipeline import run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.modules.engine", description=__doc__)
    parser.add_argument("image", help="path to a JPEG, PNG or WebP file")
    parser.add_argument("--out", help="folder to save payload.json, reason.json and crops/")
    parser.add_argument(
        "--no-detect", action="store_true", help="skip object detection (fast, no model)"
    )
    parser.add_argument("--no-ocr", action="store_true", help="skip text reading (fast, no model)")
    parser.add_argument(
        "--reason", action="store_true", help="after extraction, ask the LLM where the photo is"
    )
    parser.add_argument(
        "--from-payload",
        help="reuse a saved payload.json instead of running Phase 1 again (use with --reason)",
    )
    args = parser.parse_args(argv)

    try:
        if args.from_payload:
            payload = ExtractionPayload.model_validate_json(Path(args.from_payload).read_text())
        else:
            payload = run_pipeline(
                args.image, out_dir=args.out, detect=not args.no_detect, ocr=not args.no_ocr
            )
    except ImageLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not args.reason:
        print(payload.model_dump_json(indent=2))
        if args.out:
            print(f"saved to {args.out}/payload.json", file=sys.stderr)
        return 0

    import anthropic

    from app.modules.agent.reason import reason  # only import Strands when needed

    try:
        result = reason(payload, out_dir=args.out)
    except (RuntimeError, anthropic.APIError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out = result.model_dump_json(indent=2)
    print(out)
    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)
        (Path(args.out) / "reason.json").write_text(out)
        print(f"saved to {args.out}/reason.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
