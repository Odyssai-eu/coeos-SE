# CoeOS SE

**One OpenAI-compatible endpoint. Every request routed to the model proven best
at that skill.**

CoeOS SE is a benchmark-composed LLM router. Instead of picking one frontier
model and living with its weak spots, you call a single virtual model —
`coeos` — and each request is classified into a **skill axis** (debugging,
long-form writing, planning, GDPR analysis, …) and relayed to the model that
**benchmarks proved best on that axis**. Streaming, tools, `reasoning_effort`,
thinking budgets — everything passes through untouched.

Not a fusion. Not an ensemble. One classification, one call, to the
proven-best model per skill — cheaper and faster than multi-model fusion
approaches, and grounded in empirical scores rather than an algorithm.

```
client (model:"coeos")
   │
   ▼ classify → skill axis        (explicit header, or a fast decider LLM)
CoeOS SE
   │
   ▼ axis → model                 (the TMB Settings: data, not code)
OpenRouter
```

## Quickstart

```bash
uvx coeos-se            # zero install — runs straight from PyPI (uv picks the right Python)
# or, from a clone:
docker compose up -d
# or: pip install coeos-se && coeos-se
```

Open `http://localhost:4600/dashboard`, paste your
[OpenRouter](https://openrouter.ai/settings/keys) key. That's the whole setup —
the bundled routing table (TMB Settings) is imported on first boot, and CoeOS SE
polls GitHub daily and offers you the newer table when it lands.

Then point any OpenAI client at it:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:4600/v1", api_key="unused")
r = client.chat.completions.create(
    model="coeos",
    messages=[{"role": "user", "content": "Find the bug in this stack trace: …"}],
)
# → classified as `debug`, served by the model that holds the panel record on it.
```

Works as-is with agents (Cline, Aider, and anything OpenAI-compatible): set the
base URL, use model `coeos`.

## How routing works

1. **Explicit axis** — an agent that knows what phase it's in sends
   `x-coeos-axis: plan_spec` and skips classification entirely.
2. **Decider LLM** — otherwise a small fast model reads the request and picks
   the single best-matching axis from the configured menu.
3. **Default axis** — if classification is ambiguous or the decider is
   unavailable, the configured default (e.g. `code_general`) applies.

The chosen axis binds a **logical model** (e.g. `glm-5.2`), and the registry
maps it to its native OpenRouter id (`z-ai/glm-5.2`). If the proven-best model
can't be served (no key), you get a clear 503 — **CoeOS never silently routes
to a different model**.

Every response carries `x-coeos-axis`, `x-coeos-model` and `x-coeos-provider`
headers so you can observe each decision; counters are on the dashboard and
`GET /admin/coeos/decisions`.

You can also bypass the router: call a logical model directly
(`model: "glm-5.2"`) or an explicit OpenRouter id (`model: "or:z-ai/glm-5.2"`).

## The TMB Settings — routing as data

The taxonomy and bindings live entirely in a JSON file — nothing is
hard-coded. Each axis carries its benchmark provenance:

```json
{
  "key": "debug",
  "label": "Debugging",
  "model": "glm-5.2",
  "description": "Find and fix a bug in provided code, stack-trace analysis.",
  "bench": "C02 49/50 (panel record)",
  "verified": true
}
```

Settings ship **exclusively from this repo** (the canonical
`coeos_se/settings/TMB-Settings-SE.json` on `main`) — no manual import. The
dashboard shows the running snapshot and offers **Update from GitHub** when a
newer one lands; CoeOS SE also polls daily and surfaces the offer (never
applies silently).

The current snapshot binds **18 skill axes**, scored by the TMB benchmark
panel (30+ models, 5 bench suites) under a hardened judging protocol: the
judge scores each grading criterion in isolation (**notes-only**) and the
harness computes every total **mechanically** — no LLM ever writes a verdict
or adds numbers. Models move monthly; the table is regenerated when the panel
does. Watch the repo for updates.

Adding an axis = adding a JSON entry. Five axes or fifty, same engine.

## Works with your tools — both wire protocols

CoeOS SE speaks **OpenAI** (`/v1/chat/completions`) and **Anthropic**
(`/v1/messages`, streaming included). Point Claude Code at it with
`ANTHROPIC_BASE_URL` — claude tier names route automatically (opus/sonnet →
`coeos`, haiku → the fast axis). Codex, Cline, Continue, OpenCode, Aider and
any OpenAI SDK use `<base>/v1` + model `coeos`.

Open **`/endpoints`** on a running server for copy/paste setup snippets per
client.

## API surface

| Endpoint | Purpose |
|---|---|
| `POST /v1/chat/completions` | OpenAI surface (`coeos`, logical, or `or:<id>`) |
| `POST /v1/messages` (+ `/count_tokens`) | Anthropic surface (Claude Code, Anthropic SDKs) |
| `GET /endpoints` | copy/paste client setup snippets |
| `GET /v1/models` | `CoeOS` + the registry's logical models |
| `GET /admin/army` | the model roster (display names) |
| `GET /admin/settings-update` (+ `/apply`) | check / apply the latest TMB Settings from GitHub |
| `GET /admin/coeos/decisions` | routing counters |
| `PUT /admin/providers/openrouter` | set / clear the key |
| `GET /health` | status |

Set `COEOS_API_KEY` to require a bearer key on `/v1/*` and `/admin/*`.

## Configuration

One file (`coeos-config.json`, or `COEOS_CONFIG`): the OpenRouter key + the
imported settings. The key can also come from the `OPENROUTER_API_KEY` env var.
See [.env.example](.env.example). CoeOS SE polls `COEOS_SETTINGS_URL` (the TMB
Settings on GitHub) every `COEOS_UPDATE_INTERVAL` seconds (default 24 h).

## Why cloud-only? (and what the full CoeOS is)

CoeOS SE is the **Simple Edition**: OpenRouter, keys only, by design.
The full CoeOS runs inside [OdyssAI-X](https://odyssai.eu) — the same
benchmark-composed routing over **your own hardware**: local MLX model pools,
distributed clusters, cloud fallback, and the sovereign local regime where no
request leaves your network. SE is a taste of that, in one `docker compose up`.

## Development

```bash
pip install -e ".[dev]"
pytest
coeos-se --port 4600
```

MIT — see [LICENSE](LICENSE).
