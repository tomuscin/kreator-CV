"""
Kreator CV — FastAPI main application.
"""

import json
import os
import re
import secrets
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from src.cv_adapter import adapt_cv, analyze_job_posting, revise_field, revise_full_cv
from src.docx_generator import generate_cv_docx
from src.email_sender import send_cv
from src.history import add_entry, get_all, delete_entry
from src.job_scraper import fetch_job_posting

load_dotenv()

BASE_DIR    = Path(__file__).parent
OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

_APP_USERNAME = os.environ.get("APP_USERNAME", "")
_APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
_AUTH_ENABLED = bool(_APP_USERNAME and _APP_PASSWORD)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """HTTP Basic Auth protecting all routes except /health."""

    async def dispatch(self, request: Request, call_next):
        if not _AUTH_ENABLED or request.url.path in ("/health", "/api/test-smtp"):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Kreator CV"'},
                content={"detail": "Authentication required"},
            )
        import base64
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except Exception:
            return JSONResponse(status_code=401, content={"detail": "Invalid credentials"})

        ok = (
            secrets.compare_digest(username, _APP_USERNAME)
            and secrets.compare_digest(password, _APP_PASSWORD)
        )
        if not ok:
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Kreator CV"'},
                content={"detail": "Invalid credentials"},
            )
        return await call_next(request)


app = FastAPI(title="Kreator CV", version="2.0.0")
app.add_middleware(BasicAuthMiddleware)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ── Models ────────────────────────────────────────────────────────────

class JobInput(BaseModel):
    url:  str | None = None
    text: str | None = None

class AdaptRequest(BaseModel):
    job_posting: str
    edited_cv:   dict | None = None

class GenerateDocxRequest(BaseModel):
    job_posting: str
    edited_cv:   dict
    job_title:   str = ""
    company:     str = ""
    job_url:     str = ""
    job_date:    str = ""  # YYYY-MM-DD — publication date of job posting

class ReviseRequest(BaseModel):
    field_name:   str
    current_text: str
    user_comment: str
    job_posting:  str
    char_limit:   int

class ReviseCVRequest(BaseModel):
    current_cv:  dict
    instruction: str
    job_posting: str = ""

class SendRequest(BaseModel):
    cv_data:   dict
    to_email:  str
    subject:   str = "Aplikacja na stanowisko – Anna Jakubowska"
    filename:  str = "Anna_Jakubowska_CV.docx"
    job_title: str = ""
    company:   str = ""
    job_url:   str = ""
    job_date:  str = ""  # YYYY-MM-DD — publication date


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Keep-alive ping — no auth required."""
    return {"status": "ok"}


@app.get("/api/test-smtp")
async def test_smtp(_: str = Depends(lambda: None)):
    """Diagnostic: test SMTP connectivity and credentials from Render's server."""
    import smtplib, ssl, socket
    import certifi
    from src.email_sender import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
    result = {
        "host": SMTP_HOST,
        "port": SMTP_PORT,
        "user": SMTP_USER,
        "password_set": bool(SMTP_PASSWORD),
    }
    # 1. TCP connect
    try:
        with socket.create_connection((SMTP_HOST, SMTP_PORT), timeout=8):
            result["tcp_connect"] = "ok"
    except Exception as e:
        result["tcp_connect"] = f"BŁĄD: {e}"
        return result
    # 2. SSL handshake
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        with socket.create_connection((SMTP_HOST, SMTP_PORT), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=SMTP_HOST):
                result["ssl_handshake"] = "ok"
    except Exception as e:
        result["ssl_handshake"] = f"BŁĄD: {e}"
        return result
    # 3. SMTP login
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=8) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            result["smtp_login"] = "ok"
    except Exception as e:
        result["smtp_login"] = f"BŁĄD: {e}"
    return result


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = BASE_DIR / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.post("/api/fetch-job")
async def fetch_job(data: JobInput):
    if not data.url and not data.text:
        raise HTTPException(status_code=400, detail="Podaj URL lub wklej treść ogłoszenia.")
    if data.url:
        try:
            text = fetch_job_posting(data.url)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Błąd pobierania ogłoszenia: {e}")
    else:
        text = (data.text or "").strip()
    if len(text) < 50:
        raise HTTPException(status_code=422, detail="Treść ogłoszenia jest za krótka.")
    return {"job_posting": text}


@app.post("/api/analyze")
async def analyze(data: JobInput):
    job_text = _resolve_job_text(data)
    try:
        analysis = analyze_job_posting(job_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd analizy ogłoszenia: {e}")
    return {"analysis": analysis, "job_posting": job_text}


@app.post("/api/adapt")
async def adapt(data: AdaptRequest):
    if len(data.job_posting) < 50:
        raise HTTPException(status_code=400, detail="Treść ogłoszenia jest za krótka.")
    try:
        adapted = adapt_cv(data.job_posting, master_cv=data.edited_cv)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"adapted_cv": adapted}


@app.post("/api/revise")
async def revise(data: ReviseRequest):
    if not data.user_comment.strip():
        raise HTTPException(status_code=400, detail="Komentarz jest wymagany.")
    try:
        revised = revise_field(
            field_name=data.field_name,
            current_text=data.current_text,
            user_comment=data.user_comment,
            job_posting=data.job_posting,
            char_limit=data.char_limit,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"revised_text": revised}


@app.post("/api/revise-cv")
async def revise_cv_endpoint(data: ReviseCVRequest):
    if not data.instruction.strip():
        raise HTTPException(status_code=400, detail="Instrukcja jest wymagana.")
    try:
        revised = revise_full_cv(
            current_cv=data.current_cv,
            instruction=data.instruction,
            job_posting=data.job_posting,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"revised_cv": revised}


@app.post("/api/generate-docx")
async def generate_docx(data: GenerateDocxRequest):
    filename = _make_filename(data.company, data.job_date)
    output_path = OUTPUTS_DIR / filename
    try:
        generate_cv_docx(data.edited_cv, output_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd generowania .docx: {e}")
    try:
        add_entry(
            company=data.company,
            job_title=data.job_title,
            match_score=data.edited_cv.get("match_score", 0),
            filename=filename,
            job_url=data.job_url,
            job_text_preview=data.job_posting[:200],
            ats_keywords_count=len(data.edited_cv.get("ats_keywords", [])),
        )
    except Exception:
        pass
    return {"filename": filename, "download_url": f"/api/download/{filename}"}


@app.get("/api/download/{filename}")
async def download(filename: str):
    safe_name = Path(filename).name
    file_path = OUTPUTS_DIR / safe_name
    resolved  = file_path.resolve()
    if not resolved.is_file() or not str(resolved).startswith(str(OUTPUTS_DIR.resolve())):
        raise HTTPException(status_code=404, detail="Plik nie istnieje.")
    return FileResponse(
        path=str(file_path),
        filename=safe_name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.post("/api/send-email")
async def send_email(data: SendRequest):
    filename = data.filename if data.filename.endswith(".docx") else _make_filename(
        data.company, data.job_date
    )
    output_path = OUTPUTS_DIR / filename
    if not output_path.exists():
        try:
            generate_cv_docx(data.cv_data, output_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Błąd generowania .docx: {e}")

    # Personal message body from Tomasz to Anna
    job_url_line = f'<p>🔗 <a href="{data.job_url}">{data.job_url}</a></p>' if data.job_url else ""
    body_html = f"""
    <p>Kochanie,</p>
    <p>w załączeniu przesyłam Twoją aplikację:</p>
    {job_url_line}
    <p>
      📌 <strong>Stanowisko:</strong> {data.job_title or '—'}<br>
      🏢 <strong>Firma:</strong> {data.company or '—'}<br>
      📅 <strong>Data publikacji:</strong> {data.job_date or '—'}<br>
      📎 <strong>Załącznik:</strong> {filename}
    </p>
    <br>
    <p>Całusy,<br>
    <strong>Tomasz</strong><br>
    Twój Doradca Zawodowy 💼</p>
    """
    try:
        send_cv(to=data.to_email, subject=data.subject, body_html=body_html, docx_path=output_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Błąd wysyłki maila: {e}")
    return {"status": "sent", "to": data.to_email, "filename": filename}


@app.get("/api/history")
async def history_list():
    return {"entries": get_all()}


@app.delete("/api/history/{entry_id}")
async def history_delete(entry_id: str):
    if not delete_entry(entry_id):
        raise HTTPException(status_code=404, detail="Wpis nie istnieje.")
    return {"status": "deleted"}


@app.get("/api/master-cv")
async def get_master_cv():
    with open(BASE_DIR / "data" / "master_cv.json", encoding="utf-8") as f:
        return json.load(f)


# ── Helpers ──────────────────────────────────────────────────────────

def _resolve_job_text(data: JobInput) -> str:
    if not data.url and not data.text:
        raise HTTPException(status_code=400, detail="Podaj URL lub wklej treść ogłoszenia.")
    if data.url:
        try:
            return fetch_job_posting(data.url)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    return (data.text or "").strip()


def _slugify(text: str, max_len: int = 28) -> str:
    pl = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    text = text.strip().translate(pl)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:max_len].strip("_")


def _make_filename(company: str, job_date: str = "") -> str:
    """Anna Jakubowska_{firma}_{YYYY.MM.DD}.docx — date is generation date."""
    today = date.today().strftime("%Y.%m.%d")
    parts = ["Anna Jakubowska"]
    if company:
        parts.append(_slugify(company))
    parts.append(today)
    return "_".join(p for p in parts if p) + ".docx"
