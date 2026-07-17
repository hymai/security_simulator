"""
Certus — operational readiness platform, fully local (Ollama + BGE-M3).

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
      streamlit run certus.py
"""

import logging
import time

import streamlit as st

import admin_panel
import assessment
import corpus_config
import pipeline
import retention
import retrieval
import storage
import ui_colors

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("certus.app")


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
             "step_answers", "retention_updates", "mode", "assessment_settings",
             "deadline", "assessment_result", "inject_revealed", "team_names"):
        st.session_state.pop(k, None)
    st.session_state.stage = 0


def inject_reveal_if_due(steps: list) -> str:
    """Advanced difficulty: reveal the hidden mid-scenario inject once the
    trainee crosses the midpoint step. Returns markdown to append to the
    current reply (empty when there's no inject or it's already out).

    The answer key was derived from scenario + inject with inject steps pinned
    to the second half (pipeline.generate_answer_key), so nothing is graded
    before it's been revealed."""
    inject = (st.session_state.scenario.get("inject") or "").strip()
    if not inject or st.session_state.get("inject_revealed"):
        return ""
    if st.session_state.current_step >= max(1, len(steps) // 2):
        st.session_state.inject_revealed = True
        return f"\n\n⚡ **Development** — the situation has changed: {inject}"
    return ""


def grading_context() -> str:
    """Scenario text the grader sees this turn: includes the inject only once
    the trainee has seen it too, so the grader never judges references to a
    development the trainee couldn't know about (and vice versa)."""
    scenario = st.session_state.scenario
    text = scenario.get("scenario", "") or scenario.get("text", "")
    if st.session_state.get("inject_revealed"):
        inject = (scenario.get("inject") or "").strip()
        if inject:
            text += f"\n\nLater development (already announced): {inject}"
    return text


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
        # Stay on this step. Acknowledge partial credit by COUNT only (never
        # the missing actions' text) — a flat "Not quite" on every incomplete
        # attempt erases real progress and reads as "you're wrong" even when
        # most of the answer was right.
        covered, total = len(verdict["covered_ids"]), len(verdict["covered_ids"]) + len(verdict["missing_ids"])
        prefix = f"✓ {covered} of {total} covered — " if covered else "Not quite — "
        return f"{prefix}{verdict['reply']}"

    idx = st.session_state.current_step
    st.session_state.current_step += 1
    done = steps[idx]
    # The step TITLE is revealed only here, after completion — retrospectively
    # it's feedback; shown up front it would summarize the expected response.
    # Orientation for the NEXT step uses its threat cue instead: the scenario's
    # own wording, which the trainee has already read (see pipeline.py).
    if st.session_state.current_step >= n:
        st.session_state.complete = True
        return (f"✅ Step {idx + 1} — *{done['title']}* — complete. "
                f"That's all {n} steps — well done! See the summary below.")
    cue = steps[st.session_state.current_step].get("threat", "")
    orient = f" This one responds to: *{cue}*." if cue else ""
    return (
        f"✅ Step {idx + 1} — *{done['title']}* — complete. "
        f"Now give me the actions for **Step {idx + 2}**.{orient}"
        f"{inject_reveal_if_due(steps)}"
    )


def finalize_assessment(reason: str) -> str:
    """Score the recorded events, persist the verdict, and compose the closing
    message. The score is recomputed from the DB (assessment.score_session)
    rather than from session_state so the persisted verdict is a pure function
    of the recorded evidence — exactly what the export recomputes."""
    settings = st.session_state.assessment_settings
    detail = storage.session_detail(st.session_state.db_session_id)
    result = assessment.score_session(detail)
    passed = result["score"] >= settings["pass_threshold"]
    storage.finish_assessment(st.session_state.db_session_id,
                              result["score"], passed)
    st.session_state.complete = True
    st.session_state.assessment_result = {**result, "passed": passed,
                                          "reason": reason}
    # A failed assessment is still a genuine retrieval event — it feeds the
    # same spaced-repetition schedule as training (and a fail schedules the
    # SOP for early re-drill, which is the point). Never let a retention bug
    # block the verdict.
    try:
        st.session_state.retention_updates = \
            retention.update_from_session(st.session_state.db_session_id)
    except Exception:
        log.exception("retention update failed for session %s",
                      st.session_state.db_session_id)
    verdict = "**PASS** ✅" if passed else "**FAIL** ❌"
    return (f"The assessment has ended ({reason}). "
            f"Score: **{result['score']:.0%}** against a "
            f"{settings['pass_threshold']:.0%} threshold — {verdict}. "
            f"See the summary below.")


def build_assessment_reply(verdict: dict, steps: list, attempt: int,
                           max_attempts: int) -> str:
    """Assessment counterpart of build_reply: coverage counts only, never a
    hint (the model's nudge reply is deliberately discarded — in a scored
    setting a hint is answer-key leakage). Advances on full coverage OR when
    the attempt limit is spent; finishing the last step finalizes.

    Step titles are NOT revealed mid-assessment, even after a step closes —
    unlike training, a step can close while incomplete here, and its title
    summarizes the expected response."""
    if verdict["message_type"] == "question":
        # Clarifying questions are free (they never reach grading storage) and
        # the reply was already leak-checked in pipeline.grade_step.
        return verdict["reply"]

    covered = len(verdict["covered_ids"])
    total = covered + len(verdict["missing_ids"])
    if not verdict["complete"] and attempt < max_attempts:
        return (f"Recorded — {covered} of {total} expected action(s) covered "
                f"so far (attempt {attempt} of {max_attempts}). You may refine "
                f"or extend your answer; no hints in assessment mode.")

    idx = st.session_state.current_step
    st.session_state.current_step += 1
    n = len(steps)
    status = ("all actions covered ✅" if verdict["complete"]
              else f"{covered} of {total} covered — attempt limit reached")
    if st.session_state.current_step >= n:
        closing = finalize_assessment("all steps answered")
        return f"Step {idx + 1} recorded: {status}. That was the last step. {closing}"
    cue = steps[st.session_state.current_step].get("threat", "")
    orient = f" This one responds to: *{cue}*." if cue else ""
    return (f"Step {idx + 1} recorded: {status}. "
            f"Now describe your actions for **Step {idx + 2}** of {n}.{orient}"
            f"{inject_reveal_if_due(steps)}")


def render_assessment_summary():
    """End-of-assessment view. Unlike training, the model answer is NOT
    revealed — an assessment produces a verdict and pointers to which SOPs to
    review, and the trainee practices those in training mode (where every
    scenario is fresh anyway)."""
    res = st.session_state.assessment_result
    settings = st.session_state.assessment_settings
    if res["passed"]:
        st.success(f"**PASS** — score {res['score']:.0%} "
                   f"(threshold {settings['pass_threshold']:.0%})")
    else:
        st.error(f"**FAIL** — score {res['score']:.0%} "
                 f"(threshold {settings['pass_threshold']:.0%})")
    st.caption(f"Assessment ended: {res['reason']}. "
               "Scores are per expected action, averaged over all steps.")

    rows = [{"Step": f"{r['step']}. {r['title']}",
             "Attempts": r["attempts"],
             "Coverage": f"{r['covered']}/{r['total']} ({r['coverage']:.0%})",
             "SOP sources": ", ".join(r["sources"]) or "—"}
            for r in res["steps"]]
    st.dataframe(rows, hide_index=True, width="stretch")

    to_review = sorted({s for r in res["steps"] if r["coverage"] < 1.0
                        for s in r["sources"]})
    if to_review:
        st.markdown("**SOPs to review before a re-take:** "
                    + ", ".join(f"`{s}`" for s in to_review))
        st.caption("The model answer isn't shown after an assessment — review "
                   "the documents above, or run a training session (every "
                   "scenario is freshly generated, so practicing can't leak "
                   "a future assessment).")

    record = assessment.evidence_markdown(
        storage.session_detail(st.session_state.db_session_id))
    st.download_button("Download assessment record (.md)", record,
                       file_name="assessment_record.md", mime="text/markdown")
    updates = st.session_state.get("retention_updates")
    if updates:
        st.caption("Next reviews: " + " · ".join(
            f"{u['source']} in {u['interval_days']:.0f} day(s)" for u in updates))
    if st.button("Start a new scenario"):
        reset_session()
        st.rerun()


def render_summary_download(steps: list):
    """Training over: show a recap and offer the full record as a download.

    The answer key is revealed only here, after completion — never mid-session,
    and never written to the server's filesystem (in-memory download only)."""
    st.success("Training complete. Here is the model answer with sources.")
    team = st.session_state.get("team_names") or []
    participants = f"\n**Participants**: {', '.join(team)}\n" if team else ""
    inject = (st.session_state.scenario.get("inject") or "").strip()
    inject_line = f"\n**Mid-drill development**: {inject}\n" if inject else ""
    lines = [f"# Training record\n{participants}{inject_line}\n"
             f"## Scenario\n\n{st.session_state.scenario['text']}\n\n## Model answer\n"]
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
    updates = st.session_state.get("retention_updates")
    if updates:
        st.caption("Next reviews: " + " · ".join(
            f"{u['source']} in {u['interval_days']:.0f} day(s)" for u in updates))
    if st.button("Start a new scenario"):
        reset_session()
        st.rerun()


# --- app -------------------------------------------------------------------

st.set_page_config(page_title="Certus", page_icon="🛡️")
st.title("🛡️ Certus — Incident Scenario & Response Trainer")

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

    # Identity lives OUTSIDE the form (plain widgets reflect their value on
    # every rerun, so the submit handler below still reads them as locals):
    # knowing who's training *before* incident-type selection is what lets
    # the due-for-review panel inform that selection.
    trainee_name, record_session, mode, difficulty = "", False, "training", "standard"
    if storage.RECORDING_ENABLED:
        st.sidebar.markdown("---")
        trainee_name = st.sidebar.text_input(
            "Your name (for the instructor's records)")
        record_session = st.sidebar.checkbox(
            "Record this session for instructor review", value=True,
            help="The instructor for this site has enabled session review. "
                 "Uncheck to opt out — nothing about this session is then "
                 "stored on the server.")

        # Assessment mode only exists where recording exists: a verdict with
        # no recorded evidence behind it certifies nothing.
        mode_label = st.sidebar.radio(
            "Session mode",
            ["Training — Socratic tutor", "Assessment — scored, no hints"],
            help="Training coaches you step by step. Assessment measures you: "
                 "limited attempts per step, optionally timed, no hints, and "
                 "a pass/fail verdict recorded for the instructor. Settings "
                 "are fixed per site profile by the instructor.")
        if mode_label.startswith("Assessment"):
            mode = "assessment"
            a = assessment.load_settings(active_profile)
            # Difficulty is instructor-fixed for assessments (config.json),
            # same principle as the pass threshold: the trainee doesn't pick
            # their own bar.
            difficulty = a["difficulty"]
            timing = (f"{a['time_limit_minutes']} min limit"
                      if a["time_limit_minutes"] else "untimed")
            mandate = f" · {a['mandate']}" if a["mandate"] else ""
            diff = " · advanced difficulty" if difficulty == "advanced" else ""
            st.sidebar.caption(
                f"Pass ≥ {a['pass_threshold']:.0%} · "
                f"{a['max_attempts_per_step']} attempt(s)/step · "
                f"{timing}{diff}{mandate}")

        if record_session and trainee_name.strip():
            due = retention.due_for_review(active_profile, trainee_name)
            if due:
                with st.sidebar.expander(f"📅 Due for review ({len(due)})",
                                         expanded=True):
                    for d in due:
                        overdue = (f"{d['days_overdue']} day(s) overdue"
                                   if d["days_overdue"] > 0 else "due today")
                        suggest = ", ".join(d["suggested_types"]) or "—"
                        st.markdown(f"`{d['source']}` — {overdue} · try: {suggest}")
                    st.caption(
                        "Selecting the suggested incident types makes it "
                        "likely — not guaranteed — that a fresh scenario "
                        "exercises these procedures again.")
                    suggested = sorted({t for d in due for t in d["suggested_types"]
                                        if t in incident_types})
                    # Rendered before the multiselect instantiates on this
                    # run, so setting its key here simply prefills it.
                    if suggested and st.button("Use suggested types"):
                        st.session_state[f"types_{active_profile}"] = suggested

    team_members: list[str] = []
    if mode == "training":
        # Both are training-only: assessment difficulty is instructor-fixed
        # above, and assessments certify individuals, not rooms.
        difficulty = "advanced" if st.sidebar.checkbox(
            "Advanced difficulty",
            help="Harder scenario: more concurrent threats, at least one "
                 "deliberate diversion, ambiguous sensor information — plus "
                 "a surprise development revealed partway through the drill "
                 "(a mid-scenario inject).") else "standard"
        team_raw = st.sidebar.text_input(
            "Tabletop team (optional) — participant names, comma-separated",
            help="Runs the drill as a facilitated team exercise on one "
                 "screen: the group answers each step together, every "
                 "participant covering their own role's actions. If session "
                 "recording is on, it's recorded under the team's name.")
        team_members = [n.strip() for n in team_raw.split(",") if n.strip()]

    with st.sidebar.form("generate_form"):
        selected = st.multiselect("Incident type(s)", incident_types,
                                  key=f"types_{active_profile}")

        if st.form_submit_button("Generate Scenario"):
            if not selected:
                st.sidebar.warning("Select at least one incident type.")
            elif mode == "assessment" and (not record_session or not trainee_name.strip()):
                st.sidebar.warning(
                    "An assessment needs your name and session recording "
                    "turned on — the recorded answers ARE the evidence behind "
                    "the verdict. Use training mode to practice anonymously.")
            else:
                reset_session()
                st.session_state.profile = active_profile
                st.session_state.mode = mode
                st.session_state.team_names = team_members
                if mode == "assessment":
                    st.session_state.assessment_settings = \
                        assessment.load_settings(active_profile)
                st.session_state.scenario = run_with_progress(
                    "Generating scenario…", pipeline.generate_scenario, selected,
                    profile=active_profile, difficulty=difficulty)
                st.session_state.stage = 1

                st.session_state.record_session = storage.RECORDING_ENABLED and record_session
                if st.session_state.record_session:
                    # A tabletop drill is recorded under the team's collective
                    # name — retention then tracks the team as a unit, which
                    # is the honest granularity for a group exercise.
                    if team_members:
                        st.session_state.trainee_name = "Team: " + ", ".join(team_members)
                    else:
                        st.session_state.trainee_name = trainee_name.strip() or "Anonymous"
                    st.session_state.db_session_id = storage.start_session(
                        active_profile, st.session_state.trainee_name, selected,
                        st.session_state.scenario["text"], mode=mode,
                        settings=(st.session_state.get("assessment_settings")
                                  or {"difficulty": difficulty}))
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
    is_assessment = st.session_state.get("mode") == "assessment"
    begin_label = ("Generate response plan and begin assessment"
                   if is_assessment else
                   "Generate response plan and begin training")
    if is_assessment and st.session_state.assessment_settings["time_limit_minutes"]:
        st.caption(f"⏱ The {st.session_state.assessment_settings['time_limit_minutes']}-minute "
                   "timer starts when the response plan is ready, not now.")
    if st.button(begin_label):
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
        first_cue = steps[0].get("threat", "")
        first_orient = f" It responds to: *{first_cue}*." if first_cue else ""
        if is_assessment:
            a = st.session_state.assessment_settings
            # Timer anchored here, after key generation — the model's 1-2
            # minute derivation must not eat into the trainee's clock.
            st.session_state.deadline = (
                time.time() + a["time_limit_minutes"] * 60
                if a["time_limit_minutes"] else None)
            timing = (f" You have **{a['time_limit_minutes']} minutes**."
                      if a["time_limit_minutes"] else "")
            intro = (
                f"This is a scored assessment: **{len(steps)} step(s)**, up to "
                f"**{a['max_attempts_per_step']} graded attempt(s)** per step, "
                f"pass at **{a['pass_threshold']:.0%}** coverage. No hints — "
                f"but clarifying questions about the scenario are free and "
                f"don't count as attempts.{timing} "
                f"Describe the actions for each role in **Step 1**.{first_orient}"
            )
        elif team := st.session_state.get("team_names"):
            intro = (
                f"This is a facilitated tabletop drill for **{', '.join(team)}** "
                f"— **{len(steps)} step(s)**. For each step, discuss as a team "
                f"and submit one combined answer, each participant giving the "
                f"actions for their own role. Start with **Step 1**.{first_orient}"
            )
        else:
            intro = (
                f"I'm your trainer for today. This scenario has **{len(steps)} step(s)**. "
                f"Give me the actions for each role in **Step 1** to begin —"
                f" consider every stakeholder, not just one role.{first_orient}"
            )
        # Degenerate case: a single-step key never crosses a "midpoint", so a
        # hidden inject would otherwise stay hidden while still being graded —
        # reveal it up front instead.
        inject = (st.session_state.scenario.get("inject") or "").strip()
        if inject and len(steps) < 2:
            st.session_state.inject_revealed = True
            intro += f"\n\n⚡ **Development** — the situation has changed: {inject}"
        st.session_state.messages = [{"role": "assistant", "content": intro}]
        st.session_state.stage = 2
        st.rerun()

# Stage 2: the tutoring/assessment loop (state machine lives here)
if st.session_state.stage == 2:
    steps = st.session_state.steps
    n = len(steps)
    is_assessment = st.session_state.get("mode") == "assessment"
    deadline = st.session_state.get("deadline")

    # Passive expiry check: catches a refresh/rerun after the clock ran out,
    # so an expired assessment finalizes even if no further message is sent.
    if is_assessment and deadline and not st.session_state.complete \
            and time.time() > deadline:
        st.session_state.messages.append(
            {"role": "assistant", "content": finalize_assessment("time expired")})

    if not st.session_state.complete:
        current = steps[st.session_state.current_step]
        cue = current.get("threat", "")
        suffix = f" — responding to: {cue}" if cue else ""
        if is_assessment and deadline:
            suffix += f" · ⏱ {max(0.0, (deadline - time.time()) / 60):.0f} min left"
        st.info(f"Progress: **Step {st.session_state.current_step + 1} of {n}**{suffix}")

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    if st.session_state.complete:
        if is_assessment:
            render_assessment_summary()
        else:
            render_summary_download(steps)
    elif prompt := st.chat_input("Describe the actions for the current step…"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        step = steps[st.session_state.current_step]
        step_number = step["step"]
        step_answers = st.session_state.setdefault("step_answers", {})
        prior_answer = "\n".join(step_answers.get(step_number, []))
        scenario_text = grading_context()

        with st.chat_message("assistant"), st.spinner("Thinking…"):
            language = corpus_config.load_config(st.session_state.profile)["language"]
            verdict = pipeline.grade_step(step, scenario_text, prior_answer, prompt,
                                          language=language)

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
                        verdict["covered_ids"], verdict["missing_ids"], verdict["complete"],
                        # The exact text the grader saw this turn (incl. a
                        # revealed inject) so calibration replays are verbatim.
                        context_text=scenario_text)

            if is_assessment:
                # attempt_counts was just incremented in the recording block
                # above (recording is mandatory in assessment mode), so this
                # is the attempt the trainee is currently on.
                attempt = st.session_state.get("attempt_counts", {}).get(step_number, 0)
                reply = build_assessment_reply(
                    verdict, steps, attempt,
                    st.session_state.assessment_settings["max_attempts_per_step"])
            else:
                reply = build_reply(verdict, steps)
                if st.session_state.complete and st.session_state.get("record_session"):
                    storage.complete_session(st.session_state.db_session_id)
                    # Advance spaced-repetition state for every SOP source this
                    # session actually exercised. Never let a retention bug block
                    # the trainee's completion — recording is strictly additive.
                    try:
                        st.session_state.retention_updates = \
                            retention.update_from_session(st.session_state.db_session_id)
                    except Exception:
                        log.exception("retention update failed for session %s",
                                      st.session_state.db_session_id)
            st.markdown(reply)
        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.rerun()
