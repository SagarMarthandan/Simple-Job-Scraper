---
name: Jobscraper
description: >-
  Use when the user wants to run the automated job search pipeline. Fetches fresh job postings (< 24 hours old) from LinkedIn, Indeed, Arbeitnow, Xing, Stepstone, and ATS Direct for data/AI/analytics roles in Germany, filters by experience (<= 2 years), location (working student: Hamburg & Kiel only), and title relevance (must contain data/analytics/AI/SQL/Python keywords), deduplicates against yesterday's run, and exports to CSV/XLSX/JSON/MD. Trigger on keywords like "job search", "job scraper", "find jobs", "scrape jobs", "job postings", "fresh jobs", "data jobs germany", "linkedin jobs", "indeed jobs", "arbeitnow", "xing jobs", "stepstone jobs", "apify jobs", "job pipeline", "run job search".
dependencies: python>=3.10, requests, openpyxl, beautifulsoup4, tinyfish-cli
---

# Jobscraper Pipeline

> **Instructions for Oh My Pi (OMP) Session:**
> When this skill is invoked, execute the automated job search pipeline below.
> It fetches fresh job postings (< 24 hours old) from **LinkedIn**, **Indeed**, **Arbeitnow**, **Xing**, **Stepstone**, and **ATS Direct** (Greenhouse/SmartRecruiters/Ashby) across all target role profiles, applies location-aware working student rules, deduplicates against yesterday's run, and outputs a sortable **CSV + XLSX** with the current execution date/time stamp.

## Execution

### Step 1: Scrape (bash)

```bash
cd /home/sagar/Skills/Jobscraper && python3 apify_job_search.py
```

### Step 2: Verify (eval — requires OMP runtime for LLM)

The verify step MUST run through `eval` (OMP Python kernel), not `bash`.
It needs `completion` (OMP-injected) for LLM classification. TinyFish JD
fetching uses the `tinyfish` CLI via subprocess. Running via bash silently
skips LLM classification, producing inaccurate German/experience detection.

```python
# eval cell (language: py)
import json, subprocess
from pathlib import Path
from datetime import datetime

def tinyfish_fetch(urls):
    result = subprocess.run(
        ["tinyfish", "fetch", "content", "get", "--format", "markdown"] + urls,
        capture_output=True, text=True, timeout=120
    )
    return json.loads(result.stdout)

_completion_orig = completion
def completion(prompt, model="smol"):
    return _completion_orig(prompt=prompt, model=model).wait()

with open('/home/sagar/Skills/Jobscraper/verify_jobs.py') as f:
    exec(compile(f.read(), 'verify_jobs.py', 'exec'), globals())

today = datetime.now().strftime("%Y-%m-%d")
date_str = datetime.now().strftime("%b_%d_%Y").replace("_0", "_")
csv_path = Path(f'/home/sagar/Skills/Jobscraper/Job Search/{today}/Job_Search_{date_str}.csv')
run_verification(csv_path, force=True)
```

TinyFish descriptions are cached to `tinyfish_cache.json` in the run
directory — interrupts and re-runs load the cache and skip already-fetched
URLs (no wallet re-spend).

Dependencies: `requests`, `openpyxl`, `beautifulsoup4` (for ATS scraper), `tinyfish` CLI (for JD fetching).
Install: `pip install requests openpyxl beautifulsoup4` and `npm install -g @tiny-fish/cli`

Indeed JD fallback: `playwright`, `playwright-extra`, `puppeteer-extra-plugin-stealth` (Node.js).
Install: `cd /home/sagar/Skills/Jobscraper && npm install playwright playwright-extra puppeteer-extra-plugin-stealth && npx playwright install chromium`

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
   - **LinkedIn** (free HTML scraping, no Apify, $0)
   - **Indeed** (Apify — valig/indeed-jobs-scraper, ~$0.04/run)
   - **Arbeitnow** (free API, no Apify)
   - **Xing** (free HTML scraping via plain `requests`, no Apify)
   - **Stepstone** (free HTML scraping via plain `requests`, no Apify)
   - **ATS Direct** (Greenhouse/SmartRecruiters/Ashby public APIs, free)
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
7. **Cross-Run Deduplication:** After within-run dedup, jobs already in yesterday's CSV are removed by URL match. Prevents duplicates across consecutive daily sheets when 24h freshness windows overlap.

## Output

Files written to `/home/sagar/Skills/Jobscraper/Job Search/YYYY-MM-DD/`:
- `Job_Search_<Month>_<Day>_<Year>.csv` — UTF-8 BOM, sortable
- `Job_Search_<Month>_<Day>_<Year>.json`
- `Job_Search_<Month>_<Day>_<Year>.xlsx` — autofilter + clickable links
- `JOB_OPENINGS_LAST_24H.md` — markdown summary

### Step 2 Output (verify)

- `Job_Search_<Month>_<Day>_<Year>_verified.xlsx` — 2 sheets: "Job Search" (live, apply-ready, enriched with German requirement, experience years, salary, remote/hybrid) and "Reposted" (LinkedIn reposts for manual review)
- `tinyfish_cache.json` — cached JD descriptions (survives interrupts, re-runs skip already-fetched URLs)

## Cost

~$0.04/run Apify (Indeed only). LinkedIn, Arbeitnow, Xing, Stepstone, and ATS Direct are free. Verify step: TinyFish fetch is free; LLM classification uses `completion(model="smol")` (minimal cost). Cache prevents re-fetching on re-runs.
