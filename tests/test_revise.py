"""自动修正：程序校验发现问题时反馈给模型重写，问题变少才采用新版本。"""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.llm_client import MOCK_RESPONSE, MockClient

ROOT = Path(__file__).resolve().parent.parent
GOOD = MOCK_RESPONSE


def late_cauliflower(payload=None):
    """第 26 天种花椰菜：来不及成熟（日程和清单都写了，只算 1 个问题）。"""
    p = copy.deepcopy(payload or GOOD)
    p["daily_goals"]["26"] = "种花椰菜"
    p["plantings"].append({"day": 26, "crop": "花椰菜", "tiles": 5})
    return p


def missing_gift(payload=None):
    """海莉生日（第 14 天）没有送礼。"""
    p = copy.deepcopy(payload or GOOD)
    p["daily_goals"]["14"] = "种下草莓"
    return p


class Scripted:
    """按顺序返回预设回复（dict 自动转 JSON，str 原样返回），并记录每次收到的对话。"""

    def __init__(self, *replies):
        self.replies, self.conversations = list(replies), []

    def complete(self, system, messages):
        self.conversations.append(copy.deepcopy(messages))
        reply = self.replies[min(len(self.conversations), len(self.replies)) - 1]
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def run(data, store, client, **kw):
    return generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=store, **kw)


def test_clean_plan_is_not_revised(data, indexed_store):
    client = Scripted(GOOD)
    result = run(data, indexed_store, client)
    assert len(client.conversations) == 1 and result.revisions == []
    assert result.problems == [] and result.initial_problems == []


def test_problems_are_fed_back_and_fixed(data, indexed_store):
    client = Scripted(late_cauliflower(), GOOD)
    result = run(data, indexed_store, client)
    assert len(client.conversations) == 2 and result.attempts == 2
    assert len(result.initial_problems) == 1 and "花椰菜来不及成熟" in result.initial_problems[0]
    assert result.problems == []
    assert result.revisions == [{"round": 1, "problems_before": 1, "problems_after": 0, "accepted": True}]
    assert result.daily_goals[26] == GOOD["daily_goals"]["26"]            # 采用了修正版
    assert not any(p.crop.name == "花椰菜" and p.day == 26 for p in result.plantings)
    assert any("问题从 1 条减少到 0 条" in w for w in result.season_warnings)


def test_revision_conversation(data, indexed_store):
    client = Scripted(late_cauliflower(), GOOD)
    run(data, indexed_store, client)
    convo = client.conversations[1]
    assert [m["role"] for m in convo] == ["user", "assistant", "user"]
    assert json.loads(convo[1]["content"])["daily_goals"]["26"] == "种花椰菜"   # 原版本作为上一轮回答
    assert "1. 第 26 天：花椰菜来不及成熟" in convo[2]["content"]                # 问题清单，去掉了 ⚠️
    assert "重新输出完整的 JSON" in convo[2]["content"]


def test_revision_without_improvement_is_rejected(data, indexed_store):
    original = late_cauliflower()
    client = Scripted(original, late_cauliflower(missing_gift()))              # 修正版反而多了问题
    result = run(data, indexed_store, client)
    assert result.revisions[0]["accepted"] is False and result.revisions[0]["problems_after"] == 2
    assert result.daily_goals[26] == "种花椰菜" and len(result.problems) == 1  # 保留原版本
    assert any("问题没有减少（1 → 2 条），保留原版本" in w for w in result.season_warnings)


def test_equal_problems_keep_original(data, indexed_store):
    client = Scripted(late_cauliflower(), late_cauliflower())
    result = run(data, indexed_store, client)
    assert result.revisions[0]["accepted"] is False and len(client.conversations) == 2


def test_unparseable_revision_keeps_original(data, indexed_store):
    client = Scripted(late_cauliflower(), "抱歉，我无法输出 JSON")
    result = run(data, indexed_store, client)
    (rev,) = result.revisions
    assert rev["accepted"] is False and rev["problems_after"] is None and "error" in rev
    assert result.daily_goals[26] == "种花椰菜" and result.attempts == 2
    assert any("修正版格式不合格，保留原版本" in w for w in result.season_warnings)


def test_multiple_rounds(data, indexed_store):
    client = Scripted(late_cauliflower(missing_gift()), late_cauliflower(), GOOD)
    result = run(data, indexed_store, client, revise_rounds=2)
    assert [(r["problems_before"], r["problems_after"]) for r in result.revisions] == [(2, 1), (1, 0)]
    assert result.problems == [] and len(client.conversations) == 3


def test_one_round_by_default(data, indexed_store):
    client = Scripted(late_cauliflower(missing_gift()), late_cauliflower(), GOOD)
    result = run(data, indexed_store, client)
    assert len(result.revisions) == 1 and len(result.problems) == 1


def test_revision_can_be_turned_off(data, indexed_store):
    client = Scripted(late_cauliflower(), GOOD)
    result = run(data, indexed_store, client, revise_rounds=0)
    assert len(client.conversations) == 1 and result.revisions == []
    assert len(result.problems) == 1 and result.initial_problems == result.problems


def test_gift_problems_trigger_revision(data, indexed_store):
    client = Scripted(missing_gift(), GOOD)
    result = run(data, indexed_store, client)
    assert "海莉生日当天没有送礼任务" in client.conversations[1][-1]["content"]
    assert result.gift_warnings == [] and result.revisions[0]["accepted"]


def test_fallback_is_not_revised(data, indexed_store):
    bad = copy.deepcopy(GOOD)
    bad["plantings"] = "坏数据"
    client = Scripted(bad)
    result = run(data, indexed_store, client)
    assert result.revisions == [] and len(client.conversations) == 2   # 只有格式重试，没有修正轮
    assert any("已用兜底结果" in w for w in result.season_warnings)


def test_reminders_alone_do_not_trigger_revision(data, indexed_store):
    # 换季枯死提醒、跨季提示、自动补上的清理目标都不算需要修正的问题
    payload = copy.deepcopy(GOOD)
    payload["daily_goals"]["28"] = "收完作物"
    result = run(data, indexed_store, Scripted(payload))
    assert result.revisions == [] and result.problems == []
    assert any("已自动在第 28 天加入" in w for w in result.season_warnings)


def test_negative_rounds_rejected(data, indexed_store):
    with pytest.raises(ValueError, match="自动修正轮数"):
        run(data, indexed_store, MockClient(), revise_rounds=-1)


def test_plan_passes_rounds_to_each_season(data, indexed_store):
    client = Scripted(late_cauliflower(), GOOD, GOOD)
    plan = generate_plan("种田", "稳健", "spring,summer", client=client, data=data, guide_store=indexed_store,
                         revise_rounds=0)
    assert all(r.revisions == [] for r in plan.results) and len(client.conversations) == 2


def test_cli_rejects_negative_rounds():
    r = subprocess.run([sys.executable, "main.py", "--provider", "mock", "--revise-rounds", "-1"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 2 and "自动修正轮数" in r.stderr
