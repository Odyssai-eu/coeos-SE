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


def test_export_download(loaded_client):
    r = loaded_client.get("/admin/coeos/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert json.loads(r.text)["name"] == "test settings"
