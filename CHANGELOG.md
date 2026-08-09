# Changelog

All notable changes to the Jobscraper pipeline are documented here.
Dates are in ISO 8601 format (`YYYY-MM-DD`).

## [2026-08-09b]

### Critical Fix
- **Indeed `datePosted='1'` parameter is IGNORED by the API** — same bug as Glassdoor's `fromAge=1`. Audit of 5 runs (102 jobs) found 11 stale jobs (10.8%) leaking through, including jobs **526 days old** (B&L Real Estate, posted Feb 2025) and 513 days old (Helios Kliniken, posted Mar 2025). The `datePosted` parameter is sent to the actor but Indeed's API silently ignores it, returning unfiltered results. Fixed by adding a post-filter on `datePublished` in `fetch_indeed_jobs()` that rejects any job older than `FRESHNESS_HOURS` (24h).

### Added
- **LinkedIn safety-net post-filter** in `fetch_linkedin_jobs()`. LinkedIn's `f_TPR=r86400` server-side filter was verified working (727/727 jobs within 24h across 5 runs), but the post-filter provides defense-in-depth against future regressions. Date-only `postedAt` timestamps (LinkedIn's format: `YYYY-MM-DD`) are treated as end-of-day (`23:59:59`) to avoid false rejections of jobs posted late on the previous day.
- **Naive datetime fix** — `datetime.fromisoformat("2026-08-08")` returns a timezone-naive datetime that causes `TypeError` when compared with the timezone-aware `cutoff`. Both `fetch_linkedin_jobs()` and `fetch_indeed_jobs()` now add `tzinfo=timezone.utc` when `fromisoformat` returns a naive datetime.

### Freshness Audit Summary
- **Arbeitnow**: ✅ Clean — API `created_at` timestamps, all 10 jobs 0.9–21h old.
- **Startup.jobs**: ✅ Clean — SSR `timestamp` attribute, real UTC timestamps, 24h post-filter works.
- **Xing**: ✅ Clean — `<time dateTime>` ISO timestamps, sponsored listings (no timestamp) correctly skipped.
- **Stepstone**: ✅ Clean — `ag=age_1` server-side filter verified working (changes results), German timeago strings genuine.
- **LinkedIn**: ✅ Clean — `f_TPR=r86400` server-side filter verified (727/727 within 24h), safety-net post-filter added.
- **Indeed**: ❌→✅ Fixed — `datePosted='1'` ignored by API, 10.8% stale jobs. Post-filter now enforces 24h.
- **Glassdoor**: ✅ Fixed (prior) — `fromAge=1` ignored by SSR, `ageInDays==0` filter added.

## [2026-08-09]

### Added
- **Glassdoor platform** — free HTML scraping via `cloudscraper` with chrome emulation. Glassdoor SSR embeds job listings as JSON-LD `<script type="application/ld+json">` ItemList tags (30 jobs/page). Company names extracted from URL slugs using `_KE{start},{end}` character offsets (Glassdoor's KO/KE encoding). `ageInDays` extracted from Next.js RSC payload (`self.__next_f.push`) and used for server-side freshness filtering. No login required. Cloudflare blocks ~60% of requests; each page fetch retries up to 8 times with fresh scraper instances (~98% reliability). 3 pages per role × 10 roles.

### Critical Fix
- **Glassdoor `fromAge=1` URL parameter is IGNORED by SSR.** Glassdoor is a React SPA that applies date filtering client-side after hydration. The SSR HTML always returns unfiltered results (jobs 30-165 days old). Without `ageInDays` filtering, all jobs appeared as "posted today" (`posted_at = now()`), delivering stale postings labeled as fresh. Fixed by extracting `ageInDays` from the RSC payload, matching via `listingId` ↔ `jl` ID, and filtering to `ageInDays == 0` only. Jobs with unverified age (`ageInDays = None`) are skipped.

### Changed
- Pipeline now fetches 7 platforms (was 6). Execution steps updated from `[1/6]`–`[6/6]` to `[1/7]`–`[7/7]`.
- `main()` print statements and cost estimate updated to reflect 7-platform pipeline.
- Script header docstring updated with Glassdoor documentation.

### Note
- Glassdoor's SSR returns predominantly stale jobs (age distribution: 1-165 days). In testing, 0 out of 269 raw listings across 10 roles had `ageInDays == 0`. Glassdoor may still produce fresh jobs on days when employers actively post, but the yield is expected to be very low compared to other platforms.

## [2026-08-07]

### Added
- **Germany Location Guard** in `check_experience_and_location()` (§2a). Rejects jobs whose location string explicitly names a non-Germany country (30 countries in EN/DE). Ambiguous locations (city-only, "Remote") pass through to avoid false rejections. Defensive against LinkedIn AI search softening `location=Germany` into a natural-language hint, and against Indeed's known Germany drift.
- `NON_GERMANY_COUNTRIES` constant tuple (30 countries, English + German names).
- `.gitignore` — ignores `__pycache__/`, `Job Search/` output, `config.json` (token).
- `README.md` — project documentation with quick start, architecture, and function reference.
- `CHANGELOG.md` — this file.

### Changed
- `apify_job_search.md` Section 5 header updated from "2026-08-05" to reflect ongoing maintenance.
- `apify_job_search.md` actor input schema table: LinkedIn row updated with `autoConvertToAiSearch` parameter documentation.
- `apify_job_search.md` Quality Filters section: added Germany Location Guard documentation.
- `apify_job_search.md` Notes/Gotchas: added LinkedIn AI search change note.

### Removed
- `stepstone_scraper.py` — standalone prototype. Logic integrated into `fetch_stepstone_jobs()` in the main pipeline. No imports referenced it.
- `csv_to_xlsx.py` — standalone prototype. Logic integrated into `convert_csv_to_xlsx()` in the main pipeline. No imports referenced it.
- `merge_linkedin.py` — one-off recovery script for a 408 run-sync error. Referenced stale path `/home/sagar/Desktop/job scrapper` (folder since moved). Non-functional.
- `__pycache__/` — Python build artifact.

### External
- **LinkedIn AI search rollout** (notified by Apify 2026-08-07): LinkedIn removed most classic URL filters (experience, job type, workplace, salary, sort). The `curious_coder/linkedin-jobs-scraper` actor's new `autoConvertToAiSearch` option (default `true`) converts unsupported filters into natural-language search terms. Only date posted (`f_TPR`), company, easy apply, and under-10-applicants remain as URL filters. The pipeline's `f_TPR=r86400` 24h filter is unaffected. The Germany Location Guard was added as a defensive measure.

## [2026-08-06]

### Added
- **Xing platform** — free HTML scraping via `cloudscraper`. Parses server-rendered job cards using `data-testid` attributes and `<time dateTime>` ISO timestamps. 3 pages per role, sponsored listings (no `dateTime`) skipped. ~8% of raw results are < 24h fresh. Encoding forced to UTF-8 (cloudscraper misdetects as ISO-8859-1).
- **Stepstone platform** — free HTML scraping via `cloudscraper`. Uses path-based URLs (`/jobs/{slug}/in-deutschland?sort=2&ag=age_1`). Parses SSR job cards with `data-at` attributes. German relative time strings ("vor 49 Minuten") parsed by `parse_stepstone_timeago()`. `ag=age_1` pre-filters to last 24h.
- `parse_stepstone_timeago()` function — converts German relative time strings to approximate UTC datetime.

### Changed
- Pipeline now fetches 6 platforms (was 4). Execution steps updated from `[1/4]`–`[4/4]` to `[1/6]`–`[6/6]`.
- `main()` print statements updated to reflect 6-platform pipeline.
- Script header docstring updated with Xing and Stepstone documentation.

## [2026-08-05]

### Added
- **Startup.jobs platform** — free HTML scraping via `cloudscraper`. 6 category pages (data-engineer, data-analyst, ai-engineer, data-scientist, business-analyst, analytics-engineer). Bypasses Cloudflare challenge.
- `maxTotalChargeUsd` safety cap on Apify actor runs (`$0.60` for LinkedIn, `$0.50` default) to prevent aborted-run cost spikes.
- `is_relevant_title()` universal post-filter — requires at least one data/analytics/AI/SQL/Python keyword in the job title. Applied to all platforms via `check_experience_and_location()`.
- `DOMAIN_TITLE_KEYWORDS` regex constant.
- `fetch_last_run_dataset()` fallback — retrieves the most recent run's dataset if a new run fails to start.

### Changed
- **Cost reduced from ~$1.50–3.00/run to ~$0.55/run** (82% reduction).
- **SEARCH_ROLES consolidated from 24 → 10 core roles.** Search engines cover variants (Junior, Cloud, etc.) automatically. German terms kept where they produce distinct results.
- **LinkedIn `maxItems` bug fixed**: code was sending `maxItems: 30` but the actor uses `count` (default 100). Was scraping 100 results × 24 URLs = ~1070 results × $0.001 = $1.07/run. Fixed to use `count` parameter directly.
- **Indeed `title` parameter fix**: the search keyword was missing from `run_input` — all 10 parallel runs fetched generic Germany garbage ("Haustechniker", "Koch") with no keyword. Added `"title": role`.
- **LinkedIn `count` regression fixed**: temporarily set to `count=15` which returned only 16 jobs total. Restored to `count=500` for adequate coverage.
- Per-date subfolder structure: each run's files grouped into `Job Search/YYYY-MM-DD/`.

### Removed
- **Kununu platform dropped** — `shahidirfan/kununu-jobs-scraper` returns 0 results (broken). All 8 Apify store kununu actors are review scrapers, not job listing scrapers.

### Rejected Alternatives
- `misceres/indeed-scraper` — weak output.
- `borderline/indeed-scraper` — run failed.
- All 8 Apify store kununu actors — review scrapers, not job listings, or broken.

## [2026-08-04]

### Initial Release
- Pipeline fetches from LinkedIn (Apify), Indeed (Apify), Arbeitnow (free API), Kununu (Apify).
- Filters: freshness (< 24h), experience (<= 2 years), working student (Hamburg/Kiel only), internships (Germany-wide).
- Output: CSV (UTF-8 BOM), JSON, Markdown summary, XLSX (autofilter + hyperlinks).
- 24 SEARCH_ROLES.
- Cost: ~$1.50–3.00/run.
