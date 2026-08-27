#!/usr/bin/env python3
"""
Apify Job Fetcher, Deduplicator & Dated CSV Exporter for OMP Session
Platforms: LinkedIn (free HTML), Indeed (Apify), Arbeitnow, Startup.jobs, Xing, Stepstone, Glassdoor, ATS Direct (Greenhouse/SmartRecruiters/Ashby)
Filters: Freshness (<24h), Experience (<=2 yrs), Germany location guard, Working Student (Hamburg & Kiel ONLY),
         Internships (Germany-wide), Title relevance (data/analytics/AI/SQL/Python keywords)
Output Path: /home/sagar/Skills/Jobscraper/Job Search/YYYY-MM-DD/Job_Search_<Month>_<Day>_<Year>.csv

Changelog:
  2026-08-04: Initial release. LinkedIn + Indeed + Arbeitnow + Kununu. 24 SEARCH_ROLES. ~$1.50-3.00/run.
  2026-08-05: Cost optimization (82% reduction → ~$0.55/run). Consolidated 24 → 10 SEARCH_ROLES.
              Fixed LinkedIn maxItems bug (was sending maxItems, actor uses count). Fixed Indeed missing
              title param (generic garbage results). Added maxTotalChargeUsd safety caps. Dropped Kununu
              (broken actor). Added Startup.jobs (free cloudscraper). Added is_relevant_title() post-filter.
  2026-08-06: Added Xing (free cloudscraper, data-testid + <time dateTime>). Added Stepstone (free
              cloudscraper, path-based URLs with ag=age_1 24h filter, data-at SSR attributes, German
              relative time parsing via parse_stepstone_timeago()). Pipeline now 6 platforms.
  2026-08-07: LinkedIn AI search rollout — actor autoConvertToAiSearch defaults true, softening
              location=Germany into a natural-language hint. Added Germany Location Guard in
              check_experience_and_location() (rejects 30 non-Germany countries, ambiguous locations
              pass through). f_TPR=r86400 24h filter unaffected (stays as URL filter under AI search).
              Removed dead prototype scripts (stepstone_scraper.py, csv_to_xlsx.py, merge_linkedin.py).
              Added .gitignore, README.md, CHANGELOG.md.
  2026-08-09: Added Glassdoor (free cloudscraper, JSON-LD ItemList parsing, _KE offset company
              extraction, ageInDays filtering from Next.js RSC payload). Pipeline now 7 platforms.
              No login required — search results are public SSR JSON-LD. CRITICAL FIX: Glassdoor's
              fromAge=1 URL parameter is IGNORED by SSR (React app filters client-side post-hydration).
              Without ageInDays filtering, all jobs appear as "posted today" regardless of actual age
              (30-165 days old). Now extracts ageInDays from RSC payload and filters to ageInDays==0.
  2026-08-09b: CRITICAL FIX: Indeed's datePosted='1' parameter is IGNORED by the API (same bug as
              Glassdoor's fromAge=1). 11/102 jobs (10.8%) were stale, including jobs 500+ days old.
              Added post-filter on datePublished in fetch_indeed_jobs() to reject jobs older than 24h.
              Also added defense-in-depth safety-net post-filter to fetch_linkedin_jobs() (f_TPR=r86400
              verified working 727/727, but post-filter catches any future regressions). Date-only
              timestamps (LinkedIn format) treated as end-of-day (23:59:59) to avoid false rejections.
  2026-08-10: CRITICAL FIX: Stepstone switched from cloudscraper to plain requests. Stepstone is
              behind Akamai (not Cloudflare) — cloudscraper's challenge-solving hangs on Akamai,
              causing read timeouts on port 443. Plain requests with a browser User-Agent works
              (Stepstone serves SSR HTML without anti-bot challenge). All data-at parsing unchanged.
              Also switched Xing from cloudscraper to plain requests — Xing is behind AWS CloudFront
              (not Cloudflare), so cloudscraper was unnecessary overhead with the same hang risk.
              Startup.jobs and Glassdoor genuinely need cloudscraper (Cloudflare 403 on plain requests).

See CHANGELOG.md for full version history and apify_job_search.md for detailed technical documentation.
"""

import os
import re
import csv
import json
import time
import urllib.request
import html as html_mod
import urllib.parse
try:
    import cloudscraper
except ImportError:
    cloudscraper = None
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Paths
BASE_RESUMES_DIR = Path("/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/base_files")
PORTFOLIO_DIR = Path("/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/portfolio")
JOB_SEARCH_DIR = Path("/home/sagar/Skills/Jobscraper/Job Search")

# Ensure Output Directory Exists
JOB_SEARCH_DIR.mkdir(parents=True, exist_ok=True)

# Search Parameters
# Consolidated from 24 → 10 core roles.
# LinkedIn/Indeed/Kununu search engines cover variants (Junior, Cloud, etc.) automatically.
# German terms kept where they produce distinct results not covered by English equivalents.
SEARCH_ROLES = [
    # Core Data Engineering (covers Junior, Cloud, Data Warehouse, ETL, Dateningenieur)
    "Data Engineer",
    "Analytics Engineer",
    # Business Intelligence & Analytics (covers Datenanalyst, BI Developer, BI Entwickler)
    "Data Analyst",
    # AI & Data Science (covers AI Data Engineer, GenAI, Junior Data Scientist)
    "AI Engineer",
    "Machine Learning Engineer",
    # Business / Quantitative Analytics
    "Business Analyst",
    # Database & Systems (covers Database Developer, Python Data Developer)
    "SQL Developer",
    # Internships & Working Student (distinct result sets)
    "Praktikum Data",
    "Werkstudent Data",
    "Werkstudent Business Intelligence",
]

MAX_EXP_YEARS = 2
FRESHNESS_HOURS = 24

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
if not APIFY_TOKEN:
    config_file = JOB_SEARCH_DIR.parent / "config.json"
    if config_file.exists():
        try:
            with open(config_file, encoding="utf-8") as f:
                cfg = json.load(f)
                APIFY_TOKEN = cfg.get("APIFY_TOKEN") or cfg.get("apify_token") or cfg.get("APIFY_API_KEY") or ""
        except Exception:
            pass
if not APIFY_TOKEN:
    apify_cli_auth = Path.home() / ".apify" / "auth.json"
    if apify_cli_auth.exists():
        try:
            with open(apify_cli_auth, encoding="utf-8") as f:
                cfg = json.load(f)
                APIFY_TOKEN = cfg.get("token", "")
        except Exception:
            pass

# Verified Apify actor IDs (alphanumeric form, public store actors)
ACTOR_LINKEDIN = "hKByXkMQaC5Qt9UMN"   # curious_coder/linkedin-jobs-scraper
ACTOR_INDEED = "TrtlecxAsNRbKl1na"     # valig/indeed-jobs-scraper

# Seniority & Experience Filter Regexes
EXCLUDED_TITLE_PATTERNS = re.compile(
    r"\b(senior|sr|lead|head|principal|staff|manager|director|architect|vp|chief|expert|team lead)\b",
    re.IGNORECASE
)

EXCLUDED_EXP_PATTERNS = [
    re.compile(r"\b([3-9]|\d{2,})\+?\s*(?:years?|jahre?|j\.?)\b", re.IGNORECASE),
    re.compile(r"\b(?:at least|minimum|min\.?|mindestens)\s*([3-9]|\d{2,})\s*(?:years?|jahre?)\b", re.IGNORECASE),
    re.compile(r"\b([3-9]|\d{2,})\s*bis\s*\d+\s*jahre\b", re.IGNORECASE),
    re.compile(r"\b(?:3|4|5|6|7|8|9)\s*\+\s*(?:jahre|years)\b", re.IGNORECASE),
]

# Core Tech Stack Keywords for Match Scoring
TECH_KEYWORDS = ["dbt", "airflow", "spark", "pyspark", "python", "sql", "gcp", "bigquery", "aws", "azure", "databricks", "docker", "kafka", "postgresql", "snowflake"]

# Core domain keywords that MUST appear in the job title for it to be relevant.
# This is a universal post-filter applied to ALL platforms (LinkedIn, Indeed, Arbeitnow).
# Without this, actors return garbage: "Nachtwächter" for "Data Engineer" search, etc.
DOMAIN_TITLE_KEYWORDS = re.compile(
    r"\b(data|daten\w*|analytics|analyst\w*|ai|artificial intelligence|"
    r"bi|business intelligence|sql|python|database|datenbank\w*|"
    r"machine learning|ml|deep learning|etl|pipeline|warehouse|"
    r"datapipeline|dataops|mlops|llm|genai)\b",
    re.IGNORECASE
)

# Countries commonly leaked by LinkedIn AI search (Aug 2026) when location=Germany
# is softened into a natural-language hint. Used by check_experience_and_location()
# to reject jobs whose location explicitly names a non-Germany country.
# Ambiguous locations (city-only, "Remote") pass through to avoid false rejections.
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

# Foreign CITIES seen leaking through city-only location strings (LinkedIn/
# Stepstone/Xing return e.g. "London", "Bern", "Amsterdam"). Only consulted
# when the location lacks a germany/deutschland marker. Word-boundary matched
# so German lookalikes ("Bernau bei Berlin") are NOT rejected.
NON_GERMANY_CITIES = re.compile(
    r"\b(london|amsterdam|rotterdam|the\s+hague|antwerp\w*|brussels?|bruxelles|"
    r"bern|basel|z(?:ü|ue|u)rich|gen[eè]ve|genf|graz|vienna|wien|paris|milan|milano|"
    r"rome|roma|madrid|barcelona|lisbon|lisboa|dublin|copenhagen|københavn|kopenhagen|"
    r"stockholm|oslo|helsinki|warsaw|warszawa|prague|prag|praha|budapest|"
    r"s[aã]o\s+paulo|new\s+york)\b",
    re.IGNORECASE,
)

def is_relevant_title(title: str) -> bool:
    """Check if the job title contains at least one core data/analytics/AI keyword.
    This catches false positives from all actors (Indeed returns 'Nachtwächter' for
    'Data Engineer' searches, LinkedIn returns 'Junior Software Engineer', etc.).
    """
    return bool(DOMAIN_TITLE_KEYWORDS.search(title))

def detect_language(text: str) -> str:
    """Detect if job description/title is primarily German or English."""
    text_lower = text.lower()
    german_indicators = ["und", "die", "das", "der", "mit", "für", "aufgaben", "profil", "kenntnisse", "anforderungen", "ihre", "wir"]
    english_indicators = ["and", "the", "with", "for", "responsibilities", "requirements", "skills", "your", "we", "looking"]

    de_score = sum(len(re.findall(r"\b" + w + r"\b", text_lower)) for w in german_indicators)
    en_score = sum(len(re.findall(r"\b" + w + r"\b", text_lower)) for w in english_indicators)

    return "German" if de_score > en_score else "English"

def classify_role_type(title: str, description: str) -> str:
    """Classify into Working Student, Internship, or Full-Time / Entry Level."""
    combined = f"{title} {description}".lower()
    if re.search(r"\b(werkstudent|werkstudentin|working student)\b", combined):
        return "Working Student"
    elif re.search(r"\b(intern|internship|praktikum|praktikant|praktikantin)\b", combined):
        return "Internship"
    else:
        return "Full-Time / Entry-Level"

def check_experience_and_location(title: str, description: str, location: str) -> tuple[bool, str]:
    """
    Validates role against seniority ceiling and location constraints.
    Returns (is_valid, role_type_or_reason)
    """
    # 0. Title Relevance Check — reject jobs with no data/analytics/AI keywords in title
    # This catches all actor false positives (Indeed returns "Nachtwächter" for "Data Engineer" search)
    if not is_relevant_title(title):
        return False, "Title not relevant to data/analytics/AI"

    # 1. Seniority Check
    if EXCLUDED_TITLE_PATTERNS.search(title):
        return False, "Seniority title excluded"

    for pattern in EXCLUDED_EXP_PATTERNS:
        if pattern.search(description):
            return False, "Requires >2 years experience"

    # 2. Role Type & Location Filter
    role_type = classify_role_type(title, description)
    loc_clean = location.lower()

    # 2a. Germany Location Guard — LinkedIn AI search (Aug 2026) softened
    # location=Germany into a natural-language hint, so non-Germany jobs can
    # leak through. Reject any job whose location explicitly names a non-Germany
    # country. Ambiguous locations (city-only, "Remote") pass to avoid false
    # rejections of valid German jobs. Also catches Indeed's known Germany drift.
    if "germany" not in loc_clean and "deutschland" not in loc_clean:
        for country in NON_GERMANY_COUNTRIES:
            if country in loc_clean:
                return False, f"Location outside Germany ({location})"
        if NON_GERMANY_CITIES.search(location):
            return False, f"Location outside Germany ({location})"

    # 2b. Working Student — strictly restricted to Hamburg & Kiel
    if role_type == "Working Student":
        if not ("hamburg" in loc_clean or "kiel" in loc_clean):
            return False, f"Working student outside Hamburg/Kiel ({location})"

    return True, role_type

def compute_match_score(text: str) -> int:
    """Calculate percentage match score against Sagar's core tech stack."""
    text_lower = text.lower()
    matches = sum(1 for kw in TECH_KEYWORDS if kw in text_lower)
    return min(100, int((matches / len(TECH_KEYWORDS)) * 100 * 2.5))  # Normalized score

def _norm_company(c: str) -> str:
    c = (c or "").lower().strip()
    # Strip parentheticals: "(REWE Group)", "(m/w/d)"
    c = re.sub(r"\(.*?\)", "", c)
    # Strip legal suffixes (expanded)
    c = re.sub(r"\b(gmbh|ag|inc|ltd|co|kg|se|corp|llc|group|gruppe|holding|"
               r"international|deutschland|germany|global|e\.?g\.?|gmbh & co)\b", "", c)
    # Strip punctuation, collapse whitespace
    c = re.sub(r"[^\w\s]", "", c)
    return " ".join(c.split())

def _norm_title(t: str) -> str:
    t = (t or "").lower().strip()
    # Strip parentheticals (gender markers, location hints)
    t = re.sub(r"\(.*?\)", "", t)
    # Strip seniority + gender markers
    t = re.sub(r"\b(senior|junior|lead|principal|staff|sr\.?|jr\.?)\b", "", t)
    t = re.sub(r"\b(m/w/d|m/f/d|m/w|f/m/d|w/m/d|w/m/x|m/f/x|all genders|w/m|f/w/d)\b", "", t)
    # Strip reference codes: "- REF99139A", "REF12345"
    t = re.sub(r"\bref\d+\w*\b", "", t)
    # Strip punctuation, collapse whitespace
    t = re.sub(r"[^\w\s]", "", t)
    return " ".join(t.split())

def normalize_key(company: str, title: str) -> str:
    """Generate deduplication key (enhanced: strips parentheticals, legal suffixes,
    seniority/gender markers, and REF codes for cross-platform matching)."""
    return f"{_norm_company(company)}::{_norm_title(title)}"

def _company_tokens(c: str) -> set:
    """Distinctive tokens from company name for overlap matching."""
    c = _norm_company(c)
    GENERIC = {"the", "und", "and", "de", "solutions", "consulting", "services"}
    return {t for t in c.split() if t not in GENERIC and len(t) > 2}

def _title_similarity(t1: str, t2: str) -> float:
    """Jaccard similarity of normalized title token sets."""
    s1 = set(_norm_title(t1).split())
    s2 = set(_norm_title(t2).split())
    if not s1 or not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)

def _location_match(l1: str, l2: str) -> bool:
    """Check if two location strings refer to the same city."""
    CITY_ALIASES = {
        "munich": "munich", "münchen": "munich",
        "cologne": "cologne", "köln": "cologne",
        "frankfurt": "frankfurt", "frankfurt am main": "frankfurt",
        "nuremberg": "nuremberg", "nürnberg": "nuremberg",
        "hannover": "hannover", "hanover": "hannover",
    }
    def _norm_loc(l):
        l = l.lower()
        for alias, canonical in CITY_ALIASES.items():
            if alias in l:
                return canonical
        return " ".join(re.sub(r"[^\w\s]", "", l).split())
    return _norm_loc(l1) == _norm_loc(l2)

def cross_platform_dedup(jobs: list) -> tuple[list, int]:
    """Remove cross-platform duplicates via fuzzy company+title+location matching.

    Only compares jobs across different job_board values. A job is a duplicate if:
    - company token overlap >= 0.5 (min-set denominator)
    - title Jaccard similarity >= 0.6
    - location match
    Keeps the first occurrence by platform priority order.

    Returns (deduped_jobs, removed_count).
    """
    PLATFORM_PRIORITY = {"LinkedIn": 0, "Xing": 1, "Stepstone": 2, "Indeed": 3}
    # Sort by platform priority so the preferred platform's job is kept first.
    priority = lambda job: PLATFORM_PRIORITY.get(job["job_board"], 99)
    ordered = sorted(jobs, key=priority)
    kept = []
    removed = 0
    # Track signatures of kept jobs for comparison
    kept_sigs = []  # list of (job, company_tokens, norm_title, loc_key)

    for job in ordered:
        ct = _company_tokens(job["company"])
        nt = _norm_title(job["title"])
        is_dup = False
        for kept_job, kept_ct, kept_nt, kept_loc in kept_sigs:
            # Only compare across different platforms
            if job["job_board"] == kept_job["job_board"]:
                continue
            # Company token overlap
            if not ct or not kept_ct:
                continue
            overlap = len(ct & kept_ct) / min(len(ct), len(kept_ct))
            if overlap < 0.5:
                continue
            # Title similarity
            s1, s2 = set(nt.split()), set(kept_nt.split())
            if not s1 or not s2:
                continue
            tsim = len(s1 & s2) / len(s1 | s2)
            if tsim < 0.6:
                continue
            # Location match
            if not _location_match(job["location"], kept_loc):
                continue
            # Confirmed dup — skip this job
            is_dup = True
            removed += 1
            break
        if not is_dup:
            kept.append(job)
            kept_sigs.append((job, ct, nt, job["location"]))

    return kept, removed

def normalize_job_url(url: str) -> str:
    """Normalize a job URL into a stable cross-run identity key.

    LinkedIn URLs carry per-run tracking params (position/pageNum/refId/trackingId)
    while the job ID lives in the path — drop the query string there.
    Indeed (?jk=) and Glassdoor (?jl=) carry their job IDs in the query — keep it.
    """
    url = (url or "").strip()
    if not url:
        return ""
    if "linkedin.com" in url:
        url = url.split("?", 1)[0]
    return url.rstrip("/").lower()

def load_previous_run_keys(now: datetime) -> tuple[set, set, int, int]:
    """Load dedup keys from the single most recent previous run folder.

    Compares today's run against only the immediate previous run (e.g. Friday vs
    Thursday, or vs Wednesday if Thursday was skipped). For same-day reruns, the
    most recent folder is today's own earlier run.

    Returns (url_keys, title_keys, runs_scanned, jobs_scanned):
      - url_keys: normalized job URLs from that one run.
      - title_keys: normalize_key(company, title) from that same run.
        Catches LinkedIn re-lists (new URL per run, same job) and cross-platform
        duplicates (same job on LinkedIn vs Indeed).
    """
    url_keys = set()
    title_keys = set()
    runs_scanned = 0
    jobs_scanned = 0

    # Find the most recent dated run folder (<= today)
    prev_folder = None
    for folder in sorted(JOB_SEARCH_DIR.iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        try:
            run_date = datetime.strptime(folder.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if run_date > now.date():
            continue
        if any(folder.glob("Job_Search_*.csv")):
            prev_folder = folder
            break

    if not prev_folder:
        return url_keys, title_keys, runs_scanned, jobs_scanned

    for csv_file in sorted(prev_folder.glob("Job_Search_*.csv")):
        try:
            with open(csv_file, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            continue
        runs_scanned += 1
        jobs_scanned += len(rows)
        for row in rows:
            url = normalize_job_url(row.get("job_url", ""))
            if url:
                url_keys.add(url)
            title_keys.add(normalize_key(row.get("company", ""), row.get("title", "")))

    return url_keys, title_keys, runs_scanned, jobs_scanned

def fetch_arbeitnow_jobs():
    """Fetch jobs from Arbeitnow API across all search roles."""
    jobs = []
    try:
        url = "https://www.arbeitnow.com/api/job-board-api"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=FRESHNESS_HOURS)

            for item in data.get("data", []):
                created_at = datetime.fromtimestamp(item.get("created_at", 0), tz=timezone.utc)
                if created_at < cutoff:
                    continue

                title = item.get("title", "")
                desc = item.get("description", "")
                loc = item.get("location", "Germany")


                is_valid, role_type_or_reason = check_experience_and_location(title, desc, loc)
                if not is_valid:
                    continue

                lang = detect_language(f"{title} {desc}")
                score = compute_match_score(f"{title} {desc}")

                jobs.append({
                    "language": lang,
                    "job_board": "Arbeitnow",
                    "role_type": role_type_or_reason,
                    "title": title,
                    "company": item.get("company_name", "Unknown"),
                    "location": loc,
                    "posted_at": created_at.isoformat(),
                    "exp_required": "<= 2 Years",
                    "match_score": f"{score}%",
                    "job_url": item.get("url", ""),
                    "description": desc[:500]
                })
    except Exception as e:
        print(f"[!] Error fetching Arbeitnow: {e}")
    return jobs

# startup.jobs pages to scrape (free, no Apify needed — uses cloudscraper to bypass Cloudflare)
# The main Germany page catches all jobs; category pages provide targeted results.
STARTUPJOBS_PAGES = [
    "https://startup.jobs/locations/germany",           # all jobs in Germany
    "https://startup.jobs/locations/germany/data-engineer",
    "https://startup.jobs/locations/germany/data-analyst",
    "https://startup.jobs/locations/germany/ai-engineer",
    "https://startup.jobs/locations/germany/data-scientist",
    "https://startup.jobs/locations/germany/business-analyst",
    "https://startup.jobs/locations/germany/analytics-engineer",
]

def fetch_startupjobs_jobs():
    """Fetch jobs from startup.jobs via HTML scraping (free, no Apify).
    Uses cloudscraper to bypass Cloudflare challenge. Fetches category pages
    and parses job listings from server-rendered HTML using data-post-template-target attributes.
    """
    if not cloudscraper:
        print("[!] cloudscraper not installed — skipping startup.jobs. Install with: pip install cloudscraper")
        return []

    jobs = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)
    scraper = cloudscraper.create_scraper()
    seen_urls = set()  # dedup across all pages

    for page_url in STARTUPJOBS_PAGES:
        label = page_url.split("/germany/")[-1] if "/germany/" in page_url else "all"
        try:
            r = scraper.get(page_url, timeout=15)
            if r.status_code != 200:
                print(f"[!] startup.jobs/{label}: HTTP {r.status_code}")
                continue
            raw = r.text
        except Exception as e:
            print(f"[!] startup.jobs/{label} fetch error: {e}")
            continue

        # Parse using data-post-template-target attributes
        companies = re.findall(r'data-post-template-target="companyName"[^>]*>([^<]+)<', raw)
        titles = re.findall(r'data-post-template-target="title"[^>]*href="([^"]+)"[^>]*>.*?<div[^>]*>([^<]+)</div>', raw, re.DOTALL)
        loc_blocks = re.findall(r'data-post-template-target="location"[^>]*>(.*?)</div>', raw, re.DOTALL)
        locations = []
        for block in loc_blocks:
            parts = re.findall(r'>([^<]+)<', block)
            loc_str = ", ".join(p.strip() for p in parts if p.strip() and p.strip() != ",")
            locations.append(loc_str)
        timestamps = re.findall(r'data-post-template-target="timestamp"[^>]*>([^<]*)<', raw)

        page_count = 0
        for i, (job_url, title) in enumerate(titles):
            if job_url in seen_urls:
                continue
            seen_urls.add(job_url)

            title = html_mod.unescape(title.strip())
            company = html_mod.unescape(companies[i].strip()) if i < len(companies) else "Unknown"
            location = html_mod.unescape(locations[i].strip()) if i < len(locations) else "Germany"
            posted_str = html_mod.unescape(timestamps[i].strip()) if i < len(timestamps) else ""

            # Parse date and enforce 24h freshness
            posted_dt = None
            if posted_str:
                try:
                    posted_dt = datetime.strptime(posted_str, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
                    if posted_dt < cutoff:
                        continue
                except ValueError:
                    pass

            # No description available from listing page — use title for filtering
            desc = ""
            is_valid, role_type_or_reason = check_experience_and_location(title, desc, location)
            if not is_valid:
                continue

            jobs.append({
                "language": detect_language(title),
                "job_board": "Startup.jobs",
                "role_type": role_type_or_reason,
                "title": title,
                "company": company,
                "location": location,
                "posted_at": posted_str or "Last 24h",
                "exp_required": "<= 2 Years",
                "match_score": f"{compute_match_score(title)}%",
                "job_url": f"https://startup.jobs{job_url}",
                "description": ""
            })
            page_count += 1

        print(f"    startup.jobs/{label}: {page_count} new jobs (total seen: {len(seen_urls)})")

    return jobs

def fetch_xing_jobs():
    """Fetch jobs from Xing.com via HTML scraping (free, no Apify).
    Uses plain requests — Xing is behind AWS CloudFront (not Cloudflare), so
    no anti-bot bypass needed. cloudscraper worked but was unnecessary overhead
    with the same Akamai-style hang risk as Stepstone if cloudscraper updates
    its challenge handling. Fetches search result pages for each SEARCH_ROLE
    and parses job listings from server-rendered HTML. Relies on data-testid
    attributes (stable test IDs) and <time dateTime> ISO timestamps for 24h
    freshness filtering. Sponsored listings (no dateTime) are skipped.
    """
    if requests is None:
        print("[!] requests not installed — skipping Xing. Install with: pip install requests")
        return []

    jobs = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    })
    seen_urls = set()
    XING_PAGES_PER_ROLE = 3  # 20 results/page × 3 = 60 max per role

    for role in SEARCH_ROLES:
        role_fresh = 0
        role_raw = 0

        for page in range(1, XING_PAGES_PER_ROLE + 1):
            url = f"https://www.xing.com/search/in/jobs?keywords={urllib.parse.quote(role)}&location=germany&page={page}"
            try:
                r = session.get(url, timeout=15)
                if r.status_code != 200:
                    break
                r.encoding = "utf-8"  # Xing sends UTF-8
                raw = r.text
            except Exception as e:
                print(f"[!] Xing fetch error ({role} page {page}): {e}")
                break

            # Split into job cards by styled-components class prefix (hash-independent)
            cards = re.split(r"job-teaser-list-item-styles__Card", raw)
            job_cards = [c for c in cards if "job-teaser-list-title" in c]

            page_fresh = 0
            for card in job_cards:
                # URL — skip /jobs/search/ (promoted recommendation links)
                url_m = re.search(r'href="(/jobs/(?!search)[^"]+)"', card)
                if not url_m:
                    continue
                job_url = url_m.group(1)
                if job_url in seen_urls:
                    continue
                seen_urls.add(job_url)
                role_raw += 1

                # Date — ISO datetime from <time dateTime="..."> (sponsored jobs lack this).
                # Per "false positives > false negatives" rule: if no date or unparseable,
                # include the job (let title/seniority filters handle it, cross-run dedup
                # catches repeats). Only reject jobs with a confirmed date older than 24h.
                date_m = re.search(r'dateTime="([^"]+)"', card)
                posted_dt = None
                if date_m:
                    try:
                        posted_dt = datetime.fromisoformat(date_m.group(1).replace("Z", "+00:00"))
                    except ValueError:
                        posted_dt = None

                if posted_dt and posted_dt < cutoff:
                    continue  # confirmed stale — skip

                # Title
                title_m = re.search(r'job-teaser-list-title">([^<]+)<', card)
                if not title_m:
                    continue
                title = html_mod.unescape(title_m.group(1).strip())

                # Company — aria-label on <img> is most reliable
                company_m = re.search(r'aria-label="([^"]+)"[^>]*loading="lazy"', card)
                company = html_mod.unescape(company_m.group(1).strip()) if company_m else "Unknown"

                # Location — text before <b> tag inside multi-location-display container
                loc_m = re.search(
                    r'multi-location-display-styles__Container[^>]*>.*?data-xds="BodyCopy">([^<]+)<b',
                    card, re.DOTALL
                )
                location = html_mod.unescape(loc_m.group(1).strip()) if loc_m else "Germany"

                # Apply existing filters (title relevance, seniority, experience, location)
                desc = ""  # no description from listing page
                is_valid, role_type_or_reason = check_experience_and_location(title, desc, location)
                if not is_valid:
                    continue

                jobs.append({
                    "language": detect_language(title),
                    "job_board": "Xing",
                    "role_type": role_type_or_reason,
                    "title": title,
                    "company": company,
                    "location": location,
                    "posted_at": posted_dt.isoformat() if posted_dt else "Last 24h",
                    "exp_required": "<= 2 Years",
                    "match_score": f"{compute_match_score(title)}%",
                    "job_url": f"https://www.xing.com{job_url}",
                    "description": ""
                })
                page_fresh += 1
                role_fresh += 1

            # Stop paginating if no fresh jobs on this page (results are relevance-sorted, not date-sorted)
            if page_fresh == 0 and page > 1:
                break

        print(f"    Xing/{role}: {role_fresh} fresh (of {role_raw} raw)")

    return jobs

def parse_stepstone_timeago(timeago: str) -> datetime:
    """Convert German relative time string to approximate UTC datetime.

    Examples: 'vor 49 Minuten', 'vor 1 Stunde', 'vor 3 Stunden', 'vor 1 Tag'
    """
    now = datetime.now(timezone.utc)
    m = re.match(r"vor\s+(\d+)\s+(Minute|Stunde|Stunden|Tag|Tagen)", timeago, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        if "minute" in unit:
            return now - timedelta(minutes=n)
        elif "stunde" in unit:
            return now - timedelta(hours=n)
        elif "tag" in unit:
            return now - timedelta(days=n)
    return now
def fetch_stepstone_jobs():
    """Fetch jobs from Stepstone.de via HTML scraping (free, no Apify).
    Uses plain requests to fetch server-side rendered search result pages.
    Stepstone is behind Akamai (not Cloudflare), so cloudscraper's challenge-
    solving hangs and causes read timeouts on port 443. Plain requests with a
    browser User-Agent works — Stepstone serves SSR HTML without anti-bot
    challenge. SSR-renders job cards in the initial HTML with data-at
    attributes for each field. Uses path-based URLs (/jobs/{slug}/in-deutschland)
    — the query-param format (?keyword=...) returns generic results regardless
    of the keyword. The ag=age_1 parameter pre-filters to last 24h ('Neuer als 24h').
    """
    if requests is None:
        print("[!] requests not installed — skipping Stepstone. Install with: pip install requests")
        return []

    jobs = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    })
    seen_urls = set()
    STEPSTONE_PAGES_PER_ROLE = 3  # 25 results/page × 3 = 75 max per role

    for role in SEARCH_ROLES:
        role_fresh = 0
        role_raw = 0
        slug = role.lower().replace(" ", "-")

        for page in range(1, STEPSTONE_PAGES_PER_ROLE + 1):
            url = (
                f"https://www.stepstone.de/jobs/{slug}/in-deutschland"
                f"?sort=2"          # sort by date (Datum)
                f"&ag=age_1"        # freshness: last 24h
                f"&page={page}"
            )
            try:
                r = session.get(url, timeout=15)
                if r.status_code != 200:
                    print(f"[!] Stepstone/{role} page {page}: HTTP {r.status_code}")
                    break
                r.encoding = "utf-8"
                raw = r.text
            except Exception as e:
                print(f"[!] Stepstone fetch error ({role} page {page}): {e}")
                break

            # Strip <style> and <svg> blocks — they contain CSS/paths that
            # interfere with regex parsing of data-at attributes.
            clean = re.sub(r'<style[^>]*>.*?</style>', '', raw, flags=re.DOTALL)
            clean = re.sub(r'<svg[^>]*>.*?</svg>', '', clean, flags=re.DOTALL)

            # Split into job cards by <article data-at="job-item"
            cards = re.split(r'<article[^>]*data-at="job-item"', clean)
            job_cards = [c.split('</article>')[0] for c in cards if 'data-at="job-item-title"' in c]

            page_fresh = 0
            for card in job_cards:
                # URL — href on the job-item-title link
                url_m = re.search(r'href="(/stellenangebote--[^"]+)"', card)
                if not url_m:
                    continue
                job_url = url_m.group(1)
                if job_url in seen_urls:
                    continue
                seen_urls.add(job_url)
                role_raw += 1

                # Title — text content inside the job-item-title link
                title_m = re.search(r'data-at="job-item-title"[^>]*>(.*?)</a>', card, re.DOTALL)
                if not title_m:
                    continue
                title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
                title = html_mod.unescape(title)

                # Company — text inside a <div> within the TEXT span after company-name
                company_m = re.search(
                    r'data-at="job-item-company-name"[^>]*>.*?data-genesis-element="TEXT"[^>]*>(.*?)</span>\s*</span>\s*</span>',
                    card, re.DOTALL
                )
                if company_m:
                    company = re.sub(r'<[^>]+>', '', company_m.group(1)).strip()
                else:
                    company = "Unknown"
                company = html_mod.unescape(company)

                # Location — text inside the TEXT span within job-item-location
                loc_m = re.search(
                    r'data-at="job-item-location"[^>]*>.*?data-genesis-element="TEXT"[^>]*>([^<]+)<',
                    card, re.DOTALL
                )
                location = html_mod.unescape(loc_m.group(1).strip()) if loc_m else "Germany"

                # Date — inside <time> tag within job-item-timeago
                time_m = re.search(
                    r'data-at="job-item-timeago"[^>]*><time[^>]*>([^<]+)</time>', card
                )
                timeago = time_m.group(1).strip() if time_m else ""
                posted_dt = parse_stepstone_timeago(timeago) if timeago else now

                # Freshness check (redundant with ag=age_1, but catches edge cases)
                if posted_dt < cutoff:
                    continue

                # Description snippet from jobcard-content
                desc_m = re.search(r'data-at="jobcard-content"[^>]*>(.*?)</div>', card, re.DOTALL)
                desc = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip() if desc_m else ""
                desc = html_mod.unescape(desc)

                # Apply existing filters (title relevance, seniority, experience, location)
                is_valid, role_type_or_reason = check_experience_and_location(title, desc, location)
                if not is_valid:
                    continue

                jobs.append({
                    "language": detect_language(title),
                    "job_board": "Stepstone",
                    "role_type": role_type_or_reason,
                    "title": title,
                    "company": company,
                    "location": location,
                    "posted_at": posted_dt.isoformat(),
                    "exp_required": "<= 2 Years",
                    "match_score": f"{compute_match_score(title)}%",
                    "job_url": f"https://www.stepstone.de{job_url}",
                    "description": desc
                })
                page_fresh += 1
                role_fresh += 1

            # Stop paginating if no fresh jobs on this page
            if page_fresh == 0:
                break

        print(f"    Stepstone/{role}: {role_fresh} fresh (of {role_raw} raw)")

    return jobs

def fetch_glassdoor_jobs():
    """Fetch jobs from Glassdoor.de via HTML scraping (free, no Apify).
    Uses cloudscraper with chrome emulation to bypass Cloudflare. Glassdoor SSR
    embeds job listings as JSON-LD <script type="application/ld+json"> ItemList
    tags (titles + URLs) and ageInDays in the Next.js RSC payload.

    CRITICAL: Glassdoor's fromAge URL parameter is IGNORED by SSR — the React app
    applies date filtering client-side after hydration. The SSR HTML always returns
    unfiltered results (mostly 30-165 days old). We extract ageInDays from the RSC
    payload and filter server-side: only ageInDays == 0 (posted today) passes.

    Company names are extracted from URL slugs using _KE{start},{end} character
    offsets (Glassdoor's KO/KE encoding). Location defaults to "Germany" (search
    uses locId=26 = Germany-wide). Cloudflare blocks ~60% of requests, so each
    page fetch retries up to 8 times with fresh scraper instances.
    """
    if not cloudscraper:
        print("[!] cloudscraper not installed — skipping Glassdoor. Install with: pip install cloudscraper")
        return []

    jobs = []
    seen_urls = set()
    GLASSDOOR_PAGES_PER_ROLE = 3  # 30 results/page × 3 = 90 max per role
    GLASSDOOR_MAX_RETRIES = 8     # ~40% success rate → 8 retries = 98%+ reliability

    def _fetch_with_retry(url):
        """Fetch a Glassdoor URL with fresh scraper instances and retry on Cloudflare 403."""
        for attempt in range(GLASSDOOR_MAX_RETRIES):
            try:
                s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
                r = s.get(url, timeout=20)
                if r.status_code == 200 and 'application/ld+json' in r.text:
                    return r.text
            except Exception:
                pass
            time.sleep(2)
        return None

    def _extract_age_lookup(raw):
        """Extract {listingId: ageInDays} from the Next.js RSC payload.

        The RSC payload (self.__next_f.push) contains escaped JSON with job data.
        Each job entry has: \\"ageInDays\\":NNN ... listingId:LLL
        ageInDays comes BEFORE listingId in each entry. We pair them by finding
        each listingId and searching backward for the nearest ageInDays.
        """
        lid_matches = list(re.finditer(r'listingId["\\]*:(\d+)', raw))
        age_matches = list(re.finditer(r'\\"ageInDays\\":(\d+)', raw))

        lookup = {}
        for lid_m in lid_matches:
            lid = lid_m.group(1)
            lid_pos = lid_m.start()
            # Find nearest ageInDays BEFORE this listingId (within 3000 chars)
            nearest_age = None
            nearest_dist = float('inf')
            for age_m in age_matches:
                dist = lid_pos - age_m.end()
                if 0 < dist < 3000 and dist < nearest_dist:
                    nearest_age = int(age_m.group(1))
                    nearest_dist = dist
            if lid not in lookup or nearest_age is not None:
                lookup[lid] = nearest_age
        return lookup

    for role in SEARCH_ROLES:
        role_fresh = 0
        role_raw = 0
        role_stale = 0

        for page in range(1, GLASSDOOR_PAGES_PER_ROLE + 1):
            url = (
                f"https://www.glassdoor.de/Job/jobs.htm"
                f"?sc.keyword={urllib.parse.quote(role)}"
                f"&locT=C&locId=26"   # locT=C (country), locId=26 (Germany)
                f"&page={page}"
            )

            raw = _fetch_with_retry(url)
            if not raw:
                print(f"[!] Glassdoor/{role} page {page}: blocked by Cloudflare after {GLASSDOOR_MAX_RETRIES} retries")
                break

            # Extract JSON-LD ItemList (contains job titles + URLs)
            json_scripts = re.findall(
                r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                raw, re.DOTALL
            )

            page_items = []
            for js in json_scripts:
                try:
                    data = json.loads(js)
                    if data.get('@type') == 'ItemList':
                        page_items = data.get('itemListElement', [])
                        break
                except (json.JSONDecodeError, KeyError):
                    continue

            if not page_items:
                break  # no JSON-LD data on this page

            # Extract ageInDays lookup from RSC payload
            age_lookup = _extract_age_lookup(raw)

            page_fresh = 0
            for item in page_items:
                job_url = item.get('url', '')
                if not job_url or job_url in seen_urls:
                    continue
                seen_urls.add(job_url)
                role_raw += 1

                title = html_mod.unescape(item.get('name', '').strip())
                if not title:
                    continue

                # Extract jl ID to look up ageInDays
                jl_m = re.search(r'jl=(\d+)', job_url)
                if not jl_m:
                    continue
                jl_id = jl_m.group(1)

                # Filter by ageInDays — only keep jobs posted today (ageInDays == 0).
                # GLASSDOOR EXCEPTION: Unlike other platforms where "no date = include"
                # (false positives > false negatives), Glassdoor's SSR IGNORES the
                # fromAge URL param and serves unfiltered results (0/30 fresh, 16/30
                # stale at 15-179 days old in live testing). The ageInDays filter is
                # the ONLY barrier against stale jobs. Jobs missing from age_lookup
                # are from an unfiltered result set and more likely old than fresh.
                # Verified 2026-08-23: including None jobs let 42 unconfirmed jobs
                # through with 0 confirmed fresh — all were ageInDays=None.
                age_in_days = age_lookup.get(jl_id)
                if age_in_days is None:
                    continue  # can't verify freshness — skip (Glassdoor SSR is unfiltered)
                if age_in_days > 0:
                    role_stale += 1
                    continue  # stale job — skip

                # Extract company from URL slug using _KE{start},{end} offsets
                company = "Unknown"
                slug_m = re.search(r'/job-listing/(.+?)-JV_', job_url)
                ke_m = re.search(r'_KE(\d+),(\d+)', job_url)
                if slug_m and ke_m:
                    slug = slug_m.group(1)
                    ke_start = int(ke_m.group(1))
                    ke_end = int(ke_m.group(2))
                    if ke_end <= len(slug):
                        company_slug = slug[ke_start:ke_end]
                        company = company_slug.replace('-', ' ').strip().title()

                # Location not in JSON-LD — default to Germany (search is Germany-wide)
                location = "Germany"

                # posted_at based on ageInDays (0 = today, always int here — None was filtered above)
                posted_dt = datetime.now(timezone.utc) - timedelta(days=age_in_days)
                posted_at_str = posted_dt.isoformat()

                # Description not available from search page
                desc = ""

                # Apply existing filters (title relevance, seniority, experience, location)
                is_valid, role_type_or_reason = check_experience_and_location(title, desc, location)
                if not is_valid:
                    continue

                jobs.append({
                    "language": detect_language(title),
                    "job_board": "Glassdoor",
                    "role_type": role_type_or_reason,
                    "title": title,
                    "company": company,
                    "location": location,
                    "posted_at": posted_at_str,
                    "exp_required": "<= 2 Years",
                    "match_score": f"{compute_match_score(title)}%",
                    "job_url": job_url,
                    "description": desc
                })
                page_fresh += 1
                role_fresh += 1

            # Stop paginating if no fresh jobs on this page
            if page_fresh == 0:
                break

        print(f"    Glassdoor/{role}: {role_fresh} fresh (of {role_raw} raw, {role_stale} stale)")

    return jobs

def fetch_last_run_dataset(actor_id: str):
    try:
        runs_url = f"https://api.apify.com/v2/acts/{actor_id}/runs?desc=1&limit=1&token={APIFY_TOKEN}"
        req = urllib.request.Request(runs_url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            items = data.get("data", {}).get("items", [])
            if items:
                dataset_id = items[0].get("defaultDatasetId")
                status = items[0].get("status")
                for _ in range(30):
                    if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                        break
                    time.sleep(10)
                    try:
                        with urllib.request.urlopen(req, timeout=30) as r2:
                            items = json.loads(r2.read().decode()).get("data", {}).get("items", [])
                            if items:
                                status = items[0].get("status")
                    except Exception:
                        pass
                if dataset_id:
                    ds_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_TOKEN}&clean=true"
                    with urllib.request.urlopen(urllib.request.Request(ds_url), timeout=60) as resp2:
                        return json.loads(resp2.read().decode())
    except Exception as err:
        print(f"[!] Error fetching fallback dataset for actor {actor_id}: {err}")
    return []

def run_apify_actor(actor_id: str, run_input: dict, label: str = "Apify Actor", max_charge_usd: float = 0.50):
    """Start an Apify actor asynchronously, poll with live status logs, and return dataset items."""
    if not APIFY_TOKEN:
        return []

    start_url = f"https://api.apify.com/v2/acts/{actor_id}/runs?token={APIFY_TOKEN}&maxTotalChargeUsd={max_charge_usd}"
    req_data = json.dumps(run_input).encode("utf-8")
    req = urllib.request.Request(start_url, data=req_data, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            run_info = json.loads(resp.read().decode()).get("data", {})
    except Exception as e:
        print(f"[!] Failed to start {label} ({actor_id}): {e}", flush=True)
        return []

    run_id = run_info.get("id")
    dataset_id = run_info.get("defaultDatasetId")
    if not run_id or not dataset_id:
        print(f"[!] {label} returned invalid run info", flush=True)
        return []

    print(f"[➔] Started {label} (Run ID: {run_id}). Polling status...", flush=True)

    poll_url = f"https://api.apify.com/v2/acts/{actor_id}/runs/{run_id}?token={APIFY_TOKEN}"
    start_time = time.time()
    max_poll_seconds = 300  # safety cap: Apify actors should finish well within 5 min
    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(poll_url), timeout=30) as resp:
                status_data = json.loads(resp.read().decode()).get("data", {})
                status = status_data.get("status")
                elapsed = int(time.time() - start_time)

                if status in ("SUCCEEDED", "FINISHED"):
                    print(f"[✓] {label} completed in {elapsed}s. Fetching dataset...", flush=True)
                    break
                elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    print(f"[!] {label} run failed with status '{status}' after {elapsed}s", flush=True)
                    return []
                elif elapsed >= max_poll_seconds:
                    print(f"[!] {label} exceeded {max_poll_seconds}s poll timeout (status={status}); giving up", flush=True)
                    return []
                else:
                    print(f"    ... {label} status: {status} ({elapsed}s elapsed)", flush=True)
        except Exception as e:
            print(f"    ... warning checking {label} status: {e}", flush=True)
        time.sleep(6)

    ds_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={APIFY_TOKEN}&clean=true"
    try:
        with urllib.request.urlopen(urllib.request.Request(ds_url), timeout=60) as resp:
            items = json.loads(resp.read().decode())
            print(f"[✓] Received {len(items)} items from {label}.", flush=True)
            return items
    except Exception as e:
        print(f"[!] Failed to fetch dataset items for {label}: {e}", flush=True)
        return []

def fetch_linkedin_jobs_free():
    """Fetch LinkedIn jobs via free HTML scraping (no Apify, $0 cost).
    Scrapes the public LinkedIn jobs search page with f_TPR=r86400 (24h filter).

    Multi-city search strategy: LinkedIn caps results at 60 per page with no
    pagination (start= parameter is ignored). Searching "Germany" alone misses
    jobs beyond the first 60. To maximize coverage, each role is searched across
    multiple German cities — city-level results have different rankings and
    overlap only partially with the Germany-wide search.

    Parallelism: 10 search roles run in parallel via ThreadPoolExecutor (I/O bound
    — GIL released during requests). Each role scrapes 6 locations sequentially
    (0.5s polite delay between locations). Total time: ~14s (was 83s sequential).

    Verified 2026-08-23: Germany-only gives 60/role (277 unique across 10 roles).
    Multi-city (6 locations) gives 184/role (3.1x more coverage).

    Tradeoff vs paid Apify actor:
    - No job descriptions (search page only). Description-based seniority filter
      (EXCLUDED_EXP_PATTERNS) can't match — MORE permissive, not less. Title-based
      EXCLUDED_TITLE_PATTERNS still catches Senior/Lead/Manager in titles.
    - No pagination, but multi-city strategy recovers more jobs than the paid actor's
      count=500 cap in practice (184-211 unique/role vs 500 total across all roles).
    """
    from bs4 import BeautifulSoup
    from urllib.parse import urlencode

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Multi-city search: Germany-wide + major tech hubs.
    LINKEDIN_LOCATIONS = ["Germany", "Berlin", "Munich", "Hamburg", "Frankfurt", "Cologne"]

    def _scrape_role(role: str) -> list[dict]:
        """Scrape one role across all locations. Returns jobs for this role only."""
        role_jobs = []
        for loc in LINKEDIN_LOCATIONS:
            params = {
                "keywords": role,
                "location": loc,
                "f_TPR": "r86400",   # last 24h (server-side filter)
                "sortBy": "DD",       # date descending
            }
            url = f"https://www.linkedin.com/jobs/search/?{urlencode(params)}"
            try:
                resp = requests.get(url, headers=HEADERS, timeout=15)
                if resp.status_code == 429:
                    # Rate limited — back off and retry once
                    time.sleep(3)
                    resp = requests.get(url, headers=HEADERS, timeout=15)
                resp.raise_for_status()
            except Exception as exc:
                print(f"[!] LinkedIn free scrape failed for '{role}' / '{loc}': {exc}")
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.select("div.base-card"):
                title_el = card.select_one("h3.base-search-card__title")
                company_el = card.select_one("h4.base-search-card__subtitle")
                location_el = card.select_one("span.job-search-card__location")
                link_el = card.select_one("a.base-card__full-link")
                date_el = card.select_one("time")

                if not title_el or not link_el:
                    continue

                title = title_el.get_text(strip=True)
                job_url = link_el.get("href", "").split("?")[0]
                company = company_el.get_text(strip=True) if company_el else "Unknown"
                card_location = location_el.get_text(strip=True) if location_el else loc
                posted_raw = date_el.get("datetime", "") if date_el else ""

                desc = ""
                is_valid, role_type = check_experience_and_location(title, desc, card_location)
                if not is_valid:
                    continue

                # Safety-net freshness post-filter
                if posted_raw:
                    posted_dt = None
                    try:
                        posted_dt = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
                        if posted_dt.tzinfo is None:
                            posted_dt = posted_dt.replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        try:
                            posted_dt = datetime.strptime(posted_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        except (ValueError, TypeError):
                            pass
                    if posted_dt:
                        now = datetime.now(timezone.utc)
                        cutoff = now - timedelta(hours=FRESHNESS_HOURS)
                        if posted_dt.hour == 0 and posted_dt.minute == 0:
                            posted_dt = posted_dt.replace(hour=23, minute=59, second=59)
                        if posted_dt < cutoff:
                            continue

                role_jobs.append({
                    "language": detect_language(title),
                    "job_board": "LinkedIn",
                    "role_type": role_type,
                    "title": title,
                    "company": company,
                    "location": card_location,
                    "posted_at": posted_raw or "Last 24h",
                    "exp_required": "<= 2 Years",
                    "match_score": f"{compute_match_score(title)}%",
                    "job_url": job_url,
                    "description": "",
                })

            time.sleep(0.5)  # polite delay between locations

        print(f"    LinkedIn free '{role}': {len(role_jobs)} jobs (6 locations)", flush=True)
        return role_jobs

    # Run roles in parallel with limited concurrency to avoid LinkedIn 429 rate
    # limiting. 10 concurrent roles × 6 locations = 60 simultaneous requests
    # triggers rate limiting. 5 workers keeps peak concurrency manageable
    # (~30 requests over ~14s = ~2 req/s) while still 6x faster than sequential.
    all_role_jobs = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_scrape_role, role): role for role in SEARCH_ROLES}
        for future in as_completed(futures):
            try:
                all_role_jobs.extend(future.result())
            except Exception as exc:
                print(f"[!] LinkedIn role error: {exc}", flush=True)

    # Cross-role URL dedup (each role scraped independently, URLs may overlap)
    seen_urls = set()
    jobs = []
    for job in all_role_jobs:
        url = job["job_url"]
        if url not in seen_urls:
            seen_urls.add(url)
            jobs.append(job)

    return jobs

def fetch_linkedin_jobs():
    """Fetch LinkedIn jobs via curious_coder/linkedin-jobs-scraper using search URLs (f_TPR=r86400 = last 24h).
    f_TPR=r86400 is LinkedIn's native server-side 24h filter (verified: 727/727 jobs within 24h).
    Post-filters on postedAt as defense-in-depth safety net."""
    jobs = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)
    urls = [
        f"https://www.linkedin.com/jobs/search?keywords={urllib.parse.quote(role)}&location=Germany&f_TPR=r86400"
        for role in SEARCH_ROLES
    ]
    # 'count' is a TOTAL cap across all URLs (NOT per-URL — verified via actor docs + live runs).
    # count=500 gives ~500 raw jobs; after is_relevant_title + seniority filters, ~200 survive.
    # Cost: $1.00/1K results ⇒ ~$0.50/run. maxTotalChargeUsd=0.60 is a safety cap for aborted runs.
    items = run_apify_actor(ACTOR_LINKEDIN, {"urls": urls, "count": 500}, label="LinkedIn Scraper", max_charge_usd=0.60)
    for item in items:
        title = item.get("title", "")
        desc = item.get("descriptionText") or item.get("descriptionHtml") or ""
        loc = item.get("location", "Germany")

        is_valid, role_type_or_reason = check_experience_and_location(title, desc, loc)
        if not is_valid:
            continue

        # Safety-net post-filter: reject stale jobs even if f_TPR=r86400 lets one through.
        # LinkedIn postedAt is date-only (YYYY-MM-DD) — treat as end-of-day (23:59:59) to
        # avoid false rejections of jobs posted late on the previous day.
        posted_raw = item.get("postedAt", "")
        posted_dt = None
        if posted_raw:
            try:
                posted_dt = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
                if posted_dt.tzinfo is None:
                    posted_dt = posted_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                try:
                    posted_dt = datetime.strptime(posted_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pass
            # Date-only timestamps: shift to end-of-day for a generous freshness check
            if posted_dt and ":" not in posted_raw and posted_dt.hour == 0:
                posted_dt = posted_dt.replace(hour=23, minute=59, second=59)
        if posted_dt and posted_dt < cutoff:
            continue  # stale job — skip

        jobs.append({
            "language": detect_language(f"{title} {desc}"),
            "job_board": "LinkedIn",
            "role_type": role_type_or_reason,
            "title": title,
            "company": item.get("companyName", "Unknown"),
            "location": loc,
            "posted_at": posted_raw or "Last 24h",
            "exp_required": "<= 2 Years",
            "match_score": f"{compute_match_score(f'{title} {desc}')}%",
            "job_url": item.get("link") or item.get("applyUrl") or "",
            "description": desc[:500]
        })
    return jobs

def fetch_indeed_jobs():
    """Fetch Indeed jobs via valig/indeed-jobs-scraper (Germany, last 24h).
    datePosted='1' is unreliable — Indeed's API ignores it (~11% of listings are stale,
    some 500+ days old). Post-filters on datePublished to enforce 24h freshness."""
    jobs = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)

    def fetch_role(role):
        role_jobs = []
        items = run_apify_actor(ACTOR_INDEED, {
            "country": "de",
            "title": role,
            "limit": 50,
            "location": "Germany",
            "datePosted": "1"
        }, label=f"Indeed ({role})")
        for item in items:
            title = item.get("title", "")
            emp = item.get("employer") or {}
            desc_obj = item.get("description") or {}
            desc = desc_obj.get("text") if isinstance(desc_obj, dict) else str(desc_obj)
            loc_obj = item.get("location") or {}
            loc = ", ".join(filter(None, [loc_obj.get("city"), loc_obj.get("countryName")])) or "Germany"

            is_valid, role_type_or_reason = check_experience_and_location(title, desc, loc)
            if not is_valid:
                continue

            # Post-filter: reject stale jobs (datePosted='1' is unreliable)
            posted_raw = item.get("datePublished", "")
            posted_dt = None
            if posted_raw:
                try:
                    posted_dt = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
                    if posted_dt.tzinfo is None:
                        posted_dt = posted_dt.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    try:
                        posted_dt = datetime.strptime(posted_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        pass
            if posted_dt and posted_dt < cutoff:
                continue  # stale job — skip

            posted_at = posted_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z") if posted_dt else "Last 24h"
            role_jobs.append({
                "language": detect_language(f"{title} {desc}"),
                "job_board": "Indeed",
                "role_type": role_type_or_reason,
                "title": title,
                "company": emp.get("name", "Unknown"),
                "location": loc,
                "posted_at": posted_at,
                "exp_required": "<= 2 Years",
                "match_score": f"{compute_match_score(f'{title} {desc}')}%",
                "job_url": item.get("url") or item.get("jobUrl") or "",
                "description": desc[:500]
            })
        return role_jobs

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_role, role) for role in SEARCH_ROLES]
        for future in as_completed(futures):
            try:
                jobs.extend(future.result())
            except Exception as e:
                print(f"[!] Indeed role fetch error: {e}", flush=True)
    return jobs

def convert_csv_to_xlsx(csv_path: Path, xlsx_path: Path) -> None:
    """Convert the exported CSV to XLSX: frozen header, autofilter (easy sorting),
    numeric match_score, and clickable job_url hyperlinks. Requires openpyxl."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("[!] openpyxl not installed - skipping XLSX export")
        return

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        print("[!] Empty CSV - skipping XLSX export")
        return
    header, data = rows[0], rows[1:]

    wb = Workbook()
    ws = wb.active
    ws.title = "Job Search"

    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF")
    link_font = Font(color="0563C1", underline="single")
    center = Alignment(horizontal="center", vertical="center")

    ws.append(header)
    for c in ws[1]:
        c.fill = header_fill
        c.font = header_font
        c.alignment = center

    url_col = header.index("job_url") + 1 if "job_url" in header else None
    score_col = header.index("match_score") + 1 if "match_score" in header else None

    for row in data:
        ws.append(row)
        r = ws.max_row
        if url_col:
            cell = ws.cell(row=r, column=url_col)
            url = cell.value or ""
            if url:
                cell.hyperlink = url
                cell.font = link_font
        if score_col:
            cell = ws.cell(row=r, column=score_col)
            v = cell.value
            if isinstance(v, str) and v.endswith("%") and v[:-1].strip().isdigit():
                cell.value = int(v[:-1].strip())
                cell.number_format = '0"%"'
                cell.alignment = center

    for idx in range(1, len(header) + 1):
        letter = get_column_letter(idx)
        widths = [len(str(ws.cell(row=r, column=idx).value or "")) for r in range(1, ws.max_row + 1)]
        ws.column_dimensions[letter].width = min(max(widths) + 2, 80)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(header))}{ws.max_row}"
    ws.row_dimensions[1].height = 22

    wb.save(xlsx_path)
    print(f"[✓] XLSX exported to: {xlsx_path}")

def main():
    print("=== Apify Job Fetcher & Dated CSV Exporter ===")
    print(f"    LinkedIn: FREE HTML scraping (no Apify, $0, 10 roles parallel)")
    print(f"    Indeed: title=<role>, limit=50, parallel (10 roles)")
    print(f"    Xing/Stepstone/Glassdoor/Startup.jobs: free HTML scraping (parallel)")
    print(f"    ATS Direct: Greenhouse/SmartRecruiters/Ashby (free public APIs)")
    print(f"    All 9 platforms run in parallel via ThreadPoolExecutor")

    from ats_scraper import fetch_all_ats

    # Each platform fetcher runs in its own thread.
    # I/O bound work (HTTP requests + parsing) — GIL released during I/O,
    # so threads give near-linear speedup. Each fetcher is independent:
    # no shared mutable state, results collected after all complete.
    PLATFORM_FETCHERS = [
        ("Arbeitnow",    fetch_arbeitnow_jobs),
        ("Startup.jobs", fetch_startupjobs_jobs),
        ("Xing",         fetch_xing_jobs),
        ("Stepstone",    fetch_stepstone_jobs),
        ("Glassdoor",    fetch_glassdoor_jobs),
        ("LinkedIn",     fetch_linkedin_jobs_free),
        ("Indeed",       fetch_indeed_jobs),
        ("ATS Direct",   fetch_all_ats),
    ]

    all_jobs = []
    platform_counts = {}
    errors = {}

    print(f"\n  Launching {len(PLATFORM_FETCHERS)} platform fetchers in parallel...")
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=len(PLATFORM_FETCHERS)) as executor:
        future_to_name = {
            executor.submit(fetcher): name
            for name, fetcher in PLATFORM_FETCHERS
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                jobs = future.result()
                all_jobs.extend(jobs)
                platform_counts[name] = len(jobs)
                print(f"  [✓] {name}: {len(jobs)} jobs ({time.time() - start_time:.1f}s)", flush=True)
            except Exception as exc:
                platform_counts[name] = 0
                errors[name] = str(exc)
                print(f"  [!] {name}: ERROR — {exc} ({time.time() - start_time:.1f}s)", flush=True)

    elapsed = time.time() - start_time
    print(f"\n  All platforms complete in {elapsed:.1f}s")

    print("\n  Deduplication...")


    # Deduplication (within this run)
    seen_keys = set()
    deduped_jobs = []
    duplicates_count = 0

    for job in all_jobs:
        key = normalize_key(job["company"], job["title"])
        if key in seen_keys:
            duplicates_count += 1
            continue
        seen_keys.add(key)
        deduped_jobs.append(job)
    # --- Cross-platform fuzzy dedup ---
    deduped_jobs, cross_platform_count = cross_platform_dedup(deduped_jobs)
    print(f"Cross-platform dedup: removed {cross_platform_count} cross-platform duplicate(s)")

    # Cross-run deduplication: drop jobs already exported by previous runs
    # (24h freshness windows of consecutive runs overlap when run times drift).
    now = datetime.now()
    prev_url_keys, prev_title_keys, runs_scanned, jobs_scanned = load_previous_run_keys(now)
    cross_run_duplicates = 0
    if prev_url_keys or prev_title_keys:
        final_jobs = []
        for job in deduped_jobs:
            if normalize_job_url(job["job_url"]) in prev_url_keys or \
               normalize_key(job["company"], job["title"]) in prev_title_keys:
                cross_run_duplicates += 1
                continue
            final_jobs.append(job)
        deduped_jobs = final_jobs

    print(f"Per-platform: {platform_counts}")
    print(f"Cross-run dedup: compared against previous run ({jobs_scanned} jobs), removed {cross_run_duplicates} already-seen job(s)")
    print(f"Est. Apify cost: ~${0.04:.3f} (Indeed only) + LinkedIn/Arbeitnow/Startup.jobs/Xing/Stepstone/Glassdoor/ATS Direct ALL FREE")

    # Format Date String: e.g. Aug_4_2026
    date_str = now.strftime("%b_%d_%Y").replace("_0", "_")

    # Per-date subfolder so each run's files are grouped/sorted by day
    date_folder = JOB_SEARCH_DIR / now.strftime("%Y-%m-%d")
    date_folder.mkdir(parents=True, exist_ok=True)

    csv_filename = f"Job_Search_{date_str}.csv"
    csv_path = date_folder / csv_filename
    json_path = date_folder / f"Job_Search_{date_str}.json"
    report_path = date_folder / "JOB_OPENINGS_LAST_24H.md"

    # 1. Write Dated Sortable CSV File (UTF-8 BOM for Excel compatibility)
    fieldnames = ["language", "job_board", "role_type", "title", "company", "location", "posted_at", "exp_required", "match_score", "job_url"]

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for j in deduped_jobs:
            row = {k: j[k] for k in fieldnames}
            writer.writerow(row)

    # 2. Write JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(deduped_jobs, f, indent=2, ensure_ascii=False)

    # 3. Write Markdown Report
    report_md = f"""# Daily Job Openings Report (Last 24 Hours)

**Generated:** {now.strftime("%Y-%m-%d %H:%M")}  
**CSV File:** `{csv_path}` (Sortable in Excel / Calc)

## Constraints Summary
- **Freshness:** Last 24 Hours
- **Experience:** <= 2 Years (No Senior/Lead/Manager roles)
- **Role Types:** Full-Time & Internships (Germany-wide) | Working Student (**Hamburg & Kiel ONLY**)
- **Target Roles:** Data Engineer, Analytics Engineer, Data Analyst, AI Data Engineer

| # | Language | Job Board | Role Type | Company | Job Title | Location | Match | Direct Link |
|---|---|---|---|---|---|---|---|---|
"""
    for idx, j in enumerate(deduped_jobs, 1):
        report_md += f"| {idx} | {j['language']} | **{j['job_board']}** | {j['role_type']} | {j['company']} | {j['title']} | {j['location']} | {j['match_score']} | [Apply Link]({j['job_url']}) |\n"

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"[✓] CSV exported successfully to: {csv_path}")
    print(f"[✓] JSON exported to: {json_path}")
    print(f"[✓] Markdown report exported to: {report_path}")

    # 4. Write Sortable XLSX (autofilter + clickable links)
    xlsx_path = date_folder / f"Job_Search_{date_str}.xlsx"
    convert_csv_to_xlsx(csv_path, xlsx_path)

if __name__ == "__main__":
    main()
