"""
Security incident training simulator — fully local (Ollama + BGE-M3).

Three stages:
  1. Pick incident types  -> a scenario is generated from the threat corpus.
  2. An authoritative answer key is derived from the SOP corpus (server-side only).
  3. A Socratic tutor walks the trainee through the response one step at a time.

The step-tracking is a real state machine in Python (st.session_state.current_step),
not a set of instructions in a prompt. The answer key never reaches the browser;
each turn only the current step's grading result (coverage + a hint) is shown.

Run:  ollama serve            # in another terminal, with qwen2.5:14b pulled
      streamlit run security_simulator.py
"""

import logging

import streamlit as st

import pipeline
import retrieval

logging.basicConfig(level=logging.INFO)

INCIDENT_TYPES = ["Physical Security", "Cyber Security", "Facilities Management"]


@st.cache_resource(show_spinner="Loading embedding model and indices…")
def warm_up():
    """Load BGE-M3 and both indices once per process (this is what cache_resource
    is for — not stochastic model output, which the old app wrongly cached)."""
    retrieval.get_model()
    return {name: retrieval.load_index(name) for name in ("threats", "sops")}


def reset_session():
    for k in ("scenario", "steps", "current_step", "messages", "complete"):
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


def build_reply(verdict: dict, steps: list) -> str:
    """Advance the Python state machine and compose the trainer's reply."""
    n = len(steps)
    if not verdict["complete"]:
        # Stay on this step. Show the hint only (never the missing actions' text).
        return f"Not quite — {verdict['hint']}"

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

warm_up()
st.session_state.setdefault("stage", 0)

# Sidebar: how it works + incident-type selection
st.sidebar.title("How it works")
st.sidebar.markdown(
    "1. Pick one or more incident types and generate a scenario.\n"
    "2. Generate the response plan to begin training.\n"
    "3. Answer the trainer one step at a time. It will nudge you toward what "
    "you're missing but never hand you the answer."
)
st.sidebar.title("Step 1 — Select incident type(s)")
with st.sidebar.form("generate_form"):
    selected = [t for t in INCIDENT_TYPES if st.checkbox(t, key=f"chk_{t}")]
    if st.form_submit_button("Generate Scenario"):
        if not selected:
            st.sidebar.warning("Select at least one incident type.")
        else:
            reset_session()
            st.session_state.scenario = run_with_progress(
                "Generating scenario…", pipeline.generate_scenario, selected)
            st.session_state.stage = 1

# Stage 1: show the scenario, offer to generate the response plan
if st.session_state.stage >= 1:
    st.subheader("Scenario")
    st.markdown(st.session_state.scenario["text"])

if st.session_state.stage == 1:
    if st.button("Generate response plan and begin training"):
        steps = run_with_progress(
            "Deriving the response plan (this is the slower stage — typically "
            "1-2 minutes on this model/hardware)…",
            pipeline.generate_answer_key, st.session_state.scenario)
        st.session_state.steps = steps
        st.session_state.current_step = 0
        st.session_state.complete = False
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
        with st.chat_message("assistant"), st.spinner("Grading…"):
            verdict = pipeline.grade_step(step, prompt)
            reply = build_reply(verdict, steps)
            st.markdown(reply)
        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.rerun()
