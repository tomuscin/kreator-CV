"""
Job posting scraper for Kreator CV.
Supports: URL fetch (with fallback) and raw text paste.
"""

import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}

TIMEOUT = 15  # seconds


def fetch_job_posting(url: str) -> str:
    """
    Fetches and extracts plain text from a job posting URL.

    Tries to extract the main content block. Falls back to full
    page text if specific selectors are not found.

    Args:
        url: URL of the job posting.

    Returns:
        Cleaned plain text of the job posting.

    Raises:
        ValueError: If URL is invalid or fetching fails.
        httpx.HTTPStatusError: On HTTP errors.
    """
    _validate_url(url)

    try:
        with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"Nie można pobrać ogłoszenia (HTTP {exc.response.status_code}): {url}") from exc
    except httpx.RequestError as exc:
        raise ValueError(f"Błąd połączenia z {url}: {exc}") from exc

    return _extract_text(response.text, url)


def _extract_text(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Remove noise elements
    for tag in soup(["script", "style", "nav", "header", "footer",
                     "aside", "iframe", "noscript", "meta", "link"]):
        tag.decompose()

    domain = urlparse(url).netloc.lower()

    # Site-specific selectors
    selectors = _get_selectors(domain)
    for selector in selectors:
        block = soup.select_one(selector)
        if block:
            return _clean_text(block.get_text(separator="\n"))

    # Fallback: longest <div> / <article> / <section>
    candidates = soup.find_all(["article", "section", "div", "main"])
    if candidates:
        best = max(candidates, key=lambda t: len(t.get_text()))
        return _clean_text(best.get_text(separator="\n"))

    return _clean_text(soup.get_text(separator="\n"))


def _get_selectors(domain: str) -> list[str]:
    mapping = {
        "pracuj.pl":         ["[data-test='offer-details']", ".offer-content", "#offer-body"],
        "linkedin.com":      [".description__text", ".show-more-less-html__markup"],
        "nofluffjobs.com":   ["#job-description", ".posting-details-description"],
        "justjoin.it":       [".css-p1glgt", "[class*='offer']"],
        "olx.pl":            [".offer-description", "[data-testid='ad-description-text']"],
        "indeed.com":        ["#jobDescriptionText"],
        "theprotocol.it":    [".OfferDescription", ".description"],
        "hellowork.com":     [".offer-description"],
        "bulldogjob.pl":     [".job-description"],
    }
    for key, selectors in mapping.items():
        if key in domain:
            return selectors
    return []


def _clean_text(text: str) -> str:
    lines = text.splitlines()
    cleaned = [line.strip() for line in lines if line.strip()]
    # Remove repeated blank lines
    result = []
    prev_blank = False
    for line in cleaned:
        if not line:
            if not prev_blank:
                result.append("")
            prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    return "\n".join(result).strip()


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Nieprawidłowy URL: {url}. Musi zaczynać się od http:// lub https://")
    if not parsed.netloc:
        raise ValueError(f"Nieprawidłowy URL: {url}")
