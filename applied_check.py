#!/usr/bin/env python3
"""
applied_check.py — Already-Applied Job Detection
================================================

Reads the applications tracker CSV to build detection data for positions
already applied to. Used by verify_jobs.py to segregate already-applied
jobs into a separate "Already Applied" sheet.

Three-tier matching:
  1. URL match (exact — catches cross-platform same job)
  2. Company + Title key match (normalized key — existing logic)
  3. Company match + title similarity (Jaccard >= 0.6 — catches same
     position with different formatting across platforms)

Source:
  /home/sagar/Documents/applications_tracker.csv
  Columns: Date, Company, Position, Source URL, ...
  (Company + Position + Source URL used for matching)
"""

import csv
import sys
from pathlib import Path

APPLICATIONS_TRACKER_CSV = Path("/home/sagar/Documents/applications_tracker.csv")

# Jaccard threshold for title similarity when company matches.
# 0.6 catches same-position-different-formatting while avoiding
# most different-position-same-company false positives.
TITLE_SIMILARITY_THRESHOLD = 0.6


# ── Module-level import cache (avoids repeated sys.path manipulation) ────────
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


def load_applied_job_keys() -> set[str]:
    """Build a set of normalize_key(company, title) for every job already
    applied to, reading from the applications tracker CSV.

    Returns a set of normalized keys; empty set if the CSV doesn't exist.
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
    """
    _, _norm_company, _, normalize_job_url = _get_apify()

    keys: set[str] = set()
    companies: dict[str, list[str]] = {}
    urls: set[str] = set()
    row_count = 0

    if not APPLICATIONS_TRACKER_CSV.exists():
        print(f"[!] Already-applied detection: CSV not found at {APPLICATIONS_TRACKER_CSV}")
        return {"keys": keys, "companies": companies, "urls": urls}

    with open(APPLICATIONS_TRACKER_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            company = (row.get("Company") or "").strip()
            title = (row.get("Position") or "").strip()
            url = (row.get("Source URL") or "").strip()
            if not company or not title:
                continue
            nc = _norm_company(company)
            _, _norm_title = _get_apify()[1], _get_apify()[2]
            nt = _norm_title(title)
            keys.add(f"{nc}::{nt}")
            companies.setdefault(nc, []).append(title)
            if url:
                urls.add(normalize_job_url(url))
            row_count += 1

    print(
        f"[*] Already-applied detection: {row_count} CSV rows → "
        f"{len(keys)} keys, {len(companies)} companies, {len(urls)} URLs"
    )
    return {"keys": keys, "companies": companies, "urls": urls}


def _title_similarity(title1: str, title2: str) -> float:
    """Compute Jaccard similarity between two raw titles (normalized internally)."""
    _, _, _norm_title, _ = _get_apify()
    tokens1 = set(_norm_title(title1).split())
    tokens2 = set(_norm_title(title2).split())
    if not tokens1 or not tokens2:
        return 0.0
    return len(tokens1 & tokens2) / len(tokens1 | tokens2)


def is_already_applied(
    company: str, title: str, url: str, applied_data: dict
) -> bool:
    """Check if a job has already been applied to using three-tier matching.

    1. URL match (exact — catches cross-platform same job listing)
    2. Company + Title key match (normalized key — existing logic)
    3. Company match + title similarity (Jaccard >= 0.6 — catches same
       position with different formatting across platforms)

    Skips company matching for "Unknown" / empty companies to avoid
    false positives (Xing often has "Unknown" as company name).
    """
    _, _norm_company, _norm_title, normalize_job_url = _get_apify()

    nc = _norm_company(company)
    nt = _norm_title(title)
    nurl = normalize_job_url(url)

    # 1. URL match (strongest — exact, no ambiguity)
    if nurl and nurl in applied_data["urls"]:
        return True

    # 2. Key match (existing normalized company::title)
    key = f"{nc}::{nt}"
    if key in applied_data["keys"]:
        return True

    # 3. Company match + title similarity
    # Skip "Unknown" and empty companies to avoid false positives
    if nc and nc != "unknown" and nc in applied_data["companies"]:
        for tracker_title in applied_data["companies"][nc]:
            if _title_similarity(title, tracker_title) >= TITLE_SIMILARITY_THRESHOLD:
                return True

    return False
