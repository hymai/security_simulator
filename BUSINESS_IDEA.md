# Business Idea: Competence Verification for Security Training

*A one-pager for the security-simulator project. (July 2026)*

## The problem

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

## The insight (why now)

Two things LLMs make possible for the first time:

1. **Grading open-ended, free-recall answers** — the thing MCQs exist to avoid,
   and the exact skill an incident demands.
2. **Freshly generated scenarios per session** — so retrying is *un-gameable*.
   A retry means a new scenario, which is genuinely more practice, not
   answer-bank memorization. The gaming strategy and the learning strategy
   become the same behavior.

## What exists today

A fully local pipeline (Ollama + on-device embeddings — SOPs never leave the
building):

- **Scenario generation** from a threat corpus; **answer key** derived from the
  SOP corpus, server-side only — it never reaches the browser, so trainees can't
  game the grader.
- **Socratic step-by-step tutoring**: each free-text answer is graded for
  coverage; only hints and missing/covered status are shown.
- Grounded provenance (source citations computed in code, not by the model) and
  exportable session transcripts — *evidence of competence*, not a certificate
  of exposure.

## Positioning

Not "better elearning" — a **verification layer** that sits on top of existing
LMS training. Click through the LMS module as before, then *prove it here*.
This sells alongside incumbents instead of against them.

Beachhead: organizations where failure is expensive and competence actually
matters — corporate/physical security, safety-critical operations, healthcare
(code responses), SOC/IR teams, labs, schools.

## Iteration ladder

1. **Bring-your-own-corpus** — any org drops in its own SOPs and threat docs;
   incident types derived from the corpus. Turns a single-site demo into a
   general tool.
2. **Instructor & analytics layer** — persist sessions; show which SOP steps are
   missed most *across* trainees (surfaces unclear SOPs, not just weak trainees).
3. **Retention engine** — spaced repetition on each trainee's weak steps at
   expanding intervals; transfer variation (new scenario surface, same underlying
   SOP) so re-tests measure the principle, not memory of the last drill.
4. **Assessment & compliance mode** — thresholds, timed drills, certificates;
   attaches to mandated-training budgets (fire safety, ISO 27001, HIPAA).
5. **Dynamic scenarios & tabletop mode** — mid-scenario injects, difficulty
   knobs, multi-role team exercises; automates expensive consultant-led tabletops.
6. **Reach** — any OpenAI-compatible model endpoint, Docker on-prem deploy,
   multilingual scenarios.

## The proof metric

**Cohort retention at 30 and 90 days** — "we can show you whether your people
still know the procedure in three months." No LMS vendor can say that;
completion rates can't.

## Trust prerequisite

Before any pedagogy-led sales motion: grader calibration and a trainee
appeal/override path. One false "you missed this" destroys learner trust in the
whole approach. (Calibration seed already exists in `spike_grader.py`.)
