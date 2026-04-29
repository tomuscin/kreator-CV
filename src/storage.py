"""
Storage module — FTP upload for generated CV DOCX files.

Kreator CV — Anna Jakubowska.

Feature-flagged: FTP_ENABLED=true must be set along with FTP_HOST, FTP_USER,
FTP_PASSWORD. If not configured, upload is skipped and the application
continues normally — the local DOCX file is still available.
"""

import ftplib
import logging
import os
import re
import secrets
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Polish character transliteration table ────────────────────────────

_PL_CHARS = str.maketrans(
    "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ",
    "acelnoszzACELNOSZZ",
)


# ── Configuration ─────────────────────────────────────────────────────

def _ftp_config() -> dict | None:
    """Returns FTP config dict if FTP_ENABLED=true and required vars set, else None."""
    if os.environ.get("FTP_ENABLED", "false").lower() != "true":
        return None
    host     = os.environ.get("FTP_HOST", "")
    user     = os.environ.get("FTP_USER", "")
    password = os.environ.get("FTP_PASSWORD", "")
    if not all([host, user, password]):
        logger.warning(
            "FTP_ENABLED=true but FTP_HOST/FTP_USER/FTP_PASSWORD incomplete — FTP disabled"
        )
        return None
    return {
        "host":            host,
        "port":            int(os.environ.get("FTP_PORT", "21")),
        "user":            user,
        "password":        password,
        "base_dir":        os.environ.get("FTP_BASE_DIR", "/cv-kreator/generated"),
        "public_base_url": os.environ.get("FTP_PUBLIC_BASE_URL", "").rstrip("/"),
    }


# ── Filename helpers ──────────────────────────────────────────────────

def _sanitize_part(text: str, max_len: int = 30) -> str:
    """Removes Polish chars and special chars; replaces spaces/hyphens with underscores."""
    text = (text or "").strip().translate(_PL_CHARS)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:max_len].strip("_")


def make_remote_filename(
    job_title: str = "",
    company_name: str = "",
    date_str: str = "",
    suffix: str = "",
) -> str:
    """
    Creates a human-readable FTP filename matching the local download name.

    Example:
      CV Anna Jakubowska Market Researcher ACME sp. z o.o. 2026-04-29.docx

    Note: '/' in job_title replaced with '-' (FTP path separator safety).
    Suffix appended when provided (collision avoidance).
    """
    date_part = (date_str or datetime.utcnow().strftime("%Y-%m-%d")).replace(".", "-")
    safe_title   = (job_title or "").strip().replace("/", "-")
    safe_company = (company_name or "").strip()
    parts = ["CV", "Anna Jakubowska"]
    if safe_title:
        parts.append(safe_title)
    if safe_company:
        parts.append(safe_company)
    parts.append(date_part)
    base = " ".join(filter(None, parts))
    if suffix:
        base = f"{base}_{suffix}"
    return base + ".docx"


def make_remote_path(
    base_dir: str,
    filename: str,
    now: datetime | None = None,
) -> str:
    """
    Builds the remote FTP path: base_dir/YYYY/MM/filename

    Example:
      /cv-kreator/generated/2026/04/CV Anna Jakubowska ...docx
    """
    dt = now or datetime.utcnow()
    return f"{base_dir.rstrip('/')}/{dt.strftime('%Y')}/{dt.strftime('%m')}/{filename}"


# ── FTP helpers ───────────────────────────────────────────────────────

def _ensure_dirs(ftp: ftplib.FTP, remote_path: str) -> None:
    """Creates all intermediate directories for the given remote_path."""
    dir_path = remote_path.rsplit("/", 1)[0]
    segments = dir_path.strip("/").split("/")
    current = ""
    for seg in segments:
        current = f"{current}/{seg}"
        try:
            ftp.mkd(current)
        except ftplib.error_perm:
            pass  # directory already exists — that's fine


# ── Public upload function ────────────────────────────────────────────

def upload_docx(
    local_path: str | Path,
    job_title: str = "",
    company_name: str = "",
    date_str: str = "",
) -> dict:
    """
    Uploads a DOCX file to FTP.

    Returns a dict with:
      - ok:          bool
      - reason:      "uploaded" | "disabled" | "error"
      - remote_path: str  (set when reason == "uploaded")
      - public_url:  str  (set when FTP_PUBLIC_BASE_URL is configured)
      - error:       str  (set when reason == "error", sanitized — no secrets)
    """
    cfg = _ftp_config()
    if cfg is None:
        return {"ok": False, "reason": "disabled", "remote_path": "", "public_url": ""}

    suffix = secrets.token_hex(3)          # 6-char hex to avoid collisions
    now    = datetime.utcnow()
    fname  = make_remote_filename(job_title, company_name, date_str, suffix)
    rpath  = make_remote_path(cfg["base_dir"], fname, now)

    public_url = ""
    if cfg["public_base_url"]:
        public_url = (
            f"{cfg['public_base_url']}"
            f"/{now.strftime('%Y')}/{now.strftime('%m')}/{fname}"
        )

    try:
        ftp = ftplib.FTP()
        ftp.connect(cfg["host"], cfg["port"], timeout=15)
        ftp.login(cfg["user"], cfg["password"])
        ftp.set_pasv(True)
        _ensure_dirs(ftp, rpath)
        with open(local_path, "rb") as f:
            ftp.storbinary(f"STOR {rpath}", f)
        ftp.quit()
        logger.info("FTP upload OK: %s", rpath)
        return {
            "ok":          True,
            "reason":      "uploaded",
            "remote_path": rpath,
            "public_url":  public_url,
        }
    except Exception as exc:  # noqa: BLE001
        # Log type only — never log FTP password or host credentials
        logger.error("FTP upload failed: %s", type(exc).__name__)
        return {
            "ok":          False,
            "reason":      "error",
            "remote_path": "",
            "public_url":  "",
            "error":       f"FTP upload failed: {type(exc).__name__}",
        }
