"""工具升级时间轴：data/tools.json、tool_upgrades 解析、取回日、冲突校验、升级费用。

规则来自 https://stardewvalleywiki.com/Blacksmith#Upgrade_Tools（核对日期 2026-11-11）。
"""

import copy
import json

import pytest

from stardew_planner import generate_strategy
from stardew_planner.farm_state import compute_finance, describe_finance
from stardew_planner.llm_client import MOCK_RESPONSE
from stardew_planner.tools import check_upgrades, describe_tools, parse_upgrades, upgrade_costs


def test_wiki_rules(data):
    r = data.tool_rules
    assert r.upgrade_days == 2
    assert [(lv.name, lv.price, lv.bar, lv.bars) for lv in r.levels] == [
        ("铜", 2000, "铜锭", 5), ("钢", 5000, "铁锭", 5), ("金", 10000, "金锭", 5), ("铱", 25000, "铱锭", 5)]
    assert {t.name for t in r.tools} == {"喷壶", "锄头", "十字镐", "斧头"}


def test_parse_and_ready_day(data):
    (u,), problems = parse_upgrades([{"tool": "水壶", "day": 5, "level": "铜"}], data.tool_rules, set())
    assert problems == [] and u.tool.name == "喷壶" and u.ready_day == 7 and list(u.away_days) == [6]


def test_pickup_waits_when_shop_closed(data):
    # 第 11 天交，第 13 天是复活节、铁匠铺关门 → 第 14 天才能取
    (u,), _ = parse_upgrades([{"tool": "斧头", "day": 11}], data.tool_rules, {13, 24})
    assert u.ready_day == 14 and list(u.away_days) == [12, 13] and u.level is None


@pytest.mark.parametrize("raw", [[{"tool": "钓竿", "day": 3}], [{"tool": "喷壶", "day": 30}], [{"day": 3}], "喷壶"])
def test_invalid_entries(data, raw):
    upgrades, problems = parse_upgrades(raw, data.tool_rules, set())
    assert upgrades == [] and problems and problems[0].startswith("⚠️ tool_upgrades")


def goals_with(**overrides):
    goals = {d: "钓鱼" for d in range(1, 29)}
    goals.update({int(k[1:]): v for k, v in overrides.items()})
    return goals


def test_conflicts(data):
    (u,), _ = parse_upgrades([{"tool": "喷壶", "day": 5, "level": "铜"}], data.tool_rules, set())
    ok = goals_with(d5="浇水后交送升级喷壶", d7="取回喷壶，浇水")
    assert check_upgrades([u], ok, set()) == []
    conflict = check_upgrades([u], goals_with(d5="交送升级喷壶", d6="大面积浇水", d7="取回喷壶"), set())
    assert len(conflict) == 1 and "第 6 天喷壶还在铁匠铺升级" in conflict[0] and "浇水" in conflict[0]
    sowing = check_upgrades([u], ok, {6})
    assert len(sowing) == 1 and "新播种的作物没法浇水" in sowing[0]


def test_missing_drop_and_pickup_goals(data):
    (u,), _ = parse_upgrades([{"tool": "十字镐", "day": 10, "level": "铜"}], data.tool_rules, set())
    problems = check_upgrades([u], goals_with(d11="下矿井推进"), set())
    assert any("第 10 天的目标没有写“交送升级十字镐”" in p for p in problems)
    assert any("第 12 天的目标没有写“取回十字镐”" in p for p in problems)
    assert any("第 11 天十字镐还在铁匠铺" in p and "下矿" in p for p in problems)


def test_pickup_next_season_not_required(data):
    (u,), _ = parse_upgrades([{"tool": "斧头", "day": 27}], data.tool_rules, set())
    assert u.ready_day == 29
    assert check_upgrades([u], goals_with(d27="交送升级斧头"), set()) == []


def test_costs_in_finance(data):
    (u,), _ = parse_upgrades([{"tool": "喷壶", "day": 5, "level": "钢"}], data.tool_rules, set())
    f = compute_finance([], start_gold=6000, fixed_costs=upgrade_costs([u]))
    assert f.tool_cost == 5000 and f.end_gold == 1000
    assert "− 工具升级 5000 =" in "\n".join(describe_finance(f))
    assert "5 个铁锭" in "\n".join(f.lines)
    short = compute_finance([], start_gold=1000, fixed_costs=upgrade_costs([u]))
    assert "升级工具" in short.warnings[0] and "第 5 天" in short.warnings[0]


def test_prompt(data):
    text = describe_tools(data.tool_rules)
    assert "要 2 天" in text and "铜：2000g + 5 个铜锭" in text


class Scripted:
    def __init__(self, *payloads):
        self.payloads, self.prompts, self.system = list(payloads), [], None

    def complete(self, system, messages):
        self.system = system
        self.prompts.append(messages[-1]["content"])
        return json.dumps(self.payloads[min(len(self.prompts), len(self.payloads)) - 1], ensure_ascii=False)


def test_strategy_detects_and_revises(data, indexed_store):
    bad = copy.deepcopy(MOCK_RESPONSE)
    bad["tool_upgrades"] = [{"tool": "喷壶", "day": 21, "level": "铜"}]
    bad["daily_goals"]["21"] = "交送升级喷壶"
    bad["daily_goals"]["22"] = "收获花椰菜，补种土豆，浇水"   # 第 22 天喷壶不在，还安排了浇水和播种
    bad["daily_goals"]["23"] = "取回喷壶，下矿井推进到 40 层"
    good = copy.deepcopy(bad)
    good["tool_upgrades"] = [{"tool": "喷壶", "day": 25, "level": "铜"}]
    good["daily_goals"]["21"], good["daily_goals"]["22"] = "下矿井推进到 30 层", "收获花椰菜，补种土豆"
    good["daily_goals"]["23"] = "下矿井推进到 40 层"
    good["daily_goals"]["25"], good["daily_goals"]["27"] = "收获草莓，交送升级喷壶", "取回喷壶，送艾米丽紫水晶"
    client = Scripted(bad, good)
    result = generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               start_gold=5000)
    assert "交送升级X" in client.system and "tool_upgrades" in client.system
    assert any("工具冲突：第 22 天喷壶" in p for p in result.initial_problems)
    assert not any("工具冲突" in p for p in result.problems)
    assert result.finance.tool_cost == 2000


def test_no_upgrades_no_change(data, indexed_store):
    client = Scripted(MOCK_RESPONSE)
    result = generate_strategy("种田", "稳健", "spring", client=client, data=data, guide_store=indexed_store)
    assert result.finance.tool_cost == 0 and not any("工具" in p for p in result.problems)
