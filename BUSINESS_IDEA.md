# Business Idea: Certus — Operational Readiness Platform

*A one-pager for Certus (formerly the security-simulator project, renamed to
reflect the reframe below). (Updated July 2026 — reframed from "competence
verification for security training" after a first-principles review;
iterations 1–2 of the roadmap are shipped.)*

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

Certus is a fully local pipeline (Ollama + on-device embeddings — SOPs never
leave the building), now with two full iterations shipped:

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
- **Readiness heatmap** (roadmap): which procedures, on which teams, would fail
  tonight, trending over time — the executive view built on the instructor
  dashboard's data.
- **SOP-gap feedback loop** (seeded, shipped): when trainees consistently miss
  or reasonably deviate from the same step, the *document* gets flagged, not
  just the trainee. No competitor improves the customer's procedures as a
  byproduct of training on them.
- **Certifiable assessment mode** (roadmap): the no-leak grading architecture
  supports defensible competence certification — the thing an LMS completion
  can't be.
- **Deployment spectrum**: fully local for the paranoid (today), managed cloud
  for everyone else (roadmap), same profile format either way.

## Iteration ladder

1. ✅ **Bring-your-own-corpus** — shipped. Multi-profile, admin-panel upload,
   inferred incident types. Remaining gap: doc-system connectors (folded into
   iteration 6).
2. ✅ **Instructor & analytics layer** — shipped. SQLite persistence, per-trainee
   history, most-missed-SOP tab. Next: per-step/per-team/trended resolution
   (readiness heatmap) and audit-grade export, both in iteration 4.
3. **Retention engine** — spaced repetition on each trainee's weak steps at
   expanding intervals; transfer variation (new scenario surface, same
   underlying SOP) so re-tests measure the principle, not memory of the last
   drill; retention data rolls up into the readiness heatmap.
4. **Assessment & compliance mode** (priority raised) — the monetization core
   under "sell proof, not practice": thresholds, timed drills, audit-grade
   evidence export mapped to the mandate (fire safety, ISO 27001, HIPAA,
   OSHA/PSM), certifiable assessment, and a productized SOP-gap report.
5. **Dynamic scenarios & tabletop mode** — mid-scenario injects, difficulty
   knobs, multi-role team exercises; automates expensive consultant-led
   tabletops and is a natural team-tier upsell.
6. **Reach** — any OpenAI-compatible model endpoint, Docker on-prem deploy,
   multilingual scenarios, doc-system connectors, managed-cloud tier, and
   (long-term) an embed API for GRC/LMS/IR tools plus cross-customer readiness
   benchmarks.

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
and a trainee appeal/override path. One false "you missed this" destroys
learner trust in the whole approach — and once assessment results carry audit
weight (iteration 4), a false negative has compliance consequences, not just
trust ones. (Calibration seed already exists in `spike_grader.py`.)
