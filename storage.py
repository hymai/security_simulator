"""
Optional local persistence of training sessions, for the instructor dashboard
(instructor_dashboard.py). Off by default — see RECORDING_ENABLED.

Why opt-in: the README's privacy promise has been "nothing is written to the
server's filesystem." Persisting session records is a real change to that
guarantee, not just an implementation detail, so it's gated behind the
CERTUS_RECORD_SESSIONS environment variable (instructor sets it when
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

RECORDING_ENABLED = bool(os.environ.get("CERTUS_RECORD_SESSIONS"))

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

-- Assessment-mode columns (iteration 4) live in _SESSION_COLUMNS below and are
-- ALTER'd in by _migrate(), so a pre-assessment sessions.db upgrades in place.

CREATE TABLE IF NOT EXISTS retention_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile TEXT NOT NULL,
    trainee_key TEXT NOT NULL,
    trainee_display TEXT NOT NULL,
    source TEXT NOT NULL,
    easiness REAL NOT NULL,
    interval_days REAL NOT NULL,
    repetitions INTEGER NOT NULL,
    last_quality INTEGER NOT NULL,
    last_session_id INTEGER REFERENCES sessions(id),
    last_reviewed_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    UNIQUE (profile, trainee_key, source)
);
"""


# Columns added after the original schema shipped. Applied via ALTER TABLE on
# every connection open (cheap: a PRAGMA read), so both a fresh DB and one
# recorded before assessment mode existed end up with the same shape.
#   mode      — 'training' (Socratic tutor) or 'assessment' (scored, no hints)
#   score     — assessment only: mean per-step coverage fraction, 0.0-1.0
#   passed    — assessment only: 1/0 verdict against the session's threshold
#   settings  — assessment only: JSON snapshot of the settings the verdict was
#               judged under (threshold, attempt/time limits, mandate) — stored
#               per session so later config edits can't rewrite past evidence
#   override_passed/override_note/override_at — instructor appeal path: the
#               original verdict is never mutated; an override sits alongside
#               it and both appear in the evidence export
_SESSION_COLUMNS = {
    "mode": "TEXT NOT NULL DEFAULT 'training'",
    "score": "REAL",
    "passed": "INTEGER",
    "settings": "TEXT",
    "override_passed": "INTEGER",
    "override_note": "TEXT",
    "override_at": "TEXT",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(conn) -> None:
    have = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    for col, decl in _SESSION_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {decl}")


@contextmanager
def _conn():
    os.makedirs(LOCAL_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- writes, called from certus.py during a live session -------------------

def start_session(profile: str, trainee: str, incident_types: list[str],
                  scenario_text: str, mode: str = "training",
                  settings: dict | None = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO sessions (profile, trainee, incident_types, scenario_text, "
            "started_at, mode, settings) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (profile, trainee or "Anonymous", json.dumps(incident_types),
             scenario_text, _now(), mode,
             json.dumps(settings) if settings else None),
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


def finish_assessment(session_id: int, score: float, passed: bool) -> None:
    """Close an assessment session with its verdict. Unlike training,
    completed_at here means 'the assessment ended' (all steps exhausted or
    time expired), not 'every step was fully covered'."""
    with _conn() as c:
        c.execute(
            "UPDATE sessions SET completed_at = ?, score = ?, passed = ? "
            "WHERE id = ?", (_now(), score, int(passed), session_id))


def override_assessment(session_id: int, passed: bool, note: str) -> None:
    """Instructor appeal path. Deliberately additive: the machine verdict
    (score/passed) is never rewritten — the override sits alongside it and
    the evidence export shows both, so an audit trail can't be silently
    cleaned up."""
    with _conn() as c:
        c.execute(
            "UPDATE sessions SET override_passed = ?, override_note = ?, "
            "override_at = ? WHERE id = ?",
            (int(passed), note.strip(), _now(), session_id))


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


# --- retention state (spaced repetition), for retention.py ------------------
#
# retention_state is a materialized view, not a source of truth: it is fully
# derivable from sessions/steps/grade_events by retention.rebuild_all(), so it
# can be dropped and replayed at any time (e.g. after tuning SM-2 constants).
# All SM-2 math lives in retention.py; this section is plain CRUD only.

def normalize_trainee(name: str) -> str:
    """Single place free-text trainee names become a matching key. Python
    casefold, not SQL LOWER() — SQLite's LOWER is ASCII-only and names
    aren't."""
    return (name or "").strip().casefold()


def upsert_retention_state(profile: str, trainee_key: str, trainee_display: str,
                           source: str, easiness: float, interval_days: float,
                           repetitions: int, quality: int, session_id: int,
                           reviewed_at: str, due_at: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO retention_state (profile, trainee_key, trainee_display, "
            "source, easiness, interval_days, repetitions, last_quality, "
            "last_session_id, last_reviewed_at, due_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (profile, trainee_key, source) DO UPDATE SET "
            "trainee_display = excluded.trainee_display, "
            "easiness = excluded.easiness, "
            "interval_days = excluded.interval_days, "
            "repetitions = excluded.repetitions, "
            "last_quality = excluded.last_quality, "
            "last_session_id = excluded.last_session_id, "
            "last_reviewed_at = excluded.last_reviewed_at, "
            "due_at = excluded.due_at",
            (profile, trainee_key, trainee_display, source, easiness,
             interval_days, repetitions, quality, session_id, reviewed_at, due_at),
        )


def get_retention_states(profile: str, trainee_key: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM retention_state WHERE profile = ? AND trainee_key = ?",
            (profile, trainee_key)).fetchall()
        return [dict(r) for r in rows]


def due_retention_states(profile: str | None = None, trainee_key: str | None = None,
                         as_of: str | None = None) -> list[dict]:
    """Due/overdue rows, most overdue first. trainee_key=None spans all
    trainees (instructor drill queue); a key scopes to one (sidebar panel).
    ISO-8601 UTC timestamps compare correctly as text."""
    query = "SELECT * FROM retention_state WHERE due_at <= ?"
    params: list = [as_of or _now()]
    if profile:
        query += " AND profile = ?"
        params.append(profile)
    if trainee_key:
        query += " AND trainee_key = ?"
        params.append(trainee_key)
    query += " ORDER BY due_at ASC"
    with _conn() as c:
        return [dict(r) for r in c.execute(query, params).fetchall()]


def delete_retention_states(profile: str | None = None) -> None:
    with _conn() as c:
        if profile:
            c.execute("DELETE FROM retention_state WHERE profile = ?", (profile,))
        else:
            c.execute("DELETE FROM retention_state")


def sessions_citing_sources(profile: str, trainee_key: str | None = None) -> list[dict]:
    """Every (session, step) row with its session's incident_types and the
    step's sources, both still JSON-encoded — decoded by the caller in Python,
    the same technique most_missed_sources uses. Feeds the 'which incident
    types historically co-occur with this SOP source' suggestion."""
    query = (
        "SELECT se.id AS session_id, se.trainee, se.incident_types, st.sources "
        "FROM steps st JOIN sessions se ON se.id = st.session_id "
        "WHERE se.profile = ?"
    )
    params: list = [profile]
    rows_out = []
    with _conn() as c:
        for r in c.execute(query, params).fetchall():
            if trainee_key and normalize_trainee(r["trainee"]) != trainee_key:
                continue
            rows_out.append(dict(r))
    return rows_out


def completed_sessions(profile: str | None = None) -> list[dict]:
    """Completed sessions in start order — the replay feed for
    retention.rebuild_all()."""
    query = "SELECT * FROM sessions WHERE completed_at IS NOT NULL"
    params: tuple = ()
    if profile:
        query += " AND profile = ?"
        params = (profile,)
    query += " ORDER BY started_at ASC"
    with _conn() as c:
        return [dict(r) for r in c.execute(query, params).fetchall()]


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
