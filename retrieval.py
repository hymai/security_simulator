"""
Retrieval over the two markdown corpora, using BGE-M3 embeddings.

Design notes:
- BGE-M3 needs NO query-instruction prefix. The "Represent this sentence for
  searching relevant passages:" convention belongs to the bge-*-en-v1.5 family;
  applying it to BGE-M3 degrades retrieval. Queries and passages are embedded the
  same way.
- Embeddings are L2-normalized, so cosine similarity is a plain dot product.
- Only the query text is embedded — never the instructions. Concatenating
  instructions onto the query was the original app's biggest retrieval bug (it
  searched for the nearest neighbor of "Let's think step by step").
- Chunking is heading-aware: one chunk per `##` section, prefixed with the
  document `#` title so each chunk carries its own context.
- Storage is a .npz of vectors plus a JSON sidecar of chunk records. No vector
  DB is warranted at this corpus size.

The two indices are kept separate on purpose: `threats` (threat catalog + site
security measures) feeds scenario generation; `sops` (response procedures) feeds
answer-key generation. The scenario stage never queries `sops`, so it cannot
leak the response plan.
"""

import glob
import json
import os

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-m3"
_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "data")
INDEX_DIR = os.path.join(_HERE, ".index")

_model = None


def get_model() -> SentenceTransformer:
    """Load BGE-M3 once per process. ~2GB on first download."""
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _embed(texts: list[str]) -> np.ndarray:
    return get_model().encode(
        texts, normalize_embeddings=True, convert_to_numpy=True
    ).astype(np.float32)


def chunk_markdown(text: str, source: str) -> list[dict]:
    """Split a markdown document into one chunk per `##` section.

    Everything before the first `##` (the `#` title + any intro) becomes its own
    chunk. Each section chunk is prefixed with the document title so it reads
    standalone. Returns records: {id, source, heading, text}.
    """
    lines = text.splitlines()
    doc_title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), source)

    chunks: list[dict] = []
    heading = None          # None => the intro section (title + preamble)
    body: list[str] = []

    def flush():
        content = "\n".join(body).strip()
        if not content and heading is None:
            return
        if heading is None:
            block_text = content                     # intro already contains "# title"
            label = doc_title
        else:
            block_text = f"# {doc_title}\n\n## {heading}\n{content}"
            label = heading
        slug = (label.lower().replace(" ", "-").replace("—", "-"))
        chunks.append({
            "id": f"{source}#{slug}",
            "source": source,
            "heading": label,
            "text": block_text,
        })

    for ln in lines:
        if ln.startswith("## "):
            flush()
            heading = ln[3:].strip()
            body = []
        else:
            body.append(ln)
    flush()
    return chunks


def build_index(name: str) -> dict:
    """Build (and persist) the index for corpus `name` (a subdir of data/)."""
    corpus_dir = os.path.join(DATA_DIR, name)
    paths = sorted(glob.glob(os.path.join(corpus_dir, "*.md")))
    if not paths:
        raise FileNotFoundError(f"No .md files in {corpus_dir}")

    chunks: list[dict] = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            chunks.extend(chunk_markdown(f.read(), os.path.basename(path)))

    vectors = _embed([c["text"] for c in chunks])

    os.makedirs(INDEX_DIR, exist_ok=True)
    np.savez(os.path.join(INDEX_DIR, f"{name}.npz"), vectors=vectors)
    with open(os.path.join(INDEX_DIR, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    return {"name": name, "vectors": vectors, "chunks": chunks}


def load_index(name: str) -> dict:
    """Load a persisted index, building it if missing."""
    npz_path = os.path.join(INDEX_DIR, f"{name}.npz")
    json_path = os.path.join(INDEX_DIR, f"{name}.json")
    if not (os.path.exists(npz_path) and os.path.exists(json_path)):
        return build_index(name)
    vectors = np.load(npz_path)["vectors"]
    with open(json_path, encoding="utf-8") as f:
        chunks = json.load(f)
    return {"name": name, "vectors": vectors, "chunks": chunks}


def search(index: dict, query: str, k: int = 5, cutoff: float | None = None) -> list[dict]:
    """Return up to `k` chunks most similar to `query`, ranked by cosine score.

    If `cutoff` is set, chunks scoring below it are dropped. Pass cutoff=None to
    use bare top-k (see calibrate_cutoff.py for why the value must be measured
    against BGE-M3 rather than carried over from the old ada-002 threshold).
    Each result is a chunk record plus a "score" float.
    """
    q = _embed([query])[0]
    scores = index["vectors"] @ q
    order = np.argsort(-scores)
    results = []
    for i in order:
        s = float(scores[i])
        if cutoff is not None and s < cutoff:
            break
        results.append({**index["chunks"][i], "score": s})
        if len(results) >= k:
            break
    return results
