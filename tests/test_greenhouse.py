"""温室：不受季节限制、可再生作物换季不会枯死、最多 120 格、没修好时不能用。"""

import copy
import json

import pytest

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.farm_state import (
    SEASON_DAYS, extract_farm_state, compute_finance, greenhouse_warnings, harvest_days, resolve_plantings,
)
from stardew_planner.generator import _parse_plantings
from stardew_planner.llm_client import MOCK_FALL, MOCK_SUMMER
from stardew_planner.season_rules import check_plantings


def gh(data, season, *items, unlocked=True, notes=None):
    raw = [{"day": d, "crop": c, "tiles": t, "place": "greenhouse"} for d, c, t in items]
    return resolve_plantings(raw, data, season, 1, greenhouse=unlocked, notes=notes)


def test_off_season_crop_grows_in_greenhouse(data):
    notes = []
    plantings, warnings = gh(data, "summer", (1, "草莓", 10), notes=notes)
    (p,) = plantings
    assert warnings == [] and p.in_greenhouse
    # 草莓生长 8 天、再生 4 天：第 9 天首次收获，之后每 4 天一次，换季不枯死
    assert p.harvests[:3] == (9, 13, 17) and max(p.harvests) > SEASON_DAYS
    assert notes and "提前买好" in notes[0]


def test_field_planting_still_checks_season(data):
    plantings, warnings = resolve_plantings([{"day": 1, "crop": "草莓", "tiles": 10}], data, "summer", 1,
                                            greenhouse=True)
    assert plantings == [] and warnings


def test_greenhouse_locked(data):
    plantings, warnings = gh(data, "spring", (1, "防风草", 10), unlocked=False)
    assert plantings == [] and "温室还没修好" in warnings[0]


def test_regrowing_crop_lives_longer_in_greenhouse(data, crops):
    field = harvest_days(crops["蓝莓"], 1, "summer")
    inside = harvest_days(crops["蓝莓"], 1, "summer", greenhouse=True)
    assert max(field) <= SEASON_DAYS < max(inside) and inside[:len(field)] == field


def test_greenhouse_crops_carry_to_next_season(data):
    plantings, _ = gh(data, "summer", (1, "草莓", 10))
    state = extract_farm_state("summer", 1, plantings, compute_finance(plantings))
    (carry,) = state.carry
    assert carry.place == "greenhouse" and carry.harvest_days[0] == 1   # 第 29 天 = 秋季第 1 天
    assert "温室" in carry.describe("fall")


def test_tile_limit(data):
    plantings, _ = gh(data, "spring", (1, "防风草", 100), (2, "土豆", 30))
    assert "第 2 天温室要用 130 格" in greenhouse_warnings(plantings, (), 120)[0]
    # 单次收获的作物收获当天空出格子：防风草第 5 天收获，第 5 天再种 30 格不超
    plantings, _ = gh(data, "spring", (1, "防风草", 100), (5, "土豆", 30))
    assert greenhouse_warnings(plantings, (), 120) == []


def test_tile_limit_counts_carried_regrowing_crops(data):
    plantings, _ = gh(data, "summer", (1, "草莓", 100))
    state = extract_farm_state("summer", 1, plantings, compute_finance(plantings))
    more, _ = gh(data, "fall", (1, "南瓜", 30))
    assert greenhouse_warnings(more, state.carry, 120)


def test_parse_place():
    errors = []
    items = _parse_plantings([{"day": 1, "crop": "草莓", "tiles": 5, "place": "温室"},
                              {"day": 2, "crop": "土豆", "tiles": 5}], errors)
    assert errors == [] and [i["place"] for i in items] == ["greenhouse", "field"]
    _parse_plantings([{"day": 1, "crop": "草莓", "tiles": 5, "place": "roof"}], errors)
    assert errors and "place" in errors[0]


def test_schedule_check_skips_season_in_greenhouse(data):
    goals = {1: "在温室种下草莓", 20: "种下花椰菜"}
    names = lambda ws: {(w.day, w.crop) for w in ws}
    assert (1, "草莓") in names(check_plantings(goals, data, "summer", year=1))
    found = names(check_plantings(goals, data, "summer", year=1, greenhouse=True))
    assert (1, "草莓") not in found and (20, "花椰菜") in found


class Scripted:
    def __init__(self, *payloads):
        self.payloads, self.prompts = list(payloads), []

    def complete(self, system, messages):
        self.prompts.append(messages[0]["content"])
        return json.dumps(self.payloads[len(self.prompts) - 1], ensure_ascii=False)


def with_greenhouse_strawberries(payload):
    p = copy.deepcopy(payload)
    p["daily_goals"]["1"] += "，在温室种下草莓"
    p["plantings"].append({"day": 1, "crop": "草莓", "tiles": 20, "place": "greenhouse"})
    return p


def test_strategy_with_greenhouse(data, indexed_store):
    payload = with_greenhouse_strawberries(MOCK_SUMMER)
    client = Scripted(payload)
    result = generate_strategy("种田", "稳健", "summer", client=client, data=data, guide_store=indexed_store,
                               start_gold=20000, revise_rounds=0, greenhouse=True)
    assert "温室：已修好，最多 120 格" in client.prompts[0]
    assert not any("草莓" in w for w in result.problems)
    assert "草莓（温室）" in result.markdown

    locked = generate_strategy("种田", "稳健", "summer", client=Scripted(payload), data=data,
                               guide_store=indexed_store, start_gold=20000, revise_rounds=0)
    assert any("温室还没修好" in w for w in locked.problems)


def test_plan_carries_greenhouse_crops(data, indexed_store):
    client = Scripted(with_greenhouse_strawberries(MOCK_SUMMER), MOCK_FALL)
    plan = generate_plan("种田", "稳健", "summer,fall", client=client, data=data, guide_store=indexed_store,
                         start_gold=20000, revise_rounds=0, greenhouse=True)
    assert any(c.crop.name == "草莓" and c.place == "greenhouse" for c in plan.results[0].end_state.carry)
    assert "草莓 ×20 格（夏季第 1 天种下，温室）" in client.prompts[1]


def test_cli_and_web_flags(indexed_store):
    from fastapi.testclient import TestClient

    from stardew_planner.llm_client import MockClient
    from web.app import Settings, create_app

    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    app = create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)
    with TestClient(app) as c:
        r = c.post("/api/generate", json={"playstyle": "种田", "pacing": "稳健", "seasons": ["spring"],
                                          "greenhouse": True})
    assert r.status_code == 200
