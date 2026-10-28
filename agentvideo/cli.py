"""Command line entry point.

    agentvideo "Cắt 10 giây đầu của intro.mp4, chuyển sang 9:16, thêm chữ 'EIV Education'" -w ./project
    agentvideo -w ./project            # interactive chat mode
"""

from __future__ import annotations

import argparse
import shutil
import sys

from .agent import DEFAULT_MODEL, run_agent

COLORS = {"tool": "\033[36m", "result": "\033[2m", "error": "\033[31m", "text": ""}


def _printer(kind: str, text: str) -> None:
    prefix = {"tool": "▶ ", "result": "  ↳ ", "error": "✖ ", "text": ""}[kind]
    color = COLORS[kind] if sys.stdout.isatty() else ""
    reset = "\033[0m" if color else ""
    print(f"{color}{prefix}{text}{reset}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentvideo", description="AI agent that edits and creates videos from a description.")
    parser.add_argument("request", nargs="?", help="What to do, in any language. Omit for interactive mode.")
    parser.add_argument("-w", "--workdir", default=".", help="Workspace folder holding source media (default: current dir).")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Claude model (default: {DEFAULT_MODEL}).")
    parser.add_argument("-e", "--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--max-turns", type=int, default=60)
    args = parser.parse_args(argv)

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg/ffprobe not found. Install FFmpeg first (https://ffmpeg.org/download.html).", file=sys.stderr)
        return 1

    history: list = []
    request = args.request
    interactive = request is None
    while True:
        if interactive:
            try:
                request = input("\n🎬 Yêu cầu (Enter trống để thoát) > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not request:
                return 0
        _, history = run_agent(request, args.workdir, model=args.model, effort=args.effort,
                               max_turns=args.max_turns, history=history, on_event=_printer)
        if not interactive:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
