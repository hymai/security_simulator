"""
Admin-only corpus upload, gated behind a shared password.

Why gated at all: the SOP corpus is the answer key. If any trainee could
upload or overwrite it from the same sidebar they train in, they could plant
their own "SOP" (or read the real one via the upload form's file browser) and
defeat the whole answer-key-isolation guarantee the app is built around. This
is a shared-secret gate proportionate to a single-instructor local tool, not a
full user/role system — see SIMULATOR_ADMIN_PASSWORD below.

Set SIMULATOR_ADMIN_PASSWORD in the environment before launching Streamlit to
enable this panel:

    SIMULATOR_ADMIN_PASSWORD=letmein streamlit run security_simulator.py

Without it, the admin section stays locked with no way in — there is no
default password.
"""

import hmac
import os
import re

import streamlit as st

import build_index
import corpus_config
import instructor_dashboard
import retrieval

_PASSWORD_ENV = "SIMULATOR_ADMIN_PASSWORD"
_PROFILE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _sanitize_profile_name(name: str) -> str | None:
    slug = name.strip().lower().replace(" ", "-")
    return slug if _PROFILE_SLUG_RE.match(slug) else None


def _save_uploads(profile: str, corpus: str, files) -> list[str]:
    """Write uploaded .md files into profiles/<profile>/data/<corpus>/. Returns
    the basenames actually written (non-.md uploads are skipped)."""
    target_dir = os.path.join(retrieval.profile_data_dir(profile), corpus)
    os.makedirs(target_dir, exist_ok=True)
    written = []
    for f in files:
        name = os.path.basename(f.name)  # defend against path traversal in the filename
        if not name.lower().endswith(".md"):
            continue
        with open(os.path.join(target_dir, name), "wb") as out:
            out.write(f.getbuffer())
        written.append(name)
    return written


def _render_corpus_management() -> None:
    profiles = corpus_config.list_profiles()
    existing_data_profiles = sorted(
        p for p in os.listdir(retrieval.PROFILES_DIR)
        if os.path.isdir(os.path.join(retrieval.PROFILES_DIR, p))
    ) if os.path.isdir(retrieval.PROFILES_DIR) else []

    mode = st.radio("Profile", ["Add to existing", "Create new"], horizontal=True)
    if mode == "Add to existing":
        if not existing_data_profiles:
            st.info("No profiles yet — switch to 'Create new'.")
            return
        profile = st.selectbox("Which profile", existing_data_profiles)
    else:
        raw_name = st.text_input("New profile name (e.g. 'acme-labs')")
        profile = _sanitize_profile_name(raw_name) if raw_name else None
        if raw_name and not profile:
            st.warning("Use lowercase letters, numbers, '-' or '_' only.")

    if not profile:
        return

    threats_files = st.file_uploader(
        "Threat catalog documents (.md)", type=["md"], accept_multiple_files=True,
        key=f"threats_{profile}")
    sops_files = st.file_uploader(
        "SOP documents (.md)", type=["md"], accept_multiple_files=True,
        key=f"sops_{profile}")

    if st.button("Save & build index", disabled=not (threats_files or sops_files)):
        for corpus, files in (("threats", threats_files), ("sops", sops_files)):
            if not files:
                continue
            written = _save_uploads(profile, corpus, files)
            if written:
                st.write(f"Saved {corpus}: {', '.join(written)}")
                retrieval.build_index(profile, corpus)
                st.write(f"Rebuilt '{corpus}' index for '{profile}'.")
        st.success(f"Profile '{profile}' updated.")
        st.rerun()

    config_path = os.path.join(retrieval.PROFILES_DIR, profile, "config.json")
    has_threats_index = os.path.exists(
        os.path.join(retrieval.profile_index_dir(profile), "threats.npz"))
    if has_threats_index:
        label = "Refresh incident types from threats corpus" if os.path.exists(config_path) \
            else "Infer incident types from threats corpus"
        if st.button(label):
            with st.spinner("Asking the model to propose incident types..."):
                config = build_index.infer_config(profile)
            import json
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            st.success(f"Wrote {config_path}")
            st.json(config)

    if profiles:
        st.caption("Existing indexed profiles: " + ", ".join(profiles))


def render() -> None:
    """Draw the admin section at the bottom of the sidebar.

    expanded is tied to admin_unlocked so the panel doesn't snap shut on every
    rerun triggered by something else on the page (e.g. generating a scenario) —
    without this, st.expander defaults to collapsed on every rerun regardless
    of auth state, which reads as "losing access" even though nothing was
    actually lost.
    """
    unlocked = st.session_state.get("admin_unlocked", False)
    with st.sidebar.expander("🔒 Admin", expanded=unlocked):
        required = os.environ.get(_PASSWORD_ENV)
        if not required:
            st.caption(
                f"Set the `{_PASSWORD_ENV}` environment variable before "
                "launching Streamlit to enable corpus upload and the "
                "instructor dashboard.")
            return

        if not st.session_state.get("admin_unlocked"):
            entered = st.text_input("Admin password", type="password", key="admin_pw")
            if entered:
                if hmac.compare_digest(entered, required):
                    st.session_state.admin_unlocked = True
                    st.rerun()
                else:
                    st.error("Incorrect password.")
            return

        st.success("Admin unlocked.")
        tab_corpora, tab_dashboard = st.tabs(["Manage corpora", "Instructor dashboard"])
        with tab_corpora:
            _render_corpus_management()
        with tab_dashboard:
            instructor_dashboard.render()
