---
name: Jobscraper
description: >-
  Use when the user wants to run the automated job search pipeline. Fetches fresh job postings (< 24 hours old) from LinkedIn, Indeed, Arbeitnow, Xing, Stepstone, Wellfound, EU Remote Jobs, and ATS Direct for data/AI/analytics roles in Germany, filters by experience (<= 2 years), location (working student: Hamburg & Kiel only), and title relevance (must contain data/analytics/AI/SQL/Python keywords), deduplicates against yesterday's run, and exports to CSV/XLSX/JSON/MD. Trigger on keywords like "job search", "job scraper", "find jobs", "scrape jobs", "job postings", "fresh jobs", "data jobs germany", "linkedin jobs", "indeed jobs", "arbeitnow", "xing jobs", "stepstone jobs", "wellfound jobs", "remote jobs", "job pipeline", "run job search".
dependencies: python>=3.10, requests, openpyxl, beautifulsoup4
---

# Jobscraper Pipeline

> **Instructions for Oh My Pi (OMP) Session:**
> When this skill is invoked, execute the automated job search pipeline below.
> It fetches fresh job postings (< 24 hours old) from **LinkedIn**, **Indeed**, **Arbeitnow**, **Xing**, **Stepstone**, **Wellfound**, **EU Remote Jobs**, and **ATS Direct** (Greenhouse/SmartRecruiters/Ashby) across all target role profiles, applies location-aware working student rules, deduplicates against yesterday's run, and outputs a sortable **CSV + XLSX** with the current execution date/time stamp.

## Execution

### Step 1: Scrape (bash)

```bash
cd /home/sagar/Skills/Jobscraper && python3 apify_job_search.py
```

### Step 2: Verify (eval — requires OMP runtime for LLM)

The verify step MUST run through `eval` (OMP Python/JS kernels), not `bash`.
It needs `completion` (OMP-injected) for LLM classification of German
language requirements and experience years. Running via bash silently
skips LLM classification, producing inaccurate German/experience detection.

**Note:** The Python `completion` prelude function has a recursion bug.
LLM classification must be done from JS `eval`, then injected into the
Python run. The eval kernel may use a `.venv` Python without openpyxl —
add the user site-packages path.

Three sub-steps, run in order: **2a** (Python: extract JD texts) →
**2b** (JS: LLM classification) → **2c** (Python: full verification).

**Step 2a: Extract JD texts (Python eval)**

Loads today's CSV + JSON, injects descriptions from JSON into CSV rows,
extracts relevant sections (German/experience keywords) via
`_extract_relevant_sections()` from `verify_jobs.py`, and saves the
extracted texts to `/tmp/jd_to_classify.json` for the JS classification step.

```python
# eval cell (language: py)
import sys, json, csv, site
from pathlib import Path
from datetime import datetime

# Fix openpyxl path (eval kernel may use .venv without it)
if site.getusersitepackages() not in sys.path:
    sys.path.insert(0, site.getusersitepackages())

skill_dir = Path("/home/sagar/Skills/Jobscraper")
if str(skill_dir) not in sys.path:
    sys.path.insert(0, str(skill_dir))

# Exec verify_jobs.py to get _extract_relevant_sections
_g = {'__file__': str(skill_dir / 'verify_jobs.py'), 'completion': lambda *a, **k: "stub"}
with open(skill_dir / 'verify_jobs.py') as f:
    exec(compile(f.read(), 'verify_jobs.py', 'exec'), _g)
_extract_relevant_sections = _g['_extract_relevant_sections']

# Find today's CSV
csv_path = skill_dir / 'Job Search' / datetime.now().strftime("%Y-%m-%d") / f'Job_Search_{datetime.now().strftime("%b_%d_%Y").replace("_0", "_")}.csv'
print(f"[*] Loading: {csv_path}")

# Load CSV rows
with open(csv_path, encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))

# Inject descriptions from sibling JSON (same logic as run_verification)
json_path = csv_path.with_suffix('.json')
if json_path.exists():
    with open(json_path, encoding='utf-8') as f:
        json_data = json.load(f)
    url_to_desc = {j.get('job_url', ''): j['description'] for j in json_data
                   if isinstance(j, dict) and j.get('description')}
    injected = 0
    for row in rows:
        url = row.get('job_url', '')
        if url in url_to_desc and not row.get('description'):
            row['description'] = url_to_desc[url]
            injected += 1
    if injected:
        print(f"[*] Injected descriptions from JSON for {injected} job(s)")

# Extract relevant JD sections for LLM classification
items = []
for idx, row in enumerate(rows):
    jd = row.get('description', '')
    if len(jd) >= 50:
        items.append({'idx': idx, 'text': _extract_relevant_sections(jd)})

with open('/tmp/jd_to_classify.json', 'w') as f:
    json.dump(items, f)

print(f"[*] Extracted {len(items)} JD texts for classification (out of {len(rows)} total jobs)")
```

**Step 2b: LLM classification (JS eval)**

Reads `/tmp/jd_to_classify.json` (created by Step 2a), classifies German
language requirement and minimum experience years via `completion(model='smol')`
in batches of 10, and saves results to `/tmp/jd_classifications.json`.

```javascript
// eval cell (language: js)
import { readFileSync, writeFileSync } from 'fs';

const items = JSON.parse(readFileSync('/tmp/jd_to_classify.json', 'utf-8'));
const BATCH = 10;
const PROMPT = `Classify German language requirement and minimum experience years for each job.

German level (pick one):
- C1+ = C1, C2, fluent/fließend, native/Muttersprache, verhandlungssicher, "sehr gute Deutschkenntnisse" standalone, "mind. C1", "mindestens C1"
- B1/B2 = B1 or B2 only, "gute Deutschkenntnisse" without "sehr"
- preferred = nice-to-have/wünschenswert/von Vorteil/idealerweise
- none = no German mentioned, English-only

Notes: "Sehr gute Deutsch- und Englischkenntnisse" = B1/B2. "Sehr gute Deutschkenntnisse" standalone = C1+.

Experience years: extract the MINIMUM required years as a number. Empty if not specified.

Jobs:
{jobs}

Reply with EXACTLY {count} lines. Format: <job_number>|<german_level>|<exp_years_or_empty>
Example:
1|C1+|3
2|none|
3|preferred|2`;

const results = [];
for (let i = 0; i < items.length; i += BATCH) {
    const batch = items.slice(i, i + BATCH);
    const jobs = batch.map((it, j) => `${j+1}: ${it.text}`).join('\n\n');
    const prompt = PROMPT.replace('{jobs}', jobs).replace('{count}', String(batch.length));
    for (let attempt = 0; attempt <= 3; attempt++) {
        try {
            const h = await completion(prompt, 'smol');
            const text = await h.wait();
            const lines = text.trim().split('\n');
            for (const line of lines) {
                const m = line.match(/^(\d+)\s*\|\s*(C1\+|B1\/B2|preferred|none)\s*\|\s*(\d*)\s*$/i);
                if (m && batch[parseInt(m[1])-1])
                    results.push({ idx: batch[parseInt(m[1])-1].idx, german: m[2].toLowerCase(), exp_years: m[3] ? parseInt(m[3]) : null });
            }
            for (let j = 0; j < batch.length; j++)
                if (!results.some(r => r.idx === batch[j].idx))
                    results.push({ idx: batch[j].idx, german: 'none', exp_years: null });
            break;
        } catch (e) {
            if (attempt < 3 && String(e).includes('429')) {
                await new Promise(r => setTimeout(r, (attempt+1) * 6000));
            } else { batch.forEach(it => results.push({ idx: it.idx, german: 'none', exp_years: null })); break; }
        }
    }
}
writeFileSync('/tmp/jd_classifications.json', JSON.stringify(results));
```

**Step 2c: Full verification (Python eval)**

Loads pre-computed LLM classifications from Step 2b, execs `verify_jobs.py`,
monkey-patches `llm_classify_all` to inject the pre-computed results (bypassing
the broken Python `completion` prelude), and runs `run_verification()` which:
verifies per-platform job liveness, detects LinkedIn reposts, segregates
staffing agencies and already-applied jobs, and writes the 4-sheet verified XLSX.

```python
# eval cell (language: py)
import sys, json, site
from pathlib import Path
from datetime import datetime

if site.getusersitepackages() not in sys.path:
    sys.path.insert(0, site.getusersitepackages())

skill_dir = Path("/home/sagar/Skills/Jobscraper")
if str(skill_dir) not in sys.path:
    sys.path.insert(0, str(skill_dir))

# Load pre-computed classifications from JS (Step 2b)
with open("/tmp/jd_classifications.json") as f:
    classifications = json.load(f)

# Exec verify_jobs.py fresh (gets all functions + imports staffing_filter, applied_check)
g = {'__file__': str(skill_dir / 'verify_jobs.py'), 'completion': lambda *a, **k: "stub"}
with open(skill_dir / 'verify_jobs.py') as f:
    exec(compile(f.read(), 'verify_jobs.py', 'exec'), g)

# Monkey-patch llm_classify_all to use pre-computed JS results
def patched(rows):
    for c in classifications:
        if c["idx"] < len(rows):
            r = rows[c["idx"]]
            r["detail_language"] = {"c1+": "German C1+ required", "b1/b2": "German B1/B2 OK", "preferred": "German preferred"}.get(c["german"], "")
            r["detail_exp_years"] = str(c["exp_years"]) if c["exp_years"] else ""
    print(f"[*] LLM classification: {len(classifications)}/{len(rows)} jobs classified (pre-computed from JS)")
g['llm_classify_all'] = patched

csv_path = skill_dir / 'Job Search' / datetime.now().strftime("%Y-%m-%d") / f'Job_Search_{datetime.now().strftime("%b_%d_%Y").replace("_0", "_")}.csv'
g['run_verification'](csv_path, force=True)
```

Dependencies: `requests`, `openpyxl`, `beautifulsoup4`.
Install: `pip install requests openpyxl beautifulsoup4`

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
7. **Cross-Run Deduplication:** After within-run dedup, jobs already in yesterday's CSV are removed by URL match. Prevents duplicates across consecutive daily sheets when 24h freshness windows overlap.

## Output

Files written to `/home/sagar/Skills/Jobscraper/Job Search/YYYY-MM-DD/`:
- `Job_Search_<Month>_<Day>_<Year>.csv` — UTF-8 BOM, sortable
- `Job_Search_<Month>_<Day>_<Year>.json`
- `Job_Search_<Month>_<Day>_<Year>.xlsx` — autofilter + clickable links
- `JOB_OPENINGS_LAST_24H.md` — markdown summary

### Step 2 Output (verify)

- `Job_Search_<Month>_<Day>_<Year>_verified.xlsx` — 4 sheets: "To Apply" (live, apply-ready, enriched with German requirement, experience years, salary, remote/hybrid), "Reposted" (LinkedIn reposts for manual review), "Staffing Companies" (staffing/recruitment agency postings — segregated, not dropped), and "Already Applied" (jobs matching `applications_tracker.csv`). Hyperlink smoke test runs automatically after export.

$0.00/run — all 8 platforms free (no Apify). Indeed uses a public GraphQL API; LinkedIn uses free HTML scraping; Arbeitnow/Xing/Stepstone use free HTML/REST; Wellfound uses SSR role pages + JSON-LD detail pages; EU Remote Jobs uses WordPress REST API; ATS Direct uses free public JSON APIs. Job descriptions arrive from step 1 (JSON-LD on detail pages for LinkedIn/Xing/Stepstone/Wellfound, GraphQL/API for Indeed/Arbeitnow/EU Remote Jobs/ATS). Verify step only needs LLM classification via `completion(model="smol")` (minimal cost).
