"""
Application Repository — MySQL/MariaDB storage for CV application history.

Kreator CV — Anna Jakubowska.

Feature-flagged: DB_ENABLED=true must be set along with DB_HOST, DB_NAME,
DB_USER, DB_PASSWORD. If not configured, all operations return safe fallbacks
and the application continues normally.
"""

import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Candidate identity (used to separate AJ/TU records in shared DB) ───
CANDIDATE_NAME = "Anna Jakubowska"

# ── Role type labels ──────────────────────────────────────────────────

ROLE_TYPE_LABELS: dict[str, str] = {
    "individual_contributor": "Business Development / Client Partner",
    "sales_leadership":       "Sales Leadership / Management",
    "mixed":                  "Mixed — sprzedaż indywidualna + leadership",
    "unknown":                "Nieustalony",
}


def role_type_label(role_type: str) -> str:
    return ROLE_TYPE_LABELS.get(role_type, "Nieustalony")


# ── Configuration ─────────────────────────────────────────────────────

def _db_enabled() -> bool:
    return os.environ.get("DB_ENABLED", "false").lower() == "true"


def _db_config() -> dict | None:
    """Returns DB config dict if DB_ENABLED=true and required vars are set, else None."""
    if not _db_enabled():
        return None
    host     = os.environ.get("DB_HOST", "")
    db_name  = os.environ.get("DB_NAME", "")
    user     = os.environ.get("DB_USER", "")
    password = os.environ.get("DB_PASSWORD", "")
    if not all([host, db_name, user, password]):
        logger.warning(
            "DB_ENABLED=true but DB_HOST/DB_NAME/DB_USER/DB_PASSWORD incomplete — database disabled"
        )
        return None
    return {
        "host":      host,
        "port":      int(os.environ.get("DB_PORT", "3306")),
        "database":  db_name,
        "user":      user,
        "password":  password,
        "charset":   os.environ.get("DB_CHARSET", "utf8mb4"),
        "autocommit": True,
    }


def _get_conn():
    """Opens a new PyMySQL connection. Raises RuntimeError if DB not configured."""
    try:
        import pymysql  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("pymysql is not installed — add it to requirements.txt") from exc
    cfg = _db_config()
    if cfg is None:
        raise RuntimeError("Database is not configured")
    return pymysql.connect(**cfg)


# ── Table schema ──────────────────────────────────────────────────────

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS cv_applications (
  id INT AUTO_INCREMENT PRIMARY KEY,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,

  job_posting_date DATE NULL,
  company_name VARCHAR(255) NULL,
  job_title VARCHAR(255) NULL,
  role_type VARCHAR(64) NULL,
  role_type_label VARCHAR(255) NULL,
  role_type_confidence VARCHAR(32) NULL,

  job_language VARCHAR(32) NULL,
  cv_output_language VARCHAR(32) NULL,

  job_url TEXT NULL,
  source_type VARCHAR(32) NULL,

  status VARCHAR(64) NOT NULL,

  cv_filename VARCHAR(255) NULL,
  cv_local_path TEXT NULL,
  cv_remote_path TEXT NULL,
  cv_public_url TEXT NULL,

  sent_to_email VARCHAR(255) NULL,
  sent_at DATETIME NULL,
  email_subject VARCHAR(255) NULL,

  contact_person VARCHAR(255) NULL,
  contact_phone VARCHAR(100) NULL,
  contact_email VARCHAR(255) NULL,

  match_score INT NULL,

  confirmed_strengths TEXT NULL,
  gaps TEXT NULL,
  transferable_angles TEXT NULL,
  do_not_claim TEXT NULL,
  used_keywords TEXT NULL,
  excluded_keywords TEXT NULL,

  notes TEXT NULL,
  error_message TEXT NULL,
  candidate_name VARCHAR(255) NULL
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
"""

# Columns that can be added via ALTER TABLE migration (excludes always-present columns)
_MIGRATION_COLUMNS: dict[str, str] = {
    "job_posting_date":     "DATE NULL",
    "company_name":         "VARCHAR(255) NULL",
    "job_title":            "VARCHAR(255) NULL",
    "role_type":            "VARCHAR(64) NULL",
    "role_type_label":      "VARCHAR(255) NULL",
    "role_type_confidence": "VARCHAR(32) NULL",
    "job_language":         "VARCHAR(32) NULL",
    "cv_output_language":   "VARCHAR(32) NULL",
    "job_url":              "TEXT NULL",
    "source_type":          "VARCHAR(32) NULL",
    "cv_filename":          "VARCHAR(255) NULL",
    "cv_local_path":        "TEXT NULL",
    "cv_remote_path":       "TEXT NULL",
    "cv_public_url":        "TEXT NULL",
    "sent_to_email":        "VARCHAR(255) NULL",
    "sent_at":              "DATETIME NULL",
    "email_subject":        "VARCHAR(255) NULL",
    "contact_person":       "VARCHAR(255) NULL",
    "contact_phone":        "VARCHAR(100) NULL",
    "contact_email":        "VARCHAR(255) NULL",
    "match_score":          "INT NULL",
    "confirmed_strengths":  "TEXT NULL",
    "gaps":                 "TEXT NULL",
    "transferable_angles":  "TEXT NULL",
    "do_not_claim":         "TEXT NULL",
    "used_keywords":        "TEXT NULL",
    "excluded_keywords":    "TEXT NULL",
    "notes":                "TEXT NULL",
    "error_message":        "TEXT NULL",
    "candidate_name":       "VARCHAR(255) NULL",
}


# ── Init / migration ──────────────────────────────────────────────────

def init_db() -> bool:
    """
    Creates table if not exists and runs safe column migration.
    Returns True if successful, False if DB disabled or error.
    Does NOT raise.
    """
    if _db_config() is None:
        return False
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(_TABLE_DDL)
            # Discover existing columns
            cur.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'cv_applications'"
            )
            existing = {row[0] for row in cur.fetchall()}
            for col, defn in _MIGRATION_COLUMNS.items():
                if col not in existing:
                    try:
                        cur.execute(
                            f"ALTER TABLE cv_applications ADD COLUMN `{col}` {defn}"
                        )
                        logger.info("DB migration: added column '%s'", col)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "DB migration: could not add column '%s': %s",
                            col, type(exc).__name__,
                        )
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("DB init failed: %s", type(exc).__name__)
        return False


# ── Helpers ───────────────────────────────────────────────────────────

def _list_to_json(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _row_to_dict(row, cursor) -> dict:
    cols = [d[0] for d in cursor.description]
    result: dict = {}
    for col, val in zip(cols, row):
        if isinstance(val, datetime):
            result[col] = val.isoformat()
        elif hasattr(val, "strftime"):  # date object
            result[col] = val.strftime("%Y-%m-%d")
        else:
            result[col] = val
    return result


def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ── Write operations ──────────────────────────────────────────────────

def save_application(data: dict) -> int | None:
    """
    Inserts a new cv_applications record.

    Returns new row id (int) or None if DB disabled/error.

    Expected keys in data (all optional except status):
      job_posting_date, company_name, job_title, role_type, role_type_label,
      role_type_confidence, job_language, cv_output_language, job_url,
      source_type, status, cv_filename, cv_local_path, cv_remote_path,
      cv_public_url, sent_to_email, sent_at, email_subject,
      contact_person, contact_phone, contact_email, match_score,
      confirmed_strengths (list), gaps (list), transferable_angles (list),
      do_not_claim (list), used_keywords (list), excluded_keywords (list),
      notes, error_message
    """
    if _db_config() is None:
        return None
    try:
        now = _now_str()
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cv_applications (
                    created_at, updated_at,
                    job_posting_date, company_name, job_title,
                    role_type, role_type_label, role_type_confidence,
                    job_language, cv_output_language,
                    job_url, source_type, status,
                    cv_filename, cv_local_path, cv_remote_path, cv_public_url,
                    sent_to_email, sent_at, email_subject,
                    contact_person, contact_phone, contact_email,
                    match_score,
                    confirmed_strengths, gaps, transferable_angles,
                    do_not_claim, used_keywords, excluded_keywords,
                    notes, error_message, candidate_name
                ) VALUES (
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    now, now,
                    data.get("job_posting_date") or None,
                    data.get("company_name", ""),
                    data.get("job_title", ""),
                    data.get("role_type", ""),
                    data.get("role_type_label", ""),
                    data.get("role_type_confidence", ""),
                    data.get("job_language", ""),
                    data.get("cv_output_language", ""),
                    data.get("job_url", ""),
                    data.get("source_type", "kreator"),
                    data.get("status", "generated"),
                    data.get("cv_filename", ""),
                    data.get("cv_local_path", ""),
                    data.get("cv_remote_path", ""),
                    data.get("cv_public_url", ""),
                    data.get("sent_to_email", ""),
                    data.get("sent_at") or None,
                    data.get("email_subject", ""),
                    data.get("contact_person", ""),
                    data.get("contact_phone", ""),
                    data.get("contact_email", ""),
                    data.get("match_score") or None,
                    _list_to_json(data.get("confirmed_strengths")),
                    _list_to_json(data.get("gaps")),
                    _list_to_json(data.get("transferable_angles")),
                    _list_to_json(data.get("do_not_claim")),
                    _list_to_json(data.get("used_keywords")),
                    _list_to_json(data.get("excluded_keywords")),
                    data.get("notes", ""),
                    data.get("error_message", ""),
                    CANDIDATE_NAME,
                ),
            )
            new_id = cur.lastrowid
        conn.close()
        return new_id
    except Exception as exc:  # noqa: BLE001
        logger.error("save_application error: %s", type(exc).__name__)
        return None


def update_status(record_id: int, status: str, **kwargs) -> bool:
    """
    Updates status and optionally extra fields.
    Allowed extra fields: sent_to_email, sent_at, email_subject, error_message,
                          cv_remote_path, cv_public_url.
    Returns True if updated, False otherwise.
    """
    if _db_config() is None:
        return False
    _ALLOWED = {
        "sent_to_email", "sent_at", "email_subject", "error_message",
        "cv_remote_path", "cv_public_url",
    }
    try:
        now = _now_str()
        updates: dict = {"status": status, "updated_at": now}
        for k, v in kwargs.items():
            if k in _ALLOWED:
                updates[k] = v
        set_clause = ", ".join(f"`{k}` = %s" for k in updates)
        values = list(updates.values()) + [record_id]
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE cv_applications SET {set_clause} WHERE id = %s", values
            )
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("update_status error: %s", type(exc).__name__)
        return False


def update_contact(
    record_id: int,
    contact_person: str,
    contact_phone: str,
    contact_email: str,
) -> bool:
    """Updates only contact fields. Returns True if successful."""
    if _db_config() is None:
        return False
    try:
        now = _now_str()
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cv_applications
                SET contact_person=%s, contact_phone=%s, contact_email=%s, updated_at=%s
                WHERE id=%s
                """,
                (contact_person, contact_phone, contact_email, now, record_id),
            )
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("update_contact error: %s", type(exc).__name__)
        return False


def update_notes(record_id: int, notes: str) -> bool:
    """Updates only the notes field. Returns True if successful."""
    if _db_config() is None:
        return False
    try:
        now = _now_str()
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE cv_applications SET notes=%s, updated_at=%s WHERE id=%s",
                (notes, now, record_id),
            )
        conn.close()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("update_notes error: %s", type(exc).__name__)
        return False


# ── Read operations ───────────────────────────────────────────────────

def list_applications(
    status: str | None = None,
    role_type: str | None = None,
    cv_output_language: str | None = None,
    company_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict] | None:
    """
    Returns list of application records with optional filters.
    Returns None if DB disabled. Returns [] if DB enabled but query fails.
    """
    if _db_config() is None:
        return None
    try:
        where_clauses: list[str] = ["candidate_name = %s"]
        params: list = [CANDIDATE_NAME]
        if status:
            where_clauses.append("status = %s")
            params.append(status)
        if role_type:
            where_clauses.append("role_type = %s")
            params.append(role_type)
        if cv_output_language:
            where_clauses.append("cv_output_language = %s")
            params.append(cv_output_language)
        if company_name:
            where_clauses.append("company_name LIKE %s")
            params.append(f"%{company_name}%")
        if date_from:
            where_clauses.append("DATE(created_at) >= %s")
            params.append(date_from)
        if date_to:
            where_clauses.append("DATE(created_at) <= %s")
            params.append(date_to)
        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM cv_applications {where} ORDER BY created_at DESC",
                params,
            )
            rows = [_row_to_dict(row, cur) for row in cur.fetchall()]
        conn.close()
        return rows
    except Exception as exc:  # noqa: BLE001
        logger.error("list_applications error: %s", type(exc).__name__)
        return []


def get_application(record_id: int) -> dict | None:
    """Returns a single record by id, or None if not found or DB disabled."""
    if _db_config() is None:
        return None
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM cv_applications WHERE id = %s AND candidate_name = %s",
                (record_id, CANDIDATE_NAME),
            )
            row = cur.fetchone()
            result = _row_to_dict(row, cur) if row else None
        conn.close()
        return result
    except Exception as exc:  # noqa: BLE001
        logger.error("get_application error: %s", type(exc).__name__)
        return None


def get_stats() -> dict:
    """Returns aggregate stats. Returns empty dict if DB disabled or error."""
    if _db_config() is None:
        return {}
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            _cn = CANDIDATE_NAME
            cur.execute("SELECT COUNT(*) FROM cv_applications WHERE candidate_name = %s", (_cn,))
            total = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM cv_applications WHERE status = 'sent' AND candidate_name = %s", (_cn,))
            sent = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM cv_applications "
                "WHERE YEAR(created_at)=YEAR(NOW()) AND MONTH(created_at)=MONTH(NOW()) AND candidate_name = %s",
                (_cn,),
            )
            this_month = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM cv_applications WHERE cv_output_language = 'en-US' AND candidate_name = %s",
                (_cn,),
            )
            en_us = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM cv_applications WHERE cv_output_language = 'pl' AND candidate_name = %s",
                (_cn,),
            )
            pl_count = cur.fetchone()[0]

            cur.execute(
                "SELECT role_type, COUNT(*) AS cnt FROM cv_applications "
                "WHERE role_type IS NOT NULL AND role_type != '' AND candidate_name = %s "
                "GROUP BY role_type ORDER BY cnt DESC LIMIT 1",
                (_cn,),
            )
            row = cur.fetchone()
            top_role = row[0] if row else ""

            cur.execute(
                "SELECT COUNT(*) FROM cv_applications "
                "WHERE status IN ('send_error', 'storage_error') AND candidate_name = %s",
                (_cn,),
            )
            errors = cur.fetchone()[0]

        conn.close()
        return {
            "total":         total,
            "sent":          sent,
            "this_month":    this_month,
            "en_us":         en_us,
            "pl":            pl_count,
            "top_role_type": top_role,
            "errors":        errors,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("get_stats error: %s", type(exc).__name__)
        return {}


# ── CSV export ─────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "id", "created_at", "updated_at", "company_name", "job_title",
    "role_type", "role_type_label", "role_type_confidence",
    "job_language", "cv_output_language", "job_url", "source_type", "status",
    "cv_filename", "cv_local_path", "cv_remote_path", "cv_public_url",
    "sent_to_email", "sent_at", "email_subject",
    "contact_person", "contact_phone", "contact_email",
    "match_score",
    "confirmed_strengths", "gaps", "transferable_angles",
    "do_not_claim", "used_keywords", "excluded_keywords",
    "notes", "error_message",
]


def export_csv() -> str:
    """
    Returns CSV string of all records.
    If DB disabled, returns only the header row.
    """
    import csv
    import io

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=CSV_COLUMNS,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()

    rows = list_applications()
    if rows:
        for row in rows:
            writer.writerow({k: (row.get(k) or "") for k in CSV_COLUMNS})

    return output.getvalue()
