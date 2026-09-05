# Jobscraper

Automated job search pipeline that fetches fresh postings (< 24 hours old) from **6 platforms** (LinkedIn, Indeed, Arbeitnow, Xing, Stepstone, ATS Direct), filters them for entry-level data/analytics/AI roles in Germany, and exports a sortable CSV/XLSX/JSON/MD report. All platforms run in **parallel** — total runtime ~27s.

## Quick Start

```bash
cd /home/sagar/Skills/Jobscraper
pip install requests openpyxl beautifulsoup4
python3 apify_job_search.py
```

Output is written to `Job Search/YYYY-MM-DD/`.

## Pipeline

```
6 platforms in parallel → title relevance → seniority/experience
→ working-student city → within-run dedup → cross-run dedup → export
```

### Platforms

| Platform | Cost/run | Method |
|---|---|---|
| LinkedIn | $0.00 | Free HTML scraping, 6 locations × 10 roles, `f_TPR=r86400` 24h filter |
| Indeed | $0.00 | GraphQL API (`apis.indeed.com/graphql`), 10 roles parallel, `dateOnIndeed` 24h filter, full descriptions |
| Arbeitnow | $0.00 | Free REST API |
| Xing | $0.00 | `requests` HTML, AWS CloudFront (no anti-bot) |
| Stepstone | $0.00 | `requests` HTML, Akamai (plain requests work) |
| ATS Direct | $0.00 | Greenhouse/SmartRecruiters/Ashby public JSON APIs, 17 companies |
| **Total** | **$0.00** | |

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

1. **Platform verification** — each platform verifier checks if the listing is still active (404/410 = closed). Uses descriptions from step 1 (JSON-LD on detail pages for LinkedIn/Xing/Stepstone, GraphQL/API for Indeed/Arbeitnow/ATS) for salary/remote extraction. Runs in parallel (1 thread per platform).
2. **Reposted detection** — LinkedIn jobs flagged via cross-run history (>7d) + job ID age gap (>14d). No LLM tokens — pure computation.
3. **LLM classification** — smol model (GLM 5.2 free via OpenRouter) classifies German level + experience years in batches of 10. Reads `row["description"]` directly (already populated from step 1). Prints `X/Y jobs classified`.
4. **Already-applied detection** — `load_applied_job_keys()` scans `/home/sagar/Applications` (folder names) and Obsidian vault `Applications/` (.md files) for jobs already applied to. Uses `normalize_key(company, title)` for fuzzy matching. Checked first — already-applied jobs go to "Already Applied" sheet regardless of other filters.
5. **Filter + export** — drops closed, German C1+, exp ≥3y. Segregates reposted and already-applied to separate sheets. Writes 3-sheet XLSX with hyperlink smoke test.

### Filters Applied

| Filter | Action |
|---|---|
| Already applied | Segregate to "Already Applied" sheet |
| Reposted LinkedIn | Segregate to "Reposted" sheet |
| German C1+ required | Drop |
| Experience ≥3 years | Drop |
| German B1/B2 | Keep + flag |
| German preferred | Keep + flag |

### LLM Classification (Sandbox Mode)

The verify step MUST run inside the OMP eval sandbox — it needs `completion` (OMP-injected LLM function that doesn't exist in standalone `python3`). Running via bash silently skips LLM classification, producing inaccurate German/experience detection.

**Note:** The Python `completion` prelude has a recursion bug. LLM classification must be done from JS `eval` first, then injected into the Python run. See `SKILL.md` for the two-step JS+Python workflow.

No regex fallback: if LLM is unavailable, jobs get `none`/empty defaults (visible in output).

Output: `Job_Search_<date>_verified.xlsx` — 3-sheet workbook:
- **To Apply** — live, apply-ready jobs enriched with German requirement, experience years, salary, remote/hybrid
- **Reposted** — LinkedIn reposts for manual review
- **Already Applied** — jobs matching Applications folder or Obsidian vault

A hyperlink smoke test runs automatically after export — verifies cell value == hyperlink target for all rows, plus HTTP HEAD on a random sample.

## Configuration

### Dependencies

```bash
pip install requests openpyxl beautifulsoup4
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
├── apify_job_search.py      # main pipeline (6 fetchers + 2-tier dedup + export)
├── verify_jobs.py           # post-step: LLM classification, reposted detection, already-applied detection, 3-sheet XLSX + hyperlink smoke test
├── ats_scraper.py           # ATS direct scraping (Greenhouse/SmartRecruiters/Ashby)
├── dedup_existing_sheets.py # standalone retroactive dedup cleanup
├── apify_job_search.md      # platform-specific gotchas, cost analysis, Indeed GraphQL API docs
└── Job Search/              # output directory (gitignored)
```

## Further Reading

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — filter chain, dedup tiers, freshness filtering, verification internals, function reference
- [`CHANGELOG.md`](CHANGELOG.md) — version history
- [`SKILL.md`](SKILL.md) — OMP skill trigger definition
- [`apify_job_search.md`](apify_job_search.md) — platform gotchas, Indeed GraphQL API, cost analysis
