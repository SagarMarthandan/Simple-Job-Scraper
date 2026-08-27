# Jobscraper: Full Pipeline + JD Verification Plan (v2)

> **Status:** Ready for implementation. Workstream 1 already shipped; Workstream 2 is a full rewrite.
> **Date:** 2026-08-27 (revised)
> **Budget:** ~25–30 min total (scrape + dedup + verify every job URL + enrich + export)
> **Context:** v1 skipped LinkedIn verification (185 jobs = 44% of output). A job requiring "Deutsch auf muttersprachlichem Level (mind. C1)" sailed through unfiltered. v2 verifies **every** job — including LinkedIn via plain requests + JSON-LD (no auth needed) — and applies three hard drop filters (German level > B2, experience ≥ 3 years, closed/inactive) plus one segregation filter (reposted LinkedIn jobs → separate sheet). Match score is recalculated from actual JD text.

---

## Workstream 1: Cross-Platform Fuzzy Dedup — SHIPPED

**Status:** Implemented and committed (`f286c12`, 2026-08-27).

Three-tier dedup in `apify_job_search.py`:
1. **Within-run** — `normalize_key()` with enhanced `_norm_company`/`_norm_title` (strips parentheticals, expanded legal suffixes, seniority/gender markers, REF codes)
2. **Cross-platform** — `cross_platform_dedup()` fuzzy matching across different platforms (company token overlap ≥ 0.5, title Jaccard ≥ 0.6, location match). Keeps higher-priority platform (LinkedIn > Xing > Stepstone > Indeed)
3. **Cross-run** — `load_previous_run_keys()` compares against immediate previous run (URL keys + title keys)

No further work needed. This plan focuses on Workstream 2.

---

## Workstream 2: Full JD Verification (`verify_jobs.py` v2)

### What Changed from v1

| Aspect | v1 (shipped) | v2 (this plan) |
| LinkedIn | **Skipped** (185 jobs, 44% unfiltered) | **Verified via plain requests + JSON-LD** (no auth needed, 2 workers, 1s delay) |
| Indeed | Skipped (trusted as fresh) | **Verified** — JD text fetched from URL, all filters applied |
| German filter | C1+ required → DROP | **Max B2** — C1/C2/fließend/Muttersprache/verhandlungssicher → DROP. B2 or lower → KEEP |
| Experience filter | Not a drop criterion (enrichment only) | **≥ 3 years → DROP** (hard filter, not just enrichment) |
| Match score | Not recalculated (pipeline title-based score kept) | **Recalculated from actual JD text** (tech keyword density in full description) |
| Reposted jobs | Not detected | **Detected via cross-run history + job ID age gap** → segregated to "Reposted" sheet |
| Architecture | Single script, ThreadPoolExecutor per platform | **3 parallel subagents** (FastVerifiers, GermanBoards, LinkedInVerifier) |
| Runtime | ~10 min (Xing-dominated, LinkedIn skipped) | **~8 min** (Xing-dominated, LinkedIn ~3 min via plain requests) |

### Goal

After the pipeline exports the deduped CSV, visit **every** job URL to:
1. Check if the listing is still active (not closed/filled)
2. Extract the full job description text
3. Apply three hard drop filters — remove rows that fail any:
   - **German level > B2** (C1/C2/fließend/Muttersprache/verhandlungssicher/sehr gute)
   - **Experience ≥ 3 years** (from JD body text, not just title)
   - **Closed/inactive** (404/410/redirect-to-expired)
4. Apply one segregation filter — move to "Reposted" sheet (not dropped):
   - **Reposted LinkedIn jobs** (appeared in previous runs >7 days ago, or job ID age >14 days)
5. Recalculate match score from actual JD text
6. Enrich with: detail_language, detail_exp_years, detail_salary, detail_remote

### Architecture: 3 Parallel Subagents

```
apify_job_search.py  (main agent, ~30s)
  ├── fetch 8 platforms in parallel
  ├── 3-tier dedup (within-run → cross-platform → cross-run)
  └── export Job_Search_<date>.csv  (~400–450 jobs)
          ↓
verify_jobs.py  (3 subagents in parallel, ~8 min max)
  ├── Subagent 1: FastVerifiers      (~26 URLs, ~2 min)
  │   ├── ATS (Greenhouse/SmartRecruiters/Ashby) — public JSON API, 4 workers, 0.5s
  │   ├── Arbeitnow — free API
  │   ├── Startup.jobs — cloudscraper, 2s
  │   ├── Glassdoor — cloudscraper, 2s
  │   └── Indeed — plain requests, 2s
  ├── Subagent 2: GermanBoards       (~232 URLs, ~6 min)
  │   ├── Xing — plain requests, 1.5s delay, 1 worker
  │   └── Stepstone — plain requests, 2s delay, 1 worker
  └── Subagent 3: LinkedInVerifier   (~185 URLs, ~3 min)
      └── LinkedIn — plain requests + JSON-LD, 2 workers, 1s delay (no auth needed)
          ↓
Merge + Export  (main agent, ~30s)
  ├── Collect results from all 3 subagents
  ├── Drop: closed + German > B2 + exp ≥ 3 years
  ├── Segregate: reposted LinkedIn jobs → "Reposted" sheet
  ├── Recalculate match_score from JD text
  └── Write Job_Search_<date>_verified.xlsx (2 sheets)
```

**Total wall time:** ~8 min (scrape 30s + verify 6 min + merge 30s). Well within 25–30 min budget.

### Per-Subagent Details

#### Subagent 1: FastVerifiers (~26 URLs, ~2 min)

| Platform | URLs | Method | Delay | Workers |
|---|---|---|---|---|
| Greenhouse | ~2 | `boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}` | 0.5s | 4 |
| SmartRecruiters | ~1 | `api.smartrecruiters.com/v1/companies/{slug}/jobs/{id}` | 0.5s | 4 |
| Ashby | ~4 | `api.ashbyhq.com/posting-api/job-board/{slug}` | 0.5s | 4 |
| Arbeitnow | ~4 | Free API endpoint | — | 1 |
| Startup.jobs | ~2 | `cloudscraper` (Cloudflare) | 2s | 1 |
| Glassdoor | ~2 | `cloudscraper` (Cloudflare) | 2s | 1 |
| Indeed | ~14 | Plain `requests` (public job page) | 2s | 1 |

Per URL:
1. Fetch page/API → extract JD text
2. Check active (200 + content vs 404/empty)
3. Run German filter, experience filter, match score on JD text
4. Return enriched row

#### Subagent 2: GermanBoards (~232 URLs, ~6 min)

| Platform | URLs | Method | Delay | Workers |
|---|---|---|---|---|
| Xing | ~199 | Plain `requests` (CloudFront, no anti-bot) | 1.5s | 1 |
| Stepstone | ~33 | Plain `requests` (Akamai, no anti-bot) | 2s | 1 |

Per URL:
1. `requests.get(url, timeout=15)` with browser User-Agent
2. Live check: HTTP 200 + JSON-LD `JobPosting` or title present. 404 = closed.
3. Extract JD text: JSON-LD `description` field first, fall back to stripping HTML tags
4. Run all filters + enrichment on JD text
5. Return enriched row

#### Subagent 3: LinkedInVerifier (~185 URLs, ~3 min)

**Method:** Plain `requests` — LinkedIn job detail pages serve full JD via JSON-LD `<script type="application/ld+json">` without authentication. Verified 2026-08-27: 10/10 sample URLs returned JSON-LD with `description` field (3.9K–8.3K chars). Bulk test at 5 workers got 132/185 (71.4%) — the 53 failures were rate-limiting, not auth walls (re-tested individually: all returned JSON-LD). At 2 workers with 1s delay, expect ~95%+ success.

Per URL:
1. `requests.get(url, timeout=15)` with browser User-Agent
2. Check active: HTTP 200 + JSON-LD present. 404/redirect = closed.
3. Extract JD text: JSON-LD `description` field (HTML-entity-decoded)
4. **Check reposted**: cross-run history scan (all previous runs, company::title match >7 days ago) + job ID age gap (>14 days by ID estimation). See Filter 4 below. This check uses only the CSV data (URL + company + title) — no page fetch needed.
5. Run German filter, experience filter, match score on JD text
6. Extract salary, remote from JD text
7. 1s delay between requests (2 workers, polite but fast)

**Why no browser relay (changed from earlier plan):**
- LinkedIn job detail pages are public SSR — JSON-LD with full description is served to unauthenticated requests
- No auth wall, no Cloudflare challenge on detail pages (unlike search pages which can rate-limit)
- Browser relay was planned for auth-walled content, but verification proved it's unnecessary
- Eliminates anti-bot risk entirely (no authenticated session to flag)
- 6x faster: ~3 min at 2 workers vs ~15 min sequential at 5s delays

**Rate-limiting mitigation:**
- 2 workers (not 5 — bulk test showed 5 workers causes ~29% rate-limit failures)
- 1s delay between requests per worker
- Retry once with 3s backoff on non-200 or missing JSON-LD
- If still no JSON-LD after retry: leave `verified_active=""` (unknown, keep), `detail_language=""` — can't filter without JD text

**Fallback if plain requests fails at scale:**
- Option A: Drop to 1 worker with 2s delay (more conservative)
- Option B: Keep unverified LinkedIn jobs with empty fields — still deliver clean output for other ~260 jobs
- Option C: Use Apify LinkedIn actor (paid, $0.50) as last resort — fetches JD text for all URLs in one API call

### Hard Filters (Drop Row)

#### Filter 1: German Language Level > B2

Sagar has max B2 German. Any job requiring above B2 is a guaranteed waste of time.

**DROP if any of these match** (German C1+ / native / fluent required):

```python
GERMAN_REQUIRED_PATTERNS = [
    # Explicit CEFR level C1/C2
    r'(?:mindestens|min\.|ab)\s*C[12]\s*(?:Deutsch|German|in\s+Deutsch)',
    r'C[12]\s*[-/]?\s*(?:Deutsch|German)',
    r'(?:Deutsch|German)\s*C[12]',
    r'(?:Deutsch|German)\s+(?:auf\s+)?C[12](?:\s*[-–]?\s*Niveau|\s+level)?',
    # "Fließend" / "fluent" — implies C1+ regardless of explicit level
    r'flie[ßss]end\s*(?:Deutsch|German|in\s+Deutsch|Deutschkenntnisse)',
    r'(?:Deutsch|German)\s+flie[ßss]end',
    # "Muttersprache" / native speaker
    r'Muttersprache\s+Deutsch',
    r'(?:Deutsch|German)\s+as\s+a\s+(?:first\s+)?native\s+language',
    # "Sehr gute Deutschkenntnisse" — usually means C1+
    r'sehr\s+gute\s+Deutsch(?:kenntnisse|sprachkenntnisse)',
    # "Verhandlungssicher" — business-fluent, C1+
    r'(?:Deutsch|German)\s+verhandlungssicher',
    r'verhandlungssicher\s+in\s+Deutsch',
    # "Business fluent in German" (English postings)
    r'business\s+fluent\s+(?:in\s+)?German',
    r'fluent\s+German\s+(?:required|mandatory|must)',
    # "Muttersprachlich" (adjective form)
    r'muttersprachlich(?:e|er|es|em)?\s*(?:Deutsch|German)',
    # "Deutsch auf muttersprachlichem Niveau"
    r'(?:Deutsch|German)\s+.*muttersprachlich',
    # "Fachfließend" / "verhandlungssicher" variants
    r'fachflie[ßss]end\s*(?:Deutsch|German)',
]
```

**KEEP + flag if only these match** (German helpful but not required):

```python
GERMAN_SOFT_PATTERNS = [
    r'Deutsch(?:kenntnisse)?\s+(?:w[üu]nschenswert|von\s+Vorteil|idealerweise)',
    r'German\s+(?:is\s+a\s+plus|preferred|nice\s+to\s+have|a\s+bonus)',
    r'idealerweise\s+Deutsch',
    # B1/B2 explicit — these are OK, Sagar has B2
    r'B[12]\s*[-/]?\s*(?:Deutsch|German)',
    r'(?:Deutsch|German)\s+B[12]',
    # "Grundkenntnisse" / "basic German" — fine
    r'(?:Grund|Basis)kenntnisse\s+(?:Deutsch|German)',
    r'basic\s+German',
]
```

**Decision logic:**
- Any `GERMAN_REQUIRED_PATTERNS` matches AND no `GERMAN_SOFT_PATTERNS` in same sentence → `detail_language = "German C1+ required"` → **DROP**
- Only `GERMAN_SOFT_PATTERNS` → `detail_language = "German preferred"` → KEEP + flag
- B1/B2 explicit → `detail_language = "German B1/B2 OK"` → KEEP
- Neither → `detail_language = ""` → KEEP

**Where to search:** Job description body text only. NOT company name, navigation, or footer. For JSON-LD: the `description` field. For HTML: the main content area (`.description__text` on LinkedIn, JSON-LD on Xing/Stepstone, `description` field in ATS API JSON).

#### Filter 2: Experience ≥ 3 Years

```python
EXP_PATTERNS = [
    r'(\d+)\s*(?:\+\s*)?Jahre?\s*(?:Berufs)?[eE]rfahrung',
    r'(\d+)\s*\+?\s*years?\s*(?:of\s*)?(?:professional\s*)?experience',
    r'min\.?\s*(\d+)\s*Jahre',
    r'(?:at\s+least|minimum)\s*(\d+)\s*years?',
    r'(\d+)\s*Jahre\s*(?:berufliche|praktische)\s*(?:Erfahrung|Kenntnisse)',
]
```

If extracted years ≥ 3 → `detail_exp_years = N` → **DROP**.
If < 3 → `detail_exp_years = N` → KEEP.
If no match → `detail_exp_years = ""` → KEEP (assume entry-level, title filter already caught most senior titles).

#### Filter 3: Active/Closed

- HTTP 404/410 → **DROP**
- Redirect to expired/no-longer-accepting page → **DROP**
- LinkedIn: "No longer accepting applications" banner → **DROP**
- HTTP 200 + job content present → KEEP
- Network error/timeout → `verified_active = ""` (unknown, KEEP — don't penalize for network issues)

#### Filter 4: Reposted Jobs (LinkedIn only) — Segregate, Not Drop

LinkedIn allows employers to repost expired listings as fresh advertisements. A reposted job typically means: the first round got no qualified applicants, the selected candidate declined/dropped out, or the listing hit its 21–30 day time limit and was refreshed. These are **stale opportunities** dressed up as fresh — the role has been open for weeks without being filled, which signals either an unattractive position, unrealistic requirements, or a disorganized hiring process.

**Action: Move to a separate "Reposted" sheet in the Excel workbook.** Not dropped — the user reviews them manually. Better safe than sorry; some reposted jobs may still be worth applying to (e.g. evergreen roles at good companies). Segregating them keeps the main sheet clean while preserving the option to apply.

**Why LinkedIn detail pages can't detect reposts (verified 2026-08-27):**
- LinkedIn resets `datePosted` in JSON-LD on repost — all reposted jobs show today's date
- No "Reposted" badge, text, or metadata appears on the detail page HTML
- No "Originally posted" or "Erneut veröffentlicht" text anywhere on the page
- The search results page also shows no repost indicator
- LinkedIn job IDs are sequential globally (~530K new IDs/day). A job with ID 4078616301 appearing alongside ID 4459855154 in the same day's scrape is ~72 days old by ID gap — but `datePosted` says today. The ID gap is the smoking gun.

**Detection: Two signals, either triggers repost flag**

**Signal 1: Cross-run history by company::title (primary)**

Scan ALL previous run folders (not just the most recent one — the existing cross-run dedup only checks the immediate previous run). Build a map of `normalize_key(company, title) → list of dates appeared`. If today's job matches a key that appeared in a run **more than 7 days ago**, flag as reposted.

```python
def detect_reposted_cross_run(company: str, title: str, today: str, job_search_dir: Path) -> bool:
    """Check if this company::title appeared in a previous run older than 7 days."""
    key = normalize_key(company, title)
    cutoff = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=7)
    for run_dir in sorted(job_search_dir.iterdir()):
        if not run_dir.is_dir() or run_dir.name == today:
            continue
        run_date = run_dir.name  # YYYY-MM-DD
        if datetime.strptime(run_date, "%Y-%m-%d") < cutoff:
            # This run is older than 7 days — check for title match
            csvs = list(run_dir.glob("Job_Search_*.csv"))
            if not csvs:
                continue
            with open(csvs[0], newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if row.get("job_board") != "LinkedIn":
                        continue
                    if normalize_key(row["company"], row["title"]) == key:
                        return True
    return False
```

**Verified data (2026-08-27 run):** 55 of 185 LinkedIn jobs (30%) appeared in previous runs dating back to Aug 5 (22 days ago). Examples:
- `adesso::business analyst workflowmanagementsystem` — appeared in 5 runs: Aug 5, 6, 7, 8, 27 (reposted after 19-day gap)
- `chefs culinar::business intelligence specialist` — appeared in 7 runs: Aug 13, 14, 16, 18, 22, 25, 27 (continuously reposted)
- `adesso::ai engineer software engineer` — appeared in 4 runs: Aug 6, 7, 8, 27

**Signal 2: Job ID age gap (secondary, catches reposts with no prior run history)**

LinkedIn job IDs are sequential globally (~530K new IDs/day). Today's newest ID minus a job's ID, divided by 530K, estimates the job's true age in days. If a job's ID suggests it's >14 days old but `datePosted` says today, it was reposted.

```python
def estimate_job_age_days(job_id: int, today_max_id: int) -> float:
    """Estimate true age of a LinkedIn job from its ID gap."""
    DAILY_ID_GROWTH = 530000  # global, avg from Aug 23-27 data
    return (today_max_id - job_id) / DAILY_ID_GROWTH
```

Threshold: `age_days > 14` → flag as reposted. This catches jobs that weren't in previous pipeline runs (e.g. jobs from platforms/locations not previously scraped) but have old IDs indicating they were originally created weeks ago.

**Decision logic:**
- Signal 1 OR Signal 2 triggers → `detail_reposted = True` → **move to "Reposted" sheet**
- Neither triggers → `detail_reposted = False` → keep in main sheet
- Non-LinkedIn jobs → `detail_reposted = ""` (not checked)

**Scope:** LinkedIn only. Xing/Stepstone/ATS don't have a repost mechanism — if a job is closed and relisted, it gets a new URL (caught by cross-run dedup) or shows as a fresh posting with a genuine new date.

**Cross-run dedup relationship:** The existing `load_previous_run_keys()` only checks the most recent previous run. This filter scans ALL historical runs, catching reposts that survived cross-run dedup because the gap was >1 run. The two mechanisms are complementary: cross-run dedup removes same-day and consecutive-run dups; this filter catches long-gap reposts and segregates them.

### Match Score Recalculation

The pipeline's `compute_match_score()` runs on title + search-result snippet only. After fetching the full JD, recalculate from the complete description text:

```python
def compute_match_score_from_jd(jd_text: str) -> int:
    """Recalculate match score from full job description text."""
    text_lower = jd_text.lower()
    matches = sum(1 for kw in TECH_KEYWORDS if kw in text_lower)
    return min(100, int((matches / len(TECH_KEYWORDS)) * 100 * 2.5))
```

`TECH_KEYWORDS` is the existing list in `apify_job_search.py`: `dbt, airflow, spark, pyspark, python, sql, gcp, bigquery, aws, azure, databricks, docker, kafka, postgresql, snowflake`.

This replaces the `match_score` column in the output. Jobs with rich JDs mentioning many stack keywords score higher than the title-only score.

### Enrichment (Non-Drop Signals)

| Signal | Extraction | Column |
|---|---|---|
| Salary | Regex on JD text or JSON-LD `baseSalary` | `detail_salary` (e.g. "60000-85000 EUR/year") |
| Remote/hybrid/onsite | Keyword match in JD: Remote, Home-Office, hybrid, vor Ort | `detail_remote` |

### Output

`Job_Search_<date>_verified.xlsx` — **two sheets**:

**Sheet 1: "Job Search"** (main — applicable jobs)

Same columns as input plus:

| Column | Values |
|---|---|
| `verified_active` | `True` / `False` / empty (unknown) |
| `detail_language` | `German C1+ required` (dropped) / `German preferred` (flagged) / `German B1/B2 OK` / empty |
| `detail_exp_years` | Integer or empty |
| `detail_reposted` | `False` / empty (not checked — non-LinkedIn) |
| `detail_salary` | e.g. `60000-85000 EUR/year` or empty |
| `detail_remote` | `remote` / `hybrid` / `onsite` / empty |
| `match_score` | **Recalculated** from JD text (replaces pipeline's title-only score) |

**Sheet 2: "Reposted"** (segregated — user reviews manually)

Same columns as Sheet 1, containing only jobs where `detail_reposted = True`. These are LinkedIn jobs that appeared in previous runs >7 days ago or have job IDs indicating they're >14 days old. Not dropped — preserved for manual review.

**Rows dropped (removed entirely) if:** `verified_active = False` OR `detail_language = "German C1+ required"` OR `detail_exp_years ≥ 3`.

**Rows moved to Reposted sheet if:** `detail_reposted = True` (LinkedIn only).

**Summary log:**
```
Verification Summary
  Input:              NNN jobs
  Kept (main sheet):  NNN
  Reposted sheet:     NN
  Closed/removed:     NN
  Dropped (German C1+): NN
  Dropped (exp ≥ 3y):  NN
  Enriched (salary):   NN
  Enriched (remote):   NN
  Match score recalculated: NNN jobs
  Output: Job_Search_<date>_verified.xlsx (2 sheets: Job Search + Reposted)
```

### Estimated Yield

For a ~440-job run (typical Thursday):

| Filter | Estimated count | Action | Reasoning |
|---|---|---|---|
| German C1+ required | 80–120 | **Drop** | 30–50% of Xing/Stepstone + 10–20% of LinkedIn |
| Experience ≥ 3 years | 15–30 | **Drop** | Senior jobs that slipped through title filter |
| Closed/inactive | 10–20 | **Drop** | Xing/Stepstone stale + some LinkedIn closed |
| Reposted (LinkedIn) | 20–55 | **Move to Reposted sheet** | 30% of LinkedIn jobs appeared in runs >7 days ago (verified: 55/185 on Aug 27) |
| **Total dropped** | **105–170** | | |
| **Reposted sheet** | **20–55** | | Segregated for manual review |
| **Main sheet** | **~215–315 jobs** | | Actually applicable, enriched, score-recalculated |

### Implementation Notes

- **`verify_jobs.py` rewrite** — replaces the v1 script entirely. Same CLI interface (`--csv`, `--force`).
- **LinkedIn via plain requests + JSON-LD** — no browser relay, no auth. Job detail pages serve full JD in JSON-LD `<script>` tags to unauthenticated requests. 2 workers, 1s delay, retry once on failure.
- **Reposted detection** — runs from CSV data only (no page fetch needed): cross-run history scan + job ID age gap. Segregates to "Reposted" sheet, not dropped.
- **Error handling** — network errors/timeouts don't drop jobs; `verified_active` left empty (unknown, keep). Only confirmed 404/explicit-closed drops.
- **Idempotent** — skip rows already verified unless `--force`.
- **Dependencies** — existing `requests`, `cloudscraper`, `openpyxl`. No new packages.
- **Subagent coordination** — main agent spawns 3 `task` subagents in parallel. Each writes results to a `local://` file. Main agent merges after all complete.

### LinkedIn Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Rate-limited at 2 workers | Low-Medium | Some URLs missing JSON-LD | Retry once with 3s backoff; leave unknown if still fails |
| No auth wall risk | — | — | Plain requests, no authenticated session to flag |
| JSON-LD structure changes | Low | Extraction fails | Defensive parsing: try JSON-LD first, fall back to HTML tag stripping |
| 5 workers causes 29% failures | Avoided | — | Using 2 workers (verified: 5 workers = 71.4% success, individual retries = 100%) |

### Future Enhancements (not in v2)
- **Already-applied detection** — cross-reference with `/home/sagar/Applications/` folder names
- **Delta mode** — only verify platforms with high stale rates
- **Historical stale-rate tracking** — log per-platform closed-job rates over time
- **LinkedIn via Apify fallback** — if plain requests + JSON-LD stops working (LinkedIn changes SSR), add `--verify-linkedin=apify` flag using the paid actor ($0.50) to fetch all JDs in one API call

---

## Implementation Order

| Step | What | Who | Time |
|---|---|---|---|
| 1 | Run `apify_job_search.py` (scrape + 3-tier dedup + export CSV) | Main agent | ~30s |
| 2 | Spawn 3 subagents in parallel (FastVerifiers, GermanBoards, LinkedInVerifier) | Main agent | spawn |
| 3 | Each subagent: fetch URLs, extract JD, apply filters, enrich, write results | 3 subagents | ~6 min max |
| 4 | Merge results, drop filtered rows, segregate reposted, recalculate match scores, export XLSX | Main agent | ~30s |
| **Total** | | | **~8 min** |

### File Layout

| What | File | Status |
|---|---|---|
| 3-tier dedup | `apify_job_search.py` (already modified) | Shipped |
| Full verification (v2) | `verify_jobs.py` (rewrite) | This plan |
| Plan document | `PLAN_dedup_and_verification.md` (this file) | Updated |

### Pipeline Flow

```
apify_job_search.py
  ├── fetch 8 platforms in parallel
  ├── within-run dedup
  ├── cross-platform fuzzy dedup
  ├── cross-run dedup
  └── exports Job_Search_<date>.csv  ← deduped, ~400-450 jobs
          ↓
verify_jobs.py v2  (3 subagents in parallel)
  ├── FastVerifiers:   ATS + Arbeitnow + Startup.jobs + Glassdoor + Indeed (~26 URLs, ~2 min)
  ├── GermanBoards:    Xing + Stepstone (~232 URLs, ~6 min)
  ├── LinkedInVerifier: LinkedIn via plain requests + JSON-LD (~185 URLs, ~3 min)
  │   Each: fetch JD → check active → check reposted → German filter → exp filter → match score → enrich
  ↓
Merge + Export
  ├── Drop: closed + German > B2 + exp ≥ 3y
  ├── Segregate: reposted LinkedIn jobs → "Reposted" sheet
  ├── Recalculate match_score from JD text
  └── Write Job_Search_<date>_verified.xlsx  ← 2 sheets: Job Search + Reposted
```
