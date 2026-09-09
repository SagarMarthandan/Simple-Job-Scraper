#!/usr/bin/env python3
"""
applied_check.py — Already-Applied Job Detection
================================================

Reads the applications tracker CSV to build a set of normalized job keys
for positions already applied to. Used by verify_jobs.py to segregate
already-applied jobs into a separate "Already Applied" sheet.

Source:
  /home/sagar/Documents/applications_tracker.csv
  Columns: Date, Company, Position, ... (Company + Position used for matching)
"""

import csv
import sys
from pathlib import Path

APPLICATIONS_TRACKER_CSV = Path("/home/sagar/Documents/applications_tracker.csv")


def load_applied_job_keys() -> set[str]:
    """Build a set of normalize_key(company, title) for every job already
    applied to, reading from the applications tracker CSV.

    Returns a set of normalized keys; empty set if the CSV doesn't exist.
    """
    skill_dir = Path("/home/sagar/Skills/Jobscraper")
    if str(skill_dir) not in sys.path:
        sys.path.insert(0, str(skill_dir))
    from apify_job_search import normalize_key

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
