"""Flask web app — job alert dashboard and manual trigger."""

import os
import threading
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from job_alerts import CATEGORIES, build_email_html, build_plain_text, fetch_jobs_for_category, send_email

app = Flask(__name__)

# In-memory store for last run state
_state: dict = {"last_run": None, "total_jobs": 0, "results": [], "running": False}


def _run_search(send: bool = True, dry_run: bool = False) -> dict:
    _state["running"] = True
    results = []
    for cat in CATEGORIES:
        jobs = fetch_jobs_for_category(cat)
        results.append({"category": cat, "jobs": jobs})

    html = build_email_html(results)
    plain = build_plain_text(results)
    sent = False
    if send:
        sent = send_email(html, plain, dry_run=dry_run)

    _state.update(
        {
            "last_run": datetime.now().isoformat(),
            "total_jobs": sum(len(r["jobs"]) for r in results),
            "results": results,
            "html": html,
            "sent": sent,
            "running": False,
        }
    )
    return _state


@app.route("/")
def index():
    return render_template("index.html", state=_state, categories=CATEGORIES)


@app.route("/preview")
def preview():
    """Render the current email HTML preview directly in the browser."""
    if not _state.get("html"):
        results = []
        for cat in CATEGORIES:
            jobs = fetch_jobs_for_category(cat)
            results.append({"category": cat, "jobs": jobs})
        html = build_email_html(results)
        _state["html"] = html
        _state["results"] = results
        _state["total_jobs"] = sum(len(r["jobs"]) for r in results)
    return _state["html"]


@app.route("/trigger", methods=["POST"])
def trigger():
    """Manually trigger a search + email send."""
    if _state.get("running"):
        return jsonify({"error": "Already running"}), 409

    dry_run = request.json.get("dry_run", False) if request.is_json else False
    thread = threading.Thread(target=_run_search, kwargs={"send": True, "dry_run": dry_run})
    thread.daemon = True
    thread.start()
    return jsonify({"status": "started"})


@app.route("/status")
def status():
    return jsonify(
        {
            "last_run": _state.get("last_run"),
            "total_jobs": _state.get("total_jobs", 0),
            "sent": _state.get("sent"),
            "running": _state.get("running", False),
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
