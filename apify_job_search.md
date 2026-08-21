# Automated Job Search, Filtering & Dynamic CSV Export (Last 24 Hours)

> **Instructions for Oh My Pi (OMP) Session:**
> When this file is uploaded/run in an OMP session, execute the automated job search pipeline below.
> It fetches fresh job postings (< 24 hours old) from **LinkedIn**, **Indeed**, **Arbeitnow**, **Startup.jobs**, **Xing**, **Stepstone**, and **Glassdoor** across all target role profiles, applies location-aware working student rules and internship inclusions, deduplicates them, filters out roles requiring > 2 years of experience, and outputs a sortable **CSV + XLSX** (autofilter dropdowns, clickable links) with the current execution date/time stamp into `/home/sagar/Skills/Jobscraper/Job Search/`.

> **Cost-optimized 2026-08-05:** ~$0.55/run Apify (LinkedIn ~$0.50 + Indeed ~$0.05). Arbeitnow, Startup.jobs, Xing, Stepstone, and Glassdoor are free. See Section 5 for details.

---

## 1. Context & Setup

- **Candidate Name:** Sagar Marthandan
- **Base Location:** Kiel, Germany
- **Base Resumes Directory:** `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/base_files`
- **Portfolio Directory:** `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/portfolio`
- **Target Output Directory:** `/home/sagar/Skills/Jobscraper/Job Search`
- **Apify Token:** read from the `APIFY_TOKEN` environment variable or `config.json` (never hardcode it in this file; the token was previously pasted here and is now rotated)
- **Dependency:** `cloudscraper` (for startup.jobs and Glassdoor HTML scraping — bypasses Cloudflare/anti-bot), `requests` (for Xing — AWS CloudFront, and Stepstone — Akamai CDN; neither has anti-bot challenge). Install: `pip install cloudscraper requests`
- **Target Role Profiles (Consolidated 10 core roles — search engines cover variants):**
  1. **Core Data & Pipeline Engineering:** `Data Engineer`, `Analytics Engineer`
  2. **Business Intelligence & Analytics:** `Data Analyst`
  3. **AI & Data Science:** `AI Engineer`, `Machine Learning Engineer`
  4. **Business Analytics:** `Business Analyst`
  5. **Database & Systems:** `SQL Developer`
  6. **Internships & Working Student:** `Praktikum Data`, `Werkstudent Data`, `Werkstudent Business Intelligence`

---

## 2. Core Search Criteria & Hard Guardrails

1. **Freshness Window:** Strictly posted within the **last 24 hours** (timestamp within 86,400 seconds).
2. **Target Platforms:**
   - **LinkedIn** (Apify — curious_coder/linkedin-jobs-scraper, ~$0.015/run)
   - **Indeed** (Apify — valig/indeed-jobs-scraper, ~$0.025/run)
   - **Arbeitnow** (free API, no Apify)
   - **Startup.jobs** (free HTML scraping via cloudscraper, no Apify)
   - **Xing** (free HTML scraping via plain `requests`, no Apify — Xing is behind AWS CloudFront, not Cloudflare; parses server-rendered job cards using `data-testid` attributes and `<time dateTime>` ISO timestamps; 3 pages per role, sponsored listings skipped)
   - **Stepstone** (free HTML scraping via plain `requests`, no Apify — uses path-based URLs `/jobs/{slug}/in-deutschland?sort=2&ag=age_1`; parses SSR job cards with `data-at` attributes; 3 pages per role, 24h freshness pre-filter via `ag=age_1`. Stepstone is behind Akamai, not Cloudflare — cloudscraper hangs on Akamai, plain requests works)
   - **Glassdoor** (free HTML scraping via cloudscraper, no Apify — parses JSON-LD `<script type="application/ld+json">` ItemList tags from SSR HTML; company names extracted from URL slugs using `_KE{start},{end}` character offsets; `fromAge=1` pre-filters to last 24h; chrome emulation + 8x retry for Cloudflare bypass; 3 pages per role)
   - ~~Kununu~~ (DROPPED — shahidirfan/kununu-jobs-scraper returns 0 results as of 2026-08-05; all 8 Apify store kununu actors are review scrapers, not job listing scrapers)
3. **Title Relevance Filter:** Universal post-filter `is_relevant_title()` rejects any job whose title doesn't contain at least one data/analytics/AI/SQL/Python keyword. Catches actor false positives (Indeed returning "Nachtwächter" for "Data Engineer" searches, etc.).
4. **Role Types & Location Constraints:**
   - **Full-Time / Part-Time / Entry-Level / Junior (0–2 yrs exp):** Germany-wide (Kiel, Hamburg, Berlin, Munich, Frankfurt, Remote, etc.).
   - **Internships (*Praktikum* / *Internship*):** Included **Germany-wide**.
   - **Working Student (*Werkstudent* / *Working Student*):** Strictly **ONLY Hamburg and Kiel** (including remote roles based in HH/Kiel). Exclude working student postings in other cities (e.g. Munich, Frankfurt, Berlin).
5. **Experience Ceiling (Strict <= 2 Years):**
   - **ALLOWED:** 0–2 years experience, Junior, Entry-Level, Associate, Working Student, Intern, or unspecified low-experience roles.
   - **REJECTED:** Any role requiring **> 2 years of experience** (e.g., 3+, 4+, 5+ years, or titled *Senior*, *Lead*, *Principal*, *Staff*, *Manager*, *Head*, *Architect*, *Director*).
6. **Output Deliverables Directory & Naming:**
   - **Directory:** `/home/sagar/Skills/Jobscraper/Job Search/` (Auto-created if not present)
   - **CSV File:** `Job_Search_<Month>_<Day>_<Year>.csv` (e.g., `Job_Search_Aug_4_2026.csv`) with UTF-8 BOM encoding for Excel/Calc sorting.
   - **JSON File:** `Job_Search_<Month>_<Day>_<Year>.json`
   - **Markdown Summary:** `JOB_OPENINGS_LAST_24H.md`

---

## 3. Automated Execution Pipeline Script

The script lives at `/home/sagar/Skills/Jobscraper/apify_job_search.py`.

Run it with:
```bash
cd "/home/sagar/Skills/Jobscraper" && python3 apify_job_search.py
```

Dependencies: `cloudscraper` (for startup.jobs and Glassdoor), `requests` (for Xing and Stepstone), `openpyxl` (for XLSX export).
Install: `pip install cloudscraper requests openpyxl`

The full source code is in `apify_job_search.py` — this .md file no longer embeds a duplicate copy to avoid drift between the two.

---

## 4. Deliverable Files Output Directory & Naming

When executed, the script automatically creates `/home/sagar/Skills/Jobscraper/Job Search/` if it does not exist, and writes the following deliverables inside it:

1. **`Job_Search_<Month>_<Day>_<Year>.csv`** (e.g. `Job_Search_Aug_4_2026.csv`):
   - Structured, sortable CSV with UTF-8 BOM encoding.
   - Dynamic filename based on execution date.
2. **`Job_Search_<Month>_<Day>_<Year>.json`**
3. **`JOB_OPENINGS_LAST_24H.md`**
4. **`Job_Search_<Month>_<Day>_<Year>.xlsx`** (e.g. `Job_Search_Aug_4_2026.xlsx`):
   - Same data as the CSV, ready for OnlyOffice Calc / Excel: frozen header row, **autofilter dropdowns on every column** (click the arrow to sort), and **clickable `job_url` hyperlinks**.
   - `match_score` is stored as a number so sorting by Match is numeric, not alphabetical.
   - Requires `openpyxl` in the venv; generated automatically at the end of the pipeline run.

### Cross-Run Deduplication

Each run writes to its own dated subfolder (`Job Search/YYYY-MM-DD/`). Consecutive daily runs with 24h freshness windows overlap when run times drift — a job posted at 14:00 on Aug 20 appears in both the Aug 20 run (if run at 16:00) and the Aug 21 run (if run at 10:00, since 20h < 24h). Without cross-run dedup, 31.5% of jobs were duplicates across consecutive sheets.

After within-run dedup, `load_previous_run_keys()` loads keys from the **single most recent previous run** (e.g. Friday vs Thursday, or vs Wednesday if Thursday was skipped) and removes jobs already seen:

| Key | Method | Rationale |
|---|---|---|
| **URL key** | `normalize_job_url()` — strips LinkedIn tracking params (position/pageNum/refId/trackingId); preserves Indeed `jk=` and Glassdoor `jl=` IDs | URLs are unique — if the same URL appeared in the previous run, it's the same job |
| **Title key** | `normalize_key(company, title)` — same fuzzy key used for within-run dedup | Catches LinkedIn re-lists (new URL per run, same job) and cross-platform duplicates (same job on LinkedIn vs Indeed) |

Same-day reruns: today's own earlier CSV is the most recent folder, so a second run on the same day suppresses everything already exported.

---
## 5. Cost Optimization & Actor Status (last updated 2026-08-07)

### Cost Breakdown (per full pipeline run)

| Platform | Source | Cost/run | Notes |
|---|---|---|---|
| LinkedIn | Apify: `curious_coder/linkedin-jobs-scraper` | ~$0.50 | Uses `count=500` (total cap across all 10 role URLs). `maxTotalChargeUsd=$0.60` safety cap. $0.001/result. |
| Indeed | Apify: `valig/indeed-jobs-scraper` | ~$0.05 | `limit=50` per role, 10 roles parallel. $0.0001/result. Actor sometimes returns 0 results for Germany — actor-side issue. |
| Arbeitnow | Free REST API | $0.00 | `https://www.arbeitnow.com/api/job-board-api` |
| Startup.jobs | Free HTML scraping | $0.00 | `cloudscraper` bypasses Cloudflare. 6 category pages: data-engineer, data-analyst, ai-engineer, data-scientist, business-analyst, analytics-engineer. |
| Xing | Free HTML scraping | $0.00 | `cloudscraper` bypasses anti-bot. 10 search roles × 3 pages. Parses `data-testid` attributes + `<time dateTime>` ISO timestamps. Sponsored listings (no dateTime) skipped. ~8% of raw results are <24h fresh. |
| Stepstone | Free HTML scraping | $0.00 | `cloudscraper` bypasses anti-bot. 10 search roles × 3 pages. Path-based URLs `/jobs/{slug}/in-deutschland?sort=2&ag=age_1`. Parses SSR `data-at` attributes + `<time>` relative timestamps. `ag=age_1` pre-filters to last 24h. |
| Glassdoor | Free HTML scraping | $0.00 | `cloudscraper` with chrome emulation. 10 search roles × 3 pages. Parses JSON-LD ItemList from SSR HTML. Company from URL slug `_KE` offsets. `fromAge=1` = last 24h. Cloudflare blocks ~60% → 8x retry with fresh instances (~98% reliability). |
| ~~Kununu~~ | DROPPED | $0.00 | `shahidirfan/kununu-jobs-scraper` returns 0 results (broken). All 8 Apify store kununu actors are review scrapers, not job listing scrapers. |
| **Total** | | **~$0.55** | **Was $1.50-3.00** (82% reduction) |

### What was costing $2.50-3.00/run
1. **LinkedIn `maxItems` bug**: The code sent `maxItems: 30` but the actor uses `count` (default 100). So it scraped 100 results × 24 URLs = ~1070 results × $0.001 = **$1.07/run**. Fixed: use `count` parameter directly.
2. **ABORTED LinkedIn runs**: Two aborted runs cost $1.24 + $1.36 = **$2.60** — aborted runs still charge for results scraped before abort. Fixed: `maxTotalChargeUsd=$0.60` cap prevents runaway costs.
3. **24 redundant SEARCH_ROLES**: Each role triggered a separate Indeed/Kununu run. Consolidated to 10 core roles.
4. **LinkedIn count regression (2026-08-05)**: Temporarily set to `count=15` which returned only 16 jobs total. Restored to `count=500` (~$0.50/run at $0.001/result) for adequate coverage.

### Quality Filters

- **`is_relevant_title(title)`**: Universal post-filter requiring at least one data/analytics/AI/SQL/Python keyword in the job title. Applied to ALL platforms via `check_experience_and_location()`. Catches:
  - Indeed returning "Nachtwächter" (night watchman) for "Data Engineer" searches
  - Indeed returning "Kita Standortleitung" (daycare manager) for "Business Analyst" searches
  - Arbeitnow returning "Schlosser / Metallbauer" (metalworker) — was caused by substring matching "bi" in description
  - LinkedIn returning "Junior Software Engineer" — no data keywords in title
- **Germany Location Guard** (`check_experience_and_location()` §2a): Rejects any job whose location string explicitly names a non-Germany country (Austria, Switzerland, Netherlands, France, UK, etc. — 30 countries in EN/DE). Added 2026-08-07 after LinkedIn rolled out AI-powered job search (actor `autoConvertToAiSearch` now defaults `true`), which softens `location=Germany` from a hard URL filter into a natural-language search hint. Ambiguous locations (city-only, "Remote") pass through to avoid false rejections. The `f_TPR=r86400` (24h date-posted) filter is unaffected — it remains a supported URL filter under AI search.

### Verified Actor Input Schemas

| Platform | Actor | Actor ID | Key input params | Pricing |
|---|---|---|---|---|
| LinkedIn | `curious_coder/linkedin-jobs-scraper` | `hKByXkMQaC5Qt9UMN` | `urls[]`, `count` (NOT `maxItems`), `scrapeCompany`, `autoConvertToAiSearch` (default `true` — converts unsupported classic filters like `location` into natural-language search terms; `f_TPR` date-posted filter stays as URL filter) | $0.001/result (PAY_PER_EVENT) |
| Indeed | `valig/indeed-jobs-scraper` | `TrtlecxAsNRbKl1na` | `country`, `title`, `location`, `limit`, `datePosted` | $0.0001/result (PAY_PER_EVENT) |
| Arbeitnow | — | — | Free REST API: `https://www.arbeitnow.com/api/job-board-api` | Free |
| Xing | — | — | HTML scraping via plain `requests` (Xing is behind AWS CloudFront, not Cloudflare — no anti-bot). URL: `xing.com/search/in/jobs?keywords=<role>&location=germany&page=<N>`. Parses `data-testid="job-teaser-list-title"`, `aria-label` on `<img>`, `<time dateTime>` for 24h filter. | Free |
| Stepstone | — | — | HTML scraping via plain `requests` (Stepstone is behind Akamai, not Cloudflare — cloudscraper hangs, plain requests works). URL: `stepstone.de/jobs/{slug}/in-deutschland?sort=2&ag=age_1&page=<N>`. Path-based URLs required — query-param `?keyword=` returns generic results. Parses `data-at="job-item-title"`, `data-at="job-item-company-name"`, `data-at="job-item-location"`, `data-at="job-item-timeago"` (`<time>` tag). `ag=age_1` = last 24h filter. | Free |
| Glassdoor | — | — | HTML scraping via `cloudscraper` (chrome emulation). URL: `glassdoor.de/Job/jobs.htm?sc.keyword=<role>&locT=C&locId=26&fromAge=1&page=<N>`. Parses JSON-LD `<script type="application/ld+json">` ItemList (30 jobs/page). Company extracted from URL slug using `_KE{start},{end}` character offsets. `fromAge=1` = last 24h. `locId=26` = Germany. No posted-date in JSON-LD — `fromAge=1` guarantees freshness. | Free |

### Notes / Gotchas
- **Output layout**: each run writes its files into `/home/sagar/Skills/Jobscraper/Job Search/YYYY-MM-DD/`.
- **XLSX export**: requires `openpyxl`. If missing, script skips XLSX with a warning.
- **Startup.jobs**: uses `cloudscraper` to bypass Cloudflare challenge. Install: `pip install cloudscraper`. Parses `data-post-template-target` attributes from server-rendered HTML.
- **Xing**: uses plain `requests` (NOT `cloudscraper`). Xing is behind **AWS CloudFront** (not Cloudflare) — no anti-bot challenge, so `cloudscraper` was unnecessary overhead. Server-rendered HTML with `data-testid` attributes (stable test IDs, not styled-components hashes). No date filter URL parameter — must fetch all results and filter by `<time dateTime>` ISO timestamp post-hoc. Sponsored listings lack `dateTime` and are skipped. ~8% of raw results are <24h fresh. Stops paginating when a page yields 0 fresh jobs.
- **Stepstone**: uses plain `requests` (NOT `cloudscraper`). Stepstone is behind **Akamai** (not Cloudflare) — `cloudscraper`'s challenge-solving hangs on Akamai, causing `ReadTimeout` on port 443. Plain `requests` with a browser User-Agent works (Stepstone serves SSR HTML without anti-bot challenge). Server-side rendered HTML (React hydration) with `data-at` attributes. **Critical**: must use path-based URLs (`/jobs/{slug}/in-deutschland`) — the query-param format (`?keyword=...`) returns generic results regardless of the search term. `<style>` and `<svg>` blocks must be stripped before regex parsing (they contain CSS/paths that interfere with `data-at` attribute matching). Company name is nested inside a `<div>` within the TEXT span (unlike location which is plain text). The `ag=age_1` parameter pre-filters to last 24h ('Neuer als 24h'). `sort=2` sorts by date (Datum). Timeago strings are German relative format ('vor 49 Minuten', 'vor 3 Stunden') parsed by `parse_stepstone_timeago()`.
- **Glassdoor**: uses `cloudscraper` with chrome browser emulation to bypass Cloudflare. Glassdoor is a React SPA but SSR-embeds job listings as JSON-LD `<script type="application/ld+json">` ItemList tags (30 jobs/page) — no React hydration needed for titles/URLs. Company names are extracted from URL slugs using `_KE{start},{end}` character offsets (Glassdoor's KO/KE encoding: KO = keyword offset, KE = employer offset). **CRITICAL: `fromAge=1` URL parameter is IGNORED by SSR** — Glassdoor's React app applies date filtering client-side after hydration. The SSR HTML always returns unfiltered results (jobs 30-165 days old). Without `ageInDays` filtering, all jobs appear as "posted today". Fixed by extracting `ageInDays` from the Next.js RSC payload (`self.__next_f.push`): each job entry has `\"ageInDays\":NNN` paired with `listingId:LLL` (which matches the `jl` parameter in JSON-LD URLs). Only `ageInDays == 0` (posted today) passes the filter. Jobs with unverified age (`None`) are skipped. Location defaults to "Germany" (`locId=26`). Cloudflare blocks ~60% of requests — 8x retry with fresh instances (~98% reliability). No login required. **Yield warning**: in testing, 0/269 raw listings had `ageInDays == 0` — Glassdoor's SSR returns predominantly stale jobs. Fresh yield is expected to be very low compared to other platforms.
- **Indeed actor**: sometimes returns 0 results for Germany — this is an actor-side issue, not our code. The `title` param does fuzzy search, not exact match, so the `is_relevant_title` post-filter is essential.
- **LinkedIn AI search (2026-08-07)**: LinkedIn rolled out AI-powered job search, removing most classic URL filters (experience, job type, workplace, salary, sort). The actor's `autoConvertToAiSearch` (default `true`) converts unsupported filters into natural-language search terms appended to keywords. Only date posted (`f_TPR`), company, easy apply, and under-10-applicants remain as URL filters. `location=Germany` is softened to a search hint — the Germany Location Guard in `check_experience_and_location()` catches leakage. To force exact classic URL behavior, set `autoConvertToAiSearch: false` in the actor call (not recommended — LinkedIn forces AI search server-side). To use the pre-AI actor, select `last-v6` build under Run options on Apify.
- **Token**: read from env var `APIFY_TOKEN` or `config.json`. Rotate if exposed.
- Rejected/tested: `misceres/indeed-scraper` (weak output), `borderline/indeed-scraper` (run failed), all 8 Apify store kununu actors (review scrapers, not job listings, or broken).
