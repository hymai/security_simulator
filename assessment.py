"""
Assessment & compliance mode (iteration 4) — "sell proof, not practice".

Training mode (the Socratic tutor) practices a procedure; assessment mode
*measures* it. Same pipeline, same freshly-generated scenario and answer key,
but: no hints, a per-step attempt limit, an optional time limit, and a pass
threshold — producing a score, a verdict, and an audit-grade evidence export.

Division of labor mirrors retention.py: this module owns settings, scoring
math, and report/export rendering; storage.py stays plain CRUD; certus.py and
instructor_dashboard.py render.

Why the evidence is defensible (the integrity statement embedded in every
export summarizes this):
- The answer key is derived server-side from the SOP corpus and never sent to
  the browser; grading exchanges action *ids* only (grading.py), so a trainee
  cannot extract the key through the assessment itself.
- Every retry is a freshly generated scenario, so a re-take is more practice
  against the same SOPs, never answer-bank recall.
- Settings are snapshotted onto the session row at start (storage.py), so
  later config edits cannot rewrite what a past verdict was judged against.
- An instructor override never mutates the machine verdict — both appear in
  the export, with the override note and timestamp.

Scoring: a step's coverage is taken from its LAST grade event, because grading
accumulates everything the trainee said for that step (grading.py) — the last
event's covered_ids are cumulative. Step score = covered/total actions; the
session score is the unweighted mean over ALL answer-key steps, so a step
never reached (time expired) counts as 0 rather than silently dropping out.
"""

import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone

import calibration
import corpus_config
import mandates
import retention
import storage

# Per-profile overrides live under an "assessment" key in
# profiles/<profile>/config.json; unknown keys there are ignored.
DEFAULTS = {
    "pass_threshold": 0.8,        # fraction of expected actions, 0.0-1.0
    "max_attempts_per_step": 2,   # graded answer attempts; questions are free
    "time_limit_minutes": 0,      # 0 = untimed
    "mandate": "",                # free text, e.g. "OSHA PSM 29 CFR 1910.119(o)"
    "difficulty": "standard",     # or "advanced" — instructor-fixed, like the
                                  # threshold: trainees don't pick their own bar
}


def load_settings(profile: str) -> dict:
    """Assessment settings for a profile: config.json overrides on DEFAULTS.

    Set by the instructor in config, never by the trainee in the UI — a
    self-chosen pass threshold wouldn't certify anything.
    """
    overrides = corpus_config.load_config(profile).get("assessment") or {}
    settings = dict(DEFAULTS)
    settings.update({k: overrides[k] for k in DEFAULTS if k in overrides})
    return settings


# --- scoring -----------------------------------------------------------------

def score_session(detail: dict) -> dict:
    """Score one recorded session (storage.session_detail dict).

    Returns {"steps": [per-step rows], "score": float}. Pure function of the
    recorded events, so the verdict can always be recomputed from evidence.
    """
    rows = []
    for step in detail["steps"]:
        all_ids = set(step["actions"])
        all_events = [e for e in detail["events"]
                      if e["step_number"] == step["step_number"]]
        # A late/ungraded event (is_graded=0 — recorded verbatim after the
        # deadline, never scored) must not inflate the attempt count an
        # auditor reads as "the trainee tried N times" — it carries forward
        # the last graded event's coverage unchanged, never its own verdict.
        events = [e for e in all_events if e.get("is_graded", 1)]
        covered = set(events[-1]["covered_ids"]) & all_ids if events else set()
        rows.append({
            "step": step["step_number"],
            "title": step["title"],
            "sources": step["sources"],
            "attempts": len(events),
            "covered": len(covered),
            "total": len(all_ids),
            "coverage": len(covered) / len(all_ids) if all_ids else 1.0,
        })
    score = sum(r["coverage"] for r in rows) / len(rows) if rows else 0.0
    return {"steps": rows, "score": score}


def effective_passed(session: dict) -> bool | None:
    """The verdict that currently stands: instructor override if present,
    otherwise the machine verdict. None if the assessment hasn't finished."""
    if session.get("override_passed") is not None:
        return bool(session["override_passed"])
    return bool(session["passed"]) if session.get("passed") is not None else None


def _settings_of(session: dict) -> dict:
    try:
        stored = json.loads(session.get("settings") or "{}")
    except (ValueError, TypeError):
        stored = {}
    settings = dict(DEFAULTS)
    settings.update({k: stored[k] for k in DEFAULTS if k in stored})
    return settings


# --- evidence export ---------------------------------------------------------

_INTEGRITY_STATEMENT = (
    "**Grading integrity.** The expected-actions key for this assessment was "
    "derived server-side from the organization's SOP corpus and was never "
    "transmitted to the trainee's browser; grading exchanges action "
    "identifiers only, so the key cannot be extracted through the assessment "
    "itself. The scenario was freshly generated for this session — a re-take "
    "receives a new scenario against the same SOPs, so results cannot come "
    "from answer-bank recall. Source provenance below is resolved in code "
    "from the retrieval index, not asserted by a language model. The "
    "assessment settings shown were snapshotted when the session started and "
    "the machine verdict is never mutated; any instructor override appears "
    "alongside it with its note and timestamp."
)


def evidence_markdown(detail: dict, include_answers: bool = False) -> str:
    """Audit-grade record of one assessment, as self-contained Markdown.

    `include_answers` adds the trainee's verbatim answers per step (the
    instructor-side export wants them; the trainee's own copy doesn't need
    them). The expected-action TEXT is never included in either variant —
    evidence should be shareable with an auditor without also handing over a
    reusable answer bank. A SHA-256 of the body is appended so any later edit
    to the file is detectable.
    """
    settings = _settings_of(detail)
    result = score_session(detail)
    verdict = effective_passed(detail)
    incident_types = ", ".join(json.loads(detail["incident_types"]))
    display = detail["profile"]
    try:
        display = corpus_config.load_config(detail["profile"])["display_name"]
    except OSError:
        pass  # profile deleted since; the slug still identifies it

    lines = [
        "# Certus assessment record",
        "",
        f"| | |",
        f"|---|---|",
        f"| Trainee | {detail['trainee']} |",
        f"| Site profile | {display} (`{detail['profile']}`) |",
        f"| Session ID | {detail['id']} |",
        f"| Incident types | {incident_types} |",
        f"| Started (UTC) | {detail['started_at'][:19]} |",
        f"| Finished (UTC) | {(detail['completed_at'] or '—')[:19]} |",
        f"| Mandate | {settings['mandate'] or '—'} |",
        f"| Pass threshold | {settings['pass_threshold']:.0%} coverage |",
        f"| Attempt limit | {settings['max_attempts_per_step']} per step |",
        f"| Difficulty | {settings['difficulty']} |",
        f"| Time limit | "
        f"{settings['time_limit_minutes'] or 'none'}"
        f"{' min' if settings['time_limit_minutes'] else ''} |",
    ]
    # Stamped at session start (certus.py): earlier assessments this trainee
    # started on this profile and never finished. Shown whenever recorded —
    # including 0 — so an auditor can tell "clean first take" from "recorded
    # before this field existed" (absent row).
    try:
        stored = json.loads(detail.get("settings") or "{}")
    except (ValueError, TypeError):
        stored = {}
    if "abandoned_before_this" in stored:
        lines.append(f"| Prior unfinished assessments | "
                     f"{stored['abandoned_before_this']} |")
    lines += [
        "",
        f"## Verdict",
        "",
    ]
    if detail.get("score") is not None:
        machine = "PASS" if detail["passed"] else "FAIL"
        lines.append(f"**Score: {detail['score']:.0%}** — machine verdict: "
                     f"**{machine}** (threshold {settings['pass_threshold']:.0%})")
        if detail.get("override_passed") is not None:
            o = "PASS" if detail["override_passed"] else "FAIL"
            lines += [
                "",
                f"**Instructor override: {o}** — {detail['override_at'][:19]} UTC",
                f"> {detail.get('override_note') or '(no note)'}",
            ]
        lines += ["", f"**Standing result: "
                      f"{'PASS' if verdict else 'FAIL'}**"]
    else:
        lines.append("Assessment not finished — no verdict.")

    lines += ["", "## Scenario", "", detail["scenario_text"], "",
              "## Per-step results", "",
              "| Step | Attempts | Coverage | SOP sources |",
              "|---|---|---|---|"]
    for r in result["steps"]:
        srcs = ", ".join(f"`{s}`" for s in r["sources"]) or "—"
        lines.append(f"| {r['step']}. {r['title']} | {r['attempts']} | "
                     f"{r['covered']}/{r['total']} ({r['coverage']:.0%}) | {srcs} |")

    if include_answers:
        lines += ["", "## Trainee answers (verbatim)", ""]
        for e in detail["events"]:
            lines += [f"**Step {e['step_number']}, attempt {e['attempt']}** "
                      f"({e['created_at'][:19]} UTC):",
                      f"> {e['trainee_answer']}", ""]

    # If the mandate field is a registry id, expand it into the full citation
    # an auditor can act on. Free-text mandates keep legacy behavior (the
    # value in the header table) with no extra section.
    mandate = mandates.get(settings["mandate"])
    if mandate:
        lines += ["", "## Mandate", ""] + mandates.citation_markdown(mandate)

    # Live calibration figures, not boilerplate: how often expert review has
    # agreed with this profile's grader so far — or an honest "unmeasured".
    lines += ["", "## Grader calibration", "",
              calibration.evidence_text(detail["profile"])]

    lines += ["", "---", "", _INTEGRITY_STATEMENT, ""]
    body = "\n".join(lines)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return body + f"\n_Record integrity: SHA-256 `{digest}` of this document up to this line._\n"


def cohort_csv(sessions: list[dict]) -> str:
    """Flat export of finished assessments — the row format a GRC tool or an
    auditor's spreadsheet actually ingests."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["session_id", "profile", "trainee", "incident_types",
                "started_at", "completed_at", "score", "machine_verdict",
                "override_verdict", "standing_verdict", "pass_threshold",
                "mandate"])
    for s in sessions:
        if s.get("score") is None:
            continue
        settings = _settings_of(s)
        override = s.get("override_passed")
        w.writerow([
            s["id"], s["profile"], s["trainee"],
            "; ".join(json.loads(s["incident_types"])),
            s["started_at"], s["completed_at"], f"{s['score']:.3f}",
            "PASS" if s["passed"] else "FAIL",
            ("PASS" if override else "FAIL") if override is not None else "",
            "PASS" if effective_passed(s) else "FAIL",
            settings["pass_threshold"], settings["mandate"],
        ])
    return buf.getvalue()


# --- readiness report ----------------------------------------------------------

def _display_name(profile: str | None) -> str:
    if not profile:
        return "all profiles"
    try:
        return corpus_config.load_config(profile)["display_name"]
    except OSError:
        return profile


def readiness_report(profile: str | None, stale_days: int = 90) -> str:
    """The executive readiness artifact: current heatmap summary, the trend
    over the whole recorded history, and a per-procedure now-vs-30-days-ago
    comparison — a Markdown report a risk lead can drop into a board pack.

    Everything is derived at read time from recorded sessions (the same
    derivation as the dashboard), with the heatmap's staleness rule applied
    at every point in time: an observation older than `stale_days` reads as
    unknown, so the trend DECLINES when drilling stops. A readiness number
    that can only go up isn't a measurement — that honesty is stated in the
    report itself so the reader knows a dip means 'evidence expired', not
    'people got worse overnight'."""
    matrix = retention.readiness_matrix(profile, stale_days)
    trend = retention.readiness_trend(profile, stale_days)
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        f"# Readiness report — {_display_name(profile)}",
        "",
        f"_Generated {today} by Certus from recorded drill and assessment "
        "sessions. A (trainee, procedure) pair is **ready** when its latest "
        "drill was completed cleanly or near-cleanly; **shaky** with heavy "
        "prompting; **at risk** when it couldn't be completed; **unknown** "
        f"when the last observation is more than {stale_days} days old — an "
        "old green is not a current green, so readiness declines when "
        "drilling stops. A dip in the trend means evidence expired, not that "
        "people got worse overnight — and both are reasons to drill._",
        "",
    ]
    if not trend["pairs"]:
        lines += ["No recorded sessions with named trainees yet — no "
                  "readiness can be claimed.", ""]
        return "\n".join(lines)

    current = trend["counts"][-1]
    lines += [
        f"**Tonight's picture:** {current['ready']} of {trend['pairs']} "
        f"(trainee, procedure) pairs ready "
        f"({current['ready'] / trend['pairs']:.0%}) · {current['shaky']} "
        f"shaky · {current['at_risk']} at risk · {current['unknown']} "
        f"unknown.",
        "",
        f"## Trend (snapshots every {trend['step_days']} days)",
        "",
        "| As of | Ready | Shaky | At risk | Unknown | Ready % |",
        "|---|---|---|---|---|---|",
    ]
    for date, c in zip(trend["dates"], trend["counts"]):
        lines.append(f"| {date} | {c['ready']} | {c['shaky']} | "
                     f"{c['at_risk']} | {c['unknown']} | "
                     f"{c['ready'] / trend['pairs']:.0%} |")

    # The profile's configured mandate, expanded when it's a registry id —
    # with cadence progress, so the report answers "are we on track for the
    # drills the regulator expects" in the same artifact.
    if profile:
        try:
            mandate = mandates.get(load_settings(profile)["mandate"])
        except OSError:
            mandate = None
        if mandate:
            lines += ["", "## Mandate", ""] + mandates.citation_markdown(mandate)
            status = mandates.cadence_status(profile, mandate)
            if status:
                lines += ["", f"**Cadence (trailing {status['window_days']} "
                              "days):**"]
                for r in status["requirements"]:
                    if r["recorded"] is None:
                        lines.append(f"- {r['label']}: {r['required']} per "
                                     "year required")
                    else:
                        gap = (f" — {r['shortfall']} more needed"
                               if r["shortfall"] else " — on track")
                        lines.append(f"- {r['label']}: {r['recorded']} of "
                                     f"{r['required']} recorded{gap}")

    prior = retention.readiness_matrix(
        profile, stale_days,
        as_of=datetime.now(timezone.utc) - timedelta(days=30))
    lines += ["", "## Per procedure — ready trainees, now vs 30 days ago", "",
              "| SOP document | Now | 30 days ago | Direction |",
              "|---|---|---|---|"]
    for source in sorted(matrix["source_summary"],
                         key=lambda s: (matrix["source_summary"][s]["ready"] /
                                        matrix["source_summary"][s]["total"])):
        now_s = matrix["source_summary"][source]
        then_s = prior["source_summary"].get(source)
        then = f"{then_s['ready']}/{then_s['total']}" if then_s else "—"
        if then_s is None:
            direction = "new"
        else:
            direction = ("↑ improving" if now_s["ready"] > then_s["ready"]
                         else "↓ declining" if now_s["ready"] < then_s["ready"]
                         else "→ holding")
        lines.append(f"| `{source}` | {now_s['ready']}/{now_s['total']} | "
                     f"{then} | {direction} |")
    lines.append("")
    return "\n".join(lines)


# --- SOP-gap report ----------------------------------------------------------

def sop_gap_report(profile: str | None) -> str:
    """The productized version of the most-missed dashboard tab: a standalone
    Markdown report a readiness lead can attach to a procedure-review ticket.
    The framing matters — a high miss rate indicts the DOCUMENT first, the
    trainees second; that's the SOP-gap flywheel."""
    missed = storage.most_missed_sources(profile, limit=50)
    display = _display_name(profile)
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        f"# SOP-gap report — {display}",
        "",
        f"_Generated {today} by Certus from recorded drill and assessment "
        "sessions. For each SOP document: how often a response step drawing "
        "on it was **not fully covered on the trainee's first attempt**. "
        "Because every scenario and answer key is freshly generated, results "
        "are aggregated by source document — the one identity stable across "
        "sessions._",
        "",
        "**How to read this:** a document that many *different* trainees miss "
        "is a document problem — unclear, out of date, or unknown to staff — "
        "before it is a trainee problem. Start procedure reviews at the top "
        "of this table.",
        "",
        "| SOP document | First attempts | Missed | Miss rate |",
        "|---|---|---|---|",
    ]
    for r in missed:
        lines.append(f"| `{r['source']}` | {r['attempts']} | {r['misses']} | "
                     f"{r['miss_rate']:.0%} |")
    if not missed:
        lines.append("| _no recorded first attempts yet_ | | | |")
    lines += [
        "",
        "Suggested triage:",
        "",
        "- **≥ 50% miss rate, ≥ 5 attempts** — review the document itself: is "
        "the required action stated where a responder would look for it?",
        "- **High miss rate, few attempts** — schedule more drills against it "
        "before concluding anything.",
        "- **Low miss rate, many attempts** — healthy; candidates for longer "
        "review intervals.",
        "",
    ]
    return "\n".join(lines)
