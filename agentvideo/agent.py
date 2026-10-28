"""The video agent: Claude plans the edit and calls the ffmpeg tools until the job is done."""

from __future__ import annotations

import os
from typing import Callable

import anthropic

from .tools import Workspace, build_tools

DEFAULT_MODEL = os.environ.get("AGENTVIDEO_MODEL", "claude-opus-5-5")

SYSTEM_PROMPT = """You are AgentVideo, a professional video editor and producer. You turn a plain-language
request into a finished video file by calling the editing tools. All files live in one workspace directory;
always use workspace-relative paths.

How to work:
1. Inspect first: list_files, then probe_media on every input you will use. Use view_frames when the
   content of the footage matters (choosing cuts, checking text placement, verifying the result).
2. Plan briefly: state the steps (cuts, order, text, audio, format) in one short list before editing.
3. Edit step by step. Write intermediate files under work/ (e.g. work/01_trim.mp4) and the final file under
   output/ with a descriptive name. Never overwrite the user's source files.
4. Verify: probe_media the final file (duration, resolution, audio) and view_frames on it at least once.
   Fix anything wrong before finishing.
5. Finish with a short report in the user's language: the output path, duration, resolution, and what was done.

Creating videos from a description (no footage): build scenes with create_title_card (and images_to_video
when the user supplies images), add text overlays, join them with concat_videos using transitions, and add
music if an audio file is available. You cannot generate photorealistic footage or speech; say so and offer
what you can build instead.

Defaults when the request doesn't say: vertical 1080x1920 (9:16) for TikTok/Reels/Shorts, 1920x1080 (16:9)
for YouTube/lessons; background music at volume 0.25-0.3 under speech; on-screen text short, high contrast,
and kept clear of the bottom 15% on vertical video where platform UI sits. Vietnamese text is supported.
If an instruction is genuinely ambiguous and a wrong guess would waste a long render, ask one concise
question; otherwise pick a sensible default and mention it in the report."""


def run_agent(
    request: str,
    workdir: str,
    *,
    model: str = DEFAULT_MODEL,
    effort: str = "high",
    max_turns: int = 60,
    history: list | None = None,
    on_event: Callable[[str, str], None] | None = None,
    client: anthropic.Anthropic | None = None,
) -> tuple[str, list]:
    """Run one user request to completion. Returns (final_text, message_history).

    Pass the returned history back in to continue the same conversation ("now make it shorter").
    """
    client = client or anthropic.Anthropic()
    emit = on_event or (lambda kind, text: None)
    ws = Workspace(workdir)
    tools = build_tools(ws)
    by_name = {t.name: t for t in tools}
    messages = list(history or [])
    messages.append({"role": "user", "content": request})

    final_text = ""
    for _ in range(max_turns):
        with client.beta.messages.stream(
            model=model,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            tools=[t.to_dict() for t in tools],
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()

        # Keep the assistant turn exactly as returned (thinking blocks included).
        messages.append({"role": "assistant", "content": response.content})
        texts = [b.text for b in response.content if b.type == "text" and b.text.strip()]
        for t in texts:
            emit("text", t)
        if texts:
            final_text = texts[-1]

        if response.stop_reason == "refusal":
            emit("error", "The model declined this request.")
            break
        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason == "max_tokens":
            messages.append({"role": "user", "content": "Your last reply was cut off. Continue from where you stopped."})
            continue
        if response.stop_reason != "tool_use":
            break

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            emit("tool", f"{block.name}({_short(block.input)})")
            tool = by_name.get(block.name)
            try:
                if tool is None:
                    raise ValueError(f"unknown tool {block.name}")
                content = tool.call(block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
                if isinstance(content, str):
                    emit("result", content[:300])
            except Exception as exc:  # report tool failures back to the model so it can recover
                emit("error", f"{block.name}: {exc}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(exc), "is_error": True})
        # All results for one assistant turn go back in a single user message.
        messages.append({"role": "user", "content": results})
    else:
        emit("error", f"Stopped after {max_turns} turns.")

    return final_text, messages


def _short(data: dict, limit: int = 160) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in data.items())
    return s if len(s) <= limit else s[: limit - 1] + "…"
