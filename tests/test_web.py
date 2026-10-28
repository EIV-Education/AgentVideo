import importlib
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from agentvideo import ffmpeg_ops as ops  # noqa: E402


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTVIDEO_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("APP_PASSWORD", "secret")
    from agentvideo import web as web_mod

    web_mod = importlib.reload(web_mod)

    def fake_run_agent(request, workdir, *, history=None, on_event=None, **kw):
        on_event("tool", "trim_video(...)")
        ops.trim(f"{workdir}/clip.mp4", f"{workdir}/output/final.mp4", 0, 1)
        on_event("text", f"Xong: {request}")
        return "ok", [*(history or []), request]

    monkeypatch.setattr(web_mod, "run_agent", fake_run_agent)
    return web_mod


def _wait(client, job_id, auth):
    for _ in range(100):
        d = client.get(f"/api/jobs/{job_id}", auth=auth).json()
        if d["status"] in ("done", "error"):
            return d
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_password_required(web):
    client = TestClient(web.app)
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("any", "wrong")).status_code == 401
    assert "AgentVideo" in client.get("/", auth=("any", "secret")).text


def test_upload_run_download_and_follow_up(web, tmp_path):
    src = tmp_path / "clip.mp4"
    ops.ffmpeg("-f", "lavfi", "-i", "testsrc=s=320x240:d=2", *ops.ENCODE, str(src))
    client = TestClient(web.app)
    auth = ("any", "secret")

    r = client.post("/api/jobs", auth=auth, data={"request": "cắt 1 giây"},
                    files=[("files", ("../../clip.mp4", src.read_bytes(), "video/mp4"))])
    assert r.status_code == 200
    job_id = r.json()["id"]
    d = _wait(client, job_id, auth)
    assert d["status"] == "done"
    assert d["outputs"][0]["path"] == "output/final.mp4"

    f = client.get(f"/api/jobs/{job_id}/files/output/final.mp4", auth=auth)
    assert f.status_code == 200 and len(f.content) > 1000
    assert client.get(f"/api/jobs/{job_id}/files/../../etc/passwd", auth=auth).status_code == 404

    r = client.post(f"/api/jobs/{job_id}/messages", auth=auth, data={"request": "làm lại"})
    assert r.status_code == 200
    _wait(client, job_id, auth)
    assert web._jobs[job_id].history == ["cắt 1 giây", "làm lại"]
