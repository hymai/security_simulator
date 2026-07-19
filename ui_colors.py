"""
Deterministic color per incident-type label, so the same label always renders
the same color across a session and across profiles, without any manual
per-profile color config — needed because incident types are defined by
whatever corpus a profile ships (see corpus_config.py), not a fixed enum.

Colors are picked by hashing the label into a fixed, contrast-checked palette,
so a brand-new profile's incident types get sensible colors immediately.
"""

import hashlib
import html

# (background, text) — chosen for readable contrast in both light and dark
# Streamlit themes; kept to a small fixed set so labels stay visually distinct.
_PALETTE = [
    ("#e63946", "#ffffff"),
    ("#457b9d", "#ffffff"),
    ("#2a9d8f", "#ffffff"),
    ("#f4a261", "#000000"),
    ("#8338ec", "#ffffff"),
    ("#e9c46a", "#000000"),
    ("#06d6a0", "#000000"),
    ("#ef476f", "#ffffff"),
]


def color_for(label: str) -> tuple[str, str]:
    """Return (background_hex, text_hex), stable for a given label."""
    idx = int(hashlib.md5(label.encode()).hexdigest(), 16) % len(_PALETTE)
    return _PALETTE[idx]


def badge(label: str) -> str:
    """An inline-styled HTML badge for `label`. Caller must render with
    unsafe_allow_html=True.

    `label` is HTML-escaped before interpolation: although this is meant for
    "our own labels" rather than raw trainee input, incident-type labels are
    themselves model output (build_index.infer_config(), fed the admin's
    uploaded threat-catalog text) with no length cap or sanitization applied
    — a crafted corpus document could make the model echo markup into this
    field, which would otherwise reach every trainee's DOM unescaped."""
    bg, fg = color_for(label)
    safe_label = html.escape(label)
    return (
        f'<span style="background:{bg};color:{fg};padding:1px 8px;'
        f'border-radius:999px;font-size:0.85em;font-weight:600;'
        f'white-space:nowrap;">{safe_label}</span>'
    )
