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
import calibration
import mandates
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
    _render_mandate_cadence(profile)

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


def _render_mandate_cadence(profile: str | None) -> None:
    """Cadence progress for a registry mandate (mandates.py) — e.g. SCDF's
    2 table-top exercises + 2 evacuation drills per year. Only renders when a
    single profile is selected AND its configured mandate is a registry id
    with a cadence; free-text mandates change nothing."""
    if not profile:
        return
    try:
        mandate = mandates.get(assessment.load_settings(profile)["mandate"])
    except OSError:
        return
    if not mandate:
        return
    status = mandates.cadence_status(profile, mandate)
    if not status:
        return

    st.markdown(f"**Mandate cadence** — {mandate['regulator']} "
                f"(trailing {status['window_days']} days)")
    if status["basis"] and status["basis"] != "statutory":
        st.caption(f"Cadence basis: {status['basis']}")
    cols = st.columns(max(2, len(status["requirements"])))
    for col, r in zip(cols, status["requirements"]):
        with col:
            if r["recorded"] is None:
                st.metric(r["label"], f"{r['required']}/yr required",
                          help="Conducted physically and documented outside "
                               "Certus — shown here so the schedule isn't "
                               "forgotten, never counted automatically.")
            else:
                st.metric(r["label"], f"{r['recorded']} of {r['required']}",
                          delta=(f"-{r['shortfall']} needed"
                                 if r["shortfall"] else "on track"),
                          delta_color="inverse" if r["shortfall"] else "normal")


def _render_calibration_tab(profile: str | None) -> None:
    """The calibration flywheel's instructor surface: review individual
    gradings against the recorded answers, record the expert verdict, watch
    the agreement figures accumulate, export the dataset. Unlike the evidence
    export, this tab shows answer-key action TEXT — necessary for judging
    coverage, acceptable because the whole dashboard sits behind the admin
    password gate."""
    st.caption(
        "Review recorded gradings one at a time: read what the trainee had "
        "said up to that turn, then mark which expected actions were actually "
        "covered. Each review becomes a labeled example — the agreement "
        "figures below appear in every evidence export for this profile, and "
        "the dataset feeds calibrate_grader.py when tuning the grader.")

    all_examples = calibration.examples(profile)
    if not all_examples:
        st.info("No recorded grade events yet. Labels come from recorded "
                "sessions — run some drills with recording on first.")
        return

    stats = calibration.grader_stats(profile)
    col_n, col_agree, col_pr, col_ovr = st.columns(4)
    col_n.metric("Expert-reviewed", f"{stats['labeled']} / {stats['events']}")
    col_agree.metric("Exact agreement",
                     f"{stats['agreement']:.0%}" if stats["labeled"] else "—",
                     help="Share of reviewed gradings where the expert marked "
                          "exactly the same covered actions as the model.")
    col_pr.metric("Precision / recall",
                  (f"{stats['precision']:.0%} / {stats['recall']:.0%}"
                   if stats["labeled"] else "—"),
                  help="Action-level: precision = model credits the expert "
                       "confirmed; recall = expert credits the model caught.")
    col_ovr.metric("Override rate",
                   (f"{stats['override_rate']:.0%}"
                    if stats["override_rate"] is not None else "—"),
                   help="Finished assessments whose verdict an instructor "
                        "overrode — the coarser trust signal.")

    labeled_rows = calibration.labeled(profile)
    if labeled_rows:
        per_source = calibration.per_source_stats(labeled_rows)
        if per_source:
            df = pd.DataFrame(per_source)
            df["agreement"] = (df["agreement"] * 100).round(0).astype(int).astype(str) + "%"
            df = df[["source", "labeled", "agreement"]]
            df.columns = ["SOP source", "reviewed", "exact agreement"]
            st.markdown("**Agreement by SOP source** (weakest first)")
            st.dataframe(df, hide_index=True, width="stretch")
        st.download_button(
            "Download calibration dataset (.jsonl)",
            calibration.to_jsonl(labeled_rows),
            file_name=f"calibration_{profile or 'all'}.jsonl",
            mime="application/jsonl")
        st.caption("The dataset contains answer-key action text — handle it "
                   "like the SOPs themselves, not like an evidence export.")

    unlabeled = [e for e in all_examples if e["verified_ids"] is None]
    st.markdown(f"**Review queue** — {len(unlabeled)} grading(s) awaiting review")
    if not unlabeled:
        st.success("Every recorded grading for this profile has been reviewed.")
        return

    chosen = st.selectbox(
        "Grading to review", unlabeled, key="calibration_pick",
        format_func=lambda e: (f"#{e['event_id']} — {e['trainee']} — "
                               f"step {e['step_number']}, attempt "
                               f"{e['attempt']} ({e['mode']})"))
    st.markdown(f"**Step {chosen['step_number']}: {chosen['title']}** — "
                f"sources: {', '.join(chosen['sources']) or '—'}")
    if chosen["prior_answer"]:
        st.markdown("Trainee's earlier answers for this step:")
        st.info(chosen["prior_answer"])
    st.markdown("Trainee's answer this turn:")
    st.info(chosen["message"])

    def _fmt(aid: str) -> str:
        role, action = chosen["actions"][aid]
        return f"{aid} · [{role}] {action}"

    model_set = set(chosen["model_ids"])
    verified = st.multiselect(
        "Actions the trainee's cumulative answer actually covered "
        "(pre-filled with the model's verdict — correct it where it's wrong)",
        options=sorted(chosen["actions"]),
        default=sorted(model_set & set(chosen["actions"])),
        format_func=_fmt, key=f"cal_ids_{chosen['event_id']}")
    labeler = st.text_input("Reviewer name (required)",
                            key=f"cal_labeler_{chosen['event_id']}")
    note = st.text_input("Note (optional — e.g. why the model was wrong)",
                         key=f"cal_note_{chosen['event_id']}")
    if st.button("Record expert verdict", key=f"cal_b_{chosen['event_id']}"):
        if not labeler.strip():
            st.warning("A label without a reviewer isn't auditable — add "
                       "your name.")
        else:
            storage.upsert_calibration_label(chosen["event_id"], verified,
                                             labeler, note)
            agree = set(verified) == model_set
            st.success("Recorded — expert "
                       f"{'agrees' if agree else 'DISAGREES'} with the model.")
            st.rerun()


def _render_readiness_tab(profile: str | None) -> None:
    """The executive view: which procedures, on which people, would fail
    tonight. Emoji cells rather than CSS so it renders identically in light/
    dark themes and pastes cleanly into a report."""
    st.caption(
        "Latest observed competence per trainee and SOP document, from "
        "recorded drills and assessments. 🟢 ready (clean or near-clean last "
        "drill) · 🟡 shaky (heavy prompting) · 🔴 at risk (couldn't complete) "
        "· ⚪ unknown (last observation > 90 days old — memory decays, so an "
        "old green is not a current green).")
    matrix = retention.readiness_matrix(profile)
    if not matrix["trainees"]:
        st.info("No completed recorded sessions with named trainees yet.")
        return

    icons = {"ready": "🟢", "shaky": "🟡", "at_risk": "🔴", "unknown": "⚪"}

    def cell_icon(cell):
        if cell is None:
            return ""
        return icons[retention.state_of(cell["quality"], cell["stale"])]

    rows = []
    for trainee in matrix["trainees"]:
        row = {"Trainee": trainee}
        for source in matrix["sources"]:
            row[source] = cell_icon(matrix["cells"].get((trainee, source)))
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    summary = [{"SOP document": src,
                "trainees ready": f"{v['ready']}/{v['total']}",
                "ready %": f"{100 * v['ready'] / v['total']:.0f}%"}
               for src, v in sorted(matrix["source_summary"].items(),
                                    key=lambda kv: kv[1]["ready"] / kv[1]["total"])]
    st.markdown("**Per-procedure readiness** (worst first)")
    st.dataframe(pd.DataFrame(summary), hide_index=True, width="stretch")

    # The longitudinal layer: the same four states snapshotted across the
    # whole recorded history. Colors validated for light/dark and CVD
    # separation; "unknown" is deliberately neutral gray. Identity never
    # rides on color alone — the legend names each state and the tables
    # above carry the same data.
    trend = retention.readiness_trend(profile)
    if len(trend["dates"]) >= 2:
        st.markdown(f"**Readiness over time** — all {trend['pairs']} "
                    f"(trainee, procedure) pairs ever observed, snapshotted "
                    f"every {trend['step_days']} days")
        df = pd.DataFrame(trend["counts"],
                          index=pd.to_datetime(trend["dates"]))
        df.columns = ["ready", "shaky", "at risk", "unknown"]
        # stack=True: this is a composition — the four states always sum to
        # the fixed pair universe, so the stack's flat top IS the denominator.
        st.area_chart(df, color=["#2a9d8f", "#d97706", "#e63946", "#6b7280"],
                      stack=True)
        st.caption("Pairs not yet observed count as unknown, and a pair whose "
                   "last drill ages past 90 days RETURNS to unknown — the "
                   "line goes down when drilling stops. That decline is "
                   "honest: a readiness number that can only go up isn't a "
                   "measurement.")

    st.download_button(
        "Download readiness report (.md)",
        assessment.readiness_report(profile),
        file_name=f"readiness_report_{profile or 'all'}.md",
        mime="text/markdown")


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

    (tab_sessions, tab_assessments, tab_readiness, tab_missed, tab_retention,
     tab_calibration) = st.tabs(
        ["Sessions", "Assessments", "Readiness", "Most-missed SOP steps",
         "Retention", "Calibration"])
    with tab_sessions:
        _render_sessions_tab(selected_profile)
    with tab_assessments:
        _render_assessments_tab(selected_profile)
    with tab_readiness:
        _render_readiness_tab(selected_profile)
    with tab_missed:
        _render_most_missed_tab(selected_profile)
    with tab_retention:
        _render_retention_tab(selected_profile)
    with tab_calibration:
        _render_calibration_tab(selected_profile)
