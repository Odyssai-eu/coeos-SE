"""CoeOS SE (Simple Edition) — benchmark-composed LLM router, OpenAI-compatible.

Extracted from OdyssAI-X's CoeOS (RFC #63), cloud-only: two providers
(OpenRouter, Comet API), a data-driven skill-axis taxonomy (the "TMB Settings"
file), and a virtual model id `coeos` that classifies each request and relays
it to the model proven best on that axis.
"""

__version__ = "0.2.0"
