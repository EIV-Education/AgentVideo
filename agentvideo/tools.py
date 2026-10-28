"""Claude tool definitions wrapping ffmpeg_ops.

All paths the model passes are resolved inside a single workspace directory,
so the agent can never read or write outside it.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from anthropic import beta_tool

from . import ffmpeg_ops as ops

MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".gif", ".mp3", ".wav", ".m4a", ".aac",
             ".png", ".jpg", ".jpeg", ".webp", ".srt", ".ass", ".txt", ".json"}


class Workspace:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, rel: str, must_exist: bool = False) -> str:
        p = Path(rel)
        p = (p if p.is_absolute() else self.root / p).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError(f"path '{rel}' is outside the workspace {self.root}")
        if must_exist and not p.exists():
            raise FileNotFoundError(f"'{rel}' does not exist in the workspace (use list_files)")
        return str(p)

    def rel(self, path: str) -> str:
        return str(Path(path).resolve().relative_to(self.root))

    def done(self, path: str) -> str:
        """Standard success message: relative path + probe summary."""
        rel = self.rel(path)
        try:
            return json.dumps({"output": rel, **ops.probe(path).summary()}, ensure_ascii=False)
        except ops.FFmpegError:
            return json.dumps({"output": rel}, ensure_ascii=False)


def build_tools(ws: Workspace) -> list:
    P = ws.path

    @beta_tool
    def list_files(subdir: str = ".") -> str:
        """List media files in the workspace (videos, audio, images, subtitles) with size.

        Args:
            subdir: Sub-directory relative to the workspace root.
        """
        base = Path(P(subdir, must_exist=True))
        rows = []
        for f in sorted(base.rglob("*")):
            if f.is_file() and f.suffix.lower() in MEDIA_EXT and ".agentvideo" not in f.parts:
                rows.append(f"{ws.rel(str(f))}\t{f.stat().st_size / 1_048_576:.2f} MB")
        return "\n".join(rows) or "(no media files)"

    @beta_tool
    def probe_media(path: str) -> str:
        """Get duration, resolution, fps and whether a file has video/audio. Call this before editing a file.

        Args:
            path: File path relative to the workspace.
        """
        return json.dumps(ops.probe(P(path, must_exist=True)).summary())

    @beta_tool
    def view_frames(path: str, count: int = 4) -> list:
        """Look at evenly spaced frames of a video (returned as images) to understand or verify its content.

        Args:
            path: Video path relative to the workspace.
            count: Number of frames, 1-8.
        """
        count = max(1, min(int(count), 8))
        frames = ops.extract_frames(P(path, must_exist=True), P(".agentvideo/frames"), count=count, width=512)
        content: list = []
        for f in frames:
            content.append({"type": "text", "text": Path(f).name})
            data = base64.standard_b64encode(Path(f).read_bytes()).decode()
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        return content

    @beta_tool
    def trim_video(input: str, output: str, start: float, end: float | None = None) -> str:
        """Cut a clip from start to end seconds (re-encoded, frame accurate).

        Args:
            input: Source video.
            output: Output path (.mp4).
            start: Start time in seconds.
            end: End time in seconds; omit to keep until the end.
        """
        return ws.done(ops.trim(P(input, True), P(output), start, end))

    @beta_tool
    def concat_videos(inputs: list[str], output: str, transition: str = "none", transition_duration: float = 0.5,
                      width: int | None = None, height: int | None = None) -> str:
        """Join videos in order. Clips of different sizes/fps are normalised automatically (letterboxed).

        Args:
            inputs: Ordered list of video paths (2 or more).
            output: Output path (.mp4).
            transition: 'none' or an xfade transition: fade, dissolve, wipeleft, wiperight, slideleft, slideup, circleopen, fadeblack.
            transition_duration: Transition length in seconds.
            width: Output width; defaults to the first clip's width.
            height: Output height; defaults to the first clip's height.
        """
        return ws.done(ops.concat([P(i, True) for i in inputs], P(output), transition, transition_duration, width, height))

    @beta_tool
    def reframe_video(input: str, output: str, aspect: str = "9:16", mode: str = "crop", width: int | None = None,
                      blur_background: bool = False) -> str:
        """Change aspect ratio, e.g. landscape to vertical for TikTok/Reels/Shorts.

        Args:
            input: Source video.
            output: Output path (.mp4).
            aspect: One of 16:9, 9:16, 1:1, 4:5, 4:3.
            mode: 'crop' fills the frame by centre-cropping; 'pad' keeps the whole picture with bars.
            width: Optional output width (height follows the aspect). Default is 1080/1920 class.
            blur_background: With mode='pad', fill bars with a blurred copy instead of black.
        """
        return ws.done(ops.reframe(P(input, True), P(output), aspect, mode, width, blur_background))

    @beta_tool
    def add_text_overlay(input: str, output: str, text: str, position: str = "bottom", fontsize: int = 56,
                         color: str = "white", box: bool = True, start: float | None = None, end: float | None = None) -> str:
        """Draw a caption/title on the video (Vietnamese diacritics supported). Use \\n for line breaks; keep lines short.

        Args:
            input: Source video.
            output: Output path (.mp4).
            text: Text to show.
            position: top, center, bottom, top-left, top-right, bottom-left, bottom-right.
            fontsize: Font size in pixels (about 4-6% of video height reads well).
            color: Colour name or hex like #FFD700.
            box: Semi-transparent dark box behind the text.
            start: Show from this second (omit = from start).
            end: Hide after this second (omit = until end).
        """
        return ws.done(ops.add_text(P(input, True), P(output), text, position, fontsize, color, box, start, end))

    @beta_tool
    def write_subtitles(segments: list[dict], output: str) -> str:
        """Write an .srt subtitle file from timed segments.

        Args:
            segments: List of {"start": seconds, "end": seconds, "text": "..."}.
            output: Output path ending in .srt.
        """
        return json.dumps({"output": ws.rel(ops.write_srt(segments, P(output))), "segments": len(segments)})

    @beta_tool
    def burn_subtitles(input: str, subtitle_file: str, output: str, fontsize: int = 22) -> str:
        """Hard-burn an .srt/.ass subtitle file into the video.

        Args:
            input: Source video.
            subtitle_file: .srt or .ass file in the workspace.
            output: Output path (.mp4).
            fontsize: Subtitle font size (libass units, 18-28 typical).
        """
        return ws.done(ops.burn_subtitles(P(input, True), P(subtitle_file, True), P(output), fontsize))

    @beta_tool
    def add_audio_track(input: str, audio: str, output: str, mode: str = "mix", audio_volume: float = 0.3,
                        original_volume: float = 1.0, fade_out: float = 2.0) -> str:
        """Add background music or a voice-over. The audio is looped/trimmed to the video length.

        Args:
            input: Source video.
            audio: Audio file (mp3/wav/m4a) in the workspace.
            output: Output path (.mp4).
            mode: 'mix' keeps the original sound under the new audio; 'replace' drops it.
            audio_volume: Volume of the added audio (0.2-0.4 for background music, 1.0 for voice-over).
            original_volume: Volume of the original sound when mixing.
            fade_out: Seconds of fade-out at the end.
        """
        return ws.done(ops.add_audio(P(input, True), P(audio, True), P(output), mode, audio_volume, original_volume, True, fade_out))

    @beta_tool
    def change_speed(input: str, output: str, factor: float) -> str:
        """Speed up (factor > 1) or slow down (factor < 1) video and audio together.

        Args:
            input: Source video.
            output: Output path (.mp4).
            factor: Speed multiplier, 0.1-10. 2.0 = twice as fast.
        """
        return ws.done(ops.change_speed(P(input, True), P(output), factor))

    @beta_tool
    def apply_effects(input: str, output: str, brightness: float = 0.0, contrast: float = 1.0, saturation: float = 1.0,
                      grayscale: bool = False, fade_in: float = 0.0, fade_out: float = 0.0, rotate: int = 0,
                      hflip: bool = False, volume: float = 1.0, denoise: bool = False, sharpen: bool = False) -> str:
        """Colour correction, fades, rotation, mirroring and volume in one pass.

        Args:
            input: Source video.
            output: Output path (.mp4).
            brightness: -1.0 to 1.0 (0.05 is a gentle lift).
            contrast: 0.5-2.0, 1.0 = unchanged.
            saturation: 0-3, 1.0 = unchanged.
            grayscale: Black and white.
            fade_in: Seconds of fade from black (video and audio).
            fade_out: Seconds of fade to black at the end.
            rotate: 0, 90, 180 or 270 degrees clockwise.
            hflip: Mirror horizontally.
            volume: Audio gain multiplier.
            denoise: Light video denoise.
            sharpen: Light sharpening.
        """
        return ws.done(ops.apply_effects(P(input, True), P(output), brightness, contrast, saturation, grayscale,
                                         fade_in, fade_out, rotate, hflip, volume, denoise, sharpen))

    @beta_tool
    def overlay_image(input: str, image: str, output: str, position: str = "top-right", scale_width: int = 200,
                      opacity: float = 1.0, start: float | None = None, end: float | None = None) -> str:
        """Put a logo/watermark/picture on top of the video.

        Args:
            input: Source video.
            image: PNG/JPG in the workspace.
            output: Output path (.mp4).
            position: top-left, top-right, bottom-left, bottom-right, center.
            scale_width: Width of the overlay in pixels.
            opacity: 0-1.
            start: Show from this second.
            end: Hide after this second.
        """
        return ws.done(ops.overlay_image(P(input, True), P(image, True), P(output), position, scale_width, opacity, start, end))

    @beta_tool
    def create_title_card(output: str, text: str, duration: float = 3.0, aspect: str = "16:9", background: str = "#1e3a8a",
                          fontsize: int = 80, color: str = "white", subtitle: str | None = None) -> str:
        """Generate a solid-colour clip with centred text: intro, outro, chapter or scene card.

        Args:
            output: Output path (.mp4).
            text: Main title (use \\n to break long titles).
            duration: Seconds.
            aspect: 16:9, 9:16, 1:1, 4:5, 4:3.
            background: Background colour (hex or name).
            fontsize: Title font size.
            color: Text colour.
            subtitle: Optional smaller second line.
        """
        return ws.done(ops.title_card(P(output), text, duration, aspect, background, fontsize, color, subtitle))

    @beta_tool
    def images_to_video(images: list[str], output: str, seconds_per_image: float = 3.0, aspect: str = "16:9",
                        ken_burns: bool = True, transition: str = "fade") -> str:
        """Build a slideshow video from images, with slow zoom and transitions.

        Args:
            images: Ordered image paths.
            output: Output path (.mp4).
            seconds_per_image: Duration of each image.
            aspect: 16:9, 9:16, 1:1, 4:5, 4:3.
            ken_burns: Slow zoom-in on each image.
            transition: 'none' or an xfade transition name (fade, dissolve, slideleft...).
        """
        return ws.done(ops.images_to_video([P(i, True) for i in images], P(output), seconds_per_image, aspect, ken_burns, transition))

    @beta_tool
    def extract_audio(input: str, output: str) -> str:
        """Save the audio track as .mp3, .wav or .m4a.

        Args:
            input: Source video.
            output: Output audio path.
        """
        return ws.done(ops.extract_audio(P(input, True), P(output)))

    @beta_tool
    def detect_scenes(input: str, threshold: float = 0.3) -> str:
        """Find timestamps where the shot changes, to plan cuts.

        Args:
            input: Source video.
            threshold: 0.1 (sensitive) to 0.6 (only hard cuts).
        """
        return json.dumps({"scene_changes_sec": ops.detect_scenes(P(input, True), threshold)})

    @beta_tool
    def detect_silence(input: str, noise_db: float = -35, min_duration: float = 0.6) -> str:
        """Find silent ranges (dead air, pauses) in the audio.

        Args:
            input: Source video or audio.
            noise_db: Level below which audio counts as silence, e.g. -35.
            min_duration: Minimum silence length in seconds.
        """
        return json.dumps({"silences": ops.detect_silence(P(input, True), noise_db, min_duration)})

    @beta_tool
    def remove_segments(input: str, output: str, ranges: list[dict], padding: float = 0.1) -> str:
        """Cut out time ranges (e.g. silences from detect_silence, mistakes) and join the rest.

        Args:
            input: Source video.
            output: Output path (.mp4).
            ranges: List of {"start": seconds, "end": seconds} to remove.
            padding: Seconds kept at each edge of a removed range so speech isn't clipped.
        """
        return ws.done(ops.remove_ranges(P(input, True), P(output), ranges, padding))

    @beta_tool
    def convert_or_compress(input: str, output: str, crf: int = 23, max_width: int | None = None, fps: int | None = None) -> str:
        """Re-encode to another format (.mp4/.webm/.mov/.gif) or shrink file size.

        Args:
            input: Source video.
            output: Output path; its extension picks the format.
            crf: Quality, 18 (high) to 32 (small). Ignored for GIF.
            max_width: Downscale to at most this width.
            fps: Change frame rate (GIF default 12).
        """
        return ws.done(ops.convert(P(input, True), P(output), crf, max_width, fps))

    @beta_tool
    def run_ffmpeg(args: list[str]) -> str:
        """Escape hatch: run ffmpeg with raw arguments (no shell) when no other tool fits.
        Every file argument must be a workspace-relative path; the command runs inside the workspace.

        Args:
            args: ffmpeg arguments without the leading 'ffmpeg', e.g. ["-i", "in.mp4", "-vf", "vflip", "out.mp4"].
        """
        if any("://" in str(a) for a in args):
            raise ValueError("network inputs/outputs are not allowed")
        for a in args:
            s = str(a)
            if s.startswith("/") or ".." in Path(s).parts:
                P(s)  # raises if outside the workspace
        ops.ffmpeg(*map(str, args), cwd=str(ws.root))
        return "ffmpeg finished. Use probe_media/view_frames to check the result."

    return [
        list_files, probe_media, view_frames, trim_video, concat_videos, reframe_video, add_text_overlay,
        write_subtitles, burn_subtitles, add_audio_track, change_speed, apply_effects, overlay_image,
        create_title_card, images_to_video, extract_audio, detect_scenes, detect_silence, remove_segments,
        convert_or_compress, run_ffmpeg,
    ]
