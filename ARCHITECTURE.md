# Architecture

Technical details for the Jobscraper pipeline and verification post-step.

## Pipeline Flow

```mermaid
graph TD
    A[main ThreadPoolExecutor] --> B[Arbeitnow API]
    A --> D[Xing HTML]
    A --> E[Stepstone HTML]
    A --> G[LinkedIn HTML 5-thread pool]
    A --> H[Indeed Apify 8-thread pool]
    A --> I[ATS Direct APIs]
    B --> J[check_experience_and_location]
    D --> J
    E --> J
    G --> J
    H --> J
    I --> J
    J --> K[Within-run dedup by company::title]
    K --> L[Cross-run dedup vs yesterday]
    L --> M[Export CSV + JSON + MD + XLSX]
    M --> N[verify_jobs.py — TinyFish JD fetch + cache → platform verify → reposted detection → LLM classify → filters → 2-sheet XLSX]

### Parallelization

All 6 platform fetchers run simultaneously via `ThreadPoolExecutor(max_workers=6)`. Each fetcher is independent — no shared mutable state, results collected after all complete. LinkedIn internally parallelizes its 10 search roles with `max_workers=5` (limited to avoid 429 rate limiting) + 3s backoff retry.

Runtime: **~27s** (was 191s sequential — 7x speedup). I/O bound work — Python releases the GIL during HTTP requests, so threads give near-linear speedup.

## Filter Chain

Every job passes through `check_experience_and_location()` which applies, in order:

1. **Title relevance** (`is_relevant_title`) — rejects titles with no data/analytics/AI/SQL/Python keyword. Catches actor false positives (Indeed returning "Nachtwächter" for "Data Engineer" searches).
2. **Seniority ceiling** — rejects Senior, Lead, Principal, Staff, Manager, Head, Architect, Director titles and descriptions requiring > 2 years experience.
3. **Working-student city restriction** — working student roles restricted to Hamburg and Kiel only. Full-time and internships are Germany-wide.

## Freshness Filtering (24h, all 6 platforms)

| Platform | Server-side filter | Post-filter | No-date behavior |
|---|---|---|---|
| Arbeitnow | — | `created_at` vs 24h cutoff | N/A (API always has timestamp) |
| Xing | — | `<time dateTime>` vs cutoff | Include (sponsored listings are real jobs) |
| Stepstone | `ag=age_1` (24h) | `parse_stepstone_timeago()` vs cutoff | Include (defaults to now) |
| LinkedIn | `f_TPR=r86400` (24h) | `posted_at` datetime vs cutoff | Include (safety net only) |
| Indeed | `datePosted='1'` (unreliable) | `datePublished` vs cutoff | Include (false positives > false negatives) |
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

`Job_Search_<date>_verified.xlsx` — 2-sheet Excel workbook:

| Sheet | Content |
|---|---|
| **Job Search** | Jobs that passed all filters (active, German ≤B2, exp <3y, not reposted) |
| **Reposted** | LinkedIn jobs flagged as reposted (for manual review — not dropped) |

| Column | Values |
|---|---|
| `verified_active` | `True` / `False` / empty (unknown) |
| `detail_language` | `German C1+ required` (dropped) / `German preferred` (flagged) / `German B1/B2 OK` (kept) / `AUTH WALL — review manually` / empty |
| `detail_exp_years` | Integer (minimum years required) or empty |
| `detail_reposted` | `True` / `False` (LinkedIn only) / empty |
| `detail_salary` | e.g. `45000-60000 EUR/year` or empty |
| `detail_remote` | `remote` / `hybrid` / `onsite` / empty |
| `match_score` | Recalculated from JD text (0-100%) |

Rows are dropped if `verified_active = False` OR `detail_language = "German C1+ required"` OR `detail_exp_years >= 3`. Reposted jobs are segregated to the Reposted sheet (not dropped).

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
| `fetch_linkedin_jobs()` | Apify fallback (paid). Delegates parsing to `_parse_linkedin_item()`, `_parse_linkedin_date()` |
| `fetch_indeed_jobs()` | Apify actor, 10 roles parallel, post-filter on `datePublished` |
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
| `run_verification()` | Main entry: load CSV, TinyFish pre-fetch + cache, verify per-platform, reposted detection, LLM classify, filter, write 2-sheet XLSX. Prints description acquisition + classification coverage stats |
| `verify_linkedin()` | Uses pre-fetched TinyFish description or falls back to `requests` + JSON-LD. Auth-wall detection for boilerplate-only responses |
| `verify_indeed()` | Uses Apify JSON description or TinyFish pre-fetch |
| `verify_xing()` / `verify_stepstone()` | Pre-fetched description or native requests fallback |
| `verify_greenhouse()` / `verify_smartrecruiters()` / `verify_ashby()` | ATS API verification — 404/empty = closed. Ashby delegates to `_find_ashby_posting()`, `_extract_ashby_desc()` |
| `verify_arbeitnow()` | Free API verification |
| `detect_reposted()` | Cross-run history (>7d) + job ID age gap (>14d), with job ID override and carryover exception. Pure computation — no LLM tokens |
| `_load_repost_data()` | Loads repost detection data. Delegates to `_find_previous_run_dirs()`, `_load_urls_from_csv()`, `_load_linkedin_title_keys_from_csv()` |
| `llm_classify_batch()` | LLM batch classification (10 JDs/call, plain-text `N|level|years` format). Returns `{"german": "none", "exp_years": None}` defaults on failure — no regex fallback. Delegates parsing to `_parse_llm_response()` |
| `llm_classify_all()` | Orchestrates LLM classification across all rows. Reads `row["description"]` directly. Prints coverage stats |
| `extract_salary()` | Salary from JSON-LD `baseSalary` or body text regex. Delegates to `_extract_salary_jsonld()`, `_detect_salary_period()` |
| `extract_remote()` | Remote/hybrid/onsite detection from JD text |
| `compute_match_score_from_jd()` | Recalculates match score from full JD text |
| `save_xlsx()` | 2-sheet XLSX export (Job Search + Reposted) |
