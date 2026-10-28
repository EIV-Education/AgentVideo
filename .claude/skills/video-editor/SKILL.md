---
name: video-editor
description: Edit or create videos from a plain-language request (trim, join, reframe to 9:16, captions, subtitles, music, effects, title cards, slideshows, silence removal) using the agentvideo FFmpeg toolkit in this repo. Use when the user asks to cut, edit, convert, subtitle or produce a video file.
---

# Video editor

Use the functions in `agentvideo/ffmpeg_ops.py` (tested, Vietnamese-safe text) instead of hand-writing ffmpeg
commands. Call them from a short Python snippet run at the repo root:

```bash
python3 -c "from agentvideo import ffmpeg_ops as f; print(f.probe('in.mp4').summary())"
```

Workflow:
1. `probe(path).summary()` on every input; `extract_frames(path, out_dir, count)` and look at the JPEGs when content matters.
2. Plan the steps; write intermediates to `work/`, the final file to `output/`; never overwrite sources.
3. Available operations: `trim`, `concat` (transition="fade"/"dissolve"/...), `reframe` (aspect "9:16", mode crop/pad,
   blur_background), `add_text`, `write_srt` + `burn_subtitles`, `add_audio` (mix/replace), `change_speed`,
   `apply_effects` (color, fades, rotate, volume), `overlay_image`, `title_card`, `images_to_video`,
   `detect_scenes`, `detect_silence` + `remove_ranges`, `extract_audio`, `convert` (.mp4/.webm/.gif, compress).
4. Verify the final file with `probe` and a frame grab, then report path, duration and resolution.

Outside Claude Code the same toolkit runs as a standalone agent: `agentvideo "request" -w <folder>` (needs ANTHROPIC_API_KEY).
