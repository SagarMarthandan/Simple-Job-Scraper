# Architecture

Technical details for the Jobscraper pipeline and verification post-step.

## Pipeline Flow

```mermaid
graph TD
    A[main ThreadPoolExecutor] --> B[Arbeitnow API]
    A --> D[Xing HTML]
    A --> E[Stepstone HTML]
    A --> G[LinkedIn HTML 5-thread pool]
    A --> H[Indeed GraphQL 5-thread pool]
    A --> I[ATS Direct APIs]
    A --> J[Wellfound SSR role pages 3-thread pool]
    A --> K[EU Remote Jobs WordPress REST API]
    B --> L[check_experience_and_location]
    D --> L
    E --> L
    G --> L
    H --> L
    I --> L
    J --> L
    K --> L
    L --> M[Within-run dedup by company::title]
    M --> N[Cross-run dedup vs yesterday]
    N --> O[Export CSV + JSON + MD + XLSX]
    O --> P[verify_jobs.py — platform verify → reposted detection → LLM classify → already-applied → filters → 4-sheet XLSX]

All 8 platform fetchers run simultaneously via `ThreadPoolExecutor(max_workers=8)`. Each fetcher is independent — no shared mutable state, results collected after all complete. LinkedIn internally parallelizes its 10 search roles with `max_workers=5` (limited to avoid 429 rate limiting) + 3s backoff retry. Wellfound parallelizes 6 role slugs with `max_workers=3`.

Runtime: **~156s** (was 191s sequential — 7x speedup). I/O bound work — Python releases the GIL during HTTP requests, so threads give near-linear speedup. Dominated by JSON-LD description enrichment (LinkedIn ~196 URLs, Xing ~279 URLs).

## Filter Chain

Every job passes through `check_experience_and_location()` which applies, in order:

1. **Title relevance** (`is_relevant_title`) — rejects titles with no data/analytics/AI/SQL/Python keyword. Catches actor false positives (Indeed returning "Nachtwächter" for "Data Engineer" searches).
2. **Seniority ceiling** — rejects Senior, Lead, Principal, Staff, Manager, Head, Architect, Director titles and descriptions requiring > 2 years experience.
3. **Working-student city restriction** — working student roles restricted to Hamburg and Kiel only. Full-time and internships are Germany-wide.

## Freshness Filtering (24h, all 8 platforms)

| Platform | Server-side filter | Post-filter | No-date behavior |
|---|---|---|---|
| Arbeitnow | — | `created_at` vs 24h cutoff | N/A (API always has timestamp) |
| Xing | — | `<time dateTime>` vs cutoff | Include (sponsored listings are real jobs) |
| Stepstone | `ag=age_1` (24h) | `parse_stepstone_timeago()` vs cutoff | Include (defaults to now) |
| LinkedIn | `f_TPR=r86400` (24h) | `posted_at` datetime vs cutoff | Include (safety net only) |
| Indeed | `datePosted='1'` (unreliable) | `datePublished` vs cutoff | Include (false positives > false negatives) |
| Wellfound | — | `_parse_wellfound_date()` relative date vs cutoff | Include (no date = "Last 24h") |
| EU Remote Jobs | `after` param (ISO datetime) | `date` field vs cutoff | N/A (API always has timestamp) |
| ATS: Greenhouse | — | `first_published` vs cutoff | Include (via `_is_fresh`) |
| ATS: SmartRecruiters | — | `releasedDate` vs cutoff | Include (via `_is_fresh`) |
| ATS: Ashby | — | `publishedDate` vs cutoff | Include (via `_is_fresh`) |

## Deduplication (Two Tiers)

| Tier | Stage | Method | Catches |
|---|---|---|---|
| 1 | **Within-run** | `normalize_key()` — strips parentheticals, legal suffixes (`gmbh\|ag\|group\|gruppe\|international\|deutschland\|germany\|global\|e.g.`), seniority/gender markers (`senior\|junior\|m/w/d`), and REF codes (`REF99139A`). Exact match on `company::title`. | Same posting on same platform with name/title variants |
| 2 | **Cross-run** | `load_previous_run_urls()` — compares today's URLs against yesterday's CSV. | Consecutive-day duplicates from 24h window overlap |

## Verification Post-Step

### Per-Platform Strategy

| Platform | Sandbox (TinyFish) | Standalone (no TinyFish) | Workers |
|---|---|---|---|
| LinkedIn | TinyFish pre-fetch (89% render rate) | Plain `requests` + JSON-LD (6% hit rate) | 2 |
| Indeed | TinyFish pre-fetch (fills Apify gaps) | Apify JSON description only | 1 |
| Xing | TinyFish pre-fetch | Plain `requests` | 1 |
| Stepstone | TinyFish pre-fetch | Plain `requests` | 1 |
| Greenhouse/SmartRecruiters/Ashby | Public JSON API (no change) | Public JSON API | 4 |
| Arbeitnow | Free API (no change) | Free API | 1 |

All platforms run in parallel via `ThreadPoolExecutor` (1 thread per platform). Network errors don't drop jobs — `verified_active` is left empty (treated as "unknown, keep").

### TinyFish JD Pre-Fetch

When `tinyfish_fetch` is injected, all JD-dependent platforms (LinkedIn, Indeed, Xing, Stepstone) are pre-fetched via TinyFish in the **main thread** before platform verification starts. TinyFish MCP tool is not thread-safe — calling `tool.*` from `ThreadPoolExecutor` worker threads raises `RuntimeError: Missing session/run/name`. ATS platforms and Arbeitnow are skipped (public APIs with 100% accuracy).

JDs are fetched in batches of 2 URLs (TinyFish response truncates at ~25K chars with larger batches), injected into `row["description"]`. Fetched JDs are cached to `tinyfish_cache.json` in the run directory after every batch — re-runs load the cache and skip already-fetched URLs (no wallet re-spend). Each platform verifier checks for a pre-fetched description (>50 chars) and calls `_process_result` directly. Falls back to the platform's native method when no pre-fetched description is available.

**Auth-wall detection** (LinkedIn): if TinyFish returns only LinkedIn boilerplate (Similar jobs, People also viewed, Referrals increase) without real JD markers (requirements, responsibilities, Aufgaben, etc.), the job is flagged with `detail_language = "AUTH WALL — review manually"` and left unverified. ~11% of LinkedIn jobs affected.

**Runtime**: ~500 URLs → 250 batches × ~8s = ~33 min. No cost — TinyFish `fetch_content` is free.

### Playwright Indeed Fallback

TinyFish cannot fetch Indeed JDs (`target_http_error`). After the TinyFish pre-fetch stage, any Indeed jobs still missing descriptions are fetched via `indeed_playwright_fetch.js` — a Node.js script using `playwright-extra` + `puppeteer-extra-plugin-stealth` with a mobile user agent and mobile URL path (`/m/viewjob`). This bypasses both Cloudflare (stealth plugin) and Indeed's login wall (mobile path). Results are injected into `row["description"]` and cached in `tinyfish_cache.json`. Runs sequentially (~4s per URL). Dependencies installed in the Jobscraper directory: `playwright`, `playwright-extra`, `puppeteer-extra-plugin-stealth`, Chromium.

### LLM Classification

German level and experience years are classified by an LLM (smol model via `completion()`) in batches of 10 JDs per call. Output format is plain-text `N|level|years` per line (not JSON schema — JSON caused response shape mismatches across models). The parser uses `_LLM_LINE_RE = re.compile(r'^(\d+)\s*\|\s*(C1\+|B1/B2|preferred|none)\s*\|\s*(\d*)\s*$', re.IGNORECASE)`. Unparsed lines get `{"german": "none", "exp_years": None}` defaults. No regex fallback — if `completion()` is unavailable, all jobs get defaults and the run output shows "LLM not available — skipping classification". Current smol model: Gemini 3.1 Flash Lite.

### Reposted LinkedIn Detection

Two signals (pure computation — no LLM tokens, no TinyFish):
1. **Cross-run history** — same `company::title` appeared in a run >7 days ago
2. **Job ID age gap** — LinkedIn creates ~530K IDs/day; if job ID suggests >14 days old, flag as reposted

**Job ID override**: if the job ID is fresh (<14 days old), signal 1 is suppressed — a fresh ID means it's a new posting, not a repost, even if the same company+title appeared in an old run.

**Carryover exception**: if the job URL appeared in the most recent previous run, it's a carryover (not a repost) — skip signal 1.

URLs normalized (trailing slash + query params stripped) before comparison. `datePosted` is reset on repost, so it can't be used.

### Verified XLSX Output

`Job_Search_<date>_verified.xlsx` — 4-sheet Excel workbook:

| Sheet | Content |
|---|---|
| **To Apply** | Jobs that passed all filters (active, German ≤B2, exp <3y, not staffing) |
| **Reposted** | LinkedIn jobs flagged as reposted (for manual review — not dropped) |
| **Staffing Companies** | Staffing/recruitment agency postings (segregated, not dropped — genuine recruiter outreach may be visible) |
| **Already Applied** | Jobs matching `applications_tracker.csv` |

| Column | Values |
|---|---|
| `verified_active` | `True` / `False` / empty (unknown) |
| `detail_language` | `German C1+ required` (dropped) / `German preferred` (flagged) / `German B1/B2 OK` (kept) / `AUTH WALL — review manually` / empty |
| `detail_exp_years` | Integer (minimum years required) or empty |
| `detail_reposted` | `True` / `False` (LinkedIn only) / empty |
| `detail_salary` | e.g. `45000-60000 EUR/year` or empty |
| `detail_remote` | `remote` / `hybrid` / `onsite` / empty |
| `match_score` | Recalculated from JD text (0-100%) |

Rows are dropped if `verified_active = False` OR `detail_language = "German C1+ required"` OR `detail_exp_years >= 3`. Reposted, staffing, and already-applied jobs are segregated to their respective sheets (not dropped).

## Target Role Profiles

| # | Role | Covers variants |
|---|---|---|
| 1 | Data Engineer | Junior, Cloud, Data Warehouse, ETL, Dateningenieur |
| 2 | Analytics Engineer | — |
| 3 | Data Analyst | Datenanalyst, BI Developer, BI Entwickler |
| 4 | AI Engineer | AI Data Engineer, GenAI, Junior Data Scientist |
| 5 | Machine Learning Engineer | — |
| 6 | Business Analyst | — |
| 7 | SQL Developer | Database Developer, Python Data Developer |
| 8 | Praktikum Data | Internship Data |
| 9 | Werkstudent Data | Working Student Data |
| 10 | Werkstudent Business Intelligence | Working Student BI |

## Function Reference

### Pipeline (`apify_job_search.py`)

| Function | Purpose |
|---|---|
| `fetch_arbeitnow_jobs()` | Free REST API, filters by `created_at` |
| `fetch_xing_jobs()` | `requests` HTML, `data-testid` attrs, no-date jobs included. Delegates parsing to `_parse_xing_card()` |
| `fetch_stepstone_jobs()` | `requests` HTML, `data-at` SSR attrs, `ag=age_1`. Delegates parsing to `_parse_stepstone_card()` |
| `fetch_linkedin_jobs_free()` | Free HTML scraping, multi-city (6 locations), 10 roles parallel, 429 retry |
| `fetch_indeed_jobs()` | GraphQL API (`apis.indeed.com/graphql`), 10 roles parallel, `dateOnIndeed` 24h filter, full descriptions |
| `fetch_wellfound_jobs()` | SSR role pages (`/role/l/{slug}/germany`), 6 slugs, 3 workers. Company from `<img alt>`. JSON-LD enrichment via `_enrich_descriptions()` |
| `fetch_euremotejobs_jobs()` | WordPress REST API, full descriptions in `content.rendered`, Data/Eng/IT category filter, paginated |
| `_parse_wellfound_date()` | Converts Wellfound relative dates ("today", "2 days ago", "4 weeks ago") to datetime |
| `fetch_all_ats()` | Orchestrator for Greenhouse/SmartRecruiters/Ashby |
| `check_experience_and_location()` | Multi-stage filter: title relevance → seniority → city |
| `compute_match_score()` | Percentage match against `TECH_KEYWORDS` |
| `normalize_key()` | Dedup key: `company::title` with parentheticals, legal suffixes, seniority/gender markers, REF codes stripped |
| `load_previous_run_urls()` | URL set from yesterday's CSV for cross-run dedup |
| `convert_csv_to_xlsx()` | openpyxl export. Delegates styling to `_style_xlsx_header()`, `_format_xlsx_cells()` |

### ATS (`ats_scraper.py`)

| Function | Purpose |
|---|---|
| `fetch_greenhouse()` | Greenhouse public JSON API, `first_published` freshness |
| `fetch_smartrecruiters()` | SmartRecruiters public JSON API, paginated, `releasedDate` |
| `fetch_ashby()` | Ashby embedded `window.__appData` JSON, `publishedDate` |
| `_is_fresh()` | Freshness check — returns True when date is None (false positives > false negatives) |

### Verification (`verify_jobs.py`)

| Function | Purpose |
|---|---|
| `run_verification()` | Main entry: load CSV, verify per-platform, reposted detection, LLM classify, already-applied detection, staffing segregation, filter, write 4-sheet XLSX. Prints description acquisition + classification coverage stats |
| `verify_linkedin()` | Reads pre-fetched description from step 1 JSON-LD. Auth-wall detection for boilerplate-only responses |
| `verify_indeed()` | Uses GraphQL API description from step 1 |
| `verify_xing()` / `verify_stepstone()` | Reads pre-fetched description from step 1 JSON-LD |
| `verify_greenhouse()` / `verify_smartrecruiters()` / `verify_ashby()` | ATS API verification — 404/empty = closed. Ashby delegates to `_find_ashby_posting()`, `_extract_ashby_desc()` |
| `verify_arbeitnow()` | Free API verification |
| `detect_reposted()` | Cross-run history (>7d) + job ID age gap (>14d), with job ID override and carryover exception. Pure computation — no LLM tokens |
| `_load_repost_data()` | Loads repost detection data. Delegates to `_find_previous_run_dirs()`, `_load_urls_from_csv()`, `_load_linkedin_title_keys_from_csv()` |
| `llm_classify_batch()` | LLM batch classification (10 JDs/call, plain-text `N|level|years` format). Returns `{"german": "none", "exp_years": None}` defaults on failure — no regex fallback. Delegates parsing to `_parse_llm_response()` |
| `llm_classify_all()` | Orchestrates LLM classification across all rows. Reads `row["description"]` directly. Prints coverage stats |
| `extract_salary()` | Salary from JSON-LD `baseSalary` or body text regex. Delegates to `_extract_salary_jsonld()`, `_detect_salary_period()` |
| `extract_remote()` | Remote/hybrid/onsite detection from JD text |
| `compute_match_score_from_jd()` | Recalculates match score from full JD text |
| `save_xlsx()` | 4-sheet XLSX export (To Apply + Reposted + Staffing Companies + Already Applied) |

### Staffing Filter (`staffing_filter.py`)

| Function | Purpose |
|---|---|
| `is_staffing_company()` | Checks a raw company name against `STAFFING_COMPANIES` regex (word-boundary, case-insensitive). Single source of truth — imported by both `apify_job_search.py` and `verify_jobs.py` |

### Already-Applied Detection (`applied_check.py`)

| Function | Purpose |
|---|---|
| `load_applied_data()` | Reads `/home/sagar/Documents/applications_tracker.csv` (Company + Position + Source URL columns). Returns dict with normalized `company::title` keys, company→titles mapping, normalized URLs, and raw entries list for LLM input prep |
| `prepare_match_input(csv_path)` | Pre-filters candidate pairs between today's scraped jobs and tracker entries by company token overlap (≥1 shared meaningful token, excluding legal suffixes/stopwords) + exact URL match. Saves flat array of pair objects to `/tmp/already_applied_input.json` for JS-side LLM classification |
| `load_llm_matches()` | Loads `/tmp/already_applied_matches.json` (LLM-classified matches from JS eval). Returns `{"urls": set, "keys": set}` for O(1) lookup, or None if file doesn't exist (fallback to deterministic match) |
| `is_already_applied()` | Checks LLM match sets (URLs + keys) first. Falls back to deterministic URL+key match when `llm_matches` is None (standalone execution without LLM) |
| `load_applied_job_keys()` | Legacy: returns `set[str]` of normalized keys only. Kept for backward compatibility |
