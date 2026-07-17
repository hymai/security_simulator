"""
Grader-calibration flywheel — turning recorded gradings into a measurable
accuracy claim.

Every recorded grade event is a potential labeled example: the full grading
input is already persisted (scenario context, the step's expected actions,
everything the trainee had said), and so is the model's verdict. What's
missing is ground truth. An instructor supplies it by reviewing an event in
the dashboard's Calibration tab and marking which actions the trainee's
cumulative answer ACTUALLY covered (storage.calibration_labels).

Each label buys three things at once:
- a per-profile accuracy figure a compliance buyer can cite ("expert agreement
  N% on M reviewed gradings from OUR corpus"), surfaced in every evidence
  export (assessment.py);
- a regression corpus: calibrate_grader.py replays labeled events through the
  live grader, so a model/prompt change is measured against real trainee
  answers instead of the hand-written spike cases;
- an exportable dataset (JSONL) that accumulates across cohorts — the asset
  that grows with use and doesn't ship with a competitor's feature copy.

Division of labor mirrors retention.py/assessment.py: this module owns
example assembly, agreement math, and export rendering; storage.py stays
plain CRUD; instructor_dashboard.py renders.

The one reconstruction done here: an event's PRIOR answer text. Grading is
cumulative (grading.py), so the input for attempt N includes the answers from
attempts 1..N-1 joined with newlines — exactly how certus.py builds
`prior_answer` from step_answers. Only answer attempts are ever recorded
(questions add no coverage), so the recorded attempts ARE that history.
"""

import io
import json

import storage

# An event's exact-match agreement is all-or-nothing per event; the action-
# level view (precision/recall over ids) credits partial agreement and is the
# number that moves first as labels accumulate.


def examples(profile: str | None = None) -> list[dict]:
    """All recorded grade events as replayable examples, labeled or not.

    Adds to each storage row:
      prior_answer — joined earlier attempts for the same session+step
      context      — what the grader saw (context_text, falling back to the
                     session's scenario_text for pre-column rows)
      agree        — exact set equality of model vs verified ids (None if
                     unlabeled)
    """
    rows = storage.calibration_events(profile)
    history: dict[tuple, list[str]] = {}
    out = []
    for r in rows:
        key = (r["session_id"], r["step_number"])
        prior = history.setdefault(key, [])
        all_ids = set(r["actions"])
        model_ids = sorted(set(r["covered_ids"]) & all_ids)
        verified = r["verified_ids"]
        if verified is not None:
            verified = sorted(set(verified) & all_ids)
        out.append({
            "event_id": r["event_id"],
            "profile": r["profile"],
            "session_id": r["session_id"],
            "trainee": r["trainee"],
            "mode": r["mode"],
            "step_number": r["step_number"],
            "attempt": r["attempt"],
            "title": r["title"],
            "actions": r["actions"],
            "sources": r["sources"],
            "context": r["context_text"] or r["scenario_text"],
            "prior_answer": "\n".join(prior),
            "message": r["trainee_answer"],
            "model_ids": model_ids,
            "verified_ids": verified,
            "agree": (model_ids == verified) if verified is not None else None,
            "labeler": r["labeler"],
            "note": r["note"],
            "created_at": r["created_at"],
            "labeled_at": r["labeled_at"],
        })
        prior.append(r["trainee_answer"])
    return out


def labeled(profile: str | None = None) -> list[dict]:
    return [e for e in examples(profile) if e["verified_ids"] is not None]


def to_jsonl(examples_: list[dict]) -> str:
    """The calibration dataset as JSONL — one self-contained example per line,
    consumable by calibrate_grader.py or any external eval harness. Contains
    the answer-key action text: treat the file like the SOPs themselves, not
    like an evidence export."""
    buf = io.StringIO()
    for e in examples_:
        buf.write(json.dumps(e, ensure_ascii=False) + "\n")
    return buf.getvalue()


# --- agreement math ----------------------------------------------------------

def agreement_stats(examples_: list[dict]) -> dict:
    """Exact-match agreement plus micro precision/recall over action ids,
    for labeled examples. Precision: of the actions the model credited, how
    many the expert also credited. Recall: of the actions the expert
    credited, how many the model caught. An empty denominator reads as 1.0 —
    'no credits given, none wrong'."""
    rows = [e for e in examples_ if e["verified_ids"] is not None]
    tp = fp = fn = 0
    agree = 0
    for e in rows:
        model, verified = set(e["model_ids"]), set(e["verified_ids"])
        tp += len(model & verified)
        fp += len(model - verified)
        fn += len(verified - model)
        agree += model == verified
    n = len(rows)
    return {
        "labeled": n,
        "agree": agree,
        "agreement": agree / n if n else None,
        "precision": tp / (tp + fp) if (tp + fp) else 1.0,
        "recall": tp / (tp + fn) if (tp + fn) else 1.0,
    }


def per_source_stats(examples_: list[dict]) -> list[dict]:
    """agreement_stats re-cut by SOP source document — the same stable
    identity most_missed_sources aggregates by. An event citing two sources
    counts toward both (like the miss tally does)."""
    by_source: dict[str, list[dict]] = {}
    for e in examples_:
        if e["verified_ids"] is None:
            continue
        for src in e["sources"] or ["(no source)"]:
            by_source.setdefault(src, []).append(e)
    out = []
    for src, rows in by_source.items():
        stats = agreement_stats(rows)
        stats["source"] = src
        out.append(stats)
    out.sort(key=lambda s: (s["agreement"], -s["labeled"]))
    return out


# --- the stats block for evidence exports and the dashboard ------------------

def grader_stats(profile: str | None = None) -> dict:
    """Everything the evidence export and the dashboard header need:
    event/label counts, agreement figures, and the instructor-override rate
    across finished assessments (the coarser trust signal that exists even
    before any per-event labels do)."""
    all_examples = examples(profile)
    stats = agreement_stats(all_examples)
    stats["events"] = len(all_examples)

    finished = [s for s in storage.list_sessions(profile)
                if s.get("mode") == "assessment" and s.get("score") is not None]
    overridden = [s for s in finished if s.get("override_passed") is not None]
    stats["assessments"] = len(finished)
    stats["overridden"] = len(overridden)
    stats["override_rate"] = (
        len(overridden) / len(finished) if finished else None)
    return stats


def evidence_text(profile: str) -> str:
    """The calibration paragraph embedded in every evidence export. Same
    honesty rule as the readiness heatmap's staleness: an unmeasured grader
    is reported as unmeasured, never implied to be accurate."""
    s = grader_stats(profile)
    if s["labeled"]:
        lines = [
            f"{s['labeled']} grading event(s) from this profile have been "
            f"reviewed by an instructor against the recorded answers "
            f"({s['events']} recorded in total). Expert agreement with the "
            f"machine grader: **{s['agreement']:.0%}** exact-verdict; "
            f"action-level precision {s['precision']:.0%}, recall "
            f"{s['recall']:.0%}.",
        ]
    else:
        lines = [
            f"No expert-reviewed gradings have been recorded for this profile "
            f"yet ({s['events']} grading event(s) recorded) — the grader's "
            f"agreement with expert judgment on this corpus is unmeasured.",
        ]
    if s["assessments"]:
        lines.append(
            f"Instructor override rate across finished assessments: "
            f"{s['override_rate']:.0%} ({s['overridden']} of "
            f"{s['assessments']}).")
    return " ".join(lines)
