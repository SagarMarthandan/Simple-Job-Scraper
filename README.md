# Jobscraper

Automated job search pipeline that fetches fresh postings (< 24 hours old) from **9 sources across 8 platforms**, filters them for entry-level data/analytics/AI roles in Germany, and exports a sortable CSV/XLSX/JSON/MD report. All platforms run in **parallel** — total runtime ~27s.

## Quick Start

```bash
cd /home/sagar/Skills/Jobscraper
pip install cloudscraper requests openpyxl beautifulsoup4
python3 apify_job_search.py
```

Output is written to `Job Search/YYYY-MM-DD/`.

## Pipeline

```
8 platforms in parallel → title relevance → seniority/experience → Germany location
→ working-student city → within-run dedup → cross-platform dedup → cross-run dedup → export
```

### Platforms

| Platform | Cost/run | Method |
|---|---|---|
| LinkedIn | $0.00 | Free HTML scraping, 6 locations × 10 roles, `f_TPR=r86400` 24h filter |
| Indeed | ~$0.04 | Apify actor, 10 roles parallel, post-filter on `datePublished` |
| Arbeitnow | $0.00 | Free REST API |
| Startup.jobs | $0.00 | `cloudscraper` HTML, Cloudflare bypass |
| Xing | $0.00 | `requests` HTML, AWS CloudFront (no anti-bot) |
| Stepstone | $0.00 | `requests` HTML, Akamai (plain requests work) |
| Glassdoor | $0.00 | `cloudscraper` (chrome), JSON-LD + RSC `ageInDays` filtering |
| ATS Direct | $0.00 | Greenhouse/SmartRecruiters/Ashby public JSON APIs, 17 companies |
| **Total** | **~$0.04** | |

### Output Files

| File | Description |
|---|---|
| `Job_Search_<date>.csv` | UTF-8 BOM, sortable in Excel/Calc |
| `Job_Search_<date>.json` | Same data in JSON |
| `Job_Search_<date>.xlsx` | Frozen header, autofilter, clickable hyperlinks, numeric `match_score` |
| `JOB_OPENINGS_LAST_24H.md` | Markdown summary table with apply links |

## Job Verification (Post-Step)

`verify_jobs.py` visits every job URL to filter out unsuitable listings before you apply:

```bash
python3 verify_jobs.py                    # auto-finds latest CSV
python3 verify_jobs.py --csv path/to.csv  # specific CSV
python3 verify_jobs.py --force            # re-verify all rows
```

### Pipeline Stages

1. **Description acquisition** — TinyFish pre-fetches JDs from all platform URLs (batch size 2, cached to `tinyfish_cache.json`). JSON injection from Apify for Indeed/Arbeitnow. Prints `X/Y jobs acquired descriptions`.
2. **Platform verification** — each platform verifier checks if the listing is still active (404/410 = closed). Uses pre-fetched description for salary/remote extraction. Runs in parallel (1 thread per platform).
3. **Reposted detection** — LinkedIn jobs flagged via cross-run history (>7d) + job ID age gap (>14d).
4. **LLM classification** — smol model (Gemini 3.1 Flash Lite) classifies German level + experience years in batches of 10. Reads `row["description"]` directly. Prints `X/Y jobs classified`.
5. **Filter + export** — drops closed, German C1+, exp ≥3y. Segregates reposted to separate sheet. Writes 2-sheet XLSX.

### Filters Applied

| Filter | Action |
|---|---|
| Closed (404/410) | Drop |
| German C1+ required | Drop |
| Experience ≥3 years | Drop |
| Reposted LinkedIn | Segregate to "Reposted" sheet |
| German B1/B2 | Keep + flag |
| German preferred | Keep + flag |

### TinyFish + LLM (Sandbox Mode)

The verify step MUST run inside the OMP eval sandbox — it needs `tinyfish_fetch` and `completion` (OMP-injected functions that don't exist in standalone `python3`). Running via bash silently skips JD fetching and LLM classification.

```python
# eval cell (language: py)
import verify_jobs
verify_jobs.completion = completion          # smol model for classification
verify_jobs.tinyfish_fetch = tinyfish_fetch  # TinyFish bridge for JD rendering
verify_jobs.run_verification(Path("Job Search/YYYY-MM-DD/Job_Search_*.csv"), force=True)
```

TinyFish descriptions are cached to `tinyfish_cache.json` — interrupts and re-runs load the cache and skip already-fetched URLs (no wallet re-spend). No regex fallback: if LLM is unavailable, jobs get `none`/empty defaults (visible in output).

Output: `Job_Search_<date>_verified.xlsx` — 2-sheet workbook (Job Search + Reposted).

## Configuration

### Apify Token

Only needed for Indeed (~$0.04/run). Read from (in order of precedence):
1. `APIFY_TOKEN` environment variable
2. `config.json` in the skill root
3. `~/.apify_token` (Apify CLI auth)

### Dependencies

```bash
pip install cloudscraper requests openpyxl beautifulsoup4
```

### Customization

This pipeline is configured for a specific candidate profile. Key settings to adapt:

| Setting | Where | Current value |
|---|---|---|
| Target roles | `apify_job_search.py` → `SEARCH_ROLES` | 10 data/AI/BI roles |
| Experience ceiling | `apify_job_search.py` → `MAX_EXP_YEARS` | ≤ 2 years |
| Working student cities | `apify_job_search.py` → `check_experience_and_location()` | Hamburg, Kiel |
| ATS company slugs | `ats_scraper.py` → `*_SLUGS` | 17 German tech companies |
| LinkedIn search cities | `apify_job_search.py` → `LINKEDIN_LOCATIONS` | Germany, Berlin, Munich, Hamburg, Frankfurt, Cologne |

## Project Structure

```
Jobscraper/
├── README.md                # this file
├── ARCHITECTURE.md          # technical details: filter chain, dedup, freshness, verification
├── CHANGELOG.md             # version history
├── SKILL.md                 # OMP skill definition
├── apify_job_search.py      # main pipeline (8 fetchers + 3-tier dedup + export)
├── verify_jobs.py           # post-step: TinyFish JD fetch + cache, LLM classification, 2-sheet XLSX
├── ats_scraper.py           # ATS direct scraping (Greenhouse/SmartRecruiters/Ashby)
├── dedup_existing_sheets.py # standalone retroactive dedup cleanup
├── apify_job_search.md      # platform-specific gotchas, actor schemas, cost analysis
├── config.json              # Apify token (gitignored)
└── Job Search/              # output directory (gitignored)
```

## Further Reading

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — filter chain, dedup tiers, freshness filtering, verification internals, function reference
- [`CHANGELOG.md`](CHANGELOG.md) — version history
- [`SKILL.md`](SKILL.md) — OMP skill trigger definition
- [`apify_job_search.md`](apify_job_search.md) — actor schemas, platform gotchas, cost optimization
