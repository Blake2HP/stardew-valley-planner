"""前期体力：每天手浇的格数、浇水体力、补体力校验与自动修正。

数值来自 https://stardewvalleywiki.com/Energy 与 /Skills#Proficiency（核对日期 2026-11-18）。
"""

import copy
import json

from stardew_planner import generate_strategy
from stardew_planner.analysis import build_data_context
from stardew_planner.bonuses import NO_BONUSES, resolve_bonuses
from stardew_planner.energy import check_energy, heavy_days, watered_tiles
from stardew_planner.farm_state import compute_finance, extract_farm_state, resolve_plantings
from stardew_planner.llm_client import MOCK_RESPONSE


def plant(data, season, *items):
    raw = [{"day": d, "crop": c, "tiles": t} for d, c, t in items]
    return resolve_plantings(raw, data, season, 1)[0]


def test_wiki_values(data):
    r = data.energy_rules
    assert (r.max_energy, r.watering_cost, r.level_reduction) == (270, 2.0, 0.1)
    assert r.cost_per_tile(0) == 2.0 and r.cost_per_tile(10) == 1.0


def test_watered_tiles_single_harvest_and_regrow(data):
    # 防风草第 1 天种、4 天成熟（第 5 天收）：第 1-4 天要浇；蓝莓可再生：整季都要浇
    tiles = watered_tiles(plant(data, "spring", (1, "防风草", 10)))
    assert [tiles[d] for d in (1, 4, 5, 28)] == [10, 10, 0, 0]
    tiles = watered_tiles(plant(data, "summer", (3, "蓝莓", 6)))
    assert [tiles[d] for d in (2, 3, 28)] == [0, 6, 6]


def test_carried_crops_are_watered(data):
    plantings = plant(data, "summer", (1, "玉米", 8))
    state = extract_farm_state("summer", 1, plantings, compute_finance(plantings))
    tiles = watered_tiles([], state.carry)
    assert tiles[1] == 8 and tiles[28] == 8


def test_heavy_days_follow_farming_level(data):
    tiles = watered_tiles(plant(data, "spring", (1, "防风草", 60)))
    assert heavy_days(tiles, data.energy_rules) == {d: 120 for d in range(1, 5)}       # 60 格 × 2 点
    assert heavy_days(tiles, data.energy_rules, resolve_bonuses([], farming_level=10)) == {}  # 60 × 1 = 60


def test_replenish_every_three_days(data):
    plantings = plant(data, "spring", (1, "防风草", 60))      # 第 1-4 天压力大
    goals = {d: "种地" for d in range(1, 29)}
    (w,) = check_energy(goals, plantings, (), data.energy_rules)
    assert w.startswith("⚠️ 体力预警：第 1-4 天每天要手浇最多 60 格，约 120 点体力") and "第 1-3 天、第 4 天没有安排" in w
    goals[2] = "浇水，采集大葱"
    (w,) = check_energy(goals, plantings, (), data.energy_rules)
    assert w.split("开局上限 270），")[1].startswith("第 4 天没有安排补充体力")
    goals[4] = "浇水，去沙龙买沙拉"
    assert check_energy(goals, plantings, (), data.energy_rules) == []


def test_small_farm_no_warning(data):
    plantings = plant(data, "spring", (1, "防风草", 15))
    assert check_energy({d: "钓鱼" for d in range(1, 29)}, plantings, (), data.energy_rules) == []


def test_prompt_section(data):
    text = build_data_context(data, "spring")["energy"]
    assert "开局最大体力 270 点" in text and "= 2 点（耕种等级 0）" in text and "约 50 格" in text
    level10 = build_data_context(data, "spring", bonuses=resolve_bonuses([], farming_level=10))["energy"]
    assert "= 1 点（耕种等级 10）" in level10 and "约 100 格" in level10


class Scripted:
    def __init__(self, *payloads):
        self.payloads, self.calls, self.system = list(payloads), 0, None

    def complete(self, system, messages):
        self.system, self.calls = system, self.calls + 1
        return json.dumps(self.payloads[min(self.calls, len(self.payloads)) - 1], ensure_ascii=False)


def test_strategy_warns_then_revises(data, indexed_store):
    big = copy.deepcopy(MOCK_RESPONSE)
    big["plantings"][0]["tiles"] = 70                      # 第 1 天 70 格防风草：第 1-4 天每天 140 点体力
    fixed = copy.deepcopy(big)
    fixed["daily_goals"]["2"] = "领取鱼竿，采集大葱补体力"
    fixed["daily_goals"]["4"] = "海滩钓鱼，去沙龙买沙拉"
    client = Scripted(big, fixed)
    result = generate_strategy("种田", "速通极限", "spring", client=client, data=data, guide_store=indexed_store,
                               start_gold=5000)
    assert "14. 种植规模要考虑体力" in client.system
    assert any(p.startswith("⚠️ 体力预警") for p in result.initial_problems)
    assert not any(p.startswith("⚠️ 体力预警") for p in result.problems)


def test_no_energy_file_skips_check(data, indexed_store):
    import dataclasses
    no_energy = dataclasses.replace(data, energy_rules=None)
    big = copy.deepcopy(MOCK_RESPONSE)
    big["plantings"][0]["tiles"] = 70
    result = generate_strategy("种田", "速通极限", "spring", client=Scripted(big), data=no_energy,
                               guide_store=indexed_store, start_gold=5000, revise_rounds=0)
    assert not any("体力" in p for p in result.problems)
    assert build_data_context(no_energy, "spring")["energy"] == "（没有体力数据）"
