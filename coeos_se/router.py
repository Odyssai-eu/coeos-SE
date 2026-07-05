"""CoeOS routing core — classify a request into a skill axis, resolve the
axis's bound model to a (provider, upstream id) pair.

Ported from OdyssAI-X scripts/api.py:6986-7269 (`coeos_resolve` and friends),
with the cluster servability machinery deleted (cloud-only: every published
model is always "hot") and the model registry extended to per-provider ids
with resolution option 1: global priority → per-axis pin → fallback to the
other provider when an id or key is missing.

The taxonomy (axes) and bindings come ENTIRELY from the imported TMB Settings
— no hard-coded categories or rules.
"""

from __future__ import annotations

import re
import sys

from fastapi import HTTPException

from . import proxy
from .providers import PROVIDERS, provider_priority, provider_ready

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
    """Logical model name → {name, or, comet, note?}. The registry is the only
    place provider-native ids live; axes bind portable logical names."""
    reg = c.get("models")
    return reg if isinstance(reg, dict) else {}


def decider_spec(c: dict) -> dict | None:
    """The decider's own setting: {name, or, comet} — its display name and its
    native id on each provider, independent of the axis registry.

    Back-compat: a legacy `decider_model` string is looked up in the registry
    (or, failing that, treated as a raw upstream id on both providers)."""
    d = c.get("decider")
    if isinstance(d, dict) and any((d.get(PROVIDERS[p]["registry_field"]) or "").strip()
                                   for p in PROVIDERS):
        return d
    legacy = (c.get("decider_model") or "").strip()
    if not legacy:
        return None
    entry = registry_of(c).get(legacy)
    if isinstance(entry, dict):
        return {"name": entry.get("name") or legacy,
                **{PROVIDERS[p]["registry_field"]: entry.get(PROVIDERS[p]["registry_field"]) or ""
                   for p in PROVIDERS}}
    return {"name": legacy,
            **{PROVIDERS[p]["registry_field"]: legacy for p in PROVIDERS}}


def resolve_decider(cfg: dict, c: dict) -> tuple[str, str] | None:
    """Decider spec → (provider_id, upstream id), resolution option 1 (global
    priority; a provider is skipped when not ready or its id is empty)."""
    spec = decider_spec(c)
    if not spec:
        return None
    for pid in provider_priority(cfg):
        if not provider_ready(cfg, pid):
            continue
        upstream = (spec.get(PROVIDERS[pid]["registry_field"]) or "").strip()
        if upstream:
            return pid, upstream
    return None


def resolve_logical(cfg: dict, logical: str, pin: str | None = None) -> tuple[str, str] | None:
    """Resolution option 1: logical name → (provider_id, upstream_model_id).

    Order = per-axis pin first (if any), then the global provider priority.
    A provider is skipped when it's not ready (disabled / no key) or the
    registry has no id for it — the other provider covers the gap. A logical
    with no registry entry is treated as the upstream id itself (back-compat
    with hand-written settings)."""
    logical = (logical or "").strip()
    if not logical:
        return None
    entry = registry_of(coeos_cfg(cfg)).get(logical)
    order = provider_priority(cfg)
    if pin in PROVIDERS:
        order = [pin] + [p for p in order if p != pin]
    for pid in order:
        if not provider_ready(cfg, pid):
            continue
        if entry is None:
            return pid, logical  # legacy: the binding IS the upstream id
        upstream = (entry.get(PROVIDERS[pid]["registry_field"]) or "").strip()
        if upstream:
            return pid, upstream
    return None


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
            cfg, pid, upstream, [{"role": "user", "content": prompt}], max_tokens=160)
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
    pin = ax.get("provider")
    resolved = resolve_logical(cfg, logical, pin)
    if resolved is None:
        entry = registry_of(c).get(logical) or {}
        display = entry.get("name") or logical
        raise HTTPException(status_code=503, detail={
            "error": "coeos_unresolvable",
            "axis": axis,
            "recommended": display,
            "message": f"{display} — no ready provider can serve it. Add your "
                       "OpenRouter or Comet API key, or fill this model's id in "
                       "the registry. CoeOS does not silently route to a "
                       "different model."})
    pid, upstream = resolved

    k = (logical, axis, pid)
    decisions[k] = decisions.get(k, 0) + 1
    return {"provider": pid, "upstream": upstream, "axis": axis, "logical": logical}
