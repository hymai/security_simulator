"""
Optional local persistence of training sessions, for the instructor dashboard
(instructor_dashboard.py). Off by default — see RECORDING_ENABLED.

Why opt-in: the README's privacy promise has been "nothing is written to the
server's filesystem." Persisting session records is a real change to that
guarantee, not just an implementation detail, so it's gated behind the
SIMULATOR_RECORD_SESSIONS environment variable (instructor sets it when
running a cohort where review is wanted) and, per session, a trainee-visible
checkbox in the sidebar — never silently on.

Because each scenario/answer-key is generated fresh per session (that's the
whole point — see pipeline.py), there's no stable "step" identity to compare
across sessions. What IS stable is which SOP source file a step draws from.
So the analytics here aggregate by source file ("trainees miss fire-response.md
steps on their first try 60% of the time"), not by literal step text — that's
the granularity that's actually actionable for an instructor and doesn't
require fragile text-matching across regenerated scenarios.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

RECORDING_ENABLED = bool(os.environ.get("SIMULATOR_RECORD_SESSIONS"))

_HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_DIR = os.path.join(_HERE, ".local")
DB_PATH = os.path.join(LOCAL_DIR, "sessions.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile TEXT NOT NULL,
    trainee TEXT NOT NULL,
    incident_types TEXT NOT NULL,
    scenario_text TEXT NOT NULL,
    total_steps INTEGER,
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    step_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    sources TEXT NOT NULL,
    actions TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS grade_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    step_number INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    trainee_answer TEXT NOT NULL,
    covered_ids TEXT NOT NULL,
    missing_ids TEXT NOT NULL,
    complete INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conn():
    os.makedirs(LOCAL_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- writes, called from security_simulator.py during a live session -------

def start_session(profile: str, trainee: str, incident_types: list[str],
                  scenario_text: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO sessions (profile, trainee, incident_types, scenario_text, "
            "started_at) VALUES (?, ?, ?, ?, ?)",
            (profile, trainee or "Anonymous", json.dumps(incident_types),
             scenario_text, _now()),
        )
        return cur.lastrowid


def save_steps(session_id: int, steps: list[dict]) -> None:
    with _conn() as c:
        c.execute("UPDATE sessions SET total_steps = ? WHERE id = ?",
                  (len(steps), session_id))
        for s in steps:
            c.execute(
                "INSERT INTO steps (session_id, step_number, title, sources, actions) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, s["step"], s["title"], json.dumps(s["sources"]),
                 json.dumps(s["actions"])),
            )


def record_grade_event(session_id: int, step_number: int, attempt: int,
                       trainee_answer: str, covered_ids, missing_ids,
                       complete: bool) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO grade_events (session_id, step_number, attempt, "
            "trainee_answer, covered_ids, missing_ids, complete, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, step_number, attempt, trainee_answer,
             json.dumps(sorted(covered_ids)), json.dumps(sorted(missing_ids)),
             int(complete), _now()),
        )


def complete_session(session_id: int) -> None:
    with _conn() as c:
        c.execute("UPDATE sessions SET completed_at = ? WHERE id = ?",
                  (_now(), session_id))


# --- reads, for instructor_dashboard.py -------------------------------------

def list_profiles_with_sessions() -> list[str]:
    with _conn() as c:
        rows = c.execute("SELECT DISTINCT profile FROM sessions ORDER BY profile").fetchall()
        return [r["profile"] for r in rows]


def list_sessions(profile: str | None = None) -> list[dict]:
    with _conn() as c:
        query = (
            "SELECT s.*, "
            "(SELECT COUNT(DISTINCT step_number) FROM grade_events "
            " WHERE session_id = s.id AND complete = 1) AS steps_completed "
            "FROM sessions s"
        )
        params: tuple = ()
        if profile:
            query += " WHERE s.profile = ?"
            params = (profile,)
        query += " ORDER BY s.started_at DESC"
        return [dict(r) for r in c.execute(query, params).fetchall()]


def session_detail(session_id: int) -> dict:
    with _conn() as c:
        session = dict(c.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone())
        steps = [dict(r) for r in c.execute(
            "SELECT * FROM steps WHERE session_id = ? ORDER BY step_number",
            (session_id,)).fetchall()]
        events = [dict(r) for r in c.execute(
            "SELECT * FROM grade_events WHERE session_id = ? "
            "ORDER BY step_number, attempt", (session_id,)).fetchall()]
        for s in steps:
            s["sources"] = json.loads(s["sources"])
            s["actions"] = json.loads(s["actions"])
        for e in events:
            e["covered_ids"] = json.loads(e["covered_ids"])
            e["missing_ids"] = json.loads(e["missing_ids"])
        session["steps"] = steps
        session["events"] = events
        return session


def most_missed_sources(profile: str | None = None, limit: int = 10) -> list[dict]:
    """Per SOP source file: how often a step drawing on it was NOT fully
    covered on the trainee's first attempt. Aggregated by file rather than by
    literal step text because each session's answer key is freshly generated
    (see module docstring) — the file is the one stable identity across runs.
    """
    with _conn() as c:
        query = (
            "SELECT st.session_id, st.step_number, st.sources, ge.complete "
            "FROM steps st "
            "JOIN grade_events ge "
            "  ON ge.session_id = st.session_id AND ge.step_number = st.step_number "
            "JOIN sessions se ON se.id = st.session_id "
            "WHERE ge.attempt = 1"
        )
        params: tuple = ()
        if profile:
            query += " AND se.profile = ?"
            params = (profile,)
        rows = c.execute(query, params).fetchall()

    tally: dict[str, dict] = {}
    for r in rows:
        sources = json.loads(r["sources"]) or ["(no source)"]
        for src in sources:
            entry = tally.setdefault(src, {"source": src, "attempts": 0, "misses": 0})
            entry["attempts"] += 1
            if not r["complete"]:
                entry["misses"] += 1

    results = list(tally.values())
    for r in results:
        r["miss_rate"] = r["misses"] / r["attempts"] if r["attempts"] else 0.0
    results.sort(key=lambda r: (-r["miss_rate"], -r["attempts"]))
    return results[:limit]
