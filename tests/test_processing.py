"""加工模拟：机器数量、加工时间、低星优先、机器排满后换下一种机器、跨季的加工品。"""

import pytest

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.bonuses import NO_BONUSES, resolve_bonuses
from stardew_planner.farm_state import compute_finance, resolve_plantings
from stardew_planner.llm_client import MockClient
from stardew_planner.processing import GoodsInProcess, simulate_processing


def sim(data, harvests, machines, bonuses=NO_BONUSES, goods_in=()):
    return simulate_processing(harvests, data.artisan_rules_for, machines, bonuses, goods_in)


def test_kegs_limit_and_carry_over(data, crops):
    # 草莓 10 格第 21 天收获（10.204 个）；小桶 3 个，果酒 6.25 天 → 7 天
    # 第 21 天到 28 天共 3 × 8 = 24 机器·天，最多留 24 / 7 = 3.43 个；3 个第 28 天完成，0.43 个第 28 天开工
    r = sim(data, [(21, crops["草莓"], 10)], {"小桶": 3})
    assert r.completed == {"果酒": pytest.approx(3.0)}
    assert r.completed_value == pytest.approx(3 * 360)
    assert r.held_value == pytest.approx(24 / 7 * 120)
    (g,) = r.goods_out
    assert (g.product, g.finish_day) == ("果酒", 7) and g.units == pytest.approx(24 / 7 - 3)
    assert r.cash_by_day[21] == pytest.approx(-24 / 7 * 120) and r.cash_by_day[28] == pytest.approx(1080)


def test_full_machines_sell_the_rest_directly(data, crops):
    # 第 25 天再收一次：机器已经排满，不再留作物
    r = sim(data, [(21, crops["草莓"], 10), (25, crops["草莓"], 10)], {"小桶": 3})
    assert r.held_value == pytest.approx(24 / 7 * 120)


def test_no_machines_no_processing(data, crops):
    r = sim(data, [(5, crops["防风草"], 10)], {})
    assert r.net == 0 and r.lines == [] and r.goods_out == []


def test_jar_preferred_then_keg_takes_overflow(data, crops):
    # 草莓：果酱 (2 × 120 + 50 − 120) / 3 天 ≈ 56.7g/天，比果酒 (360 − 120) / 7 天 ≈ 34.3g/天 好
    r = sim(data, [(1, crops["草莓"], 100)], {"罐头瓶": 1, "小桶": 1})
    assert r.completed["果酱"] > 0 and r.completed["果酒"] > 0       # 罐头瓶排满后用小桶
    only_jar = sim(data, [(1, crops["草莓"], 100)], {"罐头瓶": 1})
    assert "果酒" not in only_jar.completed


def test_low_quality_processed_first(data, crops):
    # 顶级肥料、10 级：银星 11.3%、金星 48.2%、铱星 40.5%；罐头瓶 1 个，第 13 天收获 10 格花椰菜
    # 剩 16 天 → 16 / 3 = 5.33 个可加工：先银星（1.13 个），再金星（4.20 个），铱星直接卖
    b = resolve_bonuses([], "deluxe_fertilizer", farming_level=10)
    r = sim(data, [(13, crops["花椰菜"], 10)], {"罐头瓶": 1}, b)
    silver, gold = 10 * 0.11305, 16 / 3 - 10 * 0.11305
    assert r.held_value == pytest.approx(silver * 218 + gold * 262)


def test_goods_from_last_season_finish_and_occupy_machines(data, crops):
    goods = (GoodsInProcess("果酒", "小桶", 3.0, 1080.0, 4),)
    r = sim(data, [(1, crops["草莓"], 10)], {"小桶": 3}, goods_in=goods)
    assert r.cash_by_day[4] >= 1080 - 1e-6 and r.carried_value_in == pytest.approx(1080)
    # 第 1-3 天机器被占着：第 1 天收获的草莓第 4 天才开工，第 11 天才出第一批果酒（空闲的话是第 8 天）
    assert 8 not in r.cash_by_day and r.cash_by_day[11] == pytest.approx(3 * 360)
    free = sim(data, [(1, crops["草莓"], 10)], {"小桶": 3})
    assert free.cash_by_day[8] == pytest.approx(3 * 360)


def test_finance_includes_processing(data):
    plantings, _ = resolve_plantings([{"day": 1, "crop": "花椰菜", "tiles": 10}], data, "spring", 1)
    plain = compute_finance(plantings, start_gold=2000)
    with_jars = compute_finance(plantings, start_gold=2000, machines={"罐头瓶": 5}, rules_for=data.artisan_rules_for)
    assert with_jars.processing_net > 0
    assert with_jars.end_gold == plain.end_gold + with_jars.processing_net
    assert any(line.startswith("加工（罐头瓶 5 个）") for line in with_jars.lines)


def test_holding_crops_can_cause_cash_shortfall(data):
    # 第 13 天收获后全部留作加工，第 14 天买种子时现金不够（直接卖的话够）
    plantings, _ = resolve_plantings([{"day": 1, "crop": "花椰菜", "tiles": 10}, {"day": 14, "crop": "花椰菜", "tiles": 10}],
                                     data, "spring", 1)
    plain = compute_finance(plantings, start_gold=800)
    held = compute_finance(plantings, start_gold=800, machines={"罐头瓶": 50}, rules_for=data.artisan_rules_for)
    assert plain.warnings == [] and held.warnings and "第 14 天" in held.warnings[0]


def test_generator_and_prompt(data, indexed_store):
    class Recorder(MockClient):
        def complete(self, system, messages):
            self.prompt = messages[0]["content"]
            return super().complete(system, messages)

    client = Recorder()
    result = generate_strategy("酿酒", "稳健", "spring", client=client, data=data, guide_store=indexed_store,
                               kegs=3, jars=5)
    assert "玩家现有加工设备：小桶 3 个、罐头瓶 5 个" in client.prompt
    assert result.finance.processing_net > 0
    assert "加工净增" in result.markdown
    with pytest.raises(ValueError, match="机器数量"):
        generate_strategy("酿酒", "稳健", "spring", client=MockClient(), data=data, kegs=-1)


def test_plan_carries_goods_to_next_season(data, indexed_store):
    plan = generate_plan("酿酒", "稳健", "spring,summer", client=MockClient(), data=data, guide_store=indexed_store,
                         kegs=3, jars=5)
    spring, summer = plan.results
    assert spring.end_state.goods
    assert any("其中上季机器里的货品本季完成" in line for line in summer.finance.lines)


def test_web_accepts_machines(indexed_store):
    from fastapi.testclient import TestClient

    from web.app import Settings, create_app

    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    app = create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)
    body = {"playstyle": "种田", "pacing": "稳健", "seasons": ["spring"]}
    with TestClient(app) as c:
        plain = c.post("/api/generate", json=body).json()["seasons"][0]["end_gold"]
        machines = c.post("/api/generate", json={**body, "kegs": 2, "jars": 3}).json()["seasons"][0]["end_gold"]
        bad = c.post("/api/generate", json={**body, "kegs": -1})
    assert machines > plain and bad.status_code == 422
