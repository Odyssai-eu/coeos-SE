"""Unit tests: OpenRouter-only resolution, axis parsing, coeos_resolve."""

import asyncio

import pytest
from fastapi import HTTPException

from coeos_se.config import load_config
from coeos_se.router import (bound_axes, coeos_resolve, decider_spec,
                             parse_axis, resolve_decider, resolve_logical)


# ── resolution (OpenRouter only, plain registry lookup) ──────────────────────

def test_resolve_registry(base_cfg):
    assert resolve_logical(load_config(), "glm") == ("openrouter", "z-ai/glm")
    assert resolve_logical(load_config(), "mm") == ("openrouter", "minimax/mm")


def test_resolve_unknown_logical_is_legacy_upstream(base_cfg):
    # No registry entry → the binding IS the upstream id.
    assert resolve_logical(load_config(), "vendor/raw-id") == ("openrouter", "vendor/raw-id")


def test_resolve_no_key_returns_none(base_cfg, write_cfg):
    del base_cfg["providers"]["openrouter"]["api_key"]
    write_cfg(base_cfg)
    assert resolve_logical(load_config(), "glm") is None


def test_resolve_disabled_returns_none(base_cfg, write_cfg):
    base_cfg["providers"]["openrouter"]["enabled"] = False
    write_cfg(base_cfg)
    assert resolve_logical(load_config(), "glm") is None


def test_resolve_empty_registry_id_returns_none(base_cfg, write_cfg):
    base_cfg["coeos"]["models"]["glm"] = {"name": "GLM", "or": ""}
    write_cfg(base_cfg)
    assert resolve_logical(load_config(), "glm") is None


def test_resolve_env_key_counts_as_ready(base_cfg, write_cfg, monkeypatch):
    del base_cfg["providers"]["openrouter"]["api_key"]
    write_cfg(base_cfg)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert resolve_logical(load_config(), "glm") == ("openrouter", "z-ai/glm")


# ── decider setting {name, or} ───────────────────────────────────────────────

def test_decider_object(base_cfg, write_cfg):
    base_cfg["coeos"]["decider"] = {"name": "Flash", "or": "z-ai/flash"}
    write_cfg(base_cfg)
    cfg = load_config()
    assert decider_spec(cfg["coeos"])["name"] == "Flash"
    assert resolve_decider(cfg, cfg["coeos"]) == ("openrouter", "z-ai/flash")


def test_decider_legacy_string_via_registry(base_cfg, write_cfg):
    base_cfg["coeos"].pop("decider", None)
    base_cfg["coeos"]["decider_model"] = "haiku"
    write_cfg(base_cfg)
    cfg = load_config()
    spec = decider_spec(cfg["coeos"])
    assert spec["name"] == "Haiku" and spec["or"] == "anthropic/haiku"
    assert resolve_decider(cfg, cfg["coeos"]) == ("openrouter", "anthropic/haiku")


def test_decider_legacy_raw_id(base_cfg, write_cfg):
    base_cfg["coeos"].pop("decider", None)
    base_cfg["coeos"]["decider_model"] = "vendor/raw-fast"
    write_cfg(base_cfg)
    cfg = load_config()
    assert resolve_decider(cfg, cfg["coeos"]) == ("openrouter", "vendor/raw-fast")


def test_decider_none_when_unset(base_cfg, write_cfg):
    base_cfg["coeos"].pop("decider", None)
    base_cfg["coeos"].pop("decider_model", None)
    write_cfg(base_cfg)
    cfg = load_config()
    assert decider_spec(cfg["coeos"]) is None
    assert resolve_decider(cfg, cfg["coeos"]) is None


def test_decider_empty_object_falls_back_to_legacy(base_cfg, write_cfg):
    base_cfg["coeos"]["decider"] = {"name": "", "or": ""}
    base_cfg["coeos"]["decider_model"] = "haiku"
    write_cfg(base_cfg)
    cfg = load_config()
    assert decider_spec(cfg["coeos"])["or"] == "anthropic/haiku"


def test_decider_no_key_returns_none(base_cfg, write_cfg):
    base_cfg["providers"] = {}
    write_cfg(base_cfg)
    cfg = load_config()
    assert resolve_decider(cfg, cfg["coeos"]) is None


# ── axis parsing ─────────────────────────────────────────────────────────────

KEYS = ["code", "plan", "creative"]


def test_parse_axis_final_line():
    assert parse_axis("Thinking… deliverable is code.\nAXIS: code", KEYS) == "code"


def test_parse_axis_last_axis_line_wins():
    assert parse_axis("AXIS: plan\nreconsidering…\nAXIS: creative", KEYS) == "creative"


def test_parse_axis_bare_reply():
    assert parse_axis("plan", KEYS) == "plan"


def test_parse_axis_last_mention():
    assert parse_axis("could be code but really it is about plan", KEYS) == "plan"


def test_parse_axis_none():
    assert parse_axis("no idea", KEYS) is None
    assert parse_axis("", KEYS) is None


# ── coeos_resolve ────────────────────────────────────────────────────────────

def _resolve(headers, body=None):
    return asyncio.run(coeos_resolve(load_config(), headers, body or {"messages": []}))


async def _none():
    return None


def test_unbound_axis_excluded(base_cfg):
    axes = bound_axes(load_config()["coeos"])
    assert [a["key"] for a in axes] == ["code", "plan"]  # swift is unbound


def test_header_axis_wins(base_cfg):
    d = _resolve({"x-coeos-axis": "plan"})
    assert (d["axis"], d["provider"], d["upstream"]) == ("plan", "openrouter", "minimax/mm")


def test_header_axis_unbound_ignored_falls_to_default(base_cfg, monkeypatch):
    monkeypatch.setattr("coeos_se.router.llm_classify", lambda *a, **k: _none())
    d = _resolve({"x-coeos-axis": "swift"})
    assert d["axis"] == "code"


def test_decider_failure_falls_to_default(base_cfg, monkeypatch):
    monkeypatch.setattr("coeos_se.router.llm_classify", lambda *a, **k: _none())
    d = _resolve({})
    assert (d["axis"], d["logical"]) == ("code", "glm")


def test_decider_classifies(base_cfg, monkeypatch):
    async def fake_classify(cfg, c, axes, messages):
        return "plan"
    monkeypatch.setattr("coeos_se.router.llm_classify", fake_classify)
    d = _resolve({})
    assert (d["axis"], d["upstream"]) == ("plan", "minimax/mm")


def test_disabled_router_400(base_cfg, write_cfg):
    base_cfg["coeos"]["enabled"] = False
    write_cfg(base_cfg)
    with pytest.raises(HTTPException) as e:
        _resolve({"x-coeos-axis": "code"})
    assert e.value.status_code == 400


def test_no_key_503_no_silent_fallback(base_cfg, write_cfg):
    base_cfg["providers"] = {}
    write_cfg(base_cfg)
    with pytest.raises(HTTPException) as e:
        _resolve({"x-coeos-axis": "code"})
    assert e.value.status_code == 503
    assert e.value.detail["error"] == "coeos_unresolvable"
