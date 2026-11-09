"""日程校验加固：种植清单来不及成熟、肥料是否买得到。"""

import copy
import json

import pytest

from stardew_planner import generate_strategy
from stardew_planner.bonuses import fertilizer_availability_note, load_bonus_rules, resolve_bonuses
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


# ---------------------------------------------------------------------------
# 肥料是否买得到
# ---------------------------------------------------------------------------

def test_fertilizer_availability_data():
    f = load_bonus_rules().fertilizers
    assert (f["speed_gro"].sold_from_year, f["speed_gro"].sold_from_day, f["speed_gro"].craft_level) == (1, 15, 3)
    assert (f["basic_fertilizer"].sold_from_year, f["basic_fertilizer"].sold_from_day) == (1, 15)
    assert (f["quality_fertilizer"].sold_from_year, f["quality_fertilizer"].craft_level) == (2, 9)
    assert (f["deluxe_speed_gro"].sold_from_year, f["deluxe_speed_gro"].craft_level) == (2, 8)
    assert f["deluxe_fertilizer"].sold_from_year is None and f["hyper_speed_gro"].sold_from_year is None


@pytest.mark.parametrize(
    "fertilizer, season, year, day",
    [
        ("speed_gro", "spring", 1, 15),          # 开售当天
        ("speed_gro", "summer", 1, 1),           # 开售之后的季节
        ("basic_fertilizer", "spring", 2, 1),    # 第 2 年春季第 1 天已经在卖
        ("quality_fertilizer", "spring", 2, 1),
        ("deluxe_speed_gro", "winter", 3, 1),
        ("deluxe_fertilizer", "spring", 1, 1),   # 商店本来不卖：沿用“按 0 计”的说明
    ],
)
def test_no_note_when_available(fertilizer, season, year, day):
    assert fertilizer_availability_note(resolve_bonuses([], fertilizer), season, year, day) is None


def test_warns_before_spring_15():
    note = fertilizer_availability_note(resolve_bonuses([], "speed_gro"), "spring", 1, 14)
    assert note.startswith("⚠️ 生长激素第 1 年春季第 15 天起才在商店出售")
    assert "第 1 年春季第 14 天买不到" in note and "自制需要耕种等级 3（当前按 0 级）" in note


@pytest.mark.parametrize("season", ["spring", "summer", "fall", "winter"])
def test_warns_for_year_2_fertilizer_all_of_year_1(season):
    note = fertilizer_availability_note(resolve_bonuses([], "quality_fertilizer"), season, 1, 20)
    assert note.startswith("⚠️ 高级肥料第 2 年起才在商店出售")


def test_craftable_fertilizer_is_only_a_note():
    b = resolve_bonuses([], "quality_fertilizer", farming_level=9)
    note = fertilizer_availability_note(b, "fall", 1, 1)
    assert note.startswith("ℹ️") and "耕种等级 9 已达到自制要求（9 级）" in note


def test_craft_level_can_come_from_professions():
    # 工匠是 10 级职业，没写耕种等级时按 10 级：够自制高级肥料（9 级）
    note = fertilizer_availability_note(resolve_bonuses(["artisan"], "quality_fertilizer"), "fall", 1, 1)
    assert note.startswith("ℹ️")


def test_one_level_short_still_warns():
    b = resolve_bonuses([], "quality_fertilizer", farming_level=8)
    assert fertilizer_availability_note(b, "fall", 1, 1).startswith("⚠️")


def test_generator_reports_unavailable_fertilizer(data, indexed_store):
    result = run(data, indexed_store, MOCK_RESPONSE, fertilizer="speed_gro")     # mock 第 1 天就播种
    assert sum("生长激素第 1 年春季第 15 天起才在商店出售" in w for w in result.season_warnings) == 1


def test_generator_skips_note_without_plantings(data, indexed_store):
    payload = {**copy.deepcopy(MOCK_RESPONSE), "plantings": []}
    result = run(data, indexed_store, payload, fertilizer="speed_gro")
    assert not any("才在商店出售" in w for w in result.season_warnings)
