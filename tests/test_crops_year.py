"""新增作物（大蒜、红叶卷心菜、向日葵）与 available_year（种子开售年份）。期望值来自 Stardew Valley Wiki（1.6）。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from stardew_planner import generate_strategy
from stardew_planner.analysis import build_data_context, rank_crops, season_profit
from stardew_planner.data_loader import DEFAULT_DATA_DIR, load_crops
from stardew_planner.llm_client import MOCK_RESPONSE
from stardew_planner.season_rules import check_plantings, withering_crops

ROOT = Path(__file__).resolve().parent.parent
SV = str(ROOT / ".claude" / "skills" / "stardew-data-guard" / "scripts" / "sv.py")
YEAR_2_CROPS = {"大蒜", "红叶卷心菜", "未碾米", "洋蓟"}


def names(crops):
    return {c.name for c in crops}


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------

def test_every_crop_declares_available_year():
    raw = json.loads((DEFAULT_DATA_DIR / "crops.json").read_text(encoding="utf-8"))["crops"]
    assert all(isinstance(c["available_year"], int) and c["available_year"] >= 1 for c in raw)
    assert {c["name"] for c in raw if c["available_year"] > 1} == YEAR_2_CROPS


def test_available_year_defaults_to_1(tmp_path):
    path = tmp_path / "crops.json"
    path.write_text(json.dumps({"crops": [{
        "name": "测试", "name_en": "Test", "seasons": ["spring"], "seed_price": 1, "sell_price": 2,
        "grow_days": 3, "regrow_days": None, "category": "vegetable",
    }]}), encoding="utf-8")
    assert load_crops(path)[0].available_year == 1


@pytest.mark.parametrize(
    "name, seasons, seed, sell, grow, category, year",
    [
        ("大蒜", ("spring",), 40, 60, 4, "vegetable", 2),
        ("红叶卷心菜", ("summer",), 100, 260, 9, "vegetable", 2),
        ("向日葵", ("summer", "fall"), 200, 80, 8, "flower", 1),
    ],
)
def test_new_crop_values(crops, name, seasons, seed, sell, grow, category, year):
    c = crops[name]
    assert (c.seasons, c.seed_price, c.sell_price, c.grow_days, c.regrow_days, c.category, c.available_year) == \
        (seasons, seed, sell, grow, None, category, year)
    assert c.harvest_yield == 1.0 and c.earliest_available_day == 1


@pytest.mark.parametrize(
    "name, table",
    [
        ("大蒜", [3, 3, 3, 2, 2, 2]),
        ("红叶卷心菜", [8, 7, 6, 6, 5, 5]),
        ("向日葵", [7, 6, 6, 5, 5, 4]),
    ],
)
def test_new_crop_speed_tables(crops, name, table):
    assert list(crops[name].speed_grow_days.values()) == table


def test_red_cabbage_artisan_prices(data, crops):
    # wiki：果汁 585g、腌菜 570g
    prices = {r.product: r.price_for(crops["红叶卷心菜"]) for r in data.artisan_rules_for(crops["红叶卷心菜"])}
    assert prices["果汁"] == 585 and prices["腌菜"] == 570


# ---------------------------------------------------------------------------
# 按年份过滤
# ---------------------------------------------------------------------------

def test_crops_in_season_filters_by_year(data):
    assert "大蒜" not in names(data.crops_in_season("spring", 1))
    assert "大蒜" in names(data.crops_in_season("spring", 2))
    assert "大蒜" in names(data.crops_in_season("spring"))  # 不传年份：不过滤
    assert "红叶卷心菜" in names(data.crops_in_season("summer", 3))


def test_crops_for_year(data):
    assert names(data.crops_for_year(1)) == names(data.crops) - YEAR_2_CROPS
    assert names(data.crops_for_year(2)) == names(data.crops)
    with pytest.raises(ValueError, match="年份"):
        data.crops_for_year(0)


def test_rank_crops_year_1_excludes_year_2_crops(data):
    for season in ("spring", "summer"):
        assert not YEAR_2_CROPS & names(r.crop for r in rank_crops(data, season, top_n=50, year=1))
    year2 = {r.crop.name: r for r in rank_crops(data, "summer", top_n=50, year=2)}
    # 红叶卷心菜：⌊(28 − 1) / 9⌋ = 3 次；3 × 260 − 3 × 100 = 480
    assert (year2["红叶卷心菜"].harvests, year2["红叶卷心菜"].profit_per_tile) == (3, 480)
    assert season_profit(year2["红叶卷心菜"].crop) == (3, 480)


def test_garlic_profit(crops):
    # ⌊(28 − 1) / 4⌋ = 6 次；6 × 60 − 6 × 40 = 120
    assert season_profit(crops["大蒜"]) == (6, 120)


def test_data_context_year_filter(data):
    assert "红叶卷心菜" not in build_data_context(data, "summer", year=1)["crop_table"]
    assert "| 红叶卷心菜 | 100g | 260g |" in build_data_context(data, "summer", year=2)["crop_table"]


# ---------------------------------------------------------------------------
# 跨季校验
# ---------------------------------------------------------------------------

def test_planting_year_2_crop_in_year_1_warns(data):
    w = check_plantings({3: "种大蒜"}, data, "spring", year=1)
    assert len(w) == 1 and w[0].crop == "大蒜"
    assert "第 2 年起才出售" in w[0].message
    assert w[0].formula == "当前年份 1 < 开售年份 2"
    assert check_plantings({3: "种大蒜"}, data, "spring", year=2) == []
    assert check_plantings({3: "种大蒜"}, data, "spring") == []  # 不传年份：不检查


def test_sunflower_survives_summer_to_fall(data):
    assert "向日葵" not in names(withering_crops(data, "summer"))
    assert "向日葵" in names(withering_crops(data, "fall"))
    assert check_plantings({25: "种向日葵"}, data, "summer", year=1) == []
    assert check_plantings({25: "种向日葵"}, data, "fall", year=1) != []


def test_withering_crops_respects_year(data):
    assert "红叶卷心菜" not in names(withering_crops(data, "summer", 1))
    assert "红叶卷心菜" in names(withering_crops(data, "summer", 2))


# ---------------------------------------------------------------------------
# 集成：生成流程、命令行
# ---------------------------------------------------------------------------

class RecordingClient:
    def complete(self, system, messages):
        self.prompt = messages[0]["content"]
        return json.dumps(MOCK_RESPONSE, ensure_ascii=False)


@pytest.mark.parametrize("year, present", [(1, False), (2, True)])
def test_generate_strategy_uses_year(data, indexed_store, year, present):
    client = RecordingClient()
    result = generate_strategy("种田", "速通", "summer", year, client=client, data=data, guide_store=indexed_store)
    assert ("红叶卷心菜" in client.prompt) is present
    assert ("| 红叶卷心菜 |" in result.markdown) is present


def test_generate_strategy_rejects_year_0(data, indexed_store):
    with pytest.raises(ValueError, match="年份"):
        generate_strategy("种田", "速通", "spring", 0, client=RecordingClient(), data=data, guide_store=indexed_store)


def run(*args, script="main.py"):
    return subprocess.run([sys.executable, script, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def test_cli_rejects_year_0():
    r = run("--provider", "mock", "--year", "0")
    assert r.returncode == 2 and "年份" in r.stderr


def test_sv_list_filters_by_year():
    assert "大蒜" not in run("list", "--season", "spring", "--year", "1", script=SV).stdout
    out = run("list", "--season", "spring", script=SV).stdout
    assert "大蒜" in out and "第2年起" in out


def test_sv_profit_warns_for_year_2_crop():
    r = run("profit", "大蒜", "--year", "1", script=SV)
    assert r.returncode == 0
    assert "第 2 年起才出售" in r.stdout
    assert "= 120g" in r.stdout


def test_sv_check_schedule_with_year(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"3": "种大蒜", "28": "清理农场/停止种植"}, ensure_ascii=False), encoding="utf-8")
    assert run("check-schedule", str(plan), "--season", "spring", "--year", "1", script=SV).returncode == 3
    assert run("check-schedule", str(plan), "--season", "spring", "--year", "2", script=SV).returncode == 0
