"""小白菜、洋蓟、玫瑰仙子、夏季亮片、未碾米。期望值来自 Stardew Valley Wiki（1.6，核对日期 2026-11-10）。"""

import pytest

from stardew_planner.analysis import build_data_context, crop_note
from stardew_planner.bonuses import resolve_bonuses


@pytest.mark.parametrize(
    "name, seasons, seed, sell, grow, category, year, speed",
    [
        ("小白菜", ("fall",), 50, 80, 4, "vegetable", 1, [3, 3, 3, 2, 2, 2]),
        ("洋蓟", ("fall",), 30, 160, 8, "vegetable", 2, [7, 6, 6, 5, 5, 4]),
        ("玫瑰仙子", ("fall",), 200, 290, 12, "flower", 1, [10, 9, 9, 8, 7, 6]),
        ("夏季亮片", ("summer",), 50, 90, 8, "flower", 1, [7, 6, 6, 5, 5, 4]),
        ("未碾米", ("spring",), 40, 30, 8, "vegetable", 2, [7, 6, 6, 5, 5, 4]),
    ],
)
def test_wiki_values(crops, name, seasons, seed, sell, grow, category, year, speed):
    c = crops[name]
    assert (c.seasons, c.seed_price, c.sell_price, c.grow_days, c.regrow_days, c.category, c.available_year) == \
        (seasons, seed, sell, grow, None, category, year)
    assert [c.speed_grow_days[k] for k in ("10", "20", "25", "33", "35", "43")] == speed


def test_speed_gro_uses_calendar(crops):
    # 玫瑰仙子：顶级生长激素 33% + 农业学家 10% = 43% → 6 天（wiki 日历）
    b = resolve_bonuses(["agriculturist"], "hyper_speed_gro")
    assert b.grow_days(crops["玫瑰仙子"]) == 6


def test_rice_yield_and_note(crops):
    rice = crops["未碾米"]
    assert rice.harvest_yield == pytest.approx(1.61)
    assert "灌溉" in crop_note(rice) and "2.11" in crop_note(rice)


def test_new_crops_in_prompt(data):
    fall = build_data_context(data, "fall", year=2)["crop_table"]
    assert "小白菜" in fall and "洋蓟" in fall and "玫瑰仙子" in fall
    assert "洋蓟" not in build_data_context(data, "fall", year=1)["crop_table"]
    assert "夏季亮片" in build_data_context(data, "summer", year=1)["crop_table"]
