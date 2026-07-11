# Security Training Simulator

A fully local security-incident training simulator. It generates a realistic
incident scenario, derives an authoritative response plan from a set of SOPs,
and then walks a trainee through the response one step at a time as a
Socratic tutor — grading each answer and nudging toward what's missing
without ever revealing the answer key.

Runs entirely on-device via [Ollama](https://ollama.com) (`qwen2.5:14b`) and
a local [BGE-M3](https://huggingface.co/BAAI/bge-m3) embedding index — no
external API calls.

## How it works

1. **Scenario generation** — pick one or more incident types (Physical
   Security, Cyber Security, Facilities Management). A scenario is generated
   from a retrieval-augmented query against the threat catalog (`data/threats`).
2. **Answer key derivation** — the relevant Standard Operating Procedures
   (`data/sops`) are retrieved and used to build an ordered, sourced response
   plan, server-side only. It never reaches the browser.
3. **Socratic tutoring** — a Python state machine (not a prompt) tracks
   progress step by step. Each turn, the trainee's answer is graded for
   coverage against that step's actions; only a hint and the missing/covered
   status are shown, never the underlying key text.

Corpus separation is enforced in code: scenario generation only queries the
`threats` index, and answer-key generation only queries the `sops` index, so
a scenario can't leak the response plan. Source provenance ([S1], [S2], ...)
is resolved in Python from retrieval results, not asked of the model, so it
can't hallucinate a filename.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requires a local [Ollama](https://ollama.com) install with the model pulled:

```bash
ollama pull qwen2.5:14b
```

## Run

```bash
ollama serve            # in one terminal
streamlit run security_simulator.py   # in another
```

## Project layout

| File | Purpose |
|---|---|
| `security_simulator.py` | Streamlit UI and session state machine |
| `pipeline.py` | The three stages: scenario, answer key, grading |
| `retrieval.py` | BGE-M3 embedding + index search |
| `ollama_client.py` | Local Ollama chat client (streaming, JSON schema) |
| `grading.py` | Grading prompt and leak-detection for hints |
| `calibrate_cutoff.py` | Script used to measure retrieval similarity cutoffs |
| `spike_grader.py` | Standalone grading spike/prototype |
| `data/threats/` | Threat catalog and site security measures |
| `data/sops/` | Standard Operating Procedures corpus |

## Notes

- No retrieval similarity cutoff is applied (`RETRIEVAL_CUTOFF = None` in
  `pipeline.py`) — measured on this corpus, a fixed threshold either passed
  everything or rejected true hits; bare top-k retrieval is used instead.
- Training records (scenario + model answer + sources) can be downloaded as
  Markdown at the end of a session. Nothing is written to the server's
  filesystem.
