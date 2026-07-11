"""
The three stages, wiring retrieval + the local model together.

  generate_scenario(incident_types)  -> scenario dict          (creative, temp 0.8)
  generate_answer_key(scenario)      -> ordered step list       (deterministic, temp 0)
  grade_step(step, trainee_answer)   -> verdict dict            (deterministic, temp 0)

Corpus separation is enforced here: generate_scenario queries only the `threats`
index, generate_answer_key only the `sops` index. The scenario generator never
sees the SOPs, so a scenario cannot leak the response plan.

Provenance is computed in Python, never asked of the model: retrieved chunks are
labelled [S1], [S2], ... with their filenames; the model returns which S-labels a
step drew from; we map those back to filenames. The model never reproduces a
filename, so it can't hallucinate one.
"""

import logging

import grading
import retrieval
from ollama_client import ollama_chat

log = logging.getLogger("simulator.pipeline")

# Measured with calibrate_cutoff.py (BGE-M3, this corpus), not inherited:
#   SOPs   — weakest true hit 0.444, strongest out-of-domain 0.407  (gap 0.037)
#   threats— weakest true hit 0.691, strongest out-of-domain 0.407  (gap 0.284)
# The old ada-002 cutoff of 0.8 would have rejected EVERY correct hit here. On the
# SOP corpus the true-hit and noise bands nearly touch, so no threshold separates
# them cleanly — a cutoff would risk dropping good context (the old app's "I don't
# know" failure). Stage-2/1 queries are always in-domain anyway, so we rely on
# top-k selection and let the model ignore weak chunks. Bare top-k, no threshold.
RETRIEVAL_CUTOFF: float | None = None
RETRIEVAL_K = 6
NUM_CTX = 8192

# Map each incident-type checkbox to threat-catalog vocabulary for retrieval.
_TYPE_QUERY = {
    "Physical Security": "unauthorized perimeter entry, intruder crossing the Zone 3 fence, tailgating",
    "Cyber Security": "intrusion into the SCADA OT control network, compromised engineering workstation, ransomware",
    "Facilities Management": "fire in an occupied building, Block B smoke detectors, loss of power or HVAC",
}

# --- Stage 1: scenario generation ------------------------------------------

_SCENARIO_SCHEMA = {
    "type": "object",
    "properties": {
        "incident_types": {"type": "array", "items": {"type": "string"}},
        "threats": {"type": "array", "items": {"type": "string"}},
        "location": {"type": "string"},
        "time": {"type": "string"},
        "scenario": {"type": "string"},
    },
    "required": ["incident_types", "threats", "location", "time", "scenario"],
}

_SCENARIO_SYSTEM = """You are a creative scenario writer for security-incident training.

You are given the selected incident types and reference material describing the
site's threats and its security systems. Write a realistic training scenario.

Rules:
- Choose two or three concrete threats that fit the selected incident types.
- Mention only the security systems that the chosen threats would actually
  trigger (e.g. a perimeter alarm, a fire detector). Do not invent systems.
- Perpetrators may behave unpredictably and may not breach every layer of defense;
  they may use a diversion.
- Do NOT reveal any response actions, procedures, or the site's layered-defense
  reasoning. Describe only what happens and what is observed — never what staff
  should do about it.
- Keep the scenario under 150 words.

Return only JSON."""


def generate_scenario(incident_types: list[str], on_token=None) -> dict:
    """Stage 1. Retrieve from the threats corpus and write a scenario.

    `on_token(count, elapsed_s)`, if given, is called as the model streams —
    see ollama_client.ollama_chat. Generation is ~6 tok/s on this model/
    hardware (measured), so this is for progress display, not speed.
    """
    query = "; ".join(_TYPE_QUERY[t] for t in incident_types if t in _TYPE_QUERY)
    index = retrieval.load_index("threats")
    hits = retrieval.search(index, query, k=RETRIEVAL_K, cutoff=RETRIEVAL_CUTOFF)

    sources = _label_sources(hits)
    user = (
        f"Selected incident types: {', '.join(incident_types)}\n\n"
        f"Reference material:\n{sources}"
    )
    result = ollama_chat(_SCENARIO_SYSTEM, user, _SCENARIO_SCHEMA,
                         temperature=0.8, num_ctx=NUM_CTX, on_token=on_token)
    result["text"] = _render_scenario(result)
    return result


def _render_scenario(s: dict) -> str:
    return (
        f"**Incident type & Threat**\n"
        + "\n".join(f"- {t}" for t in s.get("threats", [])) + "\n\n"
        f"**Location**: {s.get('location', '')}\n\n"
        f"**Time**: {s.get('time', '')}\n\n"
        f"**Scenario**\n\n{s.get('scenario', '')}"
    )


# --- Stage 2: answer-key generation ----------------------------------------

_KEY_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string"},
                                "action": {"type": "string"},
                            },
                            "required": ["role", "action"],
                        },
                    },
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "actions", "source_ids"],
            },
        },
    },
    "required": ["steps"],
}

_KEY_SYSTEM = """You are a security trainer building the authoritative model answer for a scenario.

You are given the scenario and the relevant Standard Operating Procedures, each
source labelled [S1], [S2], and so on.

Produce an ordered list of response steps grounded ONLY in the provided sources —
do not use outside knowledge. Deal with the most severe threat first. Every threat
in the scenario must be responded to, resolved, and reported.

For each step give:
- a short title,
- the actions to take, each as a specific action performed by a named human role,
- source_ids: the labels ([S1], ...) of the sources that step is based on.

Return only JSON."""


def generate_answer_key(scenario: dict, on_token=None) -> list[dict]:
    """Stage 2. Retrieve from the SOP corpus and derive the ordered answer key.

    Returns steps as:
      {"step": int, "title": str,
       "actions": {aid: (role, action)}, "sources": [filename, ...]}
    Action ids are assigned in Python (a1, a2 for step 1; b1, b2 for step 2; ...)
    so grading has a stable id space regardless of what the model emits.

    `on_token` — see generate_scenario. This is the slower of the two
    generation stages (measured ~95s, mostly decode), so progress display
    matters most here.
    """
    scenario_text = scenario.get("scenario", "") or scenario.get("text", "")
    index = retrieval.load_index("sops")
    hits = retrieval.search(index, scenario_text, k=RETRIEVAL_K, cutoff=RETRIEVAL_CUTOFF)

    label_to_source = {f"S{i}": h["source"] for i, h in enumerate(hits, 1)}
    user = (
        f"Scenario:\n{scenario_text}\n\n"
        f"Sources:\n{_label_sources(hits)}"
    )
    raw = ollama_chat(_KEY_SYSTEM, user, _KEY_SCHEMA, temperature=0, num_ctx=NUM_CTX,
                      on_token=on_token)

    steps = []
    for i, s in enumerate(raw.get("steps", []), 1):
        prefix = chr(ord("a") + i - 1)
        actions = {
            f"{prefix}{j}": (a["role"], a["action"])
            for j, a in enumerate(s.get("actions", []), 1)
        }
        sources = sorted({
            label_to_source[sid.strip("[]")]
            for sid in s.get("source_ids", [])
            if sid.strip("[]") in label_to_source
        })
        steps.append({"step": i, "title": s.get("title", f"Step {i}"),
                      "actions": actions, "sources": sources})
    return steps


# --- Stage 3: grading ------------------------------------------------------

def grade_step(step: dict, trainee_answer: str) -> dict:
    """Stage 3. Grade one step's answer. Returns coverage + hint; never the key.

    Advancement is decided by the caller from `complete` (covered == all ids) —
    the model's own step_complete boolean is treated as advisory only.
    """
    all_ids = set(step["actions"])
    user = grading.build_user_prompt(
        step["step"], step["title"], step["actions"], trainee_answer)
    result = ollama_chat(grading.SYSTEM, user, grading.SCHEMA, temperature=0, num_ctx=NUM_CTX)

    covered = set(result["covered_action_ids"]) & all_ids   # discard hallucinated ids
    missing = all_ids - covered
    hint = result.get("hint", "")

    # Belt-and-suspenders: the schema design already prevents the key from
    # reaching the output, but re-run the spike's overlap check on the live hint.
    missing_texts = {aid: step["actions"][aid][1] for aid in missing}
    leaked = grading.leaks(hint, missing_texts)
    if leaked:
        log.warning("hint overlapped missing action(s) %s; suppressing hint", leaked)
        hint = "You're missing something for this step — think about which roles still have an action."

    return {
        "covered_ids": covered,
        "missing_ids": missing,
        "complete": covered == all_ids,
        "hint": hint,
    }


# --- shared helper ---------------------------------------------------------

def _label_sources(hits: list[dict]) -> str:
    return "\n\n".join(
        f"[S{i}] ({h['source']})\n{h['text']}" for i, h in enumerate(hits, 1)
    )
