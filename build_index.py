"""
Onboard a new corpus/profile: build its retrieval indices and, if it doesn't
have one yet, propose an incident-type config from the threat catalog.

Usage:
    # 1. Create profiles/<name>/data/threats/*.md and data/sops/*.md yourself
    #    (copy in the organization's own threat catalog + SOP documents).
    # 2. Build the indices (and infer incident types if config.json is absent):
    python3 build_index.py <profile> [--infer-types] [--force]

--force rebuilds indices even if they already exist.
--infer-types (re)writes config.json by asking the model to propose incident
types + retrieval vocabulary from the threats corpus — skip it and hand-write
config.json (see profiles/default/config.json for the shape) if you'd rather
control the incident types yourself.
"""

import argparse
import json
import logging
import os
import sys

import retrieval

log = logging.getLogger("certus.build_index")

_INFER_SCHEMA = {
    "type": "object",
    "properties": {
        "display_name": {"type": "string"},
        "incident_types": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["label", "query"],
            },
        },
    },
    "required": ["display_name", "incident_types"],
}

_INFER_SYSTEM = """You design training-scenario configuration for Certus, an
operational readiness platform, given an organization's threat catalog.

Propose incident-type categories a trainee could select (e.g. "Physical
Security", "Cyber Security", "Workplace Violence" — whatever fits this
specific catalog, not a fixed list).

Rules:
- Base every category and query ONLY on threats actually described in the
  catalog text below. Do not invent scenarios, systems, or hazards the catalog
  never mentions, even if they'd be typical for this kind of organization.
- Propose only as many categories as the catalog actually supports — one is
  fine if it only describes one kind of threat. Do not pad the list.
- Each query is a short retrieval string: a comma-separated list of the
  concrete threats/scenarios *named in the catalog* for that category (a
  handful of phrases, not an exhaustive enumeration). It is used to search the
  threat corpus, not shown to trainees.
- Output only the JSON fields requested — no reasoning, commentary, or draft
  text of any kind in any field.

Also propose a short display_name for this site/organization profile.

Return only JSON."""

_MAX_QUERY_CHARS = 400


def infer_config(profile: str) -> dict:
    """Ask the model to propose incident types from the threats corpus."""
    from ollama_client import ollama_chat

    index = retrieval.load_index(profile, "threats")
    catalog_text = "\n\n".join(c["text"] for c in index["chunks"])
    result = ollama_chat(_INFER_SYSTEM, catalog_text, _INFER_SCHEMA,
                         temperature=0, num_ctx=8192)
    incident_types = {}
    for t in result.get("incident_types", []):
        label, query = t.get("label"), t.get("query", "")
        if not label:
            continue
        if len(query) > _MAX_QUERY_CHARS:
            log.warning("infer_config: query for %r ran to %d chars (probably "
                       "ungrounded/hallucinated) — truncating to %d",
                       label, len(query), _MAX_QUERY_CHARS)
            query = query[:_MAX_QUERY_CHARS]
        incident_types[label] = query
    config = {
        "display_name": result.get("display_name", profile),
        "incident_types": incident_types,
    }
    if not config["incident_types"]:
        raise RuntimeError("Model returned no incident types — write config.json by hand.")
    return config


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile", help="profile name (subdir of profiles/)")
    ap.add_argument("--infer-types", action="store_true",
                    help="propose config.json from the threats corpus via the model")
    ap.add_argument("--force", action="store_true",
                    help="rebuild indices even if already present")
    args = ap.parse_args()

    data_dir = retrieval.profile_data_dir(args.profile)
    if not os.path.isdir(data_dir):
        sys.exit(f"No such profile data dir: {data_dir}\n"
                 f"Create {data_dir}/threats/*.md and {data_dir}/sops/*.md first.")

    for name in ("threats", "sops"):
        index_dir = retrieval.profile_index_dir(args.profile)
        npz_path = os.path.join(index_dir, f"{name}.npz")
        if args.force or not os.path.exists(npz_path):
            print(f"Building '{name}' index for profile '{args.profile}'...")
            retrieval.build_index(args.profile, name)
        else:
            print(f"'{name}' index already exists for '{args.profile}' (use --force to rebuild).")

    config_path = os.path.join(retrieval.PROFILES_DIR, args.profile, "config.json")
    if args.infer_types or not os.path.exists(config_path):
        if not args.infer_types:
            print(f"No config.json for '{args.profile}' — inferring incident types "
                 f"from the threats corpus (pass --infer-types explicitly to skip this notice).")
        print("Asking the model to propose incident types from the threats corpus...")
        config = infer_config(args.profile)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print(f"Wrote {config_path}:")
        print(json.dumps(config, ensure_ascii=False, indent=2))
    else:
        print(f"config.json already exists at {config_path} (pass --infer-types to regenerate it).")

    print(f"\nProfile '{args.profile}' ready.")


if __name__ == "__main__":
    main()
