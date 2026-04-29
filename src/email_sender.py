"""
Email sender module for Kreator CV (Anna Jakubowska).

Transport priority:
  1. Resend API  — used when RESEND_API_KEY is set (works on Render free tier)
  2. SMTP        — fallback for local dev (port 465 implicit TLS or 587 STARTTLS)
"""

import os
import smtplib
import ssl
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import certifi
from dotenv import load_dotenv

load_dotenv()

# ── Resend ─────────────────────────────────────────────────────────────
RESEND_API_KEY  = os.environ.get("RESEND_API_KEY", "")

# ── SMTP (local fallback) ──────────────────────────────────────────────
SMTP_HOST        = os.environ.get("SMTP_HOST", "mail.tomaszuscinski.pl")
SMTP_PORT        = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER        = os.environ.get("SMTP_USER", "tomasz@tomaszuscinski.pl")
SMTP_PASSWORD    = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM        = os.environ.get("SMTP_FROM", SMTP_USER)
SMTP_FROM_NAME   = os.environ.get("SMTP_FROM_NAME", "Tomasz Uściński")
# Set SMTP_VERIFY_SSL=false when the mail server uses a wildcard cert (e.g. *.webd.pl)
# that doesn't match the configured SMTP_HOST (common on shared hosting).
SMTP_VERIFY_SSL  = os.environ.get("SMTP_VERIFY_SSL", "true").lower() != "false"


def _smtp_ssl_context() -> ssl.SSLContext:
    """Return an SSL context respecting SMTP_VERIFY_SSL."""
    if SMTP_VERIFY_SSL:
        return ssl.create_default_context(cafile=certifi.where())
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _strip_html(html: str) -> str:
    """Very simple HTML → plain text strip."""
    import re
    return re.sub(r"<[^>]+>", "", html).strip()


def send_cv(
    to: str,
    subject: str,
    body_html: str,
    docx_path: Path,
    body_plain: str | None = None,
) -> None:
    """
    Sends a CV email with a .docx attachment.

    Uses Resend API when RESEND_API_KEY is set (works on Render free tier).
    Falls back to SMTP for local development.

    Raises:
        FileNotFoundError: If docx_path does not exist.
        RuntimeError: On configuration or delivery errors.
    """
    if not docx_path.exists():
        raise FileNotFoundError(f"Plik CV nie istnieje: {docx_path}")

    docx_bytes    = docx_path.read_bytes()
    docx_filename = docx_path.name
    plain         = body_plain or _strip_html(body_html)
    from_field    = f"{SMTP_FROM_NAME} <{SMTP_FROM}>" if SMTP_FROM_NAME else SMTP_FROM

    if RESEND_API_KEY:
        _send_via_resend(to, subject, body_html, plain, from_field, docx_bytes, docx_filename)
    else:
        _send_via_smtp(to, subject, body_html, plain, from_field, docx_bytes, docx_filename)


def _send_via_resend(
    to: str,
    subject: str,
    body_html: str,
    body_plain: str,
    from_field: str,
    docx_bytes: bytes,
    docx_filename: str,
) -> None:
    import resend
    resend.api_key = RESEND_API_KEY
    params = {
        "from": from_field,
        "to": [to],
        "subject": subject,
        "html": body_html,
        "text": body_plain,
        "attachments": [
            {
                "filename": docx_filename,
                "content": list(docx_bytes),
            }
        ],
    }
    response = resend.Emails.send(params)
    if not response.get("id"):
        raise RuntimeError(f"Resend nie zwrócił ID wiadomości: {response}")


def _send_via_smtp(
    to: str,
    subject: str,
    body_html: str,
    body_plain: str,
    from_field: str,
    docx_bytes: bytes,
    docx_filename: str,
) -> None:
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("Brak konfiguracji SMTP credentials.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    if SMTP_FROM_NAME:
        encoded_name = Header(SMTP_FROM_NAME, "utf-8").encode()
        msg["From"]  = f"{encoded_name} <{SMTP_FROM}>"
    else:
        msg["From"]  = SMTP_FROM
    msg["To"] = to
    msg.attach(MIMEText(body_plain, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    attachment = MIMEApplication(
        docx_bytes,
        _subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    attachment.add_header("Content-Disposition", "attachment", filename=docx_filename)

    outer = MIMEMultipart("mixed")
    outer["Subject"] = msg["Subject"]
    outer["From"]    = msg["From"]
    outer["To"]      = msg["To"]
    outer.attach(msg)
    outer.attach(attachment)

    context = _smtp_ssl_context()
    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, [to], outer.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, [to], outer.as_string())
    except smtplib.SMTPAuthenticationError:
        raise RuntimeError("Błąd logowania SMTP. Sprawdź SMTP_USER i SMTP_PASSWORD.")
    except (TimeoutError, ConnectionRefusedError, OSError) as exc:
        raise RuntimeError(
            f"Nie udało się połączyć z serwerem SMTP ({SMTP_HOST}:{SMTP_PORT}). "
            "Sprawdź SMTP_HOST, SMTP_PORT i konfigurację TLS/SSL."
        ) from exc


def test_connection() -> bool:
    """
    Tests connectivity and credentials without sending a message.
    Returns True on success, raises RuntimeError on failure.
    """
    if RESEND_API_KEY:
        import httpx
        resp = httpx.get(
            "https://api.resend.com/emails/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            timeout=10,
        )
        if resp.status_code == 401:
            raise RuntimeError("Resend: nieprawidłowy klucz API.")
        return True
    # SMTP path
    context = _smtp_ssl_context()
    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=10) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
    return True

