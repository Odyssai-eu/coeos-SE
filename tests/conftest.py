import json

import pytest

from coeos_se import config as config_mod


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """Point COEOS_CONFIG at a fresh temp file and neutralise ambient env keys
    so tests control provider readiness exactly."""
    p = tmp_path / "coeos-config.json"
    monkeypatch.setenv("COEOS_CONFIG", str(p))
    monkeypatch.setenv("COEOS_NO_POLL", "1")  # no background GitHub call in tests
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("COEOS_API_KEY", raising=False)
    return p


@pytest.fixture()
def write_cfg(cfg_file):
    def _write(cfg: dict) -> None:
        cfg_file.write_text(json.dumps(cfg))
        # Tests write the file out-of-band; a real out-of-band edit would be
        # picked up within the 2s TTL, tests need it immediately.
        config_mod._cache = None
    return _write


BASE_SETTINGS = {
    "enabled": True,
    "name": "test settings",
    "updated": "2026-07-01",
    "decider": {"name": "Haiku", "or": "anthropic/haiku"},
    "default_axis": "code",
    "axes": [
        {"key": "code", "label": "Code", "model": "glm", "description": "coding"},
        {"key": "plan", "label": "Plan", "model": "mm", "description": "planning"},
        {"key": "swift", "label": "Swift", "model": "", "description": "unbound gap"},
    ],
    "models": {
        "glm":   {"name": "GLM", "or": "z-ai/glm"},
        "mm":    {"name": "MM", "or": "minimax/mm"},
        "haiku": {"name": "Haiku", "or": "anthropic/haiku"},
    },
}


@pytest.fixture()
def base_cfg(write_cfg):
    """Config with the OpenRouter key set and the base settings imported."""
    cfg = {
        "providers": {"openrouter": {"api_key": "sk-or"}},
        "coeos": json.loads(json.dumps(BASE_SETTINGS)),
    }
    write_cfg(cfg)
    return cfg
