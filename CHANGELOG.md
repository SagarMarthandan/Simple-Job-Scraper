# Changelog

All notable changes to the Jobscraper pipeline are documented here.
Dates are in ISO 8601 format (`YYYY-MM-DD`).

## [2026-08-27]

### Added
- **Cross-platform fuzzy dedup** (`cross_platform_dedup()`) — new dedup tier that catches the same job reposted across different platforms with company name variants (Bosch vs Bosch Gruppe, PENNY vs PENNY International (REWE Group), XSYS Germany GmbH vs XSYS Global). Uses fuzzy matching across *different platforms only*: company token overlap ≥ 0.5 (min-set denominator), title Jaccard similarity ≥ 0.6, location match with city aliases (München/Munich, Köln/Cologne, etc.). Keeps the higher-priority platform's copy (LinkedIn > Xing > Stepstone > Indeed). Runs after within-run dedup, before cross-run dedup. O(n²) but with different-platform-only shortcut and early-exit on company overlap — < 1s for 600 jobs. Confirmed 11 true cross-platform dups in the 2026-08-27 run (443 → ~432 jobs).
- **Job verification post-step** (`verify_jobs.py`) — new standalone script that visits each job URL after CSV export to filter out unsuitable listings before applying. Per-platform verifiers with `ThreadPoolExecutor` parallelism (1 worker per platform, 4 for ATS APIs). Total wall time ~5 min (dominated by Xing ~200 URLs at 1.5s delay).
  - **German C1+ filter (primary eliminator)** — scans job descriptions for hard German language requirements (`fließend Deutsch`, `C1 Niveau`, `Muttersprache Deutsch`, `verhandlungssicher`, `business fluent in German`). Drops rows requiring C1+ German. Soft requirements (`Deutschkenntnisse wünschenswert`, `German is a plus`) are flagged but kept. Expected to eliminate 30–50% of Xing/Stepstone listings. Uses same-sentence contradiction logic: if a hard pattern and soft pattern co-occur in the same sentence, the soft qualifier wins.
  - **Stale/closed check** — 404/410/redirect-to-expired = drop. Catches already-filled positions (estimated 10–20% of Xing jobs).
  - **Experience years extraction** — regex for `X Jahre Berufserfahrung` / `X years experience` / `min. X Jahre` in body text. Catches senior jobs that slipped through the title filter.
  - **Remote/hybrid/onsite detection** — from page text (Remote, Home-Office, hybrid, vor Ort).
  - **Salary extraction** — from body text regex or JSON-LD `baseSalary` field.
  - **LinkedIn/Indeed skipped** — fresh < 24h data, trust it. Anti-bot risk not worth the ROI.
  - **Idempotent** — skips already-verified rows unless `--force`. Network errors leave `verified_active` empty (treat as "unknown, keep").
  - **Output**: `Job_Search_<date>_verified.csv` with 5 new columns (`verified_active`, `detail_language`, `detail_exp_years`, `detail_salary`, `detail_remote`). Drops rows where `verified_active = False` OR `detail_language = "German C1+ required"`.

### Changed
- **`normalize_key()` enhanced** — now splits into `_norm_company()` and `_norm_title()` helpers. Strips parentheticals (`(REWE Group)`, `(m/w/d)`), expanded legal suffixes (`group`, `gruppe`, `holding`, `international`, `deutschland`, `germany`, `global`, `e.g.`), seniority markers (`senior`, `junior`, `lead`, `principal`, `staff`, `sr.`, `jr.`), gender markers (`m/w/d`, `m/f/d`, `all genders`), and reference codes (`REF99139A`). Catches Bosch/Bosch Gruppe, PENNY/PENNY International, Intersport/INTERSPORT Deutschland e.G., Continental with/without REF code — all now produce identical dedup keys. Cross-run dedup auto-benefits (no separate change needed).
- **Pipeline dedup flow**: within-run → cross-platform fuzzy → cross-run (was within-run → cross-run, 2 tiers → 3 tiers).

### Verification
- `normalize_key('Bosch Gruppe', 'Data Engineer (m/w/d)') == normalize_key('Bosch', 'Data Engineer')` → `'bosch::data engineer'` ✓
- Synthetic `cross_platform_dedup()` test: 6 jobs (2 cross-platform dups + 2 non-dups) → removed 2, kept 4. Higher-priority platform kept in each dup pair. Same-company different-title non-dups retained. ✓
- `detect_german_requirement('fließend Deutsch C1')` → `'German C1+ required'` ✓
- `detect_german_requirement('Deutschkenntnisse wünschenswert')` → `'German preferred'` ✓
- `detect_german_requirement('English only')` → `''` ✓

## [2026-08-23]

### Added
- **LinkedIn free HTML scraper** (`fetch_linkedin_jobs_free()`) — replaces paid Apify LinkedIn actor ($0.50/run savings). Scrapes LinkedIn's public jobs search page directly: server-rendered HTML with 60 `div.base-card` elements per search, no auth wall. `f_TPR=r86400` server-side 24h filter + `posted_at` datetime post-filter. No job descriptions (title-only filtering = more permissive, title-based `EXCLUDED_TITLE_PATTERNS` still catches Senior/Lead/Manager). Paid actor kept as fallback (`fetch_linkedin_jobs()` at line 1154) but not called.
- **Multi-city LinkedIn search** — LinkedIn caps results at 60/page with no pagination. Searching "Germany" alone misses jobs beyond the first 60. Each role is now searched across 6 locations (`Germany`, `Berlin`, `Munich`, `Hamburg`, `Frankfurt`, `Cologne`). City-level results have different rankings and overlap only partially with Germany-wide. Verified: 184 unique URLs/role (3.1x coverage vs Germany-only). "Remote" excluded — returns global jobs (6000+), would flood with false positives.
- **ATS Direct scraping** (`ats_scraper.py`) — new file with 3 ATS platform fetchers using public JSON APIs (no auth, no HTML selectors): Greenhouse (`boards-api.greenhouse.io/v1/boards/{slug}/jobs`, `first_published`), SmartRecruiters (`api.smartrecruiters.com/v1/companies/{slug}/postings`, paginated, `releasedDate`), Ashby (`jobs.ashbyhq.com/{slug}` HTML with embedded `window.__appData` JSON, `publishedDate`). Curated 17 German tech companies. `_is_fresh()` returns True when date is None (false positives > false negatives).
- **Parallel pipeline execution** — all 8 platform fetchers run simultaneously via `ThreadPoolExecutor(max_workers=8)`. Runtime: 27s (was 191s sequential, 7x speedup). LinkedIn internally parallelizes 10 roles with `max_workers=5` + 429 retry with 3s backoff. I/O bound work — GIL released during requests.

### Changed
- **Pipeline cost reduced from ~$0.54/run to ~$0.04/run** (93% reduction). Only Indeed still uses Apify ($0.04). LinkedIn, Arbeitnow, Startup.jobs, Xing, Stepstone, Glassdoor, ATS Direct all free.
- **Pipeline steps: 7 → 9** (added LinkedIn free, ATS Direct). Execution is now parallel — all platforms launch simultaneously instead of sequential `[1/9]`–`[9/9]`.
- **Xing freshness fix**: jobs with no `dateTime` attribute (sponsored/promoted listings) were being **skipped** (false negative). Now **included** — only jobs with a confirmed date older than 24h are rejected. Aligned with "false positives > false negatives" rule. Result: Xing went from 4 jobs to 267 (0 senior titles, all entry-level/internship/working-student).

### Freshness Audit (all 9 sources)
| Platform | Server-side filter | Post-filter | No-date behavior |
|---|---|---|---|
| Arbeitnow | — | `created_at` vs 24h | N/A (API always has timestamp) |
| Startup.jobs | — | `timestamp` vs cutoff | Include (FP > FN) |
| Xing | — | `<time dateTime>` vs cutoff | Include ✅ (was skip — fixed) |
| Stepstone | `ag=age_1` (24h) | `parse_stepstone_timeago()` vs cutoff | Include (defaults to now) |
| Glassdoor | — | `ageInDays == 0` from RSC payload | **Skip** (Glassdoor exception — see below) |
| LinkedIn free | `f_TPR=r86400` (24h) | `posted_at` datetime vs cutoff | Include (safety net only) |
| Indeed | `datePosted='1'` (unreliable) | `datePublished` vs cutoff | Include (FP > FN) |
| ATS: Greenhouse | — | `first_published` vs cutoff | Include (via `_is_fresh`) |
| ATS: SmartRecruiters | — | `releasedDate` vs cutoff | Include (via `_is_fresh`) |
| ATS: Ashby | — | `publishedDate` vs cutoff | Include (via `_is_fresh`) |

### Glassdoor Exception
Glassdoor's SSR IGNORES the `fromAge=1` URL parameter and serves unfiltered results (0/30 fresh, 16/30 stale at 15-179 days old in live testing). The `ageInDays == 0` filter is the ONLY barrier against stale jobs. Jobs missing from `age_lookup` are from an unfiltered result set and more likely old than fresh. Unlike other platforms where "no date = include" (false positives > false negatives), Glassdoor **skips** jobs with unverified `ageInDays`. Verified 2026-08-23: including None jobs let 42 unconfirmed jobs through with 0 confirmed fresh — all were `ageInDays=None`. Reverted to skip.

### Rejected Alternatives
- **Common Crawl slug harvester** (`slug.json`) — over-engineered for a daily Germany data/AI scraper. Curated list of 17 German tech companies is sufficient.
- **Lever ATS** — API works (`api.lever.co/v0/postings/{slug}?mode=json`) but all 40+ tested company slugs return 404 or 0 postings.
- **Teamtailor/Workable ATS** — auth-gated, no public API.
- **BambooHR/Recruitee/Breezy ATS** — JS-rendered, no clean API.
- **Parallel task agents** — considered for per-platform scraping, but ThreadPoolExecutor gives same speedup without agent spawning overhead. I/O bound work doesn't need independent reasoning per worker.

## [2026-08-21]

### Added
- **Cross-run deduplication** — consecutive daily runs with 24h freshness windows overlap when run times drift, causing duplicate jobs across sheets (e.g. 76/241 = 31.5% of Aug 21 jobs already appeared in Aug 20). After within-run dedup, `load_previous_run_keys()` loads keys from the **single most recent previous run** (e.g. Friday vs Thursday, or vs Wednesday if Thursday was skipped) and removes jobs already seen:
  - **URL keys**: `normalize_job_url()` strips LinkedIn tracking params (position/pageNum/refId/trackingId) since the job ID is in the path; Indeed `jk=` and Glassdoor `jl=` IDs are preserved. URLs are unique — if the same URL appeared in the previous run, it's the same job.
  - **Title keys**: `normalize_key(company, title)` from the same previous run. Catches LinkedIn re-lists (new URL per run, same job) and cross-platform duplicates (same job on LinkedIn vs Indeed).
- **`normalize_job_url()`** — stable cross-run URL identity. LinkedIn query string dropped (tracking params); other platforms keep their ID-bearing query params.
- Same-day reruns: today's own earlier CSV is the most recent folder, so a second run suppresses everything already exported.
- Console output: `Cross-run dedup: compared against previous run (N jobs), removed X already-seen job(s)`.

## [2026-08-10]

### Critical Fix
- **Stepstone switched from `cloudscraper` to plain `requests`** — Stepstone is behind **Akamai** (not Cloudflare). `cloudscraper`'s challenge-solving mechanism hangs on Akamai, causing `ReadTimeout` on port 443 (read timeout=15s). Plain `requests` with a browser User-Agent works — Stepstone serves SSR HTML without anti-bot challenge. All `data-at` attribute parsing unchanged. 25 job cards/page confirmed parsing correctly.
- **Xing switched from `cloudscraper` to plain `requests`** — Xing is behind **AWS CloudFront** (not Cloudflare). `cloudscraper` worked but was unnecessary overhead with the same Akamai-style hang risk as Stepstone if cloudscraper updates its challenge handling. Plain `requests` with a browser User-Agent works (Xing serves SSR HTML without anti-bot challenge). All `data-testid` attribute parsing unchanged. 11 jobs confirmed parsing correctly.

### Audit Summary (CDN protection)
| Platform | CDN | `requests` | `cloudscraper` | Action |
|---|---|---|---|---|
| Startup.jobs | Cloudflare | 403 | 200 ✅ | Keep cloudscraper |
| Xing | AWS CloudFront | 200 ✅ | 200 ✅ | Switch to requests |
| Stepstone | Akamai | 200 ✅ | ❌ TIMEOUT | Switch to requests |
| Glassdoor | Cloudflare | 403 | 200 (chrome) ✅ | Keep cloudscraper |

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
