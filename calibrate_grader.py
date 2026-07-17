"""
Replay expert-labeled grading events through the live grader and report
agreement — the successor to spike_grader.py's hand-written cases, fed by
real trainee answers labeled in the dashboard's Calibration tab.

Two comparisons per example, both against the expert's verified action ids:
- recorded  — what the grader said at the time the event was recorded (free,
  no model call): the accuracy figure the evidence exports cite.
- replayed  — what the CURRENT grader says for the same verbatim input: run
  after changing the model, prompt, or endpoint to measure the change against
  real answers instead of the 12 synthetic spike cases.

Reads labeled examples from the local sessions DB by default, or from a
JSONL exported by the dashboard (so a dataset collected on an instructor's
machine can calibrate a grader anywhere).

Grading is replayed exactly as pipeline.grade_step builds it (same SYSTEM,
schema, temperature 0, and the per-event context text incl. any revealed
inject), minus the reply-leak suppression — irrelevant here, since only the
covered ids are compared. Non-English profiles: replies may come back in
English (the language prompt amendment isn't applied), but coverage ids are
what's measured.

Usage:  ollama serve                          (unless CERTUS_OPENAI_BASE_URL)
        python3 calibrate_grader.py [profile] [--jsonl FILE] [--recorded-only]
"""

import argparse
import json
import sys

import grading
from ollama_client import ollama_chat


def load_examples(args) -> list[dict]:
    if args.jsonl:
        with open(args.jsonl, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        return [r for r in rows if r.get("verified_ids") is not None]
    import calibration
    return calibration.labeled(args.profile)


def replay(example: dict) -> set:
    """One grading call for one example, returning the covered ids the
    current grader awards (hallucinated ids discarded, as grade_step does)."""
    actions = {aid: tuple(ra) for aid, ra in example["actions"].items()}
    user = grading.build_user_prompt(
        example["step_number"], example["title"], actions,
        example["context"], example["prior_answer"], example["message"])
    result = ollama_chat(grading.SYSTEM, user, grading.SCHEMA,
                         temperature=0, num_ctx=8192)
    return set(result.get("covered_action_ids", [])) & set(actions)


def report(name: str, outcomes: list[tuple[set, set]]) -> None:
    """outcomes: (got_ids, verified_ids) per example."""
    tp = sum(len(g & v) for g, v in outcomes)
    fp = sum(len(g - v) for g, v in outcomes)
    fn = sum(len(v - g) for g, v in outcomes)
    agree = sum(g == v for g, v in outcomes)
    n = len(outcomes)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    print(f"\n{name}: exact agreement {agree}/{n} ({agree / n:.0%}) — "
          f"action-level precision {precision:.0%}, recall {recall:.0%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("profile", nargs="?", default=None,
                        help="profile to read labels from the DB for "
                             "(default: all profiles)")
    parser.add_argument("--jsonl", help="read examples from an exported "
                                        "calibration .jsonl instead of the DB")
    parser.add_argument("--recorded-only", action="store_true",
                        help="skip the model replay; only score the recorded "
                             "verdicts against the expert labels (no Ollama "
                             "needed)")
    args = parser.parse_args()

    rows = load_examples(args)
    if not rows:
        sys.exit("No labeled examples found. Review some gradings in the "
                 "dashboard's Calibration tab first (or pass --jsonl).")
    print(f"{len(rows)} labeled example(s)")

    recorded, replayed = [], []
    for e in rows:
        verified = set(e["verified_ids"])
        recorded.append((set(e["model_ids"]), verified))
        if args.recorded_only:
            continue
        got = replay(e)
        flag = "AGREE " if got == verified else "DIFFER"
        print(f"{flag}  #{e['event_id']} step {e['step_number']} "
              f"attempt {e['attempt']} — replayed {sorted(got) or '(none)'} "
              f"vs expert {sorted(verified) or '(none)'}")
        replayed.append((got, verified))

    report("recorded grader vs expert", recorded)
    if replayed:
        report("current grader vs expert (replay)", replayed)


if __name__ == "__main__":
    main()
