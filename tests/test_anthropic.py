"""Anthropic surface: translation, tier mapping, SSE transcoder, endpoints."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from coeos_se import anthropic_api as A
from coeos_se.app import app


def _req(**kw):
    base = {"model": "coeos", "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}]}
    base.update(kw)
    return A.AnthropicMessagesRequest(**base)


# ── tier mapping ─────────────────────────────────────────────────────────────

def test_tiers_map_to_coeos(monkeypatch):
    monkeypatch.delenv("COEOS_TIER_OPUS", raising=False)
    monkeypatch.delenv("COEOS_FAST_AXIS", raising=False)
    assert A.resolve_tier("claude-opus-4-7") == ("coeos", None)
    assert A.resolve_tier("claude-sonnet-4-6") == ("coeos", None)
    assert A.resolve_tier("claude-haiku-4-5") == ("coeos", "fast_tools")
    assert A.resolve_tier("claude-3-5-haiku-latest") == ("coeos", "fast_tools")


def test_tier_env_override(monkeypatch):
    monkeypatch.setenv("COEOS_TIER_OPUS", "or:z-ai/glm-5.2")
    assert A.resolve_tier("claude-opus-4-7") == ("or:z-ai/glm-5.2", None)


def test_non_claude_ids_pass_through():
    assert A.resolve_tier("glm-5.2") == ("glm-5.2", None)
    assert A.resolve_tier("") == ("coeos", None)
    assert A.resolve_tier(None) == ("coeos", None)


# ── request translation ──────────────────────────────────────────────────────

def test_system_and_blocks_flatten():
    r = _req(system=[{"type": "text", "text": "be terse"}],
             messages=[{"role": "user",
                        "content": [{"type": "text", "text": "hello"}]}])
    msgs = A.to_openai_messages(r)
    assert msgs[0] == {"role": "system", "content": "be terse"}
    assert msgs[1] == {"role": "user", "content": "hello"}


def test_tool_use_and_result_roundtrip():
    r = _req(messages=[
        {"role": "assistant", "content": [
            {"type": "text", "text": "calling"},
            {"type": "tool_use", "id": "toolu_abc", "name": "read_file",
             "input": {"path": "x.py"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_abc",
             "content": [{"type": "text", "text": "file contents"}]}]},
    ])
    msgs = A.to_openai_messages(r)
    assistant = msgs[0]
    assert assistant["tool_calls"][0]["function"]["name"] == "read_file"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"path": "x.py"}
    tool = msgs[1]
    assert tool == {"role": "tool", "tool_call_id": "toolu_abc",
                    "content": "file contents"}


def test_tools_and_tool_choice_translate():
    r = _req(tools=[{"name": "grep", "description": "search",
                     "input_schema": {"type": "object"}}],
             tool_choice={"type": "any"})
    body = A.to_openai_body(r, stream=False)
    assert body["tools"][0]["function"]["name"] == "grep"
    assert body["tool_choice"] == "required"
    assert A.tool_choice_to_openai({"type": "tool", "name": "grep"}) == \
        {"type": "function", "function": {"name": "grep"}}


def test_body_carries_sampling_params():
    r = _req(temperature=0.2, top_p=0.9, stop_sequences=["END"])
    body = A.to_openai_body(r, stream=True)
    assert (body["temperature"], body["top_p"], body["stop"]) == (0.2, 0.9, ["END"])
    assert body["stream"] is True and body["max_tokens"] == 64


# ── response translation ─────────────────────────────────────────────────────

def test_nonstream_response_maps():
    payload = {"choices": [{"message": {"content": "hello",
                                        "tool_calls": [{"id": "call_1",
                                                        "function": {"name": "f",
                                                                     "arguments": "{\"a\":1}"}}]},
                            "finish_reason": "tool_calls"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    out = A.openai_to_anthropic_response(payload, "msg_x", "glm-5.2")
    assert out["content"][0] == {"type": "text", "text": "hello"}
    tu = out["content"][1]
    assert tu["type"] == "tool_use" and tu["input"] == {"a": 1}
    assert out["stop_reason"] == "tool_use"
    assert out["usage"] == {"input_tokens": 10, "output_tokens": 5}


def test_stop_reason_mapping():
    assert A._stop_reason("stop", False) == "end_turn"
    assert A._stop_reason("length", False) == "max_tokens"
    assert A._stop_reason("tool_calls", True) == "tool_use"


# ── streaming transcoder (pure, no network) ──────────────────────────────────

async def _gen(chunks):
    for c in chunks:
        yield c


def _events(chunks):
    async def run():
        out = []
        async for b in A.transcode_stream(_gen(chunks), "msg_1", "glm-5.2"):
            out.append(b.decode())
        return out
    raw = asyncio.run(run())
    parsed = []
    for block in raw:
        lines = [l for l in block.strip().split("\n") if l.startswith("data:")]
        for l in lines:
            parsed.append(json.loads(l[5:].strip()))
    return parsed


def test_transcode_text_stream():
    evs = _events([
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"completion_tokens": 2}},
    ])
    types = [e["type"] for e in evs]
    assert types == ["message_start", "content_block_start", "content_block_delta",
                     "content_block_delta", "content_block_stop", "message_delta",
                     "message_stop"]
    deltas = [e["delta"]["text"] for e in evs if e["type"] == "content_block_delta"]
    assert "".join(deltas) == "Hello"
    md = next(e for e in evs if e["type"] == "message_delta")
    assert md["delta"]["stop_reason"] == "end_turn"
    assert md["usage"]["output_tokens"] == 2


def test_transcode_tool_call_fragments():
    evs = _events([
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_9", "function": {"name": "read", "arguments": "{\"pa"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "th\": \"x\"}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ])
    start = next(e for e in evs if e["type"] == "content_block_start"
                 and e["content_block"]["type"] == "tool_use")
    assert start["content_block"]["name"] == "read"
    jd = next(e for e in evs if e["type"] == "content_block_delta"
              and e["delta"]["type"] == "input_json_delta")
    assert json.loads(jd["delta"]["partial_json"]) == {"path": "x"}
    md = next(e for e in evs if e["type"] == "message_delta")
    assert md["delta"]["stop_reason"] == "tool_use"


def test_transcode_upstream_error():
    evs = _events([{"error": {"message": "boom", "code": 401}}])
    assert evs[-1]["type"] == "error"
    assert "boom" in evs[-1]["error"]["message"]


# ── endpoints ────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(cfg_file):
    with TestClient(app) as c:
        yield c


def test_count_tokens(client):
    r = client.post("/v1/messages/count_tokens", json={
        "model": "claude-opus-4-7", "max_tokens": 1,
        "messages": [{"role": "user", "content": "x" * 400}]})
    assert r.status_code == 200
    assert r.json()["input_tokens"] == 100


def test_messages_no_keys_503(client):
    # Bundled settings auto-import, but no provider key → clean 503 from the
    # shared resolution path (same contract as the OpenAI surface).
    r = client.post("/v1/messages", json={
        "model": "claude-opus-4-7", "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503


def test_decisions_clear(client):
    from coeos_se.router import decisions
    decisions[("glm", "debug", "openrouter")] = 3
    assert client.get("/admin/coeos/decisions").json()["decisions"]
    r = client.delete("/admin/coeos/decisions")
    assert r.status_code == 200
    assert client.get("/admin/coeos/decisions").json()["decisions"] == []


def test_endpoints_page_served(client):
    r = client.get("/endpoints")
    assert r.status_code == 200
    assert "Claude Code" in r.text and "Codex" in r.text and "curl" in r.text
