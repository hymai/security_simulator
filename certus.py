"""
Security incident training simulator — fully local (Ollama + BGE-M3).

Three stages:
  1. Pick incident types  -> a scenario is generated from the threat corpus.
  2. An authoritative answer key is derived from the SOP corpus (server-side only).
  3. A Socratic tutor walks the trainee through the response one step at a time.

The step-tracking is a real state machine in Python (st.session_state.current_step),
not a set of instructions in a prompt. The answer key never reaches the browser;
each turn only the current step's grading result (coverage + a hint) is shown.

Each step is graded against everything the trainee has said for it so far
(st.session_state.step_answers), not just the latest message, so an answer can
be built up across turns instead of retyped whole each time. A message that's
a clarifying question rather than an answer attempt is detected and answered
directly instead of being graded as an incomplete attempt (see grading.py).

Run:  ollama serve            # in another terminal, with qwen2.5:14b pulled
      streamlit run security_simulator.py
"""

import logging

import streamlit as st

import admin_panel
import corpus_config
import pipeline
import retrieval
import storage
import ui_colors

logging.basicConfig(level=logging.INFO)


@st.cache_resource(show_spinner="Loading embedding model and indices…")
def warm_up(profile: str):
    """Load BGE-M3 and this profile's indices once per process/profile (this is
    what cache_resource is for — not stochastic model output, which the old
    app wrongly cached)."""
    retrieval.get_model()
    return {name: retrieval.load_index(profile, name) for name in ("threats", "sops")}


def reset_session():
    for k in ("scenario", "steps", "current_step", "messages", "complete", "profile",
             "record_session", "trainee_name", "db_session_id", "attempt_counts",
             "step_answers"):
        st.session_state.pop(k, None)
    st.session_state.stage = 0


def run_with_progress(label: str, fn, *args, **kwargs):
    """Run a pipeline call that streams tokens, showing live progress instead of
    a static spinner. Generation is the dominant cost here (~6 tok/s measured
    for this model on this hardware) — this doesn't make it faster, but it
    answers "is it stuck?" instead of leaving a silent multi-minute pause."""
    with st.status(label, expanded=True) as status:
        caption = st.empty()

        def on_token(count, elapsed):
            caption.caption(f"{count} tokens generated · {elapsed:.0f}s elapsed")

        result = fn(*args, on_token=on_token, **kwargs)
        status.update(label=f"{label} — done", state="complete")
    return result


def render_scenario_display(scenario: dict) -> str:
    """Colorized version of the scenario for on-screen display only.

    scenario["text"] (plain, no HTML) is what gets downloaded as the training
    record, so it has to stay readable as raw markdown — the color coding is
    layered on separately here, from the same structured `threats` list, and
    rendered with unsafe_allow_html=True only at the call site below."""
    lines = []
    for t in scenario.get("threats", []):
        if isinstance(t, dict):
            label = t.get("incident_type", "")
            desc = t.get("description", "")
            lines.append(f"- {ui_colors.badge(label)} {desc}" if label else f"- {desc}")
        else:
            lines.append(f"- {t}")
    return (
        "**Incident type & Threat**\n"
        + "\n".join(lines) + "\n\n"
        f"**Location**: {scenario.get('location', '')}\n\n"
        f"**Time**: {scenario.get('time', '')}\n\n"
        f"**Scenario**\n\n{scenario.get('scenario', '')}"
    )


def build_reply(verdict: dict, steps: list) -> str:
    """Advance the Python state machine and compose the trainer's reply.

    A clarifying question never advances the step and is never treated as an
    incomplete attempt — it's answered and the trainee stays exactly where
    they were, free to keep building their answer across further turns."""
    if verdict["message_type"] == "question":
        return verdict["reply"]

    n = len(steps)
    if not verdict["complete"]:
        # Stay on this step. Show the hint only (never the missing actions' text).
        return f"Not quite — {verdict['reply']}"

    idx = st.session_state.current_step
    st.session_state.current_step += 1
    if st.session_state.current_step >= n:
        st.session_state.complete = True
        return f"✅ Step {idx + 1} complete. That's all {n} steps — well done! See the summary below."
    return (
        f"✅ Step {idx + 1} of {n} complete. "
        f"Now give me the actions for **Step {idx + 2}**."
    )


def render_summary_download(steps: list):
    """Training over: show a recap and offer the full record as a download.

    The answer key is revealed only here, after completion — never mid-session,
    and never written to the server's filesystem (in-memory download only)."""
    st.success("Training complete. Here is the model answer with sources.")
    lines = [f"# Training record\n\n## Scenario\n\n{st.session_state.scenario['text']}\n\n## Model answer\n"]
    for step in steps:
        srcs = ", ".join(step["sources"]) or "—"
        lines.append(f"\n### Step {step['step']}: {step['title']}  _(sources: {srcs})_")
        for _, (role, action) in step["actions"].items():
            lines.append(f"- **{role}**: {action}")
    record = "\n".join(lines)
    with st.expander("Model answer & sources", expanded=True):
        st.markdown(record)
    st.download_button("Download training record (.md)", record,
                       file_name="training_record.md", mime="text/markdown")
    if st.button("Start a new scenario"):
        reset_session()
        st.rerun()


# --- app -------------------------------------------------------------------

st.set_page_config(page_title="Security Training Simulator", page_icon="🛡️")
st.title("🛡️ Incident Scenario & Response Trainer")

st.session_state.setdefault("stage", 0)

# Sidebar: how it works + site profile + incident-type selection
st.sidebar.title("How it works")
st.sidebar.markdown(
    "1. Pick a site profile and one or more incident types, then generate a scenario.\n"
    "2. Generate the response plan to begin training.\n"
    "3. Answer the trainer one step at a time. It will nudge you toward what "
    "you're missing but never hand you the answer."
)

# Read fresh each run (not cached) so a profile added via the admin panel
# below shows up immediately without restarting the process.
profiles = corpus_config.list_profiles()

if profiles:
    st.sidebar.title("Step 1 — Select site & incident type(s)")
    active_profile = st.sidebar.selectbox(
        "Site profile", profiles,
        format_func=lambda p: corpus_config.load_config(p)["display_name"],
    )
    config = corpus_config.load_config(active_profile)
    incident_types = list(config["incident_types"])
    warm_up(active_profile)

    st.sidebar.markdown(
        " ".join(ui_colors.badge(t) for t in incident_types), unsafe_allow_html=True)

    with st.sidebar.form("generate_form"):
        selected = st.multiselect("Incident type(s)", incident_types,
                                  key=f"types_{active_profile}")

        trainee_name, record_session = "", False
        if storage.RECORDING_ENABLED:
            st.markdown("---")
            trainee_name = st.text_input("Your name (for the instructor's records)")
            record_session = st.checkbox(
                "Record this session for instructor review", value=True,
                help="The instructor for this site has enabled session review. "
                     "Uncheck to opt out — nothing about this session is then "
                     "stored on the server.")

        if st.form_submit_button("Generate Scenario"):
            if not selected:
                st.sidebar.warning("Select at least one incident type.")
            else:
                reset_session()
                st.session_state.profile = active_profile
                st.session_state.scenario = run_with_progress(
                    "Generating scenario…", pipeline.generate_scenario, selected,
                    profile=active_profile)
                st.session_state.stage = 1

                st.session_state.record_session = storage.RECORDING_ENABLED and record_session
                if st.session_state.record_session:
                    st.session_state.trainee_name = trainee_name.strip() or "Anonymous"
                    st.session_state.db_session_id = storage.start_session(
                        active_profile, st.session_state.trainee_name, selected,
                        st.session_state.scenario["text"])
else:
    st.sidebar.info("No profiles yet — use Admin below to upload a corpus.")

# Rendered regardless of whether any profile exists yet, so a fresh install
# can bootstrap its first profile through the UI instead of only the CLI.
admin_panel.render()

if not profiles:
    st.info("No profiles found under profiles/. Use the Admin panel in the "
            "sidebar to upload a corpus, or see build_index.py.")
    st.stop()

# Stage 1: show the scenario, offer to generate the response plan
if st.session_state.stage >= 1:
    st.subheader("Scenario")
    st.markdown(render_scenario_display(st.session_state.scenario), unsafe_allow_html=True)

if st.session_state.stage == 1:
    if st.button("Generate response plan and begin training"):
        steps = run_with_progress(
            "Deriving the response plan (this is the slower stage — typically "
            "1-2 minutes on this model/hardware)…",
            pipeline.generate_answer_key, st.session_state.scenario,
            profile=st.session_state.profile)
        st.session_state.steps = steps
        st.session_state.current_step = 0
        st.session_state.complete = False
        if st.session_state.get("record_session"):
            storage.save_steps(st.session_state.db_session_id, steps)
        st.session_state.messages = [{
            "role": "assistant",
            "content": (
                f"I'm your trainer for today. This scenario has **{len(steps)} step(s)**. "
                "Give me the actions for each role in **Step 1** to begin — consider every "
                "stakeholder, not just one role."
            ),
        }]
        st.session_state.stage = 2
        st.rerun()

# Stage 2: the Socratic tutoring loop (state machine lives here)
if st.session_state.stage == 2:
    steps = st.session_state.steps
    n = len(steps)

    if not st.session_state.complete:
        st.info(f"Progress: **Step {st.session_state.current_step + 1} of {n}**")

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    if st.session_state.complete:
        render_summary_download(steps)
    elif prompt := st.chat_input("Describe the actions for the current step…"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        step = steps[st.session_state.current_step]
        step_number = step["step"]
        step_answers = st.session_state.setdefault("step_answers", {})
        prior_answer = "\n".join(step_answers.get(step_number, []))
        scenario_text = (st.session_state.scenario.get("scenario", "")
                        or st.session_state.scenario.get("text", ""))

        with st.chat_message("assistant"), st.spinner("Thinking…"):
            verdict = pipeline.grade_step(step, scenario_text, prior_answer, prompt)

            # Only a genuine answer attempt adds to what's graded next turn —
            # a clarifying question contributes no coverage (see grading.py).
            if verdict["message_type"] == "answer_attempt":
                step_answers.setdefault(step_number, []).append(prompt)

                if st.session_state.get("record_session"):
                    attempt_counts = st.session_state.setdefault("attempt_counts", {})
                    attempt = attempt_counts.get(step_number, 0) + 1
                    attempt_counts[step_number] = attempt
                    storage.record_grade_event(
                        st.session_state.db_session_id, step_number, attempt, prompt,
                        verdict["covered_ids"], verdict["missing_ids"], verdict["complete"])

            reply = build_reply(verdict, steps)
            if st.session_state.complete and st.session_state.get("record_session"):
                storage.complete_session(st.session_state.db_session_id)
            st.markdown(reply)
        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.rerun()
