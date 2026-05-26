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

# ---------------------------------------------------------------------------
# Search categories — tailored to coastal engineering + pivot roles in London
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

MAX_DAYS_OLD = 3       # Jobs posted within last N days
RESULTS_PER_QUERY = 4  # Jobs fetched per search query


# ---------------------------------------------------------------------------
# Job search: Adzuna
# ---------------------------------------------------------------------------

def _adzuna_search(query: str, location: str = "London") -> list[dict]:
    app_id = os.environ.get("ADZUNA_APP_ID", "")
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
        raw = r.json().get("results", [])
        return [_normalise_adzuna(j) for j in raw]
    except Exception as exc:
        logger.error("Adzuna '%s': %s", query, exc)
        return []


def _normalise_adzuna(j: dict) -> dict:
    sal_min = j.get("salary_min")
    sal_max = j.get("salary_max")
    salary = _fmt_salary(sal_min, sal_max)
    return {
        "title": j.get("title", "").strip(),
        "company": j.get("company", {}).get("display_name", "Unknown"),
        "location": j.get("location", {}).get("display_name", "London"),
        "salary": salary,
        "url": j.get("redirect_url", ""),
        "description": (j.get("description") or "")[:220].strip() + "…",
        "created": j.get("created", ""),
        "source": "Adzuna",
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
        raw = r.json().get("results", [])
        return [_normalise_reed(j) for j in raw]
    except Exception as exc:
        logger.error("Reed '%s': %s", query, exc)
        return []


def _normalise_reed(j: dict) -> dict:
    salary = _fmt_salary(j.get("minimumSalary"), j.get("maximumSalary"))
    job_id = j.get("jobId", "")
    return {
        "title": j.get("jobTitle", "").strip(),
        "company": j.get("employerName", "Unknown"),
        "location": j.get("locationName", "London"),
        "salary": salary,
        "url": f"https://www.reed.co.uk/jobs/-{job_id}" if job_id else "https://www.reed.co.uk",
        "description": (j.get("jobDescription") or "")[:220].strip() + "…",
        "created": j.get("date", ""),
        "source": "Reed",
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
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out = []
    for j in jobs:
        key_url = j["url"].split("?")[0].rstrip("/")
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
    # Cap at 10 per category to keep email manageable
    return deduped[:10]


# ---------------------------------------------------------------------------
# Email HTML builder
# ---------------------------------------------------------------------------

_JOB_CARD = """
<div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;
            padding:16px 18px;margin-bottom:12px;">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;">
    <a href="{url}" style="font-size:15px;font-weight:600;color:{color};
                           text-decoration:none;flex:1;margin-right:8px;">{title}</a>
    <span style="font-size:11px;background:{accent};color:{color};
                 padding:3px 8px;border-radius:20px;white-space:nowrap;font-weight:500;">
      {source}
    </span>
  </div>
  <div style="margin-top:6px;font-size:13px;color:#475569;">
    <strong>{company}</strong> &nbsp;·&nbsp; {location}
  </div>
  <div style="margin-top:4px;font-size:13px;color:#16a34a;font-weight:500;">{salary}</div>
  <div style="margin-top:8px;font-size:13px;color:#64748b;line-height:1.5;">{description}</div>
  <div style="margin-top:10px;">
    <a href="{url}"
       style="display:inline-block;background:{color};color:#ffffff;font-size:12px;
              font-weight:600;padding:6px 14px;border-radius:5px;text-decoration:none;">
      View &amp; Apply →
    </a>
  </div>
</div>
"""

_CATEGORY_BLOCK = """
<div style="margin-bottom:32px;">
  <div style="border-left:4px solid {color};padding-left:12px;margin-bottom:16px;">
    <h2 style="margin:0;font-size:17px;color:{color};font-family:sans-serif;">{name}</h2>
    <span style="font-size:12px;color:#94a3b8;">{count} posting{s} found</span>
  </div>
  {cards}
  {empty_msg}
</div>
"""

_NO_JOBS = """
<div style="background:#f8fafc;border:1px dashed #cbd5e1;border-radius:8px;
            padding:14px;text-align:center;color:#94a3b8;font-size:13px;">
  No new postings in the last {days} days — check back tomorrow
</div>
"""


def build_email_html(results: list[dict]) -> str:
    today = datetime.now().strftime("%A %d %B %Y")
    total = sum(len(r["jobs"]) for r in results)

    category_blocks = ""
    for r in results:
        cat = r["category"]
        jobs = r["jobs"]
        if jobs:
            cards = "".join(
                _JOB_CARD.format(
                    title=j["title"],
                    company=j["company"],
                    location=j["location"],
                    salary=j["salary"],
                    description=j["description"],
                    url=j["url"],
                    source=j["source"],
                    color=cat["color"],
                    accent=cat["accent"],
                )
                for j in jobs
            )
            empty_msg = ""
        else:
            cards = ""
            empty_msg = _NO_JOBS.format(days=MAX_DAYS_OLD)

        category_blocks += _CATEGORY_BLOCK.format(
            name=cat["name"],
            color=cat["color"],
            count=len(jobs),
            s="" if len(jobs) == 1 else "s",
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
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,
             'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:680px;margin:24px auto;background:#f1f5f9;">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#0c2340 0%,#1a4a7a 100%);
                border-radius:12px 12px 0 0;padding:28px 32px 24px;">
      <h1 style="margin:0;font-size:22px;color:#ffffff;font-weight:700;">
        London Job Alerts
      </h1>
      <p style="margin:6px 0 0;color:#90c4f9;font-size:13px;">{today}</p>
      <div style="margin-top:14px;background:rgba(255,255,255,0.1);border-radius:8px;
                  padding:10px 16px;display:inline-block;">
        <span style="color:#ffffff;font-size:14px;font-weight:600;">
          {total} new role{'' if total == 1 else 's'} across {len(results)} categories
        </span>
      </div>
    </div>

    <!-- Body -->
    <div style="background:#ffffff;padding:28px 32px;">
      {category_blocks}
    </div>

    <!-- Footer -->
    <div style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:18px 32px;
                border-radius:0 0 12px 12px;text-align:center;">
      <p style="margin:0;font-size:11px;color:#94a3b8;">
        Delivered daily · Searches: Adzuna &amp; Reed.co.uk · London area, UK
      </p>
    </div>

  </div>
</body>
</html>"""


def build_plain_text(results: list[dict]) -> str:
    today = datetime.now().strftime("%A %d %B %Y")
    lines = [f"DAILY JOB ALERTS — LONDON   {today}", "=" * 60]
    for r in results:
        cat = r["category"]
        jobs = r["jobs"]
        lines.append(f"\n{cat['name'].upper()}")
        lines.append("-" * 40)
        if not jobs:
            lines.append(f"  No new postings in the last {MAX_DAYS_OLD} days.")
        for j in jobs:
            lines.append(f"\n  {j['title']}")
            lines.append(f"  {j['company']}  |  {j['location']}  |  {j['salary']}")
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
    msg["From"] = smtp_user
    msg["To"] = RECIPIENT_EMAIL

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

    html = build_email_html(results)
    plain = build_plain_text(results)
    ok = send_email(html, plain, dry_run=dry_run)

    total = sum(len(r["jobs"]) for r in results)
    return {"total_jobs": total, "sent": ok, "results": results, "html": html}


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    outcome = run(dry_run=dry)
    print(f"\nDone: {outcome['total_jobs']} jobs, email sent={outcome['sent']}")
