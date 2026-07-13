"""Headless end-to-end checks: the real certus.py/dashboard code driven via
streamlit.testing.v1.AppTest with the model layer faked — no Ollama, no
browser needed. Covers iteration 4 (assessment & compliance) and iterations
5-6 (difficulty/injects, tabletop, readiness matrix, language rule).

Run:  .venv/bin/python tests/headless_checks.py

This is deliberately a plain script, not pytest — same convention as
spike_grader.py. Every section prints an OK line; any failure raises.
"""
import os
import sqlite3
import sys
import tempfile

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
import pipeline  # noqa: E402
import retention  # noqa: E402
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
    print("OK dashboard — all five tabs render against recorded data")


# --- section 5: language rule ---------------------------------------------------

def check_language_rule():
    assert pipeline._language_rule("English", "x") == ""
    assert pipeline._language_rule("", "x") == ""
    assert "Nederlands" in pipeline._language_rule("Nederlands", "your reply")
    print("OK language — English is a byte-identical noop; others amend prompts")


if __name__ == "__main__":
    check_storage_and_scoring()
    check_assessment_flow()
    sid = check_training_advanced_flow()
    check_readiness_and_dashboard(sid)
    check_language_rule()
    print("\nALL HEADLESS CHECKS PASSED")
