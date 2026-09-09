#!/usr/bin/env python3
"""
staffing_filter.py — Staffing/Recruitment Agency Blocklist
==========================================================

Single source of truth for the staffing agency blocklist used by
verify_jobs.py (step 2: segregates staffing companies into a separate
"Staffing Companies" sheet instead of dropping them).

Intermediaries that post jobs on behalf of other employers (Zeitarbeit,
Personalvermittlung, job platforms) are matched against the raw company
name (case-insensitive, word-boundary).
"""

import re

# Staffing/Recruitment Agency Blocklist — intermediaries that post jobs on behalf
# of other employers (Zeitarbeit, Personalvermittlung, job platforms). Matched
# against the raw company name (case-insensitive, word-boundary).
STAFFING_COMPANIES = re.compile(
    r"\b("
    r"instaffo|hays|michael page|randstad|adecco|manpower|"
    r"kelly services|gi group|brunel|dekra arbeit|hapeko|hesys|"
    r"prostaff|staffline|timepartner|lhi leasing|"
    r"gut personalmanagement|office people|silbury|"
    r"robert walters|russell tobin|experis|"
    r"talent partner|the green recruitment|quik hire|"
    r"zero to one|hicalibre|whybrilliant|"
    r"emagine|g2i|strategie:p|"
    r"jobgether|jobster|hire feed|findr|hoshii|"
    r"mamgo|sundayy|twomynds|yes4match|studyflix|"
    r"trenkwalder|modis|"
    r"personalberatung|personaldienst|zeitarbeit|"
    r"personal leasing|personalmanagement|"
    r"recruitment agency|recruitment company|"
    # ── Added 2026-09-09: staffing agencies missed by original blocklist ──
    r"alten|akkodis|alldus|amadeus fire|dis ag|"
    r"grafton|net2source|next ventures|robert half|"
    r"sthree|allgeier it|expertum|umatr|juucy|"
    r"pens.?expert|digital waffle|"
    # ── Consulting / IT services / Big 4 ──
    r"reply|deloitte|ernst.{0,10}young|ey|kpmg|bcg|"
    r"cgi|adesso|gft|synpulse|serco|avanade"
    r")\b",
    re.IGNORECASE
)


def is_staffing_company(company: str) -> bool:
    """Check if the company is a staffing/recruitment agency or job platform
    that posts jobs on behalf of other employers (not a direct employer)."""
    return bool(STAFFING_COMPANIES.search(company or ""))
