"""
History module for Kreator CV.
Stores generated CV sessions locally in data/history.json.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

HISTORY_PATH = Path(__file__).parent.parent / "data" / "history.json"


def _load() -> list:
    if not HISTORY_PATH.exists():
        return []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save(entries: list) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def add_entry(
    company: str,
    job_title: str,
    match_score: int,
    filename: str,
    job_text_preview: str = "",
    job_url: str = "",
    ats_keywords_count: int = 0,
) -> str:
    """
    Saves a new history entry.

    Returns:
        Entry ID (8-char hex).
    """
    entries = _load()
    entry_id = uuid.uuid4().hex[:8]
    entries.append({
        "id":                 entry_id,
        "timestamp":          datetime.now().isoformat(),
        "date":               datetime.now().strftime("%Y-%m-%d"),
        "company":            company,
        "job_title":          job_title,
        "match_score":        match_score,
        "filename":           filename,
        "job_url":            job_url,
        "job_text_preview":   job_text_preview[:200],
        "ats_keywords_count": ats_keywords_count,
    })
    _save(entries)
    return entry_id


def get_all() -> list:
    """Returns all history entries, newest first."""
    return list(reversed(_load()))


def delete_entry(entry_id: str) -> bool:
    """Deletes entry by ID. Returns True if found and deleted."""
    entries = _load()
    new_entries = [e for e in entries if e.get("id") != entry_id]
    if len(new_entries) == len(entries):
        return False
    _save(new_entries)
    return True
