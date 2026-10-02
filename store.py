"""SQLite storage: the proposal queue and the memory of past decisions.

This is what makes re-runs safe. Every proposal has a stable key. Once a key is
approved or rejected it is never proposed again.
"""
import json
import sqlite3
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, locations INTEGER, accounts INTEGER,
    generated INTEGER, new INTEGER, skipped_decided INTEGER, matches TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
    key TEXT PRIMARY KEY,
    action TEXT, title TEXT, account_id TEXT, location_slug TEXT, confidence TEXT,
    changes TEXT, evidence TEXT, ops TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    first_run INTEGER, last_run INTEGER,
    decided_at TEXT, decision_note TEXT, result TEXT
);
"""

# pending   waiting for a reviewer
# approved  reviewer approved and the CRM write succeeded
# rejected  reviewer said no
# failed    reviewer approved but the CRM write failed (can be retried)
# stale     the CRM record changed after we proposed (re-run the pipeline)
# obsolete  was pending, but the latest run no longer proposes it
DECIDED = ("approved", "rejected")
JSON_COLUMNS = ("changes", "evidence", "ops", "result")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def connect(path=None):
    path = path or config.DB_PATH
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _row(row):
    data = dict(row)
    for column in JSON_COLUMNS:
        data[column] = json.loads(data[column]) if data.get(column) else None
    return data


def record_run(conn, proposals, matches, n_locations, n_accounts):
    """Save one pipeline run. Returns counts of what happened to each proposal."""
    stats = {"generated": len(proposals), "new": 0, "refreshed": 0, "skipped_decided": 0, "obsolete": 0}
    with conn:  # one transaction: all of it lands or none of it
        cursor = conn.execute(
            "INSERT INTO runs (started_at, locations, accounts, generated, matches) VALUES (?, ?, ?, ?, ?)",
            (now(), n_locations, n_accounts, len(proposals), json.dumps(matches)))
        run_id = cursor.lastrowid

        for p in proposals:
            existing = conn.execute("SELECT status FROM proposals WHERE key = ?", (p.key,)).fetchone()
            blobs = (json.dumps(p.changes), json.dumps(p.evidence), json.dumps(p.ops))
            if existing is None:
                conn.execute(
                    "INSERT INTO proposals (key, action, title, account_id, location_slug, confidence, "
                    "changes, evidence, ops, status, first_run, last_run) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (p.key, p.action, p.title, p.account_id, p.location_slug, p.confidence, *blobs, run_id, run_id))
                stats["new"] += 1
            elif existing["status"] in DECIDED:
                stats["skipped_decided"] += 1  # already decided: never re-propose
            else:
                # Still open (pending, failed, stale, obsolete): refresh it with today's data.
                conn.execute(
                    "UPDATE proposals SET title = ?, confidence = ?, changes = ?, evidence = ?, ops = ?, "
                    "status = 'pending', last_run = ? WHERE key = ?",
                    (p.title, p.confidence, *blobs, run_id, p.key))
                stats["refreshed"] += 1

        # Anything still pending that this run did not produce is out of date.
        cursor = conn.execute("UPDATE proposals SET status = 'obsolete' WHERE status = 'pending' AND last_run != ?", (run_id,))
        stats["obsolete"] = cursor.rowcount
        conn.execute("UPDATE runs SET new = ?, skipped_decided = ? WHERE id = ?",
                     (stats["new"], stats["skipped_decided"], run_id))
    return stats


def list_proposals(conn, status=None):
    sql = "SELECT * FROM proposals"
    args = ()
    if status:
        sql += " WHERE status = ?"
        args = (status,)
    order = " ORDER BY CASE confidence WHEN 'low' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, action, title"
    return [_row(r) for r in conn.execute(sql + order, args)]


def get_proposal(conn, key):
    row = conn.execute("SELECT * FROM proposals WHERE key = ?", (key,)).fetchone()
    return _row(row) if row else None


def set_status(conn, key, status, note="", result=None):
    with conn:
        conn.execute("UPDATE proposals SET status = ?, decided_at = ?, decision_note = ?, result = ? WHERE key = ?",
                     (status, now(), note, json.dumps(result) if result is not None else None, key))


def status_counts(conn):
    return {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM proposals GROUP BY status")}


def last_run(conn):
    row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    data = dict(row)
    data["matches"] = json.loads(data["matches"] or "[]")
    return data
