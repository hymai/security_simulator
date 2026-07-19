"""
One helper for every model call: ollama_chat(). Same urllib + `format` shape as
the grading spike, generalized so all three stages share it.

Two things this adds over the spike's inline version:

- `num_ctx` is set explicitly on every call. Ollama applies its own default
  context window (typically 4096) regardless of qwen2.5:14b's 32768 capability,
  and silently truncates any prompt past it. The spike never hit this because
  its prompts are tiny; answer-key generation feeds retrieved SOP chunks and
  will. We set num_ctx and log the assembled prompt size so truncation surfaces
  as a warning instead of silent quality loss.

- `temperature` is a parameter (0 for grading and answer-key generation, higher
  for scenario generation) rather than hardcoded.

- Calls always stream internally (confirmed compatible with forced `format`
  output — Ollama streams the JSON string token by token) and accept an
  optional `on_token(count, elapsed_s)` callback. Profiling this model on this
  machine showed generation running at ~6 tokens/sec, 80-85% of total wall
  time in both stages, with model load and prompt processing both fast and
  negligible by comparison — that's a hardware/model-size ceiling, not
  something request shape can fix. Streaming doesn't reduce that time, but it
  lets the UI show live progress instead of an unexplained multi-second-to-
  minute pause, and callers that don't pass `on_token` see no behavior change.

- `keep_alive` defaults to "30m" so the model stays resident in memory between
  the scenario and answer-key calls in a real session (Ollama's own default is
  5 minutes, which a slow or distracted trainee could exceed between clicks,
  paying an unnecessary reload).
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

MODEL = "qwen2.5:14b"
SCENARIO_MODEL = "mistral-nemo:12b"
# Overridable for Docker/remote Ollama (compose sets it to http://ollama:11434/...)
OLLAMA_URL = os.environ.get("CERTUS_OLLAMA_URL", "http://localhost:11434/api/chat")
DEFAULT_KEEP_ALIVE = "30m"

# --- iteration 6: any OpenAI-compatible endpoint -----------------------------
# Setting CERTUS_OPENAI_BASE_URL (e.g. http://localhost:8000/v1 for vLLM,
# https://api.openai.com/v1, LM Studio, llama.cpp server, ...) switches every
# model call from Ollama's native API to /chat/completions on that endpoint.
# The rest of the app is unchanged: ollama_chat() keeps its signature, and the
# Ollama model-name constants above are mapped to the models below.
#   CERTUS_OPENAI_MODEL           required with the base URL; used for grading
#                                 and answer-key generation
#   CERTUS_OPENAI_SCENARIO_MODEL  optional; defaults to CERTUS_OPENAI_MODEL
#   CERTUS_OPENAI_API_KEY         optional; most local servers need none
# num_ctx/keep_alive are Ollama-specific and ignored on this path (the server
# owns its context window).
OPENAI_BASE_URL = os.environ.get("CERTUS_OPENAI_BASE_URL", "").rstrip("/")
OPENAI_API_KEY = os.environ.get("CERTUS_OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("CERTUS_OPENAI_MODEL", "")
OPENAI_SCENARIO_MODEL = os.environ.get("CERTUS_OPENAI_SCENARIO_MODEL") or OPENAI_MODEL

log = logging.getLogger("certus.ollama")

# Rough chars-per-token for English prose. Only used to warn about num_ctx
# overflow, not for anything load-bearing — deliberately conservative (real
# tokenizers average ~4 chars/token, so 3.5 over-estimates and warns early).
_CHARS_PER_TOKEN = 3.5


class OllamaError(RuntimeError):
    pass


def _estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def ollama_chat(system: str, user: str, schema: dict, temperature: float,
                num_ctx: int = 8192, on_token=None,
                keep_alive: str = DEFAULT_KEEP_ALIVE, model: str = MODEL) -> dict:
    """Call the local model with a forced JSON schema; return parsed JSON.

    `schema` is passed as Ollama's `format` so the model must emit conforming
    JSON — no format examples in the prompt. Raises OllamaError on transport
    failure or unparseable output.

    If `on_token` is given, it's called as `on_token(token_count, elapsed_s)`
    after each streamed chunk — wire it to a UI progress indicator. Generation
    is the dominant cost here (~6 tok/s measured for this model on this
    hardware), so this is about setting expectations, not changing wall time.
    """
    if OPENAI_BASE_URL:
        return _openai_chat(system, user, schema, temperature, on_token, model)

    est = _estimate_tokens(system) + _estimate_tokens(user)
    log.info("ollama_chat: ~%d prompt tokens (system+user), num_ctx=%d, temp=%s",
             est, num_ctx, temperature)
    if est > num_ctx:
        # The prompt will be truncated from the left by Ollama, silently dropping
        # the earliest content (often the system prompt or the first retrieved
        # chunk). Surface it rather than let the model degrade unexplained.
        log.warning("prompt ~%d tokens EXCEEDS num_ctx=%d — Ollama will truncate; "
                    "raise num_ctx or retrieve fewer chunks", est, num_ctx)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": True,
        "format": schema,
        "keep_alive": keep_alive,
        "options": {"temperature": temperature, "num_ctx": num_ctx},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )

    start = time.monotonic()
    chunks = []
    token_count = 0
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for line in resp:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                piece = event.get("message", {}).get("content", "")
                if piece:
                    chunks.append(piece)
                    token_count += 1
                    if on_token is not None:
                        on_token(token_count, time.monotonic() - start)
                if event.get("done"):
                    break
    except json.JSONDecodeError as e:
        raise OllamaError(f"Model sent a malformed streamed line: {e}") from e
    except OSError as e:
        # Covers urllib.error.URLError (raised while establishing the
        # connection) as well as TimeoutError, ConnectionResetError, and
        # BrokenPipeError — all raised mid-read on a connection that was
        # already established (a hung, overloaded, or killed Ollama).
        raise OllamaError(
            f"Cannot reach Ollama at {OLLAMA_URL} -- is `ollama serve` running?\n  {e}"
        ) from e

    content = "".join(chunks)
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise OllamaError(f"Model returned non-JSON despite schema: {content[:200]!r}") from e


# --- OpenAI-compatible path ---------------------------------------------------

def _openai_chat(system: str, user: str, schema: dict, temperature: float,
                 on_token, requested_model: str) -> dict:
    """Same contract as ollama_chat, against {OPENAI_BASE_URL}/chat/completions.

    Structured output: first try `response_format: json_schema` (supported by
    OpenAI, vLLM, LM Studio, recent llama.cpp). If the server rejects it
    (HTTP 4xx), fall back to `json_object` with the schema stated in the
    system prompt — weaker enforcement, same parse path; the JSON parse below
    still catches nonconforming output rather than letting it through.
    """
    model = OPENAI_SCENARIO_MODEL if requested_model == SCENARIO_MODEL else OPENAI_MODEL
    if not model:
        raise OllamaError("CERTUS_OPENAI_MODEL must be set when "
                          "CERTUS_OPENAI_BASE_URL is used")

    def request(payload: dict) -> str:
        req = urllib.request.Request(
            f"{OPENAI_BASE_URL}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {OPENAI_API_KEY}"} if OPENAI_API_KEY else {}),
            },
        )
        start = time.monotonic()
        chunks, count = [], 0
        with urllib.request.urlopen(req, timeout=300) as resp:
            for line in resp:
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[len(b"data:"):].strip()
                if data == b"[DONE]":
                    break
                delta = (json.loads(data).get("choices") or [{}])[0] \
                    .get("delta", {}).get("content")
                if delta:
                    chunks.append(delta)
                    count += 1
                    if on_token is not None:
                        on_token(count, time.monotonic() - start)
        return "".join(chunks)

    base = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "stream": True,
    }
    try:
        content = request({**base, "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": schema, "strict": True},
        }})
    except urllib.error.HTTPError as e:
        if not (400 <= e.code < 500):
            raise OllamaError(f"{OPENAI_BASE_URL} returned HTTP {e.code}") from e
        log.info("endpoint rejected json_schema response_format (HTTP %d); "
                 "falling back to json_object + schema-in-prompt", e.code)
        fallback_system = (f"{system}\n\nYour reply MUST be a single JSON object "
                           f"conforming to this JSON Schema:\n{json.dumps(schema)}")
        base["messages"][0]["content"] = fallback_system
        try:
            content = request({**base, "response_format": {"type": "json_object"}})
        except OSError as e2:
            raise OllamaError(f"Cannot reach {OPENAI_BASE_URL}: {e2}") from e2
    except OSError as e:
        raise OllamaError(f"Cannot reach {OPENAI_BASE_URL}: {e}") from e

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise OllamaError(f"Model returned non-JSON despite schema: {content[:200]!r}") from e
