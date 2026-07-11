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

1. **Scenario generation** — pick a site profile and one or more incident
   types (config-driven per profile — see Profiles below). A scenario is
   generated from a retrieval-augmented query against that profile's threat
   catalog (`profiles/<profile>/data/threats`).
2. **Answer key derivation** — the relevant Standard Operating Procedures
   (`profiles/<profile>/data/sops`) are retrieved and used to build an ordered,
   sourced response plan, server-side only. It never reaches the browser.
3. **Socratic tutoring** — a Python state machine (not a prompt) tracks
   progress step by step. Each turn, the trainee's answer is graded for
   coverage against that step's actions; only a hint and the missing/covered
   status are shown, never the underlying key text.

Corpus separation is enforced in code: scenario generation only queries the
`threats` index, and answer-key generation only queries the `sops` index, so
a scenario can't leak the response plan. Source provenance ([S1], [S2], ...)
is resolved in Python from retrieval results, not asked of the model, so it
can't hallucinate a filename.

## Profiles — bring your own corpus

Each organization/site is a **profile**: a directory under `profiles/<name>/`
holding its own threat catalog, SOPs, and incident-type config. The app's
sidebar lets a trainee pick which profile to train against; `profiles/default/`
ships as the example industrial site.

To add your own:

```bash
mkdir -p profiles/acme/data/threats profiles/acme/data/sops
# copy your organization's threat catalog + site security docs into data/threats/*.md
# copy your organization's SOPs into data/sops/*.md

python3 build_index.py acme --infer-types
```

`build_index.py` embeds both corpora into `.index/acme/` and, with
`--infer-types`, asks the model to propose incident-type checkboxes and their
retrieval vocabulary from the threat catalog (writing `profiles/acme/config.json`).
You can hand-edit that file instead — see `profiles/default/config.json` for
the shape (`display_name` + `incident_types: {label: query}`).

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
streamlit run security_simulator.py --server.fileWatcherType=none   # in another
```

`--server.fileWatcherType=none` avoids intermittent instability observed on
some setups when Streamlit's source-file watcher runs alongside the
sentence-transformers/torch import scan; the app doesn't rely on hot-reload
during normal use, so there's no real downside outside active development on
the app's own source.

## Project layout

| File | Purpose |
|---|---|
| `security_simulator.py` | Streamlit UI and session state machine |
| `pipeline.py` | The three stages: scenario, answer key, grading |
| `retrieval.py` | BGE-M3 embedding + index search, per-profile |
| `corpus_config.py` | Loads each profile's `config.json` (incident types) |
| `build_index.py` | CLI to onboard a new profile: build indices, infer incident types |
| `admin_panel.py` | Password-gated sidebar: corpus upload + instructor dashboard |
| `storage.py` | Optional local SQLite persistence of session records (opt-in, see below) |
| `instructor_dashboard.py` | Per-trainee history and most-missed-SOP-step analytics |
| `ollama_client.py` | Local Ollama chat client (streaming, JSON schema) |
| `grading.py` | Grading prompt and leak-detection for hints |
| `calibrate_cutoff.py` | Script used to measure retrieval similarity cutoffs |
| `spike_grader.py` | Standalone grading spike/prototype |
| `profiles/<name>/config.json` | Display name + incident-type -> retrieval-query map |
| `profiles/<name>/data/threats/` | That profile's threat catalog and site security measures |
| `profiles/<name>/data/sops/` | That profile's Standard Operating Procedures corpus |
| `.index/<name>/` | Built embedding indices per profile (gitignored, rebuilt on demand) |
| `.local/sessions.db` | Recorded session records, only if enabled (gitignored) |

## Instructor dashboard & session recording (opt-in)

By default, nothing about a session is written to the server's filesystem —
each trainee gets a downloadable Markdown record at the end and that's it.

An instructor running a cohort can opt in to recording sessions locally
(SQLite) for review:

```bash
SIMULATOR_RECORD_SESSIONS=1 SIMULATOR_ADMIN_PASSWORD=... streamlit run security_simulator.py
```

With `SIMULATOR_RECORD_SESSIONS` set, each trainee sees a "Record this session
for instructor review" checkbox (checked by default, but they can opt out
per-session) and an optional name field before generating a scenario. Nothing
is recorded unless that variable is set — there's no way to record silently.

Recorded sessions show up under **Admin -> Instructor dashboard** (same
password gate as corpus upload, since trainee answers and names are just as
sensitive as the SOP corpus):

- **Sessions** — per-trainee history: scenario, incident types, and which
  steps were completed and in how many attempts.
- **Most-missed SOP steps** — which source document's guidance trainees fail
  to fully cover on their *first* attempt, aggregated across every session.
  This is grouped by source **file**, not exact step text, because each
  session's scenario and answer key are freshly generated (that's the whole
  point of the app) — the file is the one thing that stays stable across
  runs. A high miss rate on a given SOP is a signal the document itself is
  unclear, not that trainees are careless.

## Notes

- No retrieval similarity cutoff is applied (`RETRIEVAL_CUTOFF = None` in
  `pipeline.py`) — measured on this corpus, a fixed threshold either passed
  everything or rejected true hits; bare top-k retrieval is used instead.
- Training records (scenario + model answer + sources) can be downloaded as
  Markdown at the end of a session, regardless of whether session recording
  is enabled.
