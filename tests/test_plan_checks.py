"""日程校验加固：种植清单来不及成熟。"""

import copy
import json

from stardew_planner import generate_strategy
from stardew_planner.bonuses import resolve_bonuses
from stardew_planner.farm_state import compute_finance, late_planting_warnings, resolve_plantings
from stardew_planner.llm_client import MOCK_RESPONSE


class Scripted:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, system, messages):
        return json.dumps(self.payload, ensure_ascii=False)


def run(data, store, payload, season="spring", **kw):
    return generate_strategy("种田", "稳健", season, client=Scripted(payload), data=data, guide_store=store, **kw)


def plantings(data, season, *items, bonuses=None):
    raw = [{"day": d, "crop": c, "tiles": t} for d, c, t in items]
    return resolve_plantings(raw, data, season, 1, bonuses or resolve_bonuses([]))[0]


# ---------------------------------------------------------------------------
# 种植清单来不及成熟
# ---------------------------------------------------------------------------

def test_late_planting_warns_with_formula(data):
    (w,) = late_planting_warnings(plantings(data, "spring", (26, "花椰菜", 10)), "spring")
    assert "种植清单第 26 天的花椰菜来不及成熟" in w
    assert "成熟日 = 种植日 26 + 生长 12 天 = 38 > 最后存活日 28" in w


def test_on_time_planting_does_not_warn(data):
    # 第 16 天种花椰菜：16 + 12 = 28，当天成熟
    assert late_planting_warnings(plantings(data, "spring", (16, "花椰菜", 10)), "spring") == []


def test_multi_season_crop_uses_full_life(data):
    # 夏季第 25 天种玉米，秋季能成熟；秋季第 25 天种，活不到冬季
    assert late_planting_warnings(plantings(data, "summer", (25, "玉米", 5)), "summer") == []
    (w,) = late_planting_warnings(plantings(data, "fall", (25, "玉米", 5)), "fall")
    assert "最后存活日 28" in w


def test_speed_fertilizer_counts_toward_maturity(data):
    # 高级生长激素下花椰菜 9 天：第 19 天种刚好成熟，第 20 天来不及
    b = resolve_bonuses([], "deluxe_speed_gro")
    assert late_planting_warnings(plantings(data, "spring", (19, "花椰菜", 1), bonuses=b), "spring", b) == []
    (w,) = late_planting_warnings(plantings(data, "spring", (20, "花椰菜", 1), bonuses=b), "spring", b)
    assert "生长 9 天" in w


def test_late_planting_still_costs_seeds(data):
    f = compute_finance(plantings(data, "spring", (26, "花椰菜", 10)), start_gold=1000)
    assert (f.crop_revenue, f.seed_cost, f.end_gold) == (0, 800, 200)


def test_generator_reports_listed_late_planting(data, indexed_store):
    payload = copy.deepcopy(MOCK_RESPONSE)
    payload["plantings"].append({"day": 26, "crop": "花椰菜", "tiles": 1})   # 日程文字里没提
    result = run(data, indexed_store, payload)
    assert sum("种植清单第 26 天的花椰菜来不及成熟" in w for w in result.season_warnings) == 1


def test_generator_does_not_repeat_goal_warning(data, indexed_store):
    payload = copy.deepcopy(MOCK_RESPONSE)
    payload["plantings"].append({"day": 26, "crop": "花椰菜", "tiles": 1})
    payload["daily_goals"]["26"] = "种花椰菜"                                 # 日程校验已经会报
    result = run(data, indexed_store, payload)
    assert sum("花椰菜来不及成熟" in w for w in result.season_warnings) == 1
