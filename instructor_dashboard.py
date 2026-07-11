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

import pandas as pd
import streamlit as st

import storage


def _render_sessions_tab(profile: str | None) -> None:
    sessions = storage.list_sessions(profile)
    if not sessions:
        st.info("No sessions recorded for this profile yet.")
        return

    for s in sessions:
        status = "✅ complete" if s["completed_at"] else "⏳ in progress"
        total = s["total_steps"] if s["total_steps"] is not None else "?"
        label = (f"{s['trainee']} — {s['started_at'][:19]} UTC — {status} "
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


def render() -> None:
    st.subheader("📊 Instructor dashboard")
    if not storage.RECORDING_ENABLED:
        st.caption(
            "Session recording is currently OFF for new sessions (set "
            "SIMULATOR_RECORD_SESSIONS to turn it on). Showing previously "
            "recorded data, if any.")

    profiles = storage.list_profiles_with_sessions()
    if not profiles:
        st.info("No recorded sessions yet.")
        return

    choice = st.selectbox("Profile", ["All"] + profiles, key="dashboard_profile_filter")
    selected_profile = None if choice == "All" else choice

    tab_sessions, tab_missed = st.tabs(["Sessions", "Most-missed SOP steps"])
    with tab_sessions:
        _render_sessions_tab(selected_profile)
    with tab_missed:
        _render_most_missed_tab(selected_profile)
