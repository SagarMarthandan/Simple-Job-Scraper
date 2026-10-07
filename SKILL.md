---
name: Jobscraper
description: >-
  Use when the user wants to run the automated job search pipeline. Fetches fresh job postings (< 24 hours old) from LinkedIn, Indeed, Arbeitnow, Xing, Stepstone, Wellfound, EU Remote Jobs, and ATS Direct for data/AI/analytics roles in Germany; filters experience (<= 2 years), Germany location (working student: Hamburg & Kiel only), and title relevance; deduplicates by exact stable posting URL across all earlier dated exports; and exports CSV/XLSX/JSON/MD. Trigger on keywords like "job search", "job scraper", "find jobs", "scrape jobs", "job postings", "fresh jobs", "data jobs germany", "linkedin jobs", "indeed jobs", "arbeitnow", "xing jobs", "stepstone jobs", "wellfound jobs", "remote jobs", "job pipeline", "run job search".
dependencies: python>=3.10, requests, openpyxl, beautifulsoup4, tqdm
---

# Jobscraper Pipeline

> **Instructions for Oh My Pi (OMP) Session:**
> When this skill is invoked, execute the automated job search pipeline below.
> It fetches fresh job postings (< 24 hours old) from **LinkedIn**, **Indeed**, **Arbeitnow**, **Xing**, **Stepstone**, **Wellfound**, **EU Remote Jobs**, and **ATS Direct** (Greenhouse/SmartRecruiters/Ashby), applies Germany-only location and working-student city rules, removes exact URL repeats from every earlier dated export, and outputs sortable **CSV + XLSX** files. Same-day reruns preserve eligible existing rows.

## Execution

### Step 1: Scrape (bash)

```bash
cd /home/sagar/Skills/Jobscraper && python3 apify_job_search.py
```

### Step 2: Verify (OMP eval — typed JD judgment)

`verify_jobs.run_verification()` is asynchronous and requires OMP's typed
`judge_batch` API. Do not run `python3 verify_jobs.py`; standalone mode cannot
provide the judge and exits with an explicit instruction to use OMP eval.
Foreign/unknown locations and exact URLs present in earlier dated exports are
filtered before platform requests or JD judgment. Only final, full descriptions
(including descriptions acquired by platform verification) are judged. Missing
or failed judgments go to **Needs Review**, never to To Apply.

The separate already-applied matcher still uses `completion()` for scraped-job
versus tracker pairs. Run 2a, 2b, then 2c in the same OMP eval session.

#### Step 2a: Prepare already-applied candidates (Python eval)

```python
import site, sys
from pathlib import Path
from datetime import datetime

if site.getusersitepackages() not in sys.path:
    sys.path.insert(0, site.getusersitepackages())

skill_dir = Path("/home/sagar/Skills/Jobscraper")
if str(skill_dir) not in sys.path:
    sys.path.insert(0, str(skill_dir))

from applied_check import prepare_match_input

csv_path = (
    skill_dir / "Job Search" / datetime.now().strftime("%Y-%m-%d")
    / f'Job_Search_{datetime.now().strftime("%b_%d_%Y").replace("_0", "_")}.csv'
)
print(f"[*] Preparing tracker matches for {csv_path}")
count = prepare_match_input(str(csv_path))
print(f"[*] Prepared {count} already-applied candidate pairs")
```

#### Step 2b: Classify already-applied pairs (JavaScript eval)

This step does not classify German or experience requirements. JD judgment is
performed by `judge_batch` in Step 2c.

```javascript
import { readFileSync, writeFileSync } from 'fs';

const input = JSON.parse(readFileSync('/tmp/already_applied_input.json', 'utf-8'));
const BATCH = 10;
const PROMPT = `Determine whether each SCRAPED job is the same company and role as
the TRACKER entry. Ignore legal suffixes, location qualifiers, brand/legal entity
variants, seniority/gender formatting, and German-English title translations.
Same URL is a match even with tracking parameters.
Not a match: different roles at the same company, different companies sharing a
word, or jobs with company "Unknown" unless the URLs match.

Pairs:
{pairs}

Reply with a JSON array, one element per pair:
[{"id": 1, "match": true, "reason": "same company and role"},
 {"id": 2, "match": false, "reason": "different position"}]`;

const matches = [];
for (let i = 0; i < input.length; i += BATCH) {
    const batch = input.slice(i, i + BATCH);
    const pairs = batch.map(p =>
        `Pair ${p.id}: SCRAPED ${p.scraped_company} / ${p.scraped_title} / ${p.scraped_url}; ` +
        `TRACKER ${p.tracker_company} / ${p.tracker_title} / ${p.tracker_url}`
    ).join('\n');
    const prompt = PROMPT.replace('{pairs}', pairs);
    for (let attempt = 0; attempt <= 3; attempt++) {
        try {
            const handle = await completion(prompt, 'smol');
            const raw = await handle.wait();
            const fence = String.fromCharCode(96).repeat(3);
            const json = raw
                .replace(new RegExp('^' + fence + '(?:json)?\\s*', 'i'), '')
                .replace(new RegExp('\\s*' + fence + '\\s*$'), '').trim();
            const results = JSON.parse(json);
            for (const result of results) {
                const pair = batch.find(p => p.id === result.id);
                if (pair && result.match === true) {
                    matches.push({
                        company: pair.scraped_company,
                        title: pair.scraped_title,
                        url: pair.scraped_url
                    });
                }
            }
            break;
        } catch (error) {
            if (attempt < 3 && String(error).includes('429')) {
                await new Promise(resolve => setTimeout(resolve, (attempt + 1) * 6000));
            } else {
                break;
            }
        }
    }
}
writeFileSync('/tmp/already_applied_matches.json', JSON.stringify(matches));
console.log(`Already-applied: ${matches.length} matches from ${input.length} candidate pairs`);
```

#### Step 2c: Full verification (Python eval)

The typed judge sees each eligible row's full final description and returns
independent German and experience choices. Required C1/C2/fluent/native/
verhandlungssicher and standalone *sehr gute Deutschkenntnisse* are C1+;
required B1/B2 and the conjunctive *sehr gute Deutsch- und Englischkenntnisse*
convention are allowed unless an explicit C1+ level is stated. Experience uses
the required minimum (lower bound of a range); optional experience does not
disqualify. Use the user site-packages path for `openpyxl` if the eval kernel
does not include it.

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

Dependencies: `requests`, `openpyxl`, `beautifulsoup4`, `tqdm`.
Install: `pip install requests openpyxl beautifulsoup4 tqdm`.

## Context

- **Candidate Name:** Sagar Marthandan
- **Base Location:** Kiel, Germany
- **Base Resumes Directory:** `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/base_files`
- **Portfolio Directory:** `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/portfolio`
- **Target Output Directory:** `/home/sagar/Skills/Jobscraper/Job Search`
- **Cost:** $0.00/run (all platforms free — no Apify)
- **Script:** `/home/sagar/Skills/Jobscraper/apify_job_search.py`
- **Full documentation:** `/home/sagar/Skills/Jobscraper/apify_job_search.md`

## Search Criteria

1. **Freshness Window:** Strictly posted within the **last 24 hours**.
2. **Target Platforms:**
   - **LinkedIn** (free HTML scraping, no Apify, $0)
   - **Indeed** (free GraphQL API, $0)
   - **Arbeitnow** (free API, no Apify)
   - **Xing** (free HTML scraping via plain `requests`, no Apify)
   - **Stepstone** (free HTML scraping via plain `requests`, no Apify)
   - **Wellfound** (free HTML scraping, SSR role pages + JSON-LD detail pages, $0)
   - **EU Remote Jobs** (free WordPress REST API, full descriptions, $0)
   - **ATS Direct** (Greenhouse/SmartRecruiters/Ashby public APIs, free)
3. **Title Relevance Filter:** Universal post-filter `is_relevant_title()` rejects any job whose title doesn't contain at least one data/analytics/AI/SQL/Python keyword.
4. **Role Types & Location Constraints:**
   - **Full-Time / Part-Time / Entry-Level / Junior (0–2 yrs exp):** Germany-wide.
   - **Internships (*Praktikum* / *Internship*):** Germany-wide.
   - **Working Student (*Werkstudent* / *Working Student*):** Strictly **ONLY Hamburg and Kiel**.
5. **Experience Ceiling:** Strict <= 2 years. Rejects Senior, Lead, Principal, Staff, Manager, Head, Architect, Director titles.
6. **Target Role Profiles (10 core roles):**
   1. Data Engineer, Analytics Engineer
   2. Data Analyst
   3. AI Engineer, Machine Learning Engineer
   4. Business Analyst
   5. SQL Developer
   6. Praktikum Data, Werkstudent Data, Werkstudent Business Intelligence
7. **Cross-Run Deduplication:** After within-run dedup, remove exact stable posting URLs seen in any earlier dated export; do not fuzzy-match companies or titles across dates. Exclude current/future folders from history, and preserve eligible current-date rows on reruns.

## Output

Files written to `/home/sagar/Skills/Jobscraper/Job Search/YYYY-MM-DD/`:
- `Job_Search_<Month>_<Day>_<Year>.csv` — UTF-8 BOM, sortable
- `Job_Search_<Month>_<Day>_<Year>.json`
- `Job_Search_<Month>_<Day>_<Year>.xlsx` — autofilter + clickable links
- `JOB_OPENINGS_LAST_24H.md` — markdown summary

### Step 2 Output (verify)

- `Job_Search_<Month>_<Day>_<Year>_verified.xlsx` — To Apply plus conditional Reposted, Staffing Companies, Already Applied, Needs Review, and Previously Seen sheets. Missing or failed JD judgments remain in Needs Review; older exact URL repeats are excluded before platform verification and may be listed under Previously Seen. Hyperlink structure is checked for every sheet; HTTP sampling excludes Previously Seen URLs.

All 8 platforms are free. Descriptions arrive from step 1 and may be extended during platform verification. The verification step uses OMP `judge_batch` on each eligible row's full final description; it does not use `completion()` or truncated JD excerpts for German/experience classification.
