## [2026-08-30b]

### Removed
- **Glassdoor scraper** (~200 lines) — `fetch_glassdoor_jobs()`, `_glassdoor_fetch_with_retry()`, `_extract_age_lookup()`, `_parse_glassdoor_item()`, `GLASSDOOR_MAX_RETRIES`. 1.1 jobs/run average across 10 runs. Most complex scraper in the pipeline (RSC payload parsing, JSON-LD, KE offset company extraction, 8x Cloudflare retry) for negligible yield.
- **Startup.jobs scraper** (~90 lines) — `fetch_startupjobs_jobs()`, `_parse_startupjobs_page()`, `STARTUPJOBS_PAGES`. 1.1 jobs/run average. Same yield as Glassdoor.
- **Cross-platform fuzzy dedup** (~83 lines) — `cross_platform_dedup()`, `_company_tokens()`, `_title_similarity()`, `_location_match()`. Removed 0 jobs in recent runs — URL dedup already handles cross-platform duplicates.
- **Reposted detection** (~160 lines in verify_jobs.py) — `detect_reposted()`, `_load_repost_data()`, `_find_previous_run_dirs()`, `_load_linkedin_title_keys_from_csv()`, `_extract_linkedin_job_id()`, `_normalize_url()`, `_load_urls_from_csv()`, `LINKEDIN_DAILY_ID_GROWTH`, `REPOST_CROSS_RUN_DAYS`, `REPOST_JOB_ID_AGE_DAYS`. Second dedup system overlapping with step 1's cross-run URL dedup. Reposted sheet removed — nobody acted on it.
- **Germany geo-filter** (~40 lines) — `NON_GERMANY_COUNTRIES` (30-country tuple), `NON_GERMANY_CITIES` regex. Workaround for LinkedIn AI search bug that no longer applies (free HTML scraper uses explicit URL params).
- **Experience regex patterns** (~6 lines) — `EXCLUDED_EXP_PATTERNS` (4 regexes). LLM classification in verify step 2 is the authoritative filter.
- **Total: 676 lines removed** (3657 → 2981).

### Fixed
- **Cross-run dedup bug** — `load_previous_run_keys()` (50 lines, scanned 3 days, picked today's own folder as 'previous run') replaced with `load_previous_run_urls()` (14 lines, reads yesterday's CSV, returns URL set). Bug: dedup compared today's scrape against today's morning folder (112 jobs) instead of yesterday (374 jobs), letting 252/295 duplicates through (85%). Fix: simple — load yesterday's URLs, drop matches.

### Changed
- **Pipeline: 8 → 6 platforms** — LinkedIn, Indeed, Arbeitnow, Xing, Stepstone, ATS Direct (Greenhouse/SmartRecruiters/Ashby).
- **Dedup: 3 tiers → 2 tiers** — within-run (company::title) + cross-run (URL vs yesterday). Cross-platform fuzzy dedup removed.
- **Verify output: 2-sheet → 1-sheet** — Reposted sheet removed. `save_xlsx()` writes single 'Job Search' sheet.
- **`check_experience_and_location()` simplified** — removed exp regex loop + Germany geo-filter. Keeps: title relevance, seniority ceiling, working-student city restriction.
- **Cost: ~$0.55 → ~$0.04/run** — LinkedIn now free HTML scraping (was Apify paid). Only Indeed uses Apify.

### Verification
- Pipeline: 373 raw → 222 deduped vs yesterday → 43 new jobs, 0 duplicates with yesterday
- 6 platforms, 52s runtime


## [2026-08-30]

### Changed
- **Architecture refactor: kill `_jd_text`** — removed the hidden transport field that flowed through verifier result dicts → `row.update()` → `llm_classify_all()`. LLM classification now reads `row["description"]` directly. Eliminates the silent data loss bug where 229/238 jobs had empty `detail_language` because `_jd_text` wasn't set when verifiers hit exception paths.
- **`_process_result` simplified** — only extracts salary + remote + match score. No longer sets `_jd_text` for downstream classification. German/exp classification fully decoupled from verification stage.
- **LLM batch size 5→10** — Gemini 3.1 Flash Lite handles 10 JDs per call (~4000 tokens) within context window. Halves LLM calls: 37 batches instead of 74 for ~370 jobs.
- **Description acquisition stats** — prints `X/Y jobs acquired descriptions` with per-platform breakdown of missing jobs after TinyFish stage. Makes data gaps visible before classification runs.
- **Classification coverage stats** — prints `X/Y jobs classified (Z skipped — no description)` in `llm_classify_all()`. Makes LLM coverage visible in run output.
- **TinyFish disk cache** — `tinyfish_cache.json` saved after every batch (survives interrupts). Re-runs load cache and skip already-fetched URLs (no wallet re-spend).
- **SKILL.md updated** — documents two-step execution (scrape via bash, verify via eval). Explains why verify must run in OMP eval sandbox (TinyFish + LLM injection).

### Removed
- **Legacy regex German detection** — `detect_german_requirement()`, `GERMAN_REQUIRED_PATTERNS` (29 patterns), `GERMAN_SOFT_PATTERNS` (10 patterns), `_REQUIRED_RE`, `_SOFT_RE`, `_split_sentences()`. 95 lines. Replaced by LLM classification in [2026-08-28b], regex was fallback-only.
- **Legacy regex exp extraction** — `extract_exp_years()`, `_EXP_PATTERNS` (5 patterns). 30 lines. Same — LLM replaces this.
- **Regex fallback wrappers** — `_regex_german_level()`, `_regex_exp_years()`. 19 lines. `llm_classify_batch()` now returns `{"german": "none", "exp_years": None}` defaults on LLM failure instead of falling back to inaccurate regex.
- **Regex fallback paths in `llm_classify_batch()` and `_parse_llm_response()`** — all `if "completion" not in globals()` regex branches removed. LLM-only classification, no silent degraded mode.
- **Total: 166 lines removed** (1714→1548). No regex fallback means no inaccurate silent degradation — if LLM is unavailable, defaults are explicit and visible in output.

### Fixed
- **Misplaced shebang/imports** — `from collections import Counter` and `from datetime import datetime` were before `#!/usr/bin/env python3` and the module docstring. Reordered: shebang → docstring → imports.

### Verification
- 374 input → 258 kept, 75 reposted, 3 closed, 25 C1+ dropped, 13 exp dropped, 216 enriched (with mock LLM; real LLM expected to classify more accurately)
- Description acquisition: 372/374 (99.5%) — 2 Xing URLs missing from cache
- LLM classification coverage: 369/374 (98.7%) — 5 skipped (no description)

# Changelog

All notable changes to the Jobscraper pipeline are documented here.
Dates are in ISO 8601 format (`YYYY-MM-DD`).

## [2026-08-28d]

### Changed
- **TinyFish universal pre-fetch** — extended TinyFish JD pre-fetch from LinkedIn-only to all JD-dependent platforms: LinkedIn, Indeed, Xing, Stepstone, Startup.jobs, Glassdoor. ATS platforms (Greenhouse/SmartRecruiters/Ashby) and Arbeitnow skipped (public APIs, 100% accuracy). Each platform verifier (`verify_xing`, `verify_stepstone`, `verify_startupjobs`, `verify_glassdoor`) now checks for pre-fetched `description` (>50 chars) before making network requests, same pattern as `verify_linkedin`. Falls back to native method (requests/cloudscraper) when no pre-fetched description. Indeed already checked `description` — now TinyFish fills gaps when Apify returns empty. Runtime: ~500 URLs → 250 batches × ~8s = ~33 min. No cost (TinyFish `fetch_content` is free).


## [2026-08-28c]

### Changed
- **TinyFish LinkedIn JD pre-fetch** — LinkedIn's free scraper returns `description: ""` for all jobs, and `verify_jobs.py`'s `requests.get()` fallback gets rate-limited (no JSON-LD, 76K chars of HTML boilerplate). TinyFish MCP tool (`fetch_content`) renders LinkedIn pages in a real browser and extracts clean JD text. Pre-fetches all LinkedIn JDs in the **main thread** before platform verification (TinyFish `tool.*` is not thread-safe — `RuntimeError: Missing session/run/name` from `ThreadPoolExecutor` worker threads). JDs injected into `row["description"]`, same pattern as Indeed JSON injection. Batch size 2 (response truncates at ~25K chars with larger batches). 187 LinkedIn URLs → 94 batches × ~8s = ~12 min. Result: 299 jobs classified by LLM (was 113), 68 C1+ dropped (was 39), 34 exp dropped (was 17).
- **LLM plain-text output format** — replaced JSON schema with plain-text `N|level|years` per-line format. JSON schema caused `{'value': '[...]'}` response shape mismatches across models. Parser: `_LLM_LINE_RE = re.compile(r'^(\d+)\s*\|\s*(C1\+|B1/B2|preferred|none)\s*\|\s*(\d*)\s*$', re.IGNORECASE)`. Partial results fill gaps with regex fallback.
- **Reposted detection: job ID override** — if LinkedIn job ID is fresh (<14 days old), cross-run title match (signal 1) is suppressed. A fresh ID means it's a new posting, not a repost, even if the same `company::title` appeared in an old run. Fixes false positives like Code Compass ML Engineer (3.7-day-old job ID, same title as old run → was wrongly segregated to Reposted sheet).
- **`verify_linkedin` uses pre-fetched description** — checks `job.get("description")` (>50 chars) before making network requests. If TinyFish already injected the JD, calls `_process_result` directly and returns. Falls back to `requests.get()` + JSON-LD only when no pre-fetched description is available.
- **Auth-wall detection** — if TinyFish returns only LinkedIn boilerplate (Similar jobs, People also viewed, Referrals increase) without real JD markers (requirements, responsibilities, Aufgaben, Profil, etc.), the job is flagged with `detail_language = "AUTH WALL — review manually"` and `verified_active` is left empty for manual review. ~21/187 LinkedIn jobs affected (LinkedIn auth wall, not a code bug).

### Removed
- **`verify_linkedin_tinyfish()`** — dead code. Was a batch function called from `verify_platform_batch` that invoked `tinyfish_fetch` from worker threads (failed with `RuntimeError`). Replaced by the main-thread pre-fetch in `run_verification()`.
- **TinyFish dispatch in `verify_platform_batch`** — the `elif platform == "LinkedIn" and "tinyfish_fetch" in globals()` branch that routed to `verify_linkedin_tinyfish`. LinkedIn now falls through to the normal `PARALLEL_PLATFORMS` path with `verify_linkedin`.

### Verification
- 301 input → 105 kept, 93 reposted, 1 closed, 68 C1+ dropped, 34 exp dropped, 79 enriched (was: 151 kept, 39 C1+ dropped, 17 exp dropped, 48 enriched with regex-only)
- Hypoport SE Data Engineer correctly dropped (C1+ German, 3+ years exp) — was in main sheet with empty signals before fix
- Code Compass ML Engineer stays in main sheet (3.7-day-old job ID, fresh posting)
- 187/187 LinkedIn JDs injected via TinyFish (batch size 2, 94 batches)
- 299/301 jobs classified by LLM (60 batches of 5, plain-text format)

## [2026-08-28b]

### Changed
- **LLM-based German + experience classification** — replaced regex-based `detect_german_requirement()` and `extract_exp_years()` with LLM batch classification. Regex had endless gaps: "sehr gut Deutsch" (no -e ending), "mind. auf Level C1" (abbreviation + "auf Level"), "fließend in Wort und Schrift", "mehrjährige Erfahrung", etc. Each fix uncovered another variant. LLM approach: `llm_classify_batch()` sends 5 JDs per call to smol model with JSON schema output. Classifies German as C1+/B1-B2/preferred/none + extracts minimum exp years. Handles all phrasing variants, suspended compounds, abbreviations. Falls back to regex with one-time warning when `completion()` not available (standalone mode). Run inside eval sandbox: `import verify_jobs; verify_jobs.completion = completion; verify_jobs.run_verification(...)`. Results: 71 German C1+ drops (was 38 with regex), 42 exp drops (was 23). 383 jobs classified in 77 batches.

## [2026-08-28]

### Changed
- **`verify_jobs.py` v2 rewrite** — full rewrite of the job verification post-step. Now verifies **every** platform (including LinkedIn + Indeed, previously skipped). 109 jobs → 69 kept + 23 reposted + 13 German-dropped + 4 exp-dropped + 0 closed. Runtime: 66s (was ~5 min, v1 skipped 44% of jobs).
  - **LinkedIn via plain requests + JSON-LD** — job detail pages serve full JD in `<script type="application/ld+json">` to unauthenticated requests. No auth wall, no browser relay, no Apify actor. 2 workers, 1s delay, retry once with 3s backoff. Verified 2026-08-27: 10/10 sample URLs returned JSON-LD with full description.
  - **Indeed verification** (new) — plain requests, JD extracted from page HTML/JSON-LD. Was skipped in v1.
  - **German filter expanded to max B2** — v1 only dropped C1+. v2 drops C1/C2/fließend/Muttersprache/verhandlungssicher/sehr gute Deutschkenntnisse/business fluent. B1/B2 explicit → keep (`German B1/B2 OK`). Soft (`wünschenswert`, `von Vorteil`, `nice to have`) → keep + flag (`German preferred`). Same-sentence contradiction logic preserved.
  - **Experience ≥3 years → hard drop** — v1 had experience as enrichment-only. v2 drops rows where JD body text requires ≥3 years (`X Jahre Berufserfahrung`, `X years experience`, `min. X Jahre`, `at least X years`). Returns minimum years found (handles "1-3 Jahre" → 1).
  - **Reposted LinkedIn detection** — two signals: (1) cross-run history scan — `normalize_key(company,title)` match in any previous run >7 days ago, (2) job ID age gap — LinkedIn creates ~530K IDs/day globally; if today's max ID minus job ID suggests >14 days old, flag as reposted. Either signal → segregated to "Reposted" sheet (NOT dropped — user reviews manually). `datePosted` is reset on repost, so it can't be used for age detection.
  - **Match score recalculated from JD text** — v1 kept the pipeline's title-based score. v2 runs `compute_match_score_from_jd()` on the full JD text using `TECH_KEYWORDS` (dbt, airflow, spark, pyspark, python, sql, gcp, bigquery, aws, azure, databricks, docker, kafka, postgresql, snowflake). Same density formula as pipeline but on actual description text.
  - **Output: 2-sheet XLSX** (was CSV) — "Job Search" sheet (69 rows, passed all filters) + "Reposted" sheet (23 rows, LinkedIn reposts for manual review). Frozen header, autofilter, clickable URL hyperlinks, numeric match_score. Uses openpyxl (same formatting as pipeline's `convert_csv_to_xlsx`).
  - **New output columns**: `detail_reposted` (True/False/empty), `match_score` now recalculated from JD text. Total 16 columns (was 15).
  - **Drop logic**: `verified_active == "False"` OR `detail_language == "German C1+ required"` OR `detail_exp_years >= 3` → dropped. `detail_reposted == "True"` → segregated to Reposted sheet (not dropped).

### Verification
- `detect_german_requirement('fliessend Deutsch C1')` → `'German C1+ required'` ✓ (ASCII double-s)
- `detect_german_requirement('fließend Deutsch')` → `'German C1+ required'` ✓ (Unicode ß)
- `detect_german_requirement('Muttersprache Deutsch')` → `'German C1+ required'` ✓
- `detect_german_requirement('verhandlungssicher')` → `'German C1+ required'` ✓
- `detect_german_requirement('Deutsch B2')` → `'German B1/B2 OK'` ✓
- `detect_german_requirement('Deutschkenntnisse wünschenswert')` → `'German preferred'` ✓
- `detect_german_requirement('English only')` → `''` ✓
- `extract_exp_years('5 Jahre Berufserfahrung')` → `'5'` ✓
- `extract_exp_years('min. 2 Jahre')` → `'2'` ✓
- `compute_match_score_from_jd('Python SQL dbt airflow spark AWS')` → `100` ✓
- XLSX output: 2 sheets (Job Search: 69 rows, Reposted: 23 rows), 16 columns, numeric scores, hyperlinks, autofilter ✓


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
