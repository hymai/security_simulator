"""
Measure BGE-M3's similarity distribution on this corpus so the retrieval cutoff
is chosen from data, not inherited.

The old app carried `similarity_cutoff=0.8` from text-embedding-ada-002, whose
cosine scores compress into a narrow high band. BGE-M3's distribution is
different, so 0.8 is meaningless here. This script embeds a set of labelled
queries against each index and prints:

  - best in-domain hit per query (should be high)
  - strongest in-domain confuser (a chunk from the wrong file)
  - best score for out-of-domain queries that should match NOTHING

A cutoff only earns its place if it sits above the out-of-domain scores and
below the weakest true hit. If those two bands overlap, prefer bare top-k
(cutoff=None): a threshold that can't separate signal from noise only
reintroduces the old app's "I don't know" failure.

Usage:  python3 calibrate_cutoff.py
"""

import retrieval

# (query, expected source filename) — the file whose chunk SHOULD rank first.
LABELLED = {
    "sops": [
        ("perimeter intrusion alarm on the zone 3 fence line", "perimeter-intrusion-response.md"),
        ("security officer confirms a fence breach and reports intruders", "perimeter-intrusion-response.md"),
        ("activate the building fire alarm and evacuate to the assembly point", "fire-response.md"),
        ("account for all personnel using the attendance roster", "fire-response.md"),
        ("isolate a compromised engineering workstation from the OT network", "cyber-intrusion-response.md"),
        ("validate PLC configuration against a known-good baseline", "cyber-intrusion-response.md"),
    ],
    "threats": [
        ("intruder cutting or climbing the zone 3 perimeter fence", "threat-catalog.md"),
        ("CCTV and perimeter intrusion detection on the fence line", "site-security-measures.md"),
    ],
}

# Queries that should match nothing in either corpus — the real test of a cutoff.
OUT_OF_DOMAIN = [
    "recipe for sourdough bread",
    "quarterly sales forecast spreadsheet",
    "how to change a car tire",
]


def calibrate(name: str) -> None:
    index = retrieval.load_index(name)
    pairs = LABELLED[name]

    print(f"\n=== corpus: {name}  ({len(index['chunks'])} chunks) ===")

    weakest_true_hit = 1.0
    strongest_confuser = 0.0

    print("in-domain queries:")
    for query, expected in pairs:
        ranked = retrieval.search(index, query, k=len(index["chunks"]), cutoff=None)
        best_pos = max(r["score"] for r in ranked if r["source"] == expected)
        best_neg = max((r["score"] for r in ranked if r["source"] != expected), default=0.0)
        top = ranked[0]
        hit = "ok " if top["source"] == expected else "MISS"
        print(f"  [{hit}] pos={best_pos:.3f}  confuser={best_neg:.3f}  "
              f"top={top['source']}  << {query[:45]}")
        weakest_true_hit = min(weakest_true_hit, best_pos)
        strongest_confuser = max(strongest_confuser, best_neg)

    strongest_ood = 0.0
    print("out-of-domain queries (should score low):")
    for query in OUT_OF_DOMAIN:
        top = retrieval.search(index, query, k=1, cutoff=None)[0]
        print(f"        best={top['score']:.3f}  {top['source']}  << {query}")
        strongest_ood = max(strongest_ood, top["score"])

    print(f"  weakest true hit   : {weakest_true_hit:.3f}")
    print(f"  strongest confuser : {strongest_confuser:.3f}  (same-corpus, wrong file)")
    print(f"  strongest OOD hit  : {strongest_ood:.3f}  (should be rejected)")

    gap = weakest_true_hit - strongest_ood
    if gap > 0.03:
        suggested = round(strongest_ood + gap / 2, 2)
        print(f"  => separation {gap:.3f}. Suggested cutoff ~= {suggested} "
              f"(midpoint between OOD and weakest true hit).")
    else:
        print(f"  => separation {gap:.3f} is too small to threshold reliably. "
              f"Use bare top-k (cutoff=None).")


if __name__ == "__main__":
    for corpus in ("sops", "threats"):
        calibrate(corpus)
