"""
Per-profile config: which incident types a site offers, and the retrieval
vocabulary each one maps to (used to query the `threats` index in
pipeline.generate_scenario).

Each profile is a directory under profiles/<name>/ with:
  config.json           {"display_name": str, "incident_types": {label: query}}
  data/threats/*.md
  data/sops/*.md

This replaces a hardcoded incident-type map with one config file per corpus,
so a new organization's SOPs/threat catalog can be dropped in without touching
pipeline.py. See build_index.py to scaffold a new profile.
"""

import json
import os

import retrieval

PROFILES_DIR = retrieval.PROFILES_DIR


def load_config(profile: str) -> dict:
    """Load profiles/<profile>/config.json.

    Returns {"display_name": str, "incident_types": {label: retrieval_query}}.
    """
    path = os.path.join(PROFILES_DIR, profile, "config.json")
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    config.setdefault("display_name", profile)
    config.setdefault("incident_types", {})
    return config


def list_profiles() -> list[str]:
    return retrieval.list_profiles()
