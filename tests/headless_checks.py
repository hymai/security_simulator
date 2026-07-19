"""Headless end-to-end checks: the real certus.py/dashboard code driven via
streamlit.testing.v1.AppTest with the model layer faked — no Ollama, no
browser needed. Covers iteration 4 (assessment & compliance) and iterations
5-6 (difficulty/injects, tabletop, readiness matrix, language rule).

Run:  .venv/bin/python tests/headless_checks.py

This is deliberately a plain script, not pytest — same convention as
spike_grader.py. Every section prints an OK line; any failure raises.
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

os.environ["CERTUS_RECORD_SESSIONS"] = "1"
os.environ["CERTUS_ADMIN_PASSWORD"] = "test-pw"
os.environ.pop("CERTUS_OPENAI_BASE_URL", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
APP = os.path.join(REPO, "certus.py")

import storage  # noqa: E402

_tmp = tempfile.mkdtemp()
storage.LOCAL_DIR = _tmp
storage.DB_PATH = os.path.join(_tmp, "sessions.db")

import retrieval  # noqa: E402
retrieval.get_model = lambda: None
retrieval.load_index = lambda profile, name: {"fake": True}

import assessment  # noqa: E402
import calibration  # noqa: E402
import pipeline  # noqa: E402
import retention  # noqa: E402
import ui_colors  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


# --- section 1: storage migration + scoring + override + evidence ------------

def check_storage_and_scoring():
    # Simulate a pre-assessment DB (old schema) and let _migrate upgrade it.
    conn = sqlite3.connect(storage.DB_PATH)
    conn.executescript("""
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT NOT NULL,
        trainee TEXT NOT NULL, incident_types TEXT NOT NULL,
        scenario_text TEXT NOT NULL, total_steps INTEGER,
        started_at TEXT NOT NULL, completed_at TEXT);
    INSERT INTO sessions (profile, trainee, incident_types, scenario_text, started_at)
    VALUES ('default', 'Legacy', '["Physical Security"]', 'old', '2026-01-01T00:00:00+00:00');
    """)
    conn.commit()
    conn.close()
    legacy = storage.list_sessions("default")
    assert legacy[0]["mode"] == "training" and legacy[0]["score"] is None
    print("OK migration — pre-assessment DB upgraded in place")

    sid = storage.start_session(
        "default", "Ada", ["Physical Security"], "scenario",
        mode="assessment",
        settings={"pass_threshold": 0.8, "max_attempts_per_step": 2,
                  "time_limit_minutes": 30, "mandate": "OSHA PSM"})
    steps = [
        {"step": 1, "title": "Contain", "threat": "intruder",
         "actions": {"a1": ["Guard", "lock down gate"], "a2": ["Ops", "call police"]},
         "sources": ["perimeter.md"]},
        {"step": 2, "title": "Report", "threat": "aftermath",
         "actions": {"b1": ["Ops", "file report"], "b2": ["Mgmt", "notify HQ"]},
         "sources": ["reporting.md"]},
    ]
    storage.save_steps(sid, steps)
    storage.record_grade_event(sid, 1, 1, "lock the gate", ["a1"], ["a2"], False)
    storage.record_grade_event(sid, 1, 2, "and call police", ["a1", "a2"], [], True)
    storage.record_grade_event(sid, 2, 1, "file a report", ["b1"], ["b2"], False)
    storage.record_grade_event(sid, 2, 2, "hmm", ["b1"], ["b2"], False)

    detail = storage.session_detail(sid)
    result = assessment.score_session(detail)
    assert abs(result["score"] - 0.75) < 1e-9
    storage.finish_assessment(sid, result["score"], False)

    storage.override_assessment(sid, True, "grader missed a paraphrase")
    row = [s for s in storage.list_sessions("default") if s["id"] == sid][0]
    assert row["passed"] == 0 and row["override_passed"] == 1
    assert assessment.effective_passed(row) is True
    print("OK scoring/override — 75% score; machine FAIL preserved under PASS override")

    md = assessment.evidence_markdown(storage.session_detail(sid), include_answers=True)
    for needle in ("machine verdict", "Instructor override", "perimeter.md",
                   "SHA-256", "grader missed a paraphrase", "lock the gate"):
        assert needle in md, needle
    assert "lock down gate" not in md and "notify HQ" not in md, "key text leaked"
    assert "Trainee answers" not in assessment.evidence_markdown(storage.session_detail(sid))
    csv_text = assessment.cohort_csv(storage.list_sessions("default"))
    assert "Ada" in csv_text and "PASS" in csv_text and "FAIL" in csv_text
    assert "reporting.md" in assessment.sop_gap_report("default")
    print("OK evidence/CSV/gap-report — verdicts + provenance + hash, no key text")

    updates = retention.update_from_session(sid)
    by_src = {u["source"]: u for u in updates}
    assert by_src["perimeter.md"]["quality"] == 4
    assert by_src["reporting.md"]["quality"] == 1
    print("OK retention — assessment feeds SM-2; fail schedules early re-drill")


# --- section 2: assessment flow through the app ------------------------------

FAKE_SCENARIO = {"incident_types": ["Physical Security"], "threats": [],
                 "location": "L", "time": "T",
                 "scenario": "An intruder crosses the fence.",
                 "text": "An intruder crosses the fence."}


def fake_grade_factory(capture: dict):
    def fake_grade(step, scenario_text, prior_answer, new_message, **k):
        capture["last_grading_context"] = scenario_text
        all_ids = set(step["actions"])
        if new_message.startswith("FULL"):
            covered = all_ids
        elif new_message.startswith("HALF"):
            covered = {sorted(all_ids)[0]}
        else:
            covered = set()
        return {"message_type": "answer_attempt", "covered_ids": covered,
                "missing_ids": all_ids - covered, "complete": covered == all_ids,
                "reply": "model hint that must never be shown in assessment"}
    return fake_grade


def check_assessment_flow():
    pipeline.generate_scenario = lambda *a, **k: dict(FAKE_SCENARIO)
    pipeline.generate_answer_key = lambda *a, **k: [
        {"step": 1, "title": "Contain", "threat": "intruder",
         "actions": {"a1": ("Guard", "lock down the gate"),
                     "a2": ("Ops", "call the police")}, "sources": ["perimeter.md"]},
        {"step": 2, "title": "Report", "threat": "aftermath",
         "actions": {"b1": ("Ops", "file the incident report"),
                     "b2": ("Mgmt", "notify headquarters")}, "sources": ["reporting.md"]},
    ]
    pipeline.grade_step = fake_grade_factory({})

    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Assessment — scored, no hints")
    at.run()
    assert any("Pass ≥ 80%" in c.value for c in at.sidebar.caption)

    # Nameless assessment is refused.
    at.sidebar.multiselect[0].set_value(["Physical Security"])
    at.sidebar.button[0].click()
    at.run()
    assert any("needs your name" in w.value for w in at.sidebar.warning)
    assert at.session_state["stage"] == 0
    print("OK validation — nameless assessment blocked")

    at.sidebar.text_input[0].set_value("Ada")
    at.sidebar.button[0].click()
    at.run()
    [b for b in at.button if "begin assessment" in b.label][0].click()
    at.run()
    assert "scored assessment" in at.session_state["messages"][0]["content"]

    at.chat_input[0].set_value("FULL lockdown and police").run()
    last = at.session_state["messages"][-1]["content"]
    assert "all actions covered" in last and "model hint" not in last
    at.chat_input[0].set_value("HALF file the report").run()
    assert "attempt 1 of 2" in at.session_state["messages"][-1]["content"]
    at.chat_input[0].set_value("HALF again").run()
    last = at.session_state["messages"][-1]["content"]
    assert "attempt limit reached" in last and "assessment has ended" in last
    res = at.session_state["assessment_result"]
    assert abs(res["score"] - 0.75) < 1e-9 and res["passed"] is False
    assert any("FAIL" in e.value for e in at.error)
    md = " | ".join(m.value for m in at.markdown)
    assert "SOPs to review" in md and "lock down the gate" not in md
    print("OK assessment flow — attempt limit, FAIL verdict, no hint/key leak")


# --- section 3: training + advanced difficulty + inject + tabletop -----------

def check_training_advanced_flow():
    capture = {}

    def fake_scenario(incident_types, profile="default", difficulty="standard",
                      on_token=None):
        capture["difficulty"] = difficulty
        return {**FAKE_SCENARIO, "difficulty": difficulty,
                "inject": "A second alarm sounds in Block C."
                          if difficulty == "advanced" else None}

    pipeline.generate_scenario = fake_scenario
    pipeline.generate_answer_key = lambda *a, **k: [
        {"step": i, "title": f"S{i}", "threat": "t",
         "actions": {f"{chr(96 + i)}1": ("Guard", f"do thing {i}")},
         "sources": ["perimeter.md"]} for i in range(1, 5)]

    def fake_grade(step, scenario_text, prior_answer, new_message, **k):
        capture["last_grading_context"] = scenario_text
        ids = set(step["actions"])
        return {"message_type": "answer_attempt", "covered_ids": ids,
                "missing_ids": set(), "complete": True, "reply": "ok"}
    pipeline.grade_step = fake_grade

    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.text_input[0].set_value("Facilitator")
    at.sidebar.checkbox[1].set_value(True)                    # advanced difficulty
    at.sidebar.text_input[1].set_value("Ana, Ben, Chidi")     # tabletop team
    at.sidebar.multiselect[0].set_value(["Physical Security"])
    at.sidebar.button[0].click()
    at.run()
    assert capture["difficulty"] == "advanced"

    [b for b in at.button if "begin training" in b.label][0].click()
    at.run()
    intro = at.session_state["messages"][0]["content"]
    assert "facilitated tabletop drill" in intro and "Block C" not in intro

    at.chat_input[0].set_value("do thing 1").run()
    assert "Development" not in at.session_state["messages"][-1]["content"]
    assert "Block C" not in capture["last_grading_context"]
    at.chat_input[0].set_value("do thing 2").run()
    assert "⚡ **Development**" in at.session_state["messages"][-1]["content"]
    at.chat_input[0].set_value("do thing 3").run()
    assert "Block C" in capture["last_grading_context"]
    at.chat_input[0].set_value("do thing 4").run()
    assert at.session_state["complete"] is True
    md = " | ".join(m.value for m in at.markdown)
    assert "Participants" in md and "Mid-drill development" in md
    row = storage.list_sessions("default")[0]
    assert row["trainee"] == "Team: Ana, Ben, Chidi"
    assert '"difficulty": "advanced"' in row["settings"]
    print("OK training/advanced — tabletop intro, midpoint inject reveal, recording")
    return row["id"]


# --- section 4: readiness matrix + dashboard ----------------------------------

def check_readiness_and_dashboard(team_session_id: int):
    retention.update_from_session(team_session_id)
    matrix = retention.readiness_matrix("default")
    cell = matrix["cells"][("Team: Ana, Ben, Chidi", "perimeter.md")]
    assert cell["quality"] == 5 and cell["stale"] is False

    conn = sqlite3.connect(storage.DB_PATH)
    conn.execute("UPDATE sessions SET started_at = '2026-03-01T00:00:00+00:00', "
                 "completed_at = '2026-03-01T01:00:00+00:00' WHERE id = ?",
                 (team_session_id,))
    conn.commit()
    conn.close()
    matrix = retention.readiness_matrix("default")
    assert matrix["cells"][("Team: Ana, Ben, Chidi", "perimeter.md")]["stale"] is True
    print("OK readiness — fresh counts ready; >90 days becomes unknown")

    def _dash():
        import instructor_dashboard
        instructor_dashboard.render()
    at = AppTest.from_function(_dash, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    print("OK dashboard — all six tabs render against recorded data")


# --- section 5: calibration flywheel ------------------------------------------

def check_calibration():
    # Ada's assessment from section 1 (the one with scenario_text 'scenario';
    # sections 2-3 record their own sessions) has 4 grade events without
    # context_text — exercising the pre-column fallback. Add one event WITH a
    # context to cover the verbatim-replay path too.
    sid = [s for s in storage.list_sessions("default")
           if s["scenario_text"] == "scenario"][0]["id"]
    storage.record_grade_event(sid, 2, 3, "notify HQ as well", ["b1", "b2"],
                               [], True, context_text="scenario + inject")

    unlabeled = calibration.examples("default")
    assert all(e["verified_ids"] is None for e in unlabeled)

    # Prior reconstruction mirrors certus.py: attempt N's prior is attempts
    # 1..N-1 for the same session+step, newline-joined.
    step1 = sorted((e for e in unlabeled
                    if e["session_id"] == sid and e["step_number"] == 1),
                   key=lambda e: e["attempt"])
    assert step1[0]["prior_answer"] == ""
    assert step1[1]["prior_answer"] == "lock the gate"
    step2 = sorted((e for e in unlabeled
                    if e["session_id"] == sid and e["step_number"] == 2),
                   key=lambda e: e["attempt"])
    assert step2[2]["prior_answer"] == "file a report\nhmm"
    assert step2[2]["context"] == "scenario + inject"      # stored context wins
    assert step2[0]["context"] == "scenario"               # fallback to session
    print("OK calibration examples — prior rebuilt, context falls back")

    # Label three events: one exact agree, one model over-credit (precision
    # hit), one model under-credit (recall hit).
    storage.upsert_calibration_label(step1[0]["event_id"], ["a1"], "Ines")
    storage.upsert_calibration_label(step1[1]["event_id"], ["a1"], "Ines",
                                     note="a2 was never described")
    storage.upsert_calibration_label(step2[0]["event_id"], ["b1", "b2"], "Ines")

    rows = calibration.labeled("default")
    assert len(rows) == 3
    stats = calibration.agreement_stats(rows)
    # model: {a1}={a1} ✓ · {a1,a2}⊃{a1} ✗ · {b1}⊂{b1,b2} ✗
    assert stats["agree"] == 1 and stats["labeled"] == 3
    assert abs(stats["precision"] - 3 / 4) < 1e-9   # a2 over-credited
    assert abs(stats["recall"] - 3 / 4) < 1e-9      # b2 missed
    per_src = {r["source"]: r for r in calibration.per_source_stats(rows)}
    assert per_src["perimeter.md"]["labeled"] == 2
    print("OK calibration stats — agreement 1/3, precision/recall 75%")

    # Relabeling replaces, never stacks.
    storage.upsert_calibration_label(step1[1]["event_id"], ["a1", "a2"], "Ines",
                                     note="on reflection the paraphrase counts")
    assert calibration.agreement_stats(calibration.labeled("default"))["agree"] == 2

    jsonl = calibration.to_jsonl(calibration.labeled("default"))
    lines = [l for l in jsonl.splitlines() if l]
    assert len(lines) == 3
    first = __import__("json").loads(lines[0])
    assert first["verified_ids"] == ["a1"] and "actions" in first
    print("OK calibration dataset — JSONL round-trips, relabel replaces")

    # The evidence export now carries live calibration figures; the override
    # recorded in section 1 shows up as the override rate.
    md = assessment.evidence_markdown(storage.session_detail(sid))
    assert "Grader calibration" in md and "reviewed by an instructor" in md
    g = calibration.grader_stats("default")
    assert g["overridden"] >= 1 and g["override_rate"] is not None
    print("OK calibration evidence — figures embedded in the evidence export")


# --- section 6: readiness trend -------------------------------------------------

def _dated_session(profile, trainee, source, days_ago):
    """One clean completed drill (q=5) on `source`, back-dated `days_ago`."""
    sid = storage.start_session(profile, trainee, ["Drill"], "s")
    storage.save_steps(sid, [{"step": 1, "title": "T", "sources": [source],
                              "actions": {"a1": ["Role", "act"]}}])
    storage.record_grade_event(sid, 1, 1, "did it", ["a1"], [], True)
    storage.complete_session(sid)
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn = sqlite3.connect(storage.DB_PATH)
    conn.execute("UPDATE sessions SET started_at = ?, completed_at = ? "
                 "WHERE id = ?", (ts, ts, sid))
    conn.commit()
    conn.close()


def check_readiness_trend():
    # Isolated profile: Zoe drilled alpha.md 120 days ago and beta.md 40 days
    # ago. The fixed pair universe is {(Zoe, alpha), (Zoe, beta)}.
    _dated_session("trendsite", "Zoe", "alpha.md", 120)
    _dated_session("trendsite", "Zoe", "beta.md", 40)

    trend = retention.readiness_trend("trendsite")
    assert trend["pairs"] == 2 and trend["step_days"] == 7
    assert len(trend["dates"]) == 18                    # 120-day span, weekly
    # Earliest snapshot (119 days ago): alpha fresh, beta not yet observed.
    assert trend["counts"][0] == {"ready": 1, "shaky": 0, "at_risk": 0,
                                  "unknown": 1}
    # 35 days ago: alpha 85 days old (still fresh), beta 5 days old.
    assert trend["counts"][12]["ready"] == 2
    # Now: alpha aged past 90 days — readiness DECLINES to unknown.
    assert trend["counts"][-1] == {"ready": 1, "shaky": 0, "at_risk": 0,
                                   "unknown": 1}
    print("OK readiness trend — coverage grows, then staleness decays it")

    # as_of rewinds the matrix: 50 days ago beta didn't exist yet.
    then = retention.readiness_matrix(
        "trendsite", as_of=datetime.now(timezone.utc) - timedelta(days=50))
    assert set(then["sources"]) == {"alpha.md"}
    assert then["source_summary"]["alpha.md"] == {"ready": 1, "total": 1}
    print("OK readiness as-of — past view ignores later observations")

    md = assessment.readiness_report("trendsite")
    assert "Tonight's picture:** 1 of 2" in md
    # alpha was ready 30 days ago (age 90 = not yet stale), unknown now.
    assert "| `alpha.md` | 0/1 | 1/1 | ↓ declining |" in md
    assert "| `beta.md` | 1/1 | 1/1 | → holding |" in md
    print("OK readiness report — trend table and per-procedure direction")


# --- section 7: Singapore mandate pack ------------------------------------------

def check_mandates():
    import mandates

    # Registry integrity: every entry auditable — required fields, official
    # sources, and the honesty fields (evidence_scope; cadence_basis when a
    # cadence exists).
    entries = mandates.all_mandates()
    assert len(entries) == 6
    for m in entries:
        for field in ("id", "regulator", "instrument", "clauses",
                      "requirement", "applies_to", "evidence_scope", "sources"):
            assert m.get(field), f"{m.get('id')}: missing {field}"
        assert all(src.startswith("https://") for src in m["sources"])
        if m["cadence"]:
            assert m["cadence_basis"], f"{m['id']}: cadence without basis"
    scdf = mandates.get("sg-scdf-cert-tte")
    assert scdf["cadence"] == {"tabletops_per_year": 2, "drills_per_year": 2}
    assert scdf["cadence_basis"] == "statutory"
    assert mandates.get("OSHA PSM emergency response readiness") is None
    print("OK mandate registry — 6 SG mandates, sourced, honesty fields set")

    # Evidence export: a registry-id mandate expands into the citation block;
    # free text (section 1's OSHA session) keeps legacy behavior.
    ada_sid = [s for s in storage.list_sessions("default")
               if s["scenario_text"] == "scenario"][0]["id"]
    assert "## Mandate" not in assessment.evidence_markdown(
        storage.session_detail(ada_sid))

    sid = storage.start_session(
        "sg_highrise", "Ben", ["Fire"], "tower fire scenario",
        mode="assessment",
        settings={"pass_threshold": 0.8, "max_attempts_per_step": 2,
                  "time_limit_minutes": 30, "mandate": "sg-scdf-cert-tte"})
    storage.save_steps(sid, [
        {"step": 1, "title": "Verify", "threat": "alarm",
         "actions": {"a1": ["FCC Security Officer", "check the CCTV"]},
         "sources": ["fire-response-and-evacuation.md"]}])
    storage.record_grade_event(sid, 1, 1, "check the camera", ["a1"], [], True)
    storage.finish_assessment(sid, 1.0, True)
    md = assessment.evidence_markdown(storage.session_detail(sid))
    for needle in ("## Mandate", "Singapore Civil Defence Force",
                   "2 table-top exercises", "it does not substitute"):
        assert needle in md, needle
    print("OK mandate evidence — SCDF citation block with scope honesty")

    # Cadence math on 'default': one completed Team session (section 3,
    # re-dated to March, still inside 365 days) + three finished assessments
    # (sections 1, 2, and none other — the sg_highrise one is a different
    # profile).
    status = mandates.cadence_status("default", scdf)
    assert status["tabletops"] == 1 and status["assessments"] == 2
    tte = status["requirements"][0]
    assert tte == {"label": "table-top exercises", "required": 2,
                   "recorded": 1, "shortfall": 1}
    assert status["requirements"][1]["recorded"] is None   # physical drills
    print("OK mandate cadence — 1 of 2 TTEs, physical drills never counted")

    # Readiness report for a profile whose configured mandate is a registry
    # id: citation + cadence progress appear in the same artifact.
    _dated_session("sg_highrise", "Zoe", "fire-response-and-evacuation.md", 10)
    md = assessment.readiness_report("sg_highrise")
    for needle in ("## Mandate", "Singapore Civil Defence Force",
                   "Cadence (trailing 365 days)",
                   "table-top exercises: 0 of 2 recorded — 2 more needed"):
        assert needle in md, needle
    print("OK mandate in readiness report — citation and cadence gap")


# --- section 8: error recovery (Ollama down, empty answer key) ------------------

def _error_values(at) -> list[str]:
    """st.error texts from both the main area and the sidebar (the generate
    form lives in the sidebar, so its errors render there)."""
    vals = [e.value for e in at.error]
    try:
        vals += [e.value for e in at.sidebar.error]
    except Exception:
        pass
    return vals


def check_error_recovery():
    from ollama_client import OllamaError

    def boom(*a, **k):
        raise OllamaError("Cannot reach Ollama at http://localhost:11434 "
                          "-- is `ollama serve` running?")

    # (a) Scenario generation fails: friendly error, stage stays 0, no crash.
    pipeline.generate_scenario = boom
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.multiselect[0].set_value(["Physical Security"])
    at.sidebar.button[0].click()
    at.run()
    assert not at.exception, at.exception
    assert any("Scenario generation failed" in v for v in _error_values(at))
    assert at.session_state["stage"] == 0
    print("OK error recovery — scenario failure is a message, not a traceback")

    # (b) Answer-key generation fails: scenario intact, retry stays possible.
    pipeline.generate_scenario = lambda *a, **k: dict(FAKE_SCENARIO)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.multiselect[0].set_value(["Physical Security"])
    at.sidebar.button[0].click()
    at.run()
    assert at.session_state["stage"] == 1
    pipeline.generate_answer_key = boom
    [b for b in at.button if "begin training" in b.label][0].click()
    at.run()
    assert not at.exception, at.exception
    assert any("Response-plan generation failed" in v for v in _error_values(at))
    assert at.session_state["stage"] == 1

    # (c) Model returns ZERO steps: friendly error, same retry path.
    pipeline.generate_answer_key = lambda *a, **k: []
    [b for b in at.button if "begin training" in b.label][0].click()
    at.run()
    assert not at.exception, at.exception
    assert any("empty response plan" in v for v in _error_values(at))
    assert at.session_state["stage"] == 1
    print("OK error recovery — key failure and empty key keep the scenario")

    # (d) A retry after the failures works, and a grading failure mid-step
    # loses nothing: no attempt counted, session resumes when the model is back.
    pipeline.generate_answer_key = lambda *a, **k: [
        {"step": 1, "title": "Contain", "threat": "intruder",
         "actions": {"a1": ("Guard", "lock down the gate")},
         "sources": ["perimeter.md"]}]
    [b for b in at.button if "begin training" in b.label][0].click()
    at.run()
    assert at.session_state["stage"] == 2

    pipeline.grade_step = boom
    at.chat_input[0].set_value("lock the gate").run()
    assert not at.exception, at.exception
    last = at.session_state["messages"][-1]["content"]
    assert "no attempt was counted" in last
    assert at.session_state["current_step"] == 0
    assert at.session_state["step_answers"] == {}
    assert "attempt_counts" not in at.session_state

    def ok_grade(step, scenario_text, prior_answer, new_message, **k):
        ids = set(step["actions"])
        return {"message_type": "answer_attempt", "covered_ids": ids,
                "missing_ids": set(), "complete": True, "reply": "ok"}
    pipeline.grade_step = ok_grade
    at.chat_input[0].set_value("lock the gate").run()
    assert at.session_state["complete"] is True
    print("OK error recovery — failed grading counts nothing; resend succeeds")


# --- section 9: post-deadline submission ----------------------------------------

def check_deadline_late_answer():
    orig_load = assessment.load_settings
    assessment.load_settings = lambda p: {
        "pass_threshold": 0.8, "max_attempts_per_step": 2,
        "time_limit_minutes": 30, "mandate": "", "difficulty": "standard"}
    pipeline.generate_scenario = lambda *a, **k: dict(FAKE_SCENARIO)
    pipeline.generate_answer_key = lambda *a, **k: [
        {"step": 1, "title": "Contain", "threat": "intruder",
         "actions": {"a1": ("Guard", "lock down the gate"),
                     "a2": ("Ops", "call the police")}, "sources": ["perimeter.md"]},
        {"step": 2, "title": "Report", "threat": "aftermath",
         "actions": {"b1": ("Ops", "file the incident report"),
                     "b2": ("Mgmt", "notify headquarters")}, "sources": ["reporting.md"]},
    ]
    pipeline.grade_step = fake_grade_factory({})

    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Assessment — scored, no hints")
    at.run()
    at.sidebar.text_input[0].set_value("Tara")
    at.sidebar.multiselect[0].set_value(["Physical Security"])
    at.sidebar.button[0].click()
    at.run()
    [b for b in at.button if "begin assessment" in b.label][0].click()
    at.run()
    assert at.session_state["deadline"] is not None

    at.chat_input[0].set_value("FULL lockdown and police").run()  # step 1: 2/2

    # The clock runs out while the trainee is composing step 2's answer.
    at.session_state["deadline"] = time.time() - 5
    at.chat_input[0].set_value("LATE: file the report and notify HQ").run()
    assert not at.exception, at.exception

    msgs = at.session_state["messages"]
    assert msgs[-2] == {"role": "user",
                        "content": "LATE: file the report and notify HQ"}
    assert "after time expired" in msgs[-1]["content"]
    res = at.session_state["assessment_result"]
    assert res["reason"] == "time expired"
    # Score-neutral: step 1 full, step 2 zero — the ungraded late answer
    # neither helps nor hurts.
    assert abs(res["score"] - 0.5) < 1e-9

    sid = at.session_state["db_session_id"]
    detail = storage.session_detail(sid)
    late = [e for e in detail["events"]
            if e["trainee_answer"].startswith("LATE:")]
    assert len(late) == 1
    assert late[0]["covered_ids"] == [] and late[0]["complete"] == 0
    assert late[0]["is_graded"] == 0
    md = assessment.evidence_markdown(detail, include_answers=True)
    assert "LATE: file the report and notify HQ" in md
    print("OK deadline — late answer preserved in evidence, ungraded, "
          "score-neutral")

    # M4: the late event must not read as a real attempt anywhere downstream.
    scored = assessment.score_session(detail)
    step2 = [r for r in scored["steps"] if r["step"] == 2][0]
    assert step2["attempts"] == 0, "late-only step must show zero attempts"
    import calibration
    assert not any(e["event_id"] == late[0]["id"]
                  for e in calibration.examples("default")), \
        "late event must not enter the calibration review queue"
    print("OK late answer excluded from attempt count and calibration queue")
    assessment.load_settings = orig_load


# --- section 10: scenario-shopping guard -----------------------------------------

def check_scenario_shopping_guard():
    pipeline.generate_scenario = lambda *a, **k: dict(FAKE_SCENARIO)
    pipeline.generate_answer_key = lambda *a, **k: [
        {"step": 1, "title": "Contain", "threat": "intruder",
         "actions": {"a1": ("Guard", "lock down the gate")},
         "sources": ["perimeter.md"]}]
    pipeline.grade_step = fake_grade_factory({})

    def eve_rows():
        return [s for s in storage.list_sessions("default")
                if s["trainee"] == "Eve"]

    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Assessment — scored, no hints")
    at.run()
    at.sidebar.text_input[0].set_value("Eve")
    at.sidebar.multiselect[0].set_value(["Physical Security"])
    at.sidebar.button[0].click()
    at.run()
    assert at.session_state["stage"] == 1 and len(eve_rows()) == 1
    first_scenario = at.session_state["scenario"]["text"]

    # Reroll attempt mid-assessment: refused, nothing regenerated.
    at.sidebar.button[0].click()
    at.run()
    assert at.session_state["stage"] == 1
    assert at.session_state["scenario"]["text"] == first_scenario
    assert len(eve_rows()) == 1
    print("OK shopping guard — regenerate is locked during an assessment")

    # Refresh-and-retry (new browser session, same DB): the abandoned row is
    # counted and stamped into the new assessment's settings and evidence.
    at2 = AppTest.from_file(APP, default_timeout=30)
    at2.run()
    at2.sidebar.radio[0].set_value("Assessment — scored, no hints")
    at2.run()
    at2.sidebar.text_input[0].set_value("Eve")
    at2.sidebar.multiselect[0].set_value(["Physical Security"])
    at2.sidebar.button[0].click()
    at2.run()
    sid = at2.session_state["db_session_id"]
    row = [s for s in eve_rows() if s["id"] == sid][0]
    assert json.loads(row["settings"])["abandoned_before_this"] == 1
    md = assessment.evidence_markdown(storage.session_detail(sid))
    assert "| Prior unfinished assessments | 1 |" in md
    print("OK shopping guard — abandoned attempts leave a trail in evidence")


# --- section 11: grading regression guard (B4) ----------------------------------

def check_grading_regression_guard():
    """UAT round 2, live against a real model: attempt 1 credited {a2, a4},
    then attempt 2 — grading the FULL joined answer after the trainee only
    ADDED information — credited only {a3}. A real model isn't perfectly
    self-consistent turn to turn; the app must never let a re-grade take away
    what was already credited. Reproduces that exact shape with a stateful
    fake grader, for both the live reply shown to the trainee and the
    persisted grade_events row."""
    pipeline.generate_scenario = lambda *a, **k: dict(FAKE_SCENARIO)
    pipeline.generate_answer_key = lambda *a, **k: [
        {"step": 1, "title": "Contain", "threat": "intruder",
         "actions": {"a1": ("Guard", "lock down"), "a2": ("Ops", "call police"),
                     "a3": ("Mgmt", "log incident"), "a4": ("Sec", "sweep area")},
         "sources": ["perimeter.md"]},
    ]
    calls = {"n": 0}

    def flaky_grade(step, scenario_text, prior_answer, new_message, **k):
        calls["n"] += 1
        covered = {"a2", "a4"} if calls["n"] == 1 else {"a3"}
        all_ids = set(step["actions"])
        return {"message_type": "answer_attempt", "covered_ids": covered,
                "missing_ids": all_ids - covered, "complete": covered == all_ids,
                "reply": "ok"}
    pipeline.grade_step = flaky_grade

    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.text_input[0].set_value("QA Regress")
    at.sidebar.multiselect[0].set_value(["Physical Security"])
    at.sidebar.button[0].click()
    at.run()
    [b for b in at.button if "begin training" in b.label][0].click()
    at.run()

    at.chat_input[0].set_value("lock down and sweep the area").run()
    first = at.session_state["messages"][-1]["content"]
    assert "2 of 4" in first, first

    at.chat_input[0].set_value("also log the incident").run()
    second = at.session_state["messages"][-1]["content"]
    # Union of {a2, a4} (attempt 1) and {a3} (attempt 2) is 3 — must never
    # regress to attempt 2's raw (worse) count of 1.
    assert "3 of 4" in second, second
    assert "1 of 4" not in second

    sid = at.session_state["db_session_id"]
    detail = storage.session_detail(sid)
    last_event = detail["events"][-1]
    assert set(last_event["covered_ids"]) == {"a2", "a3", "a4"}
    assert set(last_event["missing_ids"]) == {"a1"}
    assert last_event["complete"] == 0
    print("OK grading regression guard — coverage is a monotonic running union")


# --- section 12: unescaped model/corpus text into unsafe_allow_html (M9) -------

def check_html_escaping_guard():
    """Incident-type labels and scenario fields are model output seeded from
    admin-uploaded corpus text, then rendered with unsafe_allow_html=True
    (ui_colors.badge, certus.render_scenario_display). A crafted corpus
    document making the model echo markup into one of these fields must not
    reach a trainee's DOM unescaped."""
    payload = "<img src=x onerror=alert(1)>"
    badge_html = ui_colors.badge(payload)
    assert payload not in badge_html
    assert "&lt;img" in badge_html and "onerror=alert(1)&gt;" in badge_html
    print("OK ui_colors.badge() HTML-escapes its label")

    # Driven through the real app (not a direct import of certus.py, which is
    # a top-level Streamlit script) so this exercises the exact call site:
    # render_scenario_display() -> st.markdown(..., unsafe_allow_html=True).
    pipeline.generate_scenario = lambda *a, **k: {
        "incident_types": ["Physical Security"],
        "threats": [{"incident_type": "<script>alert(1)</script>",
                     "description": "<b>bold</b> desc"}],
        "location": "<svg onload=alert(2)>",
        "time": "now",
        "scenario": "body <script>evil()</script>",
        "text": "plain text record, unaffected",
    }
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.multiselect[0].set_value(["Physical Security"])
    at.sidebar.button[0].click()
    at.run()
    assert not at.exception, at.exception
    rendered = " | ".join(m.value for m in at.markdown)
    for raw in ("<script>alert(1)</script>", "<svg onload=alert(2)>",
               "<script>evil()</script>", "<b>bold</b>"):
        assert raw not in rendered, f"unescaped markup leaked through: {raw!r}"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "&lt;svg onload=alert(2)&gt;" in rendered
    print("OK render_scenario_display() HTML-escapes model-derived fields")


# --- section 13: team-forgery guard + case-insensitive abandon match -----------

def check_team_forgery_and_case_insensitive_match():
    import mandates

    # M6: unfinished_assessments() must casefold like normalize_trainee()
    # elsewhere — retyping a name with different capitalization must not
    # reset the abandoned-attempt count F3 relies on.
    pipeline.generate_scenario = lambda *a, **k: dict(FAKE_SCENARIO)
    pipeline.generate_answer_key = lambda *a, **k: [
        {"step": 1, "title": "Contain", "threat": "intruder",
         "actions": {"a1": ("Guard", "lock down the gate")},
         "sources": ["perimeter.md"]}]
    pipeline.grade_step = fake_grade_factory({})

    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value("Assessment — scored, no hints")
    at.run()
    at.sidebar.text_input[0].set_value("Mallory")
    at.sidebar.multiselect[0].set_value(["Physical Security"])
    at.sidebar.button[0].click()
    at.run()   # abandoned: never finished

    at2 = AppTest.from_file(APP, default_timeout=30)
    at2.run()
    at2.sidebar.radio[0].set_value("Assessment — scored, no hints")
    at2.run()
    at2.sidebar.text_input[0].set_value("MALLORY")   # different capitalization
    at2.sidebar.multiselect[0].set_value(["Physical Security"])
    at2.sidebar.button[0].click()
    at2.run()
    sid = at2.session_state["db_session_id"]
    row = storage.session_detail(sid)
    assert json.loads(row["settings"])["abandoned_before_this"] == 1, \
        "case-insensitive match must still count the earlier abandoned attempt"
    print("OK M6 — abandoned-assessment match is casefolded like normalize_trainee")

    # M3: a solo trainee typing "Team: ..." directly into the plain name
    # field must NOT count as a genuine tabletop exercise — only a real
    # roster entered via the "Tabletop team" field (team_size > 0) should.
    scdf = mandates.get("sg-scdf-cert-tte")
    before = mandates.cadence_status("default", scdf)["tabletops"]

    forged_sid = storage.start_session(
        "default", "Team: Solo", ["Physical Security"], "scenario",
        mode="training")   # team_size defaults to 0 — no roster was entered
    storage.complete_session(forged_sid)
    after_forged = mandates.cadence_status("default", scdf)["tabletops"]
    assert after_forged == before, \
        "a 'Team: ' name prefix alone must not inflate tabletop cadence"

    real_sid = storage.start_session(
        "default", "Team: Ana, Ben", ["Physical Security"], "scenario",
        mode="training", team_size=2)
    storage.complete_session(real_sid)
    after_real = mandates.cadence_status("default", scdf)["tabletops"]
    assert after_real == before + 1, \
        "a real roster (team_size > 0) must count toward tabletop cadence"
    print("OK M3 — forged 'Team: ' name prefix does not inflate tabletop cadence")


# --- section 14: language rule ---------------------------------------------------

def check_language_rule():
    assert pipeline._language_rule("English", "x") == ""
    assert pipeline._language_rule("", "x") == ""
    assert "Nederlands" in pipeline._language_rule("Nederlands", "your reply")
    print("OK language — English is a byte-identical noop; others amend prompts")


if __name__ == "__main__":
    check_storage_and_scoring()
    check_assessment_flow()
    sid = check_training_advanced_flow()
    check_calibration()
    check_readiness_trend()
    check_mandates()
    check_readiness_and_dashboard(sid)
    check_language_rule()
    # These record extra sessions on 'default', so they run after the
    # sections whose counts (cadence, readiness) assume a fixed history.
    check_error_recovery()
    check_deadline_late_answer()
    check_scenario_shopping_guard()
    check_grading_regression_guard()
    check_html_escaping_guard()
    check_team_forgery_and_case_insensitive_match()
    print("\nALL HEADLESS CHECKS PASSED")
