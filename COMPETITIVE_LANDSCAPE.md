# Competitive Landscape: Certus

*Researched July 2026. Prompted by the critique "it's easy to build / if no one
else is building it there's no demand." Companion to [BUSINESS_IDEA.md](BUSINESS_IDEA.md).
Every vendor claim below is sourced; claims seen in only one source are marked
(single-sourced). Vendor marketing is reported as claimed, not verified.*

## TL;DR

Demand is validated three ways: direct AI-tabletop competitors exist and are
shipping fast (ThreatGEN, ChaosTrack), the incumbent substitute is a
[$30–50k-per-exercise consulting spend](https://www.ciodive.com/news/tabletop-exercises-security-breach-immersive-labs-osterman/583607/),
and adjacent platforms (Immersive Labs, Uptime Labs) sell readiness to
HSBC/Goldman-tier logos. The commenter's "no demand" premise is false.

But "easy to build" is half-confirmed: **ThreatGEN's AutoTableTop already
accepts uploaded IR plans and scores teams against them, self-hosted, from
$350** — so "docs-in, drills-out" *alone* is occupied territory in cyber.
What no one has: individual free-recall competence verification with a
no-leak answer-key architecture, a calibrated grader, longitudinal retention
metrics (30/90-day), and tamper-evident per-person evidence — i.e. the
*verified readiness record*, not the exercise. That's the defensible
whitespace, and every competitor's uncalibrated "AI scores you" claim is the
opening.

---

## 1. Per-competitor teardown

### ThreatGEN — AutoTableTop / TableTopGPT *(closest direct competitor)*

- **What it does**: AI-generated, AI-facilitated incident-response tabletop
  exercises. "The AI builds the scenario, the timeline, the injects, the
  decision points — tuned to the threats you actually face"
  ([threatgen.com/autotabletop](https://threatgen.com/autotabletop)).
- **BYO documents**: **Yes** — accepts uploaded incident response plans,
  network diagrams and other context, and "scores them against your IR plan"
  ([autotabletop pages](https://threatgen.com/autotabletop); corroborated by
  reseller [Arcova](https://arcova.com/cyber-operational-resilience/automated-ir-tabletop-exercises-with-threatgen-autotabletop/)).
- **Deployment**: self-hosted option — "deploy AutoTableTop entirely within
  your own environment… nothing leaves your network"
  ([threatgen.com](https://threatgen.com/)). How much of the AI stack is
  actually local (vs. API passthrough) is not stated.
- **Assessment model**: team/tabletop level — a facilitated group discussion
  exercise with AI scoring of the *team's* responses. No claim of
  per-individual free-recall grading, answer-key isolation, retention
  tracking, or spaced repetition.
- **Buyer/vertical**: cyber & OT/ICS security (critical infrastructure, DoD
  contractors); SOC and IR leadership.
- **Pricing**: **$350 for 7-day access up to $10,000/year**; $7,500 on-site
  facilitation package (single-sourced, from their portal/announcement pages).
  This anchors the low end of the market hard.
- **Threat to Certus**: highest. Occupies "AI tabletop from your own plans,
  on-prem" in cyber. Does not occupy: individual competence verification,
  retention, audit-grade individual evidence, non-cyber verticals.

### ChaosTrack

- **What it does**: automated cyber tabletop simulations in a "Slack-like
  interface"; canned scenario library plus a custom scenario builder
  ([chaostrack.com/simulations](https://chaostrack.com/simulations)).
- **BYO documents**: custom scenario *builder* (manual authoring), not
  derivation from uploaded SOPs.
- **Deployment**: cloud SaaS (mobile/browser positioning; no on-prem claim).
- **Assessment model**: "100% automated reports that summarize right and
  wrong turns" — decision-path level, no free-recall grading claim.
- **Compliance angle**: strong — "SOC2, Audit, Executive, and Gap Analysis
  reports… show evidence to oversight agencies, regulators, and attorneys,"
  plus peer benchmarking. Note: this is evidence *an exercise happened and
  how it went*, not evidence *a specific person can execute a procedure*.
- **Pricing signals**: sells against consulting — "cut costs by up to 90%."
- **Threat to Certus**: medium. Validates the compliance-evidence motion;
  cloud-only and cyber-only.

### Sklls

- **What it does**: generative **voice** AI simulator for emergency call
  operators (911/112 dispatch): the AI "calls, listens, and responds like a
  real person in distress" ([sklls.ai](https://www.sklls.ai/)).
- **Assessment model**: the most credible grading claim in the set — "every
  AI conversation is automatically scored against" customer-provided
  "evaluation protocols." That is open-ended performance scored against the
  customer's own rubric.
- **Deployment**: cloud only ("no setup or integration required"); GDPR /
  ISO 27001 / "EU AI Act ready" badges. Customers in 6+ countries.
- **Buyer/vertical**: narrow — emergency communications centers. Per-operator
  KPI tracking with gamification; no spaced-repetition/retention claim, no
  audit-evidence product.
- **Threat to Certus**: low direct overlap, high strategic signal — proof
  that *verticalized, protocol-scored simulation* sells in public safety.

### Immersive Labs — Crisis Sim

- **What it does**: enterprise crisis simulations; scenario catalog plus an
  "AI Scenario Generator to create tailored content in minutes"
  ([immersivelabs.com/products/crisis-sim](https://www.immersivelabs.com/products/crisis-sim)).
- **BYO documents**: no confirmed ingestion of customer playbooks; custom
  scenarios are authored by a "Crisis Sim Manager" role
  ([support docs](https://support.immersivelabs.com/hc/en-us/articles/8387057749265-What-is-Immersive-Crisis-Simulations)).
- **Assessment model**: decision-point analytics — "decision latency,
  accuracy, and strategic outcomes," team "Alignment Data"
  (Unified/Split/Fragmented), and an executive **Resilience Score**
  ([after-action reporting](https://www.immersivelabs.com/whats-new/how-to-use-automated-evidence-based-reporting-to-validate-crisis-readiness)).
  Structured choices, not free recall.
- **Deployment/pricing**: cloud; enterprise sales, undisclosed pricing.
  Logos: HSBC, Goldman Sachs, National Grid, Citi.
- **Threat to Certus**: owns the enterprise "readiness score for the board"
  narrative. Its Resilience Score is exercise-participation-derived, not
  competence-verified — that's the seam.

### Uptime Labs

- SRE/incident-management drills, cloud SaaS, "objective analytics at
  individual and team level," Time-to-Recovery improvement stories
  ([uptimelabs.io](https://www.uptimelabs.io/)). Tech-ops vertical, canned
  library, per-user pricing "significantly lower" than tabletops. Low overlap.

### RangeForce / CybExer *(cyber ranges — adjacent, not direct)*

- Hands-on technical skill ranges, not procedural drills.
  [RangeForce](https://www.rangeforce.com/) (acquired by Cyberbit): cloud
  SOC-skilling modules. [CybExer](https://cybexer.com/): bespoke on-prem
  ranges and digital twins for government/critical infrastructure,
  usage-based pricing. They verify *tool skills*, not *procedure execution*.
  Relevant mainly as budget competitors in the SOC segment.

### SOP-to-microlearning tools (PowerRecall, SC Training, Disco)

- The "docs-in, training-out" claim is **not unique to Certus**:
  [PowerRecall](https://www.powerdms.com/policy-learning-center/blog/transform-learning-with-ai-policy-training)
  converts policy docs into flashcards **with spaced repetition**;
  [SC Training](https://aitoolsbakery.com/blog/best-ai-tools-for-manufacturing-safety-training/)
  turns policy PDFs into microcourses with quizzes.
- But they are *recognition-task* engines (flashcards/MCQ) — exactly the
  completion-theater failure mode in BUSINESS_IDEA.md. None do scenario
  drills, free-recall grading, or readiness evidence. They matter because a
  buyer skimming feature lists can't tell the difference — positioning must
  make the recognition-vs-recall distinction explicit.

### The incumbent substitute: consulting + free government content

- Facilitated tabletop exercises average **~$30k, with 20% of orgs spending
  >$50k, typically annually**
  ([CIO Dive / Osterman](https://www.ciodive.com/news/tabletop-exercises-security-breach-immersive-labs-osterman/583607/);
  corroborated $25–75k practitioner range on
  [IAEM healthcare list](https://groups.google.com/g/iaem-healthcare/c/IEo5HKWPgCI)
  and [IANS](https://www.iansresearch.com/what-we-do/consulting/tabletop-exercises)).
- [CISA ships free tabletop exercise packages](https://www.cisa.gov/resources-tools/services/cisa-tabletop-exercise-packages)
  — the zero-cost anchor; generic, unfacilitated, no scoring.
- This spend is the demand evidence: budget exists, frequency is
  constrained (annual) by cost, and every AI vendor above sells "10x cheaper,
  run it monthly." Certus sells the same arbitrage *plus* per-person
  verification the consultants never produced.

---

## 2. Gap matrix

✅ = shipped/claimed · ⚠️ = partial · ❌ = absent/no claim · ? = unknown

| Capability | Certus | ThreatGEN | ChaosTrack | Sklls | Immersive | Uptime Labs | Ranges (RF/CybExer) | SOP-microlearning | Consulting |
|---|---|---|---|---|---|---|---|---|---|
| Scenarios/answers derived from customer's own SOPs | ✅ | ✅ IR-plan upload | ⚠️ manual builder | ⚠️ rubric upload | ⚠️ AI generator, no doc ingest confirmed | ? | ❌ | ✅ (docs→quiz) | ✅ (human) |
| Free-recall grading of open-ended answers | ✅ | ⚠️ team responses scored | ❌ decision paths | ✅ spoken, vs rubric | ❌ decision points | ❌ | ❌ | ❌ MCQ/flashcards | ⚠️ facilitator judgment |
| Answer-key isolation / anti-gaming architecture | ✅ enforced in code | ❌ no claim | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | n/a |
| Grader calibration claim (published accuracy) | ⚠️ seed only (`spike_grader.py`) | ❌ | ❌ | ❌ | ❌ | ❌ | n/a | ❌ | n/a |
| Fully local incl. model / air-gap | ✅ Ollama + local embeddings | ⚠️ "self-hosted" (AI stack locality unstated) | ❌ | ❌ | ❌ | ❌ | ⚠️ CybExer on-prem ranges | ❌ | ✅ (paper) |
| Audit-grade, tamper-evident **per-person** evidence | ✅ SHA-256, mandate stamp | ❌ | ⚠️ exercise-level reports | ❌ | ⚠️ AARs, Resilience Score | ❌ | ❌ | ⚠️ completion records | ⚠️ AAR |
| 30/90-day retention metric / spaced repetition | ✅ SM-2 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ SR on flashcards | ❌ |
| SOP-gap feedback (flags the document) | ✅ | ❌ | ⚠️ "gaps in policies" in reports | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ (human AAR) |
| Readiness heatmap w/ staleness honesty | ✅ | ❌ | ❌ | ⚠️ per-operator KPIs | ⚠️ Resilience Score, no staleness | ⚠️ skills gaps | ⚠️ skill telemetry | ❌ | ❌ |
| Beyond-cyber verticals (OSHA, Joint Commission…) | ✅ profiles | ❌ cyber/OT | ❌ cyber | ✅ public safety only | ⚠️ crisis mgmt generally | ❌ tech ops | ❌ | ✅ any docs | ✅ |
| Price floor visible to buyers | n/a | **$350–$10k/yr** | "90% cheaper than consulting" | undisclosed | enterprise | per-user | usage/enterprise | per-seat SaaS | $30–50k/exercise |

---

## 3. Positioning implications

**Where Certus is genuinely alone (defensible whitespace):**

1. **The verified individual readiness record.** No competitor combines
   free-recall grading + answer-key isolation + retention-over-time + staleness-honest
   heatmap + tamper-evident per-person evidence. Everyone else certifies that
   *an exercise happened*; Certus certifies that *a named person can execute a
   named procedure, as of a date*. This is a different product category, and it
   compounds: 12 months of readiness history cannot be migrated to a competitor.
2. **Grading you can defend to an auditor.** Every competitor's scoring is an
   unfalsifiable "AI scores you." None publishes accuracy, none has an
   override/appeal path, none has an anti-gaming architecture. The first vendor
   with a *calibration claim* ("grader agrees with expert instructors N% on a
   corpus of M real answers, disagreements auditable") converts LLM grading
   from a demo feature into audit-grade instrumentation.
3. **Non-cyber procedural verticals.** Every direct AI-tabletop competitor is
   cyber-only. Sklls proves verticalized protocol-simulation sells in public
   safety. OSHA PSM / Joint Commission EM territory (Certus's profiles +
   mandate stamping) has the consulting spend but no AI-native entrant found.
4. **Singapore mandate mapping** (added July 2026): none of the surveyed
   vendors maps Singapore instruments (SCDF's statutory 2-TTE-per-year
   cadence for FSM premises, MAS BCM attestation evidence, CSA CCoP
   exercising, MOM MHI safety cases) — shipped as Certus's `mandates/sg.json`
   pack with cadence tracking and the `sg_highrise` demo profile.

**Where Certus is behind or merely tied:**

- **"Docs-in, drills-out" is not a moat** — ThreatGEN ships IR-plan upload
  today, and SOP-microlearning tools claim the same sentence for quizzes.
  Stop leading with it; lead with what the docs *produce* (verified readiness).
- **Enterprise trust artifacts**: Immersive has the logos and the board-level
  "Resilience Score" narrative; Sklls has ISO 27001/GDPR badges. Certus has an
  architecture but zero third-party attestations or customer proof.
- **Team/tabletop facilitation UX**: ThreatGEN and ChaosTrack are richer as
  *group* exercise products; Certus's tabletop mode is v1.
- **Price anchoring**: ThreatGEN's public $350 entry point means "AI tabletop"
  is being commoditized on day one. Competing on exercise delivery is a race
  to that floor; the readiness record is what escapes it.

**What this does to the original comment:** "easy to build" is true of the
exercise generator — the market proves it by shipping several. It is *not*
true of a calibrated grader, a longitudinal readiness dataset, or auditor
trust, none of which any competitor has, all of which take time-in-market and
customer data — which is what a moat is. "No one is building it → no demand"
is factually wrong (five vendors are building adjacent things) and
economically wrong (the demand currently clears at $30–50k per exercise in
consulting fees).

---

## 4. Recommendation: which moat asset to build first

**Build the grader-calibration flywheel first.** Rationale traced to the matrix:

- The two whitespace rows Certus owns — *verified individual readiness* and
  *defensible grading* — both rest on one question a compliance buyer will ask
  in the first meeting: **"how do I know the grader is right?"** Today the
  answer is a 12-case spike (`spike_grader.py`). Every competitor's answer is
  worse (no claim at all), so this is winnable ground nobody is contesting.
- It is the only moat asset that *compounds from day one with design
  partners*: every graded answer + every instructor override (already
  captured, iteration 4) is labeled ground truth. Features are copyable in a
  quarter; a corpus of expert-verified gradings across real customer SOPs is
  not. This converts the shipped override path from a UX feature into a data
  asset.
- It de-risks the sales motion the roadmap already commits to (Phase 1 design
  partners, risk/compliance buyer): evidence exports carry audit weight only
  if the grading behind them is defensible (BUSINESS_IDEA.md "Trust
  prerequisite" names this as the open half).

Concretely: (1) instrument every grading event + override into an exportable
labeled calibration dataset per profile; (2) grow `spike_grader.py` into a
benchmark harness that replays that dataset against the grader and reports
agreement/precision/recall per SOP domain; (3) surface a "grading integrity"
stat in the evidence export (corpus size, agreement rate, override rate) so
the calibration claim ships inside the artifact auditors see.

**Second: readiness trend history** (switching cost — extends the shipped
heatmap with time as an axis; Immersive's Resilience Score shows the
executive appetite, staleness-honest trends outflank it).
**Third: one regulatory content pack** (OSHA PSM clause mapping — the
non-cyber wedge no AI-native competitor occupies).
**Deprioritize**: richer tabletop facilitation UX (contests ThreatGEN/
ChaosTrack head-on at a $350 price floor) and generic "docs-in" marketing.
