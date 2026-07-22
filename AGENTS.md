# AGENTS.md — for the agent installing or operating CoeOS SE

You are an AI coding agent asked to install, verify, or operate CoeOS SE on
this machine. Follow the steps in order; verify each before the next; never
claim success without its check passing.

## What you are installing

CoeOS SE is a **single server** exposing one OpenAI-compatible endpoint
(`model: "coeos"`) plus an Anthropic-compatible surface. Each request is
classified onto a skill axis and relayed to the model the TMB benchmarks
proved best on that axis, via **OpenRouter**. One process, port **4600**,
config in one JSON file. Nothing else to deploy.

Unlike full CoeOS, SE needs **no local engine**: OpenRouter is the fleet.
The only external requirement is an OpenRouter API key (the user's).

## Install — two equivalent routes

### Docker (preferred)

```sh
git clone https://github.com/Odyssai-eu/coeos-SE.git && cd coeos-SE
docker compose up -d
```

Serves on `:4600`. Config persists in `./data/coeos-config.json` (volume).
Optional env (see `.env.example`, read by compose): `OPENROUTER_API_KEY`
(else pasted in the dashboard), `COEOS_API_KEY` (bearer auth on `/v1/*` and
`/admin/*` when set).

### pip (no docker)

```sh
git clone https://github.com/Odyssai-eu/coeos-SE.git && cd coeos-SE
python3 -m venv .venv && .venv/bin/pip install .
.venv/bin/coeos-se                    # port 4600; COEOS_PORT to change
```

Requires Python **>= 3.10** (`requires-python` in pyproject). On stock macOS
`python3` is 3.9 — use `python3.12 -m venv .venv` (or any 3.10+). Config
lands in `./coeos-config.json` (or `COEOS_CONFIG`).

## First boot — what happens without any key

The **bundled settings auto-import on first boot**: the routing table ships
in the package (`coeos_se/settings/TMB-Settings-SE.json`) — the server is
fully configured for routing before any key exists. Without an OpenRouter
key it boots, serves the dashboard, and answers `/health`; actual completions
fail until a key is set. This is normal, not a broken install.

Set the key either way:
- dashboard `http://localhost:4600/dashboard` → paste the OpenRouter key;
- or `OPENROUTER_API_KEY` env at launch.

## Verify the install (in this order)

```sh
# 1. server up, settings bound (expect ok:true, axes_bound >= 15)
curl -sf http://localhost:4600/health | python3 -m json.tool

# 2. dashboard serves
curl -sf http://localhost:4600/dashboard | grep -q "Update from GitHub" && echo dashboard-ok

# 3. models surface (CoeOS + logical models)
curl -sf http://localhost:4600/v1/models | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data']),'models')"

# 4. end-to-end completion — ONLY once an OpenRouter key is set
curl -sf http://localhost:4600/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"coeos","messages":[{"role":"user","content":"reply with exactly: ok"}]}' \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['choices'][0]['message']['content'][:40])"
```

Steps 1–3 prove the install. Step 4 needs the user's key — if you don't have
one, say explicitly that routing is untested, do not fake it. Every routed
response carries `x-coeos-axis` / `x-coeos-model` headers — read them to
prove the router (not a passthrough) answered.

## Settings updates — GitHub only

The routing table ships exclusively from this repo
(`coeos_se/settings/TMB-Settings-SE.json` on `main`). There is **no manual
import**: the dashboard's **Update from GitHub** button (or
`POST /admin/settings-update/apply`) downloads and applies; a daily poll
offers updates but **never applies silently**. Check freshness:

```sh
curl -sf "http://localhost:4600/admin/settings-update?check=true" | python3 -m json.tool
```

`available: true` → a newer table exists; apply only if the user wants it.

## Failure modes you will actually meet

| Symptom | Cause | Fix |
|---|---|---|
| pip resolution fails on fastapi/pydantic | Python 3.9 venv | recreate the venv with python3.10+ |
| port 4600 already bound | another instance | `COEOS_PORT=4601` (pip) or edit the compose port mapping |
| `/health` ok but completions 401/402 | missing/invalid OpenRouter key | set the key in the dashboard; check credit on openrouter.ai |
| completions 503 `coeos_model_not_loaded` | axis binds a model your key can't reach | intentional — SE never silently substitutes a model; check the key/provider |
| `check for updates` says up to date unexpectedly | stale poll cache on an old build (< 0.3.0) | update the code; the current dashboard always forces a fresh check |
| 401 on `/admin/*` | `COEOS_API_KEY` is set | send `Authorization: Bearer <key>` |

## Rules for you, the installing agent

- Never commit `coeos-config.json` or `./data/` — they hold the user's keys.
- Never paste or log an API key in clear text; the dashboard flow is the
  intended path (stored server-side in the config file).
- Do not edit `coeos_se/settings/TMB-Settings-SE.json` locally to change
  routing — settings are GitHub-canonical; local edits are overwritten by
  the next update and break provenance.
- A 503 on an unreachable best-model is a **feature** (no silent rerouting).
  Do not "fix" it by patching the router.
