# Business Idea: Certus — Operational Readiness Platform

*A one-pager for Certus (formerly the security-simulator project, renamed to
reflect the reframe below). (Updated July 2026 — reframed from "competence
verification for security training" after a first-principles review. All six
iterations of the product ladder are shipped; what remains is Phase-2/3
work that needs customers or cloud infrastructure: doc-system connectors,
managed-cloud tier, embed API, and grader calibration on a design partner's
real answer corpus.)*

## The problem

Strip the security branding and ask what job this actually does: it compiles an
organization's own procedural documents into graded practice, with an answer key
that provably never leaks. That capability solves a bigger problem than better
elearning.

Corporate elearning is compliance theater. It measures **completion**, not
**competence**:

- Multiple-choice quizzes are *recognition* tasks — the answer is on the screen.
  Real incidents demand *free recall under stress*; nobody hands you four options
  when smoke is coming out of Block B.
- Unlimited quiz retries against a fixed answer bank turn assessment into
  brute-force search. By attempt three, participants are pattern-matching
  untried options. Zero learning, full certificate.
- Passive click-through produces a fluency illusion ("I recognized all of that,
  so I know it") with none of the effortful retrieval that actually builds memory.

Everyone in the chain — vendor, L&D buyer, participant — quietly accepts this,
because completion is what's auditable.

But the deeper problem sits one level up, with a different buyer: **risk,
compliance, and operations leaders can't answer "if this happened tonight,
could our people actually execute the procedure?"** Training completion
certificates don't answer that. This platform's real product isn't training —
it's the readiness score and audit evidence that answer that question, with
simulated practice as the instrument that generates the data.

## The insight (why now)

Two things LLMs make possible for the first time:

1. **Grading open-ended, free-recall answers** — the thing MCQs exist to avoid,
   and the exact skill an incident demands.
2. **Freshly generated scenarios per session** — so retrying is *un-gameable*.
   A retry means a new scenario, which is genuinely more practice, not
   answer-bank memorization. The gaming strategy and the learning strategy
   become the same behavior.

## What exists today

Certus is a fully local pipeline (Ollama or any OpenAI-compatible endpoint +
on-device embeddings — SOPs never leave the building), with all six
iterations shipped:

- **Scenario generation** from a threat corpus; **answer key** derived from the
  SOP corpus, server-side only — it never reaches the browser, so trainees can't
  game the grader.
- **Socratic step-by-step tutoring**, now conversational: each free-text answer
  is graded for coverage; only hints and missing/covered status are shown.
- Grounded provenance (source citations computed in code, not by the model).
- **Bring-your-own-corpus, multi-tenant**: any org creates a `profile` with its
  own threat catalog and SOPs; a password-gated admin panel handles upload and
  one-click index build; incident types are inferred from the corpus, not
  hardcoded. A `default` (industrial site) and `hospital` profile both exist.
- **Persistence and an instructor dashboard**: sessions, steps, and per-attempt
  grade events are stored in SQLite; instructors see per-trainee history and
  which SOP source documents are missed most *across* trainees — the seed of
  the SOP-gap flywheel described below.
- **Retention engine**: SM-2 spaced repetition over each trainee's weak SOPs,
  a due-for-review panel that steers incident-type selection (new scenario
  surface, same underlying SOP), and the 30/90-day cohort retention metric.
- **Assessment & compliance mode** (the monetization core): scored, no-hint
  assessments with instructor-set pass thresholds, attempt limits, and timed
  drills; per-assessment audit-grade evidence export (settings snapshot,
  per-step coverage with SOP provenance, grading-integrity statement, SHA-256
  tamper evidence — never the answer key itself); a cohort CSV for GRC
  ingestion; a mandate field stamped into evidence (e.g. OSHA PSM, Joint
  Commission EM); an instructor override/appeal path where the machine
  verdict is preserved alongside the override; and the productized SOP-gap
  report as a standalone export.
- **Dynamic scenarios & tabletop mode**: an advanced-difficulty knob (more
  concurrent threats, deliberate diversions, ambiguous sensor pictures);
  mid-scenario injects generated with the scenario but revealed only at the
  drill's midpoint (answer-key isolation unchanged — the key is derived from
  scenario + inject together); facilitated tabletop team drills recorded and
  retention-tracked under the team's name; and the **readiness heatmap** —
  the executive per-trainee × per-procedure view with staleness honesty (an
  observation older than 90 days reads as "unknown", not as its old score).
- **Reach**: any OpenAI-compatible endpoint (vLLM, LM Studio, llama.cpp,
  OpenAI) via env vars with schema-enforced JSON output and graceful
  fallback; Docker Compose on-prem deploy (app + Ollama sidecar, everything
  in local volumes); per-profile output language for multilingual scenarios
  and tutoring (BGE-M3 retrieval was already multilingual).

The two remaining properties that make this defensible as more than a demo:
answer-key isolation is enforced in code (corpus separation, ID-only grading
responses), and provenance is resolved in Python rather than asked of the
model — so certifiable assessment (below) rests on an architecture, not a
prompt.

## Positioning

Not "better elearning," and not merely a **verification layer** bolted onto
existing LMS training — Certus is an **operational readiness platform**. The
buyer isn't L&D; it's risk, compliance, and operations leadership, who have a
regulatory mandate and a budget for proving readiness, not just delivering content.
Insurers and auditors are a channel accelerant here: a cyber-insurance premium
discount or a clean audit finding is a stronger purchase driver than "better
learning outcomes."

**Geographic wedge: Singapore first** (July 2026 — founder's home market, and
the regulation does the selling): SCDF requires every FSM-appointed building
to run **2 table-top exercises + 2 evacuation drills per year** with the CERT
activated and records kept — a statutory cadence for exactly what Certus
does. MAS-regulated FIs need documented BCM test records behind an annual
Board attestation; CII owners (utilities like grid/gas operators, transport,
healthcare) must exercise IR plans under the Cybersecurity Act and CCoP 2.0;
MHIs on Jurong Island run safety-case regimes under MOM. The shipped
Singapore mandate pack (mandates/sg.json: SCDF, MOM×2, MAS, CSA, MHA — with
citable clauses, cadence tracking, and honest evidence-scope statements)
plus the `sg_highrise` demo profile make the first meeting concrete: "here
is your SCDF TTE evidence, generated from your own ERP." No surveyed
competitor maps Singapore mandates at all.

The wedge is still **security**, but the market is broader: any domain with
written procedures, a high cost of error, and no safe way to rehearse for real.
Segments in descending order of willingness-to-pay per seat:

1. **Safety-critical operations** — energy, utilities, chemicals, manufacturing.
   OSHA/PSM-mandated drills, catastrophic error costs, on-prem story lands
   perfectly. Closest to the current default profile.
2. **Healthcare** — clinical protocols, code responses, Joint Commission
   readiness. Larger TAM, slower sales cycle. The `hospital` profile is the
   first step here.
3. **Financial services & SOC/IR teams** — incident response, fraud/AML
   procedures, regulatory exam prep. Closest to the product's current framing.
4. **Emergency management / public sector** — a later expansion; FEMA-style
   tabletop exercises are a consulting industry this could partially automate.

Horizontal L&D (onboarding, sales training) is a bigger TAM but a race to the
bottom on price — treat as later expansion, not the wedge.

## What makes it stand out

- **Docs-in, drills-out**: point it at an org's existing procedures (today: file
  upload via the admin panel; roadmap: SharePoint/Confluence/Notion connectors)
  and get a drill catalog in minutes — this is the demo that wins deals.
- **Readiness heatmap & trend** (shipped): which procedures, on which
  people and teams, would fail tonight — with staleness honesty (old
  observations read as "unknown") — plus the trend-over-time layer on the
  same recorded data: readiness composition snapshotted across the whole
  history, where staleness makes the line decline when drilling stops, and
  a downloadable board-pack readiness report (now vs 30 days ago, per
  procedure). Twelve months of this longitudinal record is the
  switching-cost moat: it cannot be migrated to a competitor.
- **SOP-gap feedback loop** (seeded, shipped): when trainees consistently miss
  or reasonably deviate from the same step, the *document* gets flagged, not
  just the trainee. No competitor improves the customer's procedures as a
  byproduct of training on them.
- **Certifiable assessment mode** (shipped): the no-leak grading architecture
  now backs scored assessments with audit-grade, tamper-evident evidence
  export — the thing an LMS completion can't be.
- **Deployment spectrum**: fully local or Docker on-prem for the paranoid
  (today, incl. any OpenAI-compatible endpoint), managed cloud for everyone
  else (roadmap), same profile format either way.

## Iteration ladder

1. ✅ **Bring-your-own-corpus** — shipped. Multi-profile, admin-panel upload,
   inferred incident types. Remaining gap: doc-system connectors (folded into
   iteration 6).
2. ✅ **Instructor & analytics layer** — shipped. SQLite persistence, per-trainee
   history, most-missed-SOP tab. Next: per-step/per-team/trended resolution
   (readiness heatmap) and audit-grade export, both in iteration 4.
3. ✅ **Retention engine** — shipped. SM-2 on each trainee's weak SOPs,
   transfer variation (new scenario surface, same underlying SOP), and the
   30/90-day cohort retention metric.
4. ✅ **Assessment & compliance mode** — shipped. Thresholds, attempt limits,
   timed drills, audit-grade evidence export with mandate stamping and
   tamper-evident hashing, cohort CSV, instructor override (appeal path),
   and the productized SOP-gap report.
5. ✅ **Dynamic scenarios & tabletop mode** — shipped. Advanced-difficulty
   knob, mid-scenario injects (revealed mid-drill, answer key derived with
   them so isolation holds), tabletop team drills, and the readiness
   heatmap carried over from iteration 4's notes.
6. ✅ **Reach (core)** — shipped: any OpenAI-compatible model endpoint,
   Docker on-prem deploy, per-profile language. Still open (needs customers
   or cloud infra, Phase 2/3): doc-system connectors (SharePoint/Confluence/
   Notion), managed-cloud tier, embed API, cross-customer benchmarks.

## Phased roadmap

- **Phase 1 (now → ~6 mo):** win one vertical as "readiness drills" —
  energy/manufacturing or SOC teams. Ship iterations 3–4. 5–10 design partners;
  confirm risk/compliance signs the check, not L&D.
- **Phase 2 (~6–18 mo):** generalize — healthcare and financial-services
  verticals, doc connectors, multiplayer tabletops, managed-cloud tier.
- **Phase 3 (18+ mo):** infrastructure for verified human capability —
  certification accepted by auditors/insurers, embed API, benchmark data. The
  moat becomes the readiness dataset plus grading-integrity trust, which traces
  directly back to the answer-key architecture that already exists today.

## The proof metric

**Cohort retention at 30 and 90 days** — "we can show you whether your people
still know the procedure in three months." No LMS vendor can say that;
completion rates can't. Re-cut by team and procedure, the same data becomes the
readiness heatmap the risk buyer actually wants.

## Trust prerequisite

Before any pedagogy-led *or* compliance-led sales motion: grader calibration
and an appeal/override path. One false "you missed this" destroys learner
trust in the whole approach — and now that assessment results carry audit
weight (iteration 4, shipped), a false negative has compliance consequences,
not just trust ones. The **override path is shipped**: an instructor can
review the recorded verbatim answers and override a verdict with a required
note; the machine verdict is preserved alongside it in every evidence export.
Grader calibration is now **instrumented as a flywheel** (July 2026, after a
competitive teardown — see COMPETITIVE_LANDSCAPE.md — showed defensible
grading is the whitespace no competitor contests): instructors review
individual recorded gradings in the dashboard's Calibration tab, each review
becomes a labeled example, the agreement figures (exact-verdict, action-level
precision/recall, override rate) are embedded live in every evidence export
— honestly reported as "unmeasured" until reviews exist — and
`calibrate_grader.py` replays the labeled corpus through the grader so model/
prompt changes are measured against real answers (spike_grader.py's 12
synthetic cases remain as the pre-data regression floor). What still needs a
design partner: volume. The claim a compliance buyer can cite requires
hundreds of reviewed gradings on a real corpus, and every cohort now
accumulates them as a byproduct of normal instruction.
