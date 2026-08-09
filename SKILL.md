---
name: Jobscraper
description: >-
  Use when the user wants to run the automated job search pipeline. Fetches fresh job postings (< 24 hours old) from LinkedIn, Indeed, Arbeitnow, Startup.jobs, Xing, Stepstone, and Glassdoor for data/AI/analytics roles in Germany, filters by experience (<= 2 years), location (working student: Hamburg & Kiel only), and title relevance (must contain data/analytics/AI/SQL/Python keywords), deduplicates, and exports to CSV/XLSX/JSON/MD. Trigger on keywords like "job search", "job scraper", "find jobs", "scrape jobs", "job postings", "fresh jobs", "data jobs germany", "linkedin jobs", "indeed jobs", "startup.jobs", "arbeitnow", "xing jobs", "stepstone jobs", "glassdoor jobs", "apify jobs", "job pipeline", "run job search".
dependencies: python>=3.10, cloudscraper, openpyxl
---

# Jobscraper Pipeline

> **Instructions for Oh My Pi (OMP) Session:**
> When this skill is invoked, execute the automated job search pipeline below.
> It fetches fresh job postings (< 24 hours old) from **LinkedIn**, **Indeed**, **Arbeitnow**, **Startup.jobs**, **Xing**, **Stepstone**, and **Glassdoor** across all target role profiles, applies location-aware working student rules and internship inclusions, deduplicates them, filters out roles requiring > 2 years of experience, and outputs a sortable **CSV + XLSX** (autofilter dropdowns, clickable links) with the current execution date/time stamp.

## Execution

Run the pipeline script:

```bash
cd /home/sagar/Skills/Jobscraper && python3 apify_job_search.py
```

Dependencies: `cloudscraper` (for startup.jobs, Xing, Stepstone, and Glassdoor HTML scraping), `openpyxl` (for XLSX export).
Install: `pip install cloudscraper openpyxl`

## Context

- **Candidate Name:** Sagar Marthandan
- **Base Location:** Kiel, Germany
- **Base Resumes Directory:** `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/base_files`
- **Portfolio Directory:** `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/portfolio`
- **Target Output Directory:** `/home/sagar/Skills/Jobscraper/Job Search`
- **Apify Token:** read from the `APIFY_TOKEN` environment variable or `config.json`
- **Script:** `/home/sagar/Skills/Jobscraper/apify_job_search.py`
- **Full documentation:** `/home/sagar/Skills/Jobscraper/apify_job_search.md`

## Search Criteria

1. **Freshness Window:** Strictly posted within the **last 24 hours**.
2. **Target Platforms:**
   - **LinkedIn** (Apify — curious_coder/linkedin-jobs-scraper, ~$0.015/run)
   - **Indeed** (Apify — valig/indeed-jobs-scraper, ~$0.025/run)
   - **Arbeitnow** (free API, no Apify)
   - **Startup.jobs** (free HTML scraping via cloudscraper, no Apify)
   - **Xing** (free HTML scraping via cloudscraper, no Apify)
   - **Stepstone** (free HTML scraping via cloudscraper, no Apify)
   - **Glassdoor** (free HTML scraping via cloudscraper, JSON-LD parsing, no Apify)
3. **Title Relevance Filter:** Universal post-filter `is_relevant_title()` rejects any job whose title doesn't contain at least one data/analytics/AI/SQL/Python keyword.
4. **Role Types & Location Constraints:**
   - **Full-Time / Part-Time / Entry-Level / Junior (0–2 yrs exp):** Germany-wide.
   - **Internships (*Praktikum* / *Internship*):** Germany-wide.
   - **Working Student (*Werkstudent* / *Working Student*):** Strictly **ONLY Hamburg and Kiel**.
5. **Experience Ceiling:** Strict <= 2 years. Rejects Senior, Lead, Principal, Staff, Manager, Head, Architect, Director titles.
6. **Target Role Profiles (10 core roles):**
   1. Data Engineer, Analytics Engineer
   2. Data Analyst
   3. AI Engineer, Machine Learning Engineer
   4. Business Analyst
   5. SQL Developer
   6. Praktikum Data, Werkstudent Data, Werkstudent Business Intelligence

## Output

Files written to `/home/sagar/Skills/Jobscraper/Job Search/YYYY-MM-DD/`:
- `Job_Search_<Month>_<Day>_<Year>.csv` — UTF-8 BOM, sortable
- `Job_Search_<Month>_<Day>_<Year>.json`
- `Job_Search_<Month>_<Day>_<Year>.xlsx` — autofilter + clickable links
- `JOB_OPENINGS_LAST_24H.md` — markdown summary

## Cost

~$0.55/run Apify (LinkedIn ~$0.50 + Indeed ~$0.05). Arbeitnow, Startup.jobs, Xing, Stepstone, and Glassdoor are free. See `apify_job_search.md` Section 5 for the full cost breakdown.
