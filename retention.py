"""
Spaced-repetition retention engine (iteration 3). SM-2 scheduling over the
one identity that is stable across freshly-generated sessions: the SOP source
file (see storage.py's module docstring for why steps can't be compared
across sessions).

Division of labor: this module owns the SM-2 math and orchestration;
storage.py stays plain CRUD; certus.py / instructor_dashboard.py render.

Everything here is derived from sessions/steps/grade_events that are only
written when a trainee opted into recording — retention adds no new data
collection. retention_state itself is a materialized view: rebuild_all()
replays it from scratch, so tuning the constants below never strands data.

Quality (0-5) is synthesized from attempts-to-complete, since grading is
binary per attempt:
    completed on attempt 1/2/3 -> 5/4/3   (SM-2's "recalled" band, q >= 3)
    completed on attempt >= 4  -> 2       (heavy scaffolding: schedule resets)
    attempted, never completed -> 1
    never attempted            -> no review recorded
A source cited by several steps in one session gets the MIN of their
qualities — grasp of an SOP is only as good as the worst step drawing on it.
"""

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import storage

log = logging.getLogger("certus.retention")

EF_START = 2.5
EF_FLOOR = 1.3
# Canonical SM-2 first intervals (days) for repetition 1 and 2; thereafter
# interval = round(previous * EF).
FIRST_INTERVAL = 1
SECOND_INTERVAL = 6
# Headline "retained" bar for the 30/90-day metric: completed every citing
# step within 2 attempts. The strict q=5 (clean first attempt) number is
# reported alongside as the conservative claim.
RETAINED_QUALITY = 4


# --- pure SM-2 math ----------------------------------------------------------

def quality_from_events(step_events: list[dict]) -> int | None:
    """Derive 0-5 quality for one step from its grade events (each event is
    one genuine answer attempt — clarifying questions never reach storage)."""
    if not step_events:
        return None
    completed = [e for e in step_events if e["complete"]]
    if not completed:
        return 1
    attempts = min(e["attempt"] for e in completed)
    return max(2, min(5, 6 - attempts))


def session_source_qualities(steps: list[dict], events: list[dict]) -> dict[str, int]:
    """Per-source quality for one session: fan each step's quality out to
    every source it cites (same convention as most_missed_sources), MIN
    across steps citing the same source."""
    qualities: dict[str, int] = {}
    for step in steps:
        step_events = [e for e in events if e["step_number"] == step["step_number"]]
        q = quality_from_events(step_events)
        if q is None:
            continue
        for src in step["sources"]:  # empty sources list -> nothing scheduled
            qualities[src] = min(q, qualities.get(src, 5))
    return qualities


def sm2_update(easiness: float, interval_days: float, repetitions: int,
               quality: int) -> tuple[float, float, int]:
    """One canonical SM-2 review. Returns (easiness, interval_days,
    repetitions). On lapse (q < 3) repetitions and interval reset but EF is
    left unchanged, per the original algorithm."""
    if quality < 3:
        return easiness, float(FIRST_INTERVAL), 0
    easiness = max(EF_FLOOR, easiness + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    repetitions += 1
    if repetitions == 1:
        interval = float(FIRST_INTERVAL)
    elif repetitions == 2:
        interval = float(SECOND_INTERVAL)
    else:
        interval = float(round(interval_days * easiness))
    return easiness, interval, repetitions


# --- orchestration -----------------------------------------------------------

def _add_days(iso_ts: str, days: float) -> str:
    return (datetime.fromisoformat(iso_ts) + timedelta(days=days)).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _trackable_key(trainee: str) -> str | None:
    """Normalized key, or None for names retention must not track: blending
    every anonymous trainee into one SM-2 trajectory would schedule one
    person's reviews off another person's performance."""
    key = storage.normalize_trainee(trainee)
    return key if key and key != "anonymous" else None


def update_from_session(session_id: int) -> list[dict]:
    """Apply one completed session as SM-2 reviews. Opportunistic by design:
    only sources that actually appeared in this session's answer key advance;
    a due source that didn't reappear simply stays due. Returns the updates
    (source, quality, interval_days, due_at) for UI display."""
    detail = storage.session_detail(session_id)
    key = _trackable_key(detail["trainee"])
    if key is None:
        return []
    qualities = session_source_qualities(detail["steps"], detail["events"])
    if not qualities:
        return []

    prior = {s["source"]: s for s in storage.get_retention_states(detail["profile"], key)}
    reviewed_at = detail["completed_at"] or _utcnow().isoformat()
    updates = []
    for source, quality in sorted(qualities.items()):
        state = prior.get(source)
        ef, interval, reps = (
            (state["easiness"], state["interval_days"], state["repetitions"])
            if state else (EF_START, 0.0, 0))
        ef, interval, reps = sm2_update(ef, interval, reps, quality)
        due_at = _add_days(reviewed_at, interval)
        storage.upsert_retention_state(
            detail["profile"], key, detail["trainee"].strip(), source, ef,
            interval, reps, quality, session_id, reviewed_at, due_at)
        updates.append({"source": source, "quality": quality,
                        "interval_days": interval, "due_at": due_at})
    return updates


def rebuild_all(profile: str | None = None) -> int:
    """Wipe and deterministically replay retention state from every completed
    session in start order — backfills history recorded before this feature
    existed, and re-derives state after any constant tuning."""
    storage.delete_retention_states(profile)
    sessions = storage.completed_sessions(profile)
    for s in sessions:
        update_from_session(s["id"])
    return len(sessions)


def due_for_review(profile: str, trainee_name: str) -> list[dict]:
    """Due/overdue sources for one trainee, each with the incident types
    historically selected in sessions where that source appeared — the
    'try these types' suggestion (probabilistic re-triggering: corpus
    separation means no scenario can be forced to hit a given SOP)."""
    key = _trackable_key(trainee_name)
    if key is None:
        return []
    due = storage.due_retention_states(profile, key)
    if not due:
        return []

    type_counts: dict[str, Counter] = {}
    for row in storage.sessions_citing_sources(profile, key):
        types = _loads(row["incident_types"])
        for src in _loads(row["sources"]):
            type_counts.setdefault(src, Counter()).update(types)

    now = _utcnow()
    for d in due:
        d["days_overdue"] = (now - datetime.fromisoformat(d["due_at"])).days
        d["suggested_types"] = [t for t, _ in type_counts.get(d["source"], Counter()).most_common(3)]
    return due


def retention_metrics(profile: str | None = None) -> dict:
    """The headline 30/90-day cohort competence numbers, derived at read time
    from the base tables (retroactive over all recorded history — no
    dependence on retention_state). A 'retention check' is any completed
    session citing a source the same trainee last saw >= 30/90 days earlier."""
    per_key: dict[tuple[str, str], list[tuple[str, int]]] = {}
    for s in storage.completed_sessions(profile):
        key = _trackable_key(s["trainee"])
        if key is None:
            continue
        detail = storage.session_detail(s["id"])
        for source, q in session_source_qualities(detail["steps"], detail["events"]).items():
            per_key.setdefault((key, source), []).append((s["started_at"], q))

    checks = []  # (source, gap_days, quality)
    for (key, source), reviews in per_key.items():
        reviews.sort()
        for (prev_at, _), (cur_at, q) in zip(reviews, reviews[1:]):
            gap = (datetime.fromisoformat(cur_at) - datetime.fromisoformat(prev_at)).total_seconds() / 86400
            checks.append((source, gap, q))

    def window(min_gap: float) -> dict:
        hits = [(s, q) for s, g, q in checks if g >= min_gap]
        n = len(hits)
        return {
            "n": n,
            "retained_pct": 100 * sum(q >= RETAINED_QUALITY for _, q in hits) / n if n else None,
            "strict_pct": 100 * sum(q == 5 for _, q in hits) / n if n else None,
        }

    per_source: dict[str, dict] = {}
    for source, gap, q in checks:
        entry = per_source.setdefault(source, {"source": source, "n30": 0, "ret30": 0,
                                               "n90": 0, "ret90": 0})
        if gap >= 30:
            entry["n30"] += 1
            entry["ret30"] += q >= RETAINED_QUALITY
        if gap >= 90:
            entry["n90"] += 1
            entry["ret90"] += q >= RETAINED_QUALITY

    return {"d30": window(30), "d90": window(90),
            "per_source": sorted(per_source.values(), key=lambda r: -r["n30"])}


def _loads(text: str) -> list:
    try:
        return json.loads(text) or []
    except (ValueError, TypeError):
        return []
