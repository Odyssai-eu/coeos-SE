"""Anthropic-compatible /v1/messages surface.

Ported from OdyssAI-X scripts/api.py:7956-8420 (inbound Anthropic layer) and
adapted to SE: the backend is an OpenAI-protocol cloud upstream instead of a
local pool, so the streaming side is an OpenAI-SSE → Anthropic-SSE transcoder.

Lets Claude Code, the Anthropic SDKs, and Aider-in-Anthropic-mode point
ANTHROPIC_BASE_URL at CoeOS SE. Claude tier names (claude-opus/sonnet/haiku-*)
map to the router: opus/sonnet → `coeos` (the whole point), haiku → `coeos`
pinned to the fast axis (Claude Code uses haiku for quick background turns —
running the decider there would tax latency for nothing).

Coverage (parity with the source): system str|blocks, text/tool_use/tool_result
content blocks, tools, tool_choice, streaming events message_start /
content_block_* / message_delta / message_stop, count_tokens estimate.
Not supported (same as source): image/document blocks, prompt caching,
extended thinking passthrough, batch/files APIs.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator, Optional

from pydantic import BaseModel

FAST_AXIS_DEFAULT = "fast_tools"


class AnthropicMessage(BaseModel):
    role: str            # "user" | "assistant"
    content: Any         # str OR list[block]


class AnthropicTool(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: dict


class AnthropicMessagesRequest(BaseModel):
    model: Optional[str] = None
    max_tokens: int = 1024
    messages: list[AnthropicMessage]
    system: Optional[Any] = None          # str | list[{type:"text",text}]
    tools: Optional[list[AnthropicTool]] = None
    tool_choice: Optional[Any] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stop_sequences: Optional[list[str]] = None
    metadata: Optional[dict] = None


def resolve_tier(model: Optional[str]) -> tuple[str, Optional[str]]:
    """Map canonical Claude tier names → (target model, forced axis).

    Env overrides per tier (COEOS_TIER_OPUS/SONNET/HAIKU) accept any model id
    the OpenAI surface accepts (coeos / logical / or:<id> / comet:<id>).
    Non-claude ids pass through untouched."""
    m = (model or "").strip()
    low = m.lower()
    if not low.startswith("claude"):
        return (m or "coeos"), None
    tier = ("opus" if "opus" in low else
            "sonnet" if "sonnet" in low else
            "haiku" if "haiku" in low else None)
    if tier:
        override = (os.environ.get(f"COEOS_TIER_{tier.upper()}") or "").strip()
        if override:
            return override, None
    if tier == "haiku":
        return "coeos", (os.environ.get("COEOS_FAST_AXIS") or FAST_AXIS_DEFAULT)
    return "coeos", None


def text_from_blocks(content: Any) -> str:
    """Anthropic content can be str or list of blocks. Concatenated text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(str(b.get("text", "")))
                elif b.get("type") == "tool_result":
                    inner = b.get("content")
                    if isinstance(inner, str):
                        parts.append(inner)
                    elif isinstance(inner, list):
                        for ib in inner:
                            if isinstance(ib, dict) and ib.get("type") == "text":
                                parts.append(str(ib.get("text", "")))
        return "\n".join(parts)
    return ""


def to_openai_messages(req: AnthropicMessagesRequest) -> list[dict]:
    """Flatten Anthropic system + messages into OpenAI-shaped messages.
    Assistant tool_use blocks → assistant message with tool_calls.
    User tool_result blocks   → tool message(s) (one per tool_use_id)."""
    out: list[dict] = []
    sys_text = req.system if isinstance(req.system, str) else text_from_blocks(req.system)
    if sys_text:
        out.append({"role": "system", "content": sys_text})

    for m in req.messages:
        role, content = m.role, m.content
        if role == "assistant" and isinstance(content, list):
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text":
                    text_parts.append(str(b.get("text", "")))
                elif t == "tool_use":
                    tool_calls.append({
                        "id": b.get("id") or ("call_" + uuid.uuid4().hex[:12]),
                        "type": "function",
                        "function": {"name": b.get("name", ""),
                                     "arguments": json.dumps(b.get("input", {}))},
                    })
            msg: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        elif role == "user" and isinstance(content, list):
            text_parts, tool_msgs = [], []
            for b in content:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text":
                    text_parts.append(str(b.get("text", "")))
                elif t == "tool_result":
                    inner = b.get("content")
                    if isinstance(inner, list):
                        inner_text = "\n".join(
                            str(ib.get("text", "")) for ib in inner
                            if isinstance(ib, dict) and ib.get("type") == "text")
                    else:
                        inner_text = str(inner) if inner is not None else ""
                    tool_msgs.append({"role": "tool",
                                      "tool_call_id": b.get("tool_use_id", ""),
                                      "content": inner_text})
            if text_parts:
                out.append({"role": "user", "content": "\n".join(text_parts)})
            out.extend(tool_msgs)
        else:
            out.append({"role": role, "content": text_from_blocks(content)})
    return out


def tools_to_openai(tools: Optional[list[AnthropicTool]]) -> Optional[list[dict]]:
    if not tools:
        return None
    return [{"type": "function",
             "function": {"name": t.name, "description": t.description or "",
                          "parameters": t.input_schema}} for t in tools]


def tool_choice_to_openai(tc: Any) -> Any:
    """Anthropic {type: auto|any|tool, name?} → OpenAI auto|required|function."""
    if not isinstance(tc, dict):
        return None
    t = tc.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "tool" and tc.get("name"):
        return {"type": "function", "function": {"name": tc["name"]}}
    return None


def to_openai_body(req: AnthropicMessagesRequest, stream: bool) -> dict:
    body: dict = {
        "messages": to_openai_messages(req),
        "max_tokens": req.max_tokens,
        "stream": stream,
    }
    tools = tools_to_openai(req.tools)
    if tools:
        body["tools"] = tools
    tc = tool_choice_to_openai(req.tool_choice)
    if tc is not None:
        body["tool_choice"] = tc
    if req.temperature is not None:
        body["temperature"] = req.temperature
    if req.top_p is not None:
        body["top_p"] = req.top_p
    if req.stop_sequences:
        body["stop"] = req.stop_sequences
    return body


def _stop_reason(finish_reason: Optional[str], has_tools: bool) -> str:
    if has_tools or finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return "end_turn"


def _tool_use_block(tc: dict) -> dict:
    fn = tc.get("function") or {}
    try:
        inp = json.loads(fn["arguments"]) if fn.get("arguments") else {}
    except Exception:
        inp = {"_raw": fn.get("arguments", "")}
    return {"type": "tool_use",
            "id": "toolu_" + str(tc.get("id", uuid.uuid4().hex)).replace("call_", "")[:24],
            "name": fn.get("name", ""),
            "input": inp}


def openai_to_anthropic_response(payload: dict, msg_id: str, model_label: str) -> dict:
    """Translate a non-stream OpenAI chat completion → Anthropic message."""
    choices = payload.get("choices") or []
    msg = (choices[0].get("message") or {}) if choices else {}
    finish = choices[0].get("finish_reason") if choices else None
    blocks: list[dict] = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    tool_calls = msg.get("tool_calls") or []
    blocks.extend(_tool_use_block(tc) for tc in tool_calls)
    usage = payload.get("usage") or {}
    return {
        "id": msg_id, "type": "message", "role": "assistant",
        "model": model_label,
        "content": blocks,
        "stop_reason": _stop_reason(finish, bool(tool_calls)),
        "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                  "output_tokens": usage.get("completion_tokens", 0)},
    }


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def transcode_stream(chunks: AsyncIterator[dict], msg_id: str,
                           model_label: str) -> AsyncIterator[bytes]:
    """OpenAI streaming chunks → Anthropic SSE events.

    Pure transcoder (input is an async iterator of parsed chunk dicts) so it
    unit-tests without a network. Text deltas stream as text_delta on block 0;
    tool calls accumulate per OpenAI index (fragmented JSON arguments) and are
    emitted as complete tool_use blocks with a single input_json_delta before
    message_delta — the Anthropic protocol allows blocks to arrive complete."""
    yield _sse("message_start", {
        "type": "message_start",
        "message": {"id": msg_id, "type": "message", "role": "assistant",
                    "content": [], "model": model_label,
                    "stop_reason": None, "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0}}})

    text_open = False
    finish: Optional[str] = None
    usage: dict = {}
    tools: dict[int, dict] = {}   # OpenAI tool index → {id, name, arguments}

    async for ch in chunks:
        if ch.get("error"):
            yield _sse("error", {"type": "error",
                                 "error": {"type": "api_error",
                                           "message": str(ch["error"].get("message", "upstream error"))}})
            return
        if ch.get("usage"):
            usage = ch["usage"]
        choices = ch.get("choices") or []
        if not choices:
            continue
        c0 = choices[0]
        if c0.get("finish_reason"):
            finish = c0["finish_reason"]
        delta = c0.get("delta") or {}
        txt = delta.get("content")
        if txt:
            if not text_open:
                yield _sse("content_block_start",
                           {"type": "content_block_start", "index": 0,
                            "content_block": {"type": "text", "text": ""}})
                text_open = True
            yield _sse("content_block_delta",
                       {"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": txt}})
        for frag in (delta.get("tool_calls") or []):
            idx = frag.get("index", 0)
            slot = tools.setdefault(idx, {"id": None, "name": "", "arguments": ""})
            if frag.get("id"):
                slot["id"] = frag["id"]
            fn = frag.get("function") or {}
            if fn.get("name"):
                slot["name"] += fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]

    if text_open:
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})

    ordered = [tools[i] for i in sorted(tools)]
    for i, slot in enumerate(ordered, start=1):
        block = _tool_use_block({"id": slot["id"] or uuid.uuid4().hex,
                                 "function": {"name": slot["name"],
                                              "arguments": slot["arguments"]}})
        started = dict(block)
        started["input"] = {}
        yield _sse("content_block_start",
                   {"type": "content_block_start", "index": i, "content_block": started})
        if slot["arguments"]:
            yield _sse("content_block_delta",
                       {"type": "content_block_delta", "index": i,
                        "delta": {"type": "input_json_delta",
                                  "partial_json": slot["arguments"]}})
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": i})

    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": _stop_reason(finish, bool(ordered)),
                  "stop_sequence": None},
        "usage": {"output_tokens": usage.get("completion_tokens", 0)}})
    yield _sse("message_stop", {"type": "message_stop"})


def estimate_input_tokens(req: AnthropicMessagesRequest) -> int:
    """count_tokens estimate — Claude Code probes this to budget its context.
    ~4 chars/token, rounded UP so we never under-count. (api.py:8103)"""
    text_parts: list[str] = []
    try:
        for m in to_openai_messages(req):
            c = m.get("content")
            if isinstance(c, str):
                text_parts.append(c)
            for tc in (m.get("tool_calls") or []):
                fn = tc.get("function") or {}
                text_parts.append(str(fn.get("name", "")))
                text_parts.append(str(fn.get("arguments", "")))
    except Exception:
        if isinstance(req.system, str):
            text_parts.append(req.system)
        for m in req.messages:
            text_parts.append(text_from_blocks(m.content))
    if req.tools:
        try:
            text_parts.append(json.dumps(tools_to_openai(req.tools)))
        except Exception:
            pass
    total_chars = sum(len(p) for p in text_parts if p)
    return max(1, -(-total_chars // 4))
