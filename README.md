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
   ▼ axis → model → provider      (the TMB Settings: data, not code)
OpenRouter / Comet API
```

## Quickstart

```bash
docker compose up -d
# or: pip install . && coeos-se
```

Open `http://localhost:4600/dashboard`, paste your
[OpenRouter](https://openrouter.ai/settings/keys) and/or
[Comet API](https://api.cometapi.com/) key. That's the whole setup — the
bundled routing table (TMB Settings) is imported on first boot.

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

The chosen axis binds a **logical model** (e.g. `glm-5.2`), and a per-provider
registry maps it to each provider's native id
(`z-ai/glm-5.2` on OpenRouter, `glm-5.2` on Comet). Resolution follows your
provider priority, falls back to the other provider when an id or key is
missing, and honours per-axis pins. If nothing can serve the proven-best model,
you get a clear 503 — **CoeOS never silently routes to a different model**.

Every response carries `x-coeos-axis`, `x-coeos-model` and `x-coeos-provider`
headers so you can observe each decision; counters are on the dashboard and
`GET /admin/coeos/decisions`.

You can also bypass the router: call a logical model directly
(`model: "glm-5.2"`) or an explicit provider id
(`model: "or:z-ai/glm-5.2"`, `model: "comet:glm-5.2"`).

## The TMB Settings — routing as data

The taxonomy and bindings live entirely in a JSON file you can import/export
from the dashboard — nothing is hard-coded. Each axis carries its benchmark
provenance:

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

The bundled snapshot is **dated and frozen** (`v0.1`, 15 axes). Models move
monthly; the table is regenerated from our benchmark scoreboards roughly once a
month — sooner when a notable new model lands. Watch the repo for updates.

Adding an axis = adding a JSON entry. Five axes or fifty, same engine.

## API surface

| Endpoint | Purpose |
|---|---|
| `POST /v1/chat/completions` | OpenAI surface (`coeos`, logical, or `or:`/`comet:` ids) |
| `GET /v1/models` | `CoeOS` + the registry's logical models |
| `GET/PUT /admin/coeos` | read / import the TMB Settings |
| `GET /admin/coeos/decisions` | routing counters |
| `GET /admin/coeos/export` | settings download |
| `GET/PUT /admin/providers*` | keys, enable/disable, reachability test |
| `PUT /admin/priority` | provider preference order |
| `GET /health` | status |

Set `COEOS_API_KEY` to require a bearer key on `/v1/*` and `/admin/*`.

## Configuration

One file (`coeos-config.json`, or `COEOS_CONFIG`): provider keys + the imported
settings. Keys can also come from `OPENROUTER_API_KEY` / `COMETAPI_KEY` env
vars. See [.env.example](.env.example).

## Why cloud-only? (and what the full CoeOS is)

CoeOS SE is the **Simple Edition**: two cloud providers, keys only, by design.
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
