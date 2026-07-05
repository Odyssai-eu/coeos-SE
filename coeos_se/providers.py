"""The two hardcoded cloud providers — OpenRouter and Comet API.

This is CoeOS SE's deliberate limitation: cloud-only, two providers, keys
only. `api_base` and wire protocol (OpenAI) are NOT configurable — the user
pastes an OpenRouter and/or Comet key and that's the whole setup. The full
provider CRUD (custom endpoints, Anthropic protocol, local sidecars) lives in
OdyssAI-X.

Ported from OdyssAI-X scripts/api.py:4877-5017 (cloud providers layer),
reduced from a generic registry to two presets.
"""

from __future__ import annotations

import os

import httpx

# registry_field = the key each model registry entry uses for this provider's
# native model id, e.g. {"glm-5.2": {"or": "z-ai/glm-5.2", "comet": "glm-5.2"}}.
PROVIDERS: dict[str, dict] = {
    "openrouter": {
        "label": "OpenRouter",
        "api_base": "https://openrouter.ai/api/v1",
        "registry_field": "or",
        "api_key_env": "OPENROUTER_API_KEY",
        "keys_url": "https://openrouter.ai/settings/keys",
        "models_url": "https://openrouter.ai/models",
    },
    "comet": {
        "label": "Comet API",
        "api_base": "https://api.cometapi.com/v1",
        "registry_field": "comet",
        "api_key_env": "COMETAPI_KEY",
        "keys_url": "https://api.cometapi.com/console/token",
        "models_url": "https://api.cometapi.com/pricing",
    },
}

DEFAULT_PRIORITY = ["openrouter", "comet"]

# Aliases accepted in explicit model ids ("or:z-ai/glm-5.2", "comet:glm-5.2").
PREFIX_TO_PROVIDER = {
    "or": "openrouter",
    "openrouter": "openrouter",
    "comet": "comet",
}


def provider_cfg(cfg: dict, pid: str) -> dict:
    return (cfg.get("providers") or {}).get(pid) or {}


def provider_key(cfg: dict, pid: str) -> str | None:
    """Resolve the provider's API key. Priority: value stored in the config
    (set via dashboard) → the preset env var (OPENROUTER_API_KEY / COMETAPI_KEY).
    Same layering as OdyssAI-X `_cloud_provider_key` (api.py:4989)."""
    pc = provider_cfg(cfg, pid)
    direct = (pc.get("api_key") or "").strip()
    if direct:
        return direct
    env_var = PROVIDERS[pid]["api_key_env"]
    return (os.environ.get(env_var) or "").strip() or None


def provider_enabled(cfg: dict, pid: str) -> bool:
    """Enabled unless explicitly set to False (default True, like the source)."""
    return provider_cfg(cfg, pid).get("enabled", True) is not False


def provider_ready(cfg: dict, pid: str) -> bool:
    """A provider can serve traffic: known, enabled, and a key resolves."""
    return pid in PROVIDERS and provider_enabled(cfg, pid) and provider_key(cfg, pid) is not None


def ready_providers(cfg: dict) -> list[str]:
    return [pid for pid in PROVIDERS if provider_ready(cfg, pid)]


def provider_priority(cfg: dict) -> list[str]:
    """Global preference order (resolution option 1). Config may reorder;
    unknown ids are dropped, missing ones appended in default order."""
    raw = cfg.get("provider_priority")
    order = [p for p in raw if p in PROVIDERS] if isinstance(raw, list) else []
    order += [p for p in DEFAULT_PRIORITY if p not in order]
    return order


def redact_provider(cfg: dict, pid: str) -> dict:
    """Safe-to-return view. NEVER includes the api_key (api.py:5893)."""
    meta = PROVIDERS[pid]
    pc = provider_cfg(cfg, pid)
    stored = bool((pc.get("api_key") or "").strip())
    env_set = bool((os.environ.get(meta["api_key_env"]) or "").strip())
    return {
        "id": pid,
        "label": meta["label"],
        "api_base": meta["api_base"],
        "registry_field": meta["registry_field"],
        "api_key_env": meta["api_key_env"],
        "api_key_set": provider_key(cfg, pid) is not None,
        "api_key_source": "config" if stored else ("env" if env_set else "none"),
        "enabled": provider_enabled(cfg, pid),
        "ready": provider_ready(cfg, pid),
        "keys_url": meta["keys_url"],
    }


async def list_upstream_models(cfg: dict, pid: str) -> list[dict]:
    """Probe the provider's /v1/models so the dashboard can offer a picker and
    the user can verify registry ids. Ported from api.py:5781."""
    meta = PROVIDERS[pid]
    headers: dict[str, str] = {}
    key = provider_key(cfg, pid)
    if key:
        headers["authorization"] = f"Bearer {key}"
    url = f"{meta['api_base'].rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                d = r.json()
                return d.get("data", []) if isinstance(d, dict) else []
    except Exception:
        pass
    return []
