"""新增作物蓝爵、虞美人、小麦，以及作物专属加工规则（小麦 → 啤酒）。期望值来自 Stardew Valley Wiki（1.6）。"""

import json

import pytest

from stardew_planner.analysis import build_data_context, season_profit
from stardew_planner.bonuses import NO_BONUSES, resolve_bonuses
from stardew_planner.data_loader import DEFAULT_DATA_DIR, load_artisan_rules
from stardew_planner.farm_state import harvest_days
from stardew_planner.season_rules import withering_crops


@pytest.mark.parametrize(
    "name, name_en, seasons, seed, sell, grow, category, table",
    [
        ("蓝爵", "Blue Jazz", ("spring",), 30, 50, 7, "flower", [6, 5, 5, 4, 4, 3]),
        ("虞美人", "Poppy", ("summer",), 100, 140, 7, "flower", [6, 5, 5, 4, 4, 3]),
        ("小麦", "Wheat", ("summer", "fall"), 10, 25, 4, "vegetable", [3, 3, 3, 2, 2, 2]),
    ],
)
def test_new_crop_values(crops, name, name_en, seasons, seed, sell, grow, category, table):
    c = crops[name]
    assert (c.name_en, c.seasons, c.seed_price, c.sell_price, c.grow_days, c.regrow_days, c.category) == \
        (name_en, seasons, seed, sell, grow, None, category)
    assert (c.harvest_yield, c.earliest_available_day, c.available_year) == (1.0, 1, 1)
    assert list(c.speed_grow_days) == ["10", "20", "25", "33", "35", "43"]
    assert list(c.speed_grow_days.values()) == table


def test_no_duplicate_crops():
    raw = json.loads((DEFAULT_DATA_DIR / "crops.json").read_text(encoding="utf-8"))["crops"]
    for key in ("name", "name_en"):
        values = [c[key] for c in raw]
        assert len(values) == len(set(values)), key


def test_kale_is_not_duplicated(crops):
    # 用户说的“甘蓝菜”就是官方中文名里的 Kale，库里已有（名为“甘蓝”）
    assert crops["甘蓝"].name_en == "Kale"
    assert "甘蓝菜" not in crops


@pytest.mark.parametrize(
    "name, harvests, profit",
    [
        ("蓝爵", 3, 60),     # ⌊27 / 7⌋ = 3；3 × 50 − 3 × 30
        ("虞美人", 3, 120),  # 3 × 140 − 3 × 100
        ("小麦", 6, 90),     # ⌊27 / 4⌋ = 6；6 × 25 − 6 × 10
    ],
)
def test_new_crop_profit(crops, name, harvests, profit):
    assert season_profit(crops[name]) == (harvests, profit)


def test_wheat_grows_through_fall(data, crops):
    assert "小麦" not in {c.name for c in withering_crops(data, "summer")}
    assert harvest_days(crops["小麦"], 27, "summer") == [31]   # 跨到秋季第 3 天


# ---------------------------------------------------------------------------
# 作物专属加工规则
# ---------------------------------------------------------------------------

def products(data, crop):
    return {r.product: NO_BONUSES.artisan_price(r, crop) for r in data.artisan_rules_for(crop)}


def test_wheat_keg_makes_beer_not_juice(data, crops):
    # wiki：啤酒 200g；罐头瓶照常做腌菜 25 × 2 + 50 = 100g
    assert products(data, crops["小麦"]) == {"啤酒": 200, "腌菜": 100}


def test_wheat_with_artisan_profession(data, crops):
    artisan = resolve_bonuses(["artisan"])
    prices = {r.product: artisan.artisan_price(r, crops["小麦"]) for r in data.artisan_rules_for(crops["小麦"])}
    assert prices == {"啤酒": 280, "腌菜": 140}   # wiki：280g / 140g


def test_other_vegetables_still_make_juice(data, crops):
    assert set(products(data, crops["花椰菜"])) == {"果汁", "腌菜"}


def test_flowers_have_no_artisan_rules(data, crops):
    # 花只能用于蜂蜜，数据库里没有蜂蜜规则
    assert data.artisan_rules_for(crops["蓝爵"]) == [] and data.artisan_rules_for(crops["虞美人"]) == []


def test_beer_rule_loaded_from_json():
    beer = next(r for r in load_artisan_rules(DEFAULT_DATA_DIR / "artisan.json") if r.product == "啤酒")
    assert beer.input_crops == ("Wheat",) and beer.fixed_price == 200
    assert beer.processing_minutes == 1750 and beer.processing_days == round(1750 / 1600, 2)


def test_prompt_names_crop_specific_rule(data):
    rules = build_data_context(data, "summer", year=1)["artisan_rules"]
    assert "- 小桶：小麦 → 啤酒，固定 200g" in rules
