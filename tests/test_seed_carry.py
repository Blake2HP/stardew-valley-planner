"""季末剩下的种子带到下一季：开局赠送没用完的、收获掉落没用完的。"""

import copy
import json

from stardew_planner import generate_plan
from stardew_planner.farm_state import compute_finance, extract_farm_state, render_state_for_prompt, resolve_plantings
from stardew_planner.llm_client import MOCK_FALL, MOCK_RESPONSE, MOCK_SUMMER


def plant(data, season, *items):
    raw = [{"day": d, "crop": c, "tiles": t} for d, c, t in items]
    return resolve_plantings(raw, data, season, 1)[0]


def test_unused_starting_seeds_are_left(data):
    f = compute_finance(plant(data, "spring", (1, "防风草", 10)), seed_stock={"防风草": 15})
    assert f.seed_cost == 0 and f.seeds_left == {"防风草": 5}


def test_dropped_seeds_without_replant_are_left(data):
    # 向日葵第 1 天种 10 格，第 9 天收获掉 10 颗种子，之后没有补种
    f = compute_finance(plant(data, "summer", (1, "向日葵", 10)))
    assert f.seeds_left == {"向日葵": 10}


def test_used_seeds_are_not_left(data):
    f = compute_finance(plant(data, "spring", (1, "防风草", 15)), seed_stock={"防风草": 15})
    assert f.seeds_left == {}


def test_state_and_prompt_carry_seeds(data):
    plantings = plant(data, "summer", (1, "向日葵", 10))
    state = extract_farm_state("summer", 1, plantings, compute_finance(plantings))
    assert state.seeds == (("向日葵", 10),)
    assert "手上剩余种子（程序按期望值计算）：向日葵种子约 10 颗" in render_state_for_prompt(state)


def test_tiny_fractions_are_not_mentioned(data):
    plantings = plant(data, "summer", (1, "蓝莓", 5))
    state = extract_farm_state("summer", 1, plantings, compute_finance(plantings, seed_stock={"蓝莓": 0.3}))
    assert "手上剩余种子" not in render_state_for_prompt(state)


def test_next_season_uses_carried_seeds_first(data):
    carried = {"向日葵": 10}
    f = compute_finance(plant(data, "fall", (1, "向日葵", 12)), seed_stock=carried, stock_label="上季剩余")
    assert f.seed_cost == 2 * 200 and "(12 − 上季剩余 10) × 200" in f.lines[-1]


class Scripted:
    def __init__(self, *payloads):
        self.payloads, self.prompts = list(payloads), []

    def complete(self, system, messages):
        self.prompts.append(messages[0]["content"])
        return json.dumps(self.payloads[len(self.prompts) - 1], ensure_ascii=False)


def with_sunflowers(payload, goal):
    p = copy.deepcopy(payload)
    p["daily_goals"]["1"] = goal
    p["plantings"].append({"day": 1, "crop": "向日葵", "tiles": 10})
    return p


def test_plan_reuses_summer_sunflower_seeds_in_fall(data, indexed_store):
    summer = with_sunflowers(MOCK_SUMMER, "种下蓝莓、玉米和向日葵")
    fall = with_sunflowers(MOCK_FALL, "种下蔓越莓和向日葵")
    client = Scripted(summer, fall)
    plan = generate_plan("种田", "稳健", "summer,fall", client=client, data=data, guide_store=indexed_store,
                         start_gold=20000, revise_rounds=0)
    summer_state = plan.results[0].end_state
    assert dict(summer_state.seeds)["向日葵"] == 10
    assert "向日葵种子约 10 颗" in client.prompts[1]
    fall_lines = plan.results[1].finance.lines
    assert any("(10 − 上季剩余 10) × 200" in line for line in fall_lines)


def test_unused_parsnip_gift_carries_to_summer(data, indexed_store):
    spring = copy.deepcopy(MOCK_RESPONSE)
    spring["plantings"][0]["tiles"] = 10                       # 只种 10 颗，剩 5 颗赠送的种子
    client = Scripted(spring, MOCK_SUMMER)
    plan = generate_plan("种田", "稳健", "spring,summer", client=client, data=data, guide_store=indexed_store,
                         revise_rounds=0)
    assert dict(plan.results[0].end_state.seeds)["防风草"] == 5
    assert "防风草种子约 5 颗" in client.prompts[1]
    assert dict(plan.results[1].end_state.seeds)["防风草"] == 5   # 夏季种不了，继续留着
