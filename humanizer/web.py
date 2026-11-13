"""
Humanizer Pro Studio — local web app.

* Single-page studio served at ``/`` (no external assets — works offline).
* Background jobs with live progress and cancellation (``/api/jobs``).
* Explainable analysis with highlight spans (``/api/analyze``).
* DOCX/PDF/TXT/MD upload, format-preserving DOCX export.

Security: the server binds to 127.0.0.1 by default, accepts only localhost
``Host`` headers (blocks DNS-rebinding) and sends no permissive CORS headers,
so other websites cannot drive it or spend your API keys.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from humanizer import __version__
from humanizer.config import MAX_INPUT_CHARS, HumanizerConfig
from humanizer.detector import analyze, model_available, score_text
from humanizer.document import docx_text, extract_text_from_file
from humanizer.engine import humanize_docx, humanize_text
from humanizer.lint import lint_text, readability
from humanizer.providers import available_count, get_provider_status, has_providers, reset_cooldowns

logger = logging.getLogger(__name__)

_UPLOAD_DIR = Path(tempfile.gettempdir()) / "humanizer_pro_uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
_SESSION_ID_RE = re.compile(r"^[a-f0-9]{8,32}$")
_MAX_UPLOAD_BYTES = int(os.environ.get("HUMANIZER_MAX_UPLOAD_MB", "25")) * 1024 * 1024
_TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_allowed_hosts = ["localhost", "127.0.0.1", "[::1]", "testserver"]
_allowed_hosts += [h.strip() for h in os.environ.get("HUMANIZER_ALLOWED_HOSTS", "").split(",") if h.strip()]

app = FastAPI(title="Humanizer Pro Studio", version=__version__, docs_url="/api/docs", redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)


def _cleanup_uploads(max_age_hours: float = 24.0) -> None:
    cutoff = time.time() - max_age_hours * 3600
    for f in _UPLOAD_DIR.glob("*"):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except Exception:
            pass


_cleanup_uploads()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class TextRequest(BaseModel):
    text: str = Field("", max_length=MAX_INPUT_CHARS)


ScoreRequest = TextRequest


class HumanizeRequest(BaseModel):
    text: str = Field("", max_length=MAX_INPUT_CHARS)
    scientific: bool = True
    stealth_level: int = Field(3, ge=1, le=3)
    target_pct: float = Field(10.0, ge=0, le=100)
    use_gemini: bool = True
    session_docx: Optional[str] = None


class DownloadDocxRequest(BaseModel):
    text: str = Field("", max_length=MAX_INPUT_CHARS)
    session_docx: Optional[str] = None


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
class Job:
    def __init__(self, req: HumanizeRequest) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.req = req
        self.status = "queued"
        self.progress = 0.0
        self.message = "Queued"
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.cancel = threading.Event()
        self.created = time.time()
        self.finished: Optional[float] = None

    def public(self) -> dict:
        return {"id": self.id, "status": self.status, "progress": round(self.progress, 1),
                "message": self.message, "result": self.result, "error": self.error}


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=int(os.environ.get("HUMANIZER_JOB_WORKERS", "2")), thread_name_prefix="humanize")


def _prune_jobs() -> None:
    cutoff = time.time() - 3600
    with _JOBS_LOCK:
        for jid in [j.id for j in _JOBS.values() if j.finished and j.finished < cutoff]:
            _JOBS.pop(jid, None)


def _session_path(sid: Optional[str], suffix: str = ".docx") -> Optional[Path]:
    if not sid:
        return None
    if not _SESSION_ID_RE.match(sid):
        raise HTTPException(status_code=400, detail="Invalid session id")
    return _UPLOAD_DIR / f"{sid}{suffix}"


def _summary(a: dict) -> dict:
    return {k: a.get(k) for k in ("ai_score", "verdict", "confidence", "burstiness", "ai_tells", "sentences", "words", "signals")}


def _run_humanize(req: HumanizeRequest, cancel: Optional[threading.Event] = None, progress=None, status=None) -> dict:
    t0 = time.time()
    cfg = HumanizerConfig(
        use_gemini=req.use_gemini,
        stealth_level=req.stealth_level,
        target_pct=req.target_pct,
        scientific=req.scientific,
        cancel_event=cancel,
        progress_callback=progress,
        status_callback=status,
    )
    src_doc = _session_path(req.session_docx)
    if src_doc is not None and src_doc.exists():
        source_text = docx_text(src_doc)
        out_doc = _session_path(req.session_docx, "_humanized.docx")
        humanize_docx(src_doc, cfg, dst=out_doc)
        out_text = docx_text(out_doc)
        docx_ready = True
    else:
        source_text = req.text
        out_text = humanize_text(req.text, cfg)
        docx_ready = False

    before = analyze(source_text)
    after = analyze(out_text)
    read = readability(out_text)
    return {
        "success": True,
        "cancelled": bool(cancel and cancel.is_set()),
        "output": out_text,
        "engine": cfg.engine_used,
        "initial_score": before["ai_score"],
        "final_score": after["ai_score"],
        "before": _summary(before),
        "after": _summary(after),
        "metrics": {**score_text(out_text), "flesch": read.get("flesch"), "grade": read.get("grade")},
        "integrity": [{"severity": s, "message": m} for s, m in lint_text(out_text, source_text)
                      if not m.startswith(("AI-tell", "double space"))],
        "docx_ready": docx_ready,
        "duration": round(time.time() - t0, 2),
    }


def _job_runner(job: Job) -> None:
    job.status = "running"
    job.message = "Starting…"

    def on_progress(cur: int, total: int, pct: float, msg: str) -> None:
        job.progress = pct
        if msg:
            job.message = msg

    def on_status(msg: str) -> None:
        if msg and not msg.startswith("["):
            job.message = msg

    try:
        job.result = _run_humanize(job.req, job.cancel, on_progress, on_status)
        job.status = "cancelled" if job.cancel.is_set() else "done"
        job.progress = 100.0 if job.status == "done" else job.progress
        job.message = "Cancelled — partial result kept" if job.status == "cancelled" else "Done"
    except Exception as e:  # pragma: no cover - surfaced to the UI
        logger.exception("Job %s failed", job.id)
        job.status = "error"
        job.error = str(e) or e.__class__.__name__
        job.message = "Failed"
    finally:
        job.finished = time.time()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def serve_index() -> HTMLResponse:
    if not _TEMPLATE.exists():
        raise HTTPException(status_code=404, detail="Studio template not found")
    return HTMLResponse(_TEMPLATE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def api_health() -> dict:
    return {
        "version": __version__,
        "llm_configured": has_providers(),
        "llm_ready": available_count(),
        "perplexity_model": model_available(),
    }


@app.post("/api/analyze")
def api_analyze(req: TextRequest) -> dict:
    text = req.text or ""
    a = analyze(text)
    a["readability"] = readability(text)
    a["lint"] = [{"severity": s, "message": m} for s, m in lint_text(text)][:20]
    return a


@app.post("/api/score")
def api_score(req: TextRequest) -> dict:
    """Compact score (backwards compatible)."""
    text = (req.text or "").strip()
    scores = score_text(text)
    read = readability(text)
    return {**scores, "flesch": read.get("flesch"), "words": len(text.split()), "chars": len(text),
            "lint_issues": lint_text(text)[:5] if text else []}


@app.post("/api/jobs")
def api_create_job(req: HumanizeRequest) -> dict:
    if not (req.text or "").strip() and not req.session_docx:
        raise HTTPException(status_code=400, detail="Empty text provided")
    _session_path(req.session_docx)  # validate early
    _prune_jobs()
    job = Job(req)
    with _JOBS_LOCK:
        _JOBS[job.id] = job
    _EXECUTOR.submit(_job_runner, job)
    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str) -> dict:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job.public()


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str) -> dict:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    job.cancel.set()
    job.message = "Cancelling…"
    return {"success": True}


@app.post("/api/humanize")
def api_humanize(req: HumanizeRequest) -> dict:
    """Synchronous humanize (kept for scripts; the studio uses /api/jobs)."""
    if not (req.text or "").strip() and not req.session_docx:
        raise HTTPException(status_code=400, detail="Empty text provided")
    try:
        return _run_humanize(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Humanization failed")
        return {"success": False, "error": str(e)}


@app.post("/api/upload")
def api_upload(file: UploadFile = File(...)) -> dict:
    filename = os.path.basename(file.filename or "document")
    suffix = Path(filename).suffix.lower()
    if suffix not in (".docx", ".pdf", ".txt", ".md"):
        raise HTTPException(status_code=400, detail="Supported formats: .docx, .pdf, .txt, .md")
    sid = uuid.uuid4().hex[:16]
    dest = _UPLOAD_DIR / f"{sid}{suffix}"
    size = 0
    with open(dest, "wb") as fh:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > _MAX_UPLOAD_BYTES:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"File too large (max {_MAX_UPLOAD_BYTES // 1048576} MB)")
            fh.write(chunk)
    try:
        text = extract_text_from_file(dest)
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not read this file: {e}") from e
    if suffix != ".docx":
        dest.unlink(missing_ok=True)
    if len(text) > MAX_INPUT_CHARS:
        raise HTTPException(status_code=413, detail="Document is too long")
    return {
        "success": True,
        "filename": filename,
        "text": text,
        "words": len(text.split()),
        "paragraphs": len([p for p in text.split("\n\n") if p.strip()]),
        "session_docx": sid if suffix == ".docx" else None,
    }


@app.post("/api/download-docx")
def api_download_docx(req: DownloadDocxRequest):
    out_doc = _session_path(req.session_docx, "_humanized.docx")
    if out_doc is not None and out_doc.exists():
        return FileResponse(str(out_doc), filename="humanized.docx", media_type=_DOCX_MIME)
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")
    import docx

    doc = docx.Document()
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", block)
        if m:
            doc.add_heading(m.group(2).strip(), level=min(len(m.group(1)), 4))
        elif all(re.match(r"^\s*[-*•]\s+", ln) for ln in block.splitlines()):
            for ln in block.splitlines():
                doc.add_paragraph(re.sub(r"^\s*[-*•]\s+", "", ln), style="List Bullet")
        else:
            doc.add_paragraph(block)
    out = _UPLOAD_DIR / f"export_{uuid.uuid4().hex[:12]}.docx"
    doc.save(str(out))
    return FileResponse(str(out), filename="humanized.docx", media_type=_DOCX_MIME)


@app.get("/api/providers")
def api_providers() -> list[dict]:
    return get_provider_status()


@app.post("/api/reset-cooldowns")
def api_reset_cooldowns() -> dict:
    reset_cooldowns()
    return {"success": True}


@app.exception_handler(ValueError)
def _value_error(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def launch_web(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    """Start the studio and open it in the default browser."""
    import uvicorn

    if host not in ("127.0.0.1", "localhost") and host not in _allowed_hosts:
        _allowed_hosts.append(host)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}"
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"\n  Humanizer Pro Studio running at {url}  (Ctrl+C to stop)\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    launch_web()
