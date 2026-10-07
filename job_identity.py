"""Stable posting URL identity and dated-export history loading."""

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit


_TRACKING_KEYS = {
    "campaign",
    "campaignid",
    "fbclid",
    "from",
    "gh_src",
    "gclid",
    "li_fat_id",
    "mc_cid",
    "mc_eid",
    "pagenum",
    "position",
    "ref",
    "referrer",
    "refid",
    "source",
    "source_type",
    "sourcetype",
    "src",
    "tracking",
    "trackingid",
    "tracking_id",
    "trk",
}
_TRACKING_PREFIXES = ("utm_", "trk_", "ref_", "tracking_")
_INDEED_DOMAINS = (
    "indeed.co.uk", "indeed.com.au", "indeed.co.in", "indeed.co.jp",
    "indeed.com.br", "indeed.com.mx", "indeed.com.sg", "indeed.com.hk",
    "indeed.co.kr", "indeed.com.tr", "indeed.com.ar",
    "indeed.de", "indeed.fr", "indeed.it", "indeed.es", "indeed.nl",
    "indeed.at", "indeed.ch", "indeed.be", "indeed.ie", "indeed.ca",
    "indeed.pl", "indeed.se", "indeed.dk", "indeed.no", "indeed.fi",
    "indeed.pt", "indeed.com",
)
_HISTORY_OUTPUT_SUFFIXES = ("_verified", "_reposted", "_staffing", "_already_applied")


def _host_is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _is_indeed_host(host: str) -> bool:
    return any(_host_is(host, domain) for domain in _INDEED_DOMAINS)


def _is_tracking_key(key: str) -> bool:
    folded = key.casefold()
    return folded in _TRACKING_KEYS or folded.startswith(_TRACKING_PREFIXES)


def extract_linkedin_job_id(url: str) -> int:
    """Return LinkedIn's numeric posting ID, or 0 when the URL has none."""
    try:
        parsed = urlsplit((url or "").strip())
    except ValueError:
        return 0
    host = parsed.hostname or ""
    if not _host_is(host.casefold(), "linkedin.com"):
        return 0

    match = re.search(r"(?:^|[-/])(\d{6,})(?:/|$)", parsed.path)
    if match:
        return int(match.group(1))

    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() in {"currentjobid", "jobid"} and value.isdigit():
            return int(value)
    return 0


def normalize_job_url(url: str) -> str:
    """Normalize host/scheme and remove tracking, preserving exact posting IDs.

    LinkedIn numeric job IDs and Indeed ``jk`` IDs receive canonical keys.
    Other URLs retain case-sensitive paths and every non-tracking query pair.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw.rstrip("/")

    host = (parsed.hostname or "").casefold()
    if not host:
        return raw.rstrip("/")
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")

    if _host_is(host, "linkedin.com"):
        job_id = extract_linkedin_job_id(raw)
        if job_id:
            return f"linkedin:{job_id}"
        return f"https://{host}{path}"

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_key(key)
    ]
    if _is_indeed_host(host):
        jk = next(
            (value for key, value in query_pairs if key.casefold() == "jk" and value),
            "",
        )
        if jk:
            return f"indeed:{jk}"

    query_pairs.sort(key=lambda pair: (pair[0].casefold(), pair[0], pair[1]))
    query = urlencode(query_pairs)
    return f"https://{host}{path}" + (f"?{query}" if query else "")


def _source_csvs(run_dir: Path) -> list[Path]:
    return sorted(
        path for path in run_dir.glob("Job_Search_*.csv")
        if not path.stem.endswith(_HISTORY_OUTPUT_SUFFIXES)
    )


def _source_jsons(run_dir: Path) -> list[Path]:
    return sorted(
        path for path in run_dir.glob("Job_Search_*.json")
        if not path.stem.endswith(_HISTORY_OUTPUT_SUFFIXES)
    )


def _urls_from_csv(path: Path) -> set[str]:
    urls: set[str] = set()
    try:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or "job_url" not in reader.fieldnames:
                raise ValueError("missing job_url column")
            for row in reader:
                normalized = normalize_job_url(row.get("job_url", ""))
                if normalized:
                    urls.add(normalized)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        raise RuntimeError(f"Cannot read job URL history from {path}: {exc}") from exc
    return urls


def _urls_from_json(path: Path) -> set[str]:
    urls: set[str] = set()
    try:
        with path.open(encoding="utf-8") as stream:
            rows = json.load(stream)
        if not isinstance(rows, list):
            raise ValueError("expected a JSON array")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("expected each job row to be an object")
            normalized = normalize_job_url(row.get("job_url", ""))
            if normalized:
                urls.add(normalized)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Cannot read job URL history from {path}: {exc}") from exc
    return urls


def load_seen_job_urls(job_search_dir: Path, before_date: date | datetime | str) -> set[str]:
    """Load exact URL identities from every dated export before ``before_date``.

    Current-date and future folders are excluded. Unreadable exports fail with
    their path instead of silently treating their jobs as new.
    """
    if isinstance(before_date, datetime):
        cutoff = before_date.date()
    elif isinstance(before_date, date):
        cutoff = before_date
    else:
        try:
            cutoff = date.fromisoformat(str(before_date))
        except ValueError as exc:
            raise ValueError(f"Invalid history cutoff date: {before_date}") from exc

    if not job_search_dir.exists():
        return set()
    try:
        run_dirs = list(job_search_dir.iterdir())
    except OSError as exc:
        raise RuntimeError(f"Cannot scan job URL history in {job_search_dir}: {exc}") from exc

    urls: set[str] = set()
    dated_dirs = []
    for run_dir in run_dirs:
        if not run_dir.is_dir():
            continue
        try:
            run_date = date.fromisoformat(run_dir.name)
        except ValueError:
            continue
        if run_date < cutoff:
            dated_dirs.append(run_dir)

    for run_dir in sorted(dated_dirs, key=lambda path: path.name):
        csv_files = _source_csvs(run_dir)
        if csv_files:
            for path in csv_files:
                urls.update(_urls_from_csv(path))
            continue

        json_files = _source_jsons(run_dir)
        if json_files:
            for path in json_files:
                urls.update(_urls_from_json(path))
            continue

        if any(run_dir.glob("Job_Search_*")):
            raise RuntimeError(
                f"No readable raw CSV/JSON export in dated history folder {run_dir}"
            )

    return urls
