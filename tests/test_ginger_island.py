"""姜岛农场（--location ginger_island）：不受季节限制、换季不枯死、收益表不受本季剩余天数限制。

规则来自 https://stardewvalleywiki.com/Ginger_Island#The_Farm 与 /Fish_Shop#Willy's_Boat（核对日期 2026-11-11）。
"""

import copy
import json

import pytest

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.analysis import build_data_context, island_crop_lines
from stardew_planner.farm_state import (
    SEASON_DAYS, compute_finance, extract_farm_state, greenhouse_warnings, resolve_plantings,
)
from stardew_planner.generator import resolve_location
from stardew_planner.llm_client import MOCK_FALL, MOCK_WINTER, MockClient
from stardew_planner.season_rules import check_plantings


def island(data, season, *items, notes=None):
    raw = [{"day": d, "crop": c, "tiles": t} for d, c, t in items]
    return resolve_plantings(raw, data, season, 1, notes=notes, location="ginger_island")


def test_farm_data(data):
    assert data.island_tiles == 777


def test_any_crop_any_season(data):
    notes = []
    plantings, warnings = island(data, "winter", (1, "草莓", 10), notes=notes)
    (p,) = plantings
    assert warnings == [] and p.place == "island" and p.season_free
    assert p.harvests[:2] == (9, 13) and max(p.harvests) > SEASON_DAYS     # 换季不枯死
    assert "带上岛" in notes[0]


def test_strawberry_before_egg_festival(data):
    plantings, warnings = island(data, "spring", (1, "草莓", 5))
    assert warnings == [] and plantings[0].harvests[0] == 9


def test_town_still_checks_season(data):
    plantings, warnings = resolve_plantings([{"day": 1, "crop": "草莓", "tiles": 5}], data, "winter", 1)
    assert plantings == [] and warnings


def test_schedule_check_skips_season(data):
    goals = {1: "种下草莓", 25: "种下南瓜"}
    names = {(w.day, w.crop) for w in check_plantings(goals, data, "winter", year=1)}
    assert (1, "草莓") in names
    assert check_plantings(goals, data, "winter", year=1, season_free=True) == []


def test_tile_limit(data):
    plantings, _ = island(data, "summer", (1, "蓝莓", 700), (2, "玉米", 100))
    (w,) = greenhouse_warnings(plantings, (), data.island_tiles, "island")
    assert "第 2 天姜岛农场要用 800 格，超过姜岛农场的 777 格" in w


def test_island_crop_table(data):
    lines = island_crop_lines(data, year=1)
    rows = {line.split("|")[1].strip(): line for line in lines[2:] if line.startswith("|")}
    assert "草莓" in rows and "南瓜" in rows and "防风草" in rows       # 所有季节的作物都在
    # 草莓：可再生，每 28 天 28 / 4 = 7 次 × 120g × 单次产量 1.0204（2% 额外产出）≈ 857g（稳态不计种子）
    assert "| 7.0 | 857g |" in rows["草莓"]
    # 防风草：单次收获，28 / 4 = 7 次 × (35 − 20) = 105g
    assert "| 7.0 | 105g |" in rows["防风草"]
    assert "大蒜" not in rows                                            # 第 2 年起才卖


def test_context_uses_island_table(data):
    ctx = build_data_context(data, "winter", year=1, location="ginger_island")
    assert "单格每 28 天净利（稳态）" in ctx["crop_table"] and "姜岛农场（主农田 777 格）" in ctx["location"]
    assert "鹈鹕镇的农场" in build_data_context(data, "winter")["location"]


def test_island_bees_work_in_winter(data):
    f = compute_finance([], machines={"蜂房": 1}, season="winter", honey=data.honey, honey_all_seasons=True)
    assert f.honey_income == 7 * 100


@pytest.mark.parametrize("value, expected", [(None, "pelican_town"), ("姜岛", "ginger_island"),
                                             ("Ginger-Island", "ginger_island")])
def test_resolve_location(value, expected):
    assert resolve_location(value) == expected


def test_bad_location():
    with pytest.raises(ValueError, match="不支持的种植地点"):
        resolve_location("desert")


class Scripted:
    def __init__(self, *payloads):
        self.payloads, self.prompts = list(payloads), []

    def complete(self, system, messages):
        self.prompts.append(messages[0]["content"])
        return json.dumps(self.payloads[min(len(self.prompts), len(self.payloads)) - 1], ensure_ascii=False)


def winter_farming():
    p = copy.deepcopy(MOCK_WINTER)
    p["daily_goals"]["1"] = "种下草莓和花椰菜"
    p["daily_goals"]["28"] = "整理仓库"
    p["plantings"] = [{"day": 1, "crop": "草莓", "tiles": 10}, {"day": 1, "crop": "花椰菜", "tiles": 5}]
    return p


def test_strategy_on_island(data, indexed_store):
    client = Scripted(winter_farming())
    result = generate_strategy("岛上种田", "稳健", "winter", client=client, data=data, guide_store=indexed_store,
                               start_gold=20000, location="ginger_island", revise_rounds=0)
    assert "### 种植地点" in client.prompts[0] and "姜岛农场（主农田 777 格）" in client.prompts[0]
    assert result.problems == []
    assert result.schedule[-1].goal == "整理仓库"                       # 不自动插入“清理农场”
    assert not any(w.startswith("⚠️") and "枯死" in w for w in result.season_warnings)
    assert any(c.crop.name == "草莓" for c in result.end_state.carry)    # 冬天种的草莓跨入春季


def test_same_plan_in_town_reports_problems(data, indexed_store):
    result = generate_strategy("种田", "稳健", "winter", client=Scripted(winter_farming()), data=data,
                               guide_store=indexed_store, start_gold=20000, revise_rounds=0)
    assert any("不是冬季作物" in p for p in result.problems)


def test_plan_keeps_island_crops(data, indexed_store):
    client = Scripted(MOCK_FALL, winter_farming())
    plan = generate_plan("岛上种田", "稳健", "fall,winter", client=client, data=data, guide_store=indexed_store,
                         start_gold=20000, location="ginger_island", revise_rounds=0)
    fall, winter = plan.results
    assert any(c.crop.name == "蔓越莓" and c.place == "island" for c in fall.end_state.carry)
    assert winter.finance.crop_revenue > 0 and winter.problems == []


def test_web_location(indexed_store):
    from fastapi.testclient import TestClient

    from web.app import Settings, create_app

    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    app = create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)
    body = {"playstyle": "种田", "pacing": "稳健", "seasons": ["fall"]}
    with TestClient(app) as c:
        ok = c.post("/api/generate", json={**body, "location": "ginger_island"})
        bad = c.post("/api/generate", json={**body, "location": "desert"})
    assert ok.status_code == 200 and "姜岛农场" in ok.json()["markdown"] and bad.status_code == 422
