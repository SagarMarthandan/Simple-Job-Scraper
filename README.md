# Jobscraper

Automated job search pipeline that fetches fresh postings (< 24 hours old) from **8 platforms** (LinkedIn, Indeed, Arbeitnow, Xing, Stepstone, Wellfound, EU Remote Jobs, ATS Direct), filters them for entry-level data/analytics/AI roles in Germany, and exports a sortable CSV/XLSX/JSON/MD report. All platforms run in **parallel** — total runtime ~3min.

## Quick Start

```bash
cd /home/sagar/Skills/Jobscraper
pip install requests openpyxl beautifulsoup4 tqdm
python3 apify_job_search.py
```

Output is written to `Job Search/YYYY-MM-DD/`.

## Pipeline

```
8 platforms in parallel → title/seniority + shared Germany location guard
→ within-run dedup → all-history exact URL dedup → export
→ OMP platform verification → full-description typed JD judgment → verified workbook
```

### Platforms

| Platform | Cost/run | Method |
|---|---|---|
| LinkedIn | $0.00 | Free HTML scraping, 6 locations × 10 roles, `f_TPR=r86400` 24h filter |
| Indeed | $0.00 | GraphQL API (`apis.indeed.com/graphql`), 10 roles parallel, `dateOnIndeed` 24h filter, full descriptions |
| Arbeitnow | $0.00 | Free REST API |
| Xing | $0.00 | `requests` HTML, AWS CloudFront (no anti-bot) |
| Stepstone | $0.00 | `requests` HTML, Akamai (plain requests work) |
| Wellfound | $0.00 | SSR role pages (`/role/l/{slug}/germany`) + JSON-LD detail pages, 6 role slugs |
| EU Remote Jobs | $0.00 | WordPress REST API (`/wp-json/wp/v2/job-listings`), full descriptions in response |
| ATS Direct | $0.00 | Greenhouse/SmartRecruiters/Ashby public JSON APIs, 17 companies |
| **Total** | **$0.00** | |

These costs cover scraping. OMP JD judgment and already-applied matching use
the configured model providers and may incur charges.

The latest live smoke run encountered Stepstone Brotli decoding errors and an
EU Remote Jobs HTTP 401. Both returned no jobs; the other sources completed.

### Output Files

| File | Description |
|---|---|
| `Job_Search_<date>.csv` | UTF-8 BOM, sortable in Excel/Calc |
| `Job_Search_<date>.json` | Same data in JSON |
| `Job_Search_<date>.xlsx` | Frozen header, autofilter, clickable hyperlinks, numeric `match_score` |
| `JOB_OPENINGS_LAST_24H.md` | Markdown summary table with apply links |

## Job Verification (Post-Step)

`verify_jobs.py` requires OMP's typed `judge_batch` API for German/experience
judgment. The standalone CLI cannot supply that API and exits with an
instruction to use the OMP Python eval path. Before platform checks, verification
removes non-Germany/unknown locations (and working-student roles outside Hamburg
or Kiel) and diverts exact URLs found in earlier dated exports to **Previously
Seen**. Such rows do not receive platform or JD requests.

Run the separate already-applied candidate-pair preparation and JS matching
steps in [`SKILL.md`](SKILL.md), then run the final verification in OMP Python
eval. `judge_batch` is provided by the OMP eval runtime:

```python
import site, sys
from pathlib import Path
from datetime import datetime

if site.getusersitepackages() not in sys.path:
    sys.path.insert(0, site.getusersitepackages())
skill_dir = Path("/home/sagar/Skills/Jobscraper")
if str(skill_dir) not in sys.path:
    sys.path.insert(0, str(skill_dir))
import verify_jobs

csv_path = (
    skill_dir / "Job Search" / datetime.now().strftime("%Y-%m-%d")
    / f'Job_Search_{datetime.now().strftime("%b_%d_%Y").replace("_0", "_")}.csv'
)
verify_jobs.judge_batch = judge_batch
await verify_jobs.run_verification(csv_path, force=True)
```

### Verification stages

1. **Eligibility boundary** — Germany-only evidence is required. Blank/unknown
   locations, remote regions without explicit Germany eligibility, and foreign
   locations are excluded. Working-student roles require a Hamburg or Kiel
   location. The same policy runs at scrape and verification boundaries.
2. **Platform verification** — check live status and merge any description
   acquired during verification with the existing row; the longest full
   description is retained for judgment.
3. **Typed JD judgment** — one bounded `judge_batch` call evaluates each
   eligible row's full final description with independent German and experience
   questions. Required C1/C2/fluent/native/verhandlungssicher and standalone
   *sehr gute Deutschkenntnisse* are C1+; required B1/B2 is allowed. The
   conjunctive *sehr gute Deutsch- und Englischkenntnisse* convention remains
   allowed unless C1+ is explicit. Experience uses the minimum required lower
   bound; optional experience does not disqualify. Missing/insufficient
   descriptions, failed judgments, and invalid answers go to **Needs Review**,
   never to To Apply.
4. **Existing routing** — apply already-applied matching, LinkedIn repost
   routing, active-status and role filters, then staffing segregation.
5. **Export** — write the verified workbook with **To Apply** and non-empty
   **Reposted**, **Staffing Companies**, **Already Applied**, **Needs Review**,
   and **Previously Seen** sheets. No foreign/unknown job is written to any
   sheet. Hyperlink structure is checked for all sheets; the HTTP sample omits
   Previously Seen URLs.

### Filters

| Filter | Action |
|---|---|
| Outside/unknown Germany location | Exclude before verification and sheet routing |
| Working student outside Hamburg/Kiel | Exclude before verification and sheet routing |
| Exact URL from earlier dated export | Skip platform/JD work; route German row to Previously Seen |
| Missing/failed/invalid JD judgment | Segregate to Needs Review |
| Already applied | Segregate to Already Applied |
| Reposted LinkedIn | Segregate to Reposted |
| German C1+ required | Drop |
| Experience minimum >2 years | Drop |
| Staffing/recruitment agency | Segregate to Staffing Companies |
| German B1/B2 or preferred | Keep + flag |

Output: `Job_Search_<date>_verified.xlsx`. Same-day scrape reruns preserve
eligible rows already exported for that date. All-history URL matching uses
stable posting identity only; company/title fuzzy matching is not used across
dates.

## Configuration

### Dependencies

```bash
pip install requests openpyxl beautifulsoup4 tqdm
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
├── apify_job_search.py      # main pipeline (8 fetchers + within-run and all-history URL dedup)
├── verify_jobs.py           # async OMP typed JD judgment, review routing, and verified workbook
├── job_identity.py          # shared stable posting URL normalization and dated-export history
├── location_policy.py       # shared Germany-only and Hamburg/Kiel eligibility
├── staffing_filter.py       # staffing/recruitment agency blocklist regex + is_staffing_company()
├── applied_check.py         # already-applied detection via applications_tracker.csv
├── ats_scraper.py           # ATS direct scraping (Greenhouse/SmartRecruiters/Ashby)
├── dedup_existing_sheets.py # standalone exact-URL historical dedup cleanup
├── apify_job_search.md      # platform details and exact-URL dedup behavior
├── tests/test_regressions.py # deterministic boundary and identity regressions
└── Job Search/              # output directory (gitignored)
```

## Regression Checks

```bash
python3 -m unittest discover -s tests -v
```

The suite covers Germany eligibility, stable posting identities, history date
boundaries, and missing or failed JD judgments.


## Further Reading

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — filter chain, dedup tiers, freshness filtering, verification internals, function reference
- [`CHANGELOG.md`](CHANGELOG.md) — version history
- [`SKILL.md`](SKILL.md) — OMP skill trigger definition
- [`apify_job_search.md`](apify_job_search.md) — platform gotchas, Indeed GraphQL API, cost analysis
