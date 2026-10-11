"""Thin wrapper around a local Ollama server.

This is the ONLY place in the codebase that knows how to talk to an LLM.
Every agent calls `generate()` from here, so swapping the backend later
(e.g. pointing "pro mode" at the Claude API instead of a local model) is a
one-file change, not a rewrite of every agent.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import requests

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:3b")
# 120s wasn't enough in practice -- a genuinely cold model (nothing generated
# recently) commonly takes longer than that just to load into memory before
# it can even start generating, on top of everything else usually running
# alongside it (Jenkins, kind, kube-prometheus-stack). 240s plus the retry
# below gives a cold model a real chance without the caller waiting forever.
DEFAULT_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "240"))


class OllamaUnavailable(RuntimeError):
    """Raised when the local Ollama server can't be reached OR when it's
    reachable but rejects the request (e.g. the requested model hasn't been
    pulled). Callers only need to know "the LLM path didn't work" to decide
    whether to fall back to a heuristic -- but the message itself
    distinguishes the two cases so a human debugging it isn't misled."""


def is_available(timeout: float = 2.0) -> bool:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def generate(
    prompt: str,
    system: Optional[str] = None,
    model: Optional[str] = None,
    json_mode: bool = False,
    temperature: float = 0.2,
) -> str:
    """Send a single prompt to Ollama and return the raw text response.

    Raises OllamaUnavailable if the server can't be reached OR rejects the
    request, so callers can decide whether to fall back to a heuristic
    instead of crashing.
    """
    resolved_model = model or OLLAMA_MODEL
    payload = {
        "model": resolved_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system
    if json_mode:
        payload["format"] = "json"

    try:
        resp = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=DEFAULT_TIMEOUT)
    except requests.exceptions.ReadTimeout:
        # The server IS reachable (we connected fine) -- it just hasn't
        # finished responding yet, almost always because the model is still
        # cold-loading into memory. Ollama keeps working on that same
        # request/model server-side even after our client gives up waiting,
        # so a single retry right after a timeout very often succeeds
        # quickly, hitting an already-warm model instead of a cold one.
        try:
            resp = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            raise OllamaUnavailable(
                f"Ollama at {OLLAMA_HOST} timed out twice in a row (waited {DEFAULT_TIMEOUT:.0f}s "
                f"each time) trying to run model '{resolved_model}'. This usually means the model "
                f"is larger than your machine can generate with in reasonable time -- try a smaller "
                f"model (e.g. qwen2.5-coder:3b) or `ollama run {resolved_model}` once first to "
                f"confirm it works at all outside this pipeline. (Original error: {exc})"
            ) from exc
    except requests.RequestException as exc:
        # The server itself couldn't be reached at all (connection refused,
        # DNS failure, timeout before any response) -- this is the "ollama
        # serve isn't running" case.
        raise OllamaUnavailable(
            f"Could not reach Ollama at {OLLAMA_HOST} -- is `ollama serve` running? "
            f"(Original error: {exc})"
        ) from exc

    if not resp.ok:
        # The server responded, so it's definitely running -- but rejected
        # the request. By far the most common cause is that `resolved_model`
        # hasn't been pulled. Distinguishing this from "can't reach Ollama"
        # is the whole point of this branch: they need different fixes.
        body = resp.text.strip()
        raise OllamaUnavailable(
            f"Ollama is running and reachable at {OLLAMA_HOST}, but rejected the request "
            f"(HTTP {resp.status_code}). This usually means model '{resolved_model}' hasn't "
            f"been pulled -- run `ollama pull {resolved_model}`, or set OLLAMA_MODEL to a "
            f"model you've already pulled (check with `ollama list`). "
            f"Ollama's response: {body[:300]}"
        )

    data = resp.json()
    return data.get("response", "")


def generate_json(prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> dict:
    """Convenience wrapper for prompts that must return a JSON object."""
    raw = generate(prompt, system=system, model=model, json_mode=True, temperature=0.0)
    return json.loads(raw)
