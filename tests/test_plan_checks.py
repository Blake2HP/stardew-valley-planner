"""日程校验加固：种植清单来不及成熟、肥料是否买得到、清理目标保留跨季作物、沙漠作物与完整作物列表。"""

import copy
import json

import pytest

from stardew_planner import generate_strategy
from stardew_planner.bonuses import fertilizer_availability_note, load_bonus_rules, resolve_bonuses
from stardew_planner.analysis import _compact_crop, build_data_context, crop_note, rank_crops
from stardew_planner.farm_state import bus_warnings, compute_finance, late_planting_warnings, resolve_plantings
from stardew_planner.llm_client import MOCK_RESPONSE, MOCK_SUMMER
from stardew_planner.season_rules import CLEANUP_GOAL, check_plantings, enforce_season_end


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


# ---------------------------------------------------------------------------
# 清理目标保留跨季作物
# ---------------------------------------------------------------------------

def test_existing_cleanup_gets_kept_crops():
    goals, changed = enforce_season_end({28: CLEANUP_GOAL}, keep=["玉米"])
    assert changed and goals[28] == f"{CLEANUP_GOAL}（保留玉米）"


def test_mentioning_a_crop_is_not_keeping_it():
    goals, changed = enforce_season_end({28: f"收获玉米；{CLEANUP_GOAL}"}, keep=["玉米"])
    assert changed and goals[28].endswith("（保留玉米）")


@pytest.mark.parametrize(
    "goal",
    [f"{CLEANUP_GOAL}（保留玉米）", f"{CLEANUP_GOAL}，玉米留下", f"不清理玉米；{CLEANUP_GOAL}", f"{CLEANUP_GOAL}（玉米别动）"],
)
def test_already_kept_crops_are_left_alone(goal):
    goals, changed = enforce_season_end({28: goal}, keep=["玉米"])
    assert not changed and goals[28] == goal


def test_only_missing_crops_are_added():
    goals, changed = enforce_season_end({28: f"{CLEANUP_GOAL}（保留玉米）"}, keep=["玉米", "小麦"])
    assert changed and goals[28] == f"{CLEANUP_GOAL}（保留玉米）（保留小麦）"


def test_annotation_is_idempotent():
    once, _ = enforce_season_end({28: CLEANUP_GOAL}, keep=["玉米"])
    twice, changed = enforce_season_end(once, keep=["玉米"])
    assert not changed and twice == once


def test_no_kept_crops_leaves_cleanup_alone():
    goals, changed = enforce_season_end({28: CLEANUP_GOAL})
    assert not changed and goals[28] == CLEANUP_GOAL


def test_generator_annotates_model_cleanup(data, indexed_store):
    payload = copy.deepcopy(MOCK_SUMMER)
    payload["daily_goals"]["28"] = "收获蓝莓；清理农场/停止种植"              # 模型自己写了清理，但没提玉米
    result = run(data, indexed_store, payload, season="summer", start_gold=5000)
    assert result.daily_goals[28].endswith("（保留玉米）")
    assert any("第 28 天的清理目标已注明保留玉米" in w for w in result.season_warnings)
    assert not any("已自动在第 28 天加入" in w for w in result.season_warnings)


# ---------------------------------------------------------------------------
# 沙漠作物与完整作物列表
# ---------------------------------------------------------------------------

def test_desert_crops_are_flagged(data):
    assert {c.name for c in data.crops if c.requires_bus} == {"大黄", "杨桃", "甜菜"}
    assert all("沙漠绿洲" in c.seed_source for c in data.crops if c.requires_bus)


def test_goal_check_warns_in_year_1_only(data):
    (w,) = check_plantings({1: "种大黄"}, data, "spring", year=1)
    assert "要先修好巴士" in w.message and w.formula == "种子来源 = 沙漠绿洲"
    assert check_plantings({1: "种大黄"}, data, "spring", year=2) == []
    assert check_plantings({1: "种大黄"}, data, "spring") == []          # 没传年份：不检查


def test_bus_warning_does_not_hide_late_planting(data):
    messages = [w.message for w in check_plantings({20: "种大黄"}, data, "spring", year=1)]
    assert len(messages) == 2 and "来不及成熟" in messages[1]


def test_bus_warnings_from_plantings_list(data):
    listed = plantings(data, "spring", (1, "大黄", 5), (1, "土豆", 5))
    (w,) = bus_warnings(listed, 1)
    assert "种植清单第 1 天的大黄" in w
    assert bus_warnings(listed, 2) == [] and bus_warnings(listed, 1, {(1, "大黄")}) == []


def test_generator_warns_once_for_desert_crop(data, indexed_store):
    payload = copy.deepcopy(MOCK_RESPONSE)
    payload["plantings"].append({"day": 1, "crop": "大黄", "tiles": 1})
    payload["daily_goals"]["1"] = "种大黄"
    assert sum("大黄" in w and "修好巴士" in w for w in run(data, indexed_store, payload).season_warnings) == 1


def test_desert_crop_stays_listed_with_a_note(data, crops):
    table = build_data_context(data, "spring", year=1)["crop_table"]
    assert "| 大黄 |" in table and "- 大黄：种子在沙漠绿洲出售，要先修好巴士才能买到" in table
    assert crop_note(crops["土豆"]) == ""


def test_prompt_lists_every_crop_in_season(data):
    table = build_data_context(data, "spring", year=1)["crop_table"]
    for crop in data.crops_in_season("spring", 1):
        assert crop.name in table
    assert "防风草（种子 20g，售价 35g，生长 4 天，净利 " in table
    assert "大蒜" not in table                                            # 第 2 年才出售


def test_prompt_extra_list_uses_bonuses(data):
    b = resolve_bonuses(["tiller"], "speed_gro")
    table = build_data_context(data, "spring", bonuses=b, year=1)["crop_table"]
    assert "防风草（种子 20g，售价 38g，生长 3 天" in table


def test_compact_entry_marks_desert_crops(data):
    by_name = {r.crop.name: r for r in rank_crops(data, "spring", top_n=50, year=1)}
    assert _compact_crop(by_name["大黄"]).endswith("，需先修好巴士）")
    assert "巴士" not in _compact_crop(by_name["土豆"])
