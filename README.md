# Certus

A fully local operational readiness platform. It generates a realistic
incident scenario, derives an authoritative response plan from a set of SOPs,
and then walks a trainee through the response one step at a time as a
Socratic tutor — grading each answer and nudging toward what's missing
without ever revealing the answer key.

Runs entirely on-device via [Ollama](https://ollama.com) (`qwen2.5:14b`) and
a local [BGE-M3](https://huggingface.co/BAAI/bge-m3) embedding index — no
external API calls. Alternatively, point it at any OpenAI-compatible endpoint
(vLLM, LM Studio, llama.cpp server, OpenAI) — see "Model endpoints" below —
or deploy on-prem with Docker Compose.

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

Requires a local [Ollama](https://ollama.com) install with **both** models
pulled (scenario generation uses `mistral-nemo:12b`; answer keys and grading
use `qwen2.5:14b`):

```bash
ollama pull qwen2.5:14b
ollama pull mistral-nemo:12b
```

## Run

```bash
ollama serve            # in one terminal
streamlit run certus.py --server.fileWatcherType=none   # in another
```

`--server.fileWatcherType=none` avoids intermittent instability observed on
some setups when Streamlit's source-file watcher runs alongside the
sentence-transformers/torch import scan; the app doesn't rely on hot-reload
during normal use, so there's no real downside outside active development on
the app's own source.

## Project layout

| File | Purpose |
|---|---|
| `certus.py` | Streamlit UI and session state machine |
| `pipeline.py` | The three stages: scenario, answer key, grading |
| `retrieval.py` | BGE-M3 embedding + index search, per-profile |
| `corpus_config.py` | Loads each profile's `config.json` (incident types) |
| `build_index.py` | CLI to onboard a new profile: build indices, infer incident types |
| `admin_panel.py` | Password-gated sidebar: corpus upload + instructor dashboard |
| `storage.py` | Optional local SQLite persistence of session records (opt-in, see below) |
| `retention.py` | Spaced-repetition engine (SM-2) over recorded sessions — see below |
| `assessment.py` | Assessment mode: settings, scoring, evidence export, SOP-gap report — see below |
| `instructor_dashboard.py` | Per-trainee history, most-missed-SOP-step, and retention analytics |
| `ollama_client.py` | Local Ollama chat client (streaming, JSON schema) |
| `grading.py` | Grading prompt and leak-detection for hints |
| `calibration.py` | Grader-calibration flywheel: labeled examples, agreement stats, dataset export — see below |
| `calibrate_grader.py` | CLI: replay expert-labeled gradings through the live grader and report agreement |
| `mandates.py` / `mandates/sg.json` | Regulatory mandate registry (Singapore pack) + cadence tracking — see below |
| `calibrate_cutoff.py` | Script used to measure retrieval similarity cutoffs |
| `spike_grader.py` | Standalone grading spike/prototype (synthetic cases) |
| `tests/headless_checks.py` | End-to-end checks of the real app with the model layer faked (no Ollama needed) |
| `Dockerfile` / `docker-compose.yml` | On-prem container deploy (app + Ollama sidecar) |
| `profiles/<name>/config.json` | Display name, incident types, language, assessment settings |
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
CERTUS_RECORD_SESSIONS=1 CERTUS_ADMIN_PASSWORD=... streamlit run certus.py
```

With `CERTUS_RECORD_SESSIONS` set, each trainee sees a "Record this session
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
- **Retention** — spaced repetition and the 30/90-day proof metric (below).

## Spaced repetition & retention (opt-in, built on session recording)

When session recording is enabled and a trainee gives their name, Certus
tracks which SOP source files they struggled with and schedules re-drills at
expanding intervals (canonical SM-2 — quality is derived from how many
attempts each step took). Next time that trainee returns, the sidebar shows a
**"Due for review"** panel listing overdue SOPs and which incident types to
select to likely — not guaranteed, since every scenario is freshly generated —
exercise them again. A re-test therefore always has a new scenario surface
while holding the underlying SOP constant, so it measures the principle, not
memory of the last drill.

Instructors get a **Retention** tab in the dashboard: a drill queue (who
should re-drill what, most overdue first) and **cohort retention at 30/90
days** — the share of re-encounters ≥30/≥90 days later that the trainee still
handled competently. That metric is computed directly from recorded sessions,
so it works retroactively over history recorded before this feature existed;
a "Rebuild retention schedule from history" button backfills the schedule the
same way. Anonymous sessions are never tracked for retention (there's no way
to tell anonymous trainees apart).

## Assessment & compliance mode (opt-in, built on session recording)

Training mode practices a procedure; **assessment mode measures it**. When
session recording is enabled, the sidebar offers a mode choice. An assessment
runs the same freshly-generated scenario and server-side answer key, but:

- **No hints** — replies report coverage counts only ("2 of 3 actions
  covered"), never what's missing. Clarifying questions about the scenario
  are still free and don't count as attempts.
- **Attempt limit** per step and an optional **time limit** for the whole
  drill (the clock starts after the response plan is generated, so model
  latency never eats the trainee's time).
- **Pass threshold** — the score is the mean per-step coverage of expected
  actions; steps never reached count as zero. Pass/fail is recorded.

Settings are fixed per profile by the instructor (never chosen by the
trainee) under an `assessment` key in `profiles/<name>/config.json`:

```json
"assessment": {
  "pass_threshold": 0.8,
  "max_attempts_per_step": 2,
  "time_limit_minutes": 30,
  "mandate": "OSHA PSM emergency response readiness (29 CFR 1910.119)"
}
```

They are snapshotted onto the session row when the assessment starts, so
editing the config later can't rewrite what a past verdict was judged
against. An assessment requires a trainee name and recording turned on — the
recorded answers *are* the evidence behind the verdict. After an assessment
the model answer is **not** revealed (only which SOP documents to review);
practicing happens in training mode, where every scenario is fresh anyway.

The **Assessments** tab in the instructor dashboard adds:

- **Audit-grade evidence export** per assessment (Markdown): settings and
  mandate, scenario, per-step coverage with SOP provenance, verbatim
  answers, verdicts, an embedded grading-integrity statement, and a SHA-256
  of the record for tamper evidence. The expected-action text itself is
  never included, so evidence can be shared without handing over an answer
  bank. Trainees get their own copy (without verbatim answers) at the end
  of the session.
- **Cohort CSV** of all finished assessments, for GRC tools/spreadsheets.
- **Instructor override (appeal path)** — if reviewing the recorded answers
  shows the grader was wrong or a deviation was operationally sound, the
  instructor can override the verdict with a required note. The machine
  verdict is never mutated; both appear in every export.

The **Most-missed SOP steps** tab now also exports a standalone **SOP-gap
report** (Markdown) — the document-first framing of the same data, ready to
attach to a procedure-review ticket.

## Grader calibration (the flywheel behind the evidence)

"How do I know the grader is right?" is the first question a compliance buyer
asks, so the answer is measured, not asserted. The dashboard's
**Calibration** tab lets an instructor review recorded gradings one at a
time — everything the trainee had said for that step, and which expected
actions the model credited — and record the expert verdict. Every review
becomes a labeled example, and three things fall out of the accumulating
corpus:

- **A per-profile accuracy figure** (exact-verdict agreement plus
  action-level precision/recall), embedded live in every assessment evidence
  export — or an honest "unmeasured on this corpus yet" when no reviews
  exist, same staleness-honesty rule as the readiness heatmap.
- **A regression corpus**: `python3 calibrate_grader.py <profile>` replays
  the labeled events through the current grader (verbatim inputs, including
  any revealed mid-drill inject) — so a model, prompt, or endpoint change is
  measured against real trainee answers, not just `spike_grader.py`'s
  synthetic cases. `--recorded-only` scores the as-recorded verdicts with no
  model call.
- **An exportable dataset** (JSONL, per profile) for external eval harnesses.
  It contains answer-key action text — handle it like the SOPs themselves,
  not like an evidence export.

The instructor override path (above) feeds the same story at coarser grain:
the override *rate* across finished assessments is reported alongside the
agreement figures.

## Regulatory mandate packs (Singapore first)

A profile's `assessment.mandate` config value has always been stamped into
evidence as free text. Setting it to a **registry id** from `mandates/`
upgrades it to a citable mandate block: regulator, instrument, specific
clauses, the requirement in one sentence, and an *evidence-scope* statement
that says plainly what a Certus record does and does not demonstrate (a
physical evacuation drill still has to happen; Certus complements it).
Free-text values keep the old behavior untouched.

The Singapore pack (`mandates/sg.json`) ships six mandates:

| id | What it is |
|---|---|
| `sg-scdf-cert-tte` | SCDF — Fire Safety Act, CERT Regulations, National CERT Standard: FSM premises must run **2 table-top exercises + 2 evacuation drills/year** with CERT activated |
| `sg-mom-wsh-mhi` | MOM — WSH (Major Hazard Installations) Regs 2017 safety-case regime (chemical/process; NEA & SCDF P&FM cross-refs) |
| `sg-mom-wsh-general` | MOM — WSH Act + Risk Management Regs (manufacturing and general workplaces) |
| `sg-mas-bcm` | MAS — BCM Guidelines 2022: regular testing, documented records, annual Board attestation |
| `sg-csa-ccop` | CSA — Cybersecurity Act s.16/16L + CCoP 2.0 §7.3 (CII owners incl. utilities) |
| `sg-mha-ipa` | MHA/SPF — Infrastructure Protection Act 2017 (protected places, special developments) |

Mandates with a drill cadence get **cadence tracking**: the dashboard's
Assessments tab and the readiness report show table-top exercises recorded
in the trailing 12 months against the required number (tabletop sessions are
recognized by their recorded team name). A statutory cadence (SCDF) is
labeled as such; a suggested one (MAS/CSA, where the instrument says
"regular") is labeled as suggested — never presented as law. Physical
evacuation drills are shown as a reminder, never counted automatically.

The **`sg_highrise` demo profile** is a Singapore commercial high-rise (FSM +
CERT structure: Site Main Controller, Site Incident Controller, 995/999
notifications, ERP components) with threat catalog and SOPs ready to index:
`python3 build_index.py sg_highrise`. Its config points at
`sg-scdf-cert-tte`, so demo assessments export SCDF-citable evidence out of
the box.

## Difficulty, injects & tabletop mode

- **Advanced difficulty** (checkbox in training; set per profile for
  assessments via the `assessment.difficulty` config key): more concurrent
  threats, at least one deliberate diversion, deliberately ambiguous sensor
  information — plus a **mid-scenario inject**: a development generated
  together with the scenario but hidden from the trainee until they cross
  the midpoint step ("⚡ Development — the situation has changed…"). Because
  the answer key is derived from scenario + inject together (with inject
  steps pinned to the second half), answer-key isolation is unchanged and
  nothing is ever graded before it has been revealed.
- **Tabletop team drills**: enter participant names in the sidebar to run a
  facilitated group exercise on one screen — the intro addresses the team,
  each participant answers for their own role, and the session (and its
  retention schedule) is recorded under the team's collective name.
  Assessments stay individual: they certify people, not rooms.

## Readiness heatmap & trend

The instructor dashboard's **Readiness** tab is the executive view: latest
observed competence per trainee × SOP document (🟢 ready · 🟡 shaky · 🔴 at
risk · ⚪ unknown — last observation over 90 days old, because an old green
is not a current green), plus per-procedure "trainees ready" counts, worst
first. Derived at read time from recorded drills and assessments.

Below the heatmap, **Readiness over time** adds the longitudinal layer: the
same four states snapshotted at regular intervals across the whole recorded
history, as a stacked composition over a fixed universe of every (trainee,
procedure) pair ever observed. Two honest movements show: pairs not yet
observed count as unknown early on (coverage growing), and a pair whose last
drill ages past 90 days *returns* to unknown — the line goes down when
drilling stops. A readiness number that can only go up isn't a measurement.

The **readiness report** (Markdown download on the same tab) packages this
for a board pack: tonight's picture, the trend table, and per-procedure
ready counts now vs 30 days ago with an improving/holding/declining
direction per document.

## Model endpoints

Ollama on localhost is the default. Two environment overrides:

```bash
# Remote/containerized Ollama:
CERTUS_OLLAMA_URL=http://ollama:11434/api/chat

# Any OpenAI-compatible endpoint (vLLM, LM Studio, llama.cpp server, OpenAI):
CERTUS_OPENAI_BASE_URL=http://localhost:8000/v1
CERTUS_OPENAI_MODEL=Qwen/Qwen2.5-14B-Instruct
CERTUS_OPENAI_SCENARIO_MODEL=...   # optional; defaults to CERTUS_OPENAI_MODEL
CERTUS_OPENAI_API_KEY=...          # optional; most local servers need none
```

The OpenAI path requests `response_format: json_schema` and falls back to
`json_object` with the schema stated in the prompt if the server rejects it.

## Docker deploy (on-prem)

```bash
docker compose up -d --build
docker compose exec ollama ollama pull qwen2.5:14b
docker compose exec ollama ollama pull mistral-nemo:12b
# open http://localhost:8501
```

Profiles are bind-mounted from `./profiles`; indices, session records, the
embedding-model cache, and pulled models live in named volumes — nothing
leaves the machine. Enable recording/admin via the commented environment
variables in `docker-compose.yml`.

## Language

Set `"language": "Nederlands"` (or any language) in a profile's
`config.json` to get scenarios, answer keys, and tutor replies in that
language. SOPs and threat catalogs can already be in any language — BGE-M3
embeddings are multilingual. English profiles use the original, validated
prompts unchanged.

## Testing

```bash
.venv/bin/python tests/headless_checks.py
```

Drives the real app end-to-end (assessment flow, attempt limits, verdicts,
evidence export, override, injects, tabletop, readiness) with the model
layer faked — no Ollama or browser required.

## Notes

- No retrieval similarity cutoff is applied (`RETRIEVAL_CUTOFF = None` in
  `pipeline.py`) — measured on this corpus, a fixed threshold either passed
  everything or rejected true hits; bare top-k retrieval is used instead.
- Training records (scenario + model answer + sources) can be downloaded as
  Markdown at the end of a session, regardless of whether session recording
  is enabled.
