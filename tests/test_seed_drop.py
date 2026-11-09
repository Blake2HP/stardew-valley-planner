"""收获掉落种子（向日葵）与副产物（小麦的干草）。数据来自 Stardew Valley Wiki（1.6）。"""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from stardew_planner.analysis import build_data_context, crop_note, season_profit, seeds_to_buy
from stardew_planner.farm_state import CarryCrop, compute_finance, resolve_plantings

ROOT = Path(__file__).resolve().parent.parent
SV = str(ROOT / ".claude" / "skills" / "stardew-data-guard" / "scripts" / "sv.py")


def test_crop_fields(crops):
    assert crops["向日葵"].seed_drop == 1.0          # 0 / 1 / 2 颗几率相同，平均 1 颗
    assert crops["土豆"].seed_drop == 0.0
    hay = crops["小麦"].byproducts[0]
    assert (hay.name, hay.name_en, hay.chance, hay.sell_price) == ("干草", "Hay", 0.4, 0)


def test_seeds_to_buy(crops):
    # 向日葵：3 次收获 → 种子 3 份，补种 2 次都用掉落的种子，只买 1 份
    assert seeds_to_buy(crops["向日葵"], 3, 3) == 1
    assert seeds_to_buy(crops["向日葵"], 0, 1) == 1    # 来不及收获也要买第一份
    assert seeds_to_buy(crops["土豆"], 4, 4) == 4      # 不掉种子的作物不变
    assert seeds_to_buy(crops["玉米"], 4, 1) == 1      # 可再生作物只种一次


def test_sunflower_profit_counts_dropped_seeds(crops):
    # ⌊27 / 8⌋ = 3 次；3 × 80 − 1 × 200 = 40（以前按每次都买种子算是 −360）
    assert season_profit(crops["向日葵"]) == (3, 40)


def test_hay_is_not_revenue(crops):
    assert season_profit(crops["小麦"]) == (6, 90)     # 与没有干草时相同


def test_finance_uses_dropped_seed_inventory(data):
    raw = [{"day": 1, "crop": "向日葵", "tiles": 10}, {"day": 9, "crop": "向日葵", "tiles": 10},
           {"day": 17, "crop": "向日葵", "tiles": 15}]
    plantings, _ = resolve_plantings(raw, data, "summer", 1)
    f = compute_finance(plantings, start_gold=5000)
    # 第 9 天收获 10 格掉 10 颗，第 9 天补种 10 格全用掉；第 17 天收获又掉 10 颗，补种 15 格只需买 5 份
    assert f.seed_cost == (10 + 0 + 5) * 200
    assert "(10 − 掉落种子 10) × 200" in f.lines[-1] and "(15 − 掉落种子 10) × 200" in f.lines[-1]


def test_carried_sunflowers_drop_seeds_next_season(data, crops):
    carry = (CarryCrop(crops["向日葵"], 8, "夏季第 25 天种下", (5,)),)
    plantings, _ = resolve_plantings([{"day": 5, "crop": "向日葵", "tiles": 8}], data, "fall", 1)
    f = compute_finance(plantings, carry, start_gold=0)
    assert f.seed_cost == 0 and f.warnings == []        # 秋季第 5 天收获掉的种子够补种


def test_crop_note(crops):
    assert crop_note(crops["向日葵"]).startswith("向日葵：每次收获平均掉落 1 颗种子")
    assert "40% 几率掉 1 个干草" in crop_note(crops["小麦"]) and "不计入收入" in crop_note(crops["小麦"])
    assert crop_note(crops["土豆"]) == ""


def test_prompt_notes_for_listed_crops(data):
    # 只保留向日葵和小麦，保证它们出现在收益表里
    only = replace(data, crops=[c for c in data.crops if c.name in ("向日葵", "小麦")])
    table = build_data_context(only, "summer", year=1)["crop_table"]
    assert "作物备注（程序计算）：" in table
    assert "- 向日葵：每次收获平均掉落 1 颗种子" in table and "- 小麦：收获时 40% 几率掉 1 个干草" in table
    assert "向日葵" not in build_data_context(data, "spring", year=1)["crop_table"]


def run(*args):
    return subprocess.run([sys.executable, SV, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def test_sv_profit_sunflower():
    r = run("profit", "向日葵")
    assert r.returncode == 0
    assert "购买种子数 = 种子 3 份 − 补种 2 次 × min(1, 每次掉落 1 颗) = 1 份" in r.stdout
    assert "= 40g" in r.stdout


def test_sv_crop_wheat_note():
    assert "40% 几率掉 1 个干草" in run("crop", "小麦").stdout
