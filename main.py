"""
Kreator CV — FastAPI main application — Anna Jakubowska.
"""

import asyncio
import json
import json as _json_mod
import os
import re
import secrets
from datetime import date
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from src.cv_adapter import adapt_cv, analyze_job_posting, revise_field, revise_full_cv
from src.docx_generator import generate_cv_docx, generate_cv_docx_bytes
from src.email_sender import send_cv
from src.history import add_entry, get_all, delete_entry
from src.job_scraper import fetch_job_posting
import src.application_repository as app_repo
import src.storage as storage

load_dotenv()

# Initialise DB (no-op if DB_ENABLED=false or credentials missing)
app_repo.init_db()

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


app = FastAPI(title="Kreator CV — Anna Jakubowska", version="2.0.0")
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
    field_name:         str
    current_text:       str
    user_comment:       str
    job_posting:        str
    char_limit:         int
    cv_output_language: str = "pl"  # preserve CV language during revision

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
    record_id: int | None = None  # DB record id from generate-docx


class ContactRequest(BaseModel):
    contact_person: str = ""
    contact_phone:  str = ""
    contact_email:  str = ""


class NotesRequest(BaseModel):
    notes: str = ""


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Keep-alive ping — no auth required."""
    return {"status": "ok"}


@app.get("/api/test-smtp")
async def test_smtp(_: str = Depends(lambda: None)):
    """Diagnostic: test email transport (Resend or SMTP) from Render's server."""
    from src.email_sender import RESEND_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
    result = {
        "transport": "resend" if RESEND_API_KEY else "smtp",
        "resend_key_set": bool(RESEND_API_KEY),
        "host": SMTP_HOST,
        "port": SMTP_PORT,
        "user": SMTP_USER,
        "password_set": bool(SMTP_PASSWORD),
    }
    if RESEND_API_KEY:
        import httpx
        try:
            resp = httpx.get(
                "https://api.resend.com/emails/00000000-0000-0000-0000-000000000000",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                timeout=10,
            )
            result["resend_api"] = "ok" if resp.status_code != 401 else "BŁĄD: nieprawidłowy klucz API"
        except Exception as e:
            result["resend_api"] = f"BŁĄD: {e}"
        return result
    # SMTP path
    import smtplib, ssl, socket
    import certifi
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


@app.post("/api/adapt-stream")
async def adapt_stream(data: AdaptRequest):
    """
    Streaming version of /api/adapt using Server-Sent Events.
    Sends an immediate 'started' event to prevent Render 30s timeout,
    then runs adapt_cv in a thread pool and sends the result when done.
    Times out after 130 seconds and returns a controlled error event.
    """
    if len(data.job_posting) < 50:
        raise HTTPException(status_code=400, detail="Treść ogłoszenia jest za krótka.")

    async def event_generator():
        # Immediate event — prevents 30s gateway timeout
        yield 'data: {"status":"started"}\n\n'
        loop = asyncio.get_event_loop()
        try:
            adapted = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: adapt_cv(data.job_posting, master_cv=data.edited_cv),
                ),
                timeout=130.0,
            )
            result = _json_mod.dumps(
                {"status": "done", "adapted_cv": adapted}, ensure_ascii=False
            )
            yield f"data: {result}\n\n"
        except asyncio.TimeoutError:
            yield 'data: {"status":"error","detail":"Generowanie CV przekroczyło limit czasu (130s). Spróbuj ponownie lub skróć ogłoszenie."}\n\n'
        except (ValueError, RuntimeError) as exc:
            msg = str(exc)
            if "rate_limit" in msg.lower() or "rate limit" in msg.lower():
                msg = "Limit zapytań OpenAI wyczerpany. Poczekaj chwilę i spróbuj ponownie."
            elif "context_length" in msg.lower() or "token" in msg.lower() and "exceed" in msg.lower():
                msg = "Ogłoszenie jest zbyt długie dla modelu AI. Spróbuj skrócić treść ogłoszenia."
            elif "connection" in msg.lower() or "timeout" in msg.lower():
                msg = "Problem z połączeniem do OpenAI. Sprawdź internet i spróbuj ponownie."
            elif "authentication" in msg.lower() or "api_key" in msg.lower() or "unauthorized" in msg.lower():
                msg = "Błąd uwierzytelnienia OpenAI. Skontaktuj się z administratorem."
            elif "insufficient_quota" in msg.lower() or "quota" in msg.lower():
                msg = "Wyczerpany limit OpenAI (brak środków na koncie). Skontaktuj się z administratorem."
            err = _json_mod.dumps({"status": "error", "detail": msg}, ensure_ascii=False)
            yield f"data: {err}\n\n"
        except Exception as exc:
            err = _json_mod.dumps(
                {"status": "error", "detail": f"Nieoczekiwany błąd: {type(exc).__name__}"},
                ensure_ascii=False,
            )
            yield f"data: {err}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
            cv_output_language=data.cv_output_language,
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

    # ── Legacy JSON history (keep existing) ──────────────────────────
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

    # ── FTP upload ───────────────────────────────────────────────────
    ftp_result = storage.upload_docx(
        local_path=output_path,
        job_title=data.job_title,
        company_name=data.company,
        date_str=data.job_date,
    )
    remote_path = ftp_result.get("remote_path", "")
    public_url  = ftp_result.get("public_url", "")

    # ── DB record ────────────────────────────────────────────────────
    cv  = data.edited_cv
    gap = cv.get("experience_gap_analysis", {})
    ats = cv.get("ats_keyword_strategy", {})
    rt  = cv.get("role_type", "")

    if ftp_result["ok"]:
        db_status = "generated"
    elif ftp_result["reason"] == "disabled":
        db_status = "storage_skipped"
    else:
        db_status = "storage_error"

    record_id = app_repo.save_application({
        "job_posting_date":     data.job_date or None,
        "company_name":         data.company,
        "job_title":            data.job_title,
        "role_type":            rt,
        "role_type_label":      app_repo.role_type_label(rt),
        "role_type_confidence": cv.get("role_type_confidence", ""),
        "job_language":         cv.get("job_language", ""),
        "cv_output_language":   cv.get("cv_output_language", ""),
        "job_url":              data.job_url,
        "source_type":          "kreator",
        "status":               db_status,
        "cv_filename":          filename,
        "cv_local_path":        str(output_path),
        "cv_remote_path":       remote_path,
        "cv_public_url":        public_url,
        "match_score":          cv.get("match_score"),
        "confirmed_strengths":  gap.get("confirmed_strengths", []),
        "gaps":                 gap.get("gaps", []),
        "transferable_angles":  gap.get("transferable_angles", []),
        "do_not_claim":         gap.get("do_not_claim", []),
        "used_keywords":        ats.get("used_keywords", []),
        "excluded_keywords":    ats.get("excluded_keywords", []),
        "error_message":        ftp_result.get("error", "") if not ftp_result["ok"] and ftp_result["reason"] == "error" else "",
    })

    return {
        "filename":     filename,
        "download_url": f"/api/download/{quote(filename)}",
        "record_id":    record_id,
    }


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
    # Generate DOCX in memory — avoids ephemeral filesystem issues on Render
    try:
        docx_bytes = generate_cv_docx_bytes(data.cv_data)
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
        send_cv(
            to=data.to_email,
            subject=data.subject,
            body_html=body_html,
            docx_bytes=docx_bytes,
            docx_filename=filename,
        )
    except Exception as e:
        # Update DB status to send_error if possible
        if data.record_id:
            app_repo.update_status(
                data.record_id, "send_error",
                error_message=f"Email send failed: {type(e).__name__}",
            )
        raise HTTPException(status_code=500, detail=f"Błąd wysyłki maila: {e}")

    # Update DB status to sent
    if data.record_id:
        app_repo.update_status(
            data.record_id, "sent",
            sent_to_email=data.to_email,
            sent_at=__import__('datetime').datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            email_subject=data.subject,
        )

    return {"status": "sent", "to": data.to_email, "filename": filename}


@app.get("/api/history")
async def history_list():
    return {"entries": get_all()}


@app.delete("/api/history/{entry_id}")
async def history_delete(entry_id: str):
    if not delete_entry(entry_id):
        raise HTTPException(status_code=404, detail="Wpis nie istnieje.")
    return {"status": "deleted"}


# ── Applications dashboard API ────────────────────────────────────────

@app.get("/api/applications/export.csv")
async def export_csv(
    request: Request,
):
    """CSV export of all application records."""
    from fastapi.responses import Response as FapiResponse
    csv_data = app_repo.export_csv()
    return FapiResponse(
        content=csv_data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="cv_applications.csv"'},
    )


@app.get("/api/applications")
async def list_applications(
    status:             str | None = None,
    role_type:          str | None = None,
    cv_output_language: str | None = None,
    company_name:       str | None = None,
    date_from:          str | None = None,
    date_to:            str | None = None,
):
    items = app_repo.list_applications(
        status=status,
        role_type=role_type,
        cv_output_language=cv_output_language,
        company_name=company_name,
        date_from=date_from,
        date_to=date_to,
    )
    if items is None:
        return JSONResponse({
            "ok": True,
            "database_enabled": False,
            "reason": "database_disabled",
            "items": [],
            "stats": {},
        })
    stats = app_repo.get_stats()
    return {"ok": True, "database_enabled": True, "items": items, "stats": stats}


@app.get("/api/applications/{record_id}")
async def get_application(record_id: int):
    record = app_repo.get_application(record_id)
    if record is None:
        if not app_repo._db_enabled():
            return JSONResponse({"ok": False, "reason": "database_disabled"})
        raise HTTPException(status_code=404, detail="Rekord nie istnieje.")
    return {"ok": True, "item": record}


@app.post("/api/applications/{record_id}/notes")
async def update_notes(record_id: int, data: NotesRequest):
    if not app_repo._db_enabled():
        return JSONResponse({"ok": False, "reason": "database_disabled"})
    ok = app_repo.update_notes(record_id, data.notes)
    if not ok:
        raise HTTPException(status_code=500, detail="Nie udało się zapisać notatki.")
    return {"ok": True}


@app.post("/api/applications/{record_id}/contact")
async def update_contact(record_id: int, data: ContactRequest):
    if not app_repo._db_enabled():
        return JSONResponse({"ok": False, "reason": "database_disabled"})
    # Basic email format check — non-blocking
    if data.contact_email and "@" not in data.contact_email:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "reason": "invalid_email", "detail": "Niepoprawny format adresu email."},
        )
    ok = app_repo.update_contact(
        record_id,
        contact_person=data.contact_person,
        contact_phone=data.contact_phone,
        contact_email=data.contact_email,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Nie udało się zapisać danych kontaktowych.")
    updated = app_repo.get_application(record_id)
    return {"ok": True, "item": updated}


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
