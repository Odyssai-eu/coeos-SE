"""Single-file JSON config with an atomic read-modify-write transaction.

Ported from OdyssAI-X `_load_cluster_config` / `_cluster_config_txn`
(scripts/api.py:335-375), trimmed to CoeOS SE's needs. One file holds
everything: provider keys + the imported TMB Settings.

Shape:
    {
      "providers": {
        "openrouter": {"api_key": "...", "enabled": true},
        "comet":      {"api_key": "...", "enabled": true}
      },
      "provider_priority": ["openrouter", "comet"],
      "coeos": { ...TMB Settings (enabled, name, updated, decider_model,
                 default_axis, axes[], models{}) ... }
    }
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_CONFIG_TTL_S = 2.0

_lock = threading.RLock()
_cache: dict | None = None
_cache_ts: float = 0.0
_cache_path: str | None = None


def config_path() -> Path:
    """Resolved lazily so tests / docker can repoint via COEOS_CONFIG."""
    return Path(os.environ.get("COEOS_CONFIG", "coeos-config.json"))


def _read_from_disk() -> dict:
    p = config_path()
    try:
        if p.exists():
            data = json.loads(p.read_text())
            return data if isinstance(data, dict) else {}
    except Exception as e:
        sys.stderr.write(f"[coeos-se] failed to read {p}: {e}\n")
    return {}


def load_config() -> dict:
    """Parsed config, cached up to _CONFIG_TTL_S. Returns a deep copy so
    callers may mutate freely; mutations only persist via a txn / save."""
    global _cache, _cache_ts, _cache_path
    with _lock:
        now = time.monotonic()
        p = str(config_path())
        if _cache is not None and _cache_path == p and (now - _cache_ts) < _CONFIG_TTL_S:
            return copy.deepcopy(_cache)
        cfg = _read_from_disk()
        _cache, _cache_ts, _cache_path = cfg, now, p
        return copy.deepcopy(cfg)


def save_config(cfg: dict) -> None:
    """Persist atomically (tmp + os.replace) under the lock, refresh cache."""
    global _cache, _cache_ts, _cache_path
    with _lock:
        p = config_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
            os.replace(tmp, p)
            _cache, _cache_ts, _cache_path = copy.deepcopy(cfg), time.monotonic(), str(p)
        except Exception as e:
            sys.stderr.write(f"[coeos-se] failed to save {p}: {e}\n")


@contextmanager
def config_txn():
    """Atomic read-modify-write. Holds the lock across the whole cycle, reads
    a FRESH copy from disk (bypassing the TTL cache so we never write back a
    stale base), yields it for mutation, persists on clean exit.
    Must NOT `await` inside the block."""
    with _lock:
        cfg = _read_from_disk()
        yield cfg
        save_config(cfg)
