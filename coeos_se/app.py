"""CoeOS SE — FastAPI app: OpenAI-compatible surface + admin + dashboard.

Endpoint map (ported from OdyssAI-X scripts/api.py, trimmed to SE scope):
  POST /v1/chat/completions   the OpenAI surface (model:"coeos" | logical | or:/comet: prefixed)
  GET  /v1/models             CoeOS + the registry's logical models
  GET/PUT /admin/coeos        read / import the TMB Settings (PUT = import)
  GET  /admin/coeos/decisions routing decision counters
  GET  /admin/coeos/export    settings JSON as a download
  GET  /admin/providers       redacted provider list (never returns keys)
  PUT  /admin/providers/{id}  set/clear key, toggle enabled
  POST /admin/providers/{id}/test        reachability via upstream /models
  GET  /admin/providers/{id}/upstream-models
  PUT  /admin/priority        global provider preference order
  GET  /dashboard             single-file web UI
"""

from __future__ import annotations

import importlib.resources
import json
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               Response, StreamingResponse)
from pydantic import BaseModel

from . import __version__, anthropic_api, proxy, router
from .config import config_txn, load_config
from .providers import (PREFIX_TO_PROVIDER, PROVIDERS, list_upstream_models,
                        provider_key, provider_priority, ready_providers,
                        redact_provider)
from .router import (COEOS_DISPLAY_ID, COEOS_MODEL_ID, bound_axes, coeos_cfg,
                     coeos_resolve, decider_spec, decisions, registry_of,
                     resolve_logical)

_BUNDLED_SETTINGS = "TMB-Settings-SE-v0.1.json"


def _bundled_settings_text() -> str | None:
    try:
        return (importlib.resources.files("coeos_se") / "settings" /
                _BUNDLED_SETTINGS).read_text()
    except Exception:
        return None


def _auto_import_settings() -> None:
    """First boot on an empty config: load the bundled TMB Settings so
    `docker compose up` + an env key = a working router immediately."""
    cfg = load_config()
    if coeos_cfg(cfg).get("axes"):
        return
    text = _bundled_settings_text()
    if not text:
        return
    try:
        settings = json.loads(text)
    except Exception as e:
        sys.stderr.write(f"[coeos-se] bundled settings unreadable: {e}\n")
        return
    with config_txn() as c:
        c["coeos"] = settings
    sys.stderr.write(f"[coeos-se] imported bundled settings: {settings.get('name')}\n")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _auto_import_settings()
    yield


app = FastAPI(title="CoeOS SE", version=__version__, lifespan=_lifespan)


# ── Optional bearer auth (COEOS_API_KEY) ─────────────────────────────────────
# Off by default (localhost/LAN tool). When set, /v1/* and /admin/* require
# `Authorization: Bearer <key>` or `x-api-key: <key>`.

@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    required = (os.environ.get("COEOS_API_KEY") or "").strip()
    path = request.url.path
    if required and (path.startswith("/v1/") or path.startswith("/admin/")):
        got = (request.headers.get("x-api-key") or "").strip()
        auth = (request.headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            got = got or auth[7:].strip()
        if got != required:
            return JSONResponse({"error": {"message": "invalid or missing API key",
                                           "code": 401}}, status_code=401)
    return await call_next(request)


# ── Découverte LAN (CodeOS / Companion) ──────────────────────────────────────
# Public (jamais gaté : middleware ne couvre que /v1/ et /admin/). vendor='odyssai.eu'
# pour que le scanner CodeOS matche. Pairing "pré-digéré" : la clé statique
# COEOS_API_KEY est pré-partagée (provisionnée), aucun handshake /pair.
@app.get("/.well-known/inference-engine.json")
async def well_known_inference_engine():
    gated = bool((os.environ.get("COEOS_API_KEY") or "").strip())
    return {
        "vendor": "odyssai.eu",
        "product": "coeos-se",
        "auth": {"required": gated, "scheme": "bearer", "scope": "/v1/*",
                 "public_routes": ["/health", "/.well-known/*", "/v1/models"]},
    }


# ── OpenAI surface ───────────────────────────────────────────────────────────

async def resolve_target(cfg: dict, model: str, headers, body: dict) -> tuple[str, str, dict]:
    """Shared model-id resolution for both wire surfaces (OpenAI + Anthropic):
    'coeos' → router decision; 'or:'/'comet:' → explicit provider; a registry
    logical → resolution option 1. Returns (pid, upstream, decision_headers)."""
    # 1. The virtual router id.
    if model.lower() == COEOS_MODEL_ID:
        d = await coeos_resolve(cfg, headers, body)
        return d["provider"], d["upstream"], {
            "x-coeos-axis": d["axis"], "x-coeos-model": d["logical"],
            "x-coeos-provider": d["provider"]}

    # 2. Explicit provider prefix: "or:z-ai/glm-5.2", "comet:glm-5.2".
    if ":" in model:
        prefix, upstream = model.split(":", 1)
        pid = PREFIX_TO_PROVIDER.get(prefix.lower())
        if pid and upstream.strip():
            if provider_key(cfg, pid) is None:
                raise HTTPException(status_code=503, detail={
                    "error": "provider_key_missing",
                    "message": f"{PROVIDERS[pid]['label']} key not set. Add it in "
                               f"the dashboard or via {PROVIDERS[pid]['api_key_env']}."})
            return pid, upstream.strip(), {"x-coeos-provider": pid}

    # 3. A logical model name from the registry (resolution option 1).
    resolved = resolve_logical(cfg, model)
    if resolved is not None:
        pid, upstream = resolved
        return pid, upstream, {"x-coeos-model": model, "x-coeos-provider": pid}

    raise HTTPException(status_code=404, detail={
        "error": "unknown_model",
        "message": f"unknown model {model!r}. Use 'coeos', a logical model from "
                   "the registry (GET /v1/models), or an explicit "
                   "'or:<id>' / 'comet:<id>'."})


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    # Raw dict on purpose: a strict schema would strip fields like
    # `reasoning_effort` or `extra_body`-style extras. Passthrough is the
    # contract — the upstream sees exactly what the client sent.
    try:
        body = await request.json()
        assert isinstance(body, dict)
    except Exception:
        raise HTTPException(400, "body must be a JSON object")
    cfg = load_config()
    model = str(body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "missing 'model'")
    pid, upstream, decision = await resolve_target(cfg, model, request.headers, body)
    return await proxy.proxy_chat(cfg, pid, upstream, body, decision_headers=decision)


# ── Anthropic surface (/v1/messages) ─────────────────────────────────────────
# Claude Code & Anthropic SDK clients point ANTHROPIC_BASE_URL here. Claude
# tier names map to the router (haiku pinned to the fast axis); the request is
# translated to the OpenAI shape, routed exactly like /v1/chat/completions,
# and the upstream reply is translated (or stream-transcoded) back.

@app.post("/v1/messages")
async def anthropic_messages(req: anthropic_api.AnthropicMessagesRequest,
                             request: Request):
    cfg = load_config()
    model, forced_axis = anthropic_api.resolve_tier(req.model)
    headers = {k.lower(): v for k, v in request.headers.items()}
    if forced_axis:
        headers["x-coeos-axis"] = forced_axis
    body = anthropic_api.to_openai_body(req, stream=bool(req.stream))
    pid, upstream, decision = await resolve_target(cfg, model, headers, body)
    msg_id = "msg_" + uuid.uuid4().hex[:24]
    label = decision.get("x-coeos-model") or upstream

    if not req.stream:
        status, payload = await proxy.unary_upstream_json(cfg, pid, upstream, body)
        if status >= 400 or not (payload.get("choices")):
            err = (payload.get("error") or {}) if isinstance(payload, dict) else {}
            return JSONResponse(
                {"type": "error",
                 "error": {"type": "api_error",
                           "message": str(err.get("message") or payload)[:300]}},
                status_code=status if status >= 400 else 502, headers=decision)
        return JSONResponse(
            anthropic_api.openai_to_anthropic_response(payload, msg_id, label),
            headers=decision)

    chunks = proxy.stream_upstream_chunks(cfg, pid, upstream, body)
    return StreamingResponse(
        anthropic_api.transcode_stream(chunks, msg_id, label),
        media_type="text/event-stream", headers=decision)


@app.post("/v1/messages/count_tokens")
async def anthropic_count_tokens(req: anthropic_api.AnthropicMessagesRequest):
    # Claude Code probes this to budget its context window — without it, it
    # refuses to talk to a custom ANTHROPIC_BASE_URL. Char-based estimate.
    return {"input_tokens": anthropic_api.estimate_input_tokens(req)}


@app.get("/v1/models")
async def v1_models():
    cfg = load_config()
    c = coeos_cfg(cfg)
    now = int(time.time())
    data: list[dict] = []
    axes = bound_axes(c)
    if c.get("enabled") and axes:
        data.append({
            "id": COEOS_DISPLAY_ID, "object": "model", "created": now,
            "owned_by": "coeos-se", "root": COEOS_DISPLAY_ID,
            "x_coeos": {
                "router": True,
                "settings": c.get("name"),
                "updated": c.get("updated"),
                "decider": (decider_spec(c) or {}).get("name"),
                "axes": {ax["key"]: ax["model"] for ax in axes},
            },
        })
    for logical, entry in sorted(registry_of(c).items()):
        entry = entry if isinstance(entry, dict) else {}
        resolved = resolve_logical(cfg, logical)
        data.append({
            "id": logical, "object": "model", "created": now,
            "owned_by": "coeos-se",
            "x_coeos": {
                "router": False,
                "name": entry.get("name") or logical,
                "ids": {f: (entry.get(f) or None)
                        for f in (PROVIDERS[p]["registry_field"] for p in PROVIDERS)},
                "resolvable": resolved is not None,
                "provider": resolved[0] if resolved else None,
            },
        })
    return {"object": "list", "data": data}


# ── Admin: CoeOS settings ────────────────────────────────────────────────────

class CoeosSettings(BaseModel):
    # The TMB Settings the operator imports. Everything is data: the taxonomy
    # (axes) AND the per-axis bindings AND the per-provider registry.
    enabled: Optional[bool] = None
    name: Optional[str] = None
    regime: Optional[str] = None
    updated: Optional[str] = None
    note: Optional[str] = None
    # The decider's own setting: {name, or, comet}. `decider_model` (a logical
    # name looked up in the registry) is the legacy form, still accepted.
    decider: Optional[dict] = None
    decider_model: Optional[str] = None
    default_axis: Optional[str] = None
    axes: Optional[list] = None
    models: Optional[dict] = None


def _validate_axes(axes: list) -> None:
    if not isinstance(axes, list):
        raise HTTPException(400, detail={"error": "bad_axes",
            "message": "axes must be a list of {key, label, model} objects."})
    seen = set()
    for ax in axes:
        if not isinstance(ax, dict) or not ax.get("key"):
            raise HTTPException(400, detail={"error": "bad_axis",
                "message": "each axis needs a non-empty 'key'."})
        k = str(ax["key"]).strip().lower()
        if k in seen:
            raise HTTPException(400, detail={"error": "dup_axis",
                "message": f"duplicate axis key: {k!r}."})
        seen.add(k)
        m = ax.get("model")
        if m and str(m).strip().lower() == COEOS_MODEL_ID:
            raise HTTPException(400, detail={"error": "reserved_id",
                "message": "'coeos' is the router's own id and can't be bound to an axis."})
        p = ax.get("provider")
        if p and p not in PROVIDERS:
            raise HTTPException(400, detail={"error": "bad_provider_pin",
                "message": f"axis {k!r}: provider must be one of {sorted(PROVIDERS)}."})


@app.get("/admin/coeos")
async def admin_coeos_get():
    return coeos_cfg(load_config())


@app.put("/admin/coeos")
async def admin_coeos_update(req: CoeosSettings):
    """Importing a TMB Settings file = a PUT with the file's JSON. Partial
    updates supported (only non-None fields are applied)."""
    if req.axes is not None:
        _validate_axes(req.axes)
    if req.models is not None and not isinstance(req.models, dict):
        raise HTTPException(400, detail={"error": "bad_registry",
            "message": "models must be an object: logical name -> {name, or, comet}."})
    if req.decider is not None:
        bad = [k for k, v in req.decider.items() if not isinstance(v, (str, type(None)))]
        if bad:
            raise HTTPException(400, detail={"error": "bad_decider",
                "message": "decider must be {name, or, comet} with string values."})
    with config_txn() as cfg:
        c = cfg.get("coeos") or {}
        for field in ("enabled", "name", "regime", "updated", "note",
                      "decider", "decider_model", "default_axis", "axes", "models"):
            val = getattr(req, field)
            if val is not None:
                c[field] = bool(val) if field == "enabled" else val
        cfg["coeos"] = c
    return coeos_cfg(load_config())


@app.get("/admin/coeos/decisions")
async def admin_coeos_decisions():
    """Routing decision counts (model x axis x provider) for visibility."""
    return {"decisions": [
        {"model": k[0], "axis": k[1], "provider": k[2], "count": v}
        for k, v in sorted(decisions.items(), key=lambda kv: -kv[1])]}


@app.delete("/admin/coeos/decisions")
async def admin_coeos_decisions_clear():
    decisions.clear()
    return {"ok": True, "decisions": []}


@app.get("/admin/coeos/export")
async def admin_coeos_export():
    c = coeos_cfg(load_config())
    return Response(
        content=json.dumps(c, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"content-disposition":
                 'attachment; filename="TMB-Settings-export.json"'})


# ── Admin: providers (keys only — that's the whole setup) ───────────────────

class ProviderUpdate(BaseModel):
    api_key: Optional[str] = None        # non-empty value stores it in the config
    clear_api_key: Optional[bool] = None  # true wipes the stored key
    enabled: Optional[bool] = None


@app.get("/admin/providers")
async def admin_providers_list():
    cfg = load_config()
    return {"data": [redact_provider(cfg, pid) for pid in PROVIDERS],
            "priority": provider_priority(cfg)}


@app.put("/admin/providers/{pid}")
async def admin_providers_update(pid: str, req: ProviderUpdate):
    if pid not in PROVIDERS:
        raise HTTPException(404, f"unknown provider {pid!r} (SE has: {sorted(PROVIDERS)})")
    with config_txn() as cfg:
        providers = cfg.setdefault("providers", {})
        cur = providers.setdefault(pid, {})
        if req.clear_api_key:
            cur.pop("api_key", None)
        elif req.api_key is not None and req.api_key.strip():
            cur["api_key"] = req.api_key.strip()
        if req.enabled is not None:
            cur["enabled"] = bool(req.enabled)
    return redact_provider(load_config(), pid)


class PriorityUpdate(BaseModel):
    priority: list[str]


@app.put("/admin/priority")
async def admin_priority_update(req: PriorityUpdate):
    bad = [p for p in req.priority if p not in PROVIDERS]
    if bad:
        raise HTTPException(400, f"unknown providers: {bad}")
    with config_txn() as cfg:
        cfg["provider_priority"] = req.priority
    return {"priority": provider_priority(load_config())}


@app.post("/admin/providers/{pid}/test")
async def admin_providers_test(pid: str):
    """Verify the provider is reachable by hitting its /v1/models."""
    if pid not in PROVIDERS:
        raise HTTPException(404, f"unknown provider {pid!r}")
    cfg = load_config()
    has_key = provider_key(cfg, pid) is not None
    models = await list_upstream_models(cfg, pid)
    if not models:
        return {"ok": False, "models_count": 0,
                "error": "API key not set" if not has_key else
                         "upstream unreachable or empty /models"}
    return {"ok": True, "models_count": len(models), "auth_used": has_key,
            "sample": [m.get("id") for m in models[:10]]}


@app.get("/admin/providers/{pid}/upstream-models")
async def admin_providers_upstream(pid: str):
    if pid not in PROVIDERS:
        raise HTTPException(404, f"unknown provider {pid!r}")
    return {"data": await list_upstream_models(load_config(), pid)}


# ── Misc ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    cfg = load_config()
    c = coeos_cfg(cfg)
    return {"ok": True, "version": __version__,
            "settings": c.get("name"), "updated": c.get("updated"),
            "axes_bound": len(bound_axes(c)),
            "providers_ready": ready_providers(cfg)}


@app.get("/")
async def index():
    return RedirectResponse("/dashboard")


@app.get("/dashboard")
async def dashboard():
    path = importlib.resources.files("coeos_se") / "dashboard" / "index.html"
    return FileResponse(str(path), media_type="text/html")


@app.get("/dashboard/images/{name}")
async def dashboard_image(name: str):
    # Static assets for the dashboard (logo). Filename-only, no path traversal.
    if "/" in name or ".." in name:
        raise HTTPException(404)
    path = importlib.resources.files("coeos_se") / "dashboard" / "images" / name
    if not path.is_file():
        raise HTTPException(404, "asset not found")
    return FileResponse(str(path))


@app.get("/endpoints")
async def endpoints_page():
    """Copy/paste connection settings for the common client apps."""
    path = importlib.resources.files("coeos_se") / "dashboard" / "endpoints.html"
    return FileResponse(str(path), media_type="text/html")
