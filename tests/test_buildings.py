"""罗宾的建筑工期：data/buildings.json、下单日校验（周二、节日）、完工日、同时只能建一座、建筑费用。

规则与数值来自 https://stardewvalleywiki.com/Carpenter%27s_Shop（核对日期 2026-11-18）。
"""

import copy
import json

import pytest

from stardew_planner import generate_strategy
from stardew_planner.analysis import build_data_context
from stardew_planner.buildings import (
    check_constructions, construction_costs, parse_constructions, ready_day, weekday,
)
from stardew_planner.farm_state import compute_finance, describe_finance
from stardew_planner.llm_client import MOCK_RESPONSE

SPRING_NO_WORK = {13, 24}   # 复活节、花舞节


@pytest.mark.parametrize("name, price, days, materials", [
    ("鸡舍", 4000, 3, [("木材", 300), ("石头", 100)]),
    ("畜棚", 6000, 3, [("木材", 350), ("石头", 150)]),
    ("筒仓", 100, 2, [("石头", 100), ("粘土", 10), ("铜锭", 5)]),
])
def test_wiki_values(data, name, price, days, materials):
    b = data.building_rules.find(name)
    assert (b.price, b.days, [(m.name, m.count) for m in b.materials]) == (price, days, materials)
    assert data.building_rules.find("Coop").name == "鸡舍"
    assert data.building_rules.find("大鸡舍").upgrades_from == "鸡舍"


def test_weekdays():
    assert [weekday(d) for d in (1, 2, 8, 9, 23, 28)] == ["周一", "周二", "周一", "周二", "周二", "周日"]


def test_ready_day():
    assert ready_day(3, 3, set()) == 6                  # 第 3 天下单，第 4-6 天施工，第 6 天早上完工
    assert ready_day(11, 3, SPRING_NO_WORK) == 15       # 第 13 天复活节不施工，顺延一天
    assert ready_day(27, 3, set()) == 30                # 跨到下一季


def parse(data, raw):
    return parse_constructions(raw, data.building_rules, SPRING_NO_WORK)


@pytest.mark.parametrize("raw", [[{"building": "城堡", "day": 3}], [{"building": "鸡舍", "day": 0}],
                                 [{"day": 3}], "鸡舍"])
def test_invalid_entries(data, raw):
    items, problems = parse(data, raw)
    assert items == [] and problems and problems[0].startswith("⚠️ constructions")


def goals(**over):
    g = {d: "钓鱼" for d in range(1, 29)}
    g.update({int(k[1:]): v for k, v in over.items()})
    return g


def check(data, raw, g):
    items, _ = parse(data, raw)
    return check_constructions(items, g, data.building_rules, SPRING_NO_WORK)


def test_valid_order_passes(data):
    assert check(data, [{"building": "鸡舍", "day": 3}], goals(d3="找罗宾建鸡舍")) == []


def test_tuesday_and_festival_orders(data):
    (w,) = check(data, [{"building": "鸡舍", "day": 9}], goals(d9="找罗宾建鸡舍"))
    assert "第 9 天是周二" in w and "没法下单建鸡舍" in w
    (w,) = check(data, [{"building": "筒仓", "day": 13}], goals(d13="找罗宾建筒仓"))
    assert "第 13 天是节日" in w


def test_overlapping_orders(data):
    raw = [{"building": "鸡舍", "day": 3}, {"building": "筒仓", "day": 5}]
    (w,) = check(data, raw, goals(d3="找罗宾建鸡舍", d5="找罗宾建筒仓"))
    assert w.startswith("⚠️ 建筑冲突：罗宾还在建鸡舍（第 3 天下单，第 6 天完工）")
    assert "第 5 天不能再下单建筒仓" in w
    ok = [{"building": "鸡舍", "day": 3}, {"building": "筒仓", "day": 6}]
    assert check(data, ok, goals(d3="找罗宾建鸡舍", d6="找罗宾建筒仓")) == []


def test_goal_must_mention_building(data):
    (w,) = check(data, [{"building": "畜棚", "day": 4}], goals())
    assert "第 4 天的目标没有写“找罗宾建畜棚”" in w


def test_costs_in_finance(data):
    items, _ = parse(data, [{"building": "鸡舍", "day": 3}])
    f = compute_finance([], start_gold=5000, building_costs=construction_costs(items))
    assert f.building_cost == 4000 and f.end_gold == 1000
    assert "− 建筑 4000 =" in "\n".join(describe_finance(f))
    assert "木材 300、石头 100（材料需自备，不计）" in "\n".join(f.lines)
    short = compute_finance([], start_gold=1000, building_costs=construction_costs(items))
    assert "盖建筑" in short.warnings[0] and "第 3 天" in short.warnings[0]


def test_prompt_section(data):
    text = build_data_context(data, "spring")["buildings"]
    assert "周二是第 2、9、16、23 天" in text and "鸡舍 4000g、3 天" in text


class Scripted:
    def __init__(self, *payloads):
        self.payloads, self.calls, self.system = list(payloads), 0, None

    def complete(self, system, messages):
        self.system, self.calls = system, self.calls + 1
        return json.dumps(self.payloads[min(self.calls, len(self.payloads)) - 1], ensure_ascii=False)


def test_strategy_detects_and_revises(data, indexed_store):
    bad = copy.deepcopy(MOCK_RESPONSE)
    bad["constructions"] = [{"building": "鸡舍", "day": 9}, {"building": "筒仓", "day": 10}]  # 周二下单 + 施工重叠
    bad["daily_goals"]["9"], bad["daily_goals"]["10"] = "找罗宾建鸡舍", "找罗宾建筒仓"
    good = copy.deepcopy(bad)
    good["constructions"] = [{"building": "鸡舍", "day": 8}, {"building": "筒仓", "day": 11}]
    good["daily_goals"]["8"], good["daily_goals"]["9"] = "找罗宾建鸡舍", "河边钓鱼攒钱"
    good["daily_goals"]["10"], good["daily_goals"]["11"] = "河边钓鱼攒钱", "找罗宾建筒仓，下矿井推进到 20 层"
    client = Scripted(bad, good)
    result = generate_strategy("养殖", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               start_gold=10000)
    assert '"constructions"' in client.system and "15. 找罗宾建农场建筑" in client.system
    assert any("第 9 天是周二" in p for p in result.initial_problems)
    assert any(p.startswith("⚠️ 建筑冲突") for p in result.initial_problems)
    assert not any("建筑" in p for p in result.problems)
    assert result.finance.building_cost == 4000 + 100


def test_no_constructions_no_change(data, indexed_store):
    result = generate_strategy("种田", "稳健", "spring", client=Scripted(MOCK_RESPONSE), data=data,
                               guide_store=indexed_store)
    assert result.finance.building_cost == 0 and not any("建筑" in p for p in result.problems)
