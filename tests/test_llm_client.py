"""Claude 客户端的请求参数、拒答、截断、超时配置。用假的 SDK 客户端，不发真实请求。"""

import json
from types import SimpleNamespace

import pytest

from stardew_planner import generate_strategy
from stardew_planner.llm_client import (
    FALLBACK_BETA, MOCK_RESPONSE, AnthropicClient, ModelRefusal, TruncatedOutput, get_client,
)


class FakeSDK:
    """记录请求参数；beta.messages.create 和 messages.create 分开记录。"""

    def __init__(self, stop_reason="end_turn", text=None, stop_details=None):
        self.calls = []
        text = json.dumps(MOCK_RESPONSE, ensure_ascii=False) if text is None else text
        resp = SimpleNamespace(
            stop_reason=stop_reason, stop_details=stop_details,
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        )

        def create(endpoint):
            def _create(**kwargs):
                self.calls.append((endpoint, kwargs))
                return resp
            return _create

        self.messages = SimpleNamespace(create=create("messages"))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create("beta")))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ANTHROPIC_MODEL", "ANTHROPIC_EFFORT", "ANTHROPIC_FALLBACKS", "ANTHROPIC_TIMEOUT",
                 "ANTHROPIC_MAX_RETRIES", "ANTHROPIC_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_use_fallbacks_effort_and_larger_max_tokens():
    sdk = FakeSDK()
    client = AnthropicClient(client=sdk)
    assert client.complete("系统", [{"role": "user", "content": "你好"}]).startswith("{")
    ((endpoint, kw),) = sdk.calls
    assert endpoint == "beta"
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["model"] == "claude-opus-5-5" and kw["max_tokens"] == 16000
    assert kw["output_config"] == {"effort": "medium"}
    assert "thinking" not in kw                     # Opus 5.5 思考始终开启，不发 disabled（会 400）


def test_fallbacks_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_FALLBACKS", "off")
    sdk = FakeSDK()
    AnthropicClient(client=sdk).complete("s", [])
    ((endpoint, kw),) = sdk.calls
    assert endpoint == "messages" and "fallbacks" not in kw and "betas" not in kw


def test_unsupported_model_skips_fallbacks():
    sdk = FakeSDK()
    AnthropicClient(model="claude-haiku-5-5", client=sdk).complete("s", [])
    assert sdk.calls[0][0] == "messages"


def test_effort_and_max_tokens_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_EFFORT", "HIGH")
    monkeypatch.setenv("ANTHROPIC_MAX_TOKENS", "20000")
    sdk = FakeSDK()
    AnthropicClient(client=sdk).complete("s", [])
    kw = sdk.calls[0][1]
    assert kw["output_config"] == {"effort": "high"} and kw["max_tokens"] == 20000


@pytest.mark.parametrize("env, value, message", [
    ("ANTHROPIC_EFFORT", "turbo", "effort"),
    ("ANTHROPIC_TIMEOUT", "abc", "ANTHROPIC_TIMEOUT"),
    ("ANTHROPIC_TIMEOUT", "-5", "不能为负数"),
])
def test_bad_env_values(monkeypatch, env, value, message):
    monkeypatch.setenv(env, value)
    with pytest.raises(ValueError, match=message):
        AnthropicClient(client=FakeSDK())


def test_timeout_and_retries_reach_the_sdk(monkeypatch):
    import anthropic

    seen = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", Recorder)
    AnthropicClient()
    assert seen == {"timeout": 300.0, "max_retries": 1}
    seen.clear()
    AnthropicClient(timeout=60, max_retries=0)
    assert seen == {"timeout": 60, "max_retries": 0}
    monkeypatch.setenv("ANTHROPIC_TIMEOUT", "120")
    seen.clear()
    get_client("anthropic")
    assert seen["timeout"] == 120.0


def test_refusal_raises_with_category():
    details = SimpleNamespace(category="cyber", explanation="说明文字")
    with pytest.raises(ModelRefusal, match="类别：cyber") as e:
        AnthropicClient(client=FakeSDK(stop_reason="refusal", text="", stop_details=details)).complete("s", [])
    assert e.value.category == "cyber" and "说明文字" in str(e.value)


def test_refusal_without_details():
    with pytest.raises(ModelRefusal):
        AnthropicClient(client=FakeSDK(stop_reason="refusal", text="")).complete("s", [])


def test_max_tokens_still_raises_truncated():
    with pytest.raises(TruncatedOutput) as e:
        AnthropicClient(client=FakeSDK(stop_reason="max_tokens", text="半截")).complete("s", [])
    assert e.value.text == "半截"


def test_only_text_blocks_are_returned():
    out = AnthropicClient(client=FakeSDK(text="正文")).complete("s", [])
    assert out == "正文"                               # thinking 块被忽略


def test_refusal_is_not_retried_as_parse_error(data, indexed_store):
    sdk = FakeSDK(stop_reason="refusal", text="")
    with pytest.raises(ModelRefusal):
        generate_strategy("种田", "稳健", "spring", client=AnthropicClient(client=sdk), data=data,
                          guide_store=indexed_store)
    assert len(sdk.calls) == 1                          # 拒答不当成 JSON 错误去重试


def test_mock_accepts_client_options():
    assert get_client("mock", timeout=5, max_retries=0).complete("s", [])


def test_openai_provider_is_gone():
    with pytest.raises(ValueError, match="未知 provider"):
        get_client("openai")
