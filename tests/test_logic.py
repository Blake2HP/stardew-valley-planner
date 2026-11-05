"""核心判断逻辑：种植识别、跨季校验、日程合并、收益计算。"""

import pytest

from stardew_planner.analysis import season_profit
from stardew_planner.data_loader import normalize_season
from stardew_planner.schedule import ScheduleBlock, merge_daily_goals
from stardew_planner.season_rules import (
    CLEANUP_GOAL,
    _planted_crops,
    check_plantings,
    enforce_season_end,
    last_safe_plant_day,
    next_season,
    withering_crops,
)

# ---------------------------------------------------------------------------
# 种植识别：哪些作物被“安排种植”（15 种写法）
# ---------------------------------------------------------------------------

PLANTING_CASES = [
    ("不再补种（补种土豆成熟日 = 26 + 6 = 32 > 28）", [], "否定 + 括号解释"),
    ("收获全部土豆；空出的地留空，不再播种", [], "收获 + 否定播种"),
    ("收获花椰菜，补种花椰菜", ["花椰菜"], "同一作物先收后种"),
    ("收获花椰菜，补种土豆", ["土豆"], "逗号分隔的收获与种植"),
    ("收获花椰菜并补种土豆", ["土豆"], "“并”连接的收获与种植"),
    ("去皮埃尔买 50 份土豆种子（2500g）并全部种下", ["土豆"], "买种子并种下"),
    ("去买草莓种子", [], "只买种子不种"),
    ("种土豆和草莓", ["土豆", "草莓"], "“和”连接两个作物"),
    ("种土豆、草莓", ["土豆", "草莓"], "顿号连接两个作物"),
    ("种完土豆后别再种花椰菜", ["土豆"], "前半肯定后半否定"),
    ("别忘了种土豆", ["土豆"], "“别忘了”是提醒不是否定"),
    ("停止种植花椰菜", [], "停止种植"),
    ("收完作物；清理农场/停止种植", [], "换季清理目标"),
    ("种下防风草并清理农场", ["防风草"], "种下 + 其他任务"),
    ("播种甘蓝", ["甘蓝"], "“播”作为种植动词"),
]


@pytest.mark.parametrize(
    "goal, expected", [(g, e) for g, e, _ in PLANTING_CASES], ids=[i for _, _, i in PLANTING_CASES]
)
def test_planted_crops(goal, expected, data):
    assert sorted(c.name for c in _planted_crops(goal, data.crops)) == sorted(expected)


# ---------------------------------------------------------------------------
# 跨季校验
# ---------------------------------------------------------------------------

def test_late_cauliflower_warns_with_formula(data):
    warnings = check_plantings({20: "补种花椰菜"}, data, "spring")
    assert len(warnings) == 1
    w = warnings[0]
    assert (w.day, w.crop) == (20, "花椰菜")
    assert "20 + 生长 12 天 = 32 > 28" in w.formula
    assert "28 − 12 = 16" in w.formula


@pytest.mark.parametrize("day, warns", [(20, False), (21, True)])
def test_strawberry_last_plant_day_boundary(data, day, warns):
    assert bool(check_plantings({day: "种草莓"}, data, "spring")) is warns


def test_potato_maturing_on_day_28_is_ok(data):
    assert check_plantings({22: "补种土豆"}, data, "spring") == []


def test_out_of_season_crop_warns(data):
    warnings = check_plantings({23: "种玉米"}, data, "spring")
    assert len(warnings) == 1 and "不是春季作物" in warnings[0].message


def test_multi_season_crop_survives_season_change(data):
    # 玉米可跨夏秋，夏季第 20 天种下不会因换季枯死
    assert check_plantings({20: "种玉米"}, data, "summer") == []


def test_negated_planting_does_not_warn(data):
    assert check_plantings({26: "不再补种（补种土豆成熟日 = 32 > 28）"}, data, "spring") == []


def test_last_safe_plant_day(crops):
    assert last_safe_plant_day(crops["花椰菜"]) == 16
    assert last_safe_plant_day(crops["草莓"]) == 20


@pytest.mark.parametrize("season, nxt", [("spring", "summer"), ("fall", "winter"), ("冬季", "spring")])
def test_next_season(season, nxt):
    assert next_season(season) == nxt


def test_withering_crops_excludes_multi_season(data):
    names = {c.name for c in withering_crops(data, "summer")}
    assert "玉米" not in names and "蓝莓" in names


@pytest.mark.parametrize(
    "goal, expected",
    [
        ("收完作物", f"收完作物；{CLEANUP_GOAL}"),
        ("", CLEANUP_GOAL),
        (f"出售作物；{CLEANUP_GOAL}", f"出售作物；{CLEANUP_GOAL}"),
    ],
)
def test_enforce_season_end(goal, expected):
    goals, inserted = enforce_season_end({27: "钓鱼", 28: goal})
    assert goals[28] == expected
    assert goals[27] == "钓鱼"
    assert inserted is (goal != expected)


def test_enforce_season_end_is_idempotent():
    once, _ = enforce_season_end({28: "收完作物"})
    twice, inserted = enforce_season_end(once)
    assert twice == once and inserted is False


# ---------------------------------------------------------------------------
# 日程合并
# ---------------------------------------------------------------------------

def test_merge_consecutive_goals():
    blocks = merge_daily_goals({"1": "种防风草", "2": "钓鱼", "3": "钓鱼", "4": "钓鱼。"})
    assert blocks == [ScheduleBlock(1, 1, "种防风草"), ScheduleBlock(2, 4, "钓鱼")]
    assert [str(b) for b in blocks] == ["第 1 天：种防风草", "第 2-4 天：钓鱼"]


def test_same_goal_not_merged_across_gap():
    blocks = merge_daily_goals({1: "钓鱼", 2: "挖矿", 3: "钓鱼"})
    assert [(b.start, b.end) for b in blocks] == [(1, 1), (2, 2), (3, 3)]


def test_merge_sorts_days_and_normalizes_whitespace():
    blocks = merge_daily_goals({"10": " 钓鱼 ", "9": "钓鱼", "11": "下矿"})
    assert blocks == [ScheduleBlock(9, 10, "钓鱼"), ScheduleBlock(11, 11, "下矿")]


def test_merge_full_season_covers_all_days():
    goals = {d: ("钓鱼" if d < 15 else "种田") for d in range(1, 29)}
    assert merge_daily_goals(goals) == [ScheduleBlock(1, 14, "钓鱼"), ScheduleBlock(15, 28, "种田")]


# ---------------------------------------------------------------------------
# 收益计算
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "crop, plant_day, harvests",
    [
        ("防风草", 1, 6),   # ⌊27/4⌋
        ("草莓", 1, 5),     # 1 + ⌊(27-8)/4⌋
        ("草莓", 14, 2),    # 1 + ⌊(14-8)/4⌋
        ("花椰菜", 20, 0),  # 20 + 12 > 28
    ],
)
def test_season_harvest_count(crops, crop, plant_day, harvests):
    assert season_profit(crops[crop], plant_day)[0] == harvests


def test_season_profit_value(crops):
    # 草莓第 14 天：2 × 120 × 1.02 − 100 = 144.8 → 取整 144
    assert season_profit(crops["草莓"], 14) == (2, 144)


# ---------------------------------------------------------------------------
# 季节名称
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["spring", "Spring", "春季", "春"])
def test_normalize_season_aliases(name):
    assert normalize_season(name) == "spring"


def test_normalize_season_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_season("雨季")
