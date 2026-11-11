"""社区中心献祭路线：data/bundles.json、预留与扣钱、献祭校验、跨季衔接。

收集包内容来自 https://stardewvalleywiki.com/Bundles（标准收集包，核对日期 2026-11-11）。
"""

import copy
import json

import pytest

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.bonuses import NO_BONUSES
from stardew_planner.bundles import (
    crop_needs, describe_route, is_last_chance, reserve, resolve_route, route_warnings, season_needs,
)
from stardew_planner.farm_state import compute_finance, describe_finance, resolve_plantings, season_harvest_units
from stardew_planner.llm_client import MOCK_FALL, MOCK_RESPONSE, MOCK_SUMMER, MockClient


def bundle(data, name):
    return next(b for b in data.bundles if b.name == name)


def test_wiki_bundles(data):
    spring = bundle(data, "春季作物收集包")
    assert spring.room == "茶水间" and spring.slots == 4
    assert [(i.name_en, i.count) for i in spring.items] == [("Parsnip", 1), ("Green Bean", 1), ("Cauliflower", 1),
                                                             ("Potato", 1)]
    quality = bundle(data, "高品质作物收集包")
    assert quality.slots == 3 and len(quality.items) == 4 and {i.quality for i in quality.items} == {"gold"}
    assert [(i.name_en, i.count) for i in bundle(data, "饲料收集包").items][0] == ("Wheat", 10)


def test_crop_needs_required_or_optional(data):
    needs = {(n.bundle.name, n.crop.name): n.required for n in crop_needs(data.bundles, data.crops)}
    assert needs[("春季作物收集包", "防风草")] is True
    assert needs[("染料收集包", "向日葵")] is True and needs[("饲料收集包", "小麦")] is True
    assert needs[("高品质作物收集包", "防风草")] is False          # 4 选 3
    assert needs[("夏季采集收集包", "葡萄")] is False              # 采集类收集包只做说明


def test_season_needs_respect_year_and_done(data):
    needs = crop_needs(data.bundles, data.crops)
    summer1 = {n.crop.name for n in season_needs(needs, "summer", 1, set())}
    assert "红叶卷心菜" not in summer1 and "番茄" in summer1         # 红叶卷心菜第 2 年起才卖
    assert "红叶卷心菜" in {n.crop.name for n in season_needs(needs, "summer", 2, set())}
    done = {("Summer Crops Bundle", "Tomato")}
    assert "番茄" not in {n.crop.name for n in season_needs(needs, "summer", 1, done)}


def test_last_chance(crops):
    assert is_last_chance(crops["防风草"], "spring")
    assert not is_last_chance(crops["玉米"], "summer") and is_last_chance(crops["玉米"], "fall")


def spring_reservations(data, items):
    raw = [{"day": d, "crop": c, "tiles": t} for d, c, t in items]
    plantings, _ = resolve_plantings(raw, data, "spring", 1)
    needs = [n for n in season_needs(crop_needs(data.bundles, data.crops), "spring", 1, set()) if n.required]
    return plantings, reserve(needs, season_harvest_units(plantings), 0.0, NO_BONUSES)


def test_reserve_and_warn(data):
    _, res = spring_reservations(data, [(1, "防风草", 5), (1, "花椰菜", 2), (1, "土豆", 2)])
    by = {r.need.crop.name: r for r in res}
    assert by["防风草"].reserved and by["防风草"].value == 35
    assert not by["青豆"].reserved
    (w,) = route_warnings(res, "spring")
    assert w.startswith("⚠️ 献祭校验：青豆 ×1（春季作物收集包）") and "本季没有种" in w


def test_not_enough_harvest_warns(data):
    # 小麦第 1 天种 5 格，4 天一熟、可补种……这里只种一次：5 个不够饲料收集包的 10 个
    raw = [{"day": 1, "crop": "小麦", "tiles": 5}]
    plantings, _ = resolve_plantings(raw, data, "fall", 1)
    needs = [n for n in season_needs(crop_needs(data.bundles, data.crops), "fall", 1, set()) if n.crop.name == "小麦"]
    (r,) = reserve(needs, season_harvest_units(plantings), 0.0, NO_BONUSES)
    assert not r.reserved and r.harvested == pytest.approx(5)
    assert "本季期望只收获约 5.0 个" in route_warnings([r], "fall")[0]


def test_finance_deducts_reserved_value(data):
    plantings, res = spring_reservations(data, [(1, "防风草", 5), (1, "花椰菜", 2), (1, "土豆", 2)])
    plain = compute_finance(plantings, start_gold=2000)
    kept = compute_finance(plantings, start_gold=2000, set_aside=res)
    assert kept.bundle_value == 35 + 175 + 80 and kept.end_gold == plain.end_gold - 290
    assert "− 献祭预留 290 =" in "\n".join(describe_finance(kept))
    assert ("Spring Crops Bundle", "Parsnip") in kept.bundle_done


def test_route_names():
    assert resolve_route(None) is None and resolve_route("社区中心") == "community_center"
    with pytest.raises(ValueError, match="不支持的路线"):
        resolve_route("joja")


def test_prompt_section(data):
    needs = season_needs(crop_needs(data.bundles, data.crops), "spring", 1, set())
    text = describe_route(needs, "spring", [])
    assert "防风草 ×1（春季作物收集包）：本季是今年最后机会" in text and "可选：" in text


class Scripted:
    def __init__(self, *payloads):
        self.payloads, self.prompts = list(payloads), []

    def complete(self, system, messages):
        self.prompts.append(messages[0]["content"])
        return json.dumps(self.payloads[min(len(self.prompts), len(self.payloads)) - 1], ensure_ascii=False)


def test_strategy_route_reports_and_revises(data, indexed_store):
    fixed = copy.deepcopy(MOCK_RESPONSE)
    fixed["plantings"].append({"day": 7, "crop": "青豆", "tiles": 2})
    fixed["daily_goals"]["7"] = "补种花椰菜、土豆和青豆"
    client = Scripted(MOCK_RESPONSE, fixed)
    result = generate_strategy("全收集", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               route="community_center")
    assert "### 社区中心献祭（data/bundles.json）" in client.prompts[0]
    assert any("献祭校验：青豆" in p for p in result.initial_problems)
    assert not any("献祭校验" in p for p in result.problems)          # 修正版种了青豆，被采用
    assert result.finance.bundle_value == 35 + 175 + 80 + 40      # 防风草、花椰菜、土豆、青豆各 1 个


def test_no_route_keeps_old_behavior(data, indexed_store):
    result = generate_strategy("种田", "稳健", "spring", client=MockClient(), data=data, guide_store=indexed_store)
    assert result.finance.bundle_value == 0 and not any("献祭" in p for p in result.problems)


def test_plan_does_not_ask_twice(data, indexed_store):
    # 夏季种的玉米夏季就收获并留下，秋季不再要求玉米
    client = Scripted(MOCK_SUMMER, MOCK_FALL)
    plan = generate_plan("全收集", "稳健", "summer,fall", client=client, data=data, guide_store=indexed_store,
                         start_gold=20000, route="community_center", revise_rounds=0)
    assert ("Fall Crops Bundle", "Corn") in plan.results[0].end_state.bundle_done
    done_line = next(line for line in client.prompts[1].splitlines() if line.startswith("之前季节已经留下："))
    assert "玉米 ×1（秋季作物收集包）" in done_line and "蓝莓 ×1（夏季作物收集包）" in done_line
    assert not any("献祭校验：玉米" in p for p in plan.results[1].problems)


def test_web_route(indexed_store):
    from fastapi.testclient import TestClient

    from web.app import Settings, create_app

    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    app = create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)
    body = {"playstyle": "种田", "pacing": "稳健", "seasons": ["spring"]}
    with TestClient(app) as c:
        plain = c.post("/api/generate", json=body).json()["seasons"][0]["end_gold"]
        cc = c.post("/api/generate", json={**body, "route": "community_center"}).json()["seasons"][0]["end_gold"]
        bad = c.post("/api/generate", json={**body, "route": "joja"})
    assert cc == plain - 290 and bad.status_code == 422
