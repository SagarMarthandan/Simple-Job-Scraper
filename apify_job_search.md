# Automated Job Search, Filtering & Dynamic CSV Export (Last 24 Hours)

> **Instructions for Oh My Pi (OMP) Session:**
> When this file is uploaded/run in an OMP session, execute the automated job search pipeline below.
> It fetches fresh job postings (< 24 hours old) from **LinkedIn**, **Indeed**, **Arbeitnow**, **Xing**, **Stepstone**, **Wellfound**, **EU Remote Jobs**, and **ATS Direct** across all target role profiles, applies location-aware working student rules and internship inclusions, deduplicates them, filters out roles requiring > 2 years of experience, and outputs a sortable **CSV + XLSX** (autofilter dropdowns, clickable links) with the current execution date/time stamp into `/home/sagar/Skills/Jobscraper/Job Search/`.

> **100% Free (2026-09-05):** $0.00/run — all platforms free. Indeed uses a public GraphQL API; LinkedIn uses free HTML scraping. No Apify.

---

## 1. Context & Setup

- **Candidate Name:** Sagar Marthandan
- **Base Location:** Kiel, Germany
- **Base Resumes Directory:** `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/base_files`
- **Portfolio Directory:** `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/portfolio`
- **Target Output Directory:** `/home/sagar/Skills/Jobscraper/Job Search`
- **Cost:** $0.00/run (all platforms free — no Apify)
- **Dependency:** `requests` (for Xing, Stepstone, Indeed GraphQL API), `beautifulsoup4` (for HTML parsing and JSON-LD description extraction), `openpyxl` (for XLSX export). Install: `pip install requests beautifulsoup4 openpyxl`
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
   - **LinkedIn** (free HTML scraping, $0 — 6 locations × 10 roles, `f_TPR=r86400` 24h filter)
   - **Indeed** (free GraphQL API, $0 — `apis.indeed.com/graphql`, mobile user-agent, `dateOnIndeed` 24h filter, full descriptions)
   - **Arbeitnow** (free REST API, $0)
   - **Xing** (free HTML scraping via plain `requests`, $0 — AWS CloudFront, no anti-bot; parses `data-testid` attributes and `<time dateTime>` ISO timestamps; 3 pages per role)
   - **Stepstone** (free HTML scraping via plain `requests`, $0 — Akamai, plain requests work; path-based URLs with `ag=age_1` 24h filter; parses SSR `data-at` attributes; 3 pages per role)
   - **Wellfound** (free HTML scraping, $0 — SSR role pages `/role/l/{slug}/germany`, company names from `<img alt="X company logo">`, JSON-LD detail pages for full descriptions; 6 role slugs)
   - **EU Remote Jobs** (free WordPress REST API, $0 — `/wp-json/wp/v2/job-listings`, full descriptions in `content.rendered`, filters by Data/Engineering/IT categories; 30s timeout)
   - **ATS Direct** (free public JSON APIs, $0 — Greenhouse/SmartRecruiters/Ashby, 17 German tech companies)
   - ~~Startup.jobs~~ (DROPPED — 1.1 jobs/run average, negligible yield)
   - ~~Glassdoor~~ (DROPPED — 1.1 jobs/run average, most complex scraper for negligible yield)
   - ~~Kununu~~ (DROPPED — all Apify store kununu actors are review scrapers, not job listings)
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

Dependencies: `requests` (for Xing, Stepstone, Indeed GraphQL API), `beautifulsoup4` (for HTML parsing and JSON-LD description extraction), `openpyxl` (for XLSX export).
Install: `pip install requests beautifulsoup4 openpyxl`

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

`load_seen_job_urls()` scans every dated export folder earlier than the current
run date. It excludes current/future folders, uses exact stable posting URL
identity only, and fails visibly if a dated history export is unreadable.
Historical output files are read-only and are never rewritten.

| Identity | Normalization |
|---|---|
| LinkedIn | Canonical numeric job ID from the posting path or job-ID query |
| Indeed | Canonical `jk` value on a legitimate Indeed domain |
| Other sites | Lowercase scheme/host, preserve case-sensitive path and unknown query values, sort remaining query pairs, remove recognized tracking keys/prefixes |

No company/title fuzzy matching is used across dates. Within-run
company/title dedup remains separate. On same-day reruns, the current dated
export is loaded and preserved only after Germany/working-student eligibility
and the all-prior-history check; freshly scraped URL duplicates are skipped.

Verification uses the same normalizer and history loader before platform
requests. Foreign/unknown rows are excluded first; German rows with exact URLs
from earlier dated exports do not receive platform or JD work and may be routed
to **Previously Seen** when verifying a legacy input.

## 5. Cost & Platform Details (last updated 2026-09-07)

### Cost Breakdown (per full pipeline run)

| Platform | Source | Cost/run | Notes |
|---|---|---|---|
| LinkedIn | Free HTML scraping | $0.00 | 6 locations × 10 roles, `f_TPR=r86400` 24h filter. 5 workers, 3s backoff on 429. ~196 jobs/run. |
| Indeed | Free GraphQL API | $0.00 | `apis.indeed.com/graphql` — same endpoint as Indeed iOS app. Hardcoded API key, mobile user-agent, `indeed-co: DE`. `dateOnIndeed` 24h server-side filter. Full descriptions (HTML stripped to text). 5 workers, ~23 jobs/run. |
| Arbeitnow | Free REST API | $0.00 | `https://www.arbeitnow.com/api/job-board-api` |
| Xing | Free HTML scraping | $0.00 | Plain `requests` (AWS CloudFront, no anti-bot). 10 roles × 3 pages. `data-testid` attributes + `<time dateTime>`. ~279 jobs/run. |
| Stepstone | Free HTML scraping | $0.00 | Plain `requests` (Akamai, no anti-bot). 10 roles × 3 pages. Path-based URLs with `ag=age_1` 24h filter. `data-at` attributes. ~45 jobs/run. |
| Wellfound | Free HTML scraping | $0.00 | SSR role pages `/role/l/{slug}/germany` (6 slugs). Company from `<img alt="X company logo">`. JSON-LD detail pages for full descriptions. 3 workers. ~2 jobs/run (startup board, low volume). |
| EU Remote Jobs | Free WordPress REST API | $0.00 | `/wp-json/wp/v2/job-listings` with `after` date filter. Full descriptions in `content.rendered`. Filters by Data/Engineering/IT categories. 30s timeout. ~4 jobs/run. |
| ATS Direct | Free public JSON APIs | $0.00 | Greenhouse/SmartRecruiters/Ashby, 17 German tech companies. ~4 jobs/run. |
| **Total** | | **$0.00** | **100% free — no Apify** |

### Indeed GraphQL API Details

The Indeed scraper uses Indeed's public GraphQL API at `apis.indeed.com/graphql`. This is the same endpoint used by the Indeed iOS app:

- **API key**: hardcoded in the Indeed mobile app (`161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8`)
- **Headers**: mobile user-agent (`Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Indeed App 193.1`), `indeed-co: DE` for Germany, `indeed-app-info` for app version
- **Query**: GraphQL `jobSearch` with `what` (role), `location` (Germany), `limit: 50`, `sort: RELEVANCE`, `filters: { date: { field: "dateOnIndeed", start: "24h" } }`
- **Response**: full job data including `key` (job ID), `title`, `description.html` (full JD), `employer.name`, `location.formatted.long`, `dateOnIndeed` (epoch ms)
- **Job URL**: constructed as `https://de.indeed.com/viewjob?jk={key}`
- **No Cloudflare, no auth, no rate limiting observed** at 5 concurrent workers
- **Discovered via**: [JobSpy](https://github.com/speedyapply/JobSpy) open-source library (MIT license)

### Quality Filters

- **`is_relevant_title(title)`**: Universal post-filter requiring at least one data/analytics/AI/SQL/Python keyword in the job title. Applied to ALL platforms via `check_experience_and_location()`. Catches Indeed fuzzy search false positives ("Nachtwächter" for "Data Engineer", "Kita Standortleitung" for "Business Analyst").

### Notes / Gotchas
- **Output layout**: each run writes its files into `/home/sagar/Skills/Jobscraper/Job Search/YYYY-MM-DD/`.
- **XLSX export**: requires `openpyxl`. If missing, script skips XLSX with a warning.
- **Indeed GraphQL**: the `what` parameter does fuzzy search (searches description too), so `is_relevant_title` post-filter is essential. Use `-` to exclude terms, `""` for exact match.
- **LinkedIn rate limiting**: 5 concurrent workers × 6 locations = ~30 requests over ~14s. 429 rate limiting on ~5% of requests; 3s backoff retry catches most. 3 workers would eliminate 429s but double runtime.
- **Xing**: plain `requests`. AWS CloudFront — no anti-bot. `data-testid` attributes + `<time dateTime>` ISO timestamps. Sponsored listings (no dateTime) skipped. ~8% of raw results are <24h fresh.
- **Stepstone**: plain `requests`. Akamai — plain requests work. Path-based URLs required (`/jobs/{slug}/in-deutschland`) — query-param `?keyword=` returns generic results. `ag=age_1` = last 24h filter.
- **ATS Direct**: Greenhouse (`boards-api.greenhouse.io/v1/boards/{slug}/jobs`), SmartRecruiters (`api.smartrecruiters.com/v1/companies/{slug}/postings`), Ashby (`jobs.ashbyhq.com/{slug}` with embedded `__appData` JSON). Slug is case-sensitive (e.g. `BoschGroup` not `boschgroup`).
- **Wellfound**: SSR role pages at `/role/l/{slug}/germany` (6 slugs: data-engineer, analytics-engineer, data-analyst, ai-engineer, machine-learning-engineer, data-scientist). Company names are NOT in text elements — extracted from `<img alt="X company logo">` attributes by walking up the DOM from the job link to find the card container. Relative dates ("today", "2 days ago", "4 weeks ago") parsed via `_parse_wellfound_date()`. Job detail pages have JSON-LD JobPosting schema with full descriptions (3-7K chars), fetched via `_enrich_descriptions()` with 3 workers. No Cloudflare on role/detail pages. Low volume (~2 jobs/run) — startup board with mostly senior/remote roles.
- **EU Remote Jobs**: WordPress + WP Job Manager REST API at `/wp-json/wp/v2/job-listings`. Full JDs in `content.rendered` (HTML, stripped via BeautifulSoup) — no detail page fetching needed. Company in `meta._company_name`. Category filtering via `class_list` array (`job_listing_category-data`, `job_listing_category-engineering`, `job_listing_category-it`). Region from `class_list` (`job_listing_region-*`). `after` parameter for 24h freshness. `per_page=100` with pagination via `X-WP-TotalPages` header. API is slow — 30s timeout required (15s times out). No auth, no rate limiting, no Cloudflare.
