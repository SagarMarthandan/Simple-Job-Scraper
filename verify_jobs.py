#!/usr/bin/env python3
"""
verify_jobs.py — Job Verification Post-Step
============================================

Standalone script that runs AFTER the Jobscraper pipeline exports its CSV.
Visits each job URL to:
  1. Check if the listing is still active (not closed/filled)
  2. Extract detail-page signals: German language requirement (C1+),
     experience years, salary, remote/hybrid/onsite

Drops rows where the job is confirmed closed (404/explicit-closed) OR
where a German C1+ requirement is detected (Sagar does not have C1 German).

Usage:
  python3 verify_jobs.py                  # auto-finds most recent CSV
  python3 verify_jobs.py --csv path.csv   # specify input
  python3 verify_jobs.py --force          # re-verify all (ignore prior results)

Output: Job_Search_<date>_verified.csv next to the input, with added columns:
  verified_active, detail_language, detail_exp_years, detail_salary, detail_remote
"""

import argparse
import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    import cloudscraper
except ImportError:
    cloudscraper = None

# ── Constants ────────────────────────────────────────────────────────────────

JOB_SEARCH_DIR = Path("/home/sagar/Skills/Jobscraper/Job Search")

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 15

# Platforms to skip entirely (data is fresh, trust it)
SKIP_PLATFORMS = {"LinkedIn", "Indeed"}

# ATS platforms that use public APIs
ATS_PLATFORMS = {"Greenhouse", "SmartRecruiters", "Ashby"}

# ── German Language Detection ────────────────────────────────────────────────

# Explicit C1+ requirement — these jobs are DROP candidates.
# Broadened from plan for ASCII transliteration (ß→ss, ü→u/au/ue) common in
# scraped web text. Intent identical to the plan's patterns.
GERMAN_REQUIRED_PATTERNS = [
    # Explicit CEFR level: "C1 Deutsch", "Deutsch C1", "mindestens C1"
    r'(?:mindestens|min\.|ab)\s*C[12]\s*(?:Deutsch|German|in\s+Deutsch)',
    r'C[12]\s*[-/]?\s*(?:Deutsch|German)',
    r'(?:Deutsch|German)\s+C[12]',
    # "Fließend" / "fluent" — implies C1+ regardless of explicit level
    r'flie(?:ß|s)s?end\s*(?:Deutsch|German|in\s+Deutsch|Deutschkenntnisse)',
    r'(?:Deutsch|German)\s+flie(?:ß|s)s?end',
    # "Muttersprache" / native speaker
    r'Muttersprache\s+Deutsch',
    r'(?:Deutsch|German)\s+as\s+a\s+(?:first\s+)?native\s+language',
    # "Sehr gute Deutschkenntnisse" — usually means C1+
    r'sehr\s+gute\s+Deutsch(?:kenntnisse|sprachkenntnisse)',
    # "Verhandlungssicher" — business-fluent, C1+
    r'(?:Deutsch|German)\s+verhandlungssicher',
    r'verhandlungssicher\s+in\s+Deutsch',
    # "Business fluent in German" (English postings)
    r'business\s+fluent\s+(?:in\s+)?German',
    r'fluent\s+German\s+(?:required|mandatory|must)',
    # "Deutsch auf C1-Niveau" / "German at C1 level"
    r'(?:Deutsch|German)\s+(?:auf\s+)?C[12](?:\s*[-–]?\s*Niveau|\s+level)?',
]

# Broader "German helpful but not hard requirement" — do NOT eliminate.
GERMAN_SOFT_PATTERNS = [
    r'Deutsch(?:kenntnisse)?\s+(?:w(?:[üu]|au|ue)nschenswert|von\s+Vorteil|idealerweise)',
    r'German\s+(?:is\s+a\s+plus|preferred|nice\s+to\s+have|a\s+bonus)',
    r'idealerweise\s+Deutsch',
]

_REQUIRED_RE = [re.compile(p, re.IGNORECASE) for p in GERMAN_REQUIRED_PATTERNS]
_SOFT_RE = [re.compile(p, re.IGNORECASE) for p in GERMAN_SOFT_PATTERNS]


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences on . ! ? followed by whitespace or end."""
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()]


def detect_german_requirement(text: str) -> str:
    """Detect German language requirement from job description text.

    Returns:
        "German C1+ required" — hard requirement, row should be DROPPED.
        "German preferred"    — soft/preferred, keep row but flag.
        ""                    — no German requirement detected.
    """
    if not text:
        return ""

    sentences = _split_sentences(text)

    # Priority 1: find a sentence where a REQUIRED pattern matches AND no SOFT
    # pattern contradicts it in the same sentence.
    for sentence in sentences:
        req_match = any(rx.search(sentence) for rx in _REQUIRED_RE)
        if not req_match:
            continue
        soft_match = any(rx.search(sentence) for rx in _SOFT_RE)
        if not soft_match:
            return "German C1+ required"

    # Priority 2: soft/preferred only — keep, flag.
    if any(rx.search(text) for rx in _SOFT_RE):
        return "German preferred"

    return ""


# ── Signal Extraction ────────────────────────────────────────────────────────

_EXP_PATTERNS = [
    re.compile(r'(\d+)\s*(?:\+\s*)?Jahre?\s*(?:Berufs)?[eE]rfahrung'),
    re.compile(r'(\d+)\s*\+?\s*years?\s*(?:of\s*)?(?:professional\s*)?experience', re.IGNORECASE),
    re.compile(r'min\.\s*(\d+)\s*Jahre?', re.IGNORECASE),
]


def extract_exp_years(text: str) -> str:
    """Extract minimum experience years from job description text."""
    if not text:
        return ""
    for rx in _EXP_PATTERNS:
        m = rx.search(text)
        if m:
            return str(int(m.group(1)))
    return ""


_SALARY_NUM = r'(?:\d{2,3}(?:[.,]\d{3})+|\d{4,6})'
_SALARY_RE = re.compile(
    r'(' + _SALARY_NUM + r')\s*(?:€|EUR|Euro)?\s*'
    r'(?:bis\s*(' + _SALARY_NUM + r')\s*)?'
    r'(?:€|EUR|Euro)\s*'
    r'(?:(/Jahr|/jahr|p\.a\.|per\s+year)|(/Monat|/monat|per\s+month)|brutto)?',
    re.IGNORECASE,
)


def extract_salary(text: str, jsonld: dict | None = None) -> str:
    """Extract salary range from text or JSON-LD baseSalary field."""
    # JSON-LD baseSalary (structured data) — highest priority
    if jsonld and isinstance(jsonld, dict):
        bs = jsonld.get("baseSalary")
        if bs and isinstance(bs, dict):
            cur = bs.get("currency", "EUR")
            val = bs.get("value", {})
            if isinstance(val, dict):
                lo = val.get("minValue", val.get("value", ""))
                hi = val.get("maxValue", "")
                if lo and hi:
                    return f"{lo}-{hi} {cur}/year"
    if not text:
        return ""
    m = _SALARY_RE.search(text)
    if not m:
        return ""
    lo = m.group(1)
    hi = m.group(2)
    # Detect period from capture groups or surrounding context
    period = ""
    if m.group(3):
        period = "/year"
    elif m.group(4):
        period = "/month"
    else:
        tail = text[m.end():m.end() + 20].lower()
        if "jahr" in tail or "year" in tail or "p.a" in tail:
            period = "/year"
        elif "monat" in tail or "month" in tail:
            period = "/month"
    if hi:
        return f"{lo}-{hi} EUR{period}"
    return f"{lo} EUR{period}"


_REMOTE_KEYWORDS = {
    "remote": ["remote", "home-office", "home office", "homeoffice", "fully remote",
               "100% remote", "distributed", "work from anywhere"],
    "hybrid": ["hybrid", "teilweise remote", "flexibles arbeiten", "mix"],
}
_ONSITE_KEYWORDS = ["vor ort", "on-site", "onsite", "in-house", "büro", "office presence"]


def extract_remote(text: str) -> str:
    """Detect remote/hybrid/onsite from job description text."""
    if not text:
        return ""
    lower = text.lower()
    for kw in _REMOTE_KEYWORDS["remote"]:
        if kw in lower:
            return "remote"
    for kw in _REMOTE_KEYWORDS["hybrid"]:
        if kw in lower:
            return "hybrid"
    for kw in _ONSITE_KEYWORDS:
        if kw in lower:
            return "onsite"
    return ""


# ── JSON-LD Extraction ───────────────────────────────────────────────────────

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _extract_jsonld(html: str) -> dict | None:
    """Extract first JobPosting JSON-LD block from HTML."""
    for m in _JSONLD_RE.finditer(html):
        try:
            data = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") in ("JobPosting", "https://schema.org/JobPosting"):
                    return item
        elif isinstance(data, dict) and data.get("@type") in ("JobPosting", "https://schema.org/JobPosting"):
            return data
    return None


def _extract_description_text(html: str) -> str:
    """Extract job description text from HTML for signal extraction.

    Tries JSON-LD description first, then falls back to stripping HTML tags
    from the page body.
    """
    jsonld = _extract_jsonld(html)
    if jsonld and jsonld.get("description"):
        # JSON-LD descriptions may contain HTML — strip tags
        desc = jsonld["description"]
        desc = re.sub(r'<[^>]+>', ' ', desc)
        desc = re.sub(r'&[a-z]+;', ' ', desc)
        return desc.strip()
    # Fallback: strip all HTML tags from the full page
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-z]+;', ' ', text)
    return text.strip()


# ── Per-Platform Verifiers ───────────────────────────────────────────────────

def _empty_result() -> dict:
    return {
        "verified_active": "",
        "detail_language": "",
        "detail_exp_years": "",
        "detail_salary": "",
        "detail_remote": "",
    }


def _extract_signals(text: str, jsonld: dict | None = None) -> dict:
    """Run all signal extractors on description text."""
    return {
        "detail_language": detect_german_requirement(text),
        "detail_exp_years": extract_exp_years(text),
        "detail_salary": extract_salary(text, jsonld),
        "detail_remote": extract_remote(text),
    }


def verify_xing(job: dict) -> dict:
    """Verify a Xing job listing. Plain requests, 1.5s delay."""
    result = _empty_result()
    url = job.get("job_url", "")
    if not url:
        return result
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS,
                            allow_redirects=True)
        time.sleep(1.5)
        if resp.status_code == 404:
            result["verified_active"] = "False"
            return result
        if resp.status_code != 200:
            # Non-200 but not 404 — unknown, keep
            return result
        html = resp.text
        jsonld = _extract_jsonld(html)
        # Live check: JSON-LD JobPosting or title present in page
        title = job.get("title", "")
        if jsonld or (title and title.lower() in html.lower()):
            result["verified_active"] = "True"
            desc = _extract_description_text(html)
            signals = _extract_signals(desc, jsonld)
            result.update(signals)
        else:
            # Page loaded but no job content — might be closed/redirected
            result["verified_active"] = "False"
    except (requests.RequestException, requests.Timeout):
        # Network error — unknown, keep
        pass
    return result


def verify_stepstone(job: dict) -> dict:
    """Verify a Stepstone job listing. Plain requests, 2s delay."""
    result = _empty_result()
    url = job.get("job_url", "")
    if not url:
        return result
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS,
                            allow_redirects=True)
        time.sleep(2.0)
        if resp.status_code in (404, 410):
            result["verified_active"] = "False"
            return result
        if resp.status_code != 200:
            return result
        html = resp.text
        # Stepstone redirects closed jobs to a generic search page
        title = job.get("title", "")
        if title and title.lower() in html.lower():
            result["verified_active"] = "True"
            desc = _extract_description_text(html)
            jsonld = _extract_jsonld(html)
            signals = _extract_signals(desc, jsonld)
            result.update(signals)
        elif "nicht mehr verfügbar" in html.lower() or "no longer available" in html.lower():
            result["verified_active"] = "False"
        else:
            result["verified_active"] = "True"
            desc = _extract_description_text(html)
            jsonld = _extract_jsonld(html)
            signals = _extract_signals(desc, jsonld)
            result.update(signals)
    except (requests.RequestException, requests.Timeout):
        pass
    return result


def verify_startupjobs(job: dict) -> dict:
    """Verify a Startup.jobs listing. Cloudscraper, 2s delay."""
    result = _empty_result()
    url = job.get("job_url", "")
    if not url:
        return result
    if cloudscraper is None:
        return result
    try:
        scraper = cloudscraper.create_scraper(browser={"browser": "chrome"})
        resp = scraper.get(url, timeout=REQUEST_TIMEOUT)
        time.sleep(2.0)
        if resp.status_code in (404, 410):
            result["verified_active"] = "False"
            return result
        if resp.status_code != 200:
            return result
        html = resp.text
        result["verified_active"] = "True"
        desc = _extract_description_text(html)
        jsonld = _extract_jsonld(html)
        signals = _extract_signals(desc, jsonld)
        result.update(signals)
    except Exception:
        pass
    return result


def verify_glassdoor(job: dict) -> dict:
    """Verify a Glassdoor listing. Cloudscraper, 2s delay."""
    result = _empty_result()
    url = job.get("job_url", "")
    if not url:
        return result
    if cloudscraper is None:
        return result
    try:
        scraper = cloudscraper.create_scraper(browser={"browser": "chrome"})
        resp = scraper.get(url, timeout=REQUEST_TIMEOUT)
        time.sleep(2.0)
        if resp.status_code in (404, 410):
            result["verified_active"] = "False"
            return result
        if resp.status_code != 200:
            return result
        html = resp.text
        # Glassdoor detail pages may 403 — if we got 200, job is likely active
        result["verified_active"] = "True"
        desc = _extract_description_text(html)
        jsonld = _extract_jsonld(html)
        signals = _extract_signals(desc, jsonld)
        result.update(signals)
    except Exception:
        pass
    return result


def _parse_ats_url(url: str, platform: str) -> tuple[str, str]:
    """Parse company slug and job ID from an ATS job URL.

    Returns (slug, job_id) or ("", "") if unparseable.
    """
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    if platform == "Greenhouse":
        # https://boards.greenhouse.io/{slug}/jobs/{job_id}
        if len(path_parts) >= 3 and path_parts[1] == "jobs":
            return path_parts[0], path_parts[2]
    elif platform == "SmartRecruiters":
        # https://jobs.smartrecruiters.com/{slug}/{job_id}
        if len(path_parts) >= 2:
            return path_parts[0], path_parts[1]
    elif platform == "Ashby":
        # https://jobs.ashbyhq.com/{slug}/{job_id}
        if len(path_parts) >= 2:
            return path_parts[0], path_parts[1]
    return "", ""


def verify_greenhouse(job: dict) -> dict:
    """Verify a Greenhouse job via public API. 404/empty = closed."""
    result = _empty_result()
    url = job.get("job_url", "")
    slug, job_id = _parse_ats_url(url, "Greenhouse")
    if not slug or not job_id:
        return result
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
    try:
        resp = requests.get(api_url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
        time.sleep(0.5)
        if resp.status_code == 404:
            result["verified_active"] = "False"
            return result
        if resp.status_code != 200:
            return result
        data = resp.json()
        if not data:
            result["verified_active"] = "False"
            return result
        result["verified_active"] = "True"
        # Greenhouse single-job API returns job details
        desc = data.get("content", "") or data.get("title", "")
        if isinstance(desc, str):
            desc = re.sub(r'<[^>]+>', ' ', desc)
        signals = _extract_signals(desc if isinstance(desc, str) else "")
        result.update(signals)
    except (requests.RequestException, requests.Timeout, json.JSONDecodeError):
        pass
    return result


def verify_smartrecruiters(job: dict) -> dict:
    """Verify a SmartRecruiters job via public API. 404/empty = closed."""
    result = _empty_result()
    url = job.get("job_url", "")
    slug, job_id = _parse_ats_url(url, "SmartRecruiters")
    if not slug or not job_id:
        return result
    api_url = f"https://api.smartrecruiters.com/v1/companies/{slug}/jobs/{job_id}"
    try:
        resp = requests.get(api_url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
        time.sleep(0.5)
        if resp.status_code == 404:
            result["verified_active"] = "False"
            return result
        if resp.status_code != 200:
            return result
        data = resp.json()
        if not data:
            result["verified_active"] = "False"
            return result
        result["verified_active"] = "True"
        # Extract description from jobAd sections
        desc = ""
        job_ad = data.get("jobAd", {})
        if isinstance(job_ad, dict):
            sections = job_ad.get("sections", {})
            if isinstance(sections, dict):
                for section in sections.values():
                    if isinstance(section, dict):
                        text = section.get("text", "")
                        if text:
                            desc += " " + re.sub(r'<[^>]+>', ' ', text)
        signals = _extract_signals(desc.strip())
        result.update(signals)
    except (requests.RequestException, requests.Timeout, json.JSONDecodeError):
        pass
    return result


def verify_ashby(job: dict) -> dict:
    """Verify an Ashby job via public API. 404/empty = closed."""
    result = _empty_result()
    url = job.get("job_url", "")
    slug, job_id = _parse_ats_url(url, "Ashby")
    if not slug or not job_id:
        return result
    # Ashby board API returns all postings — search for our job_id
    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    try:
        resp = requests.get(api_url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
        time.sleep(0.5)
        if resp.status_code == 404:
            result["verified_active"] = "False"
            return result
        if resp.status_code != 200:
            return result
        data = resp.json()
        postings = data if isinstance(data, list) else data.get("postings", [])
        target = None
        for posting in postings:
            if not isinstance(posting, dict):
                continue
            if posting.get("id") == job_id or job_id in (posting.get("id", "")):
                target = posting
                break
        if target is None:
            result["verified_active"] = "False"
            return result
        result["verified_active"] = "True"
        desc = target.get("descriptionHtml", "") or target.get("description", "")
        if isinstance(desc, str) and "<" in desc:
            desc = re.sub(r'<[^>]+>', ' ', desc)
        signals = _extract_signals(desc if isinstance(desc, str) else "")
        result.update(signals)
    except (requests.RequestException, requests.Timeout, json.JSONDecodeError):
        pass
    return result


def verify_arbeitnow(job: dict) -> dict:
    """Verify an Arbeitnow job by checking the public API for the URL."""
    result = _empty_result()
    url = job.get("job_url", "")
    if not url:
        return result
    try:
        api_url = "https://www.arbeitnow.com/api/job-board-api"
        resp = requests.get(api_url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
        if resp.status_code != 200:
            return result
        data = resp.json()
        job_urls = {item.get("url", "") for item in data.get("data", [])
                    if isinstance(item, dict)}
        if url in job_urls:
            result["verified_active"] = "True"
        else:
            # Not in current API — could be cycled, treat as unknown (keep)
            result["verified_active"] = ""
    except (requests.RequestException, requests.Timeout, json.JSONDecodeError):
        pass
    return result


# ── Platform Dispatch ────────────────────────────────────────────────────────

PLATFORM_VERIFIERS = {
    "Xing": verify_xing,
    "Stepstone": verify_stepstone,
    "Startup.jobs": verify_startupjobs,
    "Glassdoor": verify_glassdoor,
    "Greenhouse": verify_greenhouse,
    "SmartRecruiters": verify_smartrecruiters,
    "Ashby": verify_ashby,
    "Arbeitnow": verify_arbeitnow,
}


def verify_skip_platform(job: dict) -> dict:
    """Mark job as active without fetching — for LinkedIn/Indeed (fresh data)."""
    result = _empty_result()
    result["verified_active"] = "True"
    return result


def verify_platform_batch(platform: str, jobs: list[dict]) -> list[tuple[int, dict]]:
    """Verify all jobs for one platform. Returns list of (index, result_dict).

    Sequential with platform-specific delay, except ATS at 4 workers.
    """
    results: list[tuple[int, dict]] = []

    if platform in SKIP_PLATFORMS:
        for i, job in enumerate(jobs):
            results.append((i, verify_skip_platform(job)))
        return results

    verifier = PLATFORM_VERIFIERS.get(platform)
    if verifier is None:
        # Unknown platform — mark as active, no fetch
        for i, job in enumerate(jobs):
            results.append((i, verify_skip_platform(job)))
        return results

    if platform in ATS_PLATFORMS and len(jobs) > 1:
        # ATS: 4 parallel workers, 0.5s delay handled inside each verifier
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_idx = {
                executor.submit(verifier, job): i for i, job in enumerate(jobs)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results.append((idx, future.result()))
                except Exception:
                    results.append((idx, _empty_result()))
    else:
        # Sequential with delay (delay is inside each verifier)
        for i, job in enumerate(jobs):
            try:
                results.append((i, verifier(job)))
            except Exception:
                results.append((i, _empty_result()))

    return results


# ── CSV I/O ──────────────────────────────────────────────────────────────────

INPUT_FIELDS = [
    "language", "job_board", "role_type", "title", "company",
    "location", "posted_at", "exp_required", "match_score", "job_url",
]
OUTPUT_FIELDS = INPUT_FIELDS + [
    "verified_active", "detail_language", "detail_exp_years",
    "detail_salary", "detail_remote",
]


def find_latest_csv() -> Path | None:
    """Find the most recent Job_Search_*.csv under the Job Search directory."""
    if not JOB_SEARCH_DIR.exists():
        return None
    # Date folders sorted descending (most recent first)
    date_folders = sorted(
        [d for d in JOB_SEARCH_DIR.iterdir() if d.is_dir()],
        reverse=True,
    )
    for folder in date_folders:
        csvs = sorted(folder.glob("Job_Search_*.csv"), reverse=True)
        # Skip already-verified files
        csvs = [c for c in csvs if "_verified" not in c.name]
        if csvs:
            return csvs[0]
    return None


def load_csv(path: Path) -> list[dict]:
    """Load CSV rows, handling both original and already-verified schemas."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def save_csv(path: Path, rows: list[dict]) -> None:
    """Write verified CSV with all output fields (UTF-8 BOM for Excel)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # Ensure all output fields exist
            out = {k: row.get(k, "") for k in OUTPUT_FIELDS}
            writer.writerow(out)


# ── Main Orchestration ───────────────────────────────────────────────────────

def run_verification(csv_path: Path, force: bool = False) -> None:
    """Main entry: load CSV, verify per-platform, write results, print summary."""
    rows = load_csv(csv_path)
    if not rows:
        print(f"[!] No rows found in {csv_path}")
        return

    print(f"[*] Loaded {len(rows)} jobs from {csv_path}")

    # Idempotency: skip rows already verified unless --force
    to_verify: list[tuple[int, dict]] = []
    already_verified = 0
    for i, row in enumerate(rows):
        existing = row.get("verified_active", "")
        if existing and not force:
            already_verified += 1
        else:
            to_verify.append((i, row))

    if already_verified:
        print(f"[*] {already_verified} row(s) already verified — skipping (use --force to re-verify)")

    # Group jobs to verify by platform
    platform_groups: dict[str, list[tuple[int, dict]]] = {}
    for i, row in to_verify:
        platform = row.get("job_board", "Unknown")
        platform_groups.setdefault(platform, []).append((i, row))

    if not platform_groups:
        print("[*] Nothing to verify — all rows already checked.")
        _write_and_summarize(csv_path, rows, 0, 0, 0, 0, already_verified)
        return

    # Print per-platform counts
    for platform in sorted(platform_groups):
        count = len(platform_groups[platform])
        tag = " (skip — fresh data)" if platform in SKIP_PLATFORMS else ""
        print(f"    {platform}: {count} URL(s){tag}")

    # Run all platform batches in parallel
    # Each batch runs in its own thread; within each, sequential (or ATS 4 workers)
    all_results: dict[int, dict] = {}

    with ThreadPoolExecutor(max_workers=len(platform_groups)) as executor:
        future_to_platform = {}
        for platform, group in platform_groups.items():
            jobs = [row for _, row in group]
            future = executor.submit(verify_platform_batch, platform, jobs)
            future_to_platform[future] = (platform, group)

        for future in as_completed(future_to_platform):
            platform, group = future_to_platform[future]
            try:
                batch_results = future.result()
                for local_idx, result in batch_results:
                    orig_idx = group[local_idx][0]
                    all_results[orig_idx] = result
                print(f"  [✓] {platform}: done ({len(batch_results)} verified)")
            except Exception as exc:
                print(f"  [!] {platform}: batch failed ({exc})")
                for orig_idx, _ in group:
                    all_results[orig_idx] = _empty_result()

    # Merge results into rows
    kept = 0
    closed = 0
    german_dropped = 0
    enriched = 0

    output_rows = []
    for i, row in enumerate(rows):
        result = all_results.get(i)
        if result:
            row.update(result)
        output_rows.append(row)

        active = row.get("verified_active", "")
        lang = row.get("detail_language", "")

        if active == "False":
            closed += 1
        elif lang == "German C1+ required":
            german_dropped += 1
        else:
            kept += 1
            if (row.get("detail_exp_years") or row.get("detail_salary")
                    or row.get("detail_remote") or lang == "German preferred"):
                enriched += 1

    # Drop closed and German-required rows
    final_rows = [
        row for row in output_rows
        if row.get("verified_active", "") != "False"
        and row.get("detail_language", "") != "German C1+ required"
    ]

    _write_and_summarize(csv_path, final_rows, kept, closed, german_dropped,
                         enriched, already_verified)


def _write_and_summarize(csv_path: Path, rows: list[dict],
                         kept: int, closed: int, german_dropped: int,
                         enriched: int, already_verified: int) -> None:
    """Write output CSV and print summary."""
    # Output filename: Job_Search_<date>_verified.csv next to input
    stem = csv_path.stem
    if stem.endswith("_verified"):
        stem = stem[:-len("_verified")]
    out_path = csv_path.parent / f"{stem}_verified.csv"
    save_csv(out_path, rows)

    print()
    print("=" * 60)
    print(f"  Verification Summary")
    print("=" * 60)
    print(f"  Kept (applicable):        {kept}")
    print(f"  Closed/removed:           {closed}")
    print(f"  Dropped (German C1+):     {german_dropped}")
    print(f"  Enriched with details:    {enriched}")
    if already_verified:
        print(f"  Already verified (skip):  {already_verified}")
    print(f"  Output: {out_path}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Verify job listings: check if active, extract German language "
                    "requirement, experience years, salary, and remote status."
    )
    parser.add_argument(
        "--csv", type=str, default=None,
        help="Path to input Job_Search_*.csv (default: auto-find most recent)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-verify all rows, even those already verified",
    )
    args = parser.parse_args()

    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            print(f"[!] CSV not found: {csv_path}")
            return
    else:
        csv_path = find_latest_csv()
        if csv_path is None:
            print(f"[!] No Job_Search_*.csv found under {JOB_SEARCH_DIR}")
            return

    run_verification(csv_path, force=args.force)


if __name__ == "__main__":
    main()
