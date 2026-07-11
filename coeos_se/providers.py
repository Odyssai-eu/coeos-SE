"""The single cloud provider — OpenRouter.

CoeOS SE routes through OpenRouter only. Comet API was dropped: its behaviour
was too unreliable to recommend in a router. One provider means no priority, no
fallback table — the user pastes an OpenRouter key and that's the whole setup.
The full multi-provider machinery lives in OdyssAI-X.
"""

from __future__ import annotations

import os

import httpx

PROVIDER_ID = "openrouter"

# registry_field = the key each model registry entry uses for its native id,
# e.g. {"glm-5.2": {"or": "z-ai/glm-5.2"}}.
PROVIDERS: dict[str, dict] = {
    "openrouter": {
        "label": "OpenRouter",
        "api_base": "https://openrouter.ai/api/v1",
        "registry_field": "or",
        "api_key_env": "OPENROUTER_API_KEY",
        "keys_url": "https://openrouter.ai/settings/keys",
        "models_url": "https://openrouter.ai/models",
    },
}

# Prefixes accepted in explicit model ids ("or:z-ai/glm-5.2").
PREFIX_TO_PROVIDER = {"or": "openrouter", "openrouter": "openrouter"}


def provider_cfg(cfg: dict, pid: str = PROVIDER_ID) -> dict:
    return (cfg.get("providers") or {}).get(pid) or {}


def provider_key(cfg: dict, pid: str = PROVIDER_ID) -> str | None:
    """Resolve the OpenRouter key: value stored in the config (set via the
    dashboard) → the OPENROUTER_API_KEY env var."""
    pc = provider_cfg(cfg, pid)
    direct = (pc.get("api_key") or "").strip()
    if direct:
        return direct
    env_var = PROVIDERS[pid]["api_key_env"]
    return (os.environ.get(env_var) or "").strip() or None


def provider_enabled(cfg: dict, pid: str = PROVIDER_ID) -> bool:
    """Enabled unless explicitly set to False (default True)."""
    return provider_cfg(cfg, pid).get("enabled", True) is not False


def provider_ready(cfg: dict, pid: str = PROVIDER_ID) -> bool:
    """OpenRouter can serve traffic: known, enabled, and a key resolves."""
    return pid in PROVIDERS and provider_enabled(cfg, pid) and provider_key(cfg, pid) is not None


def ready_providers(cfg: dict) -> list[str]:
    return [pid for pid in PROVIDERS if provider_ready(cfg, pid)]


def redact_provider(cfg: dict, pid: str = PROVIDER_ID) -> dict:
    """Safe-to-return view. NEVER includes the api_key."""
    meta = PROVIDERS[pid]
    pc = provider_cfg(cfg, pid)
    stored = bool((pc.get("api_key") or "").strip())
    env_set = bool((os.environ.get(meta["api_key_env"]) or "").strip())
    return {
        "id": pid,
        "label": meta["label"],
        "api_base": meta["api_base"],
        "api_key_env": meta["api_key_env"],
        "api_key_set": provider_key(cfg, pid) is not None,
        "api_key_source": "config" if stored else ("env" if env_set else "none"),
        "enabled": provider_enabled(cfg, pid),
        "ready": provider_ready(cfg, pid),
        "keys_url": meta["keys_url"],
    }


async def list_upstream_models(cfg: dict, pid: str = PROVIDER_ID) -> list[dict]:
    """Probe OpenRouter's /models (used by the reachability test)."""
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
