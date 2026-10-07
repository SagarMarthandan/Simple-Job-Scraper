#!/usr/bin/env python3
"""Verify scraped jobs and fail closed when typed JD judgment is incomplete.

Full descriptions are loaded from the sibling JSON and may be enriched by the
platform verifiers. German and experience judgments run on those final row
descriptions through the OMP eval ``judge_batch`` API.

Usage in an OMP Python eval cell:
    import verify_jobs
    verify_jobs.judge_batch = judge_batch
    await verify_jobs.run_verification(Path("Job Search/YYYY-MM-DD/Job_Search_*.csv"), force=True)

The command-line entry point cannot provide ``judge_batch`` and exits with an
explicit instruction to use OMP eval; there is no permissive standalone path.
"""
import argparse
import asyncio
from collections import Counter
import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
from tqdm import tqdm

from job_identity import extract_linkedin_job_id, load_seen_job_urls, normalize_job_url
from location_policy import is_germany_location, is_hamburg_or_kiel


try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.hyperlink import Hyperlink
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

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



# Platforms that use public APIs (no HTML scraping)
ATS_PLATFORMS = {"Greenhouse", "SmartRecruiters", "Ashby"}

# Tech keywords for match score recalculation (same as pipeline)
TECH_KEYWORDS = [
    "dbt", "airflow", "spark", "pyspark", "python", "sql", "gcp",
    "bigquery", "aws", "azure", "databricks", "docker", "kafka",
    "postgresql", "snowflake",
]

# Staffing/Recruitment Agency Blocklist — imported from staffing_filter.py
# (single source of truth shared with apify_job_search.py)
from staffing_filter import STAFFING_COMPANIES

# LinkedIn job ID growth rate (~530K new IDs/day globally, verified Aug 23-27)
LINKEDIN_DAILY_ID_GROWTH = 530000

# Repost detection thresholds
REPOST_CROSS_RUN_DAYS = 7      # appeared in a run >7 days ago
REPOST_JOB_ID_AGE_DAYS = 14    # job ID suggests >14 days old
# ── Salary Extraction ────────────────────────────────────────────────────────

_SALARY_NUM = r'(?:\d{2,3}(?:[.,]\d{3})+|\d{4,6})'
_SALARY_RE = re.compile(
    r'(' + _SALARY_NUM + r')\s*(?:€|EUR|Euro)?\s*'
    r'(?:bis\s*(' + _SALARY_NUM + r')\s*)?'
    r'(?:€|EUR|Euro)\s*'
    r'(?:(/Jahr|/jahr|p\.a\.|per\s+year)|(/Monat|/monat|per\s+month)|brutto)?',
    re.IGNORECASE,
)


def _extract_salary_jsonld(jsonld: dict | None) -> str:
    """Extract salary string from JSON-LD baseSalary field. Returns "" if absent."""
    if not jsonld or not isinstance(jsonld, dict):
        return ""
    bs = jsonld.get("baseSalary")
    if not bs or not isinstance(bs, dict):
        return ""
    cur = bs.get("currency", "EUR")
    val = bs.get("value", {})
    if not isinstance(val, dict):
        return ""
    lo = val.get("minValue", val.get("value", ""))
    hi = val.get("maxValue", "")
    if lo and hi:
        return f"{lo}-{hi} {cur}/year"
    return ""


def _detect_salary_period(text: str, match: re.Match) -> str:
    """Detect salary period ("/year", "/month", or "") from text after the match."""
    if match.group(3):
        return "/year"
    if match.group(4):
        return "/month"
    tail = text[match.end():match.end() + 20].lower()
    if "jahr" in tail or "year" in tail or "p.a" in tail:
        return "/year"
    if "monat" in tail or "month" in tail:
        return "/month"
    return ""


def extract_salary(text: str, jsonld: dict | None = None) -> str:
    """Extract salary range from text or JSON-LD baseSalary field."""
    # JSON-LD baseSalary (structured data) — highest priority
    jsonld_salary = _extract_salary_jsonld(jsonld)
    if jsonld_salary:
        return jsonld_salary
    if not text:
        return ""
    m = _SALARY_RE.search(text)
    if not m:
        return ""
    lo = m.group(1)
    hi = m.group(2)
    period = _detect_salary_period(text, m)
    if hi:
        return f"{lo}-{hi} EUR{period}"
    return f"{lo} EUR{period}"


# ── Remote Detection ─────────────────────────────────────────────────────────

_REMOTE_KEYWORDS = {
    "remote": ["remote", "home-office", "home office", "homeoffice",
               "fully remote", "100% remote", "distributed", "work from anywhere"],
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


# ── Match Score Recalculation ────────────────────────────────────────────────

def compute_match_score_from_jd(jd_text: str) -> int:
    """Recalculate match score from full job description text.

    Same algorithm as pipeline's compute_match_score() but runs on the
    actual JD text instead of just title + snippet.
    """
    if not jd_text:
        return 0
    text_lower = jd_text.lower()
    matches = sum(1 for kw in TECH_KEYWORDS if kw in text_lower)
    return min(100, int((matches / len(TECH_KEYWORDS)) * 100 * 2.5))



# ── Reposted Detection (LinkedIn only) ───────────────────────────────────────

def _find_previous_run_dirs(job_search_dir: Path, today_str: str) -> list[Path]:
    """Return previous run directories sorted by date, excluding today and non-date dirs."""
    prev_dirs = []
    for run_dir in sorted(job_search_dir.iterdir()):
        if not run_dir.is_dir() or not run_dir.name.startswith("2026-"):
            continue
        if run_dir.name >= today_str:
            continue
        try:
            datetime.strptime(run_dir.name, "%Y-%m-%d")
        except ValueError:
            continue
        prev_dirs.append(run_dir)
    return prev_dirs


def _load_urls_from_csv(csv_path: Path) -> set[str]:
    """Return set of normalized job URLs from a CSV file."""
    urls = set()
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                url = normalize_job_url(row.get("job_url", ""))
                if url:
                    urls.add(url)
    except Exception:
        pass
    return urls


def _load_linkedin_title_keys_from_csv(csv_path: Path) -> set[str]:
    """Return set of normalize_key(company, title) for LinkedIn rows from a CSV file."""
    import sys
    skill_dir = Path("/home/sagar/Skills/Jobscraper")
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))
    from apify_job_search import normalize_key

    keys = set()
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("job_board") != "LinkedIn":
                    continue
                key = normalize_key(row.get("company", ""), row.get("title", ""))
                if key:
                    keys.add(key)
    except Exception:
        pass
    return keys


def _load_repost_data(job_search_dir: Path, today_str: str) -> tuple[set, set]:
    """Load reposted detection data from previous runs.

    Returns:
        old_title_keys: normalize_key(company, title) from runs >7 days ago
        recent_urls: job URLs from the most recent previous run (carryovers)
    """
    cutoff = datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=REPOST_CROSS_RUN_DAYS)
    old_title_keys = set()
    recent_urls = set()

    if not job_search_dir.exists():
        return old_title_keys, recent_urls

    prev_dirs = _find_previous_run_dirs(job_search_dir, today_str)

    # Load URLs from the most recent previous run (carryover detection)
    if prev_dirs:
        most_recent = prev_dirs[-1]
        csvs = [c for c in most_recent.glob("Job_Search_*.csv")
                if "_verified" not in c.name and "_deduped" not in c.name]
        if csvs:
            recent_urls = _load_urls_from_csv(csvs[0])

    # Load title keys from runs older than 7 days (repost detection)
    for run_dir in prev_dirs:
        try:
            run_date = datetime.strptime(run_dir.name, "%Y-%m-%d")
        except ValueError:
            continue
        if run_date >= cutoff:
            continue
        csvs = [c for c in run_dir.glob("Job_Search_*.csv")
                if "_verified" not in c.name and "_deduped" not in c.name]
        if not csvs:
            continue
        old_title_keys |= _load_linkedin_title_keys_from_csv(csvs[0])

    return old_title_keys, recent_urls



def detect_reposted(job: dict, today_str: str, old_title_keys: set,
                    today_max_linkedin_id: int, recent_urls: set | None = None) -> bool:
    """Flag LinkedIn reposts by old company/title history or job-ID age."""
    if job.get("job_board") != "LinkedIn":
        return False

    raw_url = job.get("job_url", "")
    url = normalize_job_url(raw_url)
    is_carryover = bool(recent_urls and url in recent_urls)

    job_id = extract_linkedin_job_id(raw_url)
    is_fresh = False
    if job_id and today_max_linkedin_id:
        age_days = (today_max_linkedin_id - job_id) / LINKEDIN_DAILY_ID_GROWTH
        is_fresh = age_days <= REPOST_JOB_ID_AGE_DAYS
        if age_days > REPOST_JOB_ID_AGE_DAYS:
            return True

    if not is_carryover and not is_fresh:
        import sys
        skill_dir = Path("/home/sagar/Skills/Jobscraper")
        if str(skill_dir) not in sys.path:
            sys.path.insert(0, str(skill_dir))
        from apify_job_search import normalize_key

        key = normalize_key(job.get("company", ""), job.get("title", ""))
        return bool(key and key in old_title_keys)
    return False

# ── Already-Applied Detection (imported from applied_check.py) ────────────────
from applied_check import load_applied_data, is_already_applied, load_llm_matches
# ── Per-Platform Verifiers ───────────────────────────────────────────────────

def _empty_result() -> dict:
    return {
        "verified_active": "",
        "detail_language": "",
        "detail_exp_years": "",
        "detail_salary": "",
        "detail_remote": "",
        "detail_reposted": "",
    }


def _extract_signals(text: str, jsonld: dict | None = None) -> dict:
    """Run regex-based signal extractors on description text.

    German language and experience are classified later via LLM batch.
    Only salary and remote use regex (simpler patterns, fewer variants).
    """
    return {
        "detail_salary": extract_salary(text, jsonld),
        "detail_remote": extract_remote(text),
    }


def _process_result(result: dict, desc_text: str, jsonld: dict | None = None) -> dict:
    """Fill enrichment signals and retain a fetched full description."""
    signals = _extract_signals(desc_text, jsonld)
    result.update(signals)
    result["match_score"] = f"{compute_match_score_from_jd(desc_text)}%"
    if isinstance(desc_text, str) and len(desc_text.strip()) >= MINIMUM_DESCRIPTION_LENGTH:
        result["description"] = desc_text
    return result


# ── LinkedIn Verifier (plain requests + JSON-LD, no auth) ────────────────────

def verify_linkedin(job: dict) -> dict:
    """Verify a LinkedIn job using pre-fetched description from step 1.

    Step 1 fetches full JDs via JSON-LD on detail pages. If the description
    is present, the job is active (page was accessible < 24h ago).
    """
    result = _empty_result()
    desc = job.get("description", "")
    if not desc or len(desc) < 50:
        return result  # no description — can't verify, keep as unknown
    result["verified_active"] = "True"
    result = _process_result(result, desc)
    return result


# ── Indeed Verifier ───────────────────────────────────────────────────────────

def verify_indeed(job: dict) -> dict:
    """Verify an Indeed job using pre-fetched description from step 1.

    Description comes from the Indeed GraphQL API (already in JSON from step 1).
    If no description is available, the job stays unverified.
    """
    result = _empty_result()
    desc = job.get("description", "")
    if not desc:
        return result
    result["verified_active"] = "True"
    result = _process_result(result, desc)
    return result


# ── Xing Verifier ────────────────────────────────────────────────────────────

def verify_xing(job: dict) -> dict:
    """Verify a Xing job using pre-fetched description from step 1.

    Step 1 fetches full JDs via JSON-LD on detail pages.
    """
    result = _empty_result()
    desc = job.get("description", "")
    if not desc or len(desc) < 50:
        return result
    result["verified_active"] = "True"
    result = _process_result(result, desc)
    return result


# ── Stepstone Verifier ───────────────────────────────────────────────────────

def verify_stepstone(job: dict) -> dict:
    """Verify a Stepstone job using pre-fetched description from step 1.

    Step 1 fetches full JDs via JSON-LD on detail pages.
    """
    result = _empty_result()
    desc = job.get("description", "")
    if not desc or len(desc) < 50:
        return result
    result["verified_active"] = "True"
    result = _process_result(result, desc)
    return result




# ── ATS Verifiers (public JSON APIs) ─────────────────────────────────────────

def _parse_ats_url(url: str, platform: str) -> tuple[str, str]:
    """Parse company slug and job ID from an ATS job URL."""
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]
    if platform == "Greenhouse":
        if len(path_parts) >= 3 and path_parts[1] == "jobs":
            return path_parts[0], path_parts[2]
    elif platform == "SmartRecruiters":
        if len(path_parts) >= 2:
            return path_parts[0], path_parts[1]
    elif platform == "Ashby":
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
        desc = data.get("content", "") or data.get("title", "")
        if isinstance(desc, str):
            desc = re.sub(r'<[^>]+>', ' ', desc)
        result = _process_result(result, desc if isinstance(desc, str) else "")
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
        result = _process_result(result, desc.strip())
    except (requests.RequestException, requests.Timeout, json.JSONDecodeError):
        pass
    return result


def _find_ashby_posting(postings: list, job_id: str) -> dict | None:
    """Find the posting dict matching job_id in the Ashby postings list."""
    for posting in postings:
        if not isinstance(posting, dict):
            continue
        if posting.get("id") == job_id or job_id in (posting.get("id", "")):
            return posting
    return None


def _extract_ashby_desc(target: dict) -> str:
    """Extract and HTML-strip the description from an Ashby posting dict."""
    desc = target.get("descriptionHtml", "") or target.get("description", "")
    if isinstance(desc, str) and "<" in desc:
        desc = re.sub(r'<[^>]+>', ' ', desc)
    return desc if isinstance(desc, str) else ""


def verify_ashby(job: dict) -> dict:
    """Verify an Ashby job via public API. 404/empty = closed."""
    result = _empty_result()
    url = job.get("job_url", "")
    slug, job_id = _parse_ats_url(url, "Ashby")
    if not slug or not job_id:
        return result
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
        target = _find_ashby_posting(postings, job_id)
        if target is None:
            result["verified_active"] = "False"
            return result
        result["verified_active"] = "True"
        desc = _extract_ashby_desc(target)
        result = _process_result(result, desc)
    except (requests.RequestException, requests.Timeout, json.JSONDecodeError):
        pass
    return result


# ── Arbeitnow Verifier ───────────────────────────────────────────────────────

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
            # Arbeitnow API has description in the job data
            for item in data.get("data", []):
                if isinstance(item, dict) and item.get("url") == url:
                    desc = item.get("description", "")
                    if desc:
                        desc = re.sub(r'<[^>]+>', ' ', desc)
                        result = _process_result(result, desc)
                    break
        else:
            result["verified_active"] = ""
    except (requests.RequestException, requests.Timeout, json.JSONDecodeError):
        pass
    return result


# ── Platform Dispatch ────────────────────────────────────────────────────────

PLATFORM_VERIFIERS = {
    "LinkedIn": verify_linkedin,
    "Indeed": verify_indeed,
    "Xing": verify_xing,
    "Stepstone": verify_stepstone,

    "Greenhouse": verify_greenhouse,
    "SmartRecruiters": verify_smartrecruiters,
    "Ashby": verify_ashby,
    "Arbeitnow": verify_arbeitnow,
}

# ATS platforms: 4 workers for parallel API calls
ATS_PLATFORMS_SET = {"Greenhouse", "SmartRecruiters", "Ashby"}


def verify_platform_batch(platform: str, jobs: list[dict]) -> list[tuple[int, dict]]:
    """Verify all jobs for one platform. Returns list of (index, result_dict)."""
    results: list[tuple[int, dict]] = []
    verifier = PLATFORM_VERIFIERS.get(platform)

    if verifier is None:
        # Unknown platform — mark as active, no fetch
        for i, job in enumerate(jobs):
            r = _empty_result()
            r["verified_active"] = "True"
            results.append((i, r))
        return results

    if platform in ATS_PLATFORMS_SET and len(jobs) > 1:
        # ATS: 4 parallel workers, 0.5s delay inside each verifier
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_idx = {
                executor.submit(verifier, job): i for i, job in enumerate(jobs)
            }
            for future in tqdm(as_completed(future_to_idx), total=len(future_to_idx), desc=f"{platform} verify"):
                idx = future_to_idx[future]
                try:
                    results.append((idx, future.result()))
                except Exception:
                    results.append((idx, _empty_result()))
    else:
        # Sequential (LinkedIn/Xing/Stepstone/Indeed are instant — no HTTP,
        # just read pre-fetched description. Arbeitnow makes 1 API call per job.)
        for i, job in enumerate(jobs):
            try:
                results.append((i, verifier(job)))
            except Exception:
                results.append((i, _empty_result()))

    return results


# ── CSV / XLSX I/O ───────────────────────────────────────────────────────────

INPUT_FIELDS = [
    "language", "job_board", "role_type", "title", "company",
    "location", "posted_at", "exp_required", "match_score", "job_url",
]
OUTPUT_FIELDS = INPUT_FIELDS + [
    "verified_active", "detail_language", "detail_exp_years",
    "detail_salary", "detail_remote", "detail_reposted", "detail_review_status",
]

def find_latest_csv() -> Path | None:
    """Find the most recent Job_Search_*.csv under the Job Search directory."""
    if not JOB_SEARCH_DIR.exists():
        return None
    date_folders = sorted(
        [d for d in JOB_SEARCH_DIR.iterdir() if d.is_dir()],
        reverse=True,
    )
    for folder in date_folders:
        csvs = sorted(folder.glob("Job_Search_*.csv"), reverse=True)
        csvs = [c for c in csvs if "_verified" not in c.name and "_deduped" not in c.name]
        if csvs:
            return csvs[0]
    return None


def load_csv(path: Path) -> list[dict]:
    """Load CSV rows, handling both original and already-verified schemas."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def save_xlsx(path: Path, main_rows: list[dict],
              reposted_rows: list[dict] | None = None,
              staffing_rows: list[dict] | None = None,
              already_applied_rows: list[dict] | None = None,
              review_rows: list[dict] | None = None,
              previously_seen_rows: list[dict] | None = None) -> None:
    """Write verification sheets for apply-ready, segregated, and review rows.

    All sheets share the same fields and clickable URL formatting.
    """
    if not HAS_OPENPYXL:
        print("[!] openpyxl not installed — falling back to CSV")
        csv_main = path.with_suffix(".csv")
        with open(csv_main, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(main_rows)
        if reposted_rows:
            csv_repost = path.parent / f"{path.stem}_reposted.csv"
            with open(csv_repost, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(reposted_rows)
        if staffing_rows:
            csv_staffing = path.parent / f"{path.stem}_staffing.csv"
            with open(csv_staffing, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(staffing_rows)
        if review_rows:
            csv_review = path.parent / f"{path.stem}_needs_review.csv"
            with open(csv_review, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(review_rows)
        if previously_seen_rows:
            csv_seen = path.parent / f"{path.stem}_previously_seen.csv"
            with open(csv_seen, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(previously_seen_rows)
        print(f"[✓] CSV exported to: {csv_main}")
        return

    wb = Workbook()

    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF")
    link_font = Font(color="0563C1", underline="single")
    center = Alignment(horizontal="center", vertical="center")

    def _write_sheet(ws, rows, title):
        ws.title = title
        ws.append(OUTPUT_FIELDS)
        for c in ws[1]:
            c.fill = header_fill
            c.font = header_font
            c.alignment = center

        url_col = OUTPUT_FIELDS.index("job_url") + 1
        score_col = OUTPUT_FIELDS.index("match_score") + 1

        for row in rows:
            ws.append([row.get(k, "") for k in OUTPUT_FIELDS])
            r = ws.max_row
            cell = ws.cell(row=r, column=url_col)
            url = str(cell.value or "")
            if url:
                # Explicit Hyperlink object ensures ref is bound to the
                # correct cell coordinate — avoids openpyxl relationship
                # misalignment when many hyperlinks are present.
                cell.hyperlink = Hyperlink(ref=cell.coordinate, target=url)
                cell.font = link_font
            cell = ws.cell(row=r, column=score_col)
            v = cell.value
            if isinstance(v, str) and v.endswith("%") and v[:-1].strip().isdigit():
                cell.value = int(v[:-1].strip())
                cell.number_format = '0"%"'
                cell.alignment = center

        for idx in range(1, len(OUTPUT_FIELDS) + 1):
            letter = get_column_letter(idx)
            widths = [len(str(ws.cell(row=r, column=idx).value or ""))
                      for r in range(1, ws.max_row + 1)]
            ws.column_dimensions[letter].width = min(max(widths) + 2, 80)

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(OUTPUT_FIELDS))}{ws.max_row}"
        ws.row_dimensions[1].height = 22

    # Sheet 1: To Apply (main)
    ws_main = wb.active
    _write_sheet(ws_main, main_rows, "To Apply")

    # Sheet 2: Reposted
    if reposted_rows:
        ws_repost = wb.create_sheet("Reposted")
        _write_sheet(ws_repost, reposted_rows, "Reposted")
    # Sheet 3: Staffing Companies
    if staffing_rows:
        ws_staffing = wb.create_sheet("Staffing Companies")
        _write_sheet(ws_staffing, staffing_rows, "Staffing Companies")

    # Sheet 4: Already Applied
    if already_applied_rows:
        ws_applied = wb.create_sheet("Already Applied")
        _write_sheet(ws_applied, already_applied_rows, "Already Applied")
    if review_rows:
        ws_review = wb.create_sheet("Needs Review")
        _write_sheet(ws_review, review_rows, "Needs Review")
    if previously_seen_rows:
        ws_seen = wb.create_sheet("Previously Seen")
        _write_sheet(ws_seen, previously_seen_rows, "Previously Seen")

    wb.save(path)
    print(f"[✓] XLSX exported to: {path}")

# ── Hyperlink Smoke Test ─────────────────────────────────────────────────────

def smoke_test_hyperlinks(path: Path, sample_size: int = 10) -> bool:
    """Verify XLSX hyperlinks: (1) cell value == hyperlink target for every
    row, (2) a random sample of URLs resolve via HTTP HEAD.

    Returns True if all checks pass, False if any mismatch found.
    """
    if not HAS_OPENPYXL:
        return True
    wb = load_workbook(path)
    all_ok = True
    total_links = 0
    total_mismatches = 0

    for sn in wb.sheetnames:
        ws = wb[sn]
        mismatches = 0
        urls_to_check: list[str] = []
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            url_cell = row[OUTPUT_FIELDS.index("job_url")]
            val = str(url_cell.value or "")
            hl = url_cell.hyperlink
            target = hl.target if hl else None
            if val and target:
                total_links += 1
                if val != target:
                    mismatches += 1
                    total_mismatches += 1
                    if mismatches <= 5:
                        print(f"  [!] {sn} row {url_cell.row}: value ≠ hyperlink")
                        print(f"        value:  {val[:80]}")
                        print(f"        target: {target[:80]}")
                urls_to_check.append(val)
            elif val and not target:
                total_links += 1
                total_mismatches += 1
                all_ok = False
                print(f"  [!] {sn} row {url_cell.row}: has URL value but no hyperlink")
        if mismatches:
            all_ok = False
        print(f"  [{sn}] {ws.max_row - 1} rows, {mismatches} hyperlink mismatches")

    # HTTP sample check — verify a random subset of URLs actually resolve
    import random
    random.seed(42)  # deterministic sample
    all_urls: list[str] = []
    for sn in wb.sheetnames:
        if sn == "Previously Seen":
            continue
        ws = wb[sn]
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            val = str(row[OUTPUT_FIELDS.index("job_url")].value or "")
            if val:
                all_urls.append(val)

    sample = random.sample(all_urls, min(sample_size, len(all_urls))) if all_urls else []
    http_ok = 0
    http_fail = 0
    if sample:
        print(f"\n[*] HTTP HEAD check on {len(sample)} sample URLs...")
        for url in tqdm(sample, desc="HTTP check"):
            try:
                req = requests.head(url, headers=HEADERS, timeout=REQUEST_TIMEOUT,
                                    allow_redirects=True)
                if req.status_code < 400:
                    http_ok += 1
                else:
                    http_fail += 1
                    print(f"  [!] HTTP {req.status_code}: {url[:80]}")
            except Exception as exc:
                http_fail += 1
                print(f"  [!] HTTP error: {url[:60]}... — {exc}")
        print(f"  HTTP check: {http_ok}/{len(sample)} OK, {http_fail} failed")

    print(f"\n[*] Hyperlink smoke test: {total_links} links, {total_mismatches} mismatches, "
          f"{http_ok}/{len(sample)} HTTP OK")
    if all_ok and http_fail == 0:
        print("[✓] All hyperlinks verified")
    elif all_ok:
        print(f"[~] Hyperlinks structurally OK; {http_fail} HTTP failures (may be rate-limited)")
    else:
        print("[!] Hyperlink mismatches detected — review above")
    return all_ok

MINIMUM_DESCRIPTION_LENGTH = 50

_GERMAN_LABELS = {
    "c1_plus_required": "German C1+ required",
    "b1_b2_ok": "German B1/B2 OK",
    "preferred": "German preferred",
    "none": "",
}
_EXPERIENCE_LABELS = {
    "no_requirement_or_optional": "",
    "minimum_0_years": "0",
    "minimum_1_year": "1",
    "minimum_2_years": "2",
    "minimum_over_2_years": ">2",
}

JD_JUDGMENT_QUESTIONS = {
    "german": {
        "type": "choice",
        "instructions": (
            "Classify the job description's German-language requirement. Judge "
            "whether the level is mandatory or merely preferred, not just mentioned. "
            "C1, C2, fluent/fließend, native/Muttersprache, or verhandlungssicher "
            "is C1+ only when required; required standalone 'sehr gute "
            "Deutschkenntnisse' is also C1+. 'Sehr gute Deutsch- und "
            "Englischkenntnisse' together is B1/B2 unless an explicit C1+ level is "
            "stated. B1/B2 requirements are allowed. Preferred/nice-to-have German "
            "is not a required C1+ condition."
        ),
        "criteria": {
            "c1_plus_required": "Required C1/C2, fluent, native, verhandlungssicher, or standalone sehr gute German.",
            "b1_b2_ok": "Required B1/B2 or the conjunctive sehr gute German-and-English convention; no required C1+.",
            "preferred": "German is explicitly desired, preferred, advantageous, or optional, including preferred C1+.",
            "none": "No German requirement or preference is stated.",
        },
    },
    "experience": {
        "type": "choice",
        "instructions": (
            "Classify minimum required professional experience using the full "
            "description. Use the LOWER bound of a required range: 0-2 years "
            "means minimum 0 and is eligible; 1-3 means minimum 1 and is eligible; "
            "3-5 means minimum over 2 and is not. Distinguish required qualifications "
            "from preferred, optional, nice-to-have, or advantageous experience; "
            "optional 5 years does not disqualify. Required 'several years' or German "
            "'mehrjährige Erfahrung' means over 2. Do not infer years from "
            "senior-sounding language. If no minimum is stated (for example, "
            "'up to 2 years'), choose no requirement."
        ),
        "criteria": {
            "no_requirement_or_optional": "No required minimum, or years are only optional/preferred/desired.",
            "minimum_0_years": "A required range explicitly has a lower bound of 0.",
            "minimum_1_year": "The required lower bound is exactly 1 year.",
            "minimum_2_years": "The required lower bound is exactly 2 years.",
            "minimum_over_2_years": "The required lower bound exceeds 2 years, including required several/mehrjährige years.",
        },
    },
}


def _choice_answer(answer: object, allowed: dict[str, str], question: str) -> str:
    if not isinstance(answer, dict):
        raise ValueError(f"missing typed {question} answer")
    choice = answer.get("choice")
    if choice not in allowed:
        raise ValueError(f"invalid typed {question} choice")
    return choice


def apply_judgment_answers(row: dict, answers: dict) -> None:
    """Apply validated typed answers while preserving only exact year buckets."""
    german = _choice_answer(answers.get("german"), _GERMAN_LABELS, "German")
    experience = _choice_answer(answers.get("experience"), _EXPERIENCE_LABELS, "experience")
    row["detail_language"] = _GERMAN_LABELS[german]
    row["detail_exp_years"] = _EXPERIENCE_LABELS[experience]
    row["detail_review_status"] = "Reviewed"


async def llm_classify_all(rows: list[dict]) -> None:
    """Judge full final JD descriptions in one bounded OMP batch.

    Missing descriptions and per-item/API failures are marked for review and
    never treated as a successful no-requirement answer.
    """
    if not rows:
        return
    for row in rows:
        row["detail_language"] = ""
        row["detail_exp_years"] = ""
    states: dict[str, dict[str, str]] = {}
    for index, row in enumerate(rows):
        description = row.get("description", "")
        if not isinstance(description, str) or len(description.strip()) < MINIMUM_DESCRIPTION_LENGTH:
            row["detail_language"] = ""
            row["detail_exp_years"] = ""
            row["detail_review_status"] = "Needs review: full job description unavailable"
            continue
        row["detail_review_status"] = ""
        states[str(index)] = {"description": description}

    if not states:
        print(f"[*] Typed JD judgment: 0/{len(rows)} descriptions available; all need review")
        return

    judge_batch_api = globals().get("judge_batch")
    if not callable(judge_batch_api):
        raise RuntimeError(
            "OMP judge_batch is required for JD verification; inject it and await "
            "run_verification() from an OMP Python eval cell."
        )

    batch = None
    batch_error = None
    results = {}
    failures = {}
    try:
        batch = judge_batch_api(
            states,
            JD_JUDGMENT_QUESTIONS,
            concurrency=16,
            intent="Jobscraper German and experience review",
        )
        while batch.status()["done"] < batch.status()["total"]:
            await batch.drain(timeout=10)
        results = batch.results()
        failures = batch.failed()
    except Exception as exc:
        batch_error = exc
    finally:
        if batch is not None:
            batch.close()

    for key in states:
        row = rows[int(key)]
        if batch_error is not None:
            row["detail_review_status"] = "Needs review: typed judgment batch failed"
            continue
        if key in failures:
            row["detail_review_status"] = "Needs review: typed judgment failed"
            continue
        answers = results.get(key)
        if not isinstance(answers, dict):
            row["detail_review_status"] = "Needs review: typed judgment returned no result"
            continue
        try:
            apply_judgment_answers(row, answers)
        except (TypeError, ValueError):
            row["detail_review_status"] = "Needs review: invalid typed judgment"

    reviewed = sum(row.get("detail_review_status") == "Reviewed" for row in rows)
    needs_review = len(rows) - reviewed
    print(f"[*] Typed JD judgment: {reviewed}/{len(rows)} reviewed, {needs_review} need review")

# ── Main Orchestration ───────────────────────────────────────────────────────

def _run_date_from_path(csv_path: Path) -> date:
    try:
        return date.fromisoformat(csv_path.parent.name)
    except ValueError:
        return datetime.now().date()


async def run_verification(csv_path: Path, force: bool = False) -> None:
    """Verify and classify only Germany-eligible, previously unseen job rows."""
    rows = load_csv(csv_path)
    input_count = len(rows)
    if not rows:
        print(f"[!] No rows found in {csv_path}")
        return

    run_date = _run_date_from_path(csv_path)
    today_str = run_date.isoformat()
    previously_seen_urls = load_seen_job_urls(JOB_SEARCH_DIR, run_date)
    previously_seen_rows = []
    location_dropped = 0
    candidates = []
    for row in rows:
        location = row.get("location", "")
        if (not is_germany_location(location)
                or (row.get("role_type") == "Working Student"
                    and not is_hamburg_or_kiel(location))):
            location_dropped += 1
            continue
        identity = normalize_job_url(row.get("job_url", ""))
        if identity and identity in previously_seen_urls:
            row["detail_review_status"] = "Previously seen: exact posting URL in an earlier export"
            previously_seen_rows.append(row)
            continue
        candidates.append(row)
    rows = candidates
    # Inject source descriptions before platform verification; verifiers can
    # replace snippets with longer descriptions acquired from their APIs.
    json_path = csv_path.with_suffix(".json")
    if json_path.exists():
        try:
            with json_path.open(encoding="utf-8") as stream:
                json_data = json.load(stream)
            if not isinstance(json_data, list):
                raise ValueError("expected a JSON array")
            url_to_desc = {
                job.get("job_url", ""): job["description"]
                for job in json_data
                if isinstance(job, dict) and job.get("description")
            }
            injected = 0
            for row in rows:
                url = row.get("job_url", "")
                if url in url_to_desc and not row.get("description"):
                    row["description"] = url_to_desc[url]
                    injected += 1
            if injected:
                print(f"[*] Injected descriptions from JSON for {injected} job(s)")
        except (json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
            print(f"[!] Could not load sibling descriptions from {json_path}: {exc}")

    # Re-verify missing descriptions even when an earlier pass marked the row
    # active; verification may acquire the JD needed for a safe judgment.
    to_verify: list[tuple[int, dict]] = []
    already_verified = 0
    for index, row in enumerate(rows):
        existing = row.get("verified_active", "")
        description = row.get("description", "")
        has_sufficient_description = (
            isinstance(description, str)
            and len(description.strip()) >= MINIMUM_DESCRIPTION_LENGTH
        )
        if existing and not force and has_sufficient_description:
            already_verified += 1
        else:
            to_verify.append((index, row))

    if already_verified:
        print(f"[*] {already_verified} row(s) already verified — skipping (use --force to re-verify)")

    platform_groups: dict[str, list[tuple[int, dict]]] = {}
    for index, row in to_verify:
        platform = row.get("job_board", "Unknown")
        platform_groups.setdefault(platform, []).append((index, row))

    for platform in sorted(platform_groups):
        print(f"    {platform}: {len(platform_groups[platform])} URL(s)")

    print(f"\n[*] Loading cross-run history for reposted detection...")
    old_title_keys, recent_urls = _load_repost_data(JOB_SEARCH_DIR, today_str)
    print(f"    Loaded {len(old_title_keys)} title keys from runs >{REPOST_CROSS_RUN_DAYS} days ago")
    print(f"    Loaded {len(recent_urls)} URLs from most recent previous run (carryover detection)")
    print(f"    Loaded {len(previously_seen_urls)} exact URL identities from all earlier exports")

    today_max_linkedin_id = max(
        (
            extract_linkedin_job_id(row.get("job_url", ""))
            for row in rows
            if row.get("job_board") == "LinkedIn"
        ),
        default=0,
    )
    if today_max_linkedin_id:
        print(f"    Current export max LinkedIn job ID: {today_max_linkedin_id}")

    all_results: dict[int, dict] = {}
    if platform_groups:
        with ThreadPoolExecutor(max_workers=len(platform_groups)) as executor:
            future_to_platform = {
                executor.submit(
                    verify_platform_batch,
                    platform,
                    [row for _, row in group],
                ): (platform, group)
                for platform, group in platform_groups.items()
            }
            for future in tqdm(
                as_completed(future_to_platform),
                total=len(future_to_platform),
                desc="Verifying",
            ):
                platform, group = future_to_platform[future]
                try:
                    batch_results = future.result()
                    for local_index, result in batch_results:
                        original_index = group[local_index][0]
                        all_results[original_index] = result
                    print(f"  [✓] {platform}: done ({len(batch_results)} verified)")
                except Exception as exc:
                    print(f"  [!] {platform}: batch failed ({exc})")
                    for original_index, _ in group:
                        all_results[original_index] = _empty_result()
    else:
        print("[*] No platform refresh required; running final JD review on existing rows.")

    # Merge platform results before judgment so the judge sees the final,
    # longest description acquired for each actual row.
    for index, row in enumerate(rows):
        result = all_results.get(index)
        if result:
            prior_description = row.get("description", "")
            result_description = result.get("description", "")
            row.update(result)
            if (isinstance(prior_description, str)
                    and len(prior_description) > len(result_description or "")):
                row["description"] = prior_description
        is_repost = detect_reposted(
            row, today_str, old_title_keys, today_max_linkedin_id, recent_urls
        )
        row["detail_reposted"] = (
            "True" if is_repost else ("False" if row.get("job_board") == "LinkedIn" else "")
        )

    await llm_classify_all(rows)

    print(f"\n[*] Loading already-applied data...")
    if rows:
        applied_data = load_applied_data()
        llm_matches = load_llm_matches()
        if llm_matches is not None:
            print("[*] Using LLM-classified already-applied matches")
        else:
            print("[*] LLM matches not found — falling back to deterministic URL+key match")
    else:
        applied_data = {}
        llm_matches = None
    main_rows = []
    reposted_rows = []
    staffing_rows = []
    already_applied_rows = []
    review_rows = []
    closed_count = german_dropped = staffing_count = exp_dropped = 0
    reposted_count = already_applied_count = enriched_count = 0

    for row in rows:
        if str(row.get("detail_review_status", "")).startswith("Needs review:"):
            review_rows.append(row)
            continue

        active = row.get("verified_active", "")
        lang = row.get("detail_language", "")
        exp_str = row.get("detail_exp_years", "")
        is_repost = row.get("detail_reposted", "") == "True"

        if is_already_applied(
            row.get("company", ""), row.get("title", ""),
            row.get("job_url", ""), applied_data, llm_matches
        ):
            already_applied_count += 1
            already_applied_rows.append(row)
            continue

        if is_repost:
            reposted_count += 1
            reposted_rows.append(row)
            continue

        if active == "False":
            closed_count += 1
            continue
        if lang == "German C1+ required":
            german_dropped += 1
            continue
        if exp_str == ">2":
            exp_dropped += 1
            continue
        if exp_str:
            try:
                if int(exp_str) > 2:
                    exp_dropped += 1
                    continue
            except (ValueError, TypeError):
                review_rows.append(row)
                continue

        if STAFFING_COMPANIES.search(row.get("company", "")):
            staffing_count += 1
            staffing_rows.append(row)
            continue

        main_rows.append(row)
        if (row.get("detail_exp_years") or row.get("detail_salary")
                or row.get("detail_remote") or lang in ("German preferred", "German B1/B2 OK")):
            enriched_count += 1

    stem = csv_path.stem
    if stem.endswith("_verified"):
        stem = stem[:-len("_verified")]
    if stem.endswith("_deduped"):
        stem = stem[:-len("_deduped")]
    out_path = csv_path.parent / f"{stem}_verified.xlsx"

    save_xlsx(
        out_path,
        main_rows,
        reposted_rows,
        staffing_rows,
        already_applied_rows,
        review_rows,
        previously_seen_rows,
    )
    print(f"\n[*] Running hyperlink smoke test on {out_path.name}...")
    smoke_test_hyperlinks(out_path)

    print()
    print("=" * 60)
    print("  Verification Summary")
    print("=" * 60)
    print(f"  Input:                {input_count} jobs")
    print(f"  To Apply:             {len(main_rows)}")
    print(f"  Reposted:             {reposted_count}")
    print(f"  Staffing:             {staffing_count}")
    print(f"  Already Applied:      {already_applied_count}")
    print(f"  Previously Seen:      {len(previously_seen_rows)}")
    print(f"  Needs Review:         {len(review_rows)}")
    print(f"  Excluded by location: {location_dropped}")
    print(f"  Closed/removed:       {closed_count}")
    print(f"  Dropped (German C1+): {german_dropped}")
    print(f"  Dropped (exp > 2y):   {exp_dropped}")
    print(f"  Enriched:             {enriched_count}")
    if already_verified:
        print(f"  Already verified:     {already_verified}")
    print(f"  Output: {out_path}")
    print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(
        description="Verify jobs via OMP typed JD judgment and platform checks."
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

    await run_verification(csv_path, force=args.force)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        raise SystemExit(str(exc))
