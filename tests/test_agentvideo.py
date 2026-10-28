import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentvideo import ffmpeg_ops as ops
from agentvideo.agent import run_agent
from agentvideo.tools import Workspace, build_tools

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    ops.ffmpeg("-f", "lavfi", "-i", "testsrc=s=1280x720:r=25:d=4", "-f", "lavfi", "-i", "sine=f=440:d=4", *ops.ENCODE, str(d / "a.mp4"))
    ops.ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x480:r=30:d=3", *ops.ENCODE, str(d / "b.mp4"))
    ops.ffmpeg("-f", "lavfi", "-i", "sine=f=220:d=10", str(d / "music.mp3"))
    return d


def test_trim_and_probe(media, tmp_path):
    out = ops.trim(str(media / "a.mp4"), str(tmp_path / "t.mp4"), 1, 3)
    assert ops.probe(out).duration == pytest.approx(2.0, abs=0.1)


@pytest.mark.parametrize("transition", ["none", "fade"])
def test_concat_mixed_inputs(media, tmp_path, transition):
    out = ops.concat([str(media / "a.mp4"), str(media / "b.mp4")], str(tmp_path / "c.mp4"), transition=transition)
    info = ops.probe(out)
    assert (info.width, info.height, info.has_audio) == (1280, 720, True)
    expected = 7.0 if transition == "none" else 6.5
    assert info.duration == pytest.approx(expected, abs=0.15)


def test_reframe_vertical(media, tmp_path):
    out = ops.reframe(str(media / "a.mp4"), str(tmp_path / "v.mp4"), "9:16", "pad", width=720, blur_background=True)
    info = ops.probe(out)
    assert (info.width, info.height) == (720, 1280)


def test_text_subtitles_audio_speed(media, tmp_path):
    a = str(media / "a.mp4")
    ops.add_text(a, str(tmp_path / "t.mp4"), "Xin chào: tiếng Việt 'có dấu'", start=0.5, end=3)
    srt = ops.write_srt([{"start": 0, "end": 2, "text": "Câu một"}, {"start": 2, "end": 4, "text": "Câu hai"}], str(tmp_path / "s.srt"))
    assert "00:00:02,000 --> 00:00:04,000" in Path(srt).read_text(encoding="utf-8")
    ops.burn_subtitles(a, srt, str(tmp_path / "sub.mp4"))
    out = ops.add_audio(str(media / "b.mp4"), str(media / "music.mp3"), str(tmp_path / "m.mp4"))
    assert ops.probe(out).has_audio
    fast = ops.change_speed(a, str(tmp_path / "f.mp4"), 2.0)
    assert ops.probe(fast).duration == pytest.approx(2.0, abs=0.15)


def test_create_from_scratch(tmp_path):
    card = ops.title_card(str(tmp_path / "card.mp4"), "Bài 1: Thì hiện tại đơn", 2, "9:16", subtitle="EIV Education")
    info = ops.probe(card)
    assert (info.width, info.height, info.has_audio) == (1080, 1920, True)
    frames = ops.extract_frames(card, str(tmp_path / "fr"), count=2)
    slides = ops.images_to_video(frames, str(tmp_path / "slides.mp4"), 1.5, "1:1")
    assert ops.probe(slides).width == 1080


def test_silence_removal(tmp_path):
    src = str(tmp_path / "s.mp4")
    ops.ffmpeg("-f", "lavfi", "-i", "testsrc=s=320x240:d=6", "-f", "lavfi", "-i", "sine=d=6",
               "-af", "volume=enable='between(t,2,4)':volume=0", *ops.ENCODE, "-t", "6", src)
    silences = ops.detect_silence(src)
    assert len(silences) == 1
    out = ops.remove_ranges(src, str(tmp_path / "r.mp4"), silences)
    assert ops.probe(out).duration < 5


def test_workspace_blocks_escape(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError):
        ws.path("../outside.mp4")
    with pytest.raises(ValueError):
        ws.path("/etc/passwd")
    tools = {t.name: t for t in build_tools(ws)}
    with pytest.raises(ValueError):
        tools["run_ffmpeg"].call({"args": ["-i", "http://example.com/x.mp4", "o.mp4"]})


# --- agent loop, with a scripted fake client instead of the API ---------------------------------

class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return _Stream(self.replies.pop(0))


def _tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _text(t):
    return SimpleNamespace(type="text", text=t)


def test_agent_loop_runs_tools(media, tmp_path):
    shutil.copy(media / "a.mp4", tmp_path / "a.mp4")
    client = FakeClient([
        SimpleNamespace(stop_reason="tool_use", content=[
            _text("Plan: trim then title."),
            _tool_use("t1", "trim_video", {"input": "a.mp4", "output": "output/clip.mp4", "start": 0, "end": 2}),
            _tool_use("t2", "probe_media", {"path": "missing.mp4"}),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[_text("Xong: output/clip.mp4")]),
    ])
    events = []
    text, history = run_agent("cắt 2 giây đầu", str(tmp_path), client=client, on_event=lambda k, t: events.append((k, t)))
    assert text == "Xong: output/clip.mp4"
    assert (tmp_path / "output" / "clip.mp4").exists()
    results = history[2]["content"]
    assert len(results) == 2 and results[1]["is_error"] is True
    assert json.loads(results[0]["content"])["output"] == "output/clip.mp4"
    assert client.calls[0]["fallbacks"] == "default"
    assert any(k == "error" for k, _ in events)
