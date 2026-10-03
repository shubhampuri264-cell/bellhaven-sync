"""Review app. A reviewer reads each proposal and its evidence, then approves or rejects.

    python app.py     then open http://127.0.0.1:5000

Approve is the only thing in this project that writes to the CRM.
"""
import os

from flask import Flask, flash, redirect, render_template, request, url_for

import pipeline
import store
from apply import StaleProposal, apply_proposal
from crm import CRMClient

app = Flask(__name__)
app.secret_key = os.urandom(16)  # only used for the one-line status messages

STATUSES = ["pending", "approved", "rejected", "failed", "stale", "obsolete"]


@app.get("/")
def index():
    conn = store.connect()
    status = request.args.get("status", "pending")
    return render_template(
        "index.html",
        proposals=store.list_proposals(conn, status),
        counts=store.status_counts(conn),
        statuses=STATUSES,
        current=status,
        run=store.last_run(conn),
    )


@app.get("/locations")
def locations():
    conn = store.connect()
    return render_template("locations.html", run=store.last_run(conn), counts=store.status_counts(conn),
                           statuses=STATUSES, current="locations")


@app.post("/run")
def run_pipeline():
    try:
        _, stats = pipeline.run()
        flash(f"Pipeline ran: {stats['new']} new, {stats['skipped_decided']} already decided and skipped, "
              f"{stats['refreshed']} refreshed, {stats['obsolete']} obsolete.")
    except Exception as error:
        flash(f"Pipeline failed: {type(error).__name__}: {error}")
    return redirect(url_for("index"))


@app.post("/proposals/<key>/approve")
def approve(key):
    conn = store.connect()
    proposal = store.get_proposal(conn, key)
    note = request.form.get("note", "")
    if not proposal or proposal["status"] not in ("pending", "failed", "stale"):
        flash("That proposal is not open for approval.")
        return redirect(url_for("index"))
    try:
        result = apply_proposal(CRMClient(), proposal)
        store.set_status(conn, key, "approved", note, result)
        flash(f"Approved and written to the CRM: {proposal['title']}")
    except Exception as error:
        status = "stale" if isinstance(error, StaleProposal) else "failed"
        store.set_status(conn, key, status, note, {"error": f"{type(error).__name__}: {error}"}, keep_decided=True)
        flash(f"NOT written ({status}): {error}")
    return redirect(url_for("index"))


@app.post("/proposals/<key>/reject")
def reject(key):
    conn = store.connect()
    proposal = store.get_proposal(conn, key)
    if proposal and proposal["status"] in ("pending", "failed", "stale"):
        store.set_status(conn, key, "rejected", request.form.get("note", ""))
        flash(f"Rejected: {proposal['title']}. It will not be proposed again.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=False)
