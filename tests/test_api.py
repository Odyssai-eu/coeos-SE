"""API-level tests via TestClient: models list, admin, import, passthrough body."""

import json

import pytest
from fastapi.testclient import TestClient

from coeos_se.app import app
from tests.conftest import BASE_SETTINGS


@pytest.fixture()
def client(cfg_file):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def loaded_client(base_cfg):
    with TestClient(app) as c:
        yield c


def test_health_and_bundled_autoimport(client):
    # Startup on an empty config auto-imports the bundled TMB Settings.
    h = client.get("/health").json()
    assert h["ok"] is True
    assert h["settings"] and "TMB" in h["settings"]
    assert h["axes_bound"] > 0
    assert h["providers_ready"] == []  # no keys in test env


def test_v1_models_lists_coeos_and_logicals(loaded_client):
    d = loaded_client.get("/v1/models").json()
    ids = [m["id"] for m in d["data"]]
    assert "CoeOS" in ids
    assert "glm" in ids and "mm" in ids
    coeos = next(m for m in d["data"] if m["id"] == "CoeOS")
    assert coeos["x_coeos"]["axes"]["code"] == "glm"
    glm = next(m for m in d["data"] if m["id"] == "glm")
    assert glm["x_coeos"]["resolvable"] is True
    assert glm["x_coeos"]["or"] == "z-ai/glm"


def test_admin_providers_redacted(loaded_client):
    d = loaded_client.get("/admin/providers").json()
    assert "sk-or" not in json.dumps(d)
    assert [p["id"] for p in d["data"]] == ["openrouter"]  # single provider
    orp = d["data"][0]
    assert orp["api_key_set"] is True and orp["api_key_source"] == "config"


def test_put_key_and_test_flow(client):
    r = client.put("/admin/providers/openrouter", json={"api_key": "sk-x"})
    assert r.json()["api_key_set"] is True
    r = client.put("/admin/providers/openrouter", json={"clear_api_key": True})
    assert r.json()["api_key_set"] is False
    assert client.put("/admin/providers/comet", json={}).status_code == 404


def test_army(loaded_client):
    d = loaded_client.get("/admin/army").json()
    assert d["enabled"] is True
    assert set(d["army"]) == {"GLM", "MM", "Haiku"}  # registry display names


def test_import_settings_via_put(client):
    r = client.put("/admin/coeos", json=BASE_SETTINGS)
    assert r.status_code == 200
    got = client.get("/admin/coeos").json()
    assert got["name"] == "test settings"
    assert len(got["axes"]) == 3


def test_configs_save_load_delete_roundtrip(client):
    client.put("/admin/coeos", json=BASE_SETTINGS)
    assert client.get("/admin/coeos/configs").json()["configs"] == []

    r = client.post("/admin/coeos/configs/save", json={"name": "known-good"})
    assert r.status_code == 200
    assert client.get("/admin/coeos/configs").json()["configs"] == ["known-good"]

    # change the active config — different name, fewer axes.
    client.put("/admin/coeos", json={"name": "experiment", "axes": [
        {"key": "x", "model": "y"}]})
    assert client.get("/admin/coeos").json()["name"] == "experiment"

    # load = REPLACE wholesale, not a merge on top of "experiment".
    r = client.post("/admin/coeos/configs/load", json={"name": "known-good"})
    assert r.status_code == 200
    got = client.get("/admin/coeos").json()
    assert got["name"] == "test settings"
    assert len(got["axes"]) == 3

    r = client.delete("/admin/coeos/configs/known-good")
    assert r.status_code == 200
    assert client.get("/admin/coeos/configs").json()["configs"] == []


def test_configs_load_missing_404s(client):
    r = client.post("/admin/coeos/configs/load", json={"name": "nope"})
    assert r.status_code == 404


def test_configs_delete_missing_404s(client):
    assert client.delete("/admin/coeos/configs/nope").status_code == 404


def test_configs_save_sanitizes_name(client):
    client.put("/admin/coeos", json=BASE_SETTINGS)
    r = client.post("/admin/coeos/configs/save", json={"name": "a b/c!!"})
    assert r.status_code == 200
    assert r.json()["name"] == "a-b-c"
    assert "a-b-c" in client.get("/admin/coeos/configs").json()["configs"]


def test_import_raw_score_table_reproduces_sophies_bug_then_fixed(client):
    """PUT the score-table file UNWRAPPED (the natural 'drop this file in'
    gesture Sophie used 2026-07-14) — this used to 422 on `axes` (dict, not
    the settings shape's list). Now it's auto-detected and resolved once
    against the current registry into a normal axes=list."""
    # registry first (the "army" this operator can actually route to) — the
    # score table's own top-level `models` field is DATA, not a registry.
    client.put("/admin/coeos", json={"models": {
        "aion3 OR": {"name": "aion3", "or": "aion-labs/aion-3.0"},
        "nemotron3 super OR": {"name": "nemo", "or": "nvidia/x"},
    }})
    table = {
        "format": "tmb-score-table/1", "source": "TMB", "updated": "2026-07-14",
        "axes": {"reasoning": {"label": "Reasoning", "description": "logic"}},
        "models": {
            "aion3 OR": {"role": "contender", "cost_per_test": 0.05,
                        "axes": {"reasoning": {"score": 80.0}}},
            "nemotron3 super OR": {"role": "contender", "cost_per_test": 0.0,
                                   "axes": {"reasoning": {"score": 95.0}}},
        },
    }
    r = client.put("/admin/coeos", json=table)
    assert r.status_code == 200, r.text
    got = client.get("/admin/coeos").json()
    assert got["axes"] == [{"key": "reasoning", "label": "Reasoning",
                            "description": "logic", "model": "nemotron3 super OR"}]
    # provenance kept, router still reads plain axes (unchanged behaviour)
    assert got["score_table"]["format"] == "tmb-score-table/1"


def test_import_wrapped_score_table_still_resolves(client):
    """{"score_table": {...}} (the original wrapping convention) ALSO
    triggers the one-time resolve — not just gets stored inert (a real bug
    caught here: checking only the top-level `format` key would silently
    skip resolution for this wrapped form)."""
    client.put("/admin/coeos", json={"models": {
        "aion3 OR": {"name": "aion3", "or": "aion-labs/aion-3.0"}}})
    table = {"format": "tmb-score-table/1", "axes": {"calc": {"label": "Calc"}},
             "models": {"aion3 OR": {"role": "contender", "cost_per_test": 0.01,
                                     "axes": {"calc": {"score": 70.0}}}}}
    r = client.put("/admin/coeos", json={"score_table": table})
    assert r.status_code == 200, r.text
    got = client.get("/admin/coeos").json()
    assert got["axes"] == [{"key": "calc", "label": "Calc", "description": "",
                            "model": "aion3 OR"}]


def test_import_rejects_reserved_and_dup(client):
    bad = {"axes": [{"key": "a", "model": "coeos"}]}
    assert client.put("/admin/coeos", json=bad).status_code == 400
    dup = {"axes": [{"key": "a", "model": "x"}, {"key": "a", "model": "y"}]}
    assert client.put("/admin/coeos", json=dup).status_code == 400
    badpin = {"axes": [{"key": "a", "model": "x", "provider": "azure"}]}
    assert client.put("/admin/coeos", json=badpin).status_code == 400


def test_settings_update_offer_and_apply(loaded_client, monkeypatch):
    # Remote settings newer than local (2026-07-01) → update offered, then applied.
    from coeos_se import updates

    async def fake_fetch():
        return {"name": "remote settings", "updated": "2026-09-01",
                "enabled": True, "axes": [{"key": "code", "model": "glm"}],
                "models": {"glm": {"name": "GLM", "or": "z-ai/glm"}}}
    monkeypatch.setattr(updates, "_fetch_remote", fake_fetch)

    st = loaded_client.get("/admin/settings-update?check=true").json()
    assert st["available"] is True and st["remote_updated"] == "2026-09-01"

    ap = loaded_client.post("/admin/settings-update/apply").json()
    assert ap["ok"] is True and ap["updated"] == "2026-09-01"
    assert loaded_client.get("/admin/coeos").json()["name"] == "remote settings"
    # provider key survived the update
    assert loaded_client.get("/admin/providers").json()["data"][0]["api_key_set"] is True


def test_settings_update_up_to_date(loaded_client, monkeypatch):
    from coeos_se import updates

    async def fake_fetch():
        return {"name": "same", "updated": "2026-07-01"}  # == local
    monkeypatch.setattr(updates, "_fetch_remote", fake_fetch)
    st = loaded_client.get("/admin/settings-update?check=true").json()
    assert st["available"] is False


def test_chat_unknown_model_404(client):
    # No provider is ready (no keys in the test env), so the legacy rule
    # (unknown id = upstream on the first ready provider) can't apply → 404.
    r = client.post("/v1/chat/completions",
                    json={"model": "nope-123", "messages": []})
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_model"


def test_chat_prefix_without_key_503(client):
    r = client.post("/v1/chat/completions",
                    json={"model": "or:z-ai/glm", "messages": []})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "provider_key_missing"


def test_chat_coeos_no_axes_503(client, write_cfg):
    write_cfg({"coeos": {"enabled": True, "axes": []}})
    r = client.post("/v1/chat/completions",
                    json={"model": "coeos", "messages": []})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "coeos_no_axes"


def test_auth_middleware(loaded_client, monkeypatch):
    monkeypatch.setenv("COEOS_API_KEY", "secret")
    assert loaded_client.get("/v1/models").status_code == 401
    ok = loaded_client.get("/v1/models", headers={"x-api-key": "secret"})
    assert ok.status_code == 200
    ok2 = loaded_client.get("/v1/models", headers={"authorization": "Bearer secret"})
    assert ok2.status_code == 200
    # /health and /dashboard stay open
    assert loaded_client.get("/health").status_code == 200
