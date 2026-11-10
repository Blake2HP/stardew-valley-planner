"""兜底解析必须明确告知用户；模型输出被截断时不能直接采信。"""

import copy
import json

import pytest

from stardew_planner import generate_strategy
from stardew_planner.generator import StrategyParseError
from stardew_planner.llm_client import MOCK_RESPONSE, TruncatedOutput

GOOD = json.dumps(MOCK_RESPONSE, ensure_ascii=False)


class Scripted:
    """按顺序返回回复；回复是异常时抛出。"""

    def __init__(self, *replies):
        self.replies, self.calls, self.messages = list(replies), 0, []

    def complete(self, system, messages):
        self.messages.append(copy.deepcopy(messages))
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        return reply


def run(data, store, client):
    return generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=store)


def dumps(payload):
    return json.dumps(payload, ensure_ascii=False)


def fallback_warnings(result):
    return [w for w in result.season_warnings if "已用兜底结果" in w]


def test_good_output_has_no_fallback_warning(data, indexed_store):
    assert fallback_warnings(run(data, indexed_store, Scripted(GOOD))) == []


def test_missing_days_and_plantings_are_reported(data, indexed_store):
    bad = copy.deepcopy(MOCK_RESPONSE)
    del bad["daily_goals"]["5"], bad["daily_goals"]["9"]
    bad["plantings"] = "坏数据"
    (w,) = fallback_warnings(run(data, indexed_store, Scripted(dumps(bad))))
    assert "第 5、9 天没有目标（显示为“自由安排”）" in w
    assert "种植清单缺失" in w and "资金预期不可靠" in w and "daily_goals" in w


def test_invalid_planting_entries_are_counted(data, indexed_store):
    bad = copy.deepcopy(MOCK_RESPONSE)
    bad["plantings"][0]["tiles"] = -3
    bad["plantings"][1]["day"] = 99
    result = run(data, indexed_store, Scripted(dumps(bad)))
    (w,) = fallback_warnings(result)
    assert "种植清单有 2 条无效" in w and len(result.plantings) == 3


def test_retry_success_has_no_warning(data, indexed_store):
    bad = {k: v for k, v in MOCK_RESPONSE.items() if k != "plantings"}
    client = Scripted(dumps(bad), GOOD)
    assert fallback_warnings(run(data, indexed_store, client)) == [] and client.calls == 2


def test_truncated_output_is_retried_even_if_it_parses(data, indexed_store):
    # 截断的内容能通过校验，也要让模型重写，否则种植清单可能只有一半
    client = Scripted(TruncatedOutput(GOOD), GOOD)
    result = run(data, indexed_store, client)
    assert client.calls == 2 and result.attempts == 2 and fallback_warnings(result) == []
    assert "被截断" in client.messages[1][-1]["content"]


def test_always_truncated_uses_fallback_and_says_why(data, indexed_store):
    (w,) = fallback_warnings(run(data, indexed_store, Scripted(TruncatedOutput(GOOD))))
    assert "被截断" in w


def test_unparseable_truncation_raises_with_reason(data, indexed_store):
    with pytest.raises(StrategyParseError, match="被截断"):
        run(data, indexed_store, Scripted(TruncatedOutput("")))
