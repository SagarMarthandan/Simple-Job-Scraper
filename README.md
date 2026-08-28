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

### Filters Applied

1. **Active/closed check** — 404/410 = drop
2. **German level (max B2)** — LLM classifies JD text: C1+ → drop, B1/B2 → keep, preferred → flag
3. **Experience ≥3 years** → drop
4. **Reposted LinkedIn** → segregate to "Reposted" sheet (not dropped)
5. **Match score** — recalculated from actual JD text
6. **Enrichment** — remote/hybrid/onsite, salary extraction

### TinyFish + LLM (Sandbox Mode)

When run inside the OMP eval sandbox, inject TinyFish and LLM before calling `run_verification()`:

```python
verify_jobs.completion = completion          # smol model for classification
verify_jobs.tinyfish_fetch = tinyfish_fetch  # TinyFish bridge for JD rendering
```

TinyFish pre-fetches all JDs in the main thread (not thread-safe), batch size 2, ~33 min for 500 URLs. Falls back to plain requests/cloudscraper in standalone mode.

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
| Match score tech stack | `apify_job_search.py` → `TECH_KEYWORDS` | dbt, airflow, spark, python, sql, etc. |
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
├── verify_jobs.py           # post-step: JD verification, LLM classification, 2-sheet XLSX
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
