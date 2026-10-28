"""Web app so several people can use the agent from a browser: upload media, describe the edit, download the result.

    pip install -e ".[web]"
    APP_PASSWORD=... ANTHROPIC_API_KEY=... agentvideo-web        # http://localhost:8000

Settings (environment variables):
    APP_PASSWORD          shared password (login with any username). Strongly recommended when exposed online.
    AGENTVIDEO_DATA       where job folders are stored (default ./data)
    MAX_CONCURRENT_JOBS   agent runs at the same time; others wait in a queue (default 2)
    MAX_UPLOAD_MB         per-request upload limit (default 1024)
    JOB_TTL_HOURS         delete job folders older than this (default 48)
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .agent import DEFAULT_MODEL, run_agent

DATA_DIR = Path(os.environ.get("AGENTVIDEO_DATA", "data")).resolve()
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "1024")) * 1_048_576
JOB_TTL = float(os.environ.get("JOB_TTL_HOURS", "48")) * 3600
PASSWORD = os.environ.get("APP_PASSWORD", "")
OUTPUT_EXT = {".mp4", ".mov", ".webm", ".gif", ".mp3", ".wav", ".m4a", ".srt", ".png", ".jpg"}

app = FastAPI(title="AgentVideo")
security = HTTPBasic(auto_error=False)
_slots = threading.Semaphore(MAX_CONCURRENT)
_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()


@dataclass
class Job:
    id: str
    dir: Path
    created: float = field(default_factory=time.time)
    status: str = "queued"  # queued | running | done | error
    events: list[dict] = field(default_factory=list)
    history: list = field(default_factory=list)

    def emit(self, kind: str, text: str) -> None:
        self.events.append({"kind": kind, "text": text, "t": round(time.time() - self.created, 1)})

    def outputs(self) -> list[dict]:
        out = self.dir / "output"
        if not out.exists():
            return []
        return [
            {"path": str(f.relative_to(self.dir)), "size_mb": round(f.stat().st_size / 1_048_576, 2)}
            for f in sorted(out.rglob("*")) if f.is_file() and f.suffix.lower() in OUTPUT_EXT
        ]


def require_auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not PASSWORD:
        return
    if creds is None or not secrets.compare_digest(creds.password.encode(), PASSWORD.encode()):
        raise HTTPException(401, "Sai mật khẩu", headers={"WWW-Authenticate": 'Basic realm="AgentVideo"'})


def _get_job(job_id: str) -> Job:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Không tìm thấy phiên làm việc")
    return job


def _cleanup() -> None:
    now = time.time()
    with _lock:
        for jid, job in list(_jobs.items()):
            if now - job.created > JOB_TTL and job.status in ("done", "error"):
                shutil.rmtree(job.dir, ignore_errors=True)
                del _jobs[jid]


def _safe_name(name: str) -> str:
    name = Path(name or "file").name
    name = re.sub(r"[^\w.\- ]", "_", name, flags=re.UNICODE).strip() or "file"
    return name[:120]


def _run(job: Job, request: str) -> None:
    job.status = "queued"
    job.emit("info", "Đang chờ tới lượt…")
    with _slots:
        job.status = "running"
        job.emit("info", "Bắt đầu xử lý")
        try:
            _, job.history = run_agent(request, str(job.dir), model=os.environ.get("AGENTVIDEO_MODEL", DEFAULT_MODEL),
                                       history=job.history, on_event=job.emit)
            job.status = "done"
        except Exception as exc:  # surface API/ffmpeg failures to the user instead of hanging
            job.emit("error", f"Lỗi: {exc}")
            job.status = "error"


def _start(job: Job, request: str) -> None:
    job.emit("user", request)
    threading.Thread(target=_run, args=(job, request), daemon=True).start()


async def _save_uploads(job: Job, files: list[UploadFile]) -> None:
    total = 0
    for up in files:
        if not up.filename:
            continue
        dest = job.dir / _safe_name(up.filename)
        with dest.open("wb") as fh:
            while chunk := await up.read(1 << 20):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"Tổng dung lượng tải lên vượt {MAX_UPLOAD_BYTES // 1_048_576} MB")
                fh.write(chunk)
        job.emit("info", f"Đã nhận file {dest.name}")


@app.post("/api/jobs", dependencies=[Depends(require_auth)])
async def create_job(request: str = Form(...), files: list[UploadFile] = File(default=[])) -> dict:
    _cleanup()
    job_id = uuid.uuid4().hex[:12]
    job = Job(job_id, DATA_DIR / job_id)
    job.dir.mkdir(parents=True)
    try:
        await _save_uploads(job, files)
    except HTTPException:
        shutil.rmtree(job.dir, ignore_errors=True)
        raise
    with _lock:
        _jobs[job_id] = job
    _start(job, request)
    return {"id": job_id}


@app.post("/api/jobs/{job_id}/messages", dependencies=[Depends(require_auth)])
async def follow_up(job_id: str, request: str = Form(...), files: list[UploadFile] = File(default=[])) -> dict:
    job = _get_job(job_id)
    if job.status in ("queued", "running"):
        raise HTTPException(409, "Agent đang xử lý yêu cầu trước, vui lòng đợi")
    await _save_uploads(job, files)
    _start(job, request)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
def job_status(job_id: str, since: int = 0) -> dict:
    job = _get_job(job_id)
    return {"id": job.id, "status": job.status, "events": job.events[since:], "next": len(job.events), "outputs": job.outputs()}


@app.get("/api/jobs/{job_id}/files/{path:path}", dependencies=[Depends(require_auth)])
def download(job_id: str, path: str, download: bool = False) -> FileResponse:
    job = _get_job(job_id)
    target = (job.dir / path).resolve()
    if job.dir.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "Không có file này")
    return FileResponse(target, filename=target.name if download else None)


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


def main() -> None:
    import uvicorn

    if not PASSWORD:
        print("⚠️  APP_PASSWORD chưa được đặt: ai truy cập được địa chỉ này cũng dùng được agent.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
