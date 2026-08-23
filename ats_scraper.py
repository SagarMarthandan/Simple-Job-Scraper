#!/usr/bin/env python3
"""
ATS Direct Scraper — fetches jobs directly from company career pages via public ATS APIs.

Platforms supported (3 confirmed working public APIs):
  - Greenhouse:       boards-api.greenhouse.io/v1/boards/{slug}/jobs  (JSON, no auth)
  - SmartRecruiters:  api.smartrecruiters.com/v1/companies/{slug}/postings  (JSON, no auth)
  - Ashby:            jobs.ashbyhq.com/{slug}  (HTML with embedded window.__appData JSON)

Lever was tested but dropped — no companies with open jobs found via the public API
(api.lever.co/v0/postings/{slug}?mode=json returns 404 for all tested slugs).

Each fetcher returns a list of dicts matching the pipeline's job schema:
  {language, job_board, role_type, title, company, location, posted_at,
   exp_required, match_score, job_url, description}

Filters applied: title relevance (data/analytics/AI), seniority ceiling (<=2y),
                 Germany location guard, 24h freshness, role type classification.

Cost: $0 (all public APIs, no Apify, no auth).
"""

import json
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("ats_scraper")

# ── Constants ──────────────────────────────────────────────────────────────────

FRESHNESS_HOURS = 24
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Seniority ceiling — same as pipeline
EXCLUDED_TITLE_PATTERNS = re.compile(
    r"\b(senior|sr|lead|head|principal|staff|manager|director|architect|vp|chief|expert|team lead)\b",
    re.IGNORECASE
)

EXCLUDED_EXP_PATTERNS = [
    re.compile(r"\b([3-9]|\d{2,})\+?\s*(?:years?|jahre?|j\.?)\b", re.IGNORECASE),
    re.compile(r"\b(?:at least|minimum|min\.?|mindestens)\s*([3-9]|\d{2,})\s*(?:years?|jahre?)\b", re.IGNORECASE),
]

# Title relevance — same as pipeline
DOMAIN_TITLE_KEYWORDS = re.compile(
    r"\b(data|daten\w*|analytics|analyst\w*|ai|artificial intelligence|"
    r"bi|business intelligence|sql|python|database|datenbank\w*|"
    r"machine learning|ml|deep learning|etl|pipeline|warehouse|"
    r"datapipeline|dataops|mlops|llm|genai)\b",
    re.IGNORECASE
)

TECH_KEYWORDS = ["dbt", "airflow", "spark", "pyspark", "python", "sql", "gcp", "bigquery",
                 "aws", "azure", "databricks", "docker", "kafka", "postgresql", "snowflake"]

NON_GERMANY_COUNTRIES = (
    "austria", "österreich", "switzerland", "schweiz", "suisse",
    "netherlands", "niederlande", "holland",
    "france", "frankreich",
    "united kingdom", "england", "scotland", "wales",
    "ireland", "irland",
    "poland", "polen",
    "czech", "tschech",
    "spain", "spanien", "españa",
    "italy", "italien", "italia",
    "portugal",
    "belgium", "belgien",
    "sweden", "schweden",
    "norway", "norwegen",
    "denmark", "dänemark",
    "finland", "finnland",
    "united states", "usa",
    "canada", "india", "indien",
    "luxembourg", "luxemburg",
    "liechtenstein",
    "romania", "rumänien",
    "hungary", "ungarn",
)

# ── Curated German company slugs ───────────────────────────────────────────────
# Companies with offices in Germany that hire data/analytics/AI roles.
# Slugs verified against live APIs on 2026-08-23.

GREENHOUSE_SLUGS = [
    "n26",            # N26 — Berlin, fintech (7 data jobs in DE)
    "getyourguide",   # GetYourGuide — Berlin (9 data jobs in DE)
    "contentful",     # Contentful — Berlin
    "celonis",        # Celonis — Munich, process mining (17 data jobs in DE)
    "dataiku",        # Dataiku — Paris/Berlin
    "databricks",     # Databricks — Munich/remote (2 data jobs in DE)
    "mongodb",        # MongoDB — Berlin/remote
    "gitlab",         # GitLab — remote
    "vercel",         # Vercel — remote EU
    "planetscale",    # PlanetScale — remote
]

SMARTRECRUITERS_SLUGS = [
    "BoschGroup",     # Robert Bosch GmbH — 100+ postings (verified)
    "DeliveryHero",   # Delivery Hero — Berlin, 100+ postings (verified)
    "Continental",    # Continental AG — 100+ postings (verified)
    "Thales",         # Thales Group — Düsseldorf (verified, 2 postings)
]

ASHBY_SLUGS = [
    "supabase",       # Supabase — remote EU (3 data jobs, verified)
    "linear",         # Linear — remote (1 data job, verified)
    "vercel",         # Vercel — remote EU
]


# ── Shared filter functions (mirror pipeline) ─────────────────────────────────

def _is_relevant_title(title: str) -> bool:
    return bool(DOMAIN_TITLE_KEYWORDS.search(title))


def _detect_language(text: str) -> str:
    text_lower = text.lower()
    german_indicators = ["und", "die", "das", "der", "mit", "für", "aufgaben", "profil",
                         "kenntnisse", "anforderungen", "ihre", "wir"]
    english_indicators = ["and", "the", "with", "for", "responsibilities", "requirements",
                          "skills", "your", "we", "looking"]
    de_score = sum(len(re.findall(r"\b" + w + r"\b", text_lower)) for w in german_indicators)
    en_score = sum(len(re.findall(r"\b" + w + r"\b", text_lower)) for w in english_indicators)
    return "German" if de_score > en_score else "English"


def _classify_role_type(title: str, description: str) -> str:
    combined = f"{title} {description}".lower()
    if re.search(r"\b(werkstudent|werkstudentin|working student)\b", combined):
        return "Working Student"
    elif re.search(r"\b(intern|internship|praktikum|praktikant|praktikantin)\b", combined):
        return "Internship"
    else:
        return "Full-Time / Entry-Level"


def _check_experience_and_location(title: str, description: str, location: str) -> tuple[bool, str]:
    """Returns (is_valid, role_type_or_reason). Same logic as pipeline."""
    if not _is_relevant_title(title):
        return False, "Title not relevant to data/analytics/AI"

    if EXCLUDED_TITLE_PATTERNS.search(title):
        return False, "Seniority title excluded"

    for pattern in EXCLUDED_EXP_PATTERNS:
        if pattern.search(description):
            return False, "Requires >2 years experience"

    role_type = _classify_role_type(title, description)
    loc_clean = location.lower()

    if "germany" not in loc_clean and "deutschland" not in loc_clean:
        for country in NON_GERMANY_COUNTRIES:
            if country in loc_clean:
                return False, f"Location outside Germany ({location})"

    if role_type == "Working Student":
        if not ("hamburg" in loc_clean or "kiel" in loc_clean):
            return False, f"Working student outside Hamburg/Kiel ({location})"

    return True, role_type


def _compute_match_score(text: str) -> int:
    text_lower = text.lower()
    matches = sum(1 for kw in TECH_KEYWORDS if kw in text_lower)
    return min(100, int((matches / len(TECH_KEYWORDS)) * 100 * 2.5))


def _parse_date_flexible(date_str: str) -> Optional[datetime]:
    """Parse various date formats, return UTC datetime or None."""
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass
    return None


def _is_fresh(posted_dt: Optional[datetime], cutoff: datetime) -> bool:
    """Check if job is within freshness window.
    None = no date available — include the job (let title/seniority filters handle it,
    cross-run dedup will catch repeats on subsequent runs)."""
    if posted_dt is None:
        return True
    # Date-only timestamps: treat as end-of-day (generous, matches pipeline convention)
    if posted_dt.hour == 0 and posted_dt.minute == 0:
        posted_dt = posted_dt.replace(hour=23, minute=59, second=59)
    return posted_dt >= cutoff


def _make_job_dict(title: str, company: str, location: str, posted_at: str,
                   url: str, description: str, ats_name: str) -> Optional[dict]:
    """Apply all pipeline filters and return a job dict, or None if filtered out."""
    is_valid, role_type = _check_experience_and_location(title, description, location)
    if not is_valid:
        return None

    return {
        "language": _detect_language(f"{title} {description}"),
        "job_board": ats_name,
        "role_type": role_type,
        "title": title,
        "company": company,
        "location": location,
        "posted_at": posted_at or "Last 24h",
        "exp_required": "<= 2 Years",
        "match_score": f"{_compute_match_score(f'{title} {description}')}%",
        "job_url": url,
        "description": description[:500],
    }


# ── Greenhouse ─────────────────────────────────────────────────────────────────

def fetch_greenhouse(slug: str) -> list[dict]:
    """Fetch jobs from Greenhouse public API for a company slug.
    Uses first_published (not updated_at) for freshness — updated_at reflects
    last modification, not posting date."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        resp = requests.get(url, timeout=15, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.debug(f"Greenhouse {slug}: fetch failed ({exc})")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=FRESHNESS_HOURS)
    company_name = slug.replace("-", " ").title()
    jobs = []
    for job in data.get("jobs", []):
        title = job.get("title", "")
        loc_data = job.get("location", {})
        loc = loc_data.get("name", "Germany") if isinstance(loc_data, dict) else str(loc_data or "Germany")

        # Use first_published for freshness (posting date), not updated_at
        posted_raw = job.get("first_published", "") or job.get("updated_at", "")
        posted_dt = _parse_date_flexible(posted_raw)
        if not _is_fresh(posted_dt, cutoff):
            continue

        job_id = job.get("id", "")
        job_url = job.get("absolute_url", "") or f"https://boards.greenhouse.io/{slug}/jobs/{job_id}"

        # Greenhouse API doesn't include full descriptions in the listing endpoint
        desc = title  # minimal — title-based filters still work

        job_dict = _make_job_dict(title, company_name, loc,
                                  posted_raw[:10] if posted_raw else "", job_url, desc, "Greenhouse")
        if job_dict:
            jobs.append(job_dict)

    log.debug(f"Greenhouse {slug}: {len(jobs)} jobs after filters")
    return jobs


# ── SmartRecruiters ────────────────────────────────────────────────────────────

def fetch_smartrecruiters(slug: str) -> list[dict]:
    """Fetch jobs from SmartRecruiters public API for a company slug.
    API returns max 100 per call; uses offset for pagination."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=FRESHNESS_HOURS)
    company_name = slug.replace("-", " ").title()
    jobs = []
    offset = 0

    while True:
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}"
        try:
            resp = requests.get(url, timeout=15, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.debug(f"SmartRecruiters {slug}: fetch failed at offset={offset} ({exc})")
            break

        content = data.get("content", [])
        if not content:
            break

        for posting in content:
            title = posting.get("title", "")
            loc_data = posting.get("location", {})
            if isinstance(loc_data, dict):
                loc = loc_data.get("city", "")
                country = loc_data.get("country", "")
                if country and isinstance(country, dict):
                    loc = f"{loc}, {country.get('label', '')}"
                elif country:
                    loc = f"{loc}, {country}"
            else:
                loc = str(loc_data) or "Germany"

            posted_raw = posting.get("releasedDate", "") or posting.get("createdDate", "")
            posted_dt = _parse_date_flexible(posted_raw)
            if not _is_fresh(posted_dt, cutoff):
                continue

            job_url = posting.get("applyUrl", "") or posting.get("url", "")
            if not job_url and posting.get("id"):
                job_url = f"https://jobs.smartrecruiters.com/{slug}/{posting['id']}"

            desc = ""
            job_ad = posting.get("jobAd", {})
            if isinstance(job_ad, dict):
                sections = job_ad.get("sections", {})
                if isinstance(sections, dict):
                    desc_section = sections.get("jobDescription", {})
                    desc = desc_section.get("text", "") if isinstance(desc_section, dict) else ""
            if desc and "<" in desc:
                desc = BeautifulSoup(desc, "html.parser").get_text(strip=True)

            job_dict = _make_job_dict(title, company_name, loc,
                                      posted_raw[:10] if posted_raw else "", job_url, desc, "SmartRecruiters")
            if job_dict:
                jobs.append(job_dict)

        if len(content) < 100:
            break  # last page
        offset += 100

    log.debug(f"SmartRecruiters {slug}: {len(jobs)} jobs after filters")
    return jobs


# ── Ashby ──────────────────────────────────────────────────────────────────────

def fetch_ashby(slug: str) -> list[dict]:
    """Fetch jobs from Ashby by parsing embedded window.__appData JSON from the careers page.
    Some companies (e.g. PostHog) load job data asynchronously via JS — their __appData
    has jobBoard=None. Only companies with pre-rendered data work with this approach."""
    url = f"https://jobs.ashbyhq.com/{slug}"
    try:
        resp = requests.get(url, timeout=15, headers=HEADERS)
        resp.raise_for_status()
    except Exception as exc:
        log.debug(f"Ashby {slug}: fetch failed ({exc})")
        return []

    # Extract window.__appData = {...};
    idx = resp.text.find("window.__appData")
    if idx < 0:
        log.debug(f"Ashby {slug}: no __appData found")
        return []

    start = resp.text.find("{", idx)
    if start < 0:
        return []
    # Brace-matching to find the complete JSON object
    depth = 0
    end = start
    for i in range(start, len(resp.text)):
        c = resp.text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        if depth == 0:
            end = i + 1
            break

    try:
        app_data = json.loads(resp.text[start:end])
    except json.JSONDecodeError:
        log.debug(f"Ashby {slug}: JSON parse failed")
        return []

    job_board = app_data.get("jobBoard")
    if not job_board or not isinstance(job_board, dict):
        log.debug(f"Ashby {slug}: jobBoard is None (JS-rendered page, skipping)")
        return []

    org = app_data.get("organization") or {}
    company_name = org.get("name", slug.replace("-", " ").title())
    job_postings = job_board.get("jobPostings", [])

    cutoff = datetime.now(timezone.utc) - timedelta(hours=FRESHNESS_HOURS)
    jobs = []
    for posting in job_postings:
        title = posting.get("title", "")
        loc = posting.get("locationName", "Germany") or "Germany"
        posted_raw = posting.get("publishedDate", "") or posting.get("createdAt", "")
        posted_dt = _parse_date_flexible(posted_raw)
        if not _is_fresh(posted_dt, cutoff):
            continue

        job_id = posting.get("id", "")
        job_url = f"https://jobs.ashbyhq.com/{slug}/{job_id}"

        # Ashby doesn't include full description in the listing
        desc = title  # minimal — title-based filters still work

        job_dict = _make_job_dict(title, company_name, loc,
                                  posted_raw[:10] if posted_raw else "", job_url, desc, "Ashby")
        if job_dict:
            jobs.append(job_dict)

    log.debug(f"Ashby {slug}: {len(jobs)} jobs after filters")
    return jobs


# ── Orchestrator ───────────────────────────────────────────────────────────────

# Map platform name → (fetcher function, slug list)
_FETCHERS = {
    "Greenhouse": (fetch_greenhouse, GREENHOUSE_SLUGS),
    "SmartRecruiters": (fetch_smartrecruiters, SMARTRECRUITERS_SLUGS),
    "Ashby": (fetch_ashby, ASHBY_SLUGS),
}


def fetch_all_ats() -> list[dict]:
    """Fetch jobs from all ATS platforms with all curated company slugs.
    Returns a deduplicated list of job dicts matching the pipeline schema."""
    all_jobs = []
    seen_urls = set()

    for platform_name, (fetcher, slugs) in _FETCHERS.items():
        platform_jobs = []
        for slug in slugs:
            try:
                jobs = fetcher(slug)
                for job in jobs:
                    url = job.get("job_url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        platform_jobs.append(job)
            except Exception as exc:
                log.warning(f"{platform_name} {slug}: unexpected error ({exc})")
            time.sleep(0.3)  # polite delay between companies

        all_jobs.extend(platform_jobs)
        print(f"  {platform_name}: {len(platform_jobs)} jobs ({len(slugs)} companies)")
        log.info(f"{platform_name}: {len(platform_jobs)} jobs from {len(slugs)} companies")

    return all_jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    print("=== ATS Direct Scraper Test ===\n")
    jobs = fetch_all_ats()
    print(f"\nTotal ATS jobs: {len(jobs)}")
    for j in jobs[:10]:
        print(f"  [{j['job_board']}] {j['title']} @ {j['company']} — {j['location']}")
