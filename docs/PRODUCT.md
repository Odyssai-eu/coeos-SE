# CoeOS SE

**One endpoint. Every request routed to the model proven best at that skill.**

CoeOS SE is a benchmark-composed LLM router. You call a single virtual model —
`coeos` — and each request is classified into a *skill axis* (debugging,
long-form writing, planning, GDPR analysis, React, Swift…) and relayed to the
model that benchmarks proved best on that axis. One OpenAI-compatible endpoint,
also speaking the Anthropic protocol, in front of the open-source frontier.

Free. MIT. `docker compose up`, paste a key, point your tools at it.

---

## The problem

Every frontier model has weak spots. The one that writes the best prose isn't the
one that debugs C best; the one that plans a migration isn't the one that nails a
GDPR memo. Pick one model and you inherit all its blind spots. Wire up several by
hand and you're maintaining a switchboard and guessing which is best where.

CoeOS SE makes that decision for you, per request, from data — not vibes.

---

## How it works

```
your client  (model: "coeos")
      │
      ▼   classify → skill axis        explicit header, or a fast decider model
  CoeOS SE
      │
      ▼   axis → model → provider      the TMB Settings: data, not code
OpenRouter / Comet API
      │
      ▼   the proven-best model answers — streamed back, untouched
```

1. **Classify.** An agent that knows its phase sends `x-coeos-axis: plan_spec`
   and skips classification. Otherwise a small, fast model reads the request and
   picks the single best-matching axis.
2. **Resolve.** The axis binds a *logical* model (e.g. `glm-5.2`); a per-provider
   registry maps it to the right native id on OpenRouter or Comet.
3. **Relay.** The request is proxied verbatim — streaming, tools,
   `reasoning_effort`, thinking budgets all pass through. The answer comes back in
   your client's wire format.

**Not a fusion.** Approaches that fuse several models per query make many calls
and cost accordingly. CoeOS is single-shot: one classification, one call, to the
proven-best model per skill. Cheaper, faster, and grounded in empirical
benchmark scores rather than an ensemble algorithm.

---

## The TMB Settings — routing as data

The intelligence isn't in the code. It's in a JSON table — the **TMB Settings** —
that says which model wins which skill, with its benchmark provenance:

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

The taxonomy and the bindings are edited, imported, and exported from the
dashboard — nothing is hard-coded. Add an axis = add a JSON entry. Five axes or
fifty, same engine.

The bundled table ships **15 skill axes** across writing, law, reasoning, code
(Python, React, Swift, debug, refactoring, general), and planning
(decomposition, spec, judgment) — bound to **open-source models only** (GLM 5.2,
MiniMax-M3, Kimi K2.7, Hy3, DeepSeek V4 Flash). It's a dated, frozen snapshot: it
proves the value today. Models move monthly; the table is regenerated from
benchmark scoreboards on a regular cadence.

---

## Works with your tools — both wire protocols

CoeOS SE speaks **OpenAI** (`/v1/chat/completions`) and **Anthropic**
(`/v1/messages`, streaming included).

- **Claude Code** — point `ANTHROPIC_BASE_URL` at it. Claude tier names route
  automatically (opus/sonnet → `coeos`, haiku → the fast axis).
- **Codex, Cline, Continue, OpenCode, Aider, Hermes** — set the base URL to
  `<host>/v1`, use model `coeos`.
- **Any OpenAI or Anthropic SDK** — drop-in.

A built-in `/endpoints` page gives copy/paste setup for every client.

---

## What makes it trustworthy

- **It never silently routes to a different model.** If the proven-best model for
  an axis can't be served (no key, not in the registry), you get a clear `503` —
  never a quiet downgrade.
- **Every decision is observable.** Responses carry `x-coeos-axis`,
  `x-coeos-model` and `x-coeos-provider`. The dashboard shows a live decision
  counter.
- **You can bypass the router** any time: call a logical model directly
  (`model: "glm-5.2"`) or an explicit provider id (`or:z-ai/glm-5.2`,
  `comet:glm-5.2`).

---

## Setup is two things: a key and a URL

Two providers, keys only — that's the entire configuration. Paste an
[OpenRouter](https://openrouter.ai/settings/keys) and/or
[Comet API](https://api.cometapi.com/) key in the dashboard. With both keys, a
global priority decides which serves each model, and each provider covers the
other's gaps automatically.

```bash
docker compose up -d          # or: pip install . && coeos-se
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:4600/v1", api_key="unused")

r = client.chat.completions.create(
    model="coeos",
    messages=[{"role": "user", "content": "Find the bug in this stack trace: …"}],
)
# → classified as `debug`, served by the model that holds the panel record on it.
```

The dashboard (`/dashboard`) manages keys, the routing table, the decider, and
the decision log. Optional bearer auth locks `/v1/*` and `/admin/*`.

---

## Why cloud-only — and what the full CoeOS is

CoeOS SE is the **Simple Edition**: two cloud providers, keys only, by design.
That's the limit of the free product, and it's deliberate.

The full CoeOS runs inside **[OdyssAI-X](https://odyssai.eu)** — the same
benchmark-composed routing over **your own hardware**: local model pools,
distributed clusters, cloud fallback, and a *sovereign* regime where no request
ever leaves your network. SE is a taste of that, in one `docker compose up`.

---

## At a glance

| | |
|---|---|
| **What** | Benchmark-composed LLM router, one virtual model `coeos` |
| **Interfaces** | OpenAI `/v1/chat/completions` + Anthropic `/v1/messages` |
| **Providers** | OpenRouter, Comet API — keys only, cloud-only |
| **Routing** | 15 skill axes, per-axis proven-best model, data-driven |
| **Decider** | Fast model classifies each request; explicit header overrides |
| **Guarantees** | Never silently reroutes; every decision observable |
| **Install** | Docker or `pip`/`uvx`; self-contained dashboard |
| **License** | MIT — engine and settings, 100% free |
| **Models** | Open-source only (GLM, MiniMax, Kimi, Hy3, DeepSeek) |

---

## FAQ

**Is it really free?** Yes — engine and the bundled settings, MIT. There's no paid
tier of CoeOS SE. The paid product is OdyssAI-X, which runs this over your own
sovereign hardware.

**Do I need both provider keys?** No — one is enough. With both, they cover each
other's gaps and you pick the priority.

**Can I use my own routing table?** Yes. Import/export the TMB Settings JSON from
the dashboard. The bundled one is a starting point.

**Does it support tool calling / streaming / thinking?** Yes — the proxy relays
the request verbatim, so whatever your client and the upstream support works.

**What models does it route to?** Whatever your TMB Settings bind. The bundled
table is open-source models only; you can point axes at any model your providers
serve.

**Is the router smart or is it the data?** The data. The router is a thin,
transparent classifier + proxy; the value is the empirical table of which model
wins which skill.

---

*CoeOS SE is open source (MIT) — [github.com/Odyssai-eu/coeos-SE](https://github.com/Odyssai-eu/coeos-SE).
Built by the team behind [OdyssAI-X](https://odyssai.eu).*
