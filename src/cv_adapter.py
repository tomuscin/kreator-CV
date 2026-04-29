"""
CV Adapter — LLM-based CV customization for Kreator CV.

Rules enforced in the prompt:
- LLM is EDITOR, not author
- Must NOT invent jobs, education, or achievements
- Must NOT change dates, company names, or job titles
- May rephrase, reorder, and emphasize existing content
- Enforces character limits per field
"""

import json
import sys
from pathlib import Path

# openai_client.py lives at project root (works both locally and on Render)
sys.path.insert(0, str(Path(__file__).parent.parent))
from openai_client import chat  # noqa: E402

MASTER_CV_PATH = Path(__file__).parent.parent / "data" / "master_cv.json"

# ── Character limits (enforced in prompts + frontend) ────────────────
CHAR_LIMITS = {
    "summary":           900,
    "competencies":      600,
    "bullet":            200,
}

SYSTEM_PROMPT = """
Jesteś ekspertem od pisania CV pod kątem ATS i rekruterów.
Twoja rola: REDAKTOR, nie autor.

ZASADY (bezwzględne):
1. NIE dodajesz nowych stanowisk, firm, projektów ani osiągnięć.
2. NIE zmieniasz dat, nazw stanowisk ani wykształcenia.
3. NIE kłamiesz ani nie zmyślasz doświadczeń.
4. MOŻESZ: przeformułować treść, zmienić kolejność punktów, podkreślić inne aspekty, dostosować język do ogłoszenia.
5. Wszystkie treści MUSZĄ wynikać z materiału źródłowego (master CV).
6. Optymalizuj pod ATS — używaj słów kluczowych z ogłoszenia tam, gdzie pasują do realnego doświadczenia.

LIMITY ZNAKÓW (bezwzględne — nie przekraczaj):
- Podsumowanie zawodowe: max 900 znaków
- Lista kompetencji (łącznie wszystkie): max 600 znaków
- Każdy punkt w doświadczeniu (każde bullet): max 200 znaków
Jeśli tekst przekroczyłby limit — skróć, zachowując kluczowe informacje i słowa ATS.

ANALIZA LUK / NIEDOPASOWANIA (experience_gap_analysis):
Po wygenerowaniu sekcji CV przygotuj krótką analizę luk dla użytkownika. Zawiera:
- title: tytuł sekcji — "Niedopasowanie doświadczenia do ogłoszenia" (pl) lub "Experience gaps vs. job posting" (en-US).
- text: jeden krótki, naturalny akapit (3-6 zdań) opisujący, które wymagania ogłoszenia profil kandydata spełnia, a których nie potwierdza wprost. Ton: neutralny, praktyczny, profesjonalny. Nie krytykuj kandydata.
- confirmed_strengths: lista obszarów wyraźnie potwierdzonych w profilu i pasujących do ogłoszenia.
- gaps: lista wymagań z ogłoszenia, których profil nie potwierdza wprost.
- transferable_angles: lista pokrewnych doświadczeń, które można bezpiecznie zaakcentować jako substytut.
- do_not_claim: lista elementów, których LLM nie powinien dopisywać jako faktów (bo ich nie ma w profilu bazowym).
Ta analiza służy tylko użytkownikowi w UI przed wysyłką CV. NIE wstawiaj do sekcji CV.
Przy poprawianiu CV (revise): zachowaj lub zaktualizuj experience_gap_analysis.

Odpowiedź zawsze w formacie JSON, zgodnie ze schematem wyjściowym.
Język: polski.
""".strip()

OUTPUT_SCHEMA = {
    "summary": "string — profil zawodowy (max 900 znaków)",
    "competencies": "list[string] — lista kompetencji (max 14 pozycji, łącznie max 600 znaków)",
    "experience": [
        {
            "title": "string — bez zmian z matki",
            "dates": "string — bez zmian z matki",
            "bullets": "list[string] — każdy punkt max 200 znaków"
        }
    ],
    "ats_keywords": "list[string] — słowa kluczowe z ogłoszenia pasujące do CV",
    "match_score": "int 0-100 — ocena dopasowania ogłoszenia do profilu",
    "match_notes": "string — krótkie uzasadnienie (2-3 zdania)",
    "covered_requirements": "list[string] — wymagania z ogłoszenia dobrze pokryte przez CV (max 5)",
    "gaps": "list[string] — czego brakuje lub jest słabo pokryte (max 5)",
    "ats_report": {
        "used": [{"keyword": "string", "locations": ["Podsumowanie | Kompetencje | Doświadczenie — {title}"]}],
        "not_used": [{"keyword": "string", "reason": "string — dlaczego nie użyto"}]
    },
    "company": "string — nazwa firmy z ogłoszenia (lub '' jeśli brak)",
    "job_title": "string — stanowisko z ogłoszenia",
    "experience_gap_analysis": {
        "title":               "string — 'Niedopasowanie doświadczenia do ogłoszenia' (pl) lub 'Experience gaps vs. job posting' (en-US)",
        "text":                "string — akapit 3-6 zdań: co potwierdzone, czego brak, co transferowalne",
        "confirmed_strengths": "list[string] — obszary wyraźnie potwierdzone w profilu",
        "gaps":                "list[string] — wymagania ogłoszenia niepotwierdzone w profilu",
        "transferable_angles": "list[string] — pokrewne doświadczenia jako substytut",
        "do_not_claim":        "list[string] — czego nie dopisywać jako faktu",
    },
}


def _validate_gap_analysis(adapted: dict) -> dict:
    """
    Validates and normalises the experience_gap_analysis field from LLM response.
    Returns a safe dict with all expected sub-fields.
    """
    raw = adapted.get("experience_gap_analysis", {})
    if not isinstance(raw, dict):
        raw = {}

    def _str(key: str) -> str:
        v = raw.get(key, "")
        return v if isinstance(v, str) else ""

    def _list(key: str) -> list:
        v = raw.get(key, [])
        return v if isinstance(v, list) else []

    return {
        "title":               _str("title") or "Niedopasowanie doświadczenia do ogłoszenia",
        "text":                _str("text"),
        "confirmed_strengths": _list("confirmed_strengths"),
        "gaps":                _list("gaps"),
        "transferable_angles": _list("transferable_angles"),
        "do_not_claim":        _list("do_not_claim"),
    }


def adapt_cv(job_posting: str, master_cv: dict | None = None) -> dict:
    """
    Adapts the master CV to a specific job posting using LLM.

    Args:
        job_posting: Full text of the job posting.
        master_cv:   Optional override of master CV data.
                     If None, loads from data/master_cv.json.

    Returns:
        Dict with adapted CV content plus analysis metadata.

    Raises:
        ValueError: If LLM response cannot be parsed as JSON.
        RuntimeError: If no model in the fallback chain responds.
    """
    if master_cv is None:
        with open(MASTER_CV_PATH, encoding="utf-8") as f:
            master_cv = json.load(f)

    user_message = f"""
## MATKA CV (źródło prawdy — nie modyfikuj struktury):
{json.dumps(master_cv, ensure_ascii=False, indent=2)}

## OGŁOSZENIE REKRUTACYJNE:
{job_posting}

## ZADANIE:
Dostosuj treść CV do tego ogłoszenia. Zachowaj wszystkie stanowiska, daty i fakty.
Podkreśl doświadczenia i kompetencje najbardziej relevantne dla tej roli.
Używaj słów kluczowych z ogłoszenia tam, gdzie naturalnie pasują.
PILNUJ LIMITÓW ZNAKÓW — podsumowanie max 900, kompetencje łącznie max 600, każdy bullet max 200.

Po wygenerowaniu CV, przeanalizuj:
- które słowa kluczowe ATS z ogłoszenia znalazły się w CV i gdzie (ats_report.used),
- których nie użyto i dlaczego — bo nie ma pokrycia w matce CV (ats_report.not_used),
- które wymagania z ogłoszenia są dobrze pokryte przez CV (covered_requirements),
- jakie luki istnieją między ogłoszeniem a profilem (gaps),
- wyodrębnij nazwę firmy i stanowisko z ogłoszenia,
- przygotuj experience_gap_analysis: krótki akapit + listy confirmed_strengths, gaps, transferable_angles, do_not_claim.

Zwróć TYLKO poprawny JSON zgodny z tym schematem:
{json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2)}
""".strip()

    response = chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        max_completion_tokens=5000,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        adapted = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM zwrócił niepoprawny JSON: {exc}\n\nRaw:\n{raw}") from exc

    result = {
        "personal":            master_cv["personal"],
        "education":           master_cv["education"],
        "languages":           master_cv["languages"],
        "interests":           master_cv.get("interests", ""),
        "rodo_clause":         master_cv.get("rodo_clause", ""),
        "summary":             adapted.get("summary", master_cv["summary"]),
        "competencies":        adapted.get("competencies", master_cv["competencies"]),
        "experience":          _merge_experience(master_cv["experience"], adapted.get("experience", [])),
        "ats_keywords":        adapted.get("ats_keywords", []),
        "match_score":         adapted.get("match_score", 0),
        "match_notes":         adapted.get("match_notes", ""),
        "covered_requirements": adapted.get("covered_requirements", []),
        "gaps":                adapted.get("gaps", []),
        "ats_report":          adapted.get("ats_report", {"used": [], "not_used": []}),
        "company":             adapted.get("company", ""),
        "job_title":           adapted.get("job_title", ""),
        "experience_gap_analysis": _validate_gap_analysis(adapted),
    }

    return result


def _merge_experience(master_exp: list, adapted_exp: list) -> list:
    """Merges adapted bullets into master entries. Title + dates locked."""
    adapted_map = {e.get("title", ""): e for e in adapted_exp}
    merged = []
    for orig in master_exp:
        title = orig["title"]
        if title in adapted_map:
            merged.append({
                "title":   orig["title"],
                "dates":   orig["dates"],
                "bullets": adapted_map[title].get("bullets", orig["bullets"]),
            })
        else:
            merged.append(orig)
    return merged


def analyze_job_posting(job_posting: str) -> dict:
    """
    Analyzes job posting and extracts key requirements.

    Returns:
        Dict with: required_experience, responsibilities, technologies,
        ats_keywords, tone, priority_competencies, company, job_title.
    """
    response = chat(
        messages=[
            {
                "role": "system",
                "content": "Jesteś ekspertem HR. Analizujesz ogłoszenia rekrutacyjne. Odpowiadaj w JSON. Język: polski."
            },
            {
                "role": "user",
                "content": f"""Przeanalizuj to ogłoszenie i zwróć JSON:
{{
  "required_experience": ["lista wymagań doświadczeniowych"],
  "responsibilities": ["lista głównych obowiązków"],
  "technologies": ["narzędzia, systemy, platformy"],
  "ats_keywords": ["kluczowe słowa pod ATS"],
  "tone": "opis tonu ogłoszenia (1 zdanie)",
  "priority_competencies": ["top 5 kompetencji oczekiwanych przez pracodawcę"],
  "company": "nazwa firmy (lub '' jeśli brak)",
  "job_title": "stanowisko z ogłoszenia"
}}

OGŁOSZENIE:
{job_posting}"""
            }
        ],
        max_completion_tokens=1500,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def revise_field(
    field_name: str,
    current_text: str,
    user_comment: str,
    job_posting: str,
    char_limit: int,
) -> str:
    """
    Revises a single CV field based on user comment, respecting char limit.

    Args:
        field_name:   Human-readable name of the field (e.g. "Podsumowanie").
        current_text: Current field content.
        user_comment: User's instruction (e.g. "Skróć", "Podkreśl Agile").
        job_posting:  Job posting for context.
        char_limit:   Maximum character count for the field.

    Returns:
        Revised text as a plain string (no JSON wrapper).
    """
    response = chat(
        messages=[
            {
                "role": "system",
                "content": f"""Jesteś ekspertem od CV. Edytujesz jedno pole CV na prośbę użytkownika.

ZASADY:
- Zachowaj fakty, stanowiska, daty — nie wymyślaj nowych doświadczeń
- Uwzględnij komentarz użytkownika
- Nie przekraczaj {char_limit} znaków (BEZWZGLĘDNY LIMIT)
- Zwróć TYLKO poprawiony tekst, bez komentarza, bez cudzysłowów
- Język: polski

Pole: {field_name}
Limit znaków: {char_limit}"""
            },
            {
                "role": "user",
                "content": f"""Komentarz: {user_comment}

Obecny tekst ({len(current_text)} znaków):
{current_text}

Kontekst ogłoszenia (fragment):
{job_posting[:800]}

Zwróć TYLKO poprawiony tekst (max {char_limit} znaków)."""
            }
        ],
        max_completion_tokens=600,
    )

    revised = response.choices[0].message.content.strip()
    # Strip surrounding quotes if LLM wrapped it
    if revised.startswith('"') and revised.endswith('"'):
        revised = revised[1:-1]
    return revised


def revise_full_cv(current_cv: dict, instruction: str, job_posting: str) -> dict:
    """
    Revises the entire adapted CV based on a free-form user instruction.

    The structure (personal, education, languages) is preserved.
    Only summary, competencies, and experience bullets may change.

    Args:
        current_cv:   Current adapted CV dict (as rendered in the editor).
        instruction:  User's free-form instruction (e.g. "Podkreśl marketing").
        job_posting:  Job posting for ATS context.

    Returns:
        Updated CV dict (same structure as adapt_cv output).
    """
    cv_snapshot = {
        "summary":      current_cv.get("summary", ""),
        "competencies": current_cv.get("competencies", []),
        "experience":   [
            {
                "title":   job["title"],
                "dates":   job["dates"],
                "bullets": job.get("bullets", []),
            }
            for job in current_cv.get("experience", [])
        ],
    }

    schema = {
        "summary":      "string — poprawione podsumowanie (max 900 znaków)",
        "competencies": "list[string] — poprawiona lista kompetencji (max 14, łącznie max 600 znaków)",
        "experience": [
            {
                "title":   "string — BEZ ZMIAN z wejścia",
                "dates":   "string — BEZ ZMIAN z wejścia",
                "bullets": "list[string] — poprawione punkty, każdy max 200 znaków",
            }
        ],
    }

    user_message = f"""
## OBECNA TREŚĆ CV (do poprawy):
{json.dumps(cv_snapshot, ensure_ascii=False, indent=2)}

## OGŁOSZENIE (kontekst ATS):
{job_posting[:1500]}

## INSTRUKCJA UŻYTKOWNIKA:
{instruction}

## ZASADY (bezwzględne):
- NIE zmieniaj title ani dates w experience — skopiuj dokładnie z wejścia
- NIE dodawaj nowych stanowisk, firm ani osiągnięć których nie ma w wejściu
- Zastosuj instrukcję użytkownika do treści
- LIMITY: podsumowanie max 900 znaków, kompetencje łącznie max 600, każdy bullet max 200
- Zwróć TYLKO poprawny JSON zgodny ze schematem poniżej:
{json.dumps(schema, ensure_ascii=False, indent=2)}
""".strip()

    response = chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        max_completion_tokens=4000,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        revised = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM zwrócił niepoprawny JSON: {exc}\n\nRaw:\n{raw}") from exc

    # Merge back — lock structure from current_cv, update editable fields only
    result = dict(current_cv)
    result["summary"]      = revised.get("summary", current_cv.get("summary", ""))
    result["competencies"] = revised.get("competencies", current_cv.get("competencies", []))
    result["experience"]   = _merge_experience(
        current_cv.get("experience", []),
        revised.get("experience", []),
    )
    # Preserve existing gap analysis — revise_full_cv doesn’t regenerate it
    result["experience_gap_analysis"] = current_cv.get("experience_gap_analysis", {})
    return result
