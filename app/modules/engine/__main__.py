"""CLI: uv run python -m app.modules.engine samples/photo.jpeg [--out out/photo] [--no-detect]
[--no-ocr] [--reason] [--from-payload out/photo/payload.json] [--tools find_place,wiki_nearby]"""

import argparse
import sys
from pathlib import Path

from app.modules.engine.extractors.loader import ImageLoadError
from app.modules.engine.pipeline import run_pipeline
from app.modules.engine.schemas import ExtractionPayload


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
    parser.add_argument(
        "--tools",
        help='tools the agent gets, comma-separated; "" = none (default: AGENT_TOOLS, else all)',
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

    from app.modules.agent.reason import default_approve, reason  # import Strands when needed
    from app.modules.engine.prepare import prepare_for_agent, save_agent_input

    try:
        agent_input = prepare_for_agent(payload)
        if args.out:
            save_agent_input(agent_input, args.out)  # clues.json + sent/, to check the input
        tool_names = (
            None if args.tools is None else [n.strip() for n in args.tools.split(",") if n.strip()]
        )
        result = reason(agent_input, approve=default_approve(), tool_names=tool_names)
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
