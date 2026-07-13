---
name: verify
description: How to build, launch, and drive Certus (Streamlit + Ollama) to verify changes end-to-end.
---

# Verifying Certus

## Launch

Ollama must be running with both models pulled (`qwen2.5:14b`, `mistral-nemo:12b`):

```bash
curl -s http://localhost:11434/api/version   # check first
```

The preview harness (preview_start with launch.json) CANNOT execute this
project's venv — macOS sandbox denies reading `.venv/pyvenv.cfg`. Launch with
a background Bash command instead and open the URL in the Browser pane:

```bash
CERTUS_RECORD_SESSIONS=1 CERTUS_ADMIN_PASSWORD=dev-only \
  .venv/bin/streamlit run certus.py --server.headless=true \
  --server.port=8760 --server.fileWatcherType=none
```

Recording/admin env vars are REQUIRED to see the trainee-identity fields,
Session-mode radio (assessment mode), and the admin panel — without them
those features are invisible by design.

## Driving the UI (gotchas)

- BGE-M3 warm-up takes ~30–60 s on first page load per process.
- Generation times on this hardware: scenario ~40–60 s, answer key ~60–120 s,
  each grading turn ~15–30 s. Poll with `wait` + `get_page_text`.
- Streamlit widgets IGNORE synthetic JS value-setters for text inputs (DOM
  shows the value; server never sees it). Type with real keyboard actions,
  then commit with Tab (blur). The chat input DOES accept the setter +
  `input`-event + submit-button-click pattern.
- The multiselect dropdown opens only on a real click on its combobox ref;
  options are then clickable via `li[role="option"]` in JS.
- refs go stale after every Streamlit rerun — re-read_page before clicking.
- A page reload or browser-pane restart severs the Streamlit websocket and
  loses the in-progress session (fresh session state; DB row stays
  "in progress").

## Headless fallback (no Ollama / no browser)

Run `.venv/bin/python tests/headless_checks.py` — it drives the real
certus.py state machine via `streamlit.testing.v1.AppTest` with the model
layer faked (monkeypatched `retrieval.get_model/load_index` + `pipeline.*`,
temp `storage.LOCAL_DIR/DB_PATH`). Extend that file for new paths. Gotchas
baked into it: `AppTest.from_function` strips module globals — wrap dashboard
renders in a function that imports inside its body; `at.session_state` has
no `.get`, use `in`.

## Data layer

Recorded sessions live in `.local/sessions.db` (SQLite). Inspect with
`storage.list_sessions()` / `session_detail()`; assessment scoring/evidence
are pure functions in `assessment.py` and can be recomputed from any
recorded session for cross-checking what the UI showed.
