#!/usr/bin/env python3
"""
applied_check.py — Already-Applied Job Detection (LLM-based)
=============================================================

Reads the applications tracker CSV and today's scraped jobs CSV,
pre-filters candidate pairs by company token overlap, and uses
LLM classification (smol model via JS eval) to detect jobs already
applied to. Used by verify_jobs.py to segregate already-applied
jobs into a separate "Already Applied" sheet.

Pipeline:
  1. prepare_match_input(csv_path) — pre-filters candidate pairs,
     saves to /tmp/already_applied_input.json
  2. JS eval — LLM classifies pairs in batches of 10, saves matched
     scraped jobs to /tmp/already_applied_matches.json
  3. load_llm_matches() — loads matched URLs+keys into a dict
  4. is_already_applied() — checks URL/key against LLM matches
     (falls back to deterministic URL+key match if LLM unavailable)

Source:
  /home/sagar/Documents/applications_tracker.csv
  Columns: Date, Company, Position, Source URL, ...
  (Company + Position + Source URL used for matching)
"""

import csv
import json
import re
import sys
from pathlib import Path

APPLICATIONS_TRACKER_CSV = Path("/home/sagar/Documents/applications_tracker.csv")
LLM_INPUT_PATH = "/tmp/already_applied_input.json"
LLM_MATCHES_PATH = "/tmp/already_applied_matches.json"

# Stopwords for company token pre-filtering (legal suffixes + generic words).
# These are stripped before token matching to avoid false candidate pairs
# (e.g., "Data GmbH" and "Tech GmbH" should not match on "gmbh").
_COMPANY_STOPWORDS = {
    "gmbh", "ag", "inc", "ltd", "co", "kg", "se", "corp", "group",
    "gruppe", "holding", "international", "deutschland", "germany",
    "global", "the", "und", "der", "die", "das", "gbr", "ug",
    "dienstleistungen", "services", "solutions", "technology",
    "technologies", "tech", "systems", "software", "consulting",
    "partners", "energy", "kgaa", "coming", "soon",
}


# ── Module-level import cache ────────────────────────────────────────────────
_apify = None


def _get_apify():
    """Lazy-import normalization helpers from apify_job_search."""
    global _apify
    if _apify is None:
        skill_dir = Path("/home/sagar/Skills/Jobscraper")
        if str(skill_dir) not in sys.path:
            sys.path.insert(0, str(skill_dir))
        from apify_job_search import (
            normalize_key,
            _norm_company,
            _norm_title,
            normalize_job_url,
        )
        _apify = (normalize_key, _norm_company, _norm_title, normalize_job_url)
    return _apify


def _company_tokens(name: str) -> set[str]:
    """Extract meaningful tokens from a company name for pre-filtering.

    Lowercases, strips punctuation, removes legal suffixes and generic
    stopwords, and returns tokens of length >= 3.
    """
    name = name.lower()
    name = re.sub(r"[^\w\s]", " ", name)
    return {t for t in name.split() if len(t) >= 3 and t not in _COMPANY_STOPWORDS}


def load_applied_job_keys() -> set[str]:
    """Build a set of normalize_key(company, title) for every job already
    applied to, reading from the applications tracker CSV.

    Returns a set of normalized keys; empty set if the CSV doesn't exist.
    Kept for backward compatibility — verify_jobs.py now uses load_applied_data().
    """
    normalize_key, _, _, _ = _get_apify()

    keys: set[str] = set()
    row_count = 0

    if not APPLICATIONS_TRACKER_CSV.exists():
        print(f"[!] Already-applied detection: CSV not found at {APPLICATIONS_TRACKER_CSV}")
        return keys

    with open(APPLICATIONS_TRACKER_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            company = (row.get("Company") or "").strip()
            title = (row.get("Position") or "").strip()
            if not company or not title:
                continue
            key = normalize_key(company, title)
            if key:
                keys.add(key)
                row_count += 1

    print(f"[*] Already-applied detection: {row_count} CSV rows → {len(keys)} unique keys")
    return keys


def load_applied_data() -> dict:
    """Build already-applied detection data from the tracker CSV.

    Returns dict with:
        keys:      set[str]  — normalized company::title keys
        companies: dict[str, list[str]] — normalized company → raw tracker titles
        urls:      set[str]  — normalized URLs
        entries:   list[dict] — raw {company, title, url} for LLM input prep
    """
    normalize_key, _norm_company, _norm_title, normalize_job_url = _get_apify()

    keys: set[str] = set()
    companies: dict[str, list[str]] = {}
    urls: set[str] = set()
    entries: list[dict] = []
    row_count = 0

    if not APPLICATIONS_TRACKER_CSV.exists():
        print(f"[!] Already-applied detection: CSV not found at {APPLICATIONS_TRACKER_CSV}")
        return {"keys": keys, "companies": companies, "urls": urls, "entries": entries}

    with open(APPLICATIONS_TRACKER_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            company = (row.get("Company") or "").strip()
            title = (row.get("Position") or "").strip()
            url = (row.get("Source URL") or "").strip()
            if not company or not title:
                continue
            nc = _norm_company(company)
            nt = _norm_title(title)
            keys.add(f"{nc}::{nt}")
            companies.setdefault(nc, []).append(title)
            if url:
                urls.add(normalize_job_url(url))
            entries.append({"company": company, "title": title, "url": url})
            row_count += 1

    print(
        f"[*] Already-applied detection: {row_count} CSV rows → "
        f"{len(keys)} keys, {len(companies)} companies, {len(urls)} URLs"
    )
    return {"keys": keys, "companies": companies, "urls": urls, "entries": entries}


def prepare_match_input(csv_path: Path) -> int:
    """Pre-filter candidate pairs between today's scraped jobs and tracker entries.

    Uses company token overlap (>= 1 shared meaningful token) to narrow
    the search space, then saves candidate pairs to LLM_INPUT_PATH for
    JS-side LLM classification.

    Returns the number of candidate pairs generated.
    """
    from collections import defaultdict

    # Load today's scraped jobs
    today_jobs: list[dict] = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            company = (row.get("company") or "").strip()
            title = (row.get("title") or "").strip()
            url = (row.get("job_url") or "").strip()
            if company or url:
                today_jobs.append({"company": company, "title": title, "url": url})

    # Load tracker entries
    applied_data = load_applied_data()
    tracker_entries = applied_data["entries"]

    # Build token index: tracker company tokens → list of tracker indices
    tracker_by_token: dict[str, list[int]] = defaultdict(list)
    for i, te in enumerate(tracker_entries):
        for token in _company_tokens(te["company"]):
            tracker_by_token[token].append(i)

    # For each today job, find candidate tracker entries
    candidate_pairs: list[dict] = []
    pair_id = 1
    for tj_idx, tj in enumerate(today_jobs):
        candidate_tracker_idxs: set[int] = set()

        # Token overlap
        for token in _company_tokens(tj["company"]):
            for ti in tracker_by_token.get(token, []):
                candidate_tracker_idxs.add(ti)

        # Also check URL match (exact)
        if tj["url"]:
            _, _, _, normalize_job_url = _get_apify()
            nu = normalize_job_url(tj["url"])
            for ti, te in enumerate(tracker_entries):
                if te["url"] and normalize_job_url(te["url"]) == nu:
                    candidate_tracker_idxs.add(ti)

        for ti in candidate_tracker_idxs:
            te = tracker_entries[ti]
            candidate_pairs.append({
                "id": pair_id,
                "scraped_company": tj["company"],
                "scraped_title": tj["title"],
                "scraped_url": tj["url"],
                "tracker_company": te["company"],
                "tracker_title": te["title"],
                "tracker_url": te["url"],
            })
            pair_id += 1

    # Save flat array for JS-side LLM classification
    with open(LLM_INPUT_PATH, "w") as f:
        json.dump(candidate_pairs, f)

    print(
        f"[*] Already-applied LLM input: {len(today_jobs)} scraped jobs × "
        f"{len(tracker_entries)} tracker entries → {len(candidate_pairs)} candidate pairs"
    )
    return len(candidate_pairs)


def load_llm_matches() -> dict | None:
    """Load LLM-classified already-applied matches.

    Returns dict with "urls" (set of normalized scraped URLs) and
    "keys" (set of normalized company::title keys), or None if the
    matches file doesn't exist (fallback to deterministic matching).
    """
    path = Path(LLM_MATCHES_PATH)
    if not path.exists():
        return None

    normalize_key, _, _, normalize_job_url = _get_apify()

    with open(path) as f:
        data = json.load(f)

    urls: set[str] = set()
    keys: set[str] = set()
    for m in data:
        nu = normalize_job_url(m.get("url", ""))
        if nu:
            urls.add(nu)
        key = normalize_key(m.get("company", ""), m.get("title", ""))
        if key:
            keys.add(key)

    print(f"[*] Already-applied LLM matches: {len(urls)} URLs, {len(keys)} keys")
    return {"urls": urls, "keys": keys}


def is_already_applied(
    company: str, title: str, url: str, applied_data: dict,
    llm_matches: dict | None = None,
) -> bool:
    """Check if a job has already been applied to.

    Primary: LLM matches (pre-computed by JS-side LLM classification).
    Checks both normalized URL and normalized company::title key against
    the LLM match sets.

    Fallback (llm_matches is None): deterministic URL + key match only.
    Used when LLM classification is unavailable (standalone execution).
    """
    normalize_key, _, _, normalize_job_url = _get_apify()

    nurl = normalize_job_url(url)
    key = normalize_key(company, title)

    if llm_matches is not None:
        if nurl and nurl in llm_matches["urls"]:
            return True
        if key and key in llm_matches["keys"]:
            return True
        return False

    # Fallback: deterministic URL + key match
    if nurl and nurl in applied_data["urls"]:
        return True
    if key and key in applied_data["keys"]:
        return True
    return False
