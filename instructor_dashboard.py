"""
Instructor-facing view over recorded sessions (storage.py). Rendered inside
admin_panel's password gate — trainee answers and names are exactly the kind
of data that shouldn't be one click away from the training UI itself.

Two views:
- Sessions: per-trainee history, one session at a time.
- Most-missed SOP steps: which source document's guidance trainees fail to
  cover on their first attempt most often, aggregated across every session
  for a profile. This is the more useful of the two for an instructor — it
  points at which SOP is unclear, not just which trainee is weak.
"""

import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

import assessment
import retention
import storage


def _render_sessions_tab(profile: str | None) -> None:
    sessions = storage.list_sessions(profile)
    if not sessions:
        st.info("No sessions recorded for this profile yet.")
        return

    for s in sessions:
        status = "✅ complete" if s["completed_at"] else "⏳ in progress"
        total = s["total_steps"] if s["total_steps"] is not None else "?"
        badge = "📋 " if s.get("mode") == "assessment" else ""
        label = (f"{badge}{s['trainee']} — {s['started_at'][:19]} UTC — {status} "
                f"({s['steps_completed']}/{total} steps)")
        with st.expander(label):
            detail = storage.session_detail(s["id"])
            incident_types = ", ".join(json.loads(s["incident_types"]))
            st.markdown(f"**Incident types**: {incident_types}")
            st.markdown(f"**Scenario**\n\n{s['scenario_text']}")

            if not detail["steps"]:
                st.caption("Trainee did not reach the response-plan stage.")
                continue

            for step in detail["steps"]:
                events = [e for e in detail["events"] if e["step_number"] == step["step_number"]]
                if not events:
                    outcome = "not attempted"
                elif any(e["complete"] for e in events):
                    outcome = f"✅ completed in {len(events)} attempt(s)"
                else:
                    outcome = f"❌ not completed ({len(events)} attempt(s))"
                sources = ", ".join(step["sources"]) or "—"
                st.markdown(f"- **Step {step['step_number']}: {step['title']}** "
                           f"— {outcome} — sources: {sources}")


def _render_most_missed_tab(profile: str | None) -> None:
    st.caption(
        "Share of trainees' FIRST attempt at a step that did not fully cover "
        "it, grouped by which SOP document that step drew from. Aggregated by "
        "file rather than exact step text, since each session's scenario and "
        "answer key are freshly generated — the source file is what stays "
        "stable across sessions.")
    missed = storage.most_missed_sources(profile, limit=15)
    if not missed:
        st.info("Not enough completed steps recorded yet.")
        return

    df = pd.DataFrame(missed)[["source", "attempts", "misses", "miss_rate"]]
    df["miss_rate"] = (df["miss_rate"] * 100).round(0).astype(int).astype(str) + "%"
    df.columns = ["SOP source", "first attempts", "missed", "miss rate"]
    st.dataframe(df, hide_index=True, width="stretch")

    # The productized version of this tab: a standalone report to attach to a
    # procedure-review ticket, framed at the document, not the trainee.
    st.download_button(
        "Download SOP-gap report (.md)",
        assessment.sop_gap_report(profile),
        file_name=f"sop_gap_report_{profile or 'all'}.md",
        mime="text/markdown")


def _render_assessments_tab(profile: str | None) -> None:
    """Iteration 4's instructor surface: verdicts, audit-grade evidence
    export, the cohort CSV, and the appeal/override path. Overrides never
    mutate the machine verdict (storage.override_assessment) — both are
    shown, and both land in the evidence export."""
    sessions = [s for s in storage.list_sessions(profile)
                if s.get("mode") == "assessment"]
    if not sessions:
        st.info("No assessments recorded yet. Trainees start one by choosing "
                "'Assessment' as the session mode in the sidebar; per-profile "
                "settings (threshold, attempts, time limit, mandate) live "
                "under an 'assessment' key in the profile's config.json.")
        return

    rows = []
    for s in sessions:
        if s["score"] is not None:
            eff = assessment.effective_passed(s)
            verdict = "PASS ✅" if eff else "FAIL ❌"
            if s.get("override_passed") is not None:
                verdict += " (override)"
            score = f"{s['score']:.0%}"
        else:
            verdict, score = "in progress", "—"
        rows.append({"ID": s["id"], "Trainee": s["trainee"],
                     "Started (UTC)": s["started_at"][:19],
                     "Score": score, "Verdict": verdict})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.download_button(
        "Download cohort results (.csv)", assessment.cohort_csv(sessions),
        file_name=f"assessments_{profile or 'all'}.csv", mime="text/csv")

    finished = [s for s in sessions if s["score"] is not None]
    if not finished:
        return
    st.markdown("**Evidence & override**")
    chosen = st.selectbox(
        "Assessment", finished, key="assessment_evidence_pick",
        format_func=lambda s: (f"#{s['id']} — {s['trainee']} — "
                               f"{s['started_at'][:19]} UTC — {s['score']:.0%}"))
    detail = storage.session_detail(chosen["id"])
    st.download_button(
        "Download evidence record (.md)",
        assessment.evidence_markdown(detail, include_answers=True),
        file_name=f"assessment_{chosen['id']}_{chosen['trainee']}.md",
        mime="text/markdown", key=f"evidence_{chosen['id']}")

    with st.expander("Override this verdict (appeal path)"):
        st.caption(
            "For when a review of the recorded answers shows the grader was "
            "wrong (a false 'you missed this') or the trainee's deviation was "
            "operationally sound. The machine verdict is kept and exported "
            "alongside your override — a note is required.")
        new_verdict = st.radio("Overridden verdict", ["PASS", "FAIL"],
                               horizontal=True, key=f"ovr_v_{chosen['id']}")
        note = st.text_area("Reason (required, appears in the evidence export)",
                            key=f"ovr_n_{chosen['id']}")
        if st.button("Record override", key=f"ovr_b_{chosen['id']}"):
            if not note.strip():
                st.warning("An override without a reason isn't auditable — "
                           "add a note.")
            else:
                storage.override_assessment(chosen["id"], new_verdict == "PASS",
                                            note)
                st.success("Override recorded.")
                st.rerun()


def _render_retention_tab(profile: str | None) -> None:
    # 1. Drill queue — the local-first nudge: who should re-drill what, now.
    st.markdown("**Drill queue** — trainees with SOPs due for re-drilling")
    due = storage.due_retention_states(profile)
    if not due:
        st.info("Nothing due for review. The queue fills in as trainees "
                "complete recorded sessions and their review dates arrive.")
    else:
        now = datetime.now(timezone.utc)
        rows = [{
            "trainee": d["trainee_display"],
            "SOP source": d["source"],
            "due": d["due_at"][:10],
            "days overdue": max(0, (now - datetime.fromisoformat(d["due_at"])).days),
            "last quality (0-5)": d["last_quality"],
            "interval (days)": round(d["interval_days"]),
        } for d in due]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    # 2. The headline proof metric, derived from base tables at read time —
    # works retroactively over sessions recorded before this feature existed.
    st.markdown("**Cohort retention** — did a trainee still know the "
                "procedure when it resurfaced 30/90+ days later?")
    metrics = retention.retention_metrics(profile)
    if not metrics["d30"]["n"]:
        st.info("Not enough data yet: this metric needs the same trainee to "
                "re-encounter the same SOP at least 30 days apart. It fills "
                "in as the cohort keeps drilling.")
    else:
        col30, col90 = st.columns(2)
        for col, key, label in ((col30, "d30", "30-day"), (col90, "d90", "90-day")):
            w = metrics[key]
            with col:
                if w["n"]:
                    st.metric(f"{label} retention", f"{w['retained_pct']:.0f}%",
                              help="Share of re-encounters ≥ that many days later "
                                   "completed within 2 attempts per step. The "
                                   "stricter first-attempt-clean number is below.")
                    st.caption(f"{w['n']} check(s) · strict (first attempt "
                               f"clean): {w['strict_pct']:.0f}%")
                else:
                    st.metric(f"{label} retention", "—")
                    st.caption("no checks that far apart yet")
        if metrics["per_source"]:
            df = pd.DataFrame(metrics["per_source"])
            df["retained ≥30d"] = df.apply(
                lambda r: f"{100 * r['ret30'] / r['n30']:.0f}%" if r["n30"] else "—", axis=1)
            df["retained ≥90d"] = df.apply(
                lambda r: f"{100 * r['ret90'] / r['n90']:.0f}%" if r["n90"] else "—", axis=1)
            df = df[["source", "n30", "retained ≥30d", "n90", "retained ≥90d"]]
            df.columns = ["SOP source", "checks ≥30d", "retained ≥30d",
                          "checks ≥90d", "retained ≥90d"]
            st.dataframe(df, hide_index=True, width="stretch")

    # 3. Maintenance: state is a materialized view — rebuild backfills
    # pre-retention history and re-derives after constant tuning.
    if st.button("Rebuild retention schedule from history"):
        n = retention.rebuild_all(profile)
        st.success(f"Replayed {n} completed session(s) into retention state.")
    st.caption("Rebuilding replays recorded sessions — all retention state "
               "is derived; nothing new is collected.")


def render() -> None:
    st.subheader("📊 Instructor dashboard")
    if not storage.RECORDING_ENABLED:
        st.caption(
            "Session recording is currently OFF for new sessions (set "
            "CERTUS_RECORD_SESSIONS to turn it on). Showing previously "
            "recorded data, if any.")

    profiles = storage.list_profiles_with_sessions()
    if not profiles:
        st.info("No recorded sessions yet.")
        return

    choice = st.selectbox("Profile", ["All"] + profiles, key="dashboard_profile_filter")
    selected_profile = None if choice == "All" else choice

    tab_sessions, tab_assessments, tab_missed, tab_retention = st.tabs(
        ["Sessions", "Assessments", "Most-missed SOP steps", "Retention"])
    with tab_sessions:
        _render_sessions_tab(selected_profile)
    with tab_assessments:
        _render_assessments_tab(selected_profile)
    with tab_missed:
        _render_most_missed_tab(selected_profile)
    with tab_retention:
        _render_retention_tab(selected_profile)
