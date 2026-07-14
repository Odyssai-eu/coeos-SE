"""Unit tests: resolve_score_table (one-time import-time resolve, 2026-07-14).

Pure function on dicts — no config/registry fixtures needed, mirrors the
equivalent OdyssAI-X test (scripts/test_coeos_table.py) but adapted to SE's
lighter one-shot-at-import philosophy (vs. a live per-request resolver).
"""

from coeos_se.router import resolve_score_table

TABLE = {
    "axes": {
        "reasoning": {"label": "Reasoning", "description": "logic"},
        "calc": {"label": "Calc", "description": "math"},
    },
    "models": {
        "aion3 OR": {
            "role": "contender", "or_id": "aion-labs/aion-3.0",
            "cost_per_test": 0.05,
            "axes": {"reasoning": {"score": 90.0}, "calc": {"score": 80.0}},
        },
        "nemotron3 super OR": {
            "role": "contender", "or_id": "nvidia/nemotron-3-ultra-550b-a55b",
            "cost_per_test": 0.0,
            "axes": {"reasoning": {"score": 90.0}, "calc": {"score": 95.0}},
        },
        "Fusion-REF": {
            "role": "reference", "or_id": "anthropic/claude-opus-4.8",
            "cost_per_test": None,
            "axes": {"reasoning": {"score": 100.0}},
        },
        "unmapped-model": {
            "role": "contender", "or_id": "some/unmapped",
            "cost_per_test": 0.001,
            "axes": {"reasoning": {"score": 99.9}},
        },
    },
}


def _by_key(axes):
    return {a["key"]: a for a in axes}


def test_best_score_wins():
    # calc: nemotron3 (95.0) beats aion3 (80.0) — no tie here.
    registry = {"aion3 OR": {"name": "aion3", "or": "aion-labs/aion-3.0"},
                "nemotron3 super OR": {"name": "nemo", "or": "nvidia/x"}}
    out = _by_key(resolve_score_table(TABLE, registry))
    assert out["calc"]["model"] == "nemotron3 super OR"


def test_tie_on_score_cheaper_wins():
    # reasoning: aion3 (90.0, $0.05) ties nemotron3 (90.0, free) -> nemotron3.
    registry = {"aion3 OR": {"name": "aion3", "or": "aion-labs/aion-3.0"},
                "nemotron3 super OR": {"name": "nemo", "or": "nvidia/x"}}
    out = _by_key(resolve_score_table(TABLE, registry))
    assert out["reasoning"]["model"] == "nemotron3 super OR"


def test_reference_role_never_picked():
    # Fusion-REF scores 100.0 on reasoning — higher than any contender — but
    # must never be picked.
    registry = {"aion3 OR": {"name": "aion3", "or": "aion-labs/aion-3.0"},
                "Fusion-REF": {"name": "fusion", "or": "anthropic/claude-opus-4.8"}}
    out = _by_key(resolve_score_table(TABLE, registry))
    assert out["reasoning"]["model"] == "aion3 OR"


def test_model_absent_from_registry_ignored():
    # unmapped-model scores 99.9 on reasoning but has no registry entry.
    registry = {"aion3 OR": {"name": "aion3", "or": "aion-labs/aion-3.0"}}
    out = _by_key(resolve_score_table(TABLE, registry))
    assert out["reasoning"]["model"] == "aion3 OR"


def test_empty_registry_produces_unbound_axes():
    out = _by_key(resolve_score_table(TABLE, {}))
    assert out["reasoning"]["model"] == ""
    assert out["calc"]["model"] == ""


def test_output_carries_label_and_description():
    out = _by_key(resolve_score_table(TABLE, {}))
    assert out["reasoning"]["label"] == "Reasoning"
    assert out["calc"]["description"] == "math"


def test_every_table_axis_produces_an_entry():
    out = resolve_score_table(TABLE, {})
    assert {a["key"] for a in out} == {"reasoning", "calc"}
