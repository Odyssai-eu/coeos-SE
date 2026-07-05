"""OpenAI-compatible passthrough proxy to the upstream provider.

Ported from OdyssAI-X `_proxy_chat_completion` (scripts/api.py:5020-5243),
OpenAI protocol only (both SE providers speak it). The request body is relayed
as-is — `reasoning_effort`, `thinking`, `tools`, any extra field the client
sends reaches the upstream untouched. Streaming is a verbatim SSE byte relay,
so upstream usage fields stay transparent to the client.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from .providers import PROVIDERS, provider_key

# OpenRouter attribution headers (ignored by other providers).
_ATTRIBUTION = {
    "http-referer": "https://odyssai.eu",
    "x-title": "CoeOS SE",
}


def _upstream_headers(cfg: dict, pid: str) -> dict:
    headers = {"content-type": "application/json", **_ATTRIBUTION}
    key = provider_key(cfg, pid)
    if key:
        headers["authorization"] = f"Bearer {key}"
    return headers


def _prepare_body(body: dict, upstream: str) -> dict:
    """Copy the client body, retarget `model`, normalise thinking flags.
    Everything else passes through verbatim."""
    fwd = dict(body)
    fwd["model"] = upstream
    fwd.pop("session_id", None)  # internal field some clients attach

    # `enable_thinking` is OdyssAI-X's canonical name; most cloud upstreams use
    # `thinking`. An explicit client value wins; we only translate the field
    # name, never inject a default (api.py:5064-5084, minus the server-wide
    # default which is an engine setting SE doesn't have).
    et = fwd.pop("enable_thinking", None)
    t = fwd.get("thinking", None)
    if isinstance(t, dict):
        think_on = None  # structured config — leave untouched
    elif isinstance(t, bool):
        think_on = t
    elif et is not None:
        think_on = bool(et)
    else:
        think_on = None
    if think_on is not None:
        # MiniMax's OpenAI-compatible API validates `thinking` as an OBJECT
        # ({"type":"enabled"|"disabled"}), not a bare boolean.
        if "minimax" in str(upstream).lower():
            fwd["thinking"] = {"type": "enabled" if think_on else "disabled"}
        else:
            fwd["thinking"] = think_on

    # OpenAI spec: streaming responses omit `usage` unless the client opts in.
    # Always opt in so clients can render token counts (api.py:5085-5098).
    if fwd.get("stream"):
        opts = fwd.get("stream_options")
        opts = dict(opts) if isinstance(opts, dict) else {}
        opts.setdefault("include_usage", True)
        fwd["stream_options"] = opts
    return fwd


async def proxy_chat(cfg: dict, pid: str, upstream: str, body: dict,
                     decision_headers: dict | None = None):
    """Relay an OpenAI chat completion to `pid`'s upstream as `upstream`.

    Streaming: verbatim SSE byte relay. Non-streaming: parse JSON, return with
    the upstream status code. Decision headers (x-coeos-axis/model/provider)
    ride on the response so agents can observe the routing.
    """
    meta = PROVIDERS[pid]
    url = f"{meta['api_base'].rstrip('/')}/chat/completions"
    headers = _upstream_headers(cfg, pid)
    fwd = _prepare_body(body, upstream)
    extra = decision_headers or {}

    if fwd.get("stream"):
        async def gen() -> AsyncIterator[bytes]:
            timeout = httpx.Timeout(60.0, read=None)  # no read timeout for SSE
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    async with client.stream("POST", url, headers=headers, json=fwd) as r:
                        if r.status_code >= 400:
                            txt = (await r.aread()).decode("utf-8", "ignore")
                            err = {"error": {"message": txt[:300], "code": r.status_code,
                                             "provider": pid}}
                            yield ("data: " + json.dumps(err) + "\n\n").encode()
                            return
                        async for chunk in r.aiter_bytes():
                            if chunk:
                                yield chunk
                except Exception as e:
                    err = {"error": {"message": str(e)[:300], "provider": pid}}
                    yield ("data: " + json.dumps(err) + "\n\n").encode()

        return StreamingResponse(gen(), media_type="text/event-stream", headers=extra)

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            r = await client.post(url, headers=headers, json=fwd)
        except Exception as e:
            raise HTTPException(502, f"upstream {pid} unreachable: {e}")
        try:
            payload = r.json()
        except Exception:
            raise HTTPException(502, f"upstream {pid} returned non-JSON (status {r.status_code})")
        return JSONResponse(payload, status_code=r.status_code, headers=extra)


async def unary_upstream_json(cfg: dict, pid: str, upstream: str,
                              body: dict) -> tuple[int, dict]:
    """Non-streaming upstream call returning (status, payload). Used by the
    Anthropic surface, which needs the parsed OpenAI response to translate it
    rather than a passthrough Response."""
    meta = PROVIDERS[pid]
    url = f"{meta['api_base'].rstrip('/')}/chat/completions"
    fwd = _prepare_body(body, upstream)
    fwd["stream"] = False
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            r = await client.post(url, headers=_upstream_headers(cfg, pid), json=fwd)
        except Exception as e:
            raise HTTPException(502, f"upstream {pid} unreachable: {e}")
        try:
            return r.status_code, r.json()
        except Exception:
            raise HTTPException(502, f"upstream {pid} returned non-JSON (status {r.status_code})")


async def stream_upstream_chunks(cfg: dict, pid: str, upstream: str, body: dict):
    """Streaming upstream call yielding PARSED OpenAI chunk dicts (skipping
    keep-alive comments), for surfaces that transcode rather than relay.
    Yields {"error": {...}} once and stops on upstream/transport errors."""
    meta = PROVIDERS[pid]
    url = f"{meta['api_base'].rstrip('/')}/chat/completions"
    fwd = _prepare_body(body, upstream)
    fwd["stream"] = True
    opts = fwd.get("stream_options")
    opts = dict(opts) if isinstance(opts, dict) else {}
    opts.setdefault("include_usage", True)
    fwd["stream_options"] = opts
    timeout = httpx.Timeout(60.0, read=None)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            async with client.stream("POST", url, headers=_upstream_headers(cfg, pid),
                                     json=fwd) as r:
                if r.status_code >= 400:
                    txt = (await r.aread()).decode("utf-8", "ignore")
                    yield {"error": {"message": txt[:300], "code": r.status_code,
                                     "provider": pid}}
                    return
                buf = ""
                async for text in r.aiter_text():
                    buf += text
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue  # SSE comments / event names / blanks
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            return
                        try:
                            yield json.loads(payload)
                        except Exception:
                            continue
        except Exception as e:
            yield {"error": {"message": str(e)[:300], "provider": pid}}


async def unary_upstream_text(cfg: dict, pid: str, upstream: str,
                              messages: list[dict], max_tokens: int = 600) -> str:
    """Small non-streaming call used by the decider. Returns the assistant
    text ("" on any failure — callers fall back to the default axis).

    Reasoning-first upstreams are the trap here (verified live): some ignore
    `thinking: false`, burn the whole budget inside the `reasoning` field and
    return an EMPTY content — the AXIS line never arrives. Three guards:
    OpenRouter's unified `reasoning: {enabled: false}` hint (ignored by
    upstreams that don't know it), a budget that survives a thinking block
    anyway, and parsing `reasoning` too (content last, so a real final answer
    always wins in the last-match parsing rules)."""
    meta = PROVIDERS[pid]
    url = f"{meta['api_base'].rstrip('/')}/chat/completions"
    body = {"model": upstream, "messages": messages, "max_tokens": max_tokens,
            "stream": False, "thinking": False,
            "reasoning": {"enabled": False}}
    if "minimax" in str(upstream).lower():
        body["thinking"] = {"type": "disabled"}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=_upstream_headers(cfg, pid), json=body)
            if r.status_code >= 400:
                return ""
            payload = r.json()
            choices = payload.get("choices") or []
            msg = (choices[0].get("message") or {}) if choices else {}
            reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
            content = msg.get("content") or ""
            return f"{reasoning}\n{content}".strip()
    except Exception:
        return ""
