"""CoeOS routing core — classify a request into a skill axis, resolve the
axis's bound model to a (provider, upstream id) pair.

Ported from OdyssAI-X scripts/api.py:6986-7269 (`coeos_resolve` and friends),
with the cluster servability machinery deleted (cloud-only: every published
model is always "hot"). OpenRouter is the only provider: resolution is a plain
registry lookup — no priority, no per-axis pin, no fallback table.

The taxonomy (axes) and bindings come ENTIRELY from the imported TMB Settings
— no hard-coded categories or rules.
"""

from __future__ import annotations

import re
import sys

from fastapi import HTTPException

from . import proxy
from .providers import PROVIDER_ID, PROVIDERS, provider_ready

_OR_FIELD = PROVIDERS[PROVIDER_ID]["registry_field"]  # "or"

COEOS_MODEL_ID = "coeos"      # canonical id, matched case-insensitively
COEOS_DISPLAY_ID = "CoeOS"    # public id emitted in /v1/models

# Per-(logical, axis, provider) decision counter — operator visibility.
decisions: dict[tuple, int] = {}


def coeos_cfg(cfg: dict) -> dict:
    return cfg.get("coeos") or {}


def axes_of(c: dict) -> list[dict]:
    """Configured skill axes (data-driven taxonomy). Each = {key, label,
    model, description?, provider?, bench?, verified?}."""
    axes = c.get("axes")
    return [a for a in axes if isinstance(a, dict) and a.get("key")] if isinstance(axes, list) else []


def bound_axes(c: dict) -> list[dict]:
    """Axes with a non-empty model binding. Unbound axes (a declared gap in
    the settings, e.g. swift with no strong model benched yet) stay visible in
    the config but are excluded from the routing menu — the decider can only
    pick an axis it can actually serve."""
    return [a for a in axes_of(c)
            if (a.get("model") or "").strip()
            and str(a["model"]).strip().lower() != COEOS_MODEL_ID]


def registry_of(c: dict) -> dict:
    """Logical model name → {name, or, note?}. The registry is the only place
    provider-native ids live; axes bind portable logical names."""
    reg = c.get("models")
    return reg if isinstance(reg, dict) else {}


def _score_table_lookup(table: dict) -> dict[str, str]:
    """row-identity (name/alias/or_id/served_model, lowercased) -> row name."""
    out: dict[str, str] = {}
    for name, m in (table.get("models") or {}).items():
        if not isinstance(m, dict):
            continue
        for key in (name, m.get("alias"), m.get("or_id"), m.get("served_model")):
            if key:
                out[str(key).strip().lower()] = name
    return out


def resolve_score_table(table: dict, registry: dict) -> list[dict]:
    """One-time resolve: for each axis in the score-table's taxonomy, pick the
    best-scoring model restricted to what THIS operator's registry can serve
    (their 'army' — no separate fleet declaration). Reference-role rows (our
    benchmark etalons) are never picked. Tie on score -> cheaper wins (unknown
    cost sorts last). Unlike OdyssAI-X's live resolver, SE resolves ONCE at
    import time and writes a normal axes=[{key,label,model,description}] list
    — the router stays the simple pre-decided-binding lookup it already is;
    only the IMPORT source changed from a pre-baked settings file to the raw
    score table. Re-import (or a future 're-resolve') to pick up registry
    changes."""
    lut = _score_table_lookup(table)
    axes_meta = table.get("axes") or {}
    out = []
    for axis_key, meta in axes_meta.items():
        best = None
        for logical in registry:
            row = lut.get(str(logical).strip().lower())
            if not row:
                continue
            m = (table.get("models") or {}).get(row) or {}
            if m.get("role") == "reference":
                continue
            score = ((m.get("axes") or {}).get(axis_key) or {}).get("score")
            if score is None:
                continue
            cost = m.get("cost_per_test")
            sort_key = (-score, cost is None, cost if cost is not None else 0.0, str(logical))
            if best is None or sort_key < best[0]:
                best = (sort_key, logical)
        out.append({"key": axis_key, "label": (meta or {}).get("label", axis_key),
                    "description": (meta or {}).get("description", ""),
                    "model": best[1] if best else ""})
    return out


def decider_spec(c: dict) -> dict | None:
    """The decider's own setting: {name, or} — its display name and OpenRouter
    id, independent of the axis registry.

    Back-compat: a legacy `decider_model` string is looked up in the registry
    (or treated as a raw upstream id)."""
    d = c.get("decider")
    if isinstance(d, dict) and (d.get(_OR_FIELD) or "").strip():
        return d
    legacy = (c.get("decider_model") or "").strip()
    if not legacy:
        return None
    entry = registry_of(c).get(legacy)
    if isinstance(entry, dict):
        return {"name": entry.get("name") or legacy, _OR_FIELD: entry.get(_OR_FIELD) or ""}
    return {"name": legacy, _OR_FIELD: legacy}


def resolve_decider(cfg: dict, c: dict) -> tuple[str, str] | None:
    """Decider spec → (provider_id, upstream id) on OpenRouter."""
    spec = decider_spec(c)
    if not spec or not provider_ready(cfg):
        return None
    upstream = (spec.get(_OR_FIELD) or "").strip()
    return (PROVIDER_ID, upstream) if upstream else None


def resolve_logical(cfg: dict, logical: str) -> tuple[str, str] | None:
    """Logical name → (provider_id, upstream_model_id) on OpenRouter. Plain
    registry lookup — no priority, no fallback. A logical with no registry
    entry is treated as the upstream id itself (back-compat with hand-written
    settings)."""
    logical = (logical or "").strip()
    if not logical or not provider_ready(cfg):
        return None
    entry = registry_of(coeos_cfg(cfg)).get(logical)
    if entry is None:
        return PROVIDER_ID, logical  # legacy: the binding IS the upstream id
    upstream = (entry.get(_OR_FIELD) or "").strip()
    return (PROVIDER_ID, upstream) if upstream else None


def header_axis(headers, keys: list[str]) -> str | None:
    """Explicit axis from the agent. `x-coeos-axis` wins; `x-coeos-category`
    is a back-compat alias. Returned only if it's a CONFIGURED bound axis."""
    for h in ("x-coeos-axis", "x-coeos-category"):
        v = (headers.get(h) or "").strip().lower()
        if v in keys:
            return v
    return None


def parse_axis(text: str, keys: list[str]) -> str | None:
    """Extract the chosen axis key from the decider's (possibly multi-token,
    reasoned) reply. Priority: an explicit final `AXIS: <key>` line → a bare
    reply whose first token is a key → the LAST word-bounded key mentioned
    anywhere. None if no configured key is found. (api.py:7104)"""
    if not text or not text.strip():
        return None
    low = text.lower()
    keyset = {k.lower() for k in keys}
    for m in reversed(list(re.finditer(r"axis\s*[:=]\s*[`\"']?([a-z0-9_]+)", low))):
        if m.group(1) in keyset:
            return m.group(1)
    first = low.strip().split()[0].strip('`"\',.') if low.strip() else ""
    if first in keyset:
        return first
    best, best_pos = None, -1
    for k in keys:
        for m in re.finditer(r"\b" + re.escape(k.lower()) + r"\b", low):
            if m.start() > best_pos:
                best, best_pos = k, m.start()
    return best


def _last_user_text(messages: list[dict]) -> str:
    if not messages:
        return ""
    c = messages[-1].get("content")
    if isinstance(c, str):
        return c[:8000]
    if isinstance(c, list):  # multimodal — keep the text parts
        return " ".join(p.get("text", "") for p in c
                        if isinstance(p, dict) and p.get("type") == "text")[:8000]
    return ""


async def llm_classify(cfg: dict, c: dict, axes: list[dict], messages: list[dict]) -> str | None:
    """Ask the decider to UNDERSTAND the request and classify it into ONE
    configured axis. The taxonomy (keys + labels + per-axis frontier notes) is
    passed from config — nothing hard-coded. The decider is a reasoning
    router, not a tag matcher: full last message + axis descriptions + room to
    think, then a final `AXIS:` line. (api.py:7131)"""
    resolved = resolve_decider(cfg, c)
    if resolved is None:
        return None  # decider unresolvable → caller falls back to default axis
    pid, upstream = resolved

    def _axis_line(ax: dict) -> str:
        line = f"- {ax['key']}: {ax.get('label', ax['key'])}"
        desc = ax.get("description") or ax.get("hint")
        if desc:
            line += f" — {desc}"
        return line

    keys = [ax["key"] for ax in axes]
    menu = "\n".join(_axis_line(ax) for ax in axes)
    prompt = (
        "You are CoeOS's routing classifier. UNDERSTAND the request — its true "
        "intent and the nature of the deliverable (target language, domain) — then "
        "pick the SINGLE best-matching skill axis from the menu. Prefer the MOST "
        "SPECIFIC axis that applies; choose a generic bucket (e.g. code_general) "
        "ONLY when no specific axis fits. Honour each axis's frontier notes "
        "(the '— …' clause, including its 'not here if …' guidance).\n\n"
        f"Axes:\n{menu}\n\n"
        f"Request:\n{_last_user_text(messages)}\n\n"
        "Reason in at most two short sentences, then end your reply with a final "
        f"line exactly: `AXIS: <key>` where <key> is one of: {', '.join(keys)}")
    try:
        buf = await proxy.unary_upstream_text(
            cfg, pid, upstream, [{"role": "user", "content": prompt}], max_tokens=600)
    except Exception as e:
        sys.stderr.write(f"[coeos-se] decider error: {e}\n")
        return None
    return parse_axis(buf, keys)


async def coeos_resolve(cfg: dict, headers, body: dict) -> dict:
    """Resolve `coeos` → routing decision. Classify the request into one
    CONFIGURED bound axis (explicit `x-coeos-axis` header → decider LLM →
    `default_axis`), then resolve that axis's binding via option 1.

    No silent fallback to a different model: if the recommended binding can't
    be resolved on any ready provider, surface a clear 503 telling the user
    which key to add or which registry id to fill. (api.py:7207)"""
    c = coeos_cfg(cfg)
    if not c.get("enabled"):
        raise HTTPException(status_code=400, detail={
            "error": "coeos_disabled",
            "message": "CoeOS router is disabled. Enable it in the dashboard "
                       "or PUT /admin/coeos {\"enabled\": true}."})
    axes = bound_axes(c)
    if not axes:
        raise HTTPException(status_code=503, detail={
            "error": "coeos_no_axes",
            "message": "No skill axes are bound. Import a TMB Settings file "
                       "(dashboard → Import, or PUT /admin/coeos)."})
    keys = [ax["key"] for ax in axes]
    default_axis = c.get("default_axis")
    if default_axis not in keys:
        default_axis = keys[0]

    # Classify: explicit header → decider LLM → default.
    axis = header_axis(headers, keys)
    if not axis:
        axis = await llm_classify(cfg, c, axes, body.get("messages") or [])
    if axis not in keys:
        axis = default_axis

    ax = next(a for a in axes if a["key"] == axis)
    logical = str(ax["model"]).strip()
    resolved = resolve_logical(cfg, logical)
    if resolved is None:
        entry = registry_of(c).get(logical) or {}
        display = entry.get("name") or logical
        raise HTTPException(status_code=503, detail={
            "error": "coeos_unresolvable",
            "axis": axis,
            "recommended": display,
            "message": f"{display} — can't be served. Add your OpenRouter API key, "
                       "or fill this model's id in the registry. CoeOS does not "
                       "silently route to a different model."})
    pid, upstream = resolved

    k = (logical, axis, pid)
    decisions[k] = decisions.get(k, 0) + 1
    return {"provider": pid, "upstream": upstream, "axis": axis, "logical": logical}
