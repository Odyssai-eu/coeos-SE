"""Body-preparation contract: verbatim passthrough of client extras."""

from coeos_se.proxy import _prepare_body


def test_reasoning_effort_and_extras_pass_through():
    body = {"model": "coeos", "messages": [], "temperature": 1.0,
            "max_tokens": 65536, "reasoning_effort": "max",
            "thinking": {"type": "enabled"}, "custom_field": {"a": 1}}
    fwd = _prepare_body(body, "glm-5.2")
    assert fwd["model"] == "glm-5.2"
    assert fwd["reasoning_effort"] == "max"
    assert fwd["thinking"] == {"type": "enabled"}   # structured → untouched
    assert fwd["custom_field"] == {"a": 1}
    assert fwd["max_tokens"] == 65536
    assert "session_id" not in fwd


def test_enable_thinking_translates_to_thinking():
    fwd = _prepare_body({"enable_thinking": True}, "some/model")
    assert fwd["thinking"] is True
    assert "enable_thinking" not in fwd


def test_minimax_gets_object_thinking():
    fwd = _prepare_body({"thinking": False}, "minimax/minimax-m3")
    assert fwd["thinking"] == {"type": "disabled"}


def test_no_thinking_injected_by_default():
    fwd = _prepare_body({"messages": []}, "m")
    assert "thinking" not in fwd


def test_stream_opts_include_usage():
    fwd = _prepare_body({"stream": True}, "m")
    assert fwd["stream_options"]["include_usage"] is True
    # client's explicit choice is respected
    fwd2 = _prepare_body({"stream": True,
                          "stream_options": {"include_usage": False}}, "m")
    assert fwd2["stream_options"]["include_usage"] is False


def test_original_body_not_mutated():
    body = {"stream": True, "enable_thinking": True}
    _prepare_body(body, "m")
    assert body == {"stream": True, "enable_thinking": True}


def test_openrouter_gets_unified_reasoning_param():
    # Verified live: OR ignores `thinking`; reasoning:{enabled} is the switch.
    fwd = _prepare_body({"thinking": False}, "z-ai/glm-5.2", pid="openrouter")
    assert fwd["reasoning"] == {"enabled": False}
    fwd_on = _prepare_body({"thinking": True}, "z-ai/glm-5.2", pid="openrouter")
    assert fwd_on["reasoning"] == {"enabled": True}
    # explicit client reasoning wins; comet untouched; no intent → no param
    fwd_cl = _prepare_body({"thinking": False, "reasoning": {"effort": "low"}},
                           "z-ai/glm-5.2", pid="openrouter")
    assert fwd_cl["reasoning"] == {"effort": "low"}
    assert "reasoning" not in _prepare_body({"thinking": False}, "glm-5.2", pid="comet")
    assert "reasoning" not in _prepare_body({}, "z-ai/glm-5.2", pid="openrouter")
