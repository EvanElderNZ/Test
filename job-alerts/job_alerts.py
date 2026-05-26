#!/usr/bin/env python3
"""Daily job alert emailer — London pivot roles from coastal engineering."""

import os
import re
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RECIPIENT_EMAIL = "evanelder1992@gmail.com"

MAX_DAYS_OLD = 3        # Jobs posted within last N days (Adzuna/Reed)
RESULTS_PER_QUERY = 4   # Jobs fetched per search query per source
SALARY_FLOOR = 50_000   # Drop roles where max salary (actual or estimated) is below this

# ---------------------------------------------------------------------------
# Salary bands
# ---------------------------------------------------------------------------
SALARY_BANDS = [
    {
        "key":    "high",
        "label":  "Above £75k",
        "sublabel": "Senior / specialist roles",
        "color":  "#14532d",
        "accent": "#dcfce7",
        "badge_bg": "#166534",
    },
    {
        "key":    "mid",
        "label":  "£64k – £75k",
        "sublabel": "Mid-senior range",
        "color":  "#78350f",
        "accent": "#fef3c7",
        "badge_bg": "#92400e",
    },
    {
        "key":    "low",
        "label":  "Below £64k",
        "sublabel": "Entry to mid range",
        "color":  "#1e3a5f",
        "accent": "#dbeafe",
        "badge_bg": "#1d4ed8",
    },
    {
        "key":    "unknown",
        "label":  "Salary not disclosed",
        "sublabel": "Check listing for details",
        "color":  "#374151",
        "accent": "#f3f4f6",
        "badge_bg": "#6b7280",
    },
]

BAND_BY_KEY = {b["key"]: b for b in SALARY_BANDS}


def classify_salary(job: dict) -> str:
    low  = job.get("_salary_min")
    high = job.get("_salary_max")

    # Fall back to estimate when no real salary data is present
    if low is None and high is None:
        low  = job.get("_est_salary_min")
        high = job.get("_est_salary_max")
    if low is None and high is None:
        return "unknown"

    if low and high:
        reference = (low + high) / 2
    elif low:
        reference = low
    else:
        reference = high * 0.85
    if reference >= 75_000:
        return "high"
    if reference >= 64_000:
        return "mid"
    return "low"


# ---------------------------------------------------------------------------
# Search categories — tailored to the user's target pivot roles
# ---------------------------------------------------------------------------
CATEGORIES = [
    {
        # WTW Climate Practice, Climate X, Howden Climate Risk, Oliver Wyman
        "name": "Climate Risk & Physical Risk Analytics",
        "color": "#1e3a5f",
        "accent": "#dbeafe",
        "queries": [
            "climate risk consultant",
            "physical climate risk analyst",
            "flood scientist",
            "climate scientist risk",
            "loss modeller climate",
            "natural catastrophe consultant",
            "climate change consultant risk",
            "climate risk advisory",
            "nat cat risk consultant",
        ],
    },
    {
        # Hiscox Re ILS, Tangency Capital, Twelve Securis, Howden Capital Markets
        "name": "Insurance Linked Securities & Cat Bonds",
        "color": "#4c1d95",
        "accent": "#ede9fe",
        "queries": [
            "ILS analyst",
            "insurance linked securities",
            "catastrophe bond analyst",
            "cat bond portfolio",
            "catastrophe modeller reinsurance",
            "nat cat modeller insurance",
            "reinsurance ILS analyst",
            "ILS portfolio manager",
        ],
    },
    {
        # Macquarie, BlackRock Climate Infrastructure, NWF, Just Climate,
        # Climate Asset Management, CIP, Foresight Group, Octopus Energy Generation
        "name": "Green Infrastructure & Climate Investing",
        "color": "#14532d",
        "accent": "#dcfce7",
        "queries": [
            "infrastructure investment climate",
            "climate infrastructure investment associate",
            "green investment analyst",
            "energy transition investment",
            "clean energy investment analyst",
            "climate asset management",
            "offshore wind investment analyst",
            "infrastructure fund energy transition",
            "natural capital investment",
        ],
    },
    {
        # Schroders Greencoat, Octopus Energy Generation, SSE, Orsted
        "name": "Renewable Energy Asset Management",
        "color": "#b5451b",
        "accent": "#fff1e6",
        "queries": [
            "renewable energy asset manager",
            "offshore wind asset manager",
            "wind farm asset management",
            "renewable energy portfolio manager",
            "clean energy asset management",
            "offshore wind project manager",
        ],
    },
    {
        # DP World, Crown Estate Marine, port strategy roles
        "name": "Ports, Harbours & Maritime Strategy",
        "color": "#7c3d0f",
        "accent": "#fef3e2",
        "queries": [
            "port technical manager",
            "port strategy manager",
            "harbour engineer",
            "marine infrastructure manager",
            "port development manager",
            "maritime strategy",
            "waterfront development manager",
            "maritime project manager",
        ],
    },
    {
        # Stripe Climate, Amazon WW Sustainability, ESG advisory
        "name": "Climate Finance, ESG & Sustainability Strategy",
        "color": "#065f46",
        "accent": "#d1fae5",
        "queries": [
            "climate finance analyst",
            "sustainability strategy climate",
            "climate programme manager",
            "ESG climate analyst",
            "TCFD climate risk analyst",
            "natural capital finance",
            "sustainability consultant climate finance",
            "climate strategy manager",
        ],
    },
    {
        # National Wealth Fund, EBRD, development banks
        "name": "Development Banks & International Finance",
        "color": "#1b4332",
        "accent": "#d8f3dc",
        "queries": [
            "development bank climate",
            "infrastructure finance climate",
            "green bond analyst",
            "climate investment officer",
            "multilateral bank climate",
            "international finance climate environment",
        ],
    },
    {
        # Kept for completeness / direct market monitoring
        "name": "Coastal & Flood Risk Engineering",
        "color": "#0077b6",
        "accent": "#e0f2fe",
        "queries": [
            "coastal engineer",
            "flood risk engineer",
            "hydraulic modeller",
            "shoreline management",
            "marine civil engineer",
        ],
    },
]


# ---------------------------------------------------------------------------
# Job search: Adzuna
# ---------------------------------------------------------------------------

def _adzuna_search(query: str, location: str = "London") -> list[dict]:
    app_id  = os.environ.get("ADZUNA_APP_ID", "")
    app_key = os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        logger.warning("ADZUNA_APP_ID / ADZUNA_APP_KEY not set — skipping Adzuna")
        return []

    url = "https://api.adzuna.com/v1/api/jobs/gb/search/1"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": RESULTS_PER_QUERY,
        "what": query,
        "where": location,
        "max_days_old": MAX_DAYS_OLD,
        "sort_by": "date",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return [_normalise_adzuna(j) for j in r.json().get("results", [])]
    except Exception as exc:
        logger.error("Adzuna '%s': %s", query, exc)
        return []


def _normalise_adzuna(j: dict) -> dict:
    sal_min = j.get("salary_min")
    sal_max = j.get("salary_max")
    return {
        "title":       j.get("title", "").strip(),
        "company":     j.get("company", {}).get("display_name", "Unknown"),
        "location":    j.get("location", {}).get("display_name", "London"),
        "salary":      _fmt_salary(sal_min, sal_max),
        "_salary_min": sal_min,
        "_salary_max": sal_max,
        "url":         j.get("redirect_url", ""),
        "description": (j.get("description") or "")[:220].strip() + "…",
        "created":     j.get("created", ""),
        "source":      "Adzuna",
    }


# ---------------------------------------------------------------------------
# Job search: Reed.co.uk
# ---------------------------------------------------------------------------

def _reed_search(query: str, location: str = "London") -> list[dict]:
    api_key = os.environ.get("REED_API_KEY", "")
    if not api_key:
        return []

    url = "https://www.reed.co.uk/api/1.0/search"
    params = {
        "keywords": query,
        "locationName": location,
        "resultsToTake": RESULTS_PER_QUERY,
        "distanceFromLocation": 15,
    }
    try:
        r = requests.get(url, params=params, auth=(api_key, ""), timeout=15)
        r.raise_for_status()
        return [_normalise_reed(j) for j in r.json().get("results", [])]
    except Exception as exc:
        logger.error("Reed '%s': %s", query, exc)
        return []


def _normalise_reed(j: dict) -> dict:
    sal_min = j.get("minimumSalary")
    sal_max = j.get("maximumSalary")
    job_id  = j.get("jobId", "")
    return {
        "title":       j.get("jobTitle", "").strip(),
        "company":     j.get("employerName", "Unknown"),
        "location":    j.get("locationName", "London"),
        "salary":      _fmt_salary(sal_min, sal_max),
        "_salary_min": sal_min,
        "_salary_max": sal_max,
        "url":         f"https://www.reed.co.uk/jobs/-{job_id}" if job_id else "https://www.reed.co.uk",
        "description": (j.get("jobDescription") or "")[:220].strip() + "…",
        "created":     j.get("date", ""),
        "source":      "Reed",
    }


# ---------------------------------------------------------------------------
# Job search: Jooble
# ---------------------------------------------------------------------------

def _jooble_search(query: str, location: str = "London") -> list[dict]:
    api_key = os.environ.get("JOOBLE_API_KEY", "")
    if not api_key:
        return []

    url = f"https://jooble.org/api/{api_key}"
    payload = {
        "keywords": query,
        "location": "London, United Kingdom",  # explicit country to avoid London, Ontario etc.
        "page": 1,
        "ResultOnPage": RESULTS_PER_QUERY,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return [_normalise_jooble(j) for j in r.json().get("jobs", [])]
    except Exception as exc:
        logger.error("Jooble '%s': %s", query, exc)
        return []


def _normalise_jooble(j: dict) -> dict:
    sal_str = (j.get("salary") or "").strip()
    sal_min, sal_max = _parse_salary_string(sal_str)
    return {
        "title":       (j.get("title") or "").strip(),
        "company":     (j.get("company") or "Unknown").strip(),
        "location":    (j.get("location") or "London").strip(),
        "salary":      sal_str if sal_str else "Salary not specified",
        "_salary_min": sal_min,
        "_salary_max": sal_max,
        "url":         j.get("link", ""),
        "description": (j.get("snippet") or "")[:220].strip() + "…",
        "created":     j.get("updated", ""),
        "source":      "Jooble",
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_salary_string(s: str) -> tuple[Optional[float], Optional[float]]:
    """Parse salary strings like '£45,000 - £55,000' or '£50k per annum'."""
    if not s:
        return None, None
    cleaned = s.lower().replace(",", "").replace("£", "").replace("$", "")
    # Expand k notation: 50k → 50000
    cleaned = re.sub(r"(\d+(?:\.\d+)?)\s*k\b", lambda m: str(float(m.group(1)) * 1000), cleaned)
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", cleaned) if float(x) >= 5_000]
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    return min(nums[:2]), max(nums[:2])


def _fmt_salary(low: Optional[float], high: Optional[float]) -> str:
    if low and high:
        return f"£{int(low):,} – £{int(high):,}"
    if low:
        return f"from £{int(low):,}"
    if high:
        return f"up to £{int(high):,}"
    return "Salary not specified"


# Ordered most-to-least senior — first match wins
_SENIORITY_MAP: list[tuple[list[str], int]] = [
    (["chief ", " cto", " cfo", " ceo", " coo"],                                              115),
    (["vice president", " vp "],                                                               100),
    (["technical director", "associate director", "managing director",
      "director of", " director"],                                                              90),
    (["portfolio manager", "fund manager", "investment manager"],                               85),
    (["principal engineer", "principal consultant", "principal analyst",
      "principal adviser", "principal advisor"],                                                78),
    (["senior manager", "senior programme", "lead engineer", "lead consultant",
      "lead analyst", "programme manager", "senior associate"],                                 72),
    (["senior engineer", "senior consultant", "senior analyst", "senior adviser",
      "senior advisor", "senior specialist", "senior officer", "senior modeller"],             62),
    (["project manager", "project officer", "project lead", "programme officer",
      "investment associate", " executive "],                                                   58),
    (["engineer", "consultant", "analyst", "adviser", "advisor",
      "specialist", "officer", "coordinator", "modeller", " associate"],                       50),
    (["graduate", "junior ", "trainee", "assistant ", "entry level"],                          32),
]

# Sector premium added to seniority midpoint (£k) — reflects London market rates
_CATEGORY_PREMIUM: dict[str, int] = {
    "Insurance Linked Securities & Cat Bonds":      15,
    "Green Infrastructure & Climate Investing":     12,
    "Development Banks & International Finance":     8,
    "Renewable Energy Asset Management":             7,
    "Climate Risk & Physical Risk Analytics":        6,
    "Climate Finance, ESG & Sustainability Strategy": 4,
    "Ports, Harbours & Maritime Strategy":           3,
    "Coastal & Flood Risk Engineering":              0,
}


def _estimate_salary_range(job: dict) -> tuple[int, int]:
    """Return an estimated £10k salary range (low, high) for a role with no listed salary."""
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    cat_name = (job.get("_category") or {}).get("name", "")

    midpoint_k = 46  # default: mid-level individual contributor
    for keywords, salary_k in _SENIORITY_MAP:
        if any(kw in text for kw in keywords):
            midpoint_k = salary_k
            break

    midpoint_k += _CATEGORY_PREMIUM.get(cat_name, 0)

    # Snap to nearest £5k then build £10k range
    snapped = round(midpoint_k / 5) * 5
    return (snapped - 5) * 1_000, (snapped + 5) * 1_000


# ---------------------------------------------------------------------------
# Quality filters
# ---------------------------------------------------------------------------

# Reject any location that names a non-UK London (Ontario, Kentucky, etc.)
_NON_UK_LONDON_RE = re.compile(
    r"\b(?:ontario|canada|kentucky|ohio|tennessee|oregon|arkansas|virginia|"
    r"united\s+states|usa)\b"
    r"|,\s*(?:on|ky|oh|tn|or|ar|va|wv)\b",
    re.IGNORECASE,
)


def _is_london_uk(location: str) -> bool:
    """Return False only when the location explicitly names a non-UK London."""
    return not _NON_UK_LONDON_RE.search(location or "")


# Reject job titles that are clearly blue-collar, trades, hospitality, retail,
# basic admin, or otherwise have no plausible pivot from this background.
# Uses word-boundary matching to avoid false positives (e.g. "contractor" ≠ "cleaner").
_BLOCKED_TITLE_RE = re.compile(
    r"\b(?:"
    # Transport & logistics
    r"forklift|hgv"
    r"|(?:lorry|van|truck|bus|taxi|delivery|courier)\s+driver"
    r"|warehouse\s+(?:operative|worker|assistant|supervisor|picker|packer)"
    r"|crane\s+operator|stevedore|longshoreman|docker(?:hand)?"
    r"|picker|packer"
    # Trades & manual
    r"|electrician|plumber|carpenter|joiner|bricklayer|plasterer"
    r"|scaffolder|roofer|groundworker|tiler"
    r"|painter\s+(?:and\s+)?decorator"
    r"|gas\s+(?:fitter|installer|service\s+engineer)"
    r"|boiler\s+(?:engineer|technician|installer)"
    # Food & hospitality
    r"|(?:head|sous|commis|pastry|prep|section)\s+chef"
    r"|kitchen\s+(?:porter|assistant|hand)"
    r"|catering\s+(?:assistant|operative)"
    r"|waiter|waitress|bartender|barista|line\s+cook"
    # Retail & basic admin
    r"|retail\s+(?:assistant|advisor|manager)"
    r"|shop\s+(?:assistant|manager)|store\s+assistant"
    r"|sales\s+assistant|sales\s+advisor"
    r"|receptionist|cleaner|cleaning\s+(?:operative|supervisor)|caretaker"
    # Care & health (entry-level non-professional)
    r"|care\s+(?:assistant|worker)|support\s+worker"
    r"|healthcare\s+assistant|nursing\s+assistant|domiciliary"
    # Finance operations (non-professional)
    r"|bookkeeper|payroll\s+(?:administrator|officer|assistant)"
    # Physical security
    r"|security\s+guard"
    # Other
    r"|teaching\s+assistant|telesales"
    r")\b",
    re.IGNORECASE,
)


def _is_relevant_role(job: dict) -> bool:
    """Return False if the job title clearly signals an irrelevant blue-collar
    or unrelated role with no plausible pivot angle from this background."""
    return not _BLOCKED_TITLE_RE.search(job.get("title", ""))


def _deduplicate(jobs: list[dict]) -> list[dict]:
    seen_urls:   set[str] = set()
    seen_titles: set[str] = set()
    out = []
    for j in jobs:
        key_url   = j["url"].split("?")[0].rstrip("/")
        key_title = (j["title"] + j["company"]).lower().replace(" ", "")
        if key_url in seen_urls or key_title in seen_titles:
            continue
        seen_urls.add(key_url)
        seen_titles.add(key_title)
        out.append(j)
    return out


def _above_floor(job: dict) -> bool:
    """Return False only when we can confirm the role tops out below SALARY_FLOOR."""
    sal_max = job.get("_salary_max")
    est_max = job.get("_est_salary_max")
    if sal_max is not None and sal_max < SALARY_FLOOR:
        return False
    if sal_max is None and est_max is not None and est_max < SALARY_FLOOR:
        return False
    return True


def fetch_jobs_for_category(category: dict) -> list[dict]:
    jobs = []
    for query in category["queries"]:
        jobs.extend(_adzuna_search(query))
        jobs.extend(_reed_search(query))
        jobs.extend(_jooble_search(query))

    # Remove non-UK Londons and irrelevant/blue-collar titles before dedup
    jobs = [j for j in jobs if _is_london_uk(j.get("location", ""))]
    jobs = [j for j in jobs if _is_relevant_role(j)]

    deduped = _deduplicate(jobs)

    # Attach category so _estimate_salary_range can use the sector premium
    for j in deduped:
        j["_category"] = category
        if j["_salary_min"] is None and j["_salary_max"] is None:
            est_low, est_high = _estimate_salary_range(j)
            j["_est_salary_min"] = est_low
            j["_est_salary_max"] = est_high
            j["_salary_estimated"] = True
        else:
            j["_salary_estimated"] = False

    # Drop anything that clearly tops out below the salary floor
    deduped = [j for j in deduped if _above_floor(j)]

    return deduped[:10]


# ---------------------------------------------------------------------------
# Email HTML builder
# ---------------------------------------------------------------------------

_JOB_CARD = """
<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;
            padding:16px 18px;margin-bottom:12px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;
              flex-wrap:wrap;gap:6px;">
    <a href="{url}"
       style="font-size:15px;font-weight:600;color:#0c2340;text-decoration:none;flex:1;"
    >{title}</a>
    <div style="display:flex;gap:5px;flex-shrink:0;">
      <span style="font-size:10px;background:{cat_accent};color:{cat_color};
                   padding:2px 7px;border-radius:20px;font-weight:600;white-space:nowrap;">
        {category}
      </span>
      <span style="font-size:10px;background:#f1f5f9;color:#64748b;
                   padding:2px 7px;border-radius:20px;white-space:nowrap;">
        {source}
      </span>
    </div>
  </div>
  <div style="margin-top:6px;font-size:13px;color:#475569;">
    <strong>{company}</strong>&nbsp;·&nbsp;{location}
  </div>
  <div style="margin-top:4px;font-size:13px;font-weight:500;">{salary_html}</div>
  <div style="margin-top:8px;font-size:13px;color:#64748b;line-height:1.5;">{description}</div>
  <div style="margin-top:10px;">
    <a href="{url}"
       style="display:inline-block;background:#0c2340;color:#ffffff;font-size:12px;
              font-weight:600;padding:6px 14px;border-radius:5px;text-decoration:none;">
      View &amp; Apply →
    </a>
  </div>
</div>
"""

_BAND_SECTION = """
<div style="margin-bottom:36px;">
  <div style="background:{band_color};border-radius:8px;padding:12px 18px;margin-bottom:14px;">
    <span style="color:#ffffff;font-size:16px;font-weight:700;">{band_label}</span>
    <span style="color:rgba(255,255,255,0.75);font-size:12px;margin-left:10px;">{band_sublabel}</span>
    <span style="float:right;background:rgba(255,255,255,0.2);color:#ffffff;
                 font-size:12px;font-weight:600;padding:2px 10px;border-radius:20px;">
      {count} role{s}
    </span>
  </div>
  {cards}
  {empty_msg}
</div>
"""

_NO_JOBS_BAND = """
<div style="background:#f8fafc;border:1px dashed #cbd5e1;border-radius:8px;
            padding:14px;text-align:center;color:#94a3b8;font-size:13px;">
  No roles in this salary range in the last {days} days
</div>
"""

_SUMMARY_PILL = (
    '<span style="display:inline-block;background:{bg};color:#ffffff;'
    'font-size:12px;font-weight:600;padding:4px 12px;border-radius:20px;margin:3px;">'
    "{count} {label}</span>"
)


def _salary_html(job: dict) -> str:
    if not job.get("_salary_estimated"):
        return f'<span style="color:#16a34a;">{job["salary"]}</span>'
    est_low  = job.get("_est_salary_min", 0)
    est_high = job.get("_est_salary_max", 0)
    range_str = f"~£{int(est_low):,} – £{int(est_high):,}"
    return (
        f'<span style="color:#64748b;">{range_str}</span>'
        f'<span style="font-size:11px;color:#94a3b8;font-weight:400;margin-left:6px;">'
        f'estimated · salary not listed</span>'
    )


def build_email_html(results: list[dict]) -> str:
    today = datetime.now().strftime("%A %d %B %Y")

    # Flatten all jobs (_category already attached by fetch_jobs_for_category)
    all_jobs: list[dict] = []
    for r in results:
        for j in r["jobs"]:
            j = dict(j)
            j["_band"] = classify_salary(j)
            all_jobs.append(j)

    total = len(all_jobs)

    # Build per-band buckets
    buckets: dict[str, list[dict]] = {b["key"]: [] for b in SALARY_BANDS}
    for j in all_jobs:
        buckets[j["_band"]].append(j)

    # Summary pills
    summary_pills = ""
    for band in SALARY_BANDS:
        cnt = len(buckets[band["key"]])
        summary_pills += _SUMMARY_PILL.format(
            bg=band["badge_bg"], count=cnt, label=band["label"]
        )

    # Band sections
    band_sections = ""
    for band in SALARY_BANDS:
        jobs_in_band = buckets[band["key"]]
        if jobs_in_band:
            cards = "".join(
                _JOB_CARD.format(
                    title=j["title"],
                    company=j["company"],
                    location=j["location"],
                    salary_html=_salary_html(j),
                    description=j["description"],
                    url=j["url"],
                    source=j["source"],
                    category=j["_category"]["name"],
                    cat_color=j["_category"]["color"],
                    cat_accent=j["_category"]["accent"],
                )
                for j in jobs_in_band
            )
            empty_msg = ""
        else:
            cards     = ""
            empty_msg = _NO_JOBS_BAND.format(days=MAX_DAYS_OLD)

        band_sections += _BAND_SECTION.format(
            band_color=band["badge_bg"],
            band_label=band["label"],
            band_sublabel=band["sublabel"],
            count=len(jobs_in_band),
            s="" if len(jobs_in_band) == 1 else "s",
            cards=cards,
            empty_msg=empty_msg,
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Daily Job Alert — {today}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:680px;margin:24px auto;background:#f1f5f9;">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#0c2340 0%,#1a4a7a 100%);
                border-radius:12px 12px 0 0;padding:28px 32px 24px;">
      <h1 style="margin:0;font-size:22px;color:#ffffff;font-weight:700;">
        London Job Alerts
      </h1>
      <p style="margin:6px 0 0;color:#90c4f9;font-size:13px;">{today}</p>
      <div style="margin-top:16px;">
        <div style="color:rgba(255,255,255,0.8);font-size:12px;margin-bottom:8px;
                    text-transform:uppercase;letter-spacing:.5px;">
          {total} role{'s' if total != 1 else ''} by salary band
        </div>
        {summary_pills}
      </div>
    </div>

    <!-- Body -->
    <div style="background:#ffffff;padding:28px 32px;">
      {band_sections}
    </div>

    <!-- Footer -->
    <div style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:18px 32px;
                border-radius:0 0 12px 12px;text-align:center;">
      <p style="margin:0;font-size:11px;color:#94a3b8;">
        Delivered daily · Adzuna, Reed.co.uk &amp; Jooble · London area, UK ·
        {len(CATEGORIES)} categories · roles below £50k excluded
      </p>
    </div>

  </div>
</body>
</html>"""


def build_plain_text(results: list[dict]) -> str:
    today = datetime.now().strftime("%A %d %B %Y")

    # Flatten + classify (_category already set by fetch_jobs_for_category)
    all_jobs: list[dict] = []
    for r in results:
        for j in r["jobs"]:
            j = dict(j)
            j["_band"] = classify_salary(j)
            all_jobs.append(j)

    def _plain_salary(j: dict) -> str:
        if not j.get("_salary_estimated"):
            return j["salary"]
        est_low  = j.get("_est_salary_min", 0)
        est_high = j.get("_est_salary_max", 0)
        return f"~£{int(est_low):,} – £{int(est_high):,} (estimated)"

    lines = [f"DAILY JOB ALERTS — LONDON   {today}", "=" * 60]
    for band in SALARY_BANDS:
        jobs_in_band = [j for j in all_jobs if j["_band"] == band["key"]]
        lines.append(f"\n{'=' * 60}")
        lines.append(f"{band['label'].upper()}  ({len(jobs_in_band)} role{'s' if len(jobs_in_band) != 1 else ''})")
        lines.append("=" * 60)
        if not jobs_in_band:
            lines.append("  No roles in this salary range in the last 3 days.")
        for j in jobs_in_band:
            lines.append(f"\n  {j['title']}")
            lines.append(f"  {j['company']}  |  {j['location']}  |  {_plain_salary(j)}")
            lines.append(f"  Category: {j['_category']['name']}")
            lines.append(f"  {j['url']}")
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(html_body: str, plain_body: str, dry_run: bool = False) -> bool:
    smtp_user = os.environ.get("GMAIL_USER", "")
    smtp_pass = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not smtp_user or not smtp_pass:
        logger.error("GMAIL_USER / GMAIL_APP_PASSWORD not set")
        return False

    today = datetime.now().strftime("%d %b %Y")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"London Job Alerts — {today}"
    msg["From"]    = smtp_user
    msg["To"]      = RECIPIENT_EMAIL

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    if dry_run:
        logger.info("[DRY RUN] Would send email to %s", RECIPIENT_EMAIL)
        return True

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, RECIPIENT_EMAIL, msg.as_string())
        logger.info("Email sent to %s", RECIPIENT_EMAIL)
        return True
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> dict:
    logger.info("Starting job search — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    results = []
    for cat in CATEGORIES:
        logger.info("Searching: %s", cat["name"])
        jobs = fetch_jobs_for_category(cat)
        logger.info("  Found %d jobs", len(jobs))
        results.append({"category": cat, "jobs": jobs})

    html  = build_email_html(results)
    plain = build_plain_text(results)
    ok    = send_email(html, plain, dry_run=dry_run)

    total = sum(len(r["jobs"]) for r in results)
    return {"total_jobs": total, "sent": ok, "results": results, "html": html}


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    outcome = run(dry_run=dry)
    print(f"\nDone: {outcome['total_jobs']} jobs, email sent={outcome['sent']}")
