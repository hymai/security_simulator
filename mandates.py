"""
Regulatory mandate registry — the content layer that turns the free-text
`assessment.mandate` config field into something an auditor can cite.

Each jurisdiction ships as one JSON file under mandates/ (mandates/sg.json is
the first: Singapore — SCDF CERT/table-top, MOM WSH-MHI and general WSH, MAS
BCM, CSA CCoP, MHA IPA). A profile opts in by setting its `assessment.mandate`
to a registry id (e.g. "sg-scdf-cert-tte"); any value that is NOT a registry
id keeps today's behavior exactly — a free-text string stamped into evidence.

What a registry id buys:
- evidence exports and the readiness report expand it into the full citation
  block: regulator, instrument, clauses, requirement, and the evidence-scope
  statement (assessment.py);
- if the mandate carries a drill cadence (SCDF: 2 table-top exercises + 2
  evacuation drills per year), the dashboard shows cadence progress from
  recorded sessions (cadence_status below).

Two honesty rules, enforced in the data:
- `cadence_basis` distinguishes a statutory cadence (SCDF) from a suggested
  one (MAS/CSA, where the instrument says "regular" and the practical floor
  is annual) — the UI and evidence must never present a suggestion as law;
- `evidence_scope` states what a Certus record does NOT cover (a physical
  evacuation drill still has to happen; safety cases and plan filings are
  separate obligations).

Division of labor as elsewhere: this module owns lookup and cadence math;
storage.py stays CRUD; assessment.py renders exports; the dashboard renders.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import storage

_HERE = os.path.dirname(os.path.abspath(__file__))
MANDATES_DIR = os.path.join(_HERE, "mandates")

_cache: dict[str, dict] | None = None


def _registry() -> dict[str, dict]:
    global _cache
    if _cache is None:
        _cache = {}
        for name in sorted(os.listdir(MANDATES_DIR)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(MANDATES_DIR, name), encoding="utf-8") as f:
                for m in json.load(f).get("mandates", []):
                    _cache[m["id"]] = m
    return _cache


def all_mandates() -> list[dict]:
    return list(_registry().values())


def get(mandate_id: str | None) -> dict | None:
    """The registry entry for a profile's configured mandate value, or None —
    None simply means the value is free text and keeps legacy behavior."""
    if not mandate_id:
        return None
    return _registry().get(mandate_id.strip())


def cadence_status(profile: str, mandate: dict,
                   window_days: int = 365) -> dict | None:
    """Progress against a mandate's drill cadence over the trailing window,
    from recorded sessions. None when the mandate carries no cadence.

    Counted from what Certus actually records:
    - table-top exercises: completed sessions recorded under a team name
      (certus.py stores tabletop drills with a "Team: " trainee prefix);
    - assessments: finished scored assessments (any trainee).
    Physical evacuation drills happen outside Certus, so a drills-per-year
    requirement is surfaced as a reminder row with no recorded count rather
    than silently counted as zero-and-failing.
    """
    cadence = mandate.get("cadence")
    if not cadence:
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    sessions = [s for s in storage.list_sessions(profile)
                if s["started_at"] >= cutoff]
    tabletops = sum(1 for s in sessions
                    if s["completed_at"] and s["trainee"].startswith("Team: "))
    assessments = sum(1 for s in sessions
                      if s.get("mode") == "assessment"
                      and s.get("score") is not None)

    requirements = []
    if cadence.get("tabletops_per_year"):
        required = cadence["tabletops_per_year"]
        requirements.append({
            "label": "table-top exercises",
            "required": required,
            "recorded": tabletops,
            "shortfall": max(0, required - tabletops),
        })
    if cadence.get("drills_per_year"):
        requirements.append({
            "label": "evacuation drills (physical — conducted and recorded "
                     "outside Certus)",
            "required": cadence["drills_per_year"],
            "recorded": None,
            "shortfall": None,
        })
    return {
        "window_days": window_days,
        "tabletops": tabletops,
        "assessments": assessments,
        "basis": mandate.get("cadence_basis"),
        "requirements": requirements,
    }


def citation_markdown(mandate: dict) -> list[str]:
    """The citation block shared by the evidence export and the readiness
    report: regulator, instrument, clauses, requirement, and the
    evidence-scope honesty statement."""
    lines = [
        f"**{mandate['regulator']}** — {mandate['instrument']}",
        "",
        mandate["requirement"],
        "",
    ]
    lines += [f"- {c}" for c in mandate["clauses"]]
    if mandate.get("cadence") and mandate.get("cadence_basis"):
        lines += ["", f"_Cadence basis: {mandate['cadence_basis']}._"]
    lines += ["", f"_Scope of this evidence: {mandate['evidence_scope']}_"]
    return lines
