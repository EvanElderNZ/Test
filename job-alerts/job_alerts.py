#!/usr/bin/env python3
"""Daily job alert emailer — London coastal engineering & pivot roles."""

import os
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

MAX_DAYS_OLD = 3       # Jobs posted within last N days
RESULTS_PER_QUERY = 4  # Jobs fetched per search query

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
    if low is None and high is None:
        return "unknown"
    if low and high:
        reference = (low + high) / 2
    elif low:
        reference = low
    else:
        reference = high * 0.85  # only max known — assume slightly below
    if reference >= 75_000:
        return "high"
    if reference >= 64_000:
        return "mid"
    return "low"


# ---------------------------------------------------------------------------
# Search categories — coastal engineering + pivot roles + ports/infrastructure
# ---------------------------------------------------------------------------
CATEGORIES = [
    {
        "name": "Coastal & Flood Risk Engineering",
        "color": "#0077b6",
        "accent": "#e0f2fe",
        "queries": [
            "coastal engineer",
            "flood risk engineer",
            "hydraulic modeller",
            "shoreline management",
            "marine civil engineer",
            "coastal geomorphology",
        ],
    },
    {
        "name": "Ports, Harbours & Infrastructure Development",
        "color": "#7c3d0f",
        "accent": "#fef3e2",
        "queries": [
            "port engineer",
            "harbour engineer",
            "marine infrastructure",
            "port development",
            "maritime infrastructure project manager",
            "waterfront development engineer",
            "port authority engineer",
            "civil infrastructure project manager",
        ],
    },
    {
        "name": "Development Banks & International Finance",
        "color": "#1b4332",
        "accent": "#d8f3dc",
        "queries": [
            "climate risk analyst bank",
            "green finance",
            "infrastructure finance environment",
            "development bank climate",
            "ESG infrastructure analyst",
            "climate investment officer",
        ],
    },
    {
        "name": "Insurance & Catastrophe Risk",
        "color": "#6a0572",
        "accent": "#f3e8ff",
        "queries": [
            "catastrophe risk analyst",
            "nat cat modeller",
            "physical climate risk analyst",
            "flood risk insurance",
            "climate risk underwriter",
        ],
    },
    {
        "name": "Renewable Energy",
        "color": "#b5451b",
        "accent": "#fff1e6",
        "queries": [
            "offshore wind engineer",
            "renewable energy project manager",
            "marine energy",
            "offshore wind project",
            "tidal energy engineer",
        ],
    },
    {
        "name": "ESG, Sustainability & Climate Finance",
        "color": "#0d47a1",
        "accent": "#e3f2fd",
        "queries": [
            "ESG analyst",
            "sustainability analyst climate",
            "TCFD climate risk",
            "natural capital analyst",
            "climate change advisor finance",
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
# Helpers
# ---------------------------------------------------------------------------

def _fmt_salary(low: Optional[float], high: Optional[float]) -> str:
    if low and high:
        return f"£{int(low):,} – £{int(high):,}"
    if low:
        return f"from £{int(low):,}"
    if high:
        return f"up to £{int(high):,}"
    return "Salary not specified"


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


def fetch_jobs_for_category(category: dict) -> list[dict]:
    jobs = []
    for query in category["queries"]:
        jobs.extend(_adzuna_search(query))
        jobs.extend(_reed_search(query))
    deduped = _deduplicate(jobs)
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
  <div style="margin-top:4px;font-size:13px;color:#16a34a;font-weight:500;">{salary}</div>
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


def build_email_html(results: list[dict]) -> str:
    today = datetime.now().strftime("%A %d %B %Y")

    # Flatten all jobs, tagging each with its category
    all_jobs: list[dict] = []
    for r in results:
        for j in r["jobs"]:
            j = dict(j)
            j["_category"] = r["category"]
            j["_band"]     = classify_salary(j)
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
                    salary=j["salary"],
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
        Delivered daily · Adzuna &amp; Reed.co.uk · London area, UK ·
        {len(CATEGORIES)} categories searched
      </p>
    </div>

  </div>
</body>
</html>"""


def build_plain_text(results: list[dict]) -> str:
    today = datetime.now().strftime("%A %d %B %Y")

    # Flatten + classify
    all_jobs: list[dict] = []
    for r in results:
        for j in r["jobs"]:
            j = dict(j)
            j["_category"] = r["category"]
            j["_band"]     = classify_salary(j)
            all_jobs.append(j)

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
            lines.append(f"  {j['company']}  |  {j['location']}  |  {j['salary']}")
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
