"""Pure FFmpeg operations used by the agent's tools.

Every function takes absolute paths, runs ffmpeg/ffprobe without a shell and
returns the output path (or data). Nothing here talks to an LLM, so the module
can be used and tested on its own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

FFMPEG = os.environ.get("AGENTVIDEO_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("AGENTVIDEO_FFPROBE", "ffprobe")

DEFAULT_FPS = 30
ASPECTS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "4:3": (1440, 1080)}
POSITIONS = {
    "top": ("(w-text_w)/2", "h*0.08"),
    "center": ("(w-text_w)/2", "(h-text_h)/2"),
    "bottom": ("(w-text_w)/2", "h*0.85-text_h"),
    "top-left": ("w*0.05", "h*0.05"),
    "top-right": ("w*0.95-text_w", "h*0.05"),
    "bottom-left": ("w*0.05", "h*0.92-text_h"),
    "bottom-right": ("w*0.95-text_w", "h*0.92-text_h"),
}
OVERLAY_POSITIONS = {
    "top-left": ("20", "20"),
    "top-right": ("W-w-20", "20"),
    "bottom-left": ("20", "H-h-20"),
    "bottom-right": ("W-w-20", "H-h-20"),
    "center": ("(W-w)/2", "(H-h)/2"),
}
# Fonts with Vietnamese glyph coverage, tried in order.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


class FFmpegError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    duration: float
    width: int | None
    height: int | None
    fps: float | None
    has_video: bool
    has_audio: bool
    size_bytes: int
    raw: dict

    def summary(self) -> dict:
        return {
            "duration_sec": round(self.duration, 3),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 3) if self.fps else None,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "size_mb": round(self.size_bytes / 1_048_576, 2),
        }


def _run(args: list[str], timeout: int = 1800) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"{Path(args[0]).name} failed (exit {proc.returncode}):\n{tail}")
    return proc.stdout


def ffmpeg(*args: str) -> None:
    _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args])


def _prepare_output(output: str) -> str:
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    return output


def find_font() -> str | None:
    env = os.environ.get("AGENTVIDEO_FONT")
    if env and Path(env).exists():
        return env
    for cand in FONT_CANDIDATES:
        if Path(cand).exists():
            return cand
    if shutil.which("fc-match"):
        try:
            path = _run(["fc-match", "-f", "%{file}", "DejaVu Sans:bold"]).strip()
            if path and Path(path).exists():
                return path
        except FFmpegError:
            pass
    return None


def _font_opt() -> str:
    font = find_font()
    return f"fontfile='{_esc_filter_path(font)}':" if font else ""


def _esc_filter_path(path: str) -> str:
    # Escaping for a path used inside a filtergraph option value.
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _text_file(text: str) -> str:
    fd, path = tempfile.mkstemp(prefix="agentvideo_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _parse_fps(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1)


def probe(path: str) -> MediaInfo:
    out = _run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    return MediaInfo(
        duration=float(fmt.get("duration") or (video or {}).get("duration") or 0),
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        fps=_parse_fps(video.get("avg_frame_rate")) if video else None,
        has_video=video is not None,
        has_audio=audio is not None,
        size_bytes=int(fmt.get("size") or Path(path).stat().st_size),
        raw=data,
    )


ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]


def trim(input: str, output: str, start: float, end: float | None = None) -> str:
    args = ["-ss", str(start), "-i", input]
    if end is not None:
        if end <= start:
            raise ValueError("end must be greater than start")
        args += ["-t", str(end - start)]
    ffmpeg(*args, *ENCODE, _prepare_output(output))
    return output


def normalize(input: str, output: str, width: int, height: int, fps: int = DEFAULT_FPS) -> str:
    """Re-encode to a fixed canvas (letterboxed), fps and always-present stereo audio."""
    info = probe(input)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p"
    )
    if info.has_audio:
        ffmpeg("-i", input, "-vf", vf, "-af", "aresample=48000,aformat=channel_layouts=stereo", *ENCODE, _prepare_output(output))
    else:
        ffmpeg(
            "-i", input, "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf", vf, "-shortest", *ENCODE, _prepare_output(output),
        )
    return output


def concat(inputs: list[str], output: str, transition: str = "none", transition_duration: float = 0.5,
           width: int | None = None, height: int | None = None) -> str:
    """Join clips in order. transition: 'none' or any xfade name (fade, dissolve, wipeleft, slideup, ...)."""
    if len(inputs) < 2:
        raise ValueError("concat needs at least 2 inputs")
    first = probe(inputs[0])
    width = width or first.width or 1920
    height = height or first.height or 1080
    width, height = width - width % 2, height - height % 2
    with tempfile.TemporaryDirectory(prefix="agentvideo_concat_") as tmp:
        normed = [normalize(p, os.path.join(tmp, f"{i}.mp4"), width, height) for i, p in enumerate(inputs)]
        args: list[str] = []
        for p in normed:
            args += ["-i", p]
        n = len(normed)
        if transition == "none":
            streams = "".join(f"[{i}:v][{i}:a]" for i in range(n))
            graph = f"{streams}concat=n={n}:v=1:a=1[v][a]"
        else:
            durations = [probe(p).duration for p in normed]
            d = transition_duration
            if any(dur <= d for dur in durations):
                raise ValueError("every clip must be longer than transition_duration")
            parts, offset, vprev, aprev = [], 0.0, "[0:v]", "[0:a]"
            for i in range(1, n):
                offset += durations[i - 1] - d
                vout, aout = f"[v{i}]", f"[a{i}]"
                parts.append(f"{vprev}[{i}:v]xfade=transition={transition}:duration={d}:offset={offset:.3f}{vout}")
                parts.append(f"{aprev}[{i}:a]acrossfade=d={d}{aout}")
                vprev, aprev = vout, aout
            graph = ";".join(parts).replace(f"[v{n-1}]", "[v]").replace(f"[a{n-1}]", "[a]")
        ffmpeg(*args, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", *ENCODE, _prepare_output(output))
    return output


def reframe(input: str, output: str, aspect: str = "9:16", mode: str = "crop", width: int | None = None,
            blur_background: bool = False) -> str:
    """Change aspect ratio. mode='crop' fills the frame (center crop), 'pad' letterboxes."""
    if aspect not in ASPECTS:
        raise ValueError(f"aspect must be one of {list(ASPECTS)}")
    w, h = ASPECTS[aspect]
    if width:
        h = int(round(width * h / w / 2) * 2)
        w = width - width % 2
    if mode == "crop":
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
        ffmpeg("-i", input, "-vf", vf, *ENCODE, _prepare_output(output))
    elif mode == "pad" and blur_background:
        graph = (
            f"[0:v]split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20:5[bg];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )
        ffmpeg("-i", input, "-filter_complex", graph, "-map", "[v]", "-map", "0:a?", *ENCODE, _prepare_output(output))
    elif mode == "pad":
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        ffmpeg("-i", input, "-vf", vf, *ENCODE, _prepare_output(output))
    else:
        raise ValueError("mode must be 'crop' or 'pad'")
    return output


def _drawtext(text: str, position: str, fontsize: int, color: str, box: bool,
              start: float | None, end: float | None, fade: float = 0.0) -> tuple[str, str]:
    if position not in POSITIONS:
        raise ValueError(f"position must be one of {list(POSITIONS)}")
    x, y = POSITIONS[position]
    tf = _text_file(text)
    opts = f"drawtext={_font_opt()}textfile='{_esc_filter_path(tf)}':fontsize={fontsize}:fontcolor={color}:x={x}:y={y}:line_spacing=8"
    opts += ":borderw=2:bordercolor=black@0.6"
    if box:
        opts += ":box=1:boxcolor=black@0.5:boxborderw=18"
    if start is not None or end is not None:
        s = start or 0
        e = end if end is not None else 1e9
        opts += f":enable='between(t,{s},{e})'"
        if fade > 0 and end is not None:
            opts += f":alpha='if(lt(t,{s}+{fade}),(t-{s})/{fade},if(gt(t,{e}-{fade}),({e}-t)/{fade},1))'"
    return opts, tf


def add_text(input: str, output: str, text: str, position: str = "bottom", fontsize: int = 56,
             color: str = "white", box: bool = True, start: float | None = None, end: float | None = None) -> str:
    vf, tf = _drawtext(text, position, fontsize, color, box, start, end, fade=0.3)
    try:
        ffmpeg("-i", input, "-vf", vf, *ENCODE, _prepare_output(output))
    finally:
        os.unlink(tf)
    return output


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_srt(segments: list[dict], output: str) -> str:
    """segments: [{"start": 0.0, "end": 2.5, "text": "..."}]"""
    lines = []
    for i, seg in enumerate(sorted(segments, key=lambda s: float(s["start"])), start=1):
        lines += [str(i), f"{_srt_time(float(seg['start']))} --> {_srt_time(float(seg['end']))}", str(seg["text"]).strip(), ""]
    Path(_prepare_output(output)).write_text("\n".join(lines), encoding="utf-8")
    return output


def burn_subtitles(input: str, subtitle_file: str, output: str, fontsize: int = 22, margin_v: int = 40) -> str:
    style = f"FontName=DejaVu Sans,FontSize={fontsize},Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=1,Outline=2,Shadow=0,MarginV={margin_v}"
    vf = f"subtitles='{_esc_filter_path(subtitle_file)}':force_style='{style}'"
    font = find_font()
    if font:
        vf += f":fontsdir='{_esc_filter_path(str(Path(font).parent))}'"
    ffmpeg("-i", input, "-vf", vf, *ENCODE, _prepare_output(output))
    return output


def add_audio(input: str, audio: str, output: str, mode: str = "mix", audio_volume: float = 0.3,
              original_volume: float = 1.0, loop: bool = True, fade_out: float = 2.0) -> str:
    """Add background music / voice-over. mode='mix' keeps original sound, 'replace' drops it."""
    info = probe(input)
    dur = info.duration
    loop_args = ["-stream_loop", "-1"] if loop else []
    fade = f",afade=t=out:st={max(dur - fade_out, 0):.3f}:d={fade_out}" if fade_out > 0 else ""
    music = f"[1:a]volume={audio_volume},atrim=0:{dur:.3f}{fade}[m]"
    if mode == "mix" and info.has_audio:
        graph = f"{music};[0:a]volume={original_volume}[o];[o][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    elif mode in ("mix", "replace"):
        graph = music.replace("[m]", "[a]")
    else:
        raise ValueError("mode must be 'mix' or 'replace'")
    ffmpeg("-i", input, *loop_args, "-i", audio, "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.3f}", _prepare_output(output))
    return output


def _atempo_chain(factor: float) -> str:
    parts = []
    while factor > 2.0:
        parts.append("atempo=2.0")
        factor /= 2.0
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    parts.append(f"atempo={factor:.6f}")
    return ",".join(parts)


def change_speed(input: str, output: str, factor: float) -> str:
    if not 0.1 <= factor <= 10:
        raise ValueError("factor must be between 0.1 and 10")
    info = probe(input)
    if info.has_audio:
        ffmpeg("-i", input, "-filter_complex", f"[0:v]setpts=PTS/{factor}[v];[0:a]{_atempo_chain(factor)}[a]",
               "-map", "[v]", "-map", "[a]", *ENCODE, _prepare_output(output))
    else:
        ffmpeg("-i", input, "-vf", f"setpts=PTS/{factor}", *ENCODE, _prepare_output(output))
    return output


def apply_effects(input: str, output: str, brightness: float = 0.0, contrast: float = 1.0, saturation: float = 1.0,
                  grayscale: bool = False, fade_in: float = 0.0, fade_out: float = 0.0, rotate: int = 0,
                  hflip: bool = False, volume: float = 1.0, denoise: bool = False, sharpen: bool = False) -> str:
    info = probe(input)
    vf = []
    if brightness or contrast != 1.0 or saturation != 1.0:
        vf.append(f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}")
    if grayscale:
        vf.append("hue=s=0")
    if denoise:
        vf.append("hqdn3d")
    if sharpen:
        vf.append("unsharp=5:5:0.8")
    rot = {90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2"}
    if rotate:
        if rotate not in rot:
            raise ValueError("rotate must be 0, 90, 180 or 270")
        vf.append(rot[rotate])
    if hflip:
        vf.append("hflip")
    if fade_in > 0:
        vf.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        vf.append(f"fade=t=out:st={max(info.duration - fade_out, 0):.3f}:d={fade_out}")
    af = []
    if volume != 1.0:
        af.append(f"volume={volume}")
    if fade_in > 0:
        af.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0:
        af.append(f"afade=t=out:st={max(info.duration - fade_out, 0):.3f}:d={fade_out}")
    args = ["-i", input]
    if vf:
        args += ["-vf", ",".join(vf)]
    if af and info.has_audio:
        args += ["-af", ",".join(af)]
    ffmpeg(*args, *ENCODE, _prepare_output(output))
    return output


def overlay_image(input: str, image: str, output: str, position: str = "top-right", scale_width: int = 200,
                  opacity: float = 1.0, start: float | None = None, end: float | None = None) -> str:
    if position not in OVERLAY_POSITIONS:
        raise ValueError(f"position must be one of {list(OVERLAY_POSITIONS)}")
    x, y = OVERLAY_POSITIONS[position]
    enable = ""
    if start is not None or end is not None:
        enable = f":enable='between(t,{start or 0},{end if end is not None else 1e9})'"
    graph = (f"[1:v]scale={scale_width}:-1,format=rgba,colorchannelmixer=aa={opacity}[logo];"
             f"[0:v][logo]overlay={x}:{y}{enable}[v]")
    ffmpeg("-i", input, "-i", image, "-filter_complex", graph, "-map", "[v]", "-map", "0:a?", *ENCODE, _prepare_output(output))
    return output


def title_card(output: str, text: str, duration: float = 3.0, aspect: str = "16:9", background: str = "#1e3a8a",
               fontsize: int = 80, color: str = "white", subtitle: str | None = None) -> str:
    """Create a solid-colour clip with centred text (intro/outro/scene card), with silent audio."""
    if aspect not in ASPECTS:
        raise ValueError(f"aspect must be one of {list(ASPECTS)}")
    w, h = ASPECTS[aspect]
    bg = background.replace("#", "0x")
    temp_files = []
    vf_title, tf = _drawtext(text, "center", fontsize, color, False, None, None)
    temp_files.append(tf)
    vf = vf_title
    if subtitle:
        sub, tf2 = _drawtext(subtitle, "center", max(fontsize // 2, 24), color, False, None, None)
        temp_files.append(tf2)
        vf = vf.replace("y=(h-text_h)/2", "y=(h-text_h)/2-h*0.06") + "," + sub.replace("y=(h-text_h)/2", "y=h*0.58")
    vf += f",fade=t=in:st=0:d=0.4,fade=t=out:st={max(duration - 0.4, 0):.3f}:d=0.4"
    try:
        ffmpeg("-f", "lavfi", "-i", f"color=c={bg}:s={w}x{h}:r={DEFAULT_FPS}:d={duration}",
               "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
               "-vf", vf, "-t", str(duration), *ENCODE, _prepare_output(output))
    finally:
        for f in temp_files:
            os.unlink(f)
    return output


def images_to_video(images: list[str], output: str, seconds_per_image: float = 3.0, aspect: str = "16:9",
                    ken_burns: bool = True, transition: str = "fade", transition_duration: float = 0.5) -> str:
    """Slideshow from still images, optional slow zoom (Ken Burns) and transitions."""
    if not images:
        raise ValueError("images must not be empty")
    if aspect not in ASPECTS:
        raise ValueError(f"aspect must be one of {list(ASPECTS)}")
    w, h = ASPECTS[aspect]
    frames = int(seconds_per_image * DEFAULT_FPS)
    with tempfile.TemporaryDirectory(prefix="agentvideo_slides_") as tmp:
        clips = []
        for i, img in enumerate(images):
            fill = f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,crop={w*2}:{h*2}"
            if ken_burns:
                vf = f"{fill},zoompan=z='min(zoom+0.0008,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={DEFAULT_FPS}"
            else:
                vf = f"{fill},scale={w}:{h},fps={DEFAULT_FPS}"
            clip = os.path.join(tmp, f"{i}.mp4")
            ffmpeg("-loop", "1", "-i", img, "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                   "-vf", f"{vf},setsar=1,format=yuv420p", "-t", str(seconds_per_image), *ENCODE, clip)
            clips.append(clip)
        if len(clips) == 1:
            shutil.copy(clips[0], _prepare_output(output))
        else:
            concat(clips, output, transition=transition, transition_duration=transition_duration, width=w, height=h)
    return output


def extract_audio(input: str, output: str) -> str:
    ext = Path(output).suffix.lower()
    codec = {".mp3": ["-c:a", "libmp3lame", "-q:a", "2"], ".wav": ["-c:a", "pcm_s16le"], ".m4a": ["-c:a", "aac", "-b:a", "192k"]}
    if ext not in codec:
        raise ValueError("output must end with .mp3, .wav or .m4a")
    ffmpeg("-i", input, "-vn", *codec[ext], _prepare_output(output))
    return output


def extract_frames(input: str, output_dir: str, count: int = 6, width: int = 640) -> list[str]:
    """Grab `count` evenly spaced JPEG frames (for the agent to look at)."""
    info = probe(input)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(count):
        t = info.duration * (i + 0.5) / count
        out = os.path.join(output_dir, f"frame_{i:02d}_{t:06.2f}s.jpg")
        ffmpeg("-ss", f"{t:.3f}", "-i", input, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", out)
        paths.append(out)
    return paths


def detect_scenes(input: str, threshold: float = 0.3) -> list[float]:
    """Timestamps (seconds) where the picture changes sharply."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", input, "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, timeout=1800,
    )
    times = []
    for line in proc.stderr.splitlines():
        if "showinfo" in line and "pts_time:" in line:
            times.append(round(float(line.split("pts_time:")[1].split()[0]), 3))
    return times


def detect_silence(input: str, noise_db: float = -35, min_duration: float = 0.6) -> list[dict]:
    """Silent ranges, useful for cutting dead air from lectures/talking-head videos."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", input, "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}", "-f", "null", "-"],
        capture_output=True, text=True, timeout=1800,
    )
    ranges, start = [], None
    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            end = float(line.split("silence_end:")[1].split("|")[0])
            ranges.append({"start": round(max(start, 0), 3), "end": round(end, 3)})
            start = None
    return ranges


def remove_ranges(input: str, output: str, ranges: list[dict], padding: float = 0.1) -> str:
    """Cut out the given [start, end] ranges (e.g. silences) and join what's left."""
    info = probe(input)
    keep, cursor = [], 0.0
    for r in sorted(ranges, key=lambda r: r["start"]):
        s, e = float(r["start"]) + padding, float(r["end"]) - padding
        if e <= s:
            continue
        if s > cursor:
            keep.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < info.duration:
        keep.append((cursor, info.duration))
    keep = [(s, e) for s, e in keep if e - s > 0.05]
    if not keep:
        raise ValueError("nothing left after removing ranges")
    sel = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in keep)
    args = ["-i", input, "-vf", f"select='{sel}',setpts=N/FRAME_RATE/TB"]
    if info.has_audio:
        args += ["-af", f"aselect='{sel}',asetpts=N/SR/TB"]
    ffmpeg(*args, *ENCODE, _prepare_output(output))
    return output


def convert(input: str, output: str, crf: int = 23, max_width: int | None = None, fps: int | None = None) -> str:
    """Re-encode / compress, or export GIF/WebM/MP3 based on the output extension."""
    ext = Path(output).suffix.lower()
    vf = []
    if max_width:
        vf.append(f"scale='min({max_width},iw)':-2")
    if ext == ".gif":
        vf.append(f"fps={fps or 12}")
        chain = ",".join(vf)
        graph = f"[0:v]{chain + ',' if chain else ''}split[a][b];[a]palettegen[p];[b][p]paletteuse"
        ffmpeg("-i", input, "-filter_complex", graph, _prepare_output(output))
        return output
    if fps:
        vf.append(f"fps={fps}")
    args = ["-i", input] + (["-vf", ",".join(vf)] if vf else [])
    if ext == ".webm":
        args += ["-c:v", "libvpx-vp9", "-crf", str(crf + 8), "-b:v", "0", "-c:a", "libopus"]
    elif ext in (".mp4", ".mov", ".mkv"):
        args += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
    else:
        raise ValueError("output must be .mp4, .mov, .mkv, .webm or .gif")
    ffmpeg(*args, _prepare_output(output))
    return output
