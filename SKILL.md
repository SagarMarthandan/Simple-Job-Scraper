---
name: Jobscraper
description: >-
  Use when the user wants to run the automated job search pipeline. Fetches fresh job postings (< 24 hours old) from LinkedIn, Indeed, Arbeitnow, Xing, Stepstone, and ATS Direct for data/AI/analytics roles in Germany, filters by experience (<= 2 years), location (working student: Hamburg & Kiel only), and title relevance (must contain data/analytics/AI/SQL/Python keywords), deduplicates against yesterday's run, and exports to CSV/XLSX/JSON/MD. Trigger on keywords like "job search", "job scraper", "find jobs", "scrape jobs", "job postings", "fresh jobs", "data jobs germany", "linkedin jobs", "indeed jobs", "arbeitnow", "xing jobs", "stepstone jobs", "apify jobs", "job pipeline", "run job search".
dependencies: python>=3.10, requests, openpyxl, beautifulsoup4, tinyfish-cli
---

# Jobscraper Pipeline

> **Instructions for Oh My Pi (OMP) Session:**
> When this skill is invoked, execute the automated job search pipeline below.
> It fetches fresh job postings (< 24 hours old) from **LinkedIn**, **Indeed**, **Arbeitnow**, **Xing**, **Stepstone**, and **ATS Direct** (Greenhouse/SmartRecruiters/Ashby) across all target role profiles, applies location-aware working student rules, deduplicates against yesterday's run, and outputs a sortable **CSV + XLSX** with the current execution date/time stamp.

## Execution

### Step 1: Scrape (bash)

```bash
cd /home/sagar/Skills/Jobscraper && python3 apify_job_search.py
```

### Step 2: Verify (eval — requires OMP runtime for LLM)

The verify step MUST run through `eval` (OMP Python kernel), not `bash`.
It needs `completion` (OMP-injected) for LLM classification. TinyFish JD
fetching uses the `tinyfish` CLI via subprocess. Running via bash silently
skips LLM classification, producing inaccurate German/experience detection.

**Note:** The Python `completion` prelude function has a recursion bug.
LLM classification must be done from JS `eval` first, then injected into
the Python run. Also, the eval kernel may use a `.venv` Python without
openpyxl — add the user site-packages path.

**Step 2a: LLM classification (JS eval)**

```javascript
// eval cell (language: js)
import { readFileSync, writeFileSync } from 'fs';

// Load JD texts extracted by Python (see Step 2b)
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

**Step 2b: Full verification (Python eval)**

```python
# eval cell (language: py)
import sys, json, subprocess, re, site
from pathlib import Path
from datetime import datetime

# Fix openpyxl path (eval kernel may use .venv without it)
if site.getusersitepackages() not in sys.path:
    sys.path.insert(0, site.getusersitepackages())

skill_dir = Path("/home/sagar/Skills/Jobscraper")
if str(skill_dir) not in sys.path:
    sys.path.insert(0, str(skill_dir))

# --- Extract JD texts for JS-side LLM classification ---
csv_path = skill_dir / 'Job Search' / datetime.now().strftime("%Y-%m-%d") / f'Job_Search_{datetime.now().strftime("%b_%d_%Y").replace("_0", "_")}.csv'
# (load CSV + JSON + TinyFish cache, extract relevant sections, save to /tmp/jd_to_classify.json)
# Run JS eval cell above, then continue below.

# --- Load pre-computed classifications ---
with open("/tmp/jd_classifications.json") as f:
    classifications = json.load(f)

def tinyfish_fetch(urls):
    result = subprocess.run(["tinyfish", "fetch", "content", "get", "--format", "markdown"] + urls,
                            capture_output=True, text=True, timeout=120)
    return json.loads(result.stdout)

g = {'__file__': str(skill_dir / 'verify_jobs.py'), 'tinyfish_fetch': tinyfish_fetch, 'completion': lambda *a, **k: "stub"}
with open(skill_dir / 'verify_jobs.py') as f:
    exec(compile(f.read(), 'verify_jobs.py', 'exec'), g)

# Monkey-patch llm_classify_all to use pre-computed results
def patched(rows):
    for c in classifications:
        if c["idx"] < len(rows):
            r = rows[c["idx"]]
            r["detail_language"] = {"c1+": "German C1+ required", "b1/b2": "German B1/B2 OK", "preferred": "German preferred"}.get(c["german"], "")
            r["detail_exp_years"] = str(c["exp_years"]) if c["exp_years"] else ""
    print(f"[*] LLM classification: {len(classifications)}/{len(rows)} jobs classified (pre-computed from JS)")
g['llm_classify_all'] = patched

g['run_verification'](csv_path, force=True)
```

TinyFish descriptions are cached to `tinyfish_cache.json` in the run
directory — interrupts and re-runs load the cache and skip already-fetched
URLs (no wallet re-spend).

Dependencies: `requests`, `openpyxl`, `beautifulsoup4` (for ATS scraper), `tinyfish` CLI (for JD fetching).
Install: `pip install requests openpyxl beautifulsoup4` and `npm install -g @tiny-fish/cli`

Indeed JD fallback: `playwright`, `playwright-extra`, `puppeteer-extra-plugin-stealth` (Node.js).
Install: `cd /home/sagar/Skills/Jobscraper && npm install playwright playwright-extra puppeteer-extra-plugin-stealth && npx playwright install chromium`

## Context

- **Candidate Name:** Sagar Marthandan
- **Base Location:** Kiel, Germany
- **Base Resumes Directory:** `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/base_files`
- **Portfolio Directory:** `/home/sagar/Documents/YAML-CV/skills/okf-cv/okf/portfolio`
- **Target Output Directory:** `/home/sagar/Skills/Jobscraper/Job Search`
- **Apify Token:** read from the `APIFY_TOKEN` environment variable or `config.json`
- **Script:** `/home/sagar/Skills/Jobscraper/apify_job_search.py`
- **Full documentation:** `/home/sagar/Skills/Jobscraper/apify_job_search.md`

## Search Criteria

1. **Freshness Window:** Strictly posted within the **last 24 hours**.
2. **Target Platforms:**
   - **LinkedIn** (free HTML scraping, no Apify, $0)
   - **Indeed** (Apify — valig/indeed-jobs-scraper, ~$0.04/run)
   - **Arbeitnow** (free API, no Apify)
   - **Xing** (free HTML scraping via plain `requests`, no Apify)
   - **Stepstone** (free HTML scraping via plain `requests`, no Apify)
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

- `Job_Search_<Month>_<Day>_<Year>_verified.xlsx` — 3 sheets: "To Apply" (live, apply-ready, enriched with German requirement, experience years, salary, remote/hybrid), "Reposted" (LinkedIn reposts for manual review), and "Already Applied" (jobs matching Sagar's Applications folder or Obsidian vault). Hyperlink smoke test runs automatically after export.
- `tinyfish_cache.json` — cached JD descriptions (survives interrupts, re-runs skip already-fetched URLs)

## Cost

~$0.04/run Apify (Indeed only). LinkedIn, Arbeitnow, Xing, Stepstone, and ATS Direct are free. Verify step: TinyFish fetch is free; LLM classification uses `completion(model="smol")` (minimal cost). Cache prevents re-fetching on re-runs.
